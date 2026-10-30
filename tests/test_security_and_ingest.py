"""URL safety, ingest, de-duplication and publication-gate tests."""
import json
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.models import Channel
from src.util.urls import check_url
from src.classify import is_bangladeshi, display_key
from src.ingest.m3u_import import parse_m3u, import_file
from src.generators.live import LiveGenerator


class TestUrlSafety(unittest.TestCase):
    def test_dangerous_schemes_blocked(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x",
                    "vbscript:x", "smb://host/share"):
            self.assertFalse(check_url(url).ok, url)

    def test_local_paths_blocked(self):
        for url in ("/var/media/a.ts", "C:\\media\\a.mp4", "./rel.m3u8", "../up.m3u8"):
            self.assertFalse(check_url(url).ok, url)

    def test_private_targets_blocked_by_default(self):
        for url in ("http://127.0.0.1/a.m3u8", "http://10.1.2.3/a.m3u8",
                    "http://192.168.0.1/a.m3u8", "http://[::1]/a.m3u8",
                    "http://169.254.1.1/a.m3u8"):
            self.assertFalse(check_url(url).ok, url)

    def test_private_targets_allowed_when_opted_in(self):
        self.assertTrue(check_url("http://127.0.0.1/a.m3u8", block_private=False).ok)

    def test_executable_downloads_blocked(self):
        for url in ("https://h/x.apk", "https://h/x.exe", "https://h/x.sh"):
            self.assertFalse(check_url(url).ok, url)

    def test_normal_stream_urls_pass(self):
        for url in ("https://cdn.example/master.m3u8", "http://cdn.example:8080/live/index.m3u8"):
            self.assertTrue(check_url(url).ok, url)


class TestParser(unittest.TestCase):
    def test_parses_attributes_and_title(self):
        entries, _, problems = parse_m3u(
            '#EXTM3U\n#EXTINF:-1 group-title="News" tvg-logo="https://h/l.png",BBC World\n'
            'https://h/a.m3u8\n')
        self.assertEqual(problems, [])
        self.assertEqual(entries[0].title, "BBC World")
        self.assertEqual(entries[0].attrs["group-title"], "News")

    def test_title_containing_comma_survives(self):
        entries, _, _ = parse_m3u('#EXTM3U\n#EXTINF:-1,News, Sport and Weather\nhttps://h/a.m3u8\n')
        self.assertEqual(entries[0].title, "News, Sport and Weather")

    def test_vlcopt_headers_are_captured(self):
        entries, _, _ = parse_m3u(
            '#EXTM3U\n#EXTINF:-1,X\n#EXTVLCOPT:http-referrer=https://ref.test/\nhttps://h/a.m3u8\n')
        self.assertEqual(entries[0].vlc_opts["http-referrer"], "https://ref.test/")

    def test_missing_header_is_tolerated_for_imports(self):
        entries, _, problems = parse_m3u('#EXTINF:-1,X\nhttps://h/a.m3u8\n')
        self.assertEqual(len(entries), 1)

    def test_extinf_without_url_is_reported(self):
        _, _, problems = parse_m3u('#EXTM3U\n#EXTINF:-1,X\n#EXTINF:-1,Y\nhttps://h/b.m3u8\n')
        self.assertTrue(problems)

    def test_quality_claim_is_demoted_to_a_note(self):
        tmp = Path(tempfile.mkdtemp()) / "s.m3u"
        tmp.write_text('#EXTM3U\n#EXTINF:-1 group-title="News",Some Channel (1080p)\n'
                       'https://h/a.m3u8\n', encoding="utf-8")
        chans, _ = import_file(tmp, "src-test")
        self.assertEqual(chans[0].name, "Some Channel")
        self.assertEqual(chans[0].resolution_label, "",
                         "a source's own quality claim must never become a measured label")
        self.assertIn("unverified", chans[0].notes)


class TestDeduplication(unittest.TestCase):
    def test_identical_urls_are_collapsed(self):
        tmp = Path(tempfile.mkdtemp()) / "s.m3u"
        tmp.write_text('#EXTM3U\n#EXTINF:-1,A\nhttps://h/a.m3u8\n'
                       '#EXTINF:-1,A copy\nhttps://h/a.m3u8\n', encoding="utf-8")
        chans, stats = import_file(tmp, "src-test")
        self.assertEqual(len(chans), 1)
        self.assertEqual(len(stats["duplicates"]), 1)


class TestPublicationGate(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(use_cache=False)
        self.gen = LiveGenerator(self.cfg, Path(tempfile.mkdtemp()))

    def _ch(self, **kw):
        base = dict(id="c1", name="C", stream_url="https://h/a.m3u8", category="news")
        base.update(kw)
        return Channel(**base)

    def test_only_active_is_published_by_default(self):
        chans = [self._ch(id=s, status=s) for s in
                 ("ACTIVE", "DEGRADED", "OFFLINE", "INVALID", "UNVERIFIED")]
        published, withheld = self.gen._partition(chans, allow_unverified=False)
        self.assertEqual([c.id for c in published], ["ACTIVE"])
        self.assertEqual(sum(len(v) for v in withheld.values()), 4)

    def test_seed_mode_admits_unverified(self):
        chans = [self._ch(id=s, status=s) for s in ("UNVERIFIED", "OFFLINE")]
        published, _ = self.gen._partition(chans, allow_unverified=True)
        self.assertEqual([c.id for c in published], ["UNVERIFIED"])

    def test_excluded_rights_never_published(self):
        published, withheld = self.gen._partition(
            [self._ch(status="ACTIVE", rights_status="EXCLUDED")], allow_unverified=True)
        self.assertEqual(published, [])
        self.assertIn("rights_excluded", withheld)

    def test_streams_needing_custom_headers_are_withheld(self):
        """SS IPTV cannot send a per-stream Referer, so such a stream is a dead tile."""
        published, withheld = self.gen._partition(
            [self._ch(status="ACTIVE", http_referrer="https://ref.test/")], allow_unverified=False)
        self.assertEqual(published, [])
        self.assertIn("requires_custom_http_headers", withheld)


if __name__ == "__main__":
    unittest.main()


class TestReliabilityGate(unittest.TestCase):
    """A channel that answers only occasionally must stop being published."""

    def setUp(self):
        self.cfg = config.load(use_cache=False)
        self.cfg["validation"]["min_reliability"] = 0.34
        self.cfg["validation"]["min_checks_for_reliability_gate"] = 5
        self.gen = LiveGenerator(self.cfg, Path(tempfile.mkdtemp()))

    def _ch(self, **kw):
        base = dict(id="c1", name="C", stream_url="https://h/a.m3u8",
                    category="news", status="ACTIVE")
        base.update(kw)
        return Channel(**base)

    def test_chronically_unreliable_channel_is_withheld(self):
        published, withheld = self.gen._partition(
            [self._ch(checks_total=10, checks_ok=2)], allow_unverified=False)
        self.assertEqual(published, [])
        self.assertIn("unreliable", withheld)

    def test_reliable_channel_passes(self):
        published, _ = self.gen._partition(
            [self._ch(checks_total=10, checks_ok=9)], allow_unverified=False)
        self.assertEqual(len(published), 1)

    def test_gate_waits_for_enough_evidence(self):
        """Two failures out of three is not yet a verdict."""
        published, _ = self.gen._partition(
            [self._ch(checks_total=3, checks_ok=1)], allow_unverified=False)
        self.assertEqual(len(published), 1)


class TestReliabilityMath(unittest.TestCase):
    def test_unchecked_channel_reports_zero_but_is_not_flapping(self):
        c = Channel(id="c", name="C")
        self.assertEqual(c.reliability, 0.0)
        self.assertFalse(c.is_flapping)

    def test_flapping_detection(self):
        self.assertTrue(Channel(id="c", name="C", checks_total=8, checks_ok=4).is_flapping)
        self.assertFalse(Channel(id="c", name="C", checks_total=8, checks_ok=8).is_flapping)
        self.assertFalse(Channel(id="c", name="C", checks_total=8, checks_ok=0).is_flapping)


class TestEmptyBuildSafety(unittest.TestCase):
    """A build that can publish nothing must not destroy what is already published."""

    def test_nothing_publishable_leaves_the_existing_tree_untouched(self):
        root = Path(tempfile.mkdtemp())
        live = root / "live"
        live.mkdir(parents=True)
        existing = live / "bangladesh.m3u"
        existing.write_text("#EXTM3U\n#EXTINF:-1,Keep me\nhttps://h/a.m3u8\n", encoding="utf-8")

        cfg = config.load(use_cache=False)
        gen = LiveGenerator(cfg, root)
        result = gen.build(
            [Channel(id="c", name="C", stream_url="https://h/b.m3u8", status="UNVERIFIED")],
            allow_unverified=False,
        )
        self.assertEqual(result.published, [])
        self.assertEqual(result.files, {})
        self.assertTrue(existing.exists(), "existing playlists must survive an empty build")
        self.assertIn("Keep me", existing.read_text(encoding="utf-8"))

    def test_a_successful_build_removes_stale_playlists(self):
        root = Path(tempfile.mkdtemp())
        live = root / "live"
        live.mkdir(parents=True)
        stale = live / "sports.m3u"
        stale.write_text("#EXTM3U\n#EXTINF:-1,Gone\nhttps://h/old.m3u8\n", encoding="utf-8")

        gen = LiveGenerator(config.load(use_cache=False), root)
        result = gen.build(
            [Channel(id="c", name="C", stream_url="https://h/b.m3u8",
                     category="news", status="ACTIVE")],
            allow_unverified=False,
        )
        self.assertTrue(result.published)
        self.assertFalse(stale.exists(), "a category with no channels must not linger")


class TestLivePagination(unittest.TestCase):
    """A live screen must stay inside the Smart-TV budget too.

    Sub-splitting a category once is not enough: the Movies category alone
    produced a 207-channel screen, well over the cap.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.cfg = config.load(use_cache=False)
        self.cfg["site"]["base_url"] = "https://example.test"
        self.cfg["ssiptv"]["max_items_per_playlist"] = 10
        self.gen = LiveGenerator(self.cfg, self.root)

    def _channels(self, n, category="news"):
        return [Channel(id=f"c{i}", name=f"{chr(65 + i % 26)}ch {i:03d}",
                        category=category, status="ACTIVE",
                        stream_url=f"https://h/{i}.m3u8") for i in range(n)]

    def test_small_category_is_a_single_leaf(self):
        self.gen.build(self._channels(6))
        self.assertTrue((self.root / "live" / "news.m3u").exists())
        self.assertFalse((self.root / "live" / "news").exists())

    def test_oversized_category_paginates_and_keeps_every_channel(self):
        self.gen.build(self._channels(34))
        pages = sorted((self.root / "live" / "news").glob("*.m3u"))
        self.assertGreater(len(pages), 1)
        total = sum(p.read_text(encoding="utf-8").count("#EXTINF") for p in pages)
        self.assertEqual(total, 34)

    # The alphabet index is 27 tiles by construction, one per letter plus the
    # symbols bucket, and the continent index is six. Those are bounded by what
    # they enumerate, not by how many channels exist, so the budget that
    # pagination enforces does not apply to them.
    _FIXED_SIZE_INDEXES = {"a-z.m3u", "by-country.m3u"}

    def test_no_live_screen_exceeds_the_budget(self):
        self.gen.build(self._channels(34))
        for p in (self.root / "live").rglob("*.m3u"):
            if p.name in self._FIXED_SIZE_INDEXES:
                continue
            self.assertLessEqual(p.read_text(encoding="utf-8").count("#EXTINF"), 10, p.name)

    def test_the_fixed_indexes_are_bounded_by_what_they_enumerate(self):
        """Not unbounded, just not bounded by the channel count: 27 letters and
        a short list of continents, however large the catalogue grows."""
        self.gen.build(self._channels(34))
        az = (self.root / "live" / "a-z.m3u").read_text(encoding="utf-8")
        self.assertLessEqual(az.count("#EXTINF"), 27)
        country = self.root / "live" / "by-country.m3u"
        if country.exists():
            self.assertLessEqual(country.read_text(encoding="utf-8").count("#EXTINF"), 20)

    def test_paginated_parent_is_an_index(self):
        self.gen.build(self._channels(34))
        text = (self.root / "live" / "news.m3u").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("#EXTINF"):
                self.assertIn('type="playlist"', line)


class TestPerCategoryReliability(unittest.TestCase):
    """Bangladesh gets latitude the rest does not.

    Validation runs from a US datacentre, which inflates failure rates for
    BD-hosted streams; four Bangladeshi channels were being withheld on distance
    rather than on being broken.
    """

    def setUp(self):
        self.cfg = config.load(use_cache=False)
        self.cfg["validation"]["min_reliability"] = 0.5
        self.cfg["validation"]["min_reliability_by_category"] = {"bangladesh": 0.25}
        self.cfg["validation"]["min_checks_for_reliability_gate"] = 4
        self.gen = LiveGenerator(self.cfg, Path(tempfile.mkdtemp()))

    def _ch(self, category, ok, total):
        return Channel(id=f"{category}-{ok}", name="C", category=category,
                       status="ACTIVE", stream_url="https://h/a.m3u8",
                       checks_total=total, checks_ok=ok)

    def test_flaky_bangladeshi_channel_is_kept(self):
        pub, _ = self.gen._partition([self._ch("bangladesh", 4, 10)], allow_unverified=False)
        self.assertEqual(len(pub), 1, "40% is above the 25% Bangladesh floor")

    def test_equally_flaky_channel_elsewhere_is_withheld(self):
        pub, held = self.gen._partition([self._ch("movies", 4, 10)], allow_unverified=False)
        self.assertEqual(pub, [])
        self.assertIn("unreliable", held)

    def test_a_truly_dead_bangladeshi_channel_is_still_withheld(self):
        pub, held = self.gen._partition([self._ch("bangladesh", 0, 45)], allow_unverified=False)
        self.assertEqual(pub, [])
        self.assertIn("unreliable", held)


class TestSubscriberCredentials(unittest.TestCase):
    """A stream URL that only works by presenting someone's subscription is
    excluded, however healthy it probes.

    The case that prompted this: Sony Max arrived from the owner's own uploaded
    playlist as a Stalker/Ministra portal URL carrying a set-top-box MAC and a
    play token. It answered 14 of 45 probes, so only the reliability floor was
    holding it back — a number that could drift above the threshold on any run
    and publish it by accident. Bypassing authentication is a rule, not a score.
    """

    def test_portal_mac_url_is_rejected(self):
        v = check_url("http://dksmedia.tv/play/live.php"
                      "?mac=00:1A:79:B6:60:3D&stream=156013&play_token=slNi06NyY0")
        self.assertFalse(v.ok)
        self.assertIn("subscriber credentials", v.reason)

    def test_xtream_login_in_query_is_rejected(self):
        v = check_url("http://host.tv/get.php?username=john&password=hunter2&type=m3u")
        self.assertFalse(v.ok)

    def test_xtream_path_style_is_rejected(self):
        v = check_url("http://host.tv:8080/live/john/hunter2/12345.ts")
        self.assertFalse(v.ok)

    def test_static_publisher_token_is_not_a_credential(self):
        # `?token=test` and `?token=onlinetv` identify the stream, not a
        # subscriber. Rejecting these would drop ~20 working channels.
        for url in ("http://4.30.180.36:8420/hbo2/index.m3u8?token=test",
                    "https://fs.uplink.kz/24KZ/mono.m3u8?token=onlinetv"):
            self.assertTrue(check_url(url).ok, url)

    def test_ordinary_hls_path_is_not_mistaken_for_xtream(self):
        self.assertTrue(check_url("https://cdn.example.com/live/bbc/hls/index.m3u8").ok)

    def test_generator_reports_its_own_withheld_reason(self):
        gen = LiveGenerator(config.load(use_cache=False), Path(tempfile.mkdtemp()))
        ch = Channel(id="x", name="Sony Max", category="movies", status="ACTIVE",
                     stream_url="http://dksmedia.tv/play/live.php"
                                "?mac=00:1A:79:B6:60:3D&play_token=abc",
                     checks_total=45, checks_ok=45)
        pub, held = gen._partition([ch], allow_unverified=False)
        self.assertEqual(pub, [])
        self.assertIn("subscriber_credentials", held)


class TestSlowDegradedIsStillPublished(unittest.TestCase):
    """DEGRADED is three conditions; only one of them means "do not publish".

    T Sports HD answered 38 of 46 probes and disappeared from the Bangladesh
    screen, because a run found its origin slow and the gate published ACTIVE
    only. A slow origin starts later on the TV; an unfetchable segment is a
    black screen. The gate has to read the reason, not the label.
    """

    def setUp(self):
        self.cfg = config.load(use_cache=False)
        self.cfg["validation"]["publish_slow_degraded"] = True
        self.cfg["validation"]["slow_degraded_min_reliability"] = 0.7
        self.cfg["validation"]["min_checks_for_reliability_gate"] = 4
        self.gen = LiveGenerator(self.cfg, Path(tempfile.mkdtemp()))

    def _ch(self, reason, ok=38, total=46, fails=0, name="T Sports HD"):
        return Channel(id=f"c-{name}-{reason[:8]}", name=name, category="bangladesh",
                       status="DEGRADED", stream_url="https://h/a.m3u8",
                       status_reason=reason, consecutive_failures=fails,
                       checks_total=total, checks_ok=ok)

    def test_slow_origin_is_published(self):
        pub, _ = self.gen._partition([self._ch("slow origin (4200 ms)")],
                                     allow_unverified=False)
        self.assertEqual([c.name for c in pub], ["T Sports HD"])

    def test_unfetchable_segment_is_not(self):
        pub, held = self.gen._partition(
            [self._ch("manifest served but the first segment was not fetchable")],
            allow_unverified=False)
        self.assertEqual(pub, [])
        self.assertIn("status_degraded", held)

    def test_unusable_variant_is_not(self):
        pub, held = self.gen._partition(
            [self._ch("master manifest served but its variant playlist was not usable")],
            allow_unverified=False)
        self.assertEqual(pub, [])
        self.assertIn("status_degraded", held)

    def test_a_channel_mid_failure_is_not(self):
        # A hard failure below the condemn threshold also parks a channel in
        # DEGRADED, carrying the previous run's reason with it. Asserted on a
        # non-Bangladeshi channel: Bangladesh deliberately keeps a channel with
        # a history through a non-definitive DEGRADED, which is its own rule and
        # has its own test.
        ch = self._ch("slow origin (4200 ms)", fails=2)
        ch.category = "news"
        pub, held = self.gen._partition([ch], allow_unverified=False)
        self.assertEqual(pub, [])
        self.assertIn("status_degraded", held)

    def test_a_slow_channel_with_poor_history_is_not(self):
        pub, held = self.gen._partition([self._ch("slow origin (4200 ms)", ok=10, total=46)],
                                        allow_unverified=False)
        self.assertEqual(pub, [])

    def test_no_recorded_reason_is_not_assumed_benign(self):
        ch = self._ch("")
        ch.category = "news"
        pub, _ = self.gen._partition([ch], allow_unverified=False)
        self.assertEqual(pub, [])

    def test_the_relaxation_is_scoped_to_bangladesh_only(self):
        """A gate tuned on global data misfired on Bangladesh three times
        running, because every reading of a Bangladeshi origin is taken from a
        US datacentre. Relaxing the whole catalogue to fix one market would be
        the wrong trade, so the proven rule is off everywhere else."""
        bd = self._ch("", ok=67, total=68)
        other = self._ch("", ok=67, total=68)
        other.category, other.id = "news", "other-id"
        pub, _ = self.gen._partition([bd, other], allow_unverified=False)
        kept = {c.category for c in pub}
        self.assertEqual(kept, {"bangladesh"})

    def test_a_proven_bangladeshi_channel_survives_a_vague_degradation(self):
        """Ekushey TV at 67 of 68 probes, Me TV at 96 of 98 and Green TV twice
        over were all sitting in DEGRADED with no definitive fault recorded
        against them, and all four lost their tiles. DEGRADED means imperfect,
        not dead."""
        pub, _ = self.gen._partition([self._ch("", ok=67, total=68)],
                                     allow_unverified=False)
        self.assertEqual(len(pub), 1)


class TestBangladeshDetection(unittest.TestCase):
    """"BTV (Uganda)" was on the Bangladesh screen: btv is also Uganda
    Broadcasting's call sign. A declared foreign country overrules an ambiguous
    call sign, but never an unambiguous Bangladeshi name - the London diaspora
    feeds are Bangladeshi channels and belong on that screen."""

    def test_ambiguous_call_sign_yields_to_a_declared_country(self):
        self.assertFalse(is_bangladeshi("BTV (Uganda)", "ug"))
        self.assertFalse(is_bangladeshi("NTV (Kenya)", "ke"))
        self.assertFalse(is_bangladeshi("RTV (Indonesia)", "id"))

    def test_ambiguous_call_sign_counts_when_nothing_contradicts_it(self):
        self.assertTrue(is_bangladeshi("BTV", "bd"))
        self.assertTrue(is_bangladeshi("N TV", ""))

    def test_unambiguous_name_survives_a_foreign_uplink(self):
        self.assertTrue(is_bangladeshi("Channel S (United Kingdom)", "uk"))
        self.assertTrue(is_bangladeshi("ATN Bangla UK", "uk"))
        self.assertTrue(is_bangladeshi("T Sports HD", "bd"))

    def test_unrelated_channels_are_not_swept_in(self):
        for name, country in (("CNN", "us"), ("Star Sports 2 HD", "in"),
                              ("BBC News", "uk")):
            self.assertFalse(is_bangladeshi(name, country), name)


class TestDuplicateTiles(unittest.TestCase):
    """Two feeds of one channel are both kept, and the tiles say which is which.

    Discarding all but one was wrong twice over. Two entries sharing a name are
    often two independent operators - Boishakhi runs on aynaott and on
    sonarbanglatv - and when one stops the other is what the viewer wants.
    Discarding also threw away the better picture: Boishakhi's measured 720p
    feed lost to an unmeasured one that was more reliable by a single probe out
    of seventy-six, and the owner opened the tile and got the poor picture.
    """

    def setUp(self):
        self.gen = LiveGenerator(config.load(use_cache=False), Path(tempfile.mkdtemp()))

    def _ch(self, name, ok, total, url, res=""):
        return Channel(id=f"c-{url}", name=name, category="bangladesh", status="ACTIVE",
                       stream_url=url, checks_total=total, checks_ok=ok, resolution=res)

    def test_both_feeds_are_kept_and_numbered(self):
        a = self._ch("Boishakhi", 10, 40, "https://a/x.m3u8")
        b = self._ch("Boishakhi TV", 38, 40, "https://b/x.m3u8")
        pub, _ = self.gen._partition([a, b], allow_unverified=False)
        self.assertEqual(len(pub), 2)
        names = [c.name for c in pub]
        self.assertEqual(names[0], "Boishakhi TV")
        self.assertEqual(names[1], "Boishakhi TV (2)")

    def test_a_measured_picture_outranks_a_marginal_reliability_edge(self):
        """The exact Boishakhi case: 75/76 at a measured 720p against 46/46
        unmeasured. The measured feed leads."""
        measured = self._ch("Boishakhi", 75, 76, "https://a/x.m3u8", res="1280x720")
        unmeasured = self._ch("Boishakhi TV", 46, 46, "https://b/x.m3u8")
        pub, _ = self.gen._partition([measured, unmeasured], allow_unverified=False)
        self.assertEqual(pub[0].stream_url, "https://a/x.m3u8")
        self.assertEqual(len(pub), 2, "the second feed is still offered")

    def test_identical_names_are_both_kept(self):
        pub, _ = self.gen._partition(
            [self._ch("ATN Bangla", 40, 40, "https://a/x.m3u8"),
             self._ch("ATN Bangla", 20, 40, "https://b/x.m3u8")], allow_unverified=False)
        self.assertEqual([c.name for c in pub], ["ATN Bangla", "ATN Bangla (2)"])

    def test_channel_s_is_not_channels_tv(self):
        """display_key deleted the separators and then the trailing "TV", so
        "Channel S" - a Bangladeshi broadcaster - and "Channels TV" became one
        key and one of them was dropped."""
        self.assertNotEqual(display_key("Channel S"), display_key("Channels TV"))
        pub, _ = self.gen._partition(
            [self._ch("Channel S", 40, 40, "https://a/x.m3u8"),
             self._ch("Channels TV", 40, 40, "https://b/x.m3u8")], allow_unverified=False)
        self.assertEqual(sorted(c.name for c in pub), ["Channel S", "Channels TV"])

    def test_parenthetical_feeds_stay_separate(self):
        pub, _ = self.gen._partition(
            [self._ch("Channel S (Bangladesh)", 40, 40, "https://a/x.m3u8"),
             self._ch("Channel S (United Kingdom)", 40, 40, "https://b/x.m3u8")],
            allow_unverified=False)
        self.assertEqual(len(pub), 2)

    def test_numbered_channels_stay_separate(self):
        pub, _ = self.gen._partition(
            [self._ch("ABC News Live 1", 40, 40, "https://a/1.m3u8"),
             self._ch("ABC News Live 2", 40, 40, "https://a/2.m3u8")],
            allow_unverified=False)
        self.assertEqual(len(pub), 2)


class TestRegionBlockedChannels(unittest.TestCase):
    """Validation runs in a US datacentre; the TV is in South Asia.

    A South Asian broadcaster that fences its stream to its home region refuses
    the runner and serves the owner. Withholding those channels measured the
    distance between GitHub and Dhaka, not whether the owner can watch.
    """

    def setUp(self):
        self.cfg = config.load(use_cache=False)
        self.cfg["validation"]["publish_region_blocked"] = True
        self.cfg["site"]["viewer_region_countries"] = ["bd", "in", "pk"]
        self.gen = LiveGenerator(self.cfg, Path(tempfile.mkdtemp()))

    def _ch(self, country, http=0, tags=(), status="OFFLINE", reason=""):
        return Channel(id=f"c-{country}-{http}-{status}", name="C", category="news",
                       country=country, status=status, stream_url="https://h/a.m3u8",
                       last_http_status=http, status_reason=reason,
                       tags=list(tags), checks_total=10, checks_ok=0)

    def test_refusal_in_the_viewers_region_is_published(self):
        for code in (403, 451):
            pub, _ = self.gen._partition([self._ch("bd", http=code)],
                                         allow_unverified=False)
            self.assertEqual(len(pub), 1, code)

    def test_source_marked_geo_block_in_region_is_published(self):
        pub, _ = self.gen._partition([self._ch("in", tags=("news", "geo-blocked"))],
                                     allow_unverified=False)
        self.assertEqual(len(pub), 1)

    def test_a_refusal_outside_the_region_is_published_too(self):
        """No channel is withheld for where it is - the owner's instruction.

        The old rule re-admitted South Asian channels only, reasoning that a US
        channel refusing a US probe would refuse Dhaka too. That is a guess
        about somebody else's CDN. The refusal tells us the origin would not
        serve the runner, which is the one place the owner is certainly not
        watching from.
        """
        pub, _ = self.gen._partition([self._ch("us", http=403)], allow_unverified=False)
        self.assertEqual(len(pub), 1)
        pub, _ = self.gen._partition([self._ch("gb", http=451)], allow_unverified=False)
        self.assertEqual(len(pub), 1)

    def test_an_absence_is_still_an_absence_anywhere(self):
        """A refusal is not a failure; a 404 or a dead host is. Those are
        measurements of the stream, not of where we stood."""
        for country in ("us", "bd", "gb"):
            pub, _ = self.gen._partition([self._ch(country, http=404)],
                                         allow_unverified=False)
            self.assertEqual(pub, [], country)
            pub, _ = self.gen._partition([self._ch(country, http=0)],
                                         allow_unverified=False)
            self.assertEqual(pub, [], country)

    def test_a_dead_host_in_the_region_is_still_withheld(self):
        # No HTTP status at all means nothing answered: DNS failure, refused
        # connection. That is dead everywhere, not fenced.
        pub, held = self.gen._partition([self._ch("bd", http=0)], allow_unverified=False)
        self.assertEqual(pub, [])

    def test_a_404_in_the_region_is_still_withheld(self):
        pub, _ = self.gen._partition([self._ch("bd", http=404)], allow_unverified=False)
        self.assertEqual(pub, [])

    def test_the_rule_can_be_switched_off(self):
        self.cfg["validation"]["publish_region_blocked"] = False
        gen = LiveGenerator(self.cfg, Path(tempfile.mkdtemp()))
        pub, _ = gen._partition([self._ch("bd", http=403)], allow_unverified=False)
        self.assertEqual(pub, [])


class TestCheckOrdering(unittest.TestCase):
    """The catalogue no longer fits in one CI run, so ordering decides what gets
    measured. Never-checked first, then least recently verified: a new channel
    is measured on the run that discovers it, and nothing can be starved."""

    def test_never_checked_come_first_then_oldest(self):
        from scripts.pipeline import _check_order
        fresh = Channel(id="new", name="new", checks_total=0)
        recent = Channel(id="recent", name="recent", checks_total=9,
                         last_verified="2026-09-23T12:00:00+00:00")
        stale = Channel(id="stale", name="stale", checks_total=9,
                        last_verified="2026-09-01T12:00:00+00:00")
        self.assertEqual([c.id for c in _check_order([recent, stale, fresh])],
                         ["new", "stale", "recent"])


class TestIngestRetentionGuard(unittest.TestCase):
    """save_channels replaces the registry, so one source failing to download
    would delete every channel it carries. With the full aggregator index that
    is most of the catalogue, and the validation health gate cannot see it -
    that gate judges probe results, and no probe has run at ingest time."""

    def test_the_floor_is_configured(self):
        cfg = config.load(use_cache=False)
        floor = float(cfg.get_path("ingest.min_retention_fraction", 0))
        self.assertGreater(floor, 0, "an ingest collapse must be refused")
        self.assertLess(floor, 1.0)

    def test_a_collapse_is_below_the_floor_and_a_normal_run_is_not(self):
        floor = float(config.load(use_cache=False)
                      .get_path("ingest.min_retention_fraction", 0.6))
        existing = 12000
        # One source of several failing to download.
        self.assertLess(4600, floor * existing)
        # Ordinary churn as channels come and go between runs.
        self.assertGreaterEqual(11500, floor * existing)


class TestHomeProbeOverridesCI(unittest.TestCase):
    """The owner's own network has the last word on reachability.

    Every verdict this pipeline produces on its own means "an origin served a
    US datacentre". The television is in Bangladesh. Where the two disagree,
    the instrument standing in the right country wins.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.gen = LiveGenerator(config.load(use_cache=False), self.dir)

    def _probe(self, rows):
        p = self.dir / "home-probe.json"
        p.write_text(json.dumps({"results": rows}))
        return self.gen.load_home_probe(p)

    def _ch(self, cid, status, **kw):
        # A distinct URL per channel: identical URLs are now collapsed to one
        # tile, since the same stream twice is noise rather than a second feed.
        return Channel(id=cid, name=cid, category="news", status=status,
                       stream_url=f"https://h/{cid}.m3u8", checks_total=10,
                       checks_ok=0 if status == "OFFLINE" else 10, **kw)

    def test_a_channel_dead_to_ci_but_alive_at_home_is_published(self):
        self._probe({"a": {"status": "ACTIVE"}})
        pub, _ = self.gen._partition([self._ch("a", "OFFLINE")], allow_unverified=False)
        self.assertEqual([c.id for c in pub], ["a"])

    def test_a_channel_alive_to_ci_but_dead_at_home_is_withheld(self):
        self._probe({"a": {"status": "OFFLINE"}})
        pub, held = self.gen._partition([self._ch("a", "ACTIVE")], allow_unverified=False)
        self.assertEqual(pub, [])
        self.assertIn("offline_from_your_network", held)

    def test_channels_the_home_probe_has_not_reached_use_the_ci_verdict(self):
        self._probe({"a": {"status": "ACTIVE"}})
        pub, _ = self.gen._partition(
            [self._ch("a", "OFFLINE"), self._ch("b", "ACTIVE"), self._ch("c", "OFFLINE")],
            allow_unverified=False)
        self.assertEqual(sorted(c.id for c in pub), ["a", "b"])

    def test_it_does_not_override_the_policy_gates(self):
        """Reachability is the only question it answers. A stream needing
        headers SS IPTV cannot send, or carrying somebody's subscription, is
        still out however well it plays at home."""
        self._probe({"a": {"status": "ACTIVE"}, "b": {"status": "ACTIVE"}})
        needs_headers = self._ch("a", "ACTIVE", http_referrer="https://x/")
        credentialed = Channel(id="b", name="b", category="news", status="ACTIVE",
                               stream_url="http://h/live/user/pass/1.ts",
                               checks_total=10, checks_ok=10)
        pub, held = self.gen._partition([needs_headers, credentialed],
                                        allow_unverified=False)
        self.assertEqual(pub, [])
        self.assertIn("requires_custom_http_headers", held)
        self.assertIn("subscriber_credentials", held)

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(self.gen.load_home_probe(self.dir / "nope.json"), 0)


class TestFavourites(unittest.TestCase):
    """A hand-picked list that outlives a bad week for an origin."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "playlists").mkdir()
        (self.root / "data").mkdir()
        cfg = config.load(use_cache=False)
        cfg["site"]["base_url"] = "https://example.test"
        self.gen = LiveGenerator(cfg, self.root / "playlists")

    def _write(self, ids):
        (self.root / "data" / "favourites.json").write_text(
            json.dumps({"channel_ids": ids}))

    def _ch(self, cid, status="ACTIVE"):
        return Channel(id=cid, name=cid.upper(), category="news", status=status,
                       stream_url=f"https://h/{cid}.m3u8", checks_total=10,
                       checks_ok=10 if status == "ACTIVE" else 0)

    def test_marked_channels_appear_in_the_order_they_were_marked(self):
        self._write(["c", "a", "b"])
        self.gen.build([self._ch("a"), self._ch("b"), self._ch("c")])
        text = (self.root / "playlists" / "live" / "favourites.m3u").read_text(encoding="utf-8")
        self.assertEqual(text.count("#EXTINF"), 3)

    def test_a_favourite_that_is_off_the_air_is_skipped_not_dropped(self):
        self._write(["a", "dead"])
        self.gen.build([self._ch("a"), self._ch("dead", status="OFFLINE")])
        text = (self.root / "playlists" / "live" / "favourites.m3u").read_text(encoding="utf-8")
        self.assertEqual(text.count("#EXTINF"), 1)
        # The mark itself survives, so the channel returns when its origin does.
        stored = json.loads((self.root / "data" / "favourites.json").read_text())
        self.assertIn("dead", stored["channel_ids"])

    def test_no_favourites_means_no_empty_screen(self):
        self._write([])
        self.gen.build([self._ch("a")])
        self.assertFalse((self.root / "playlists" / "live" / "favourites.m3u").exists())

    def test_a_missing_file_is_not_an_error(self):
        self.gen.build([self._ch("a")])
        self.assertFalse((self.root / "playlists" / "live" / "favourites.m3u").exists())


class TestWatchlistAudit(unittest.TestCase):
    """The owner asked why &TV and &Music were missing and had to ask. A build
    should already know, for every name he cares about."""

    def test_the_watchlist_is_well_formed(self):
        p = Path(__file__).resolve().parent.parent / "data" / "watchlist.json"
        names = json.loads(p.read_text())["names"]
        self.assertGreater(len(names), 20)
        self.assertEqual(len(names), len(set(names)), "duplicate names")
        for n in names:
            self.assertTrue(n.strip(), "empty name in the watchlist")

    def test_every_verdict_is_one_of_the_four(self):
        from scripts.audit_watchlist import audit
        result = audit()
        self.assertEqual(result["checked"], len(result["rows"]))
        for row in result["rows"]:
            self.assertIn(row["verdict"],
                          {"published", "withheld", "rejected", "absent"})
            self.assertTrue(row["detail"], row["name"])

    def test_a_withheld_name_names_the_gate_that_stopped_it(self):
        from scripts.audit_watchlist import audit
        for row in audit()["rows"]:
            if row["verdict"] == "withheld":
                self.assertIn("best_variant", row, row["name"])
                self.assertTrue(row["best_variant"]["status"])


class TestSameStreamTwice(unittest.TestCase):
    """Two records can carry the same URL under different names - "Premier
    Sports 2" and "Premier Sports 2 (Philippines)" are the same two feeds
    listed twice - and once nothing was being discarded that put four tiles on
    the screen for two streams. An identical URL is not an independent feed."""

    def setUp(self):
        self.gen = LiveGenerator(config.load(use_cache=False), Path(tempfile.mkdtemp()))

    def _ch(self, cid, name, url, res=""):
        return Channel(id=cid, name=name, category="sports", status="ACTIVE",
                       stream_url=url, checks_total=10, checks_ok=10, resolution=res)

    def test_one_tile_per_stream_keeping_the_better_record(self):
        pub, held = self.gen._partition([
            self._ch("a", "Premier Sports 2", "https://h/ps2"),
            self._ch("b", "Premier Sports 2 (Philippines)", "https://h/ps2",
                     res="1920x1080"),
        ], allow_unverified=False)
        self.assertEqual(len(pub), 1)
        self.assertEqual(pub[0].resolution, "1920x1080")
        self.assertIn("same_stream_as_another_tile", held)

    def test_a_trailing_slash_is_not_a_different_stream(self):
        pub, _ = self.gen._partition([
            self._ch("a", "X", "https://h/live"),
            self._ch("b", "X alt", "https://h/live/"),
        ], allow_unverified=False)
        self.assertEqual(len(pub), 1)

    def test_different_urls_are_still_two_feeds(self):
        pub, _ = self.gen._partition([
            self._ch("a", "Boishakhi", "https://one/x.m3u8", res="1280x720"),
            self._ch("b", "Boishakhi TV", "https://two/x.m3u8"),
        ], allow_unverified=False)
        self.assertEqual(len(pub), 2)


class TestLastProbeFailed(unittest.TestCase):
    """A 404 should not sit on the TV while the strike count catches up.

    The strike count exists so one bad probe cannot condemn a channel. It was
    never meant to keep a definitively broken stream on the screen for a day,
    which is the "it shows but does not play" the owner kept hitting: 355
    published channels had failed their most recent probe.

    Definitive and transient are kept apart because nine of the ten Bangladeshi
    channels in that state were timing out from a US runner - the distance to
    Dhaka, not a dead stream.
    """

    def setUp(self):
        self.gen = LiveGenerator(config.load(use_cache=False), Path(tempfile.mkdtemp()))

    def _ch(self, cid, reason, cf=1):
        # ACTIVE, because the point is the new gate rather than the status gate:
        # a channel can be ACTIVE from an earlier run and have just failed.
        return Channel(id=cid, name=cid, category="bangladesh", country="bd",
                       status="ACTIVE", stream_url=f"https://h/{cid}.m3u8",
                       status_reason=reason, consecutive_failures=cf,
                       checks_total=40, checks_ok=30)

    _DEFINITIVE_REASONS = ("HTTP 404 Not Found",
                           "body is not an HLS manifest (no #EXTM3U)",
                           "manifest and segment served but the segment is not media",
                           "manifest served but the first segment was not fetchable",
                           "live playlist is not advancing: the window did not move")

    def test_a_definitive_failure_is_withheld_outside_bangladesh_at_once(self):
        for reason in self._DEFINITIVE_REASONS:
            ch = self._ch("x", reason, cf=1)
            ch.category = "news"
            pub, held = self.gen._partition([ch], allow_unverified=False)
            self.assertEqual(pub, [], reason)
            self.assertIn("last_probe_failed", held, reason)

    def test_bangladesh_gets_exactly_one_bad_reading_of_grace(self):
        """Me TV had answered 96 of 98 probes when a single 404 took its tile
        away, and every reading of a Bangladeshi origin here is taken from the
        wrong continent. One definitive failure is noise; two is a pattern."""
        for reason in self._DEFINITIVE_REASONS:
            pub, _ = self.gen._partition([self._ch("x", reason, cf=1)],
                                         allow_unverified=False)
            self.assertEqual(len(pub), 1, f"one strike, kept: {reason}")
            pub, held = self.gen._partition([self._ch("x", reason, cf=2)],
                                            allow_unverified=False)
            self.assertEqual(pub, [], f"two strikes, pulled: {reason}")
            self.assertIn("last_probe_failed", held)

    def test_a_channel_that_never_worked_gets_no_grace_anywhere(self):
        ch = self._ch("x", "HTTP 404 Not Found", cf=1)
        ch.checks_ok, ch.checks_total = 0, 40
        pub, held = self.gen._partition([ch], allow_unverified=False)
        self.assertEqual(pub, [])
        self.assertIn("last_probe_failed", held)

    def test_a_transient_failure_keeps_its_tile(self):
        for reason in ("connection error: timed out",
                       "TimeoutError: The read operation timed out",
                       "DNS failure: gaierror: [Errno -2] Name or service not known",
                       "HTTP 523 <none>",
                       "connection error: [SSL: CERTIFICATE_VERIFY_FAILED]"):
            pub, _ = self.gen._partition([self._ch("x", reason)],
                                         allow_unverified=False)
            self.assertEqual(len(pub), 1, reason)

    def test_wording_we_do_not_recognise_counts_as_transient(self):
        """An unrecognised reason is not evidence. The strike count already
        catches a stream that is really gone."""
        pub, _ = self.gen._partition(
            [self._ch("x", "some brand new error nobody has seen")],
            allow_unverified=False)
        self.assertEqual(len(pub), 1)

    def test_a_channel_whose_last_probe_passed_is_untouched(self):
        pub, _ = self.gen._partition(
            [self._ch("x", "HTTP 404 Not Found", cf=0)], allow_unverified=False)
        self.assertEqual(len(pub), 1, "no failure outstanding, so the reason is history")


class TestDecoderOutranksTheHeuristics(unittest.TestCase):
    """A byte sniff must never overrule a successful decode.

    ATN News - 96 of 96 probes good, ffmpeg confirming 720p video, zero black -
    was off the Bangladesh screen because a 64KB ranged read of one segment did
    not look like media to a heuristic. The sniff reads a fragment and guesses;
    the decoder opens the stream and counts frames. The guess was also
    suppressing the measurement entirely, because the decode step was gated on
    the sniff having passed.
    """

    def test_the_sniff_does_not_gate_the_decode(self):
        import inspect
        from src.validators import stream_validator as sv
        src = inspect.getsource(sv.StreamValidator.validate)
        self.assertNotIn("result.segment_ok is not False and self._should_decode", src,
                         "the decode must not be conditional on the sniff")

    def test_a_decode_verdict_short_circuits_the_outside_checks(self):
        import inspect
        from src.validators import stream_validator as sv
        src = inspect.getsource(sv.StreamValidator.validate)
        decode_at = src.index("if result.decode_measured:")
        segment_at = src.index("if result.segment_ok is False:")
        self.assertLess(decode_at, segment_at,
                        "the decoder's verdict is read before the proxies for it")

    def test_a_ranged_read_is_retried_plainly_before_condemning(self):
        import inspect
        from src.validators import stream_validator as sv
        src = inspect.getsource(sv.StreamValidator.validate)
        self.assertIn("mishandle a Range request", src)

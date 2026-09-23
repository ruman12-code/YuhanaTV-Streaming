"""URL safety, ingest, de-duplication and publication-gate tests."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.models import Channel
from src.util.urls import check_url
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

    def test_no_live_screen_exceeds_the_budget(self):
        self.gen.build(self._channels(34))
        for p in (self.root / "live").rglob("*.m3u"):
            self.assertLessEqual(p.read_text(encoding="utf-8").count("#EXTINF"), 10, p.name)

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

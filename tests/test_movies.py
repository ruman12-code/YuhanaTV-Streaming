"""Movie library: rights gating, bucketing, tree shape, VOD validation."""
import sys, tempfile, threading, time, unittest, http.server, socketserver
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.models import Movie
from src.generators.movies import (MovieGenerator, normalise_genre, language_bucket)
from src.sources.archive_org.adapter import classify_rights, _pick_video_file
from src.validators.vod_validator import VodValidator


def movie(**kw):
    base = dict(id="m1", title="A Film", year=1950, genre=["Drama"], language="english",
                playback_url="https://h/f.mp4", playback_status="PLAYABLE",
                rights_status="CLEARED", source="src-test")
    base.update(kw)
    return Movie(**base)


class TestRightsGate(unittest.TestCase):
    """Nothing reaches a tile without BOTH a proven URL and cleared rights."""

    def setUp(self):
        self.gen = MovieGenerator(config.load(use_cache=False), Path(tempfile.mkdtemp()))

    def test_playable_and_cleared_publishes(self):
        pub, _ = self.gen._partition([movie()])
        self.assertEqual(len(pub), 1)

    def test_discoverable_never_publishes(self):
        pub, held = self.gen._partition([movie(playback_status="DISCOVERABLE")])
        self.assertEqual(pub, [])
        self.assertIn("playback_discoverable", held)

    def test_unverified_rights_never_publishes(self):
        pub, held = self.gen._partition([movie(rights_status="UNVERIFIED")])
        self.assertEqual(pub, [])
        self.assertIn("rights_unverified", held)

    def test_excluded_never_publishes(self):
        for kw in ({"playback_status": "EXCLUDED"}, {"rights_status": "EXCLUDED"}):
            pub, held = self.gen._partition([movie(**kw)])
            self.assertEqual(pub, [], kw)
            self.assertIn("excluded", held)

    def test_missing_url_never_publishes(self):
        pub, held = self.gen._partition([movie(playback_url="")])
        self.assertEqual(pub, [])
        self.assertIn("no_playback_url", held)

    def test_model_property_agrees_with_the_gate(self):
        self.assertTrue(movie().publishable_as_vod)
        self.assertFalse(movie(rights_status="UNVERIFIED").publishable_as_vod)
        self.assertFalse(movie(playback_status="DISCOVERABLE").publishable_as_vod)


class TestBucketing(unittest.TestCase):
    def test_genre_aliases_normalise(self):
        for raw, expect in [("Sci-Fi", "scifi"), ("Science Fiction", "scifi"),
                            ("Cartoons", "animation"), ("Children", "family"),
                            ("Film-Noir", "crime"), ("Documentaries", "documentary")]:
            self.assertEqual(normalise_genre(raw), expect, raw)

    def test_unknown_genre_is_dropped_not_invented(self):
        self.assertEqual(normalise_genre("Underwater Basket Weaving"), "")

    def test_language_buckets(self):
        self.assertEqual(language_bucket(movie(language="bangla")), "bengali")
        self.assertEqual(language_bucket(movie(language="hi")), "hindi")
        self.assertEqual(language_bucket(movie(language="klingon")), "")

    def test_top_rated_respects_the_threshold(self):
        gen = MovieGenerator(config.load(use_cache=False), Path(tempfile.mkdtemp()))
        films = [movie(id=f"m{i}", title=f"F{i}", rating=r)
                 for i, r in enumerate([9.0, 7.5, 6.9, 4.0])]
        buckets = gen._buckets(films)
        self.assertIn("top-rated", buckets)
        self.assertEqual([m.rating for m in buckets["top-rated"]], [9.0, 7.5])

    def test_no_ratings_means_no_top_rated_bucket(self):
        gen = MovieGenerator(config.load(use_cache=False), Path(tempfile.mkdtemp()))
        buckets = gen._buckets([movie(id="m1", rating=None)])
        self.assertNotIn("top-rated", buckets,
                         "an unrated catalogue must not fabricate a Top Rated row")


class TestTree(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.cfg = config.load(use_cache=False)
        self.cfg["site"]["base_url"] = "https://example.test"
        self.gen = MovieGenerator(self.cfg, self.root)

    def test_tree_is_nested_and_typed(self):
        films = [movie(id=f"m{i}", title=f"Film {i}", genre=["Drama"], rating=8.0)
                 for i in range(3)]
        result = self.gen.build(films)
        self.assertTrue(result.files)
        root = (self.root / "movies" / "movies.m3u").read_text(encoding="utf-8")
        for line in root.splitlines():
            if line.startswith("#EXTINF"):
                self.assertIn('type="playlist"', line)
                self.assertTrue(line.startswith("#EXTINF:0 "))
        leaf = (self.root / "movies" / "drama.m3u").read_text(encoding="utf-8")
        for line in leaf.splitlines():
            if line.startswith("#EXTINF"):
                self.assertIn('type="video"', line)
                self.assertTrue(line.startswith("#EXTINF:0 "))

    def test_empty_catalogue_writes_nothing(self):
        result = self.gen.build([])
        self.assertEqual(result.files, {})
        self.assertFalse((self.root / "movies" / "movies.m3u").exists())

    def test_catalogue_with_no_cleared_titles_writes_nothing(self):
        result = self.gen.build([movie(rights_status="UNVERIFIED")])
        self.assertEqual(result.files, {})
        self.assertFalse((self.root / "movies" / "movies.m3u").exists())

    def test_leaf_respects_the_item_budget(self):
        self.cfg["ssiptv"]["max_items_per_playlist"] = 5
        gen = MovieGenerator(self.cfg, self.root)
        films = [movie(id=f"m{i}", title=f"F{i}", genre=["Drama"]) for i in range(20)]
        gen.build(films)
        leaf = (self.root / "movies" / "drama.m3u").read_text(encoding="utf-8")
        self.assertEqual(leaf.count("#EXTINF"), 5)

    def test_description_never_invents_a_rating(self):
        self.assertNotIn("★", MovieGenerator.description(movie(rating=None)))
        self.assertIn("★ 7.4", MovieGenerator.description(movie(rating=7.4)))


class TestArchiveRights(unittest.TestCase):
    def test_creative_commons_licence_clears(self):
        v = classify_rights({"licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"})
        self.assertEqual(v.status, "CLEARED")

    def test_public_domain_rights_text_clears(self):
        v = classify_rights({"rights": "This work is in the Public Domain."})
        self.assertEqual(v.status, "CLEARED")

    def test_curated_collection_clears(self):
        v = classify_rights({"collection": ["prelinger", "movies"]})
        self.assertEqual(v.status, "CLEARED")

    def test_silence_does_not_clear(self):
        v = classify_rights({"title": "Some Film", "collection": ["opensource_movies_random"]})
        self.assertEqual(v.status, "UNVERIFIED")
        self.assertIn("no licence", v.reason)

    def test_video_derivative_preference(self):
        files = [
            {"name": "a.ogv", "format": "Ogg Video", "size": "900000000"},
            {"name": "b.mp4", "format": "512Kb MPEG4", "size": "400000000"},
            {"name": "c.txt", "format": "Text", "size": "10"},
        ]
        self.assertEqual(_pick_video_file(files)["name"], "b.mp4")

    def test_no_video_returns_none(self):
        self.assertIsNone(_pick_video_file([{"name": "a.txt", "format": "Text", "size": "10"}]))


class _VodH(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def do_GET(self):
        p = self.path.split("?")[0]
        mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4088
        if p == "/good.mp4":
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Range", "bytes 0-4095/734003200")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(mp4)))
            self.end_headers(); self.wfile.write(mp4); return
        if p == "/noranges.mp4":
            # A server that ignores Range answers 200 with the file's real length.
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", "734003200")
            self.end_headers(); self.wfile.write(mp4); return
        if p == "/tiny.mp4":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(mp4)))
            self.end_headers(); self.wfile.write(mp4); return
        if p == "/login.html":
            body = b"<html><body>Please sign in</body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()


class TestVodValidator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socketserver.TCPServer.allow_reuse_address = True
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _VodH)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        cfg = config.load(use_cache=False)
        cfg["security"]["block_private_ip_targets"] = False
        self.v = VodValidator(cfg)

    def _check(self, path):
        return self.v.validate(movie(playback_url=f"http://127.0.0.1:{self.port}{path}"))

    def test_range_capable_video_is_playable(self):
        r = self._check("/good.mp4")
        self.assertEqual(r.status, "PLAYABLE")
        self.assertTrue(r.seekable)
        self.assertEqual(r.content_length, 734003200)

    def test_video_without_range_support_is_degraded_not_playable(self):
        r = self._check("/noranges.mp4")
        self.assertEqual(r.status, "DEGRADED")
        self.assertIn("seeking", r.error)

    def test_html_login_wall_is_invalid(self):
        r = self._check("/login.html")
        self.assertEqual(r.status, "INVALID")

    def test_file_too_small_to_be_a_feature_is_invalid(self):
        r = self._check("/tiny.mp4")
        self.assertEqual(r.status, "INVALID")
        self.assertIn("too small", r.error)

    def test_missing_file_is_unreachable(self):
        self.assertEqual(self._check("/nope.mp4").status, "UNREACHABLE")

    def test_dangerous_url_is_invalid(self):
        r = self.v.validate(movie(playback_url="javascript:alert(1)"))
        self.assertEqual(r.status, "INVALID")


if __name__ == "__main__":
    unittest.main()


class TestCollectionQuality(unittest.TestCase):
    """Collections must not duplicate each other or invent signal."""

    def setUp(self):
        self.gen = MovieGenerator(config.load(use_cache=False), Path(tempfile.mkdtemp()))

    def test_unrated_catalogue_gets_no_trending_row(self):
        films = [movie(id=f"m{i}", title=f"F{i}", rating=None, last_verified=f"2026-01-{i+1:02d}")
                 for i in range(10)]
        buckets = self.gen._buckets(films)
        self.assertIn("recently-added", buckets)
        self.assertNotIn("trending", buckets,
                         "Trending would be byte-identical to Recently Added here")

    def test_rated_catalogue_gets_a_distinct_trending_row(self):
        films = [movie(id=f"m{i}", title=f"F{i}", rating=float(i),
                       last_verified=f"2026-01-{i+1:02d}") for i in range(10)]
        buckets = self.gen._buckets(films)
        self.assertIn("trending", buckets)
        self.assertNotEqual([m.id for m in buckets["trending"]],
                            [m.id for m in buckets["recently-added"]])

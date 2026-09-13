"""Landing page generation.

The site root answering 404 looks identical to a broken deployment, so the page
that prevents it has to be generated unconditionally and must never crash on a
thin or partial report.
"""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.generators.site import render, write

FULL = {
    "generated_at": "2026-09-13T12:00:00+00:00",
    "live_channels": {"total": 171, "active": 131},
    "movies": {"published_as_vod": 208, "catalogue_total": 208},
    "resolution": {"measured_1080p": 54, "measured_720p": 47},
    "run_health": {"ok": True, "reason": "within limits"},
    "reliability": {"mean_reliability": 0.81},
}


class TestRender(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(use_cache=False)
        self.cfg["site"]["base_url"] = "https://example.test/Repo"
        self.root = Path(tempfile.mkdtemp())
        (self.root / "live").mkdir(parents=True)
        (self.root / "live" / "bangladesh.m3u").write_text(
            "#EXTM3U\n#EXTINF:-1,A\nhttps://h/a.m3u8\n#EXTINF:-1,B\nhttps://h/b.m3u8\n",
            encoding="utf-8")

    def test_produces_a_complete_html_document(self):
        out = render(FULL, self.cfg, self.root)
        self.assertTrue(out.lstrip().startswith("<!doctype html>"))
        self.assertIn("</html>", out)
        self.assertIn("<title>", out)

    def test_shows_the_master_url_for_the_tv(self):
        out = render(FULL, self.cfg, self.root)
        self.assertIn("https://example.test/Repo/iptv/master.m3u", out)

    def test_counts_come_from_the_playlists_on_disk(self):
        out = render(FULL, self.cfg, self.root)
        self.assertIn("🇧🇩 Bangladesh", out)
        self.assertIn(">2<", out, "the two channels in bangladesh.m3u should be counted")

    def test_no_external_resources(self):
        """It must load on a phone with no CDN reachable."""
        out = render(FULL, self.cfg, self.root)
        for marker in ("<script", "cdn.", "googleapis", "unpkg", "jsdelivr"):
            self.assertNotIn(marker, out.lower(), marker)

    def test_survives_an_empty_report(self):
        out = render({}, self.cfg, self.root)
        self.assertIn("</html>", out)
        self.assertIn("not run", out)

    def test_survives_a_missing_playlist_tree(self):
        out = render(FULL, self.cfg, Path(tempfile.mkdtemp()))
        self.assertIn("</html>", out)
        self.assertIn("No movie catalogue built.", out)

    def test_values_are_html_escaped(self):
        cfg = config.load(use_cache=False)
        cfg["site"]["brand"] = '<img src=x onerror="alert(1)">'
        out = render(FULL, cfg, self.root)
        self.assertNotIn("<img src=x", out)
        self.assertIn("&lt;img", out)

    def test_write_creates_the_file(self):
        target = Path(tempfile.mkdtemp()) / "site" / "index.html"
        size = write(FULL, self.cfg, self.root, target)
        self.assertTrue(target.exists())
        self.assertEqual(size, len(target.read_bytes()))


if __name__ == "__main__":
    unittest.main()

"""Regression guard: the playlists actually committed in this repo must be valid."""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
from src import config                                   # noqa: E402
from src.validators.m3u_validator import M3UValidator    # noqa: E402

PLAYLISTS = REPO / "playlists"


@unittest.skipUnless((PLAYLISTS / "master.m3u").exists(), "playlists not generated yet")
class TestGenerated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = M3UValidator(config.load(use_cache=False), PLAYLISTS).validate_tree("master.m3u")

    def test_no_structural_errors(self):
        errors = [i for i in self.result["issues"] if i["severity"] == "error"]
        self.assertEqual(errors, [], f"structural errors: {errors}")

    def test_no_circular_references(self):
        self.assertEqual(self.result["cycles"], [])

    def test_every_playlist_is_reachable_from_master(self):
        self.assertEqual(self.result["unreachable"], [])

    def test_every_file_is_plain_utf8_without_bom(self):
        for f in PLAYLISTS.rglob("*.m3u"):
            raw = f.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"{f} has a BOM")
            raw.decode("utf-8")

    def test_no_playlist_exceeds_the_smart_tv_item_budget(self):
        budget = int(config.load(use_cache=False).get_path("ssiptv.max_items_per_playlist", 120))
        for f in PLAYLISTS.rglob("*.m3u"):
            count = f.read_text(encoding="utf-8").count("#EXTINF")
            self.assertLessEqual(count, budget, f"{f.name} has {count} items")

    def test_master_is_small_and_points_only_at_playlists(self):
        text = (PLAYLISTS / "master.m3u").read_text(encoding="utf-8")
        # One screen, no scrolling. Ten is the ceiling both home-screen tests use.
        self.assertLessEqual(text.count("#EXTINF"), 10)
        for line in text.splitlines():
            if line.startswith("#EXTINF"):
                self.assertIn('type="playlist"', line)


if __name__ == "__main__":
    unittest.main()


class TestHomeScreen(unittest.TestCase):
    """master.m3u is the first screen, so it is curated rather than a bare index."""

    @classmethod
    def setUpClass(cls):
        cls.text = (PLAYLISTS / "master.m3u").read_text(encoding="utf-8")
        cls.extinfs = [l for l in cls.text.splitlines() if l.startswith("#EXTINF")]

    def test_every_tile_navigates_rather_than_playing(self):
        for line in self.extinfs:
            self.assertIn('type="playlist"', line)

    def test_tiles_are_large(self):
        self.assertEqual(self.text.count("#EXTSIZE: big"), len(self.extinfs))

    def test_every_tile_has_a_background(self):
        self.assertEqual(self.text.count("#EXTBG:"), len(self.extinfs))

    def test_backgrounds_are_images_or_colours(self):
        import re
        for line in self.text.splitlines():
            if line.startswith("#EXTBG:"):
                value = line.split(":", 1)[1].strip()
                self.assertTrue(
                    value.startswith("http") or re.match(r"^#[0-9a-fA-F]{6}$", value),
                    value)

    def test_artwork_is_not_repeated_across_tiles(self):
        images = [l.split(":", 1)[1].strip() for l in self.text.splitlines()
                  if l.startswith("#EXTBG:") and "http" in l]
        self.assertEqual(len(images), len(set(images)),
                         "each home tile should carry distinct artwork")

    def test_home_screen_stays_short(self):
        self.assertLessEqual(len(self.extinfs), 10)

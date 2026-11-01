"""Checks on BUILD OUTPUT: the playlists the generator has just written.

These run after the build in CI, never before it. Run before, they examine the
previous run's files - and if anything about those files is wrong, they stop the
very run that would regenerate them. That deadlocked the pipeline for six runs:
a test here asserted a named channel was on the Bangladesh screen, the channel
went off the air, the quick check correctly pulled it, and every full run after
that failed its tests before it could rebuild anything.

Nothing here may depend on the outside world. Whether a broadcaster is up today
is a measurement and belongs in the watchlist audit, which reports and never
blocks. What belongs here is whether the generator turned whatever it was given
into a correct tree.

Set YUHANA_SKIP_BUILD_OUTPUT_TESTS=1 to skip this module, as the pre-build test
step does.
"""
import os, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent
from src import config                                   # noqa: E402
from src.validators.m3u_validator import M3UValidator    # noqa: E402

PLAYLISTS = REPO / "playlists"


_SKIP = os.environ.get("YUHANA_SKIP_BUILD_OUTPUT_TESTS") == "1"


@unittest.skipIf(_SKIP, "build-output checks run after the build, not before it")
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

    def test_no_screen_shows_the_same_label_twice(self):
        for f in PLAYLISTS.rglob("*.m3u"):
            labels = [ln.split(",", 1)[-1].strip()
                      for ln in f.read_text(encoding="utf-8").splitlines()
                      if ln.startswith("#EXTINF")]
            dupes = {x for x in labels if labels.count(x) > 1}
            self.assertEqual(dupes, set(), f"{f.name} repeats {dupes}")

    def test_every_published_bangladeshi_channel_reaches_the_bangladesh_screen(self):
        """The generator's half of "no Bangladeshi channel left out".

        Independent of which broadcasters are up: whatever the publication
        gate let through as Bangladeshi must appear on the Bangladesh screen,
        and nothing that is not Bangladeshi may. A failure here is a bug in the
        tree builder, never a channel going off the air.
        """
        from src.models import load_channels
        from src.generators.live import LiveGenerator
        gen = LiveGenerator(config.load(use_cache=False), PLAYLISTS)
        gen.load_home_probe(REPO / "data" / "status" / "home-probe.json")
        published, _ = gen._partition(load_channels(REPO / "data" / "channels"), False)
        want = {(c.publish_url or c.stream_url).rstrip("/")
                for c in published if c.category == "bangladesh"}
        text = (PLAYLISTS / "live" / "bangladesh.m3u").read_text(encoding="utf-8")
        have = {ln.strip().rstrip("/") for ln in text.splitlines() if ln.startswith("http")}
        self.assertEqual(want - have, set(), "published as Bangladeshi but not on the screen")
        self.assertEqual(have - want, set(), "on the Bangladesh screen but not Bangladeshi")


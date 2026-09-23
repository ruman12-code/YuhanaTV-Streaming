"""Adult-content screening.

The library is browsed on a family television and has a Kids category. The
Archive does host adult material, some of it tagged as animation, which is where
a child would encounter it.
"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tempfile
from src.content_policy import adult_reason, is_adult, adult_channel_reason
from src.ingest.m3u_import import import_file


class TestScreening(unittest.TestCase):
    def test_catches_explicit_terms(self):
        for text in ("Old Age Porn", "Vintage Erotica", "a pornographic cartoon",
                     "Diary of a Nudist", "XXX Reel", "adults only",
                     "the grindhouse experience", "Afro Mood Burlesque"):
            self.assertTrue(is_adult(text), text)

    def test_screens_across_title_subjects_and_description(self):
        self.assertTrue(is_adult("Harmless Title", "cartoon, pornographic", ""))
        self.assertTrue(is_adult("Harmless Title", "", "An erotic feature."))

    def test_reports_which_term_matched(self):
        self.assertEqual(adult_reason("Old Age Porn"), "porn")

    def test_ordinary_films_pass(self):
        for text in ("Night of the Living Dead", "His Girl Friday", "Detour",
                     "Plan 9 from Outer Space", "A Trip to the Moon",
                     "Sita Sings the Blues", "Big Buck Bunny"):
            self.assertFalse(is_adult(text), text)

    def test_educational_hygiene_films_are_not_screened_out(self):
        """Public-health shorts are a real public-domain genre."""
        for text in ("Sex Education for Boys", "Social Hygiene and the Soldier",
                     "Sex Hygiene (1942)"):
            self.assertFalse(is_adult(text), text)

    def test_substrings_do_not_false_positive(self):
        for text in ("Sexton Blake Investigates", "The Essex Murders",
                     "Middlesex County", "Scunthorpe Revisited"):
            self.assertFalse(is_adult(text), text)

    def test_empty_input_is_safe(self):
        self.assertFalse(is_adult())
        self.assertFalse(is_adult("", "", ""))
        self.assertEqual(adult_reason(""), "")


class TestAdapterRejectsAdultItems(unittest.TestCase):
    def test_to_movie_refuses_an_adult_item(self):
        from src import config
        from src.sources.archive_org.adapter import ArchiveOrgAdapter
        adapter = ArchiveOrgAdapter(config.load(use_cache=False))
        payload = {
            "metadata": {"title": "Some Reel", "subject": ["pornographic", "cartoon"],
                         "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
                         "year": "1929"},
            "files": [{"name": "a.mp4", "format": "MPEG4", "size": "300000000",
                       "width": "640", "height": "480"}],
        }
        movie, reason = adapter.to_movie("some-reel", payload)
        self.assertIsNone(movie)
        self.assertIn("adult material", reason)

    def test_to_movie_accepts_an_ordinary_item(self):
        from src import config
        from src.sources.archive_org.adapter import ArchiveOrgAdapter
        adapter = ArchiveOrgAdapter(config.load(use_cache=False))
        payload = {
            "metadata": {"title": "Detour", "subject": ["film noir"], "year": "1945",
                         "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"},
            "files": [{"name": "a.mp4", "format": "h.264", "size": "900000000",
                       "width": "1920", "height": "1080"}],
        }
        movie, reason = adapter.to_movie("detour", payload)
        self.assertIsNotNone(movie, reason)
        self.assertEqual(movie.resolution_label, "1080p")
        self.assertEqual(movie.rights_status, "CLEARED")


class TestOpenLicenceCreators(unittest.TestCase):
    def test_blender_foundation_clears(self):
        from src.sources.archive_org.adapter import classify_rights
        v = classify_rights({"creator": "Blender Foundation"})
        self.assertEqual(v.status, "CLEARED")
        self.assertEqual(v.rule, "creator")

    def test_us_government_films_clear_by_collection(self):
        from src.sources.archive_org.adapter import classify_rights
        self.assertEqual(classify_rights({"collection": ["nasa"]}).status, "CLEARED")

    def test_an_unknown_creator_does_not_clear(self):
        from src.sources.archive_org.adapter import classify_rights
        self.assertEqual(classify_rights({"creator": "Some Studio"}).status, "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()


class TestLiveChannelAdultScreening(unittest.TestCase):
    """The owner asked for every playable channel and no adult content.

    The narrow category feeds this project started with contained none, so
    screening existed only for the film library. The full aggregator index has
    an entire category of adult channels, and they would otherwise have been
    filed by name into Movies or Entertainment on a family TV.
    """

    def test_aggregator_category_is_caught(self):
        self.assertTrue(adult_channel_reason("Some Channel", "xxx"))
        self.assertTrue(adult_channel_reason("Some Channel", "adult"))

    def test_compound_category_is_caught(self):
        # Group titles arrive compound: "XXX;Movies" slugifies to "xxx-movies".
        self.assertTrue(adult_channel_reason("Some Channel", "xxx-movies"))
        self.assertTrue(adult_channel_reason("Some Channel", "movies-xxx"))

    def test_brand_name_is_caught_without_any_category(self):
        for name in ("Brazzers TV", "Playboy TV", "Hustler HD", "Dorcel TV",
                     "Red Light HD", "Vivid TV", "Penthouse Gold"):
            self.assertTrue(adult_channel_reason(name, "movies"), name)

    def test_ordinary_channels_pass(self):
        for name, group in (("BBC News", "news"), ("Discovery Channel", "documentary"),
                            ("Naked Science", "documentary"), ("Cartoon Network", "kids"),
                            ("Zee Cinema", "movies"), ("T Sports HD", "sports"),
                            ("Star Movies Select HD", "movies-series")):
            self.assertEqual(adult_channel_reason(name, group), "", name)

    def test_an_adult_channel_never_enters_the_registry(self):
        p = Path(tempfile.mkdtemp()) / "s.m3u"
        p.write_text('#EXTM3U\n#EXTINF:-1 tvg-id="X.us" group-title="XXX",Some Channel\n'
                     'https://h/a.m3u8\n', encoding="utf-8")
        channels, stats = import_file(p, "t")
        self.assertEqual(channels, [])
        self.assertIn("adult", stats["rejected"][0]["reason"])

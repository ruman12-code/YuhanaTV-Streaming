"""Adult-content screening.

The library is browsed on a family television and has a Kids category. The
Archive does host adult material, some of it tagged as animation, which is where
a child would encounter it.
"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.content_policy import adult_reason, is_adult


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

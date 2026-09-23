"""Channel classification.

Zee Cinema arrived tagged "Entertainment" and sat with the drama channels. A
viewer looking for a film should not need to know how an aggregator filed it.
"""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.classify import (categorise, from_name, from_group, clean_display_name,
                          is_geo_blocked, is_not_24_7)
from src.ingest.m3u_import import import_file


class TestNameEvidence(unittest.TestCase):
    """The broadcaster's own name is the most reliable signal available."""

    def test_movie_channels_mis_tagged_as_entertainment(self):
        for name in ("Zee Cinema", "Star Gold HD", "Sony Max", "Jalsha Movies HD",
                     "ZB Cinema", "The Movie Club", "Cinevault Westerns"):
            self.assertEqual(categorise(name, "entertainment"), "movies", name)

    def test_kids_channels_win_over_a_general_group(self):
        for name in ("Disney Junior", "Cartoon Network", "Nick Jr", "Boomerang",
                     "Pogo", "CBeebies", "Discovery Kids"):
            self.assertEqual(categorise(name, "entertainment"), "kids", name)

    def test_sports_music_and_news_are_recognised(self):
        self.assertEqual(categorise("Star Sports 2", "entertainment"), "sports")
        self.assertEqual(categorise("MTV HD", "entertainment"), "music")
        self.assertEqual(categorise("Republic Bangla", "entertainment"), "news")

    def test_more_specific_rule_wins(self):
        """'Disney Junior' is kids, not caught by a looser rule first."""
        self.assertEqual(from_name("Disney Junior"), "kids")
        self.assertEqual(from_name("Sony Sports Ten 1"), "sports")

    def test_unknown_name_yields_nothing(self):
        self.assertEqual(from_name("Channel 47"), "")


class TestGroupEvidence(unittest.TestCase):
    """iptv-org writes compound group titles; 185 channels fell into 'other'."""

    def test_compound_groups_resolve(self):
        for group, expect in (("classic-movies", "movies"),
                              ("animation-kids", "kids"),
                              ("movies-series", "movies"),
                              ("classic-series", "series"),
                              ("news-public", "news"),
                              ("culture-documentary", "documentary"),
                              ("animation-kids-religious", "kids")):
            self.assertEqual(from_group(group), expect, group)

    def test_single_groups_still_work(self):
        self.assertEqual(from_group("sports"), "sports")
        self.assertEqual(from_group("music"), "music")

    def test_unknown_group_yields_nothing(self):
        self.assertEqual(from_group("wibble"), "")
        self.assertEqual(from_group(""), "")

    def test_name_beats_group(self):
        self.assertEqual(categorise("Zee Cinema", "news-public"), "movies")

    def test_group_used_when_the_name_says_nothing(self):
        self.assertEqual(categorise("Channel 47", "classic-movies"), "movies")

    def test_falls_back_to_the_default(self):
        self.assertEqual(categorise("Channel 47", "wibble"), "other")


class TestNameAnnotations(unittest.TestCase):
    def test_geo_blocked_is_detected(self):
        self.assertTrue(is_geo_blocked("History TV18 HD (1080p) [Geo-blocked]"))
        self.assertFalse(is_geo_blocked("History TV18 HD (1080p)"))

    def test_not_24_7_is_detected(self):
        self.assertTrue(is_not_24_7("Action 24 (1080p) [Not 24/7]"))

    def test_annotations_are_stripped_from_the_display_name(self):
        self.assertEqual(clean_display_name("History TV18 HD (1080p) [Geo-blocked]"),
                         "History TV18 HD")
        self.assertEqual(clean_display_name("Action 24 (1080p) [Not 24/7]"), "Action 24")
        self.assertEqual(clean_display_name("Zee Cinema"), "Zee Cinema")

    def test_stripping_does_not_eat_a_real_name(self):
        self.assertEqual(clean_display_name("24 Horas (Spain)"), "24 Horas (Spain)")


class TestIngestApplies(unittest.TestCase):
    def _ingest(self, body):
        p = Path(tempfile.mkdtemp()) / "s.m3u"
        p.write_text(body, encoding="utf-8")
        return import_file(p, "t")

    def test_geo_blocked_channel_is_kept_and_tagged(self):
        """[Geo-blocked] means "restricted to its home territory", not
        "restricted from Bangladesh". Rejecting these at ingest threw away
        exactly the South Asian channels the owner can watch and this
        validator, running in a US datacentre, cannot. Keep and tag."""
        ch, st = self._ingest(
            '#EXTM3U\n#EXTINF:-1 tvg-id="X.in" group-title="Documentary",'
            'History TV18 HD (1080p) [Geo-blocked]\nhttps://h/b.m3u8\n')
        self.assertEqual(len(ch), 1)
        self.assertIn("geo-blocked", ch[0].tags)
        self.assertEqual(st["rejected"], [])
        # The annotation is stripped from what reaches a tile.
        self.assertEqual(ch[0].name, "History TV18 HD")

    def test_bangladesh_is_a_country_bucket_not_a_genre(self):
        ch, _ = self._ingest(
            '#EXTM3U\n#EXTINF:-1 tvg-id="SomoyTV.bd" group-title="News",Somoy TV\n'
            'https://h/c.m3u8\n')
        self.assertEqual(ch[0].category, "bangladesh")
        self.assertEqual(ch[0].country, "bd")

    def test_movie_channel_is_refiled_on_import(self):
        ch, _ = self._ingest(
            '#EXTM3U\n#EXTINF:-1 tvg-id="ZeeCinema.in@SD" group-title="Entertainment",'
            'Zee Cinema\nhttps://h/a.m3u8\n')
        self.assertEqual(ch[0].category, "movies")
        self.assertEqual(ch[0].country, "in")

    def test_not_24_7_is_recorded_but_not_excluded(self):
        ch, _ = self._ingest(
            '#EXTM3U\n#EXTINF:-1 tvg-id="A.gr" group-title="Movies",'
            'Action 24 (1080p) [Not 24/7]\nhttps://h/d.m3u8\n')
        self.assertEqual(len(ch), 1)
        self.assertEqual(ch[0].name, "Action 24")
        self.assertIn("not 24/7", ch[0].notes)


if __name__ == "__main__":
    unittest.main()


class TestCountrySuffixDoesNotDecideGenre(unittest.TestCase):
    """A trailing "(Country)" disambiguates two feeds of one brand. It says
    nothing about genre, and taking it as evidence filed National Geographic
    and Film+ under News because "republic" was in the news pattern for
    Republic TV. Twenty-eight channels were wrong before the full index; at
    this scale it is a whole class."""

    def test_country_parenthetical_is_ignored_for_genre(self):
        self.assertEqual(categorise("History (Czech Republic)", "general"), "documentary")
        self.assertEqual(categorise("National Geographic (Czech Republic)", "general"),
                         "documentary")
        self.assertEqual(categorise("Film+ (Czech Republic)", "general"), "movies")
        self.assertEqual(categorise("Cartoon Network (Dominican Republic)", "general"),
                         "kids")

    def test_the_republic_news_brands_still_match(self):
        for name in ("Republic TV", "Republic Bharat", "Republic Bangla",
                     "Republic World"):
            self.assertEqual(categorise(name, "entertainment"), "news", name)

    def test_a_general_station_named_after_its_country_is_not_a_genre(self):
        self.assertEqual(categorise("Prima (Czech Republic)", "general"), "general")
        self.assertEqual(categorise("Global TV (Dominican Republic)", "undefined"),
                         "general")


class TestGeneralIsNotEntertainment(unittest.TestCase):
    """The narrow category feeds had no "General" or "Undefined" groups. The
    full aggregator index files four thousand channels that way - provincial
    broadcasters carrying news, drama and sport in one schedule. Calling them
    Entertainment made the word meaningless for the 900 channels that are."""

    def test_unclassifiable_groups_land_in_general(self):
        self.assertEqual(categorise("Telecentro", "undefined"), "general")
        self.assertEqual(categorise("Onda TV", "general"), "general")

    def test_a_name_that_says_what_it_is_still_wins(self):
        self.assertEqual(categorise("Star Sports 2 HD", "general"), "sports")
        self.assertEqual(categorise("Zee Cinema", "undefined"), "movies")
        self.assertEqual(categorise("BBC News Pashto", "general"), "news")

    def test_real_entertainment_groups_are_untouched(self):
        self.assertEqual(categorise("Some Channel", "entertainment"), "entertainment")
        self.assertEqual(categorise("Some Channel", "comedy"), "entertainment")

    def test_general_is_a_valid_model_category(self):
        from src.models import Channel, LIVE_CATEGORIES
        self.assertIn("general", LIVE_CATEGORIES)
        self.assertEqual(Channel(id="x", name="y", category="general").category,
                         "general")

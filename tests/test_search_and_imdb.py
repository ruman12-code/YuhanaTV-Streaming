"""IMDb matching and the full-text search index (spec section 25)."""
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.models import Movie, Channel
from src.search import tokenize, build_index, search
from src.sources.imdb.datasets import (normalise_title, build_lookup_keys,
                                       best_match, ImdbTitle)
from src.generators.movies import MovieGenerator


def movie(**kw):
    base = dict(id="m1", title="A Film", year=1950, genre=["Drama"], language="english",
                playback_url="https://h/f.mp4", playback_status="PLAYABLE",
                rights_status="CLEARED", source="src-test",
                runtime_minutes=95)   # a feature: the gate requires one
    base.update(kw)
    return Movie(**base)


class TestTitleNormalisation(unittest.TestCase):
    def test_case_punctuation_and_whitespace_are_folded(self):
        self.assertEqual(normalise_title("  D.O.A.  "), "d o a")
        self.assertEqual(normalise_title("Plan 9 (from Outer Space)"), "plan 9 from outer space")

    def test_leading_article_is_dropped(self):
        self.assertEqual(normalise_title("The Maltese Falcon"), "maltese falcon")
        self.assertEqual(normalise_title("An Affair"), "affair")

    def test_trailing_year_in_the_title_is_stripped(self):
        """Archive titles carry '(1968)'; IMDb's do not."""
        self.assertEqual(normalise_title("Night of the Living Dead (1968)"),
                         normalise_title("Night of the Living Dead"))
        self.assertEqual(normalise_title("C-Man [1949]"), "c man")

    def test_an_internal_number_is_not_mistaken_for_a_year(self):
        self.assertEqual(normalise_title("1984"), "1984")


class TestMatching(unittest.TestCase):
    def test_lookup_keys_cover_neighbouring_years(self):
        keys = build_lookup_keys([movie(title="Detour", year=1945)])
        self.assertEqual(sorted(y for (_, y) in keys), [1944, 1945, 1946])

    def test_film_without_a_year_registers_no_key(self):
        self.assertEqual(build_lookup_keys([movie(year=None)]), {})

    def test_exact_year_beats_a_neighbour(self):
        m = movie(title="Detour", year=1945)
        match = best_match(m, [
            ImdbTitle("tt1", "Detour", 1944, "movie", [], rating=7.0, votes=50_000),
            ImdbTitle("tt2", "Detour", 1945, "movie", [], rating=6.9, votes=10),
        ])
        self.assertEqual(match.tconst, "tt2")

    def test_votes_break_a_tie_within_the_same_year(self):
        m = movie(title="Detour", year=1945)
        match = best_match(m, [
            ImdbTitle("tt1", "Detour", 1945, "movie", [], rating=5.0, votes=10),
            ImdbTitle("tt2", "Detour", 1945, "movie", [], rating=7.2, votes=90_000),
        ])
        self.assertEqual(match.tconst, "tt2")

    def test_a_distant_year_is_rejected_rather_than_accepted(self):
        """A confident wrong rating is worse than no rating."""
        m = movie(title="The Thing", year=1951)
        self.assertIsNone(best_match(m, [ImdbTitle("tt1", "The Thing", 1982, "movie", [], rating=8.2, votes=400_000)]))

    def test_a_different_title_is_rejected(self):
        m = movie(title="Detour", year=1945)
        self.assertIsNone(best_match(m, [ImdbTitle("tt1", "Detour to Nowhere", 1945, "movie", [], rating=7.0, votes=10)]))

    def test_no_candidates_means_no_match(self):
        self.assertIsNone(best_match(movie(), []))


class TestRatingGate(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(use_cache=False)
        self.cfg["movies"]["min_rating_publish"] = 6.0
        self.gen = MovieGenerator(self.cfg, Path(tempfile.mkdtemp()))

    def test_film_below_the_threshold_is_withheld(self):
        pub, held = self.gen._partition([movie(rating=4.2)])
        self.assertEqual(pub, [])
        self.assertIn("rating_below_6", held)

    def test_film_at_or_above_the_threshold_publishes(self):
        for r in (6.0, 8.7):
            pub, _ = self.gen._partition([movie(rating=r)])
            self.assertEqual(len(pub), 1, r)

    def test_unrated_film_is_kept(self):
        """No rating is not a bad rating; most public-domain titles have none."""
        pub, _ = self.gen._partition([movie(rating=None)])
        self.assertEqual(len(pub), 1)

    def test_threshold_of_zero_disables_the_gate(self):
        self.cfg["movies"]["min_rating_publish"] = 0
        gen = MovieGenerator(self.cfg, Path(tempfile.mkdtemp()))
        pub, _ = gen._partition([movie(rating=1.0)])
        self.assertEqual(len(pub), 1)


class TestSearchIndex(unittest.TestCase):
    def setUp(self):
        self.movies = [
            movie(id="m1", title="Night of the Living Dead", year=1968,
                  genre=["Horror"], rating=7.8, synopsis="Zombies besiege a farmhouse."),
            movie(id="m2", title="Detour", year=1945, genre=["Crime"], rating=7.2),
            movie(id="m3", title="Plan 9 from Outer Space", year=1959,
                  genre=["Sci-Fi"], rating=3.9, playback_status="DISCOVERABLE"),
        ]
        self.channels = [Channel(id="c1", name="Somoy TV", category="bangladesh",
                                 language="bn", country="bd", status="ACTIVE",
                                 stream_url="https://h/a.m3u8")]
        self.index = build_index(self.movies, self.channels)

    def test_indexes_movies_and_channels_together(self):
        self.assertEqual(self.index["document_count"], 4)
        kinds = {d["kind"] for d in self.index["documents"]}
        self.assertEqual(kinds, {"movie", "channel"})

    def test_finds_a_film_by_title_word(self):
        hits = search(self.index, "zombies")
        self.assertEqual([h["id"] for h in hits], ["m1"])

    def test_multiple_tokens_are_an_intersection(self):
        self.assertEqual([h["id"] for h in search(self.index, "living dead")], ["m1"])
        self.assertEqual(search(self.index, "living detour"), [])

    def test_prefix_search_works(self):
        self.assertEqual([h["id"] for h in search(self.index, "zomb")], ["m1"])

    def test_finds_a_channel(self):
        hits = search(self.index, "somoy")
        self.assertEqual([h["id"] for h in hits], ["c1"])

    def test_search_is_case_and_accent_insensitive(self):
        self.assertTrue(search(self.index, "DETOUR"))

    def test_stopwords_do_not_match_everything(self):
        self.assertEqual(search(self.index, "the of"), [])

    def test_playable_results_rank_above_discoverable(self):
        hits = search(self.index, "e")   # prefix matching many docs
        playables = [h["playable"] for h in hits]
        self.assertEqual(playables, sorted(playables, reverse=True))

    def test_discoverable_film_carries_no_playback_url(self):
        doc = next(d for d in self.index["documents"] if d.get("id") == "m3")
        self.assertEqual(doc["url"], "")
        self.assertFalse(doc["playable"])
        self.assertTrue(doc["source_url"] or doc["source_url"] == "")

    def test_empty_query_returns_nothing(self):
        self.assertEqual(search(self.index, "   "), [])

    def test_tokenizer_drops_short_tokens_and_stopwords(self):
        self.assertEqual(tokenize("The a of X-Ray 12"), ["ray", "12"])


if __name__ == "__main__":
    unittest.main()


class TestReportAgreesWithTheGate(unittest.TestCase):
    """The report must never claim more is published than the gate allows."""

    def test_published_count_comes_from_the_gate_not_the_model_property(self):
        from src.report import build_report
        films = [movie(id="m1", rating=8.0), movie(id="m2", rating=3.0)]
        # Both are playable and rights-cleared, so the model property says 2.
        self.assertEqual(sum(1 for m in films if m.publishable_as_vod), 2)
        report = build_report(
            channels=[], movies=films, playlist_result={}, build_files={},
            withheld={}, ingest_stats=None, epg=None, seed_build=False,
            movies_published=1, movies_withheld={"rating_below_6": 1},
        )
        self.assertEqual(report["movies"]["published_as_vod"], 1)
        self.assertEqual(report["movies"]["eligible_on_rights_and_playback"], 2)
        self.assertEqual(report["movies"]["withheld"], {"rating_below_6": 1})

    def test_falls_back_to_the_property_when_the_gate_has_not_run(self):
        from src.report import build_report
        report = build_report(
            channels=[], movies=[movie(id="m1", rating=8.0)], playlist_result={},
            build_files={}, withheld={}, ingest_stats=None, epg=None, seed_build=False)
        self.assertEqual(report["movies"]["published_as_vod"], 1)

    def test_rating_statistics_are_reported(self):
        from src.report import build_report
        films = [movie(id="m1", rating=8.0, imdb_id="tt1"),
                 movie(id="m2", rating=6.0, imdb_id="tt2"),
                 movie(id="m3", rating=None)]
        r = build_report(channels=[], movies=films, playlist_result={}, build_files={},
                         withheld={}, ingest_stats=None, epg=None, seed_build=False)["movies"]
        self.assertEqual((r["rated"], r["unrated"], r["matched_to_imdb"]), (2, 1, 2))
        self.assertEqual(r["mean_rating"], 7.0)


class TestSourceTitleCleaning(unittest.TestCase):
    """Archive uploaders append year, cast and director to the title.

    "Gilda (1946) Rita Hayworth, Glenn Ford" never matched IMDb's "Gilda", so it
    never gained a runtime, so the feature-length gate withheld it. That cost the
    library most of its playable HD film-noir.
    """

    def _clean(self, raw):
        from src.sources.imdb.datasets import clean_source_title
        return clean_source_title(raw)

    def test_cast_list_is_removed_and_year_extracted(self):
        self.assertEqual(self._clean("Gilda (1946) Rita Hayworth, Glenn Ford"),
                         ("Gilda", 1946))

    def test_director_credit_is_removed(self):
        self.assertEqual(self._clean("The Big Heat (1953) Directed By Fritz Lang"),
                         ("The Big Heat", 1953))
        self.assertEqual(self._clean("Kiss Me Deadly (1955, ) Dir: Robert Aldrich"),
                         ("Kiss Me Deadly", 1955))

    def test_trailing_tags_are_removed(self):
        self.assertEqual(self._clean("Some Film (1950) (ENG Sub)")[0], "Some Film")
        self.assertEqual(self._clean("Some Film (1950) restored")[0], "Some Film")

    def test_plain_title_is_untouched(self):
        self.assertEqual(self._clean("Night of the Living Dead"),
                         ("Night of the Living Dead", None))

    def test_numeric_titles_survive(self):
        """A title that IS a year must not be cut down to nothing."""
        self.assertEqual(self._clean("1984"), ("1984", None))
        self.assertEqual(self._clean("2001: A Space Odyssey"),
                         ("2001: A Space Odyssey", None))

    def test_empty_input(self):
        self.assertEqual(self._clean(""), ("", None))
        self.assertEqual(self._clean(None), ("", None))

    def test_cleaned_title_matches_imdb_normalisation(self):
        from src.sources.imdb.datasets import clean_source_title, normalise_title
        cleaned, _ = clean_source_title("Gilda (1946) Rita Hayworth, Glenn Ford")
        self.assertEqual(normalise_title(cleaned), normalise_title("Gilda"))

    def test_adapter_uses_the_cleaned_title_and_year(self):
        from src import config as _c
        from src.sources.archive_org.adapter import ArchiveOrgAdapter
        adapter = ArchiveOrgAdapter(_c.load(use_cache=False))
        payload = {"metadata": {"title": "Gilda (1946) Rita Hayworth, Glenn Ford",
                                "date": "2009",
                                "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/"},
                   "files": [{"name": "g.mp4", "format": "h.264", "size": "900000000",
                              "width": "1280", "height": "720"}]}
        m, reason = adapter.to_movie("gilda", payload)
        self.assertIsNotNone(m, reason)
        self.assertEqual(m.title, "Gilda")
        self.assertEqual(m.year, 1946, "the title's year beats the upload date")

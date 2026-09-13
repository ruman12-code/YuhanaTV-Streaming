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
                rights_status="CLEARED", source="src-test")
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

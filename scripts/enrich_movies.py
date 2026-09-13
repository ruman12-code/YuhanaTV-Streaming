#!/usr/bin/env python3
"""Attach IMDb ratings, ids and genres to the catalogue, then build the search index.

    python3 scripts/enrich_movies.py            # match, rate, index
    python3 scripts/enrich_movies.py --dry-run  # report matches, write nothing

Uses IMDb's public non-commercial datasets: no API key, no account. Unmatched
films keep rating None, which is a statement that we do not know rather than a
claim that they are bad.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config                                        # noqa: E402
from src.models import Movie, Channel, load_channels, read_json, write_json  # noqa: E402
from src.sources.imdb.datasets import (ImdbDatasets, build_lookup_keys,      # noqa: E402
                                       best_match, normalise_title)
from src.search import build_index                            # noqa: E402

MOVIES_FILE = REPO / "data" / "movies" / "movies.json"
INDEX_FILE = REPO / "data" / "search-index.json"
CHANNELS_DIR = REPO / "data" / "channels"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-imdb", action="store_true",
                    help="rebuild the search index without re-fetching IMDb")
    args = ap.parse_args()

    cfg = config.load()
    payload = read_json(MOVIES_FILE, {}) or {}
    movies = [Movie.from_dict(m) for m in payload.get("movies", [])]
    if not movies:
        print("no movies in the catalogue", file=sys.stderr)
        return 1

    stats = Counter()
    if not args.skip_imdb:
        keys = build_lookup_keys(movies)
        print(f"{len(movies)} films -> {len(keys)} lookup keys")

        datasets = ImdbDatasets(cfg)
        print("streaming title.basics.tsv.gz ...")
        titles = datasets.find_titles(keys)
        print(f"  {len(titles)} candidate IMDb titles matched by title+year")

        print("streaming title.ratings.tsv.gz ...")
        datasets.attach_ratings(titles)
        rated = sum(1 for t in titles.values() if t.rating is not None)
        print(f"  {rated} of those carry a rating")

        # Group candidates by the key each film asked for.
        by_key: dict[tuple, list] = {}
        for t in titles.values():
            by_key.setdefault((normalise_title(t.title), t.year), []).append(t)

        for m in movies:
            norm = normalise_title(m.title)
            if m.year is None:
                stats["no year, cannot match"] += 1
                continue
            candidates = []
            for delta in (0, -1, 1):
                candidates.extend(by_key.get((norm, m.year + delta), []))
            match = best_match(m, candidates)
            if match is None:
                stats["no IMDb match"] += 1
                continue
            if match.is_adult:
                # IMDb's own flag, as a second gate behind the keyword screen.
                m.playback_status = "EXCLUDED"
                m.notes = (m.notes + "; " if m.notes else "") + "excluded: IMDb isAdult"
                stats["excluded by IMDb adult flag"] += 1
                continue
            m.imdb_id = match.tconst
            if match.rating is not None:
                m.rating = match.rating
                stats["rated"] += 1
            else:
                stats["matched but unrated on IMDb"] += 1
            for g in match.genres:
                if g not in m.genre:
                    m.genre.append(g)

        print()
        for k, v in stats.most_common():
            print(f"  {v:4}  {k}")

        rated_now = [m for m in movies if m.rating is not None]
        if rated_now:
            print(f"  rating range {min(m.rating for m in rated_now):.1f}"
                  f"-{max(m.rating for m in rated_now):.1f}, "
                  f"mean {sum(m.rating for m in rated_now)/len(rated_now):.2f}")
            threshold = float(cfg.get_path("movies.min_rating_publish", 0) or 0)
            if threshold:
                below = [m for m in rated_now if m.rating < threshold]
                print(f"  {len(below)} rated films fall below the {threshold} "
                      f"publish threshold and will be withheld")
                print(f"  {len(movies) - len(rated_now)} films are unrated and are kept: "
                      f"no rating is not a bad rating")

    channels = load_channels(CHANNELS_DIR)
    index = build_index(movies, channels)
    print(f"\nsearch index: {index['document_count']} documents, "
          f"{index['token_count']} tokens")

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    write_json(MOVIES_FILE, {"count": len(movies),
                             "movies": [m.to_dict() for m in movies]})
    write_json(INDEX_FILE, index)
    print(f"wrote {MOVIES_FILE.relative_to(REPO)} and {INDEX_FILE.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

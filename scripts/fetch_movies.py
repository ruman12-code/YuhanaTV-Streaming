#!/usr/bin/env python3
"""Populate data/movies/movies.json from the configured movie sources.

    python3 scripts/fetch_movies.py                # all enabled movie sources
    python3 scripts/fetch_movies.py --dry-run      # report, write nothing
    python3 scripts/fetch_movies.py --limit 20

Existing titles keep their verified playback status: a refetch must not quietly
downgrade a film the VOD validator has already proven.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config                                        # noqa: E402
from src.models import Movie, read_json, write_json           # noqa: E402
from src.sources.archive_org.adapter import ArchiveOrgAdapter  # noqa: E402

MOVIES_FILE = REPO / "data" / "movies" / "movies.json"
SOURCES_FILE = REPO / "data" / "sources" / "sources.json"


def load_existing() -> dict[str, Movie]:
    payload = read_json(MOVIES_FILE, {}) or {}
    return {m["id"]: Movie.from_dict(m) for m in payload.get("movies", [])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cfg = config.load()
    registry = read_json(SOURCES_FILE, {}) or {}
    sources = [s for s in registry.get("sources", [])
               if s.get("kind") == "movies" and s.get("enabled") is not False
               and s.get("authorization_status") != "DENIED"]
    if not sources:
        print("no enabled movie sources in data/sources/sources.json", file=sys.stderr)
        return 1

    existing = load_existing()
    collected: dict[str, Movie] = {}
    stats = Counter()
    rejections = Counter()

    for src in sources:
        if src.get("adapter") != "archive_org":
            print(f"  no adapter for {src['id']} ({src.get('adapter')}), skipping")
            continue
        adapter = ArchiveOrgAdapter(cfg)
        cap = args.limit or int(cfg.get_path("movies.max_titles_per_source", 300))

        for query in src.get("queries", []):
            label = query.get("label", "?")
            print(f"source {src['id']} / {label}")
            try:
                docs = adapter.search(query["q"], rows=int(query.get("rows", 50)))
            except Exception as exc:  # noqa: BLE001
                print(f"  search failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            print(f"  {len(docs)} results")

            for doc in docs:
                if len(collected) >= cap:
                    break
                identifier = doc.get("identifier")
                if not identifier:
                    continue
                try:
                    payload = adapter.metadata(identifier)
                except Exception as exc:  # noqa: BLE001
                    rejections[f"metadata_error:{type(exc).__name__}"] += 1
                    continue
                movie, reason = adapter.to_movie(identifier, payload)
                if movie is None:
                    rejections[reason[:48]] += 1
                    continue
                if movie.id in collected:
                    stats["duplicate"] += 1
                    continue

                prev = existing.get(movie.id)
                if prev and prev.playback_url == movie.playback_url:
                    # Preserve what has already been proven by fetching bytes.
                    movie.playback_status = prev.playback_status
                    movie.last_verified = prev.last_verified
                    if prev.rating is not None:
                        movie.rating = prev.rating
                hint = query.get("genre")
                if hint and hint not in movie.genre:
                    movie.genre.append(hint)
                collected[movie.id] = movie
                stats[movie.rights_status] += 1
                rule = movie.notes.split("]")[0].replace("rights[", "") if "rights[" in movie.notes else "?"
                stats[f"  cleared by: {rule}"] += 1
                time.sleep(0.2)   # be a polite guest on a free archive

    print()
    print(f"collected {len(collected)} titles")
    for k, v in stats.most_common():
        print(f"  {k:24} {v}")
    if rejections:
        print("  rejected:")
        for k, v in rejections.most_common(10):
            print(f"    {v:4}  {k}")

    cleared = sum(1 for m in collected.values() if m.rights_status == "CLEARED")
    print(f"  rights CLEARED: {cleared} / {len(collected)}")
    print("  (a per-item licence is stronger evidence than collection membership; "
          "both are recorded in each title's notes)")

    if args.dry_run:
        print("dry run: nothing written")
        return 0
    if not collected:
        print("nothing collected; leaving data/movies/movies.json untouched", file=sys.stderr)
        return 1

    write_json(MOVIES_FILE, {
        "count": len(collected),
        "movies": [m.to_dict() for m in sorted(collected.values(),
                                               key=lambda m: m.title.lower())],
    })
    print(f"wrote {MOVIES_FILE.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

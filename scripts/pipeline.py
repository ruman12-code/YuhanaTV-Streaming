#!/usr/bin/env python3
"""YuhanaTV build pipeline (spec section 20).

    python3 scripts/pipeline.py ingest        # source M3U  -> data/channels/*.json
    python3 scripts/pipeline.py check         # probe every stream, update status
    python3 scripts/pipeline.py build [--seed]# data        -> playlists/
    python3 scripts/pipeline.py verify        # structural validation of playlists/
    python3 scripts/pipeline.py report [--seed]
    python3 scripts/pipeline.py all  [--seed] # ingest-less: check -> build -> verify -> report

--seed publishes UNVERIFIED channels and stamps every output as a pre-validation
build. Without it, only channels the validator marked ACTIVE are published.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config                                     # noqa: E402
from src.models import (Channel, Movie, load_channels, save_channels,   # noqa: E402
                        read_json, write_json)
from src.ingest.m3u_import import import_file              # noqa: E402
from src.validators.stream_validator import StreamValidator  # noqa: E402
from src.validators.m3u_validator import M3UValidator      # noqa: E402
from src.generators.live import LiveGenerator, build_master  # noqa: E402
from src.report import build_report                        # noqa: E402

CHANNELS_DIR = REPO / "data" / "channels"
MOVIES_DIR = REPO / "data" / "movies"
STATUS_DIR = REPO / "data" / "status"
SOURCES_DIR = REPO / "data" / "sources"
PLAYLISTS = REPO / "playlists"

SEED_NOTE = ("PRE-VALIDATION SEED BUILD. No stream in this tree has been "
             "reachability-tested; entries may not play.")


def cmd_ingest(args) -> int:
    src = Path(args.source or (SOURCES_DIR / "imported-channel-list.m3u"))
    if not src.exists():
        print(f"source not found: {src}", file=sys.stderr)
        return 1
    channels, stats = import_file(src, args.source_id)

    # Preserve status/rights already earned by a previous validation run.
    existing = {c.id: c for c in load_channels(CHANNELS_DIR)}
    for ch in channels:
        prev = existing.get(ch.id)
        if prev and prev.stream_url == ch.stream_url:
            ch.status = prev.status
            ch.rights_status = prev.rights_status
            ch.resolution = prev.resolution
            ch.resolution_label = prev.resolution_label
            ch.codec = prev.codec
            ch.bitrate_bps = prev.bitrate_bps
            ch.last_verified = prev.last_verified
            ch.consecutive_failures = prev.consecutive_failures

    counts = save_channels(CHANNELS_DIR, channels)
    write_json(STATUS_DIR / "ingest-stats.json", stats)
    print(f"ingested {len(channels)} channels from {src.name}")
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:15} {n}")
    if stats["duplicates"]:
        print(f"  dropped {len(stats['duplicates'])} duplicates")
    return 0


def cmd_check(args) -> int:
    cfg = config.load()
    channels = load_channels(CHANNELS_DIR)
    if args.limit:
        channels = channels[: args.limit]
    if not channels:
        print("no channels to check", file=sys.stderr)
        return 1

    validator = StreamValidator(cfg)
    results = validator.validate_all(channels)
    by_id = {r.channel_id: r for r in results}
    threshold = int(cfg.get_path("validation.consecutive_failures_before_offline", 3))

    all_channels = load_channels(CHANNELS_DIR)
    for ch in all_channels:
        r = by_id.get(ch.id)
        if r is None:
            continue
        if r.status in ("OFFLINE", "INVALID"):
            ch.consecutive_failures += 1
            # A single failure demotes rather than condemns; repeated failure sticks.
            ch.status = r.status if ch.consecutive_failures >= threshold else "DEGRADED"
        else:
            ch.consecutive_failures = 0
            ch.status = r.status
        ch.last_verified = r.checked_at
        if r.resolution:
            ch.resolution, ch.resolution_label = r.resolution, r.resolution_label
        if r.codec:
            ch.codec = r.codec
        if r.bitrate_bps:
            ch.bitrate_bps = r.bitrate_bps
        if r.manifest_kind in ("master", "media"):
            ch.stream_type = "hls"
        elif r.manifest_kind == "dash":
            ch.stream_type = "dash"

    save_channels(CHANNELS_DIR, all_channels)
    write_json(STATUS_DIR / "stream-status.json",
               {"checked": len(results), "results": [r.to_dict() for r in results]})

    from collections import Counter
    counts = Counter(r.status for r in results)
    print(f"checked {len(results)} streams: " +
          ", ".join(f"{k}={v}" for k, v in counts.most_common()))
    return 0


def cmd_build(args) -> int:
    cfg = config.load()
    channels = load_channels(CHANNELS_DIR)
    gen = LiveGenerator(cfg, PLAYLISTS)
    result = gen.build(channels, allow_unverified=args.seed)

    have_bd = (PLAYLISTS / "live" / "bangladesh.m3u").exists()
    have_intl = (PLAYLISTS / "live" / "international.m3u").exists()
    have_movies = (PLAYLISTS / "movies" / "movies.m3u").exists()
    size = build_master(cfg, PLAYLISTS,
                        have_live=bool(result.files),
                        have_bangladesh=have_bd,
                        have_international=have_intl,
                        have_movies=have_movies,
                        seed_note=SEED_NOTE if args.seed else "")
    files = dict(result.files)
    files["master.m3u"] = size
    write_json(STATUS_DIR / "build.json", {
        "seed": args.seed,
        "files": files,
        "published": len(result.published),
        "withheld": {k: len(v) for k, v in result.withheld.items()},
    })
    print(f"built {len(files)} playlists, {len(result.published)} channels published")
    for reason, items in sorted(result.withheld.items()):
        print(f"  withheld {len(items):4}  {reason}")
    return 0


def cmd_verify(args) -> int:
    cfg = config.load()
    validator = M3UValidator(cfg, PLAYLISTS)
    result = validator.validate_tree("master.m3u")
    write_json(STATUS_DIR / "playlist-validation.json", result)

    print(f"files={result['files_checked']} items={result['total_items']} "
          f"errors={result['errors']} warnings={result['warnings']}")
    for issue in result["issues"]:
        if issue["severity"] == "error" or args.verbose:
            loc = f"{issue['file']}:{issue['line']}" if issue["line"] else issue["file"]
            print(f"  [{issue['severity']:7}] {loc:34} {issue['code']:16} {issue['message']}")
    if result["cycles"]:
        print("CIRCULAR REFERENCES:", *result["cycles"], sep="\n  ")
    return 0 if result["ok"] else 1


def cmd_report(args) -> int:
    cfg = config.load()
    channels = load_channels(CHANNELS_DIR)
    movies = [Movie.from_dict(m) for m in
              (read_json(MOVIES_DIR / "movies.json", {}) or {}).get("movies", [])]
    playlist_result = read_json(STATUS_DIR / "playlist-validation.json", {}) or {}
    build_state = read_json(STATUS_DIR / "build.json", {}) or {}
    ingest_stats = read_json(STATUS_DIR / "ingest-stats.json", {}) or {}

    gen = LiveGenerator(cfg, PLAYLISTS)
    _, withheld = gen._partition(channels, args.seed)

    report = build_report(
        channels=channels, movies=movies, playlist_result=playlist_result,
        build_files=build_state.get("files", {}), withheld=withheld,
        ingest_stats=ingest_stats, epg=None, seed_build=args.seed,
    )
    write_json(REPO / "validation-report.json", report)
    print(json.dumps({k: v for k, v in report.items()
                      if k in ("generated_at", "build_kind", "live_channels", "playlists")},
                     indent=2, ensure_ascii=False)[:1600])
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ingest"); sp.add_argument("--source"); \
        sp.add_argument("--source-id", default="src-user-m3u"); sp.set_defaults(fn=cmd_ingest)
    sp = sub.add_parser("check"); sp.add_argument("--limit", type=int, default=0); \
        sp.set_defaults(fn=cmd_check)
    sp = sub.add_parser("build"); sp.add_argument("--seed", action="store_true"); \
        sp.set_defaults(fn=cmd_build)
    sp = sub.add_parser("verify"); sp.add_argument("--verbose", action="store_true"); \
        sp.set_defaults(fn=cmd_verify)
    sp = sub.add_parser("report"); sp.add_argument("--seed", action="store_true"); \
        sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("all"); sp.add_argument("--seed", action="store_true"); \
        sp.add_argument("--limit", type=int, default=0); sp.add_argument("--verbose", action="store_true"); \
        sp.set_defaults(fn=None)

    args = p.parse_args()
    if args.cmd == "all":
        rc = cmd_check(args) or cmd_build(args) or cmd_verify(args) or cmd_report(args)
        return rc
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

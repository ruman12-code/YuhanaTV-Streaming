#!/usr/bin/env python3
"""Assign canonical XMLTV ids and generate epg/epg.xml (spec sections 17, 18).

    python3 scripts/build_epg.py
    python3 scripts/build_epg.py --dry-run

Two separate jobs, deliberately kept apart:

1. Give every channel a canonical `tvg-id` from the iptv-org open database. This
   is what lets SS IPTV apply guide data it already holds, which the spec asks us
   to prefer over hosting our own.
2. Emit an XMLTV file carrying those channels. Programme data is written only if
   a real source provided it; schedules are never invented, so a channel with no
   data gets a <channel> entry and no <programme> elements.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config                                            # noqa: E402
from src.models import load_channels, save_channels, write_json, read_json  # noqa: E402
from src.epg.xmltv import XmltvWriter, EpgChannel                 # noqa: E402
from src.sources.iptv_org.channels import (CHANNELS_URL, GUIDES_URL,   # noqa: E402
                                           fetch_json, load_catalog,
                                           match_channel, guide_coverage)

CHANNELS_DIR = REPO / "data" / "channels"
EPG_DIR = REPO / "epg"
STATUS_DIR = REPO / "data" / "status"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="use the epg_id values already stored, fetch nothing")
    args = ap.parse_args()

    cfg = config.load()
    ua = cfg.get_path("validation.user_agent", "Mozilla/5.0")
    channels = load_channels(CHANNELS_DIR)
    if not channels:
        print("no channels", file=sys.stderr)
        return 1

    stats = Counter()
    coverage: dict[str, list[dict]] = {}

    if not args.offline:
        print("fetching the iptv-org channel database ...")
        try:
            rows = fetch_json(CHANNELS_URL, user_agent=ua)
        except Exception as exc:  # noqa: BLE001
            print(f"could not fetch the channel database: {exc}", file=sys.stderr)
            return 1
        print(f"  {len(rows)} canonical channels")
        index = load_catalog(rows)

        for ch in channels:
            match = match_channel(ch, index)
            if match is None:
                stats["no canonical id"] += 1
                continue
            ch.epg_id = match.id
            stats["matched"] += 1
            if not ch.logo and match.logo:
                ch.logo = match.logo
                stats["logo filled in"] += 1

        print("fetching the guide index ...")
        try:
            guides = fetch_json(GUIDES_URL, user_agent=ua)
            coverage = guide_coverage(guides, {c.epg_id for c in channels if c.epg_id})
        except Exception as exc:  # noqa: BLE001
            print(f"  guide index unavailable: {exc}", file=sys.stderr)

    matched = [c for c in channels if c.epg_id]
    print()
    for k, v in stats.most_common():
        print(f"  {v:4}  {k}")
    print(f"  {len(matched)}/{len(channels)} channels carry a canonical tvg-id")
    if coverage:
        print(f"  {len(coverage)} of those have at least one public guide source")

    # Build the XMLTV document. Only ACTIVE channels are included: a guide entry
    # for a channel that is not in the playlist is dead weight on a TV.
    writer = XmltvWriter(
        generator_name=f"{cfg.get_path('site.brand', 'YuhanaTV')} EPG",
        max_bytes=int(cfg.get_path("ssiptv.max_xmltv_bytes", 5 * 1024 * 1024)),
    )
    published = [c for c in channels if c.epg_id and c.status == "ACTIVE"]
    for ch in published:
        writer.add_channel(EpgChannel(
            id=ch.epg_id,
            display_names=[n for n in (ch.name,) if n],
            icon=ch.logo,
            language=ch.language,
        ))

    # Programme data would be added here from a real guide source. Nothing is
    # synthesised in its absence: an invented schedule is worse than none.

    epg_stats = writer.stats(total_channels=len(published) or 1)
    epg_stats["guide_sources_available"] = len(coverage)
    epg_stats["channels_total"] = len(channels)
    epg_stats["channels_with_canonical_id"] = len(matched)

    print()
    print(f"epg: {epg_stats['channels_in_epg']} channels, "
          f"{epg_stats['programmes']} programmes")

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    size = writer.write(EPG_DIR / "epg.xml")
    epg_stats["bytes"] = size
    save_channels(CHANNELS_DIR, channels)
    write_json(STATUS_DIR / "epg.json", {**epg_stats, "guide_coverage": coverage})
    print(f"wrote epg/epg.xml ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

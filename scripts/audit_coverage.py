#!/usr/bin/env python3
"""How many working feeds does each channel have, and where are we thin?

The owner's complaint was that a channel plays fine in another app and not in
this one. The usual reason is not a missing channel but a missing *feed*: the
other app happens to carry a different URL for the same broadcaster, and ours is
the one that died. A channel with one feed is one outage away from a dead tile;
a channel with three is not.

So the measure that matters is not how many channels exist but how many
independent ways there are to reach each one. This reports that, worst first,
for the categories the owner watches.

Writes data/status/coverage-audit.json.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config                                  # noqa: E402
from src.models import load_channels                    # noqa: E402
from src.classify import display_key                    # noqa: E402
from src.generators.live import LiveGenerator           # noqa: E402

CHANNELS = REPO / "data" / "channels"
STATUS = REPO / "data" / "status"


def audit() -> dict:
    cfg = config.load()
    channels = load_channels(CHANNELS)
    gen = LiveGenerator(cfg, REPO / "playlists")
    gen.load_home_probe(STATUS / "home-probe.json")
    published, _ = gen._partition(channels, False)
    pub_ids = {c.id for c in published}

    # Group by what the channel IS, across every record of it.
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for c in channels:
        groups[(c.category, display_key(c.name))].append(c)

    rows = []
    for (category, key), members in groups.items():
        live = [c for c in members if c.id in pub_ids]
        hosts = {urlparse(c.stream_url).netloc.lower() for c in live if c.stream_url}
        rows.append({
            "channel": sorted(c.name for c in members)[0],
            "category": category,
            "feeds_known": len(members),
            "feeds_working": len(live),
            "distinct_operators": len(hosts),
            "best_resolution": max((c.resolution for c in live if c.resolution),
                                   default="", key=lambda r: int(r.split("x")[1])
                                   if "x" in r else 0),
            "video_confirmed": any(c.video_confirmed for c in live),
        })

    watched = {str(c).lower() for c in
               (cfg.get_path("validation.deep_check_categories") or ())}
    by_cat: dict[str, dict] = {}
    for r in rows:
        b = by_cat.setdefault(r["category"], {"channels": 0, "no_working_feed": 0,
                                              "single_feed": 0, "two_or_more": 0})
        b["channels"] += 1
        if r["feeds_working"] == 0:
            b["no_working_feed"] += 1
        elif r["feeds_working"] == 1:
            b["single_feed"] += 1
        else:
            b["two_or_more"] += 1

    fragile = sorted((r for r in rows
                      if r["category"] in watched and r["feeds_working"] <= 1),
                     key=lambda r: (r["feeds_working"], r["channel"]))

    return {
        "channels_by_identity": len(rows),
        "by_category": dict(sorted(by_cat.items(),
                                   key=lambda kv: -kv[1]["channels"])),
        "watched_categories": sorted(watched),
        "fragile_in_watched_categories": fragile,
        "$note": ("feeds_working counts the independent URLs currently "
                  "publishable for one channel. One means a single outage "
                  "takes the tile away; zero means the channel is known and "
                  "currently unreachable, which is where another app carrying "
                  "a different feed looks better than this one."),
    }


def main() -> int:
    r = audit()
    STATUS.mkdir(parents=True, exist_ok=True)
    (STATUS / "coverage-audit.json").write_text(
        json.dumps(r, indent=1, ensure_ascii=False) + "\n")

    print(f"{r['channels_by_identity']} distinct channels across all feeds\n")
    print(f"{'category':15} {'chans':>6} {'2+ feeds':>9} {'1 feed':>7} {'none':>6}")
    for cat, b in r["by_category"].items():
        print(f"{cat:15} {b['channels']:6} {b['two_or_more']:9} "
              f"{b['single_feed']:7} {b['no_working_feed']:6}")
    frag = r["fragile_in_watched_categories"]
    print(f"\nthin or missing in {', '.join(r['watched_categories']) or 'n/a'}: {len(frag)}")
    for row in frag[:30]:
        mark = "NONE" if row["feeds_working"] == 0 else "one "
        print(f"  {mark} {row['channel'][:30]:30} known {row['feeds_known']:2} "
              f"best {row['best_resolution'] or '-':9} "
              f"{'decoded' if row['video_confirmed'] else ''}")
    if len(frag) > 30:
        print(f"  ... and {len(frag) - 30} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())

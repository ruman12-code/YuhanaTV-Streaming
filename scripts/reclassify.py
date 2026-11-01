#!/usr/bin/env python3
"""Re-apply the current classification rules to the registry already on disk.

Classification happens at ingest, so a corrected rule only reaches the tree on
the next successful fetch of every source. That is fine in CI and useless when
the rule was corrected *because* a channel is in the wrong place: the fix should
be visible without waiting for a fetch, and without pretending to re-measure
anything. This re-runs only the name and group rules over the records already
held, and touches nothing that came from a probe.

    python3 scripts/reclassify.py            # show what would change
    python3 scripts/reclassify.py --write    # apply it
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.models import load_channels, save_channels                  # noqa: E402
from src.classify import categorise, is_bangladeshi                  # noqa: E402

CHANNELS = REPO / "data" / "channels"


def country_from_canonical_id(ch) -> str:
    """The country in a canonical tvg-id such as ATNBangla.bd, or ''.

    The owner's own playlist carries no tvg-ids, so ingest decides a country
    with nothing to go on and leaves it blank. The EPG step attaches canonical
    ids afterwards, by which time that decision has already been made; 69
    channels were left countryless that way, two of them Bangladeshi.
    """
    m = re.search(r"\.([a-z]{2})$", (ch.epg_id or "").lower())
    return m.group(1) if m else ""


def intended(ch) -> str:
    """The category the current rules would give this channel at ingest."""
    group = ch.tags[0] if ch.tags else ""
    if (ch.country or "").lower() == "bd" or is_bangladeshi(ch.name, ch.country):
        return "bangladesh"
    return categorise(ch.name, group)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    channels = load_channels(CHANNELS)
    filled = 0
    for ch in channels:
        if not ch.country:
            c = country_from_canonical_id(ch)
            if c:
                ch.country = c
                filled += 1
    if filled:
        print(f"filled {filled} missing countr{'y' if filled == 1 else 'ies'} "
              f"from canonical ids")
    moves = []
    for ch in channels:
        want = intended(ch)
        if want != ch.category:
            moves.append((ch, ch.category, want))

    if not moves:
        print("every channel is already where the current rules put it")
        if filled and args.write:
            save_channels(CHANNELS, channels)
            print("countries written")
        return 0

    pairs = Counter((a, b) for _, a, b in moves)
    print(f"{len(moves)} channels would move:")
    for (a, b), n in pairs.most_common():
        print(f"  {n:5}  {a} -> {b}")
    print()
    for ch, a, b in sorted(moves, key=lambda m: m[0].name)[:25]:
        print(f"  {ch.name[:34]:34} {(ch.country or '--'):3} {a:13} -> {b}")
    if len(moves) > 25:
        print(f"  ... and {len(moves) - 25} more")

    if not args.write:
        print("\nnothing written; pass --write to apply")
        return 0

    for ch, _, want in moves:
        ch.category = want
    counts = save_channels(CHANNELS, channels)
    print(f"\nwritten. {sum(counts.values())} channels across {len(counts)} categories")
    return 0


if __name__ == "__main__":
    sys.exit(main())

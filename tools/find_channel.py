#!/usr/bin/env python3
"""Print channel ids matching a name, for editing data/favourites.json by hand.

    python3 tools/find_channel.py "star sports"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    needle = " ".join(sys.argv[1:]).lower()
    rows = []
    for f in sorted((REPO / "data" / "channels").glob("*.json")):
        payload = json.loads(f.read_text())
        items = payload if isinstance(payload, list) else payload.get("channels", [])
        for c in items:
            if needle in (c.get("name", "")).lower():
                rows.append(c)
    if not rows:
        print(f"nothing matches {needle!r}")
        return 1
    rows.sort(key=lambda c: c.get("name", ""))
    for c in rows:
        ok, total = c.get("checks_ok") or 0, c.get("checks_total") or 0
        rel = f"{ok}/{total}" if total else "unchecked"
        print(f'  "{c["id"]}"   {c["name"][:38]:38} {c.get("category", ""):13} '
              f'{c.get("status", ""):9} {rel}')
    print(f"\n{len(rows)} match(es). Add an id to the channel_ids array in "
          f"data/favourites.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

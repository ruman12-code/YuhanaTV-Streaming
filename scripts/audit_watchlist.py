#!/usr/bin/env python3
"""Answer "is this channel missing, and why?" for every name on the watchlist.

The owner noticed &TV, &Movies and &Music were absent and had to ask. A build
should already know: every name in data/watchlist.json is checked against the
registry and the published tree, and the answer lands in validation-report.json
so the next question of this kind answers itself.

Four outcomes per name:
  published   - on the TV right now, with the screens it appears on
  withheld    - in the registry, not published, with the gate that stopped it
  rejected    - refused at ingest, with the rule that refused it
  absent      - in none of the configured sources at all
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config                                  # noqa: E402
from src.models import load_channels                    # noqa: E402
from src.generators.live import LiveGenerator           # noqa: E402

CHANNELS = REPO / "data" / "channels"
STATUS = REPO / "data" / "status"
PLAYLISTS = REPO / "playlists"


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def audit() -> dict:
    cfg = config.load()
    names = json.loads((REPO / "data" / "watchlist.json").read_text()).get("names", [])
    channels = load_channels(CHANNELS)

    gen = LiveGenerator(cfg, PLAYLISTS)
    gen.load_home_probe(STATUS / "home-probe.json")
    published, withheld = gen._partition(channels, False)
    published_ids = {c.id for c in published}
    hold_reason = {c.id: reason for reason, items in withheld.items() for c in items}

    try:
        rejected = json.loads((STATUS / "ingest-stats.json").read_text())["rejected"]
    except (OSError, ValueError, KeyError):
        rejected = []

    rows = []
    for name in names:
        key = _norm(name)
        hits = [c for c in channels if key in _norm(c.name)]
        rej = [r for r in rejected if key in _norm(r.get("title", ""))]

        if not hits and not rej:
            rows.append({"name": name, "verdict": "absent",
                         "detail": "no configured source lists this channel"})
            continue
        if not hits:
            rows.append({"name": name, "verdict": "rejected",
                         "detail": rej[0].get("reason", ""),
                         "variants": len(rej)})
            continue

        live = [c for c in hits if c.id in published_ids]
        if live:
            rows.append({"name": name, "verdict": "published",
                         "detail": f"{len(live)} of {len(hits)} variants on the TV",
                         "examples": sorted(c.name for c in live)[:4]})
            continue

        reasons = sorted({hold_reason.get(c.id, "unknown") for c in hits})
        worst = sorted(hits, key=lambda c: -(c.checks_ok or 0))[0]
        rows.append({
            "name": name, "verdict": "withheld",
            "detail": ", ".join(reasons),
            "variants": len(hits),
            "best_variant": {
                "name": worst.name, "status": worst.status,
                "checks": f"{worst.checks_ok}/{worst.checks_total}",
                "reason": worst.status_reason or "",
            },
        })

    summary = {}
    for r in rows:
        summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1
    return {"checked": len(rows), "summary": summary, "rows": rows}


def main() -> int:
    result = audit()
    out = STATUS / "watchlist-audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n")

    s = result["summary"]
    print(f"watchlist: {result['checked']} names  "
          + "  ".join(f"{k} {v}" for k, v in sorted(s.items())))
    for r in result["rows"]:
        if r["verdict"] == "published":
            continue
        print(f"  {r['verdict']:9} {r['name'][:24]:24} {r['detail'][:60]}")
        best = r.get("best_variant")
        if best:
            print(f"            └ best: {best['name'][:30]:30} {best['status']:9} "
                  f"{best['checks']:7} {best['reason'][:36]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

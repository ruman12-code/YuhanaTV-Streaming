#!/usr/bin/env python3
"""Emit the published channels as compact JSON for the companion page."""
from __future__ import annotations
import json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src import config                                  # noqa: E402
from src.models import load_channels                    # noqa: E402
from src.generators.live import LiveGenerator           # noqa: E402
from src.generators.countries import country_label      # noqa: E402


def main() -> int:
    cfg = config.load()
    gen = LiveGenerator(cfg, REPO / "playlists")
    gen.load_home_probe(REPO / "data" / "status" / "home-probe.json")
    published, _ = gen._partition(load_channels(REPO / "data" / "channels"), False)
    rows = [[c.id, c.name, c.category, (c.country or "").lower(), c.logo or ""]
            for c in sorted(published, key=lambda c: c.name.lower())]
    labels = {code: country_label(code) or code.upper()
              for code in {r[3] for r in rows if r[3]}}
    out = REPO / "data" / "status" / "channel-index.json"
    out.write_text(json.dumps({"countries": labels, "rows": rows},
                              ensure_ascii=False, separators=(",", ":")))
    print(f"{len(rows)} channels -> {out.relative_to(REPO)} "
          f"({out.stat().st_size / 1048576:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

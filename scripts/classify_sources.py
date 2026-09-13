#!/usr/bin/env python3
"""Write data/sources/host-trust.json from the current channel registry."""
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from src.models import load_channels, write_json          # noqa: E402
from src.util.urls import host_of                         # noqa: E402
from src.sources.host_trust import classify               # noqa: E402

channels = load_channels(REPO / "data" / "channels")
by_host: dict[str, list] = defaultdict(list)
for ch in channels:
    by_host[host_of(ch.stream_url)].append(ch)

rows, tiers = [], Counter()
for host, items in sorted(by_host.items()):
    tier, reason = classify(host)
    tiers[tier] += len(items)
    rows.append({"host": host, "channels": len(items), "trust_tier": tier, "reason": reason,
                 "examples": sorted(c.name for c in items)[:4]})

rows.sort(key=lambda r: (r["trust_tier"], -r["channels"], r["host"]))
write_json(REPO / "data" / "sources" / "host-trust.json", {
    "generated_from": "data/channels/*.json",
    "hosts": len(rows),
    "channels_by_tier": dict(tiers.most_common()),
    "note": "trust_tier describes provenance only. It is not a licensing determination "
            "and does not by itself authorise redistribution.",
    "rows": rows,
})
print(f"{len(rows)} hosts classified")
for tier, n in tiers.most_common():
    print(f"  {tier:22} {n:4} channels")

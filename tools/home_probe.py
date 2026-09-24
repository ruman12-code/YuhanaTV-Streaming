#!/usr/bin/env python3
"""Probe every channel from the owner's own network, not from a CI runner.

Why this exists
---------------
Every "ACTIVE" this project has ever produced means one thing: an origin served
a GitHub runner in the United States. The television is in Bangladesh. Those are
different questions, and they disagree in both directions - a South Asian
broadcaster fenced to its home region refuses the runner and serves the owner,
while a US or European service does the reverse.

Nothing in CI can close that gap. This script can: run it on any computer on the
same home network as the TV, and its results become the authority. The build
step reads them and overrides its own verdict wherever they disagree.

What it is not
--------------
It is not the television. It shares the TV's ISP, country and routing, which is
the whole of the geography problem, but it is not the TV's decoder. A stream this
script fetches happily can still fail on the set because of the codec, the
container or the DRM. Treat a pass here as "reachable from your house", which is
strictly more than CI could ever tell you, and not yet as "plays on the Toshiba".

Usage
-----
    python3 tools/home_probe.py                 # probe everything, resumable
    python3 tools/home_probe.py --limit 200     # try a sample first
    python3 tools/home_probe.py --category bangladesh
    python3 tools/home_probe.py --published-only

Stop it at any time with Ctrl-C: finished results are already on disk and the
next run continues where it left off. When it is done, commit the file it wrote:

    git add data/status/home-probe.json && git commit -m "home probe" && git push

Standard library only, and it never sends anything anywhere - it writes one
local file.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config                                        # noqa: E402
from src.models import load_channels                          # noqa: E402
from src.validators.stream_validator import StreamValidator    # noqa: E402

CHANNELS_DIR = REPO / "data" / "channels"
OUT = REPO / "data" / "status" / "home-probe.json"

_stop = False


def _on_sigint(signum, frame):  # noqa: ARG001
    global _stop
    if _stop:                       # second Ctrl-C: give up immediately
        raise KeyboardInterrupt
    _stop = True
    print("\n  stopping after the probes already in flight; "
          "results so far are kept", file=sys.stderr)


def _load_previous() -> dict:
    if not OUT.exists():
        return {}
    try:
        return json.loads(OUT.read_text()).get("results", {})
    except (ValueError, OSError):
        return {}


def _save(results: dict, meta: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "$comment": (
            "Measured from the owner's own network, not from CI. The build step "
            "treats these verdicts as authoritative wherever they disagree with "
            "its own. Produced by tools/home_probe.py."),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **meta,
        "results": results,
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(OUT)                # atomic: a Ctrl-C never leaves a half file


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0,
                    help="probe only the first N channels (0 = all)")
    ap.add_argument("--category", default="",
                    help="probe one category only, e.g. bangladesh")
    ap.add_argument("--published-only", action="store_true",
                    help="probe only channels currently on the TV")
    ap.add_argument("--recheck", action="store_true",
                    help="ignore previous results and probe everything again")
    ap.add_argument("--concurrency", type=int, default=0,
                    help="parallel probes (default: the config value, halved, "
                         "because a home connection is not a datacentre)")
    args = ap.parse_args()

    cfg = config.load()
    if args.concurrency:
        cfg["validation"]["concurrency"] = args.concurrency
    else:
        cfg["validation"]["concurrency"] = max(
            4, int(cfg.get_path("validation.concurrency", 40)) // 4)

    channels = load_channels(CHANNELS_DIR)
    if args.category:
        channels = [c for c in channels if c.category == args.category]
    if args.published_only:
        channels = [c for c in channels if c.status == "ACTIVE"]

    done = {} if args.recheck else _load_previous()
    todo = [c for c in channels if c.id not in done]
    if args.limit:
        todo = todo[: args.limit]

    if not todo:
        print(f"nothing to do: {len(done)} channels already probed. "
              f"Use --recheck to measure them again.")
        return 0

    print(f"probing {len(todo)} channels from this network "
          f"({len(done)} already done, {len(channels)} in total)")
    print(f"concurrency {cfg.get_path('validation.concurrency')}; "
          f"Ctrl-C stops cleanly and keeps what is finished\n")

    signal.signal(signal.SIGINT, _on_sigint)
    validator = StreamValidator(cfg)
    started = time.monotonic()
    completed = 0

    with ThreadPoolExecutor(max_workers=int(cfg.get_path("validation.concurrency"))) as pool:
        futures = {}
        for ch in todo:
            if _stop:
                break
            futures[pool.submit(validator.validate, ch)] = ch
        for fut in as_completed(futures):
            ch = futures[fut]
            try:
                r = fut.result()
                done[ch.id] = {
                    "name": ch.name,
                    "status": r.status,
                    "http": r.http_status,
                    "reason": r.error or "",
                    "ms": round(r.latency_ms),
                }
            except Exception as exc:                    # noqa: BLE001
                done[ch.id] = {"name": ch.name, "status": "OFFLINE",
                               "http": 0, "reason": f"{type(exc).__name__}: {exc}",
                               "ms": 0}
            completed += 1
            if completed % 25 == 0:
                ok = sum(1 for v in done.values() if v["status"] in ("ACTIVE", "DEGRADED"))
                rate = completed / max(time.monotonic() - started, 1)
                left = (len(todo) - completed) / max(rate, 0.01)
                print(f"  {completed}/{len(todo)}  playable so far: {ok}  "
                      f"~{left / 60:.0f} min left", flush=True)
                _save(done, {"vantage": "home-network", "complete": False})

    ok = sum(1 for v in done.values() if v["status"] in ("ACTIVE", "DEGRADED"))
    _save(done, {"vantage": "home-network", "complete": not _stop and not args.limit})
    print(f"\nprobed {len(done)} channels from your network: "
          f"{ok} reachable, {len(done) - ok} not")
    print(f"written to {OUT.relative_to(REPO)}")
    print("\nCommit and push it, and the next build will use your measurements "
          "instead of the runner's:")
    print("  git add data/status/home-probe.json")
    print('  git commit -m "home probe from Bangladesh"')
    print("  git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())

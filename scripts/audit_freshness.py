#!/usr/bin/env python3
"""Is the list actually being refreshed, and how long can a dead tile survive?

The owner asked three things at once: how often the list refreshes, whether the
channels shown are really running, and how long a dead one sits there before
anyone notices. All three are measurable from the registry, and the answers
belong in every report rather than in an answer to a question.

Writes data/status/freshness-audit.json.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config                                   # noqa: E402
from src.models import load_channels                     # noqa: E402
from src.generators.live import LiveGenerator            # noqa: E402

CHANNELS = REPO / "data" / "channels"
STATUS = REPO / "data" / "status"


def _age_hours(stamp: str, now: datetime) -> float | None:
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - then).total_seconds() / 3600.0


def audit() -> dict:
    now = datetime.now(timezone.utc)
    cfg = config.load()
    channels = load_channels(CHANNELS)

    gen = LiveGenerator(cfg, REPO / "playlists")
    gen.load_home_probe(STATUS / "home-probe.json")
    published, withheld = gen._partition(channels, False)

    ages = [a for a in (_age_hours(c.last_verified, now) for c in channels)
            if a is not None]
    never = sum(1 for c in channels if not c.last_verified)

    # How long a channel that dies right now can stay on the TV: it must fail
    # this many probes in a row, and a probe comes round only as often as the
    # oldest check is old.
    strikes = int(cfg.get_path("validation.consecutive_failures_before_offline", 2))
    worst_gap = max(ages) if ages else 0.0
    typical_gap = statistics.median(ages) if ages else 0.0

    # Channels on the TV right now that are one failed probe from being pulled.
    on_the_brink = [c for c in published if c.consecutive_failures >= strikes - 1
                    and c.consecutive_failures > 0]
    # Published, but no decoder has ever confirmed a picture in it. This is the
    # remaining black-screen risk: the manifest and segment checks are about the
    # outside of the stream, and only a decode speaks to what is in it.
    unproven = [c for c in published if not c.video_confirmed]
    confirmed = [c for c in published if c.video_confirmed]
    blackish = [c for c in published if c.black_fraction >= 0.5]

    buckets = {"<6h": 0, "6-12h": 0, "12-24h": 0, "1-2d": 0, "2-7d": 0, ">7d": 0}
    for a in ages:
        for label, limit in (("<6h", 6), ("6-12h", 12), ("12-24h", 24),
                             ("1-2d", 48), ("2-7d", 168), (">7d", float("inf"))):
            if a < limit:
                buckets[label] += 1
                break

    return {
        "generated_at": now.isoformat(),
        "refresh": {
            "cron": "0 */3 * * *",
            "$note": ("What the schedule asks for. GitHub defers scheduled runs "
                      "under load, so measure the real cadence from the ages "
                      "below rather than from the cron."),
            "check_budget_seconds": cfg.get_path("validation.max_check_seconds", 0),
        },
        "verification_age_hours": {
            "never_verified": never,
            "median": round(typical_gap, 1),
            "p90": round(sorted(ages)[int(len(ages) * 0.9)], 1) if ages else None,
            "worst": round(worst_gap, 1),
            "distribution": buckets,
        },
        "dead_tile_exposure_hours": {
            "strikes_before_pulled": strikes,
            "typical": round(typical_gap * strikes, 1),
            "worst_case": round(worst_gap * strikes, 1),
            "$note": ("A channel that dies must fail this many consecutive "
                      "probes before it leaves the TV, and probes come round "
                      "only as often as the ages above. This is the window in "
                      "which the owner can press a tile and get nothing."),
        },
        "published": {
            "total": len(published),
            "one_probe_from_being_pulled": len(on_the_brink),
            "video_confirmed_by_decoder": len(confirmed),
            "video_never_confirmed": len(unproven),
            "mostly_black_when_decoded": len(blackish),
            "$note_unproven": ("No decoder has yet opened these and got frames "
                              "out. The manifest and segment checks describe "
                              "the outside of a stream; only a decode speaks to "
                              "what is in it. The rotation clears this backlog "
                              "a few hundred channels per run."),
        },
        "withheld": {reason: len(items) for reason, items in sorted(withheld.items())},
    }


def main() -> int:
    r = audit()
    STATUS.mkdir(parents=True, exist_ok=True)
    (STATUS / "freshness-audit.json").write_text(
        json.dumps(r, indent=1, ensure_ascii=False) + "\n")

    va, dt, pub = r["verification_age_hours"], r["dead_tile_exposure_hours"], r["published"]
    print(f"refresh         cron {r['refresh']['cron']}, "
          f"budget {r['refresh']['check_budget_seconds']}s per run")
    print(f"last check      median {va['median']}h, p90 {va['p90']}h, "
          f"worst {va['worst']}h, never checked {va['never_verified']}")
    print(f"               " + "  ".join(f"{k} {v}" for k, v in va["distribution"].items()))
    print(f"dead tile       up to {dt['worst_case']}h on the TV "
          f"({dt['strikes_before_pulled']} strikes x {va['worst']}h worst gap)")
    print(f"published       {pub['total']}, "
          f"{pub['one_probe_from_being_pulled']} one probe from being pulled")
    print(f"decoder         {pub['video_confirmed_by_decoder']} confirmed, "
          f"{pub['video_never_confirmed']} not yet looked at, "
          f"{pub['mostly_black_when_decoded']} mostly black")
    return 0


if __name__ == "__main__":
    sys.exit(main())

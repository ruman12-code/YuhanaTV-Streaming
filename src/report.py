"""validation-report.json (spec section 32)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .models import Channel, Movie, read_json


def _iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_report(*, channels: list[Channel], movies: list[Movie],
                 playlist_result: dict, build_files: dict[str, int],
                 withheld: dict[str, list], ingest_stats: dict | None,
                 epg: dict | None, seed_build: bool,
                 health: dict | None = None, history: dict | None = None,
                 probe_results: list | None = None,
                 movies_published: int | None = None,
                 movies_withheld: dict | None = None) -> dict:
    status_counts = Counter(c.status for c in channels)
    res_counts = Counter(c.resolution_label for c in channels if c.resolution_label)

    broken = [
        {"id": c.id, "name": c.name, "status": c.status,
         "host": c.stream_url.split("/")[2] if "://" in c.stream_url else "",
         "note": c.notes}
        for c in channels if c.status in ("OFFLINE", "INVALID")
    ]

    probe_counts = Counter(r.get("status") for r in (probe_results or []))
    probe_errors = Counter()
    for r in (probe_results or []):
        if r.get("status") not in ("ACTIVE",):
            stage = next((s["name"] for s in reversed(r.get("stages", []))
                          if not s.get("ok")), "none")
            probe_errors[f"{r.get('status')}:{stage}"] += 1

    checked = [c for c in channels if c.checks_total]
    flapping = sorted((c for c in channels if c.is_flapping),
                      key=lambda c: c.reliability)

    return {
        "generated_at": _iso(),
        "build_kind": "pre-validation-seed" if seed_build else "validated",
        "warning": (
            "No stream in this build has been reachability-tested. Channel status is "
            "UNVERIFIED and playlists must not be treated as known-working."
            if seed_build else ""
        ),
        "live_channels": {
            "total": len(channels),
            "active": status_counts.get("ACTIVE", 0),
            "degraded": status_counts.get("DEGRADED", 0),
            "offline": status_counts.get("OFFLINE", 0),
            "invalid": status_counts.get("INVALID", 0),
            "unverified": status_counts.get("UNVERIFIED", 0),
            "by_category": dict(Counter(c.category for c in channels).most_common()),
            "by_country": dict(Counter(c.country or "unknown" for c in channels).most_common()),
        },
        "resolution": {
            "measured_4k": res_counts.get("4K", 0),
            "measured_1080p": res_counts.get("1080p", 0),
            "measured_720p": res_counts.get("720p", 0),
            "measured_576p": res_counts.get("576p", 0),
            "measured_480p": res_counts.get("480p", 0),
            "unmeasured": sum(1 for c in channels if not c.resolution_label),
            "note": "Counts come from #EXT-X-STREAM-INF RESOLUTION only. "
                    "Source-claimed labels such as '(1080p)' in a channel name are never counted.",
        },
        "movies": {
            "catalogue_total": len(movies),
            "playable": sum(1 for m in movies if m.playback_status == "PLAYABLE"),
            "discoverable": sum(1 for m in movies if m.playback_status == "DISCOVERABLE"),
            "excluded": sum(1 for m in movies if m.playback_status == "EXCLUDED"),
            "unverified": sum(1 for m in movies if m.playback_status == "UNVERIFIED"),
            # The real figure comes from the generator's gate, which also applies
            # the rating threshold. Movie.publishable_as_vod knows only about
            # rights and playback, so reporting it here overstated what shipped.
            "published_as_vod": (movies_published
                                 if movies_published is not None
                                 else sum(1 for m in movies if m.publishable_as_vod)),
            "eligible_on_rights_and_playback": sum(1 for m in movies if m.publishable_as_vod),
            "withheld": movies_withheld or {},
            "rated": sum(1 for m in movies if m.rating is not None),
            "unrated": sum(1 for m in movies if m.rating is None),
            "matched_to_imdb": sum(1 for m in movies if m.imdb_id),
            "mean_rating": (round(sum(m.rating for m in movies if m.rating is not None)
                                  / max(1, sum(1 for m in movies if m.rating is not None)), 2)
                            if any(m.rating is not None for m in movies) else None),
        },
        "run_health": health or {"ok": None, "reason": "no validation run recorded yet"},
        "last_probe": {
            "$comment": "What the most recent run actually measured, before the "
                        "3-strike demotion rule is applied. `live_channels` above "
                        "reports stored status, which lags deliberately.",
            "counts": dict(probe_counts.most_common()),
            "failing_stage": dict(probe_errors.most_common()),
        },
        "reliability": {
            "channels_with_history": len(checked),
            "mean_reliability": round(
                sum(c.reliability for c in checked) / len(checked), 4) if checked else None,
            "fully_reliable": sum(1 for c in checked if c.reliability == 1.0),
            "never_once_up": sum(1 for c in checked if c.checks_ok == 0),
            "flapping": [
                {"id": c.id, "name": c.name, "reliability": round(c.reliability, 3),
                 "checks": c.checks_total}
                for c in flapping[:40]
            ],
            "note": "reliability is checks_ok/checks_total across validation runs; "
                    "a channel with checks_total == 0 has never been measured.",
        },
        "history": (history or {}).get("runs", [])[-10:],
        "epg": epg or {"generated": False, "channels_covered": 0,
                       "coverage_percent": 0.0, "note": "EPG is Phase 7"},
        "withheld_from_playlists": {
            reason: [{"id": c.id, "name": c.name} for c in items]
            for reason, items in sorted(withheld.items())
        },
        "withheld_counts": {reason: len(items) for reason, items in sorted(withheld.items())},
        "broken_urls": broken,
        "duplicates": (ingest_stats or {}).get("duplicates", []),
        "ingest": {
            k: v for k, v in (ingest_stats or {}).items()
            if k in ("entries_parsed", "channels_accepted", "needs_custom_headers")
        },
        "playlists": {
            "files": build_files,
            "total_bytes": sum(build_files.values()),
            "structure_ok": playlist_result.get("ok"),
            "structure_errors": playlist_result.get("errors", 0),
            "structure_warnings": playlist_result.get("warnings", 0),
            "circular_references": playlist_result.get("cycles", []),
            "unreachable": playlist_result.get("unreachable", []),
            "issues": playlist_result.get("issues", []),
        },
    }

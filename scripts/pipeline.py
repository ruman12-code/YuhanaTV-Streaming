#!/usr/bin/env python3
"""YuhanaTV build pipeline (spec section 20).

    python3 scripts/pipeline.py ingest        # source M3U  -> data/channels/*.json
    python3 scripts/pipeline.py check         # probe every stream, update status
    python3 scripts/pipeline.py build [--seed]# data        -> playlists/
    python3 scripts/pipeline.py verify        # structural validation of playlists/
    python3 scripts/pipeline.py report [--seed]
    python3 scripts/pipeline.py all  [--seed] # ingest-less: check -> build -> verify -> report

--seed publishes UNVERIFIED channels and stamps every output as a pre-validation
build. Without it, only channels the validator marked ACTIVE are published.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.parse import urlsplit
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config                                     # noqa: E402
from src.models import (Channel, Movie, load_channels, save_channels,   # noqa: E402
                        read_json, write_json)
from src.ingest.m3u_import import import_file              # noqa: E402
from src.validators.stream_validator import StreamValidator  # noqa: E402
from src.validators.m3u_validator import M3UValidator      # noqa: E402
from src.validators.health import assess                    # noqa: E402
from src.generators.live import LiveGenerator, build_master  # noqa: E402
from src.generators.movies import MovieGenerator             # noqa: E402
from src.validators.vod_validator import VodValidator        # noqa: E402
from src.report import build_report                        # noqa: E402
from src.generators.site import write as write_site        # noqa: E402

CHANNELS_DIR = REPO / "data" / "channels"
MOVIES_DIR = REPO / "data" / "movies"
STATUS_DIR = REPO / "data" / "status"
SOURCES_DIR = REPO / "data" / "sources"
PLAYLISTS = REPO / "playlists"

SEED_NOTE = ("PRE-VALIDATION SEED BUILD. No stream in this tree has been "
             "reachability-tested; entries may not play.")


def _fetch_remote_source(url: str, dest: Path, cfg) -> bool:
    """Download a remote M3U source into data/sources/ before importing it."""
    import urllib.request
    from src.util.urls import check_url

    verdict = check_url(url, block_private=bool(cfg.get_path("security.block_private_ip_targets", True)))
    if not verdict.ok:
        print(f"  refusing source url ({verdict.reason}): {url}", file=sys.stderr)
        return False
    req = urllib.request.Request(url, headers={
        "User-Agent": cfg.get_path("validation.user_agent", "Mozilla/5.0"),
        "Accept": "*/*",
    })
    cap = 32 * 1024 * 1024
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get_path("validation.timeout_read_seconds", 12))) as resp:
            # One byte past the cap, so hitting it is distinguishable from a file
            # that happens to be exactly that size.
            body = resp.read(cap + 1)
    except Exception as exc:  # noqa: BLE001
        print(f"  fetch failed for {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    if len(body) > cap:
        # Importing a truncated playlist would look like the source had shrunk,
        # and would quietly drop every channel past the cut. Refuse it; the
        # caller falls back to the cached copy.
        print(f"  refusing {url}: larger than the {cap // (1024 * 1024)}MB limit",
              file=sys.stderr)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    print(f"  fetched {len(body)} bytes from {url}")
    return True


def _enabled_sources(cfg) -> list[dict]:
    """Live-TV source registry (spec Phase 2).

    Each entry may carry `path` (a file already in the repo) and/or `url`
    (re-fetched on every ingest). Entries with `enabled: false`, a non-m3u type,
    or `authorization_status: DENIED` are skipped.
    """
    registry = read_json(SOURCES_DIR / "sources.json", {}) or {}
    out = []
    for src in registry.get("sources", []):
        if src.get("type") != "m3u":
            continue
        if src.get("enabled") is False:
            continue
        if src.get("authorization_status") == "DENIED":
            print(f"  skipping {src['id']}: authorization_status=DENIED")
            continue
        if not (src.get("path") or src.get("url")):
            continue
        out.append(src)
    return out


def cmd_ingest(args) -> int:
    cfg = config.load()

    if args.source:
        specs = [{"id": args.source_id, "name": args.source, "path": args.source}]
    else:
        specs = _enabled_sources(cfg)
        if not specs:
            print("no enabled m3u sources in data/sources/sources.json", file=sys.stderr)
            return 1

    channels: list[Channel] = []
    stats: dict = {"entries_parsed": 0, "channels_accepted": 0, "rejected": [],
                   "duplicates": [], "parse_problems": [], "needs_custom_headers": 0,
                   "per_source": {}}
    seen_urls: set[str] = set()

    for spec in specs:
        print(f"source {spec['id']}: {spec.get('name', '')}")
        local = Path(spec["path"]) if spec.get("path") else (SOURCES_DIR / f"{spec['id']}.m3u")
        if not local.is_absolute():
            local = REPO / local
        if spec.get("url") and not _fetch_remote_source(spec["url"], local, cfg):
            if not local.exists():
                print(f"  no cached copy; skipping {spec['id']}", file=sys.stderr)
                continue
            print("  using the cached copy already in the repository")
        if not local.exists():
            print(f"  source file not found: {local}", file=sys.stderr)
            continue

        src_channels, src_stats = import_file(local, spec["id"])
        cap = int(spec.get("max_channels") or 0)
        if cap and len(src_channels) > cap:
            # Keep the run bounded: a curated remote list can carry hundreds of
            # channels and every one of them costs a validation probe.
            print(f"  capping {len(src_channels)} -> {cap} channels")
            src_channels = src_channels[:cap]
        # Cross-source de-duplication: the first source to claim a URL keeps it.
        kept = []
        for ch in src_channels:
            key = ch.stream_url.rstrip("/")
            if key in seen_urls:
                stats["duplicates"].append({"kept": "(earlier source)", "dropped": ch.name,
                                            "on": "url across sources"})
                continue
            seen_urls.add(key)
            kept.append(ch)
        channels.extend(kept)

        stats["entries_parsed"] += src_stats["entries_parsed"]
        stats["rejected"].extend(src_stats["rejected"])
        stats["duplicates"].extend(src_stats["duplicates"])
        stats["parse_problems"].extend(src_stats["parse_problems"])
        stats["per_source"][spec["id"]] = {
            "entries": src_stats["entries_parsed"], "accepted": len(kept)}
        print(f"  {len(kept)} channels accepted")

    stats["channels_accepted"] = len(channels)
    stats["needs_custom_headers"] = sum(1 for c in channels if c.requires_custom_headers)
    if not channels:
        print("no channels ingested", file=sys.stderr)
        return 1

    # Preserve status/rights already earned by a previous validation run.
    existing = {c.id: c for c in load_channels(CHANNELS_DIR)}
    for ch in channels:
        prev = existing.get(ch.id)
        if prev and prev.stream_url == ch.stream_url:
            ch.status = prev.status
            ch.rights_status = prev.rights_status
            ch.resolution = prev.resolution
            ch.resolution_label = prev.resolution_label
            ch.codec = prev.codec
            ch.bitrate_bps = prev.bitrate_bps
            ch.last_verified = prev.last_verified
            ch.consecutive_failures = prev.consecutive_failures
            ch.checks_total = prev.checks_total
            ch.checks_ok = prev.checks_ok
            ch.first_seen = prev.first_seen
            ch.last_status_change = prev.last_status_change
            # Carried for the same reason as status itself. The publication gate
            # reads these to re-admit a slow-but-working channel and one that is
            # only blocked from the runner's country, and a run whose time budget
            # runs out leaves most channels unchecked. Dropping them here would
            # silently withhold those channels until their next probe.
            ch.status_reason = prev.status_reason
            ch.last_http_status = prev.last_http_status
            ch.final_url = prev.final_url
            ch.publish_url = prev.publish_url
            ch.tls_unverified = prev.tls_unverified
            ch.video_confirmed = prev.video_confirmed
            ch.black_fraction = prev.black_fraction
            ch.last_decoded = prev.last_decoded

    # A channel that disappears from its source is not thereby proven dead, and
    # the owner asked that nothing be dropped without being checked for itself.
    # Aggregator lists churn: an entry is removed when a curator cannot reach it
    # from wherever they are, which is not a verdict on whether it plays in
    # Dhaka. Such a channel is retained and keeps being probed on the normal
    # rotation, and is only let go once it has been given a real chance to come
    # back and has failed throughout - see ingest.orphan_grace_days.
    seen_ids = {c.id for c in channels}
    grace_days = float(cfg.get_path("ingest.orphan_grace_days", 30) or 0)
    kept_orphans = 0
    if grace_days:
        cutoff = _now() - timedelta(days=grace_days)
        for cid, prev in existing.items():
            if cid in seen_ids:
                continue
            last = _parse_iso(prev.last_verified) or _parse_iso(prev.first_seen)
            # Keep it while it is inside the grace window, and keep it
            # indefinitely while it is still answering: a channel that plays is
            # never dropped for administrative reasons.
            if prev.status in ("ACTIVE", "DEGRADED") or (last and last > cutoff):
                if "orphaned" not in prev.tags:
                    prev.tags = list(prev.tags) + ["orphaned"]
                channels.append(prev)
                kept_orphans += 1
    if kept_orphans:
        print(f"  retained {kept_orphans} channels no longer listed by their source")

    # An ingest that collapses is a fetch failure, not news about the world.
    # save_channels REPLACES the registry, so a source that fails to download
    # would take every one of its channels with it - and the health gate cannot
    # see this, because it judges probe results and no probe has run yet.
    floor = float(cfg.get_path("ingest.min_retention_fraction", 0.6) or 0)
    if floor and existing and len(channels) < floor * len(existing):
        print(f"refusing to save: this ingest produced {len(channels)} channels "
              f"against {len(existing)} already in the registry "
              f"({len(channels) / len(existing):.0%} < {floor:.0%} floor). "
              f"A source almost certainly failed to download. "
              f"The registry is left untouched.", file=sys.stderr)
        return 1

    counts = save_channels(CHANNELS_DIR, channels)
    write_json(STATUS_DIR / "ingest-stats.json", stats)
    print(f"ingested {len(channels)} channels from {len(stats['per_source']) or 1} source(s)")
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:15} {n}")
    if stats["duplicates"]:
        print(f"  dropped {len(stats['duplicates'])} duplicates")
    return 0


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _parse_iso(value: str):
    """Lenient ISO-8601 parse; None when absent or unparseable."""
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _try_candidates(cfg, validator, channels: list) -> list[dict]:
    """Probe alternative URLs for channels whose own URL has died.

    A dead URL is not a dead channel. An operator rotates a path, the aggregator
    is slow to notice, and the tile sits there failing - T Sports HD returned 404
    on eighteen consecutive probes while the same operator went on serving twenty
    other Bangladeshi channels. data/candidates.json holds a short list of
    alternatives per channel; the first that validates replaces the dead URL, and
    the swap is recorded on the channel and in the report.

    Deliberately narrow: only a channel already condemned is considered, a
    candidate passes exactly the same validation as anything else, and the lists
    are a handful of plausible endpoints rather than a scan. The reliability
    counters restart, because a new URL has no history and inheriting the dead
    one's record would misstate both.
    """
    try:
        spec = json.loads((REPO / "data" / "candidates.json").read_text())
        spec = spec.get("channels", {})
    except (OSError, ValueError):
        return []
    if not spec:
        return []

    cap = int(cfg.get_path("validation.max_candidates_per_channel", 8))
    by_id = {c.id: c for c in channels}
    swaps: list[dict] = []

    for cid, entry in spec.items():
        ch = by_id.get(cid)
        if ch is None or ch.status not in ("OFFLINE", "INVALID"):
            continue
        urls = [u for u in (entry.get("candidates") or [])
                if u and u != ch.stream_url][:cap]
        if not urls:
            continue
        print(f"  {ch.name}: URL dead, trying {len(urls)} alternative(s)")
        for url in urls:
            probe = Channel(id=ch.id, name=ch.name, stream_url=url,
                            http_referrer=ch.http_referrer,
                            http_user_agent=ch.http_user_agent)
            try:
                r = validator.validate(probe)
            except Exception as exc:                      # noqa: BLE001
                print(f"    {url} -> {type(exc).__name__}")
                continue
            if r.status in ("ACTIVE", "DEGRADED"):
                swaps.append({"channel": ch.name, "id": ch.id,
                              "from": ch.stream_url, "to": url,
                              "status": r.status})
                note = (f"url replaced after the previous one failed; "
                        f"was {ch.stream_url}")
                ch.stream_url = url
                ch.status = r.status
                ch.status_reason = r.error or ""
                ch.last_http_status = int(r.http_status or 0)
                ch.final_url = r.final_url or ""
                ch.consecutive_failures = 0
                ch.checks_total, ch.checks_ok = 1, 1
                if r.resolution:
                    ch.resolution, ch.resolution_label = r.resolution, r.resolution_label
                ch.notes = f"{ch.notes}; {note}" if ch.notes else note
                print(f"    {url} -> {r.status}  adopted")
                break
            print(f"    {url} -> {r.status} ({(r.error or '')[:44]})")
    return swaps


def _probe_grade(r) -> str:
    """'playable', 'broken' or 'unknown' for one probe result.

    playable  ACTIVE, or DEGRADED only for being slow to answer.
    broken    a definitive fault: the stream would fail from anywhere.
    unknown   something went wrong that may only be this runner - a timeout,
              a decoder that could not get in - so it neither earns credit
              nor costs a strike.
    """
    from src.generators.live import _definitive_failure
    if r.status == "ACTIVE":
        return "playable"
    reason = (r.error or "").lower()
    if r.status == "DEGRADED" and reason.startswith("slow origin"):
        return "playable"
    if _definitive_failure(r.error or ""):
        return "broken"
    return "unknown"


def _publish_url(ch, r, previous_final: str) -> str:
    """The URL the television should be handed, when it is not stream_url.

    Two cases, each measured rather than assumed:

    * An https stream whose certificate is untrusted, served identically over
      plain http on the same path. A TV's player almost certainly validates
      TLS, so the https URL fails on the set however it probes here; the http
      twin plays.
    * A stable redirect. Every press of a redirecting tile pays an extra round
      trip - and from Dhaka to a redirector abroad that is real time before the
      picture starts. When the same final URL comes back on two consecutive
      probes and carries no query string, which is where expiring tokens live,
      it is handed out directly. If it ever changes, the very next probe sees
      the difference and the tile goes back to the original URL.
    """
    from src.util.urls import check_url
    twin = getattr(r, "http_twin", "") or ""
    if twin and check_url(twin).ok:
        return twin
    final = (r.final_url or "").rstrip("/")
    if (final and final != (ch.stream_url or "").rstrip("/")
            and final == (previous_final or "").rstrip("/")
            and not urlsplit(final).query
            and check_url(final).ok):
        return r.final_url
    return ""


def _watched_ids(cfg, channels: list) -> set[str]:
    """Bangladesh, the owner's own playlist, and his favourites."""
    cats = {str(c).lower() for c in (cfg.get_path("validation.deep_check_categories") or ())}
    srcs = {str(c) for c in (cfg.get_path("validation.deep_check_sources") or ())}
    try:
        favs = set(json.loads((REPO / "data" / "favourites.json").read_text())
                   .get("channel_ids") or [])
    except (OSError, ValueError):
        favs = set()
    return {c.id for c in channels
            if (c.category or "").lower() in cats or (c.source or "") in srcs
            or c.id in favs}


def _decode_set(cfg, channels: list) -> set[str]:
    """Which channels this run may decode.

    Two groups. The ones the owner actually watches are decoded every run, so a
    black screen on his own list is caught within hours. Then a rotating slice
    of everything whose video has never been confirmed, oldest first, so that
    backlog - three and a half thousand channels published on nothing stronger
    than "the segment was real bytes" - clears over a few days instead of never.
    """
    cap = int(cfg.get_path("validation.decode_rotation_per_run", 400))
    cats = {str(c).lower() for c in (cfg.get_path("validation.deep_check_categories") or ())}
    srcs = {str(c) for c in (cfg.get_path("validation.deep_check_sources") or ())}

    always = {c.id for c in channels
              if (c.category or "").lower() in cats or (c.source or "") in srcs}
    if cap <= 0:
        return always

    backlog = [c for c in channels
               if c.id not in always
               and not c.video_confirmed
               and c.status in ("ACTIVE", "DEGRADED")]
    backlog.sort(key=lambda c: (c.last_decoded or "", c.name))
    return always | {c.id for c in backlog[:cap]}


def _check_order(channels: list) -> list:
    """Never-checked channels first, then least-recently-checked.

    With the full aggregator index the catalogue no longer fits in one CI run,
    so a run takes as much of it as its time budget allows and the next run
    continues where this one stopped. Ordering this way means a newly ingested
    channel is measured on the run that discovers it, and no channel can be
    starved: every pass moves the oldest verification forward.
    """
    return sorted(channels, key=lambda c: (c.checks_total > 0, c.last_verified or ""))


def _validate_within_budget(validator, channels: list, budget_seconds: float) -> list:
    """Validate in chunks, stopping at the first chunk boundary past the budget.

    Chunked rather than cancelled mid-flight so every probe that was started is
    recorded: a half-finished probe would otherwise count as a failure and move
    a working channel towards OFFLINE on nothing but a clock.
    """
    chunk = max(validator.concurrency * 4, 40)
    started = time.monotonic()
    results: list = []
    for i in range(0, len(channels), chunk):
        results.extend(validator.validate_all(channels[i:i + chunk]))
        if time.monotonic() - started >= budget_seconds:
            break
    return results


def cmd_check(args) -> int:
    cfg = config.load()
    all_channels = load_channels(CHANNELS_DIR)
    if args.limit:
        all_channels = all_channels[: args.limit]
    if not all_channels:
        print("no channels to check", file=sys.stderr)
        return 1

    # Snapshot the pre-run state so the health gate has something to regress from.
    previous_status = {c.id: c.status for c in all_channels}

    if getattr(args, "only_watched", False):
        # The quick pass: only what the owner actually watches. A Bangladeshi
        # channel that dies otherwise waits for the full forty-minute pass,
        # which GitHub runs every four to eleven hours in practice.
        watched = _watched_ids(cfg, all_channels)
        all_channels = [c for c in all_channels if c.id in watched]
        print(f"  quick pass: {len(all_channels)} watched channel(s)")
        if not all_channels:
            print("no watched channels to check", file=sys.stderr)
            return 1
        previous_status = {c.id: c.status for c in all_channels}

    validator = StreamValidator(cfg)
    decode_ids = _decode_set(cfg, all_channels)
    validator.set_decode_set(decode_ids)
    if decode_ids:
        print(f"  decoding {len(decode_ids)} channel(s) this run "
              f"(the owner's own list, plus a slice of the unconfirmed backlog)")
    budget = float(args.budget_seconds
                   or cfg.get_path("validation.max_check_seconds", 0) or 0)
    channels = _check_order(all_channels)
    if budget:
        results = _validate_within_budget(validator, channels, budget)
        if len(results) < len(channels):
            print(f"  time budget reached: checked {len(results)} of {len(channels)}; "
                  f"the rest are first in line next run")
    else:
        results = validator.validate_all(channels)
    by_id = {r.channel_id: r for r in results}

    gate_cfg = cfg.get_path("validation.health_gate", {}) or {}
    verdict = assess(
        previous_status=previous_status,
        results=results,
        max_failure_fraction=float(gate_cfg.get("max_failure_fraction", 0.70)),
        max_regression_fraction=float(gate_cfg.get("max_regression_fraction", 0.40)),
        min_baseline=int(gate_cfg.get("min_baseline", 10)),
    )
    if not gate_cfg.get("enabled", True):
        verdict.ok, verdict.reason = True, "health gate disabled in config"

    now = results[0].checked_at if results else ""
    threshold = int(cfg.get_path("validation.consecutive_failures_before_offline", 3))

    all_channels = load_channels(CHANNELS_DIR)
    for ch in all_channels:
        r = by_id.get(ch.id)
        if r is None:
            continue
        if not ch.first_seen:
            ch.first_seen = now

        # Reliability counters accrue even when the gate is shut: they are a
        # measurement, and suppressing them would hide the very pattern that
        # distinguishes a flaky channel from a bad run.
        # What a probe proves, in three grades rather than two. Reliability
        # used to count every DEGRADED as a success, and DEGRADED has since come
        # to include "segment is not media", "decoded video is black" and "no
        # video stream" - black screens, every one. A channel stuck in one of
        # those accrued a perfect record and was then trusted on the strength
        # of it. Only a result that would actually play counts as a success
        # now, and a definitive fault counts as a strike even when the probe
        # technically answered.
        grade = _probe_grade(r)
        ch.checks_total += 1
        if grade == "playable":
            ch.checks_ok += 1

        if not verdict.ok:
            continue   # the run is not trusted to change published status

        previous = ch.status
        previous_final = ch.final_url
        if r.status in ("OFFLINE", "INVALID") or grade == "broken":
            ch.consecutive_failures += 1
            # One failure demotes; only sustained failure condemns.
            if r.status in ("OFFLINE", "INVALID"):
                ch.status = r.status if ch.consecutive_failures >= threshold else "DEGRADED"
            else:
                ch.status = r.status
        else:
            if grade == "playable":
                ch.consecutive_failures = 0
            ch.status = r.status
        ch.status_reason = r.error or ""
        ch.last_http_status = int(r.http_status or 0)
        ch.final_url = r.final_url or ""
        ch.publish_url = _publish_url(ch, r, previous_final)
        ch.tls_unverified = bool(getattr(r, "tls_unverified", False))
        if getattr(r, "decode_measured", False):
            ch.video_confirmed = bool(r.video_confirmed)
            ch.black_fraction = float(r.black_fraction or 0.0)
            ch.last_decoded = r.checked_at
        if ch.status != previous:
            ch.last_status_change = now
        ch.last_verified = r.checked_at
        if r.resolution:
            ch.resolution, ch.resolution_label = r.resolution, r.resolution_label
        if r.codec:
            ch.codec = r.codec
        if r.bitrate_bps:
            ch.bitrate_bps = r.bitrate_bps
        if r.manifest_kind in ("master", "media"):
            ch.stream_type = "hls"
        elif r.manifest_kind == "dash":
            ch.stream_type = "dash"
        elif r.manifest_kind == "ts":
            ch.stream_type = "ts"

    swaps = _try_candidates(cfg, validator, all_channels)
    write_json(STATUS_DIR / "url-swaps.json", {"swaps": swaps})
    if swaps:
        print(f"  adopted a working URL for {len(swaps)} channel(s)")

    save_channels(CHANNELS_DIR, all_channels)
    write_json(STATUS_DIR / "stream-status.json",
               {"checked": len(results), "health": verdict.to_dict(),
                "results": [r.to_dict() for r in results]})
    write_json(STATUS_DIR / "health.json", verdict.to_dict())

    from collections import Counter
    counts = Counter(r.status for r in results)
    _append_history(cfg, now, counts, verdict)

    print(f"checked {len(results)} streams: " +
          ", ".join(f"{k}={v}" for k, v in counts.most_common()))
    if verdict.ok:
        print(f"health gate: OPEN — {verdict.reason}")
    else:
        print(f"health gate: SHUT — {verdict.reason}", file=sys.stderr)
        print("channel statuses were NOT updated; published playlists stay as they are.",
              file=sys.stderr)
    return 0


def _append_history(cfg, when: str, counts, verdict) -> None:
    """Keep a bounded run history so flapping and trends stay visible."""
    keep = int(cfg.get_path("validation.history_runs_kept", 30))
    history = read_json(STATUS_DIR / "history.json", {"runs": []}) or {"runs": []}
    history["runs"].append({
        "at": when,
        "counts": dict(counts),
        "health_ok": verdict.ok,
        "health_reason": verdict.reason,
        "failure_fraction": verdict.failure_fraction,
        "regression_fraction": verdict.regression_fraction,
    })
    history["runs"] = history["runs"][-keep:]
    write_json(STATUS_DIR / "history.json", history)


def cmd_check_vod(args) -> int:
    """Prove each catalogued playback URL before any of them is called PLAYABLE."""
    cfg = config.load()
    payload = read_json(MOVIES_DIR / "movies.json", {}) or {}
    movies = [Movie.from_dict(m) for m in payload.get("movies", [])]
    if not movies:
        print("no movies in the catalogue; nothing to check")
        return 0

    # Only spend requests on titles that could ever be published.
    candidates = [m for m in movies
                  if m.playback_url and m.rights_status == "CLEARED"
                  and m.playback_status != "EXCLUDED"]
    if args.limit:
        candidates = candidates[: args.limit]
    print(f"checking {len(candidates)} rights-cleared playback urls "
          f"of {len(movies)} catalogued")

    results = VodValidator(cfg).validate_all(candidates)
    by_id = {r.movie_id: r for r in results}

    for m in movies:
        r = by_id.get(m.id)
        if r is None:
            continue
        m.playback_status = "PLAYABLE" if r.status == "PLAYABLE" else (
            "DISCOVERABLE" if r.status in ("UNREACHABLE", "INVALID") else "PLAYABLE")
        if r.status == "DEGRADED":
            m.notes = (m.notes + "; " if m.notes else "") + "no Range support: seeking unavailable"
        m.last_verified = r.checked_at

    write_json(MOVIES_DIR / "movies.json",
               {"count": len(movies), "movies": [m.to_dict() for m in movies]})
    write_json(STATUS_DIR / "vod-status.json",
               {"checked": len(results), "results": [r.to_dict() for r in results]})

    from collections import Counter
    counts = Counter(r.status for r in results)
    print("vod check: " + ", ".join(f"{k}={v}" for k, v in counts.most_common()))
    return 0


def cmd_build(args) -> int:
    cfg = config.load()

    health = read_json(STATUS_DIR / "health.json", None)
    if health and not health.get("ok") and not args.force:
        print(f"refusing to rebuild: {health.get('reason')}", file=sys.stderr)
        print("playlists left exactly as they were. Re-run `check` once the "
              "vantage point is healthy, or pass --force to override.", file=sys.stderr)
        return 2

    channels = load_channels(CHANNELS_DIR)
    gen = LiveGenerator(cfg, PLAYLISTS)
    n = gen.load_home_probe(STATUS_DIR / "home-probe.json")
    if n:
        print(f"  using {n} verdicts measured on the owner's own network; "
              f"these override this runner's")
    result = gen.build(channels, allow_unverified=args.seed)

    if not result.published:
        print("nothing is publishable: no channel passed the publication gate.",
              file=sys.stderr)
        for reason, items in sorted(result.withheld.items()):
            print(f"  {len(items):4}  {reason}", file=sys.stderr)
        print("Refusing to write a playlist tree with no channels in it.", file=sys.stderr)
        return 3

    movies = [Movie.from_dict(m) for m in
              (read_json(MOVIES_DIR / "movies.json", {}) or {}).get("movies", [])]
    movie_gen = MovieGenerator(cfg, PLAYLISTS)
    movie_result = movie_gen.build(movies)
    if movies:
        print(f"movies: {len(movie_result.published)} publishable of {len(movies)}")
        for reason, items in sorted(movie_result.withheld.items()):
            print(f"  withheld {len(items):4}  {reason}")

    size = build_master(cfg, PLAYLISTS,
                        movies=movie_result.published,
                        channels=result.published,
                        seed_note=SEED_NOTE if args.seed else "")
    files = dict(result.files)
    files.update(movie_result.files)
    files["master.m3u"] = size
    write_json(STATUS_DIR / "build.json", {
        "seed": args.seed,
        "files": files,
        "published": len(result.published),
        "withheld": {k: len(v) for k, v in result.withheld.items()},
        "movies_published": len(movie_result.published),
        "movies_withheld": {k: len(v) for k, v in movie_result.withheld.items()},
        "movie_buckets": movie_result.buckets,
    })
    print(f"built {len(files)} playlists, {len(result.published)} channels published")
    for reason, items in sorted(result.withheld.items()):
        print(f"  withheld {len(items):4}  {reason}")
    return 0


def cmd_verify(args) -> int:
    cfg = config.load()
    validator = M3UValidator(cfg, PLAYLISTS)
    result = validator.validate_tree("master.m3u")
    write_json(STATUS_DIR / "playlist-validation.json", result)

    print(f"files={result['files_checked']} items={result['total_items']} "
          f"errors={result['errors']} warnings={result['warnings']}")
    for issue in result["issues"]:
        if issue["severity"] == "error" or args.verbose:
            loc = f"{issue['file']}:{issue['line']}" if issue["line"] else issue["file"]
            print(f"  [{issue['severity']:7}] {loc:34} {issue['code']:16} {issue['message']}")
    if result["cycles"]:
        print("CIRCULAR REFERENCES:", *result["cycles"], sep="\n  ")
    return 0 if result["ok"] else 1


def cmd_report(args) -> int:
    cfg = config.load()
    channels = load_channels(CHANNELS_DIR)
    movies = [Movie.from_dict(m) for m in
              (read_json(MOVIES_DIR / "movies.json", {}) or {}).get("movies", [])]
    playlist_result = read_json(STATUS_DIR / "playlist-validation.json", {}) or {}
    build_state = read_json(STATUS_DIR / "build.json", {}) or {}
    ingest_stats = read_json(STATUS_DIR / "ingest-stats.json", {}) or {}

    gen = LiveGenerator(cfg, PLAYLISTS)
    gen.load_home_probe(STATUS_DIR / "home-probe.json")
    _, withheld = gen._partition(channels, args.seed)

    report = build_report(
        channels=channels, movies=movies, playlist_result=playlist_result,
        build_files=build_state.get("files", {}), withheld=withheld,
        ingest_stats=ingest_stats, seed_build=args.seed,
        epg=read_json(STATUS_DIR / "epg.json", None),
        health=read_json(STATUS_DIR / "health.json", None),
        history=read_json(STATUS_DIR / "history.json", None),
        probe_results=(read_json(STATUS_DIR / "stream-status.json", {}) or {}).get("results"),
        movies_published=build_state.get("movies_published"),
        movies_withheld=build_state.get("movies_withheld"),
    )
    write_json(REPO / "validation-report.json", report)
    # The landing page is generated from the same report, so the site root can
    # never disagree with what was measured — and Pages stops answering / with 404.
    size = write_site(report, cfg, PLAYLISTS, REPO / "site" / "index.html")
    print(f"wrote site/index.html ({size} bytes)")
    print(json.dumps({k: v for k, v in report.items()
                      if k in ("generated_at", "build_kind", "live_channels", "playlists")},
                     indent=2, ensure_ascii=False)[:1600])
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ingest"); sp.add_argument("--source"); \
        sp.add_argument("--source-id", default="src-user-m3u"); sp.set_defaults(fn=cmd_ingest)
    sp = sub.add_parser("check"); sp.add_argument("--limit", type=int, default=0); \
        sp.add_argument("--budget-seconds", type=float, default=0,
                        help="stop after this long; 0 uses validation.max_check_seconds"); \
        sp.add_argument("--only-watched", action="store_true",
                        help="probe only Bangladesh, the owner's playlist and favourites"); \
        sp.set_defaults(fn=cmd_check)
    sp = sub.add_parser("build"); sp.add_argument("--seed", action="store_true"); \
        sp.add_argument("--force", action="store_true",
                        help="rebuild even if the last check tripped the health gate"); \
        sp.set_defaults(fn=cmd_build)
    sp = sub.add_parser("check-vod"); sp.add_argument("--limit", type=int, default=0); \
        sp.set_defaults(fn=cmd_check_vod)
    sp = sub.add_parser("verify"); sp.add_argument("--verbose", action="store_true"); \
        sp.set_defaults(fn=cmd_verify)
    sp = sub.add_parser("report"); sp.add_argument("--seed", action="store_true"); \
        sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("all"); sp.add_argument("--seed", action="store_true"); \
        sp.add_argument("--limit", type=int, default=0); sp.add_argument("--verbose", action="store_true"); \
        sp.add_argument("--force", action="store_true"); sp.set_defaults(fn=None)

    args = p.parse_args()
    if args.cmd == "all":
        rc = cmd_check(args) or cmd_build(args) or cmd_verify(args) or cmd_report(args)
        return rc
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

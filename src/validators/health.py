"""Run-level health gate ("circuit breaker").

A validation run is a measurement of two things at once: whether each stream is
up, and whether *our vantage point* is working. A runner with a broken resolver,
a throttled egress path, or a transient upstream outage produces a sweeping wave
of OFFLINE results that looks exactly like "all your channels died".

Publishing that wave would delete a working playlist on the strength of one bad
run. So the run is judged before its results are allowed to change what is published.

The gate only fires when there is a prior baseline to regress *from*. On the first
real validation run every channel is UNVERIFIED, so a low pass rate is news about
the channels, not about the network, and the gate stays open.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class HealthVerdict:
    ok: bool
    reason: str = ""
    checked: int = 0
    newly_failing: int = 0
    baseline_active: int = 0
    failure_fraction: float = 0.0   # over channels not already known dead
    regression_fraction: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def assess(
    *,
    previous_status: dict[str, str],
    results: list,
    max_failure_fraction: float = 0.70,
    max_regression_fraction: float = 0.40,
    min_baseline: int = 10,
) -> HealthVerdict:
    """Decide whether this run's results may be published.

    previous_status: channel_id -> status as it stood *before* this run.
    results:         ValidationResult objects from this run.
    """
    checked = len(results)
    if not checked:
        return HealthVerdict(False, "no channels were checked", 0)

    failed_now = {r.channel_id for r in results if r.status in ("OFFLINE", "INVALID")}

    # Channels we already knew were dead are excluded from both sides of the
    # absolute ceiling. Counting them would permanently freeze a registry that
    # is genuinely mostly dead: it would fail the ceiling on every single run
    # while nothing had actually changed.
    already_failing = {
        r.channel_id for r in results
        if previous_status.get(r.channel_id) in ("OFFLINE", "INVALID")
    }
    candidates = [r for r in results if r.channel_id not in already_failing]
    newly_failing = [r for r in candidates if r.channel_id in failed_now]
    failure_fraction = (len(newly_failing) / len(candidates)) if candidates else 0.0

    baseline_active = [cid for cid, st in previous_status.items() if st == "ACTIVE"]
    checked_ids = {r.channel_id for r in results}
    baseline_in_run = [cid for cid in baseline_active if cid in checked_ids]

    verdict = HealthVerdict(
        ok=True, checked=checked,
        newly_failing=len(newly_failing),
        baseline_active=len(baseline_in_run),
        failure_fraction=round(failure_fraction, 4),
    )

    # No usable baseline: nothing to regress from, so the results stand.
    if len(baseline_in_run) < min_baseline:
        verdict.reason = (
            f"no baseline (only {len(baseline_in_run)} previously-ACTIVE channels "
            f"in this run, need {min_baseline}); results accepted as first measurement"
        )
        return verdict

    regressed = [cid for cid in baseline_in_run if cid in failed_now]
    verdict.regression_fraction = round(len(regressed) / len(baseline_in_run), 4)

    if verdict.regression_fraction > max_regression_fraction:
        verdict.ok = False
        verdict.reason = (
            f"{len(regressed)}/{len(baseline_in_run)} previously-ACTIVE channels failed "
            f"({verdict.regression_fraction:.0%} > {max_regression_fraction:.0%} limit). "
            f"This looks like a problem with the validation run, not with the channels. "
            f"Published playlists left unchanged."
        )
        return verdict

    if failure_fraction > max_failure_fraction:
        verdict.ok = False
        verdict.reason = (
            f"{len(newly_failing)}/{len(candidates)} channels that were not already "
            f"known-dead failed ({failure_fraction:.0%} > {max_failure_fraction:.0%} "
            f"limit) despite a healthy baseline. Published playlists left unchanged."
        )
        return verdict

    verdict.reason = (
        f"{verdict.regression_fraction:.0%} of {len(baseline_in_run)} baseline channels "
        f"regressed; within limits"
    )
    return verdict

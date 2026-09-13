#!/usr/bin/env python3
"""Render validation-report.json into a GitHub Actions step summary (or stdout)."""
import json, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
report = json.loads((REPO / "validation-report.json").read_text(encoding="utf-8"))

lc, rel, health = report["live_channels"], report["reliability"], report["run_health"]
pl, res = report["playlists"], report["resolution"]

lines = [
    f"## {report['build_kind']} — {report['generated_at']}",
    "",
    "| | |",
    "|---|---:|",
    f"| Channels in registry | {lc['total']} |",
    f"| ACTIVE | {lc['active']} |",
    f"| DEGRADED | {lc['degraded']} |",
    f"| OFFLINE | {lc['offline']} |",
    f"| INVALID | {lc['invalid']} |",
    f"| UNVERIFIED | {lc['unverified']} |",
    f"| Measured 1080p / 720p / 4K | {res['measured_1080p']} / {res['measured_720p']} / {res['measured_4k']} |",
    f"| Playlists generated | {len(pl['files'])} |",
    f"| Structural errors | {pl['structure_errors']} |",
    "",
    "**Health gate:** " + ({True: "OPEN", False: "SHUT"}.get(health.get("ok"), "not run yet"))
    + f" — {health.get('reason', 'n/a')}",
    "",
]

probe = report.get("last_probe", {})
if probe.get("counts"):
    lines += ["### What this run actually measured", "",
              "_Stored status lags by design: a first failure demotes to DEGRADED "
              "rather than condemning the channel._", "",
              "| probe result | channels |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in probe["counts"].items()]
    if probe.get("failing_stage"):
        lines += ["", "| failure (status : last failing stage) | channels |", "|---|---:|"]
        lines += [f"| `{k}` | {v} |" for k, v in probe["failing_stage"].items()]
    lines.append("")

if report["withheld_counts"]:
    lines += ["### Withheld from playlists", "", "| reason | channels |", "|---|---:|"]
    lines += [f"| `{k}` | {v} |" for k, v in report["withheld_counts"].items()]
    lines.append("")

if rel["flapping"]:
    lines += [f"### Flapping channels ({len(rel['flapping'])})", "",
              "| channel | reliability | checks |", "|---|---:|---:|"]
    lines += [f"| {f['name']} | {f['reliability']:.0%} | {f['checks']} |"
              for f in rel["flapping"][:15]]
    lines.append("")

if report["broken_urls"]:
    lines += [f"### Broken ({len(report['broken_urls'])})", "",
              "| channel | status | host |", "|---|---|---|"]
    lines += [f"| {b['name']} | {b['status']} | `{b['host']}` |"
              for b in report["broken_urls"][:40]]
    if len(report["broken_urls"]) > 40:
        lines.append(f"| …and {len(report['broken_urls']) - 40} more | | |")

text = "\n".join(lines) + "\n"
target = os.environ.get("GITHUB_STEP_SUMMARY")
if target:
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(text)
print(text)

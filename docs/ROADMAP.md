# Roadmap

Phases 1 and 2 are complete. Each later phase ends with generated output and a report,
and waits for approval before the next begins.

| Phase | Scope | State |
|---|---|---|
| **1** | `master.m3u`, `live-tv.m3u`, `bangladesh.m3u`; data model; generators; structural validator; 55 tests | **done — unvalidated seed** |
| **2** | Registry-driven multi-source ingest, run-health gate, reliability tracking, real validation on GitHub-hosted runners, `ACTIVE`-only publishing | **done** |
| 3 | Nested movie library skeleton: `movies.m3u` + genre/language children | |
| 4 | VOD playback proving-ground: `type="video"`, seek/pause behaviour against a known-authorised direct URL | |
| 5 | Movie metadata ingestion, full-text search index for the future web layer | |
| 6 | StreamIMDB adapter — metadata only, `DISCOVERABLE` by default | |
| 7 | XMLTV generation, `x-tvg-url`, CORS/size/no-gzip constraints, external-network test | |
| 8 | Scheduling hardening, failure thresholds, alerting on mass-offline | |
| 9 | Deployment hardening, custom domain, cache headers | |
| 10 | Optional Netflix-style web catalogue (never a dependency of the SS IPTV path) | |

## Phase 2 outcome

* Validation runs on GitHub-hosted runners (decided), with the run-health gate,
  the 3-strike demotion rule and reliability counters containing the
  geo-distance false-negative risk. See limitation 2.
* `data/sources/sources.json` now drives ingest: multiple sources, local or
  remote, with cross-source de-duplication.
* The publication gate additionally drops chronically unreliable channels once
  there is enough evidence.

## Phase 3 entry criteria

1. At least two or three validation runs, so reliability figures mean something
   and flapping channels are distinguishable from a one-off bad run.
2. A decision on which channels to replace or drop, informed by
   `validation-report.json` → `reliability.flapping` and `broken_urls`.
3. GitHub Pages enabled, so the published URLs are live and the SS IPTV
   navigation can be tested on the actual TV before the movie library is layered
   on top of it. Phase 3 is a bigger tree on the same mechanism — worth proving
   the mechanism first.

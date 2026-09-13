# Roadmap

Phase 1 is complete. Each later phase ends with generated output and a report,
and waits for approval before the next begins.

| Phase | Scope | State |
|---|---|---|
| **1** | `master.m3u`, `live-tv.m3u`, `bangladesh.m3u`; data model; generators; structural validator; 55 tests | **done — unvalidated seed** |
| **2** | Run the validator for real (CI or on-network), source registry hardening, quality measurement, `ACTIVE`-only publishing | next |
| 3 | Nested movie library skeleton: `movies.m3u` + genre/language children | |
| 4 | VOD playback proving-ground: `type="video"`, seek/pause behaviour against a known-authorised direct URL | |
| 5 | Movie metadata ingestion, full-text search index for the future web layer | |
| 6 | StreamIMDB adapter — metadata only, `DISCOVERABLE` by default | |
| 7 | XMLTV generation, `x-tvg-url`, CORS/size/no-gzip constraints, external-network test | |
| 8 | Scheduling hardening, failure thresholds, alerting on mass-offline | |
| 9 | Deployment hardening, custom domain, cache headers | |
| 10 | Optional Netflix-style web catalogue (never a dependency of the SS IPTV path) | |

## Phase 2 entry criteria

1. Decide where validation runs (GitHub-hosted vs a runner inside Bangladesh) —
   see limitation 2.
2. First real `validation-report.json`.
3. Review `data/sources/host-trust.json` and set `rights_status` where a decision
   is warranted.

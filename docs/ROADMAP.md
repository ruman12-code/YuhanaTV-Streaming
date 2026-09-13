# Roadmap

Phases 1-5 are complete. Each later phase ends with generated output and a report,
and waits for approval before the next begins.

| Phase | Scope | State |
|---|---|---|
| **1** | `master.m3u`, `live-tv.m3u`, `bangladesh.m3u`; data model; generators; structural validator; 55 tests | **done — unvalidated seed** |
| **2** | Registry-driven multi-source ingest, run-health gate, reliability tracking, real validation on GitHub-hosted runners, `ACTIVE`-only publishing | **done** |
| **3** | Nested movie library: 208 rights-cleared, byte-verified titles across 15 genre and collection playlists; VOD validator; Internet Archive adapter | **done** |
| **4** | Nested navigation confirmed on the owner's Toshiba/VIDAA set: Movies opens a screen of genre tiles | **done** |
| **5** | IMDb ratings via the public non-commercial datasets (no API key needed), `min_rating_publish` gate, full-text search index | **done** |
| 6 | StreamIMDB adapter — metadata only, `DISCOVERABLE` by default | |
| 7 | XMLTV generation, `x-tvg-url`, CORS/size/no-gzip constraints, external-network test | |
| 8 | Scheduling hardening, failure thresholds, alerting on mass-offline | |
| 9 | Deployment hardening, custom domain, cache headers | |
| 10 | Optional Netflix-style web catalogue, querying `data/search-index.json` (never a dependency of the SS IPTV path) | |

## Phase 2 measured outcome

Three validation runs on GitHub-hosted runners:

| run | ACTIVE | Bangladesh | notes |
|---|---:|---:|---|
| 1 | 118 | 16/31 | first measurement; exposed three validator bugs |
| 2 | 131 | 26/31 | after fixing sliding-window segment probing, BOM handling, sub-resource retries |
| 3 | 131 | 23/31 | after adding direct MPEG-TS support; health gate reported 2% regression |

Most of run 1's apparent failures were the validator's fault, not the origins':
it probed the oldest segment of a live sliding window, rejected manifests
carrying a byte-order mark, and gave sub-resource fetches no retry. Fixing those
moved 13 channels — 10 of them Bangladeshi — from DEGRADED to ACTIVE.

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


## Phase 3 measured outcome

213 titles imported, 208 after removing five films that exist as two separate
Archive uploads each. All 208 cleared on rights and all 208 verified playable by
fetching bytes.

Reading the first generated leaf found four faults the tests had not covered,
each of which would have been visible on the TV:

| fault | symptom | fix |
|---|---|---|
| genres aliasing to one bucket | every noir film listed twice in `crime.m3u` | bucket membership tracked by id |
| `MM:SS` read as `HH:MM` | a six-minute clip labelled "372 min" | clock parser that knows two-part values are minutes and seconds |
| `publicdate` used as year | 1940s footage dated 2011 | only fields describing the work are consulted |
| Trending derived from an unrated catalogue | the same 24 films shown twice under two names | Trending emitted only when ratings make it differ |

Known imperfections, not yet addressed:

* **No ratings.** The Archive carries none, so Top Rated is absent and the "IMDb
  6+" filter cannot be applied. Phase 5, and it needs a TMDB or OMDb API key.
* **Language buckets are thin** — 27 titles in English, none elsewhere, because
  most items declare no language.
* **Some genre assignments are loose.** A Prelinger city film sits in Crime
  because the film-noir subject query matched it.

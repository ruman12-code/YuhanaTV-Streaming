# Architecture

## Principles

1. **The registry is the truth; playlists are a rendering of it.** Nothing is
   hand-edited in `playlists/` — it is regenerated from `data/` on every run.
2. **Three independent status axes, never conflated.** `status` (does it respond?)
   is set by the validator. `rights_status` (may we redistribute it?) is set by a
   human. `playback_status` (do we hold a direct URL at all?) applies to movies.
   A tile is published only when every applicable axis allows it.
3. **A claim is not a measurement.** A source that names a channel
   "Star Plus HD (1080p)" gets that recorded in `notes`. `resolution_label` is only
   ever written from an `#EXT-X-STREAM-INF RESOLUTION` value the validator actually read.
4. **No runtime dependencies.** Standard library only, so a CI run cannot fail
   because of a package that moved.

## Layout

```
config/config.json            every tunable; overridable via YUHANA_* env vars
data/
  channels/<category>.json    the channel registry, one file per category
  movies/                     Phase 3
  sources/
    sources.json              source registry + trust-tier definitions
    host-trust.json           generated: provenance tier per stream host
    imported-channel-list.m3u verbatim snapshot of the supplied source
  status/                     generated run state
    ingest-stats.json         parse/dedupe results
    stream-status.json        per-channel probe detail from the last run
    health.json               run-health verdict
    history.json              bounded per-run history
    playlist-validation.json  structural issues
    build.json                what was published and what was withheld
src/
  config.py                   config loading + URL helpers
  models.py                   Channel / Movie / Source + persistence
  util/urls.py                URL safety (spec 27)
  ingest/m3u_import.py        Extended M3U -> Channel records, dedupe
  validators/
    stream_validator.py       DNS/TLS/HTTP/manifest/variant/segment probing
    m3u_validator.py          playlist syntax, references, cycles
    health.py                 run-level circuit breaker
  generators/
    m3u.py                    SS IPTV M3U writer
    catalog.py                tile labels, ordering, colours
    live.py                   live tree + master playlist
    movies.py                 Phase 3
  sources/
    host_trust.py             provenance classification
    streamimdb/               Phase 6 (metadata-only adapter)
  epg/                        Phase 7
  report.py                   validation-report.json
scripts/
  pipeline.py                 ingest | check | build | verify | report | all
  classify_sources.py         regenerate host-trust.json
  summarise.py                render the report as a CI job summary
playlists/                    GENERATED — do not hand-edit
epg/                          GENERATED — Phase 7
tests/                        55 tests, standard-library unittest
```

## Pipeline (spec section 20)

```
 ingest ──▶ classify ──▶ check ──▶ build ──▶ verify ──▶ report ──▶ deploy
   │           │           │         │         │          │
   │           │           │         │         │          └─ validation-report.json
   │           │           │         │         └─ structure + cycles; fails the run on error
   │           │           │         └─ playlists/ regenerated from the registry
   │           │           └─ DNS→TLS→HTTP→manifest→variant→segment, per channel
   │           └─ host provenance tiers
   └─ parse, normalise, de-duplicate, preserve prior validation state
```

`ingest` deliberately preserves the `status`, `rights_status`, measured resolution and
failure counters of any channel whose stream URL is unchanged, so re-importing the
source never discards validation work.

## The run-health gate

A validation run measures two things at once: whether each stream is up, and whether our
vantage point works. A runner with a broken resolver or a throttled egress path produces a
sweeping wave of `OFFLINE` results indistinguishable from "all your channels died".

`src/validators/health.py` judges the run before its results are allowed to change anything:

| condition | outcome |
|---|---|
| fewer than `min_baseline` previously-`ACTIVE` channels in the run | gate **open** — no baseline to regress from, results accepted as a first measurement |
| more than `max_regression_fraction` (40%) of previously-`ACTIVE` channels now failing | gate **shut** |
| more than `max_failure_fraction` (70%) of channels *not already known dead* failing | gate **shut** |
| otherwise | gate **open** |

Channels already known to be dead are excluded from both sides of the absolute ratio.
Counting them would permanently freeze a registry that is genuinely mostly dead: it would
fail the ceiling on every run while nothing had actually changed.

When the gate is shut, statuses are not updated and `pipeline.py build` refuses to
regenerate (exit 2) unless given `--force`. Reliability counters still accrue, because they
are the evidence that later distinguishes a flaky channel from a bad run.

## Reliability

Every channel carries `checks_total` / `checks_ok` across runs. Two things read them:

* the publication gate drops a channel below `min_reliability` (0.34) once it has at least
  `min_checks_for_reliability_gate` (5) measurements — a tile that works one time in four is
  worse than no tile;
* `validation-report.json` lists `flapping` channels (4+ checks, 25–75% reliability), which
  are the ones worth replacing rather than waiting on.

## The publication gate

`LiveGenerator._partition` is the single place a channel can be refused. In order:

| Reason | Meaning |
|---|---|
| `rights_excluded` | a human marked it `rights_status: EXCLUDED` |
| `requires_custom_http_headers` | needs a Referer/User-Agent SS IPTV cannot send |
| `no_stream_url` | registry entry has no URL |
| `status_<x>` | validator status not in `validation.publish_statuses` |
| `unreliable` | enough history to know it usually fails |

Default `publish_statuses` is `["ACTIVE"]`. `--seed` additionally admits `UNVERIFIED`
and `DEGRADED`, and stamps every generated file with a comment saying so. There is no
silent path by which an untested stream reaches a production playlist.

## Extending it

*A new live source*: add an entry to `data/sources/sources.json` with `type: "m3u"` and
either a `path` (a file in the repo) or a `url` (re-fetched on every ingest, through the same
URL-safety checks). Set `enabled: false` to park it, or `authorization_status: "DENIED"` to
block it outright. Ingest de-duplicates across sources on stream URL, first source wins.
For a non-M3U source, write an adapter in `src/ingest/` returning `Channel` objects.

*A new movie source*: implement `src/sources/<name>/` returning `Movie` objects with
`playback_status` defaulting to `DISCOVERABLE`. The VOD generator reads
`Movie.publishable_as_vod`, so an adapter cannot accidentally publish an unauthorised title.

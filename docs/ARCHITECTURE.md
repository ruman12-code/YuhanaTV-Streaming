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
  status/                     generated run state (ingest, validation, build)
src/
  config.py                   config loading + URL helpers
  models.py                   Channel / Movie / Source + persistence
  util/urls.py                URL safety (spec 27)
  ingest/m3u_import.py        Extended M3U -> Channel records, dedupe
  validators/
    stream_validator.py       DNS/TLS/HTTP/manifest/variant/segment probing
    m3u_validator.py          playlist syntax, references, cycles
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

## The publication gate

`LiveGenerator._partition` is the single place a channel can be refused. In order:

| Reason | Meaning |
|---|---|
| `rights_excluded` | a human marked it `rights_status: EXCLUDED` |
| `requires_custom_http_headers` | needs a Referer/User-Agent SS IPTV cannot send |
| `no_stream_url` | registry entry has no URL |
| `status_<x>` | validator status not in `validation.publish_statuses` |

Default `publish_statuses` is `["ACTIVE"]`. `--seed` additionally admits `UNVERIFIED`
and `DEGRADED`, and stamps every generated file with a comment saying so. There is no
silent path by which an untested stream reaches a production playlist.

## Extending it

*A new live source*: add a `Source` to `data/sources/sources.json`, write an adapter in
`src/ingest/` that returns `Channel` objects, run `pipeline.py ingest`. Nothing else changes.

*A new movie source*: implement `src/sources/<name>/` returning `Movie` objects with
`playback_status` defaulting to `DISCOVERABLE`. The VOD generator reads
`Movie.publishable_as_vod`, so an adapter cannot accidentally publish an unauthorised title.

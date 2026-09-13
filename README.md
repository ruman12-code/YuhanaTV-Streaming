# YuhanaTV — a personal SS IPTV library

Generates SS IPTV-compatible nested Extended M3U playlists (and, from Phase 7,
XMLTV) from a normalised content registry, and publishes them at stable URLs so
the TV is configured once and never again.

Target player: **SS IPTV** on a **VIDAA** TV.

---

## Status: Phase 2 complete — validated

Real validation, run on GitHub-hosted runners.

| | |
|---|---|
| Channels in registry | **171** (177 source entries, 6 duplicates dropped) |
| **ACTIVE (verified reachable)** | **131** |
| DEGRADED / OFFLINE / INVALID | 11 / 28 / 1 |
| 🇧🇩 Bangladesh | **23–26 of 31 ACTIVE**, varying by run |
| Measured 1080p / 720p / 576p / 480p | 54 / 47 / 4 / 5 |
| **Channels published** | **128** (3 ACTIVE withheld: need HTTP headers SS IPTV cannot send) |
| Playlists generated | 13, largest 34 items / 8.3 KB |
| Structural errors | **0**, no circular references |
| Mean reliability over 3 runs | **0.81**; 134 channels usable in every run, 29 never once up |
| Tests | **77 passing** |

Every published channel has been fetched end to end — manifest, variant playlist,
and a real media segment — from a GitHub runner. Resolutions are measured from
`EXT-X-STREAM-INF`, never from a channel name.

The Bangladesh figure moves between runs because six channels on one origin have
an unstable TLS chain. That variance is the point of the reliability counters: a
channel is judged on its record, not on one run.

Live results: [`validation-report.json`](validation-report.json) ·
per-channel detail: [`data/status/stream-status.json`](data/status/stream-status.json)

## Use it

**One manual step is outstanding:** enable Pages at *Settings → Pages → Build and
deployment → Source: **GitHub Actions***. It cannot be done through the API. Until
then the deploy job is skipped (non-fatally) and the published URL below 404s.

Once enabled, in SS IPTV go to *Settings → Content → External playlists → Add* and
enter:

```
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/master.m3u
```

That URL never changes. Everything behind it refreshes every 8 hours.

**Want it working before enabling Pages?** This repository is public, so the same
tree is already served from `raw.githubusercontent.com` — see
[docs/HOSTING.md](docs/HOSTING.md) for the one config value to change.

## Run it locally

```bash
python3 scripts/pipeline.py ingest          # source M3U    -> data/channels/
python3 scripts/classify_sources.py         # host provenance tiers
python3 scripts/pipeline.py check           # probe every stream, set status
python3 scripts/pipeline.py build           # registry      -> playlists/
python3 scripts/pipeline.py verify --verbose
python3 scripts/pipeline.py report
python3 -m unittest discover -s tests -v
```

Add `--seed` to `build`/`report` to publish `UNVERIFIED` channels for a
pre-validation build. Without it only `ACTIVE` channels are published.

No third-party packages. Python 3.11+, standard library only.

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | layout, pipeline, publication gate |
| [SSIPTV-COMPATIBILITY.md](docs/SSIPTV-COMPATIBILITY.md) | which SS IPTV features are used, and the one documented ambiguity |
| [NAVIGATION.md](docs/NAVIGATION.md) | exactly what the TV does at each hop |
| [HOSTING.md](docs/HOSTING.md) | URLs, Pages setup, alternatives |
| [LIMITATIONS.md](docs/LIMITATIONS.md) | every known constraint and its mitigation |
| [ROADMAP.md](docs/ROADMAP.md) | phases 2–10 |

## Ground rules the code enforces

* A quality label (`1080p`, `4K`) is written **only** from a resolution the
  validator measured in the HLS manifest. A source's own claim is kept as a note.
* A channel reaches a published playlist only via `LiveGenerator._partition`,
  whose default admits `ACTIVE` and nothing else.
* A movie reaches a VOD playlist only when `playback_status == PLAYABLE` **and**
  `rights_status == CLEARED`.
* Only `http`/`https` URLs are ever emitted; `javascript:`, `file:`, `data:`,
  local paths, private-network targets and executable downloads are rejected at
  ingest, at generation and again at verification.
* No stream is proxied, relayed, decrypted or re-hosted. This repository
  publishes text files.

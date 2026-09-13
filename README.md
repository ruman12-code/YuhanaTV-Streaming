# YuhanaTV — a personal SS IPTV library

Generates SS IPTV-compatible nested Extended M3U playlists (and, from Phase 7,
XMLTV) from a normalised content registry, and publishes them at stable URLs so
the TV is configured once and never again.

Target player: **SS IPTV** on a **VIDAA** TV.

---

## Status: Phase 3 complete — live and validated

**The site is deployed:** https://ruman12-code.github.io/YuhanaTV-Streaming/

### Live TV
| | |
|---|---|
| Channels in registry | **171** |
| **ACTIVE (verified reachable)** | **131** |
| 🇧🇩 Bangladesh | **23–26 of 31**, varying by run |
| Measured 1080p / 720p / 576p / 480p | 54 / 47 / 4 / 5 |
| Published | **128** (3 withheld: need headers SS IPTV cannot send) |

### Movie Library
| | |
|---|---|
| Catalogued | **208** |
| **Rights CLEARED** | **208** (170 by per-item licence, 38 by curated public-domain collection) |
| **PLAYABLE (bytes verified, Range-capable)** | **208** |
| Published as VOD | **208** across 15 genre and collection playlists |
| Source | Internet Archive public-domain and openly-licensed films |

### Tree
```
 30 playlists · 330 movie entries + 128 channel tiles · largest file 43 items / 12 KB
  0 structural errors · 0 circular references · 0 duplicate tiles
114 tests passing
```

Every published channel was fetched end to end (manifest → variant → media
segment). Every published film had its first bytes fetched and its `Range`
support confirmed. Resolutions come from `EXT-X-STREAM-INF`, never from a name.

## Use it

Pages is enabled and deploying. In SS IPTV go to *Settings → Content →
External playlists → Add* and enter:

```
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/master.m3u
```

That URL never changes. Everything behind it refreshes every 8 hours.

If Pages ever needs reconfiguring, [docs/SETUP-PAGES.md](docs/SETUP-PAGES.md)
names the exact fields — in particular the **Custom domain** box, which takes a
domain you own and must be left empty otherwise.

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
| [SETUP-PAGES.md](docs/SETUP-PAGES.md) | which Pages field is which |
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

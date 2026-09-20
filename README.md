# YuhanaTV — a personal SS IPTV library

Generates SS IPTV-compatible nested Extended M3U playlists (and, from Phase 7,
XMLTV) from a normalised content registry, and publishes them at stable URLs so
the TV is configured once and never again.

Target player: **SS IPTV** on a **VIDAA** TV.

---

## Status: live and validated

**Site:** https://ruman12-code.github.io/YuhanaTV-Streaming/

### Live TV
| | |
|---|---|
| Channels in registry | **1003** |
| **ACTIVE (probed end to end)** | **720** |
| Movie channels active | **289** — 104 at 720p or better |
| 🇧🇩 Bangladesh | 24–26 active, varies by run |
| Withheld | 105 need HTTP headers SS IPTV cannot send · 240 failed or degraded |

### Movie Library (VOD)
| | |
|---|---|
| Catalogued | **583** |
| **Published** | **151** |
| Playable HD features | **14** (720p/1080p classics) |
| Matched to IMDb / rated | 316 / 302 · mean 5.99 |
| Container | 100% mp4/m4v — no format the TV cannot decode |

There is **no 4K tile**, and there will not be one: the only 4K items in the
source are a Blender demo and a NASA reel, neither of which is a film. HD viewing
comes from the movie channels, not the VOD library.

### Tree
```
55 playlists · 1,192 entries · no screen over the 120-item budget
 0 structural errors · 0 unreachable · 0 circular references
232 tests passing
```

### Schedule and cost
Channel validation every 6 hours, movie refresh daily. Roughly 1,100 CI minutes a
month, which costs **nothing**: GitHub Actions is unlimited on public repositories.

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

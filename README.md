# YuhanaTV — a personal SS IPTV library

Generates SS IPTV-compatible nested Extended M3U playlists (and, from Phase 7,
XMLTV) from a normalised content registry, and publishes them at stable URLs so
the TV is configured once and never again.

Target player: **SS IPTV** on a **VIDAA** TV.

---

## Status: Phase 1 complete — pre-validation seed build

| | |
|---|---|
| Channels in registry | **171** (from the supplied playlist; 177 entries, 6 duplicates dropped) |
| Channels published | **168** (3 withheld: need HTTP headers SS IPTV cannot send) |
| Playlists generated | **18**, largest 31 items / 7.3 KB |
| Structural errors | **0** (2 duplicate-title warnings) |
| Tests | **55 passing** |
| **Streams reachability-tested** | **0 — see below** |

> **No stream in this repository has been verified to play.** The authoring
> environment blocks outbound HTTPS to every stream host, so validation has not
> run. Every channel is `status: UNVERIFIED` and every generated file carries a
> `PRE-VALIDATION SEED BUILD` comment. Run the `Update IPTV` workflow to produce
> the first real results. Details in [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

---

## Use it

1. Enable Pages: *Settings → Pages → Source: GitHub Actions*.
2. Run *Actions → Update IPTV → Run workflow* (tick **seed** for the first run).
3. In SS IPTV: *Settings → Content → External playlists → Add*, and enter:

   ```
   https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/master.m3u
   ```

That URL never changes. Everything behind it refreshes every 8 hours.

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

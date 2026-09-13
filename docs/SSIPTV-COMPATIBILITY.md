# SS IPTV compatibility notes

Everything below is taken from SS IPTV's own operator documentation. Where the
documentation is ambiguous the ambiguity is recorded rather than resolved by guesswork.

## Confirmed capabilities used by this project

| Capability | Documented form | Used for |
|---|---|---|
| Extended M3U, UTF-8 | `#EXTM3U` first line, `#EXTINF:` pairs | every playlist |
| Live duration | `-1` (or `0`) | live channels |
| Library duration | `0` | categories and videos |
| Nested playlists | `type="playlist"` on `#EXTINF` | the whole tree |
| Video items | `type="video"` | Phase 3 VOD |
| Tile size | `#EXTSIZE: small\|medium\|big` | root/category/channel tiles |
| Tile background | `#EXTBG: <url or #rrggbb or rgba(...)>` | category tiles |
| Per-item metadata | `tvg-name`, `tvg-logo`, `description`, `audio-track`, `aspect-ratio` | channel and movie tiles |
| Playlist defaults | `#EXTM3U size="…" background="…" description="…"` | per-file defaults |
| External EPG | `#EXTM3U x-tvg-url="…"` (jtv and xmltv) | Phase 7 |

## Directive ordering

`#EXTSIZE` and `#EXTBG` sit **between** the `#EXTINF` line and the URL line:

```
#EXTINF:0 type="playlist",2015
#EXTSIZE: Medium
#EXTBG: #046f55
http://example.com/playlists/2015.m3u
```

`src/generators/m3u.py` emits exactly this order and
`tests/test_m3u_generation.py::test_extsize_extbg_sit_between_extinf_and_url` pins it.

## One documented ambiguity: `type` vs `content-type`

The documentation names the parameter **content-type** in prose ("available values:
stream, video, playlist") but every worked example writes it as `type="playlist"` /
`type="video"`. This project emits `type="…"`, because that is the form that appears
in working examples.

If tiles ever mis-render on the TV, flip it without touching any other code:

```json
// config/config.json — not currently exposed; pass to M3UBuilder(type_attr_name=…)
```

`M3UBuilder(type_attr_name="content-type")` produces the alternative spelling and is
covered by a test. The structural validator accepts both spellings.

## Sizing limits that the design respects

* **XMLTV must be under ~5 MB and must not be gzipped**, and must be served with
  `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Methods: "GET, POST, OPTIONS, HEAD"`,
  `Access-Control-Allow-Headers: "Range"` and
  `Access-Control-Expose-Headers: "Accept-Ranges, Content-Encoding, Content-Length, Content-Range"`.
  (jtv is capped much lower, ~0.5 MB.)
* The EPG is fetched **by SS IPTV's server, not by the TV**, because cross-domain
  requests are blocked on many connected-TV devices. The EPG URL therefore has to be
  reachable from the public internet, not just from the home network.
* Playlists are kept small deliberately: no generated file in this repo exceeds
  ~31 items or ~8 KB, so no single screen asks the TV to load hundreds of posters.

## What this project deliberately does not use

* `#EXTVLCOPT` — a VLC directive. SS IPTV has no documented way to send a per-stream
  `Referer` or `User-Agent`, so streams that require them are withheld from the
  published playlists instead of being published as tiles that cannot play.
* `#EXTGRP` / `group-title` as a navigation mechanism — nested playlists are the
  documented way to group content for the Video Library, and they are what the TV
  renders as browsable tiles.
* DRM, authenticated, or key-protected streams of any kind.

## Sources

- [How to make playlist for using in SS IPTV](https://ss-iptv.com/en/operators/creating-playlist)
- [Linking up Video Library](https://ss-iptv.com/en/operators/videoteka)
- [Linking up external TV Guide](https://ss-iptv.com/en/operators/epg)
- [About M3U](https://www.ss-iptv.com/en/users/documents/m3u)
- [Playlist editor](https://ss-iptv.com/en/users/playlist)

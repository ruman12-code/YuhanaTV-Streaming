# How SS IPTV walks the tree

The user enters **one** URL, once:

```
https://<domain>/iptv/master.m3u
```

Everything below is fetched lazily by the app as the user opens a tile, which is
why no single screen ever loads more than a few dozen logos.

## Live TV → category → channel

```
SS IPTV "External playlists"
└── master.m3u                        3 tiles, 765 B
    ├── 📺 Live TV      → live/live-tv.m3u        10 category tiles
    │   ├── 🇧🇩 Bangladesh (31) → live/bangladesh.m3u     31 channel tiles ──▶ plays
    │   ├── 📰 News (7)         → live/news.m3u            7 channel tiles ──▶ plays
    │   ├── 🏆 Sports (9)       → live/sports.m3u          9 channel tiles ──▶ plays
    │   ├── 🎭 Entertainment(50)→ live/entertainment.m3u   5 SUB-category tiles
    │   │   ├── 🇮🇳 Hindi (16)        → entertainment/hindi.m3u          ──▶ plays
    │   │   ├── 🇮🇳 Indian Bangla(15) → entertainment/indian-bangla.m3u  ──▶ plays
    │   │   ├── 📺 General (12)       → entertainment/general.m3u        ──▶ plays
    │   │   ├── 📚 Series (5)         → entertainment/series.m3u         ──▶ plays
    │   │   └── Other (2)             → entertainment/other.m3u          ──▶ plays
    │   ├── 🎬 Movies on TV (6) → live/movies.m3u
    │   ├── 🎵 Music (16)       → live/music.m3u
    │   ├── 🧸 Kids (12)        → live/kids.m3u
    │   ├── 🌍 Documentary (21) → live/documentary.m3u
    │   ├── ✨ Lifestyle (9)    → live/lifestyle.m3u
    │   └── 🕌 Religious (7)    → live/religious.m3u
    ├── 🇧🇩 Bangladesh TV → live/bangladesh.m3u   (shortcut to the same file)
    └── 🌐 International  → live/international.m3u (every non-Bangladesh category)
```

Mechanically, each step is one `#EXTINF` whose `type` decides what the app does:

* `type="playlist"` → the app fetches that URL and draws a new screen of tiles.
* `type="stream"` (duration `-1`) → the app opens a live player.
* `type="video"` (duration `0`) → the app opens a VOD player with seek/pause,
  when the underlying stream supports it.

Depth is 2–3 hops to any channel. `#EXTSIZE`/`#EXTBG` make the category rows read
as coloured cards rather than a text list.

## Movies → genre → movie → playback

```
master.m3u
└── 🎬 Movies  → movies/movies.m3u           genre tiles, big, with backgrounds
    ├── 🔥 Trending       → movies/trending.m3u
    ├── ⭐ Top Rated      → movies/top-rated.m3u
    ├── 🆕 Recently Added → movies/recently-added.m3u
    ├── 🇧🇩 Bengali / 🇺🇸 English / 🇮🇳 Hindi / 🇰🇷 Korean / 🇯🇵 Japanese
    └── 🎬 Action / 😂 Comedy / 🎭 Drama / 🚀 Sci-Fi / 👻 Horror / …
        └── one #EXTINF per film:
            #EXTINF:0 type="video" tvg-logo="<poster>" description="2024 • Action • 7.4",Title
            #EXTSIZE: medium
            <direct playback URL>
```

A film only appears here when `playback_status == PLAYABLE` **and**
`rights_status == CLEARED`. Everything else stays in the database and, if wanted,
surfaces in the optional web catalogue (Phase 10) as a "where to watch" entry —
never as a tile that looks playable and is not.

`PLAYABLE` is not a label the catalogue assigns itself. `scripts/pipeline.py
check-vod` fetches the first bytes of every rights-cleared playback URL and
requires: a real video container or content type, a size plausible for a feature,
and `Range` support. A file served without `Range` still plays but cannot be
seeked, so it is recorded `DEGRADED` rather than passed off as fully playable.
An HTML login wall or a 404 is rejected outright.

Collections behave as views rather than copies. **Top Rated** appears only when
ratings exist above the threshold — an unrated catalogue gets no Top Rated row
rather than a fabricated one — and **Trending** is defined as the best-rated of
the recently added, because there is no usage data to derive real trending from.

## Why the tree is shaped this way

Flat playlists are the failure mode SS IPTV's own documentation warns about: a
few hundred `tvg-logo` images fetched at once is a memory problem on a TV. Every
category here is capped, and any category that outgrows the cap is split again
automatically (`ssiptv.subsplit_threshold` in `config/config.json`).

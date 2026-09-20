# What gets into the Movie library, and why

Four gates, in order. A title must pass every one. They exist because the
library had failed each of them in turn on a real television.

## 1. The TV must be able to decode it

`is_tv_playable()` in `src/sources/archive_org/adapter.py`.

Accepted: **h.264 / MPEG-4 in `.mp4` or `.m4v`**.
Refused: `.ogv` (Ogg Theora), `.webm`, `.avi`, `.mkv`, `.mpg`, `.mpeg`, `.wmv`,
`.flv`, and any `.mp4` whose format label names an exotic codec.

This gate is applied **before** resolution, not after. Choosing the
highest-resolution file without it put 160 Ogg Theora titles into the library —
a quarter of the catalogue — which a VIDAA set cannot play at any resolution. A
480p h.264 file beats a 1080p Ogg one, because the second does not play.

Files over **2.5 GB** are also refused: they stall on a TV over a home
connection, and are usually preservation masters rather than viewing copies.

## 2. It must be a film, not a clip

`movies.min_runtime_minutes` (60) and `movies.require_known_runtime` (true).

The Archive's collections are full of ephemeral shorts, industrial films, test
footage and — through one over-broad query since removed — conference talks. Of
663 imported titles only 104 ran 70 minutes or more, while 172 ran under twenty.
Opening "Movies" and finding clips is the predictable result.

An unknown runtime is treated as a failure, not a pass: it cannot be confirmed as
a feature. That would have excluded 351 titles outright, so enrichment fills the
gap first — IMDb's `title.basics` carries `runtimeMinutes`, and it is
authoritative where the Archive's own metadata is silent.

## 3. Redistribution must be permitted

`rights_status == CLEARED`, from the item's own licence metadata, an explicit
public-domain statement, membership of a curated public-domain collection, or a
creator whose whole output is openly licensed. Silence yields `UNVERIFIED`, which
the gate refuses. Permission is never inferred from a file being reachable.

## 4. The bytes must actually be there

`playback_status == PLAYABLE`, set only after `scripts/pipeline.py check-vod`
fetches the first bytes and confirms a real video container, a plausible size,
and `Range` support. A file without Range support plays but cannot be seeked, so
it is recorded `DEGRADED` rather than passed off as playable.

Plus the rating gate: a title **rated** below `movies.min_rating_publish` (6.0)
is withheld, while an **unrated** one is kept, because most public-domain cinema
is simply not in IMDb.

## What this costs

The gates are strict and the catalogue is much smaller for it. That is the
intended trade: a short list where everything plays beats a long one where most
tiles fail. Every exclusion is counted in `validation-report.json` under
`movies.withheld`, so the cost is visible rather than silent.

## Quality tiers

`4k`, `hd` (720p+) and `fullhd` (1080p+) are built from the height the file
declares. A title whose dimensions the source never stated appears in none of
them rather than being optimistically counted as HD.

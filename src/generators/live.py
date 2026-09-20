"""Generate the Live TV playlist tree (spec sections 5, 6, 15).

Shape produced:

    playlists/master.m3u                 root: Live TV / Bangladesh / International / Movies
    playlists/live/live-tv.m3u           index of live categories (nested playlists)
    playlists/live/<category>.m3u        the channels in one category
    playlists/live/international.m3u     every non-Bangladesh channel, grouped view

Publication gate: a channel is only written into a playlist when its validated
`status` is in `validation.publish_statuses`. The gate can be relaxed explicitly
(`allow_unverified=True`) for a pre-validation seed build, and when it is, the
playlist says so in a comment line rather than pretending the streams are known good.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import Channel
from .m3u import M3UBuilder
from .catalog import live_meta, subgroup_meta, LIVE_CATEGORY_META


@dataclass
class BuildResult:
    files: dict[str, int]                # relative path -> bytes
    published: list[Channel]
    withheld: dict[str, list[Channel]]   # reason -> channels


class LiveGenerator:
    def __init__(self, cfg, playlists_root: Path) -> None:
        self.cfg = cfg
        self.root = Path(playlists_root)
        self.live_dir = self.root / "live"
        self.max_items = int(cfg.get_path("ssiptv.max_items_per_playlist", 120))
        self.attach_epg = bool(cfg.get_path("ssiptv.attach_epg_to_live", True))
        self.size_root = cfg.get_path("ssiptv.tile_size_root", "big")
        self.size_cat = cfg.get_path("ssiptv.tile_size_category", "medium")
        self.size_chan = cfg.get_path("ssiptv.tile_size_channel", "small")
        self.publish_statuses = set(cfg.get_path("validation.publish_statuses", ["ACTIVE"]))
        self.fallback_statuses = set(
            cfg.get_path("validation.publish_statuses_when_empty", ["ACTIVE", "DEGRADED"])
        )
        # A category larger than this is split again by its source sub-group, so no
        # single SS IPTV screen has to load an unbounded number of tiles (spec 22/34).
        self.subsplit_threshold = int(cfg.get_path("ssiptv.subsplit_threshold", 40))
        self.never_subsplit = set(cfg.get_path("ssiptv.never_subsplit", []))
        # Once a channel has enough history, sustained unreliability disqualifies it
        # even on a run where it happens to answer. A tile that works one time in
        # four is worse than no tile.
        self.min_reliability = float(cfg.get_path("validation.min_reliability", 0.0))
        self.min_checks_for_gate = int(
            cfg.get_path("validation.min_checks_for_reliability_gate", 5))

    # --- gating --------------------------------------------------------------

    def _partition(self, channels: list[Channel], allow_unverified: bool):
        allowed = set(self.publish_statuses)
        if allow_unverified:
            allowed |= {"UNVERIFIED", "DEGRADED"}

        published: list[Channel] = []
        withheld: dict[str, list[Channel]] = {}

        def hold(reason: str, ch: Channel) -> None:
            withheld.setdefault(reason, []).append(ch)

        for ch in channels:
            if ch.rights_status == "EXCLUDED":
                hold("rights_excluded", ch)
                continue
            # SS IPTV cannot send a per-stream Referer or User-Agent; a stream that
            # needs them is a guaranteed failure on the TV, so it is never published.
            if ch.requires_custom_headers:
                hold("requires_custom_http_headers", ch)
                continue
            if not ch.stream_url:
                hold("no_stream_url", ch)
                continue
            if ch.status not in allowed:
                hold(f"status_{ch.status.lower()}", ch)
                continue
            if (self.min_reliability > 0
                    and ch.checks_total >= self.min_checks_for_gate
                    and ch.reliability < self.min_reliability):
                hold("unreliable", ch)
                continue
            published.append(ch)
        return published, withheld

    # --- rendering -----------------------------------------------------------

    def _channel_description(self, ch: Channel) -> str:
        bits = []
        if ch.resolution_label:
            bits.append(ch.resolution_label)          # only ever a measured label
        if ch.language:
            bits.append(ch.language.upper())
        if ch.country:
            bits.append(ch.country.upper())
        return " • ".join(bits)

    def _channel_playlist(self, channels: list[Channel], *, title: str,
                          seed_note: str) -> M3UBuilder:
        b = M3UBuilder(
            tvg_url=self.cfg.epg_url() if self.attach_epg else "",
            default_size=self.size_chan,
            default_description=title,
            header_comment=seed_note,
        )
        for ch in sorted(channels, key=lambda c: c.name.lower()):
            b.add_stream(
                ch.name,
                ch.stream_url,
                tvg_id=ch.epg_id,
                tvg_name=ch.name,
                logo=ch.logo,
                description=self._channel_description(ch),
                duration="-1",
            )
        return b

    @staticmethod
    def _page_label(chunk: list[Channel]) -> str:
        def initial(c: Channel) -> str:
            for ch in c.name.strip():
                if ch.isalnum():
                    return ch.upper()
            return "#"
        first, last = initial(chunk[0]), initial(chunk[-1])
        return first if first == last else f"{first}-{last}"

    def _write_leaf(self, items: list[Channel], *, title: str, seed_note: str,
                    rel: str, files: dict[str, int], bg: str = "#444444") -> None:
        """Write one screen of channels, paginating when there are too many.

        The movie tree gained this when a bucket silently truncated; the live
        tree needed it too. A sub-group can exceed the budget on its own - the
        Movies category alone produced a 207-channel screen - and splitting the
        category once is not enough.
        """
        ordered = sorted(items, key=lambda c: c.name.lower())
        target = self.root / rel

        if len(ordered) <= self.max_items:
            files[rel] = self._channel_playlist(
                ordered, title=title, seed_note=seed_note).write(target)
            return

        pages = (len(ordered) + self.max_items - 1) // self.max_items
        per = (len(ordered) + pages - 1) // pages
        chunks = [ordered[i:i + per] for i in range(0, len(ordered), per)]
        stem = rel[:-4]                      # strip ".m3u"

        index = M3UBuilder(default_size=self.size_cat, default_description=title,
                           header_comment=seed_note)
        for n, chunk in enumerate(chunks, start=1):
            if not chunk:
                continue
            page_rel = f"{stem}/{n:02d}.m3u"
            label = self._page_label(chunk)
            files[page_rel] = self._channel_playlist(
                chunk, title=f"{title} {label}", seed_note=seed_note).write(
                self.root / page_rel)
            index.add_playlist(f"{title} {label} ({len(chunk)})",
                               self.cfg.playlist_url(page_rel),
                               description=f"{len(chunk)} channels",
                               size=self.size_cat, background=bg)
        files[rel] = index.write(target)

    def build(self, channels: list[Channel], *, allow_unverified: bool = False) -> BuildResult:
        published, withheld = self._partition(channels, allow_unverified)

        seed_note = ""
        if allow_unverified:
            seed_note = (
                "PRE-VALIDATION SEED BUILD. No stream in this file has been "
                "reachability-tested; entries may not play. Regenerated by CI after validation."
            )

        # Nothing publishable: return before touching the filesystem, so a bad
        # run cannot leave the user with no playlists at all. The caller decides
        # whether that is an error; the previously published tree stays intact.
        if not published:
            return BuildResult(files={}, published=[], withheld=withheld)

        # Remove the previous tree. Without this, a category that loses its last
        # publishable channel would leave a stale playlist behind, still deployed
        # and still serving dead streams.
        if self.live_dir.exists():
            for stale in sorted(self.live_dir.rglob("*.m3u"), reverse=True):
                stale.unlink()
            for d in sorted((d for d in self.live_dir.rglob("*") if d.is_dir()), reverse=True):
                if not any(d.iterdir()):
                    d.rmdir()

        files: dict[str, int] = {}
        by_category: dict[str, list[Channel]] = {}
        for ch in published:
            by_category.setdefault(ch.category, []).append(ch)

        # 1. one playlist per category, sub-split when it would be too large
        for category, items in by_category.items():
            meta = live_meta(category)
            subgroups = self._subgroups(category, items)
            if subgroups:
                for slug, sub_items in subgroups.items():
                    sm = subgroup_meta(slug)
                    self._write_leaf(
                        sub_items,
                        title=f"{meta['label']} — {sm['label']}",
                        seed_note=seed_note,
                        rel=f"live/{category}/{slug}.m3u",
                        files=files,
                        bg=sm.get("bg", "#444444"),
                    )
                index = M3UBuilder(
                    default_size=self.size_cat, default_description=meta["label"],
                    header_comment=seed_note,
                )
                for slug, sub_items in sorted(
                    subgroups.items(), key=lambda kv: subgroup_meta(kv[0])["order"]
                ):
                    sm = subgroup_meta(slug)
                    index.add_playlist(
                        f"{sm['label']} ({len(sub_items)})",
                        self.cfg.playlist_url(f"live/{category}/{slug}.m3u"),
                        description=f"{len(sub_items)} channels",
                        size=self.size_cat, background=sm["bg"],
                    )
                files[f"live/{category}.m3u"] = index.write(self.live_dir / f"{category}.m3u")
            else:
                self._write_leaf(items, title=meta["label"], seed_note=seed_note,
                                 rel=f"live/{category}.m3u", files=files,
                                 bg=meta.get("bg", "#444444"))

        # 2. international.m3u - every non-Bangladesh channel (spec section 8)
        intl = [c for c in published if c.category != "bangladesh"]
        if intl:
            builder = self._index_of_categories(
                {c: v for c, v in by_category.items() if c != "bangladesh"},
                title="🌐 International TV",
                seed_note=seed_note,
            )
            files["live/international.m3u"] = builder.write(self.live_dir / "international.m3u")

        # 3. live-tv.m3u - index of every category
        index = self._index_of_categories(by_category, title="📺 Live TV", seed_note=seed_note)
        files["live/live-tv.m3u"] = index.write(self.live_dir / "live-tv.m3u")

        return BuildResult(files=files, published=published, withheld=withheld)

    def _subgroups(self, category: str, items: list[Channel]) -> dict[str, list[Channel]]:
        """Split an oversized category by its source sub-group, or return {} to keep it flat.

        Returns {} unless the split is actually worth doing: the category must be
        over threshold, and the split must produce at least two groups that are
        each meaningfully populated, otherwise the extra click buys nothing.
        """
        if category in self.never_subsplit or len(items) <= self.subsplit_threshold:
            return {}
        groups: dict[str, list[Channel]] = {}
        for ch in items:
            slug = (ch.tags[0] if ch.tags else "") or "other"
            groups.setdefault(slug, []).append(ch)
        if len(groups) < 2:
            return {}
        # Fold groups of one or two into 'other' so we do not create near-empty screens.
        folded: dict[str, list[Channel]] = {}
        spill: list[Channel] = []
        for slug, members in groups.items():
            (folded.setdefault(slug, []) if len(members) >= 3 else spill).extend(members)
        folded = {k: v for k, v in folded.items() if v}
        if spill:
            folded.setdefault("other", []).extend(spill)
        return folded if len(folded) >= 2 else {}

    def _index_of_categories(self, by_category: dict[str, list[Channel]], *,
                             title: str, seed_note: str) -> M3UBuilder:
        b = M3UBuilder(
            default_size=self.size_cat,
            default_description=title,
            header_comment=seed_note,
        )
        ordered = sorted(
            by_category.items(),
            key=lambda kv: (LIVE_CATEGORY_META.get(kv[0], {}).get("order", 999), kv[0]),
        )
        for category, items in ordered:
            meta = live_meta(category)
            b.add_playlist(
                f"{meta['label']} ({len(items)})",
                self.cfg.playlist_url(f"live/{category}.m3u"),
                description=f"{len(items)} channels",
                size=self.size_cat,
                background=meta["bg"],
            )
        return b


def _artwork(items, attr: str = "poster", *, used: set[str] | None = None) -> str:
    """Pick a representative image for a tile background.

    #EXTBG accepts an image URL, not only a colour, which is the one lever SS
    IPTV gives us for a card-based home screen. Rows are ranked the same way, so
    the top item repeats across several of them; `used` keeps each tile distinct
    and only falls back to repeating when nothing else is available.
    """
    used = used if used is not None else set()
    first = ""
    for item in items:
        value = getattr(item, attr, "") or ""
        if not value.startswith("http"):
            continue
        first = first or value
        if value not in used:
            used.add(value)
            return value
    return first


def build_master(cfg, playlists_root: Path, *, movies=None, channels=None,
                 seed_note: str = "") -> int:
    """The single URL the user configures in SS IPTV (spec sections 15 and 23).

    This is the home screen, so it is curated rather than a bare index: the rows
    someone actually reaches for first, as large tiles carrying real artwork,
    with the exhaustive listings one level behind them.
    """
    root = Path(playlists_root)
    movies = list(movies or [])
    channels = list(channels or [])

    def exists(rel: str) -> bool:
        return (root / rel).exists()

    by_rating = sorted((m for m in movies if m.rating is not None),
                       key=lambda m: -(m.rating or 0))
    uhd = [m for m in movies if m.height >= 2000 or m.width >= 3600]
    hd = [m for m in movies if m.height >= 700]
    bd = [c for c in channels if c.category == "bangladesh"]

    b = M3UBuilder(
        default_size="big",
        default_description=f"{cfg.get_path('site.brand', 'YuhanaTV')} — personal media library",
        header_comment=seed_note,
    )

    # (relative playlist, label, blurb, fallback colour, artwork source)
    rows = [
        ("movies/trending.m3u", "🔥 Trending Now",
         "Best rated of what arrived recently", "#b3121b", by_rating),
        ("movies/4k.m3u", "💎 4K Ultra HD",
         f"{len(uhd)} titles in 4K", "#3b1c6b", uhd),
        ("movies/hd.m3u", "🎞️ HD Movies",
         f"{len(hd)} titles in 720p or better", "#0e5c8a", hd),
        ("movies/top-rated.m3u", "⭐ Top Rated",
         "Highest scoring films in the library", "#8a6a00", by_rating),
        ("movies/movies.m3u", "🎬 All Movies",
         "Browse by genre and language", "#8e1b1b", by_rating),
        ("live/bangladesh.m3u", "🇧🇩 Bangladesh TV",
         f"{len(bd)} channels", "#006a4e", bd),
        ("live/live-tv.m3u", "📺 Live TV",
         "Every channel by category", "#1f3a93", channels),
        ("live/international.m3u", "🌍 International TV",
         "Channels from outside Bangladesh", "#2c3e75",
         [c for c in channels if c.category != "bangladesh"]),
    ]

    used_art: set[str] = set()
    for rel, label, blurb, colour, art_items in rows:
        if not exists(rel):
            continue
        attr = "poster" if rel.startswith("movies/") else "logo"
        b.add_playlist(
            label,
            cfg.playlist_url(rel),
            description=blurb,
            size="big",
            # Artwork where we have it, a solid colour where we do not: an empty
            # tile is worse than a plain one.
            background=_artwork(art_items, attr, used=used_art) or colour,
        )

    return b.write(root / "master.m3u")

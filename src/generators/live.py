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

    def build(self, channels: list[Channel], *, allow_unverified: bool = False) -> BuildResult:
        published, withheld = self._partition(channels, allow_unverified)

        seed_note = ""
        if allow_unverified:
            seed_note = (
                "PRE-VALIDATION SEED BUILD. No stream in this file has been "
                "reachability-tested; entries may not play. Regenerated by CI after validation."
            )

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
                    builder = self._channel_playlist(
                        sub_items, title=f"{meta['label']} — {subgroup_meta(slug)['label']}",
                        seed_note=seed_note,
                    )
                    rel = f"live/{category}/{slug}.m3u"
                    files[rel] = builder.write(self.live_dir / category / f"{slug}.m3u")
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
                builder = self._channel_playlist(
                    items, title=meta["label"], seed_note=seed_note
                )
                files[f"live/{category}.m3u"] = builder.write(self.live_dir / f"{category}.m3u")

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


def build_master(cfg, playlists_root: Path, *, have_live: bool, have_bangladesh: bool,
                 have_international: bool, have_movies: bool, seed_note: str = "") -> int:
    """The single URL the user configures in SS IPTV (spec section 15)."""
    b = M3UBuilder(
        default_size=cfg.get_path("ssiptv.tile_size_root", "big"),
        default_description=f"{cfg.get_path('site.brand', 'YuhanaTV')} — personal media library",
        header_comment=seed_note,
    )
    if have_live:
        b.add_playlist("📺 Live TV", cfg.playlist_url("live/live-tv.m3u"),
                       description="All live channels by category", size="big", background="#1f3a93")
    if have_bangladesh:
        b.add_playlist("🇧🇩 Bangladesh TV", cfg.playlist_url("live/bangladesh.m3u"),
                       description="Bangladeshi channels", size="big", background="#006a4e")
    if have_international:
        b.add_playlist("🌐 International TV", cfg.playlist_url("live/international.m3u"),
                       description="Channels from outside Bangladesh", size="big", background="#2c3e75")
    if have_movies:
        b.add_playlist("🎬 Movies", cfg.playlist_url("movies/movies.m3u"),
                       description="Video library", size="big", background="#8e1b1b")
    return b.write(Path(playlists_root) / "master.m3u")

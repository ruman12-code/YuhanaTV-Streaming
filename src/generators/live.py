"""Generate the Live TV playlist tree (spec sections 5, 6, 15).

Shape produced:

    playlists/master.m3u                 root: Live TV / Bangladesh / International / Movies
    playlists/live/live-tv.m3u           index of live categories (nested playlists)
    playlists/live/<category>.m3u        the channels in one category

Publication gate: a channel is only written into a playlist when its validated
`status` is in `validation.publish_statuses`. The gate can be relaxed explicitly
(`allow_unverified=True`) for a pre-validation seed build, and when it is, the
playlist says so in a comment line rather than pretending the streams are known good.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..models import Channel
from ..util.urls import check_url
from .m3u import M3UBuilder
from .catalog import live_meta, subgroup_meta, region_meta, LIVE_CATEGORY_META
from .countries import continent_of, continent_meta
from ..classify import movie_language_bucket, display_key, clean_display_name

# The validator's wording for the one DEGRADED cause that still plays on a TV.
# Anchored, so "manifest served but the first segment was not fetchable" can
# never match it by accident.
SLOW_ORIGIN_RE = re.compile(r"^slow origin \(", re.I)


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
        self.protected_subsplit_threshold = int(
            cfg.get_path("ssiptv.protected_subsplit_threshold", 120))
        # Categories large enough that "where is this from" is the useful first
        # question. Splitting Movies by region beats one 250-channel wall.
        self.region_split = set(cfg.get_path("ssiptv.region_split_categories", []))
        # Once a channel has enough history, sustained unreliability disqualifies it
        # even on a run where it happens to answer. A tile that works one time in
        # four is worse than no tile.
        self.require_https = bool(cfg.get_path("security.require_https_streams", False))
        self.denied_hosts = tuple(cfg.get_path("security.denied_stream_hosts", []) or ())
        self.min_reliability = float(cfg.get_path("validation.min_reliability", 0.0))
        self.min_reliability_by_category = dict(
            cfg.get_path("validation.min_reliability_by_category", {}) or {})
        self.min_checks_for_gate = int(
            cfg.get_path("validation.min_checks_for_reliability_gate", 5))
        # DEGRADED is not one condition. The validator uses it for a slow origin
        # (the stream plays, it just takes longer to start), for an unfetchable
        # segment or variant (a black screen on the TV), and the pipeline reuses
        # it as the holding state for a channel that failed once but not enough
        # times to be condemned. Publishing on status alone threw away the
        # difference: T Sports HD, which answered 38 of 46 probes, vanished from
        # the Bangladesh screen because one run found its origin slow. A slow
        # channel with a clean streak and good history is published; the other
        # two cases are not.
        self.publish_slow_degraded = bool(
            cfg.get_path("validation.publish_slow_degraded", True))
        self.slow_degraded_min_reliability = float(
            cfg.get_path("validation.slow_degraded_min_reliability", 0.7))
        # Validation runs in a US datacentre; the TV is in South Asia. When an
        # origin in the viewer's own region refuses the runner, the probe has
        # measured the distance between GitHub and the broadcaster, not whether
        # the owner can watch. Those channels are published on the strength of
        # where the viewer is.
        self.publish_region_blocked = bool(
            cfg.get_path("validation.publish_region_blocked", True))
        self.min_country_folder = int(cfg.get_path("ssiptv.min_country_folder", 4))
        self.pinned_countries = [str(c).lower() for c in
                                 cfg.get_path("ssiptv.pinned_countries", []) or ()]
        self.min_bucket = int(cfg.get_path("ssiptv.min_bucket_size", 3))
        self.owner_source = cfg.get_path("ssiptv.owner_source_id", "src-user-m3u")
        self.viewer_region = {
            str(c).lower() for c in cfg.get_path("site.viewer_region_countries", []) or ()}

    # --- gating --------------------------------------------------------------

    def load_home_probe(self, path) -> int:
        """Adopt measurements taken on the owner's own network.

        These outrank every verdict this pipeline can reach on its own. CI
        probes from a US datacentre and the television is in Bangladesh; where
        the two disagree, the one standing in the right country wins. Returns
        how many verdicts were loaded.
        """
        self.home_probe = {}
        try:
            payload = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            return 0
        for cid, row in (payload.get("results") or {}).items():
            status = str(row.get("status", "")).upper()
            if status:
                self.home_probe[cid] = status
        return len(self.home_probe)

    def _partition(self, channels: list[Channel], allow_unverified: bool):
        allowed = set(self.publish_statuses)
        if allow_unverified:
            allowed |= {"UNVERIFIED", "DEGRADED"}

        published: list[Channel] = []
        withheld: dict[str, list[Channel]] = {}

        def hold(reason: str, ch: Channel) -> None:
            withheld.setdefault(reason, []).append(ch)

        home = getattr(self, "home_probe", {}) or {}

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
            # Both the advertised URL and the endpoint it actually redirects to.
            # Checking only the first let a link shortener carry anything past
            # every rule here, which is why shorteners had to be banned outright.
            # Measuring the destination is the better answer than banning the
            # door: it also caught nothing being hidden behind iptv-org's own
            # redirector, which a blanket ban was withholding 1,020 channels for.
            verdict = check_url(ch.stream_url, require_https=self.require_https,
                                denied_hosts=self.denied_hosts)
            if verdict.ok and ch.final_url and ch.final_url != ch.stream_url:
                verdict = check_url(ch.final_url, require_https=self.require_https,
                                    denied_hosts=self.denied_hosts)
            if not verdict.ok:
                reason = ("plain_http" if "plain http" in verdict.reason
                          else "denied_host" if "denied-host" in verdict.reason
                          else "subscriber_credentials"
                          if "subscriber credentials" in verdict.reason
                          else "unsafe_url")
                hold(reason, ch)
                continue
            # The owner's own network has the last word on reachability. Note
            # what this does NOT override: the rules above it. A stream needing
            # HTTP headers SS IPTV cannot send, one carrying somebody's
            # subscription credentials, and adult material are all still out,
            # because none of those is a question about whether the packets
            # arrive.
            verdict_at_home = home.get(ch.id)
            if verdict_at_home:
                if verdict_at_home in ("ACTIVE", "DEGRADED"):
                    published.append(ch)
                else:
                    hold("offline_from_your_network", ch)
                continue

            if ch.status not in allowed and not (
                    ch.status == "DEGRADED" and self._slow_but_working(ch)):
                if self._blocked_from_here_only(ch):
                    published.append(ch)
                    continue
                hold(f"status_{ch.status.lower()}", ch)
                continue
            # Applied to slow-but-working channels too, so the re-admission above
            # can only ever be narrower than the ordinary gate, never a way round it.
            floor = self.min_reliability_by_category.get(ch.category, self.min_reliability)
            if (floor > 0
                    and ch.checks_total >= self.min_checks_for_gate
                    and ch.reliability < floor):
                hold("unreliable", ch)
                continue
            published.append(ch)

        published = self._collapse_duplicates(published, hold)
        return published, withheld

    def _slow_but_working(self, ch) -> bool:
        """True for a DEGRADED channel whose only complaint is a slow origin.

        Requires all three: the recorded reason is latency and nothing else, the
        channel is not mid-failure (a hard failure below the condemn threshold
        also parks a channel in DEGRADED), and its accumulated history clears the
        bar. Any of those missing and the channel stays withheld.
        """
        if not self.publish_slow_degraded:
            return False
        if not SLOW_ORIGIN_RE.match(ch.status_reason or ""):
            return False
        if ch.consecutive_failures:
            return False
        if ch.checks_total < self.min_checks_for_gate:
            return False
        return ch.reliability >= self.slow_degraded_min_reliability

    def _blocked_from_here_only(self, ch) -> bool:
        """True when the probe's failure says more about the runner than the stream.

        The failure must be a refusal rather than an absence: an explicit
        403/451, or a source that marks the channel fenced to its home
        territory. A DNS failure, a refused connection or a 404 is a dead
        stream from everywhere and stays withheld - that is a measurement of
        the stream, not of where we stood when we took it.
        """
        if not self.publish_region_blocked:
            return False
        # Geography is never itself a reason to withhold a channel: the owner
        # said so, and the reasoning behind the old regional limit - that a US
        # channel refusing a US probe would refuse Dhaka too - was a guess about
        # somebody else's CDN, not a measurement. A refusal means the origin
        # would not serve THIS vantage point, which is the one place we know the
        # owner is not watching from.
        if ch.last_http_status in (403, 451):
            return True
        return "geo-blocked" in (ch.tags or [])

    def _collapse_duplicates(self, published: list[Channel], hold) -> list[Channel]:
        """One tile per channel, keeping the best-evidenced entry.

        Cross-source de-duplication at ingest matches on the stream URL, so two
        sources carrying the same channel on different origins both survive. On
        the Bangladesh screen that showed as ATN Bangla twice, plus Boishakhi
        next to Boishakhi TV and Bangla Vision next to Banglavision - the same
        channel, spelled differently.

        Done here rather than at ingest on purpose. Both records keep accruing
        reliability history in the registry, and by this point status and
        reliability are known, so the entry that is kept is the one with the
        better evidence rather than whichever source was read first.
        """
        best: dict[tuple[str, str], Channel] = {}
        order: list[tuple[str, str]] = []
        for ch in published:
            key = (ch.category, display_key(ch.name))
            current = best.get(key)
            if current is None:
                best[key] = ch
                order.append(key)
            elif self._evidence(ch) > self._evidence(current):
                best[key] = ch
                hold("duplicate_of_better_entry", current)
            else:
                hold("duplicate_of_better_entry", ch)
        return [best[k] for k in order]

    @staticmethod
    def _evidence(ch: Channel) -> tuple:
        """Ranking for two records of the same channel. Measurement first."""
        height = 0
        if "x" in (ch.resolution or ""):
            try:
                height = int(ch.resolution.split("x")[1])
            except (ValueError, IndexError):
                height = 0
        return (ch.reliability, ch.checks_total, height, ch.status == "ACTIVE",
                len(ch.name))

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

        # A-Z pages are all one letter, so the first-to-last label reads "A A"
        # on every page and no tile can be told from its neighbour. Fall back to
        # numbering whenever the labels do not actually distinguish the pages.
        live_chunks = [c for c in chunks if c]
        labels = [self._page_label(c) for c in live_chunks]
        if len(set(labels)) < len(labels):
            labels = [f"{n} of {len(live_chunks)}" for n in range(1, len(live_chunks) + 1)]

        index = M3UBuilder(default_size=self.size_cat, default_description=title,
                           header_comment=seed_note)
        for n, (label, chunk) in enumerate(zip(labels, live_chunks), start=1):
            page_rel = f"{stem}/{n:02d}.m3u"
            files[page_rel] = self._channel_playlist(
                chunk, title=f"{title} {label}", seed_note=seed_note).write(
                self.root / page_rel)
            index.add_playlist(f"{label} ({len(chunk)})",
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
                    subgroups.items(),
                    # Order first, then the label: the generated country folders
                    # all share one order value and must come out alphabetically
                    # rather than in the order the channels happened to be read.
                    key=lambda kv: (subgroup_meta(kv[0])["order"],
                                    subgroup_meta(kv[0])["label"]),
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

        # A separate "international" index used to sit here. Region splitting now
        # answers the same question better - Movies and Series open on Bangladesh,
        # India, United States and so on - and keeping both left one of them
        # unreachable from the home screen.

        # 3. live-tv.m3u - index of every category
        index = self._index_of_categories(by_category, title="📺 Live TV", seed_note=seed_note)
        files["live/live-tv.m3u"] = index.write(self.live_dir / "live-tv.m3u")

        # 4-6. The other two doors to the same rooms, plus the owner's own list.
        #
        # One hierarchy cannot serve eight thousand channels. Genre-first asks
        # the viewer to guess whether Zee Bangla is Entertainment, General or
        # Movies, and guessing wrong means starting over. Every channel is
        # therefore reachable three ways - by country, by genre, and by first
        # letter - so a wrong guess costs a click instead of a search.
        self._build_by_country(published, files, seed_note)
        self._build_alphabetical(published, files, seed_note)
        self._build_my_channels(published, files, seed_note)
        self._build_favourites(published, files, seed_note)

        return BuildResult(files=files, published=published, withheld=withheld)

    # --- the other two doors --------------------------------------------------

    def _build_by_country(self, published: list[Channel], files: dict, seed_note: str) -> None:
        """Every country a folder, every folder its genres when it is large."""
        by_country: dict[str, list[Channel]] = {}
        for ch in published:
            by_country.setdefault((ch.country or "").lower() or "other", []).append(ch)

        rows = []
        spill: list[Channel] = []
        for code, items in by_country.items():
            # A country with a handful of channels does not earn a screen; it
            # joins the single Others folder at the end.
            if code == "other" or len(items) < self.min_country_folder:
                spill.extend(items)
                continue
            rows.append((code, items))

        def write_country(code, items):
            """One country's screen; returns its path and label metadata."""
            meta = region_meta(code)
            rel = f"live/country/{code}.m3u"
            if len(items) > self.subsplit_threshold:
                self._write_genre_index(items, rel=rel, title=meta["label"],
                                        sub_dir=f"live/country/{code}",
                                        files=files, seed_note=seed_note)
            else:
                self._write_leaf(items, title=meta["label"], seed_note=seed_note,
                                 rel=rel, files=files, bg=meta["bg"])
            return rel, meta

        # 167 countries will not fit on one screen, and a remote should not have
        # to scroll a list that long. Continents hold them, with the countries
        # that matter here pinned above so they are never more than one press
        # away.
        pinned = [r for r in rows if r[0] in self.pinned_countries]
        pinned.sort(key=lambda r: region_meta(r[0])["order"])
        rest = [r for r in rows if r[0] not in self.pinned_countries]

        index = M3UBuilder(default_size=self.size_cat,
                           default_description="🌏 By Country", header_comment=seed_note)
        for code, items in pinned:
            rel, meta = write_country(code, items)
            index.add_playlist(f"{meta['label']} ({len(items)})",
                               self.cfg.playlist_url(rel),
                               description=f"{len(items)} channels",
                               size=self.size_cat, background=meta["bg"])

        by_cont: dict[str, list] = {}
        for code, items in rest:
            by_cont.setdefault(continent_of(code), []).append((code, items))

        for key, members in sorted(by_cont.items(),
                                   key=lambda kv: continent_meta(kv[0])["order"]):
            cmeta = continent_meta(key)
            sub = M3UBuilder(default_size=self.size_cat,
                             default_description=cmeta["label"], header_comment=seed_note)
            total = 0
            for code, items in sorted(members,
                                      key=lambda r: region_meta(r[0])["label"]):
                rel, meta = write_country(code, items)
                total += len(items)
                sub.add_playlist(f"{meta['label']} ({len(items)})",
                                 self.cfg.playlist_url(rel),
                                 description=f"{len(items)} channels",
                                 size=self.size_cat, background=meta["bg"])
            crel = f"live/country/_{key}.m3u"
            files[crel] = sub.write(self.root / crel)
            index.add_playlist(f"{cmeta['label']} ({len(members)} countries)",
                               self.cfg.playlist_url(crel),
                               description=f"{total} channels",
                               size=self.size_cat, background=cmeta["bg"])

        if spill:
            rel = "live/country/others.m3u"
            self._write_leaf(spill, title="🌍 Others", seed_note=seed_note,
                             rel=rel, files=files, bg="#5d6d7e")
            index.add_playlist(f"🌍 Others ({len(spill)})", self.cfg.playlist_url(rel),
                               description="Countries with only a few channels",
                               size=self.size_cat, background="#5d6d7e")

        files["live/by-country.m3u"] = index.write(self.live_dir / "by-country.m3u")

    def _write_genre_index(self, items: list[Channel], *, rel: str, title: str,
                           sub_dir: str, files: dict, seed_note: str) -> None:
        """A screen of genre folders for one country."""
        by_cat: dict[str, list[Channel]] = {}
        for ch in items:
            by_cat.setdefault(ch.category, []).append(ch)
        index = M3UBuilder(default_size=self.size_cat, default_description=title,
                           header_comment=seed_note)
        small: list[Channel] = []
        entries = []
        for cat, members in by_cat.items():
            (entries.append((cat, members)) if len(members) >= self.min_bucket
             else small.extend(members))
        for cat, members in sorted(entries,
                                   key=lambda kv: live_meta(kv[0])["order"]):
            meta = live_meta(cat)
            leaf = f"{sub_dir}/{cat}.m3u"
            self._write_leaf(members, title=f"{title} — {meta['label']}",
                             seed_note=seed_note, rel=leaf, files=files,
                             bg=meta.get("bg", "#444444"))
            index.add_playlist(f"{meta['label']} ({len(members)})",
                               self.cfg.playlist_url(leaf),
                               description=f"{len(members)} channels",
                               size=self.size_cat, background=meta["bg"])
        if small:
            leaf = f"{sub_dir}/others.m3u"
            self._write_leaf(small, title=f"{title} — 🌍 Others", seed_note=seed_note,
                             rel=leaf, files=files, bg="#5d6d7e")
            index.add_playlist(f"🌍 Others ({len(small)})", self.cfg.playlist_url(leaf),
                               description="Everything else from here",
                               size=self.size_cat, background="#5d6d7e")
        files[rel] = index.write(self.root / rel)

    _AZ_BUCKETS = ("#",) + tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    def _build_alphabetical(self, published: list[Channel], files: dict,
                            seed_note: str) -> None:
        """One folder per first letter. The answer to "I know its name"."""
        buckets: dict[str, list[Channel]] = {}
        for ch in published:
            first = (clean_display_name(ch.name)[:1] or "#").upper()
            buckets.setdefault(first if first.isascii() and first.isalpha() else "#",
                               []).append(ch)
        # Within a letter, one tile per name. The genre tree keeps a channel
        # separate per category, which is right there and wrong here: an A-Z
        # screen showing "ADA TV" twice gives the viewer no way to choose.
        for letter, items in buckets.items():
            seen: dict[str, Channel] = {}
            for ch in items:
                key = display_key(ch.name)
                if key not in seen or self._evidence(ch) > self._evidence(seen[key]):
                    seen[key] = ch
            buckets[letter] = list(seen.values())

        index = M3UBuilder(default_size=self.size_cat, default_description="🔤 A–Z",
                           header_comment=seed_note)
        for letter in self._AZ_BUCKETS:
            items = buckets.get(letter)
            if not items:
                continue
            rel = f"live/az/{'sym' if letter == '#' else letter.lower()}.m3u"
            label = "0–9 & symbols" if letter == "#" else letter
            self._write_leaf(items, title=f"🔤 {label}", seed_note=seed_note,
                             rel=rel, files=files, bg="#37474f")
            index.add_playlist(f"{label} ({len(items)})", self.cfg.playlist_url(rel),
                               description=f"{len(items)} channels",
                               size=self.size_cat, background="#37474f")
        files["live/a-z.m3u"] = index.write(self.live_dir / "a-z.m3u")

    def _build_my_channels(self, published: list[Channel], files: dict,
                           seed_note: str) -> None:
        """The owner's own playlist, the one that was working before any of this.

        These are the channels he curated for himself, so they are the ones he
        reaches for daily. Everything else is a long tail he browses
        occasionally. Putting them one click from home is the single biggest
        thing this structure can do for daily use.
        """
        mine = [c for c in published if c.source == self.owner_source]
        if not mine:
            return
        self._write_leaf(mine, title="⭐ My Channels", seed_note=seed_note,
                         rel="live/my-channels.m3u", files=files, bg="#b8860b")

    def _build_favourites(self, published: list[Channel], files: dict,
                          seed_note: str) -> None:
        """The owner's hand-picked list, from data/favourites.json.

        A note on how a channel gets in here. SS IPTV is somebody else's
        application: this project writes M3U files and has no way to add a
        button to its screens or to hear about a press. So the list is kept in
        the repository and the companion page writes to it - see
        docs/FAVOURITES.md. Whatever the source, the rule below is the same: a
        favourite is published if it is still publishable, and one that has gone
        off the air is held with everything else rather than being deleted,
        because the owner's choice should outlive a bad week for an origin.
        """
        rel_path = self.root.parent / "data" / "favourites.json"
        try:
            payload = json.loads(rel_path.read_text())
        except (OSError, ValueError):
            return
        wanted = {str(x) for x in (payload.get("channel_ids") or [])}
        if not wanted:
            return
        by_id = {c.id: c for c in published}
        chosen = [by_id[cid] for cid in payload["channel_ids"] if cid in by_id]
        if not chosen:
            return
        self._write_leaf(chosen, title="❤️ Favourites", seed_note=seed_note,
                         rel="live/favourites.m3u", files=files, bg="#a8324a")

    def _subgroups(self, category: str, items: list[Channel]) -> dict[str, list[Channel]]:
        """Split an oversized category by its source sub-group, or return {} to keep it flat.

        Returns {} unless the split is actually worth doing: the category must be
        over threshold, and the split must produce at least two groups that are
        each meaningfully populated, otherwise the extra click buys nothing.
        """
        # "never_subsplit" was written when the whole catalogue was 1,500
        # channels and Sports and Kids fitted on one screen. Against the full
        # iptv-org index they do not, and a flat list paginated into a dozen
        # numbered screens is worse to navigate than a country folder. So the
        # protection is now a much higher threshold rather than an absolute
        # rule: these categories stay flat while they fit, and split when the
        # only alternative is pagination.
        threshold = (self.protected_subsplit_threshold
                     if category in self.never_subsplit else self.subsplit_threshold)
        if len(items) <= threshold:
            return {}

        # Movie channels split by film language into exactly four flat folders.
        # Region was the wrong axis here: someone hunting for a Bengali film does
        # not care which country licensed the feed.
        if category == "movies":
            buckets: dict[str, list[Channel]] = {}
            for ch in items:
                buckets.setdefault(f"lang-{movie_language_bucket(ch.name, ch.country)}",
                                   []).append(ch)
            buckets = {k: v for k, v in buckets.items() if v}
            if len(buckets) >= 2:
                return buckets

        # Region first where it is meaningful and actually known.
        if category in self.region_split:
            known = [c for c in items if c.country]
            if len(known) >= len(items) // 2:
                by_region: dict[str, list[Channel]] = {}
                for ch in items:
                    by_region.setdefault(f"region-{ch.country or 'other'}", []).append(ch)
                folded: dict[str, list[Channel]] = {}
                spill: list[Channel] = []
                for slug, members in by_region.items():
                    (folded.setdefault(slug, []) if len(members) >= 3
                     else spill).extend(members)
                if spill:
                    folded.setdefault("region-other", []).extend(spill)
                if len(folded) >= 2:
                    return folded
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


def _favourite_ids(playlists_root: Path) -> list[str]:
    """Channel ids the owner has marked, from data/favourites.json."""
    try:
        payload = json.loads((Path(playlists_root).parent / "data" /
                              "favourites.json").read_text())
    except (OSError, ValueError):
        return []
    return [str(x) for x in (payload.get("channel_ids") or [])]


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

    sports = [c for c in channels if c.category == "sports"]
    series = [c for c in channels if c.category == "series"]
    kids = [c for c in channels if c.category == "kids"]
    movie_ch = [c for c in channels if c.category == "movies"]

    # The blurb names the language folders that actually exist. It used to be
    # the fixed string "Bangla, Indian, English, Others", which promised a
    # Bangla folder on a screen that had none: the only Bangla movie channel in
    # the registry was an Xtream panel URL carrying an account login, so it is
    # no longer published. A blurb that lies about the next screen is worse
    # than a shorter one.
    _LANG_LABEL = {"bangla": "Bangla", "indian": "Indian",
                   "english": "English", "others": "Others"}
    _present = {movie_language_bucket(c.name, c.country) for c in movie_ch}
    movie_blurb = ", ".join(label for key, label in _LANG_LABEL.items()
                            if key in _present) or "By language"

    # Six tiles, and three of them are the same catalogue entered by different
    # doors. One hierarchy cannot serve eight thousand channels: genre-first
    # made the owner guess whether Zee Bangla was Entertainment, General or
    # Movies, and a wrong guess meant starting over. By Country, By Genre and
    # A-Z each reach every channel, so a wrong guess now costs one click.
    #
    # My Channels is his own playlist, the one that worked before any of this.
    # Those are the channels he reaches for daily; the other eight thousand are
    # a long tail he browses occasionally, and putting the short list first is
    # the single biggest thing this structure does for everyday use.
    #
    # Sports, Kids and Movie Channels lost their home tiles to make room. They
    # are one level down under By Genre, where they were anyway, and they are
    # now also under every country that has them.
    #
    #   (relative playlist, label, blurb, art key, fallback colour)
    owner_source = cfg.get_path("ssiptv.owner_source_id", "src-user-m3u")
    mine = [c for c in channels if c.source == owner_source]
    favs = _favourite_ids(playlists_root)
    countries = len({(c.country or "").lower() for c in channels if c.country})

    rows = [
        ("live/favourites.m3u", "❤️ Favourites",
         f"{len(favs)} you marked", "favourites", "#a8324a"),
        ("live/my-channels.m3u", "⭐ My Channels",
         f"{len(mine)} you chose yourself", "my-channels", "#b8860b"),
        ("live/bangladesh.m3u",  "🇧🇩 Bangladesh TV",
         f"{len(bd)} channels", "bangladesh", "#006a4e"),
        ("live/by-country.m3u",  "🌏 By Country",
         f"{countries} countries", "by-country", "#1f3a93"),
        ("live/live-tv.m3u",     "🎭 By Genre",
         "News, Sports, Kids, Movies…", "live-tv", "#5a2a82"),
        ("live/a-z.m3u",         "🔤 A–Z",
         "Every channel by name", "a-z", "#37474f"),
        ("movies/movies.m3u",    "🎬 Movies on Demand",
         "Watch any time, by genre", "movie-library", "#7d1128"),
    ]
    # A tile that opens an empty screen is worse than no tile, so each of these
    # appears only once it has something in it.
    rows = [r for r in rows
            if not (r[0] == "live/favourites.m3u" and not favs)
            and not (r[0] == "live/my-channels.m3u" and not mine)]
    # No TV Series tile. It would have held live channels, and the owner asked
    # for an on-demand library; recent series are under copyright and no source
    # permits redistributing them. Series channels live inside All Live TV
    # instead of a tile promising something that is not there.

    art_dir = root.parent / "site" / "art"
    for rel, label, blurb, art_key, colour in rows:
        if not exists(rel):
            continue
        # Purpose-drawn tile art where it exists. The alternative was a frame
        # grabbed from whichever film sorted first, which on public-domain
        # scans meant grainy monochrome.
        art_file = art_dir / f"{art_key}.png"
        background = (cfg.base_url + f"/art/{art_key}.png") if art_file.exists() else colour
        b.add_playlist(label, cfg.playlist_url(rel), description=blurb,
                       size="big", background=background)

    return b.write(root / "master.m3u")

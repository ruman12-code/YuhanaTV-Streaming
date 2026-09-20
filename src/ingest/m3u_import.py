"""Parse an Extended M3U file into normalised Channel records.

Written against the imported source playlist, so it tolerates real-world sloppiness:
a missing #EXTM3U header, #EXTVLCOPT lines between the #EXTINF and its URL,
mathematical-bold group titles, and '(1080p)' quality suffixes in channel names.

It records what the file *claims* and never upgrades a claim into a fact:
a '(1080p)' suffix lands in `notes`, not in `resolution_label`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..models import Channel, slugify, stable_id
from ..util.urls import check_url, host_of

_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"')
_EXTINF_RE = re.compile(r"^#EXTINF\s*:\s*(-?\d+(?:\.\d+)?)\s*(.*)$", re.IGNORECASE)
# A trailing "(1080p)" / "(720p)" / "[HD]" quality claim in the display name.
_QUALITY_SUFFIX_RE = re.compile(r"\s*[\(\[]\s*(\d{3,4}p|4k|uhd|fhd|hd|sd)\s*[\)\]]\s*$", re.IGNORECASE)
_VLCOPT_RE = re.compile(r"^#EXTVLCOPT\s*:\s*([^=]+)=(.*)$", re.IGNORECASE)
# iptv-org writes tvg-id as "00sReplay.us@SD": the country follows the last dot
# and precedes an optional feed marker.
_TVG_ID_COUNTRY = re.compile(r"\.([a-zA-Z]{2})(?:@|$)")

# Map the group titles found in the imported playlist onto canonical categories.
GROUP_TO_CATEGORY = {
    "bangladesh": "bangladesh",
    "indian-bangla": "entertainment",
    "hindi": "entertainment",
    "hindi-news": "news",
    "international-news": "news",
    "news": "news",
    "sports": "sports",
    "music": "music",
    "kids": "kids",
    "animation": "kids",
    "documentary": "documentary",
    "movies": "movies",
    "series": "series",
    "comedy": "entertainment",
    "general": "entertainment",
    "entertainment": "entertainment",
    "lifestyle": "lifestyle",
    "islamic": "religious",
    "religious": "religious",
    "educational": "educational",
    "business": "business",
    "classic": "movies",
    "outdoor": "lifestyle",
    "travel": "lifestyle",
    "cooking": "lifestyle",
    "family": "kids",
    "science": "documentary",
    "culture": "documentary",
    "history": "documentary",
    "weather": "news",
    "legislative": "news",
    "auto": "lifestyle",
    "shop": "other",
}

# Country/language inference from the group the source playlist already assigned.
GROUP_TO_LOCALE = {
    "bangladesh": ("bd", "bn"),
    "indian-bangla": ("in", "bn"),
    "hindi": ("in", "hi"),
    "hindi-news": ("in", "hi"),
    "islamic": ("", ""),
}


@dataclass
class ParsedEntry:
    duration: str
    attrs: dict[str, str]
    title: str
    url: str
    vlc_opts: dict[str, str]
    line_no: int


def parse_m3u(text: str) -> tuple[list[ParsedEntry], dict[str, str], list[str]]:
    """Return (entries, header_attrs, problems)."""
    entries: list[ParsedEntry] = []
    problems: list[str] = []
    header_attrs: dict[str, str] = {}

    pending: ParsedEntry | None = None
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip().lstrip("﻿")
        if not line:
            continue

        if line.upper().startswith("#EXTM3U"):
            header_attrs = dict(_ATTR_RE.findall(line))
            continue

        m = _EXTINF_RE.match(line)
        if m:
            if pending is not None:
                problems.append(f"line {pending.line_no}: #EXTINF without a following URL, dropped")
            duration, remainder = m.group(1), m.group(2)
            attrs = dict(_ATTR_RE.findall(remainder))
            # The display title is everything after the last comma that is not
            # inside a quoted attribute value.
            title = remainder
            depth_safe = _ATTR_RE.sub("", remainder)
            if "," in depth_safe:
                idx = remainder.rfind(",", 0, len(remainder))
                # walk back to a comma that sits outside quotes
                in_quotes = False
                idx = -1
                for i, ch in enumerate(remainder):
                    if ch == '"':
                        in_quotes = not in_quotes
                    elif ch == "," and not in_quotes:
                        idx = i
                        break
                title = remainder[idx + 1:] if idx >= 0 else remainder
            pending = ParsedEntry(duration, attrs, title.strip(), "", {}, line_no)
            continue

        vm = _VLCOPT_RE.match(line)
        if vm and pending is not None:
            pending.vlc_opts[vm.group(1).strip().lower()] = vm.group(2).strip()
            continue

        if line.startswith("#"):
            continue  # any other directive is not meaningful to us

        if pending is None:
            problems.append(f"line {line_no}: URL with no preceding #EXTINF, dropped")
            continue
        pending.url = line
        entries.append(pending)
        pending = None

    if pending is not None:
        problems.append(f"line {pending.line_no}: trailing #EXTINF without a URL, dropped")
    return entries, header_attrs, problems


def _stream_type(url: str) -> str:
    low = url.lower().split("?", 1)[0]
    if ".m3u8" in low:
        return "hls"
    if ".mpd" in low:
        return "dash"
    if low.endswith(".mp4"):
        return "mp4"
    if low.endswith(".ts"):
        return "ts"
    if "/hls" in low or low.endswith("/index") or "/playlist" in low:
        return "hls"
    return "unknown"


def entry_to_channel(entry: ParsedEntry, source_id: str) -> tuple[Channel | None, str]:
    """Convert one parsed entry to a Channel, or (None, reason) if it is rejected."""
    verdict = check_url(entry.url)
    if not verdict.ok:
        return None, verdict.reason

    raw_name = entry.attrs.get("tvg-name") or entry.title or "Unnamed"
    quality_claim = ""
    m = _QUALITY_SUFFIX_RE.search(raw_name)
    if m:
        quality_claim = m.group(1).lower()
        raw_name = _QUALITY_SUFFIX_RE.sub("", raw_name).strip()

    group_slug = slugify(entry.attrs.get("group-title", ""))
    category = GROUP_TO_CATEGORY.get(group_slug, "other")
    country, language = GROUP_TO_LOCALE.get(group_slug, ("", ""))

    tvg_id = entry.attrs.get("tvg-id", "")
    if not country:
        m = _TVG_ID_COUNTRY.search(tvg_id)
        if m:
            country = m.group(1).lower()

    notes = []
    if quality_claim:
        notes.append(f"source claimed quality '{quality_claim}' (unverified)")
    if entry.vlc_opts:
        notes.append("source required custom HTTP headers")

    ch = Channel(
        id=stable_id("ch", raw_name, host_of(entry.url)),
        name=raw_name,
        country=country,
        language=language,
        category=category,
        stream_url=verdict.url,
        stream_type=_stream_type(verdict.url),
        logo=entry.attrs.get("tvg-logo", ""),
        epg_id=tvg_id.split("@", 1)[0],
        source=source_id,
        status="UNVERIFIED",
        rights_status="UNVERIFIED",
        http_referrer=entry.vlc_opts.get("http-referrer", ""),
        http_user_agent=entry.vlc_opts.get("http-user-agent", ""),
        notes="; ".join(notes),
        tags=[t for t in [group_slug] if t and t != "unknown"],
    )
    return ch, ""


def import_file(path: Path, source_id: str) -> tuple[list[Channel], dict]:
    text = path.read_text(encoding="utf-8")
    entries, header_attrs, problems = parse_m3u(text)

    channels: list[Channel] = []
    rejected: list[dict] = []
    for entry in entries:
        ch, reason = entry_to_channel(entry, source_id)
        if ch is None:
            rejected.append({"title": entry.title, "url": entry.url[:120], "reason": reason})
        else:
            channels.append(ch)

    # De-duplicate: identical stream URL, then identical (name, host).
    seen_url: dict[str, Channel] = {}
    seen_key: dict[tuple[str, str], Channel] = {}
    unique: list[Channel] = []
    duplicates: list[dict] = []
    for ch in channels:
        url_key = ch.stream_url.rstrip("/")
        name_key = (ch.name.lower(), host_of(ch.stream_url))
        if url_key in seen_url:
            duplicates.append({"kept": seen_url[url_key].name, "dropped": ch.name, "on": "url"})
            continue
        if name_key in seen_key:
            duplicates.append({"kept": seen_key[name_key].name, "dropped": ch.name, "on": "name+host"})
            continue
        seen_url[url_key] = ch
        seen_key[name_key] = ch
        unique.append(ch)

    stats = {
        "entries_parsed": len(entries),
        "channels_accepted": len(unique),
        "rejected": rejected,
        "duplicates": duplicates,
        "parse_problems": problems,
        "header_attrs": header_attrs,
        "needs_custom_headers": sum(1 for c in unique if c.requires_custom_headers),
    }
    return unique, stats

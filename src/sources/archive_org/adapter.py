"""Internet Archive movie adapter.

Chosen as the first real VOD source because it is the rare case where all three
things we need are simultaneously true: a public catalogue with metadata, direct
byte-range-capable playback URLs, and per-item licence metadata we can read
rather than assume.

Rights handling is the whole point of this module. An item is marked CLEARED
only when its own metadata declares a public-domain or Creative Commons licence,
or it belongs to a collection curated for that. Everything else lands as
DISCOVERABLE and can never reach a VOD playlist — see Movie.publishable_as_vod.
Nothing here infers permission from the fact that a file happens to be reachable.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ...models import Movie, stable_id, slugify
from ...util.urls import check_url
from ...content_policy import adult_reason
from ..imdb.datasets import clean_source_title

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata/"
DOWNLOAD_URL = "https://archive.org/download/"

# Licence URLs that state redistribution is permitted.
_PD_LICENCE = re.compile(
    r"creativecommons\.org/(publicdomain|licenses/(by|by-sa|by-nd|by-nc)?)", re.I)
_PD_RIGHTS_TEXT = re.compile(r"public\s*domain|no known copyright|cc0", re.I)

# Collections curated as public domain / openly licensed.
PD_COLLECTIONS = {
    "publicmoviescollection", "prelinger", "feature_films", "classic_cartoons",
    "film_noir", "SciFi_Horror", "more_animation", "animationandcartoons",
    "short_films", "publicdomainmovies",
    # Works of the US federal government are not subject to copyright (17 USC 105),
    # so NASA's own films are public domain whether or not the item says so.
    "nasa", "nasaimages",
}

# Creators whose entire output is released under a known open licence.
PD_CREATORS = {"blender foundation"}

# What a VIDAA television will actually decode. Selecting purely by resolution
# filled the library with Ogg Theora (.ogv) files that the TV cannot play at all:
# high resolution is worthless if the codec is unsupported. Playability is now the
# first gate and resolution decides only among files that pass it.
_PLAYABLE_EXT = (".mp4", ".m4v")
_PLAYABLE_FORMAT = re.compile(r"h\.?264|mpeg-?4|mp4|avc", re.IGNORECASE)
# Formats deliberately refused: Ogg Theora, WebM/VP8/VP9, MPEG-2 programme
# streams and raw AVI. Support for these across VIDAA builds is absent or erratic.
_REFUSED_EXT = (".ogv", ".ogg", ".webm", ".avi", ".mpg", ".mpeg", ".mkv", ".flv", ".wmv")

# Above this a file stalls on a TV over a home connection, and is usually a
# preservation master rather than a viewing copy.
_MAX_SENSIBLE_BYTES = 2_500 * 1024 * 1024

_YEAR_RE = re.compile(r"(1[89]\d{2}|20\d{2})")
_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?$")


def _parse_runtime(raw: str) -> int | None:
    """Minutes from an Archive `runtime` value.

    The field is a clock, and a two-part value is MM:SS, not HH:MM. Reading
    "6:12" as six hours twelve minutes turned a six-minute clip into 372
    minutes, which then appeared on the tile.
    """
    raw = raw.strip()
    if not raw:
        return None
    m = _CLOCK_RE.match(raw)
    if m:
        hours = int(m.group(1)) if m.group(1) else 0
        minutes, seconds = int(m.group(2)), int(m.group(3))
        total = hours * 60 + minutes + (1 if seconds >= 30 else 0)
        return total or None
    if raw.isdigit():                       # bare seconds
        return (int(raw) + 30) // 60 or None
    return None


@dataclass
class RightsVerdict:
    status: str          # CLEARED | UNVERIFIED
    reason: str
    rule: str = ""       # licence | rights_text | collection | none


def classify_rights(meta: dict) -> RightsVerdict:
    licence = str(meta.get("licenseurl") or "")
    if _PD_LICENCE.search(licence):
        return RightsVerdict("CLEARED", f"declared licence {licence}", "licence")

    rights_text = " ".join(
        str(meta.get(k) or "") for k in ("rights", "usage", "possible-copyright-status"))
    if _PD_RIGHTS_TEXT.search(rights_text):
        return RightsVerdict("CLEARED", f"rights metadata states: {rights_text[:80]}",
                             "rights_text")

    collections = meta.get("collection") or []
    if isinstance(collections, str):
        collections = [collections]
    hit = next((c for c in collections if c in PD_COLLECTIONS), None)
    if hit:
        return RightsVerdict("CLEARED",
                             f"member of curated public-domain collection '{hit}'",
                             "collection")

    creator = str(meta.get("creator") or "").strip().lower()
    if creator in PD_CREATORS:
        return RightsVerdict("CLEARED",
                             f"creator '{creator}' releases its films under an open licence",
                             "creator")

    return RightsVerdict("UNVERIFIED",
                         "no licence or public-domain statement in item metadata", "none")


def _as_int(value) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def is_tv_playable(name: str, fmt: str) -> bool:
    """Whether a TV can be expected to decode this file at all."""
    low = (name or "").lower()
    if low.endswith(_REFUSED_EXT):
        return False
    if not low.endswith(_PLAYABLE_EXT):
        return False
    # An .mp4 container can still hold something exotic; require the format label
    # to name an h.264/MPEG-4 family codec.
    return bool(_PLAYABLE_FORMAT.search(fmt or ""))


def _pick_video_file(files: list[dict]) -> dict | None:
    """Highest resolution *among files the TV can actually play*.

    Two gates in order. Playability first: an Ogg Theora file at 1080p is not a
    better choice than an h.264 file at 480p, it is an unplayable one, and
    ranking purely by height put 160 of them into the library. Resolution then
    decides among the survivors.
    """
    videos = []
    for f in files:
        size = _as_int(f.get("size"))
        if size <= 0 or size > _MAX_SENSIBLE_BYTES:
            continue
        if not is_tv_playable(str(f.get("name", "")), str(f.get("format", ""))):
            continue
        videos.append(f)
    if not videos:
        return None

    # Highest resolution, then size as a proxy where height is not declared.
    videos.sort(key=lambda f: (-_as_int(f.get("height")), -_as_int(f.get("size"))))
    return videos[0]


def resolution_label(width: int, height: int) -> str:
    """Marketing label derived ONLY from measured pixels."""
    if height >= 2000 or width >= 3600:
        return "4K"
    if height >= 1000:
        return "1080p"
    if height >= 700:
        return "720p"
    if height >= 560:
        return "576p"
    if height >= 400:
        return "480p"
    if height > 0:
        return f"{height}p"
    return ""


def _pick_poster(identifier: str, files: list[dict]) -> str:
    """A small thumbnail, never full artwork: SS IPTV runs on a TV's memory."""
    for f in files:
        name = str(f.get("name", ""))
        if name.lower().endswith((".jpg", ".jpeg", ".png")) and "thumb" in name.lower():
            return f"{DOWNLOAD_URL}{identifier}/{urllib.parse.quote(name)}"
    # Archive.org's own services thumbnail is already TV-sized.
    return f"https://archive.org/services/img/{urllib.parse.quote(identifier)}"


class ArchiveOrgAdapter:
    def __init__(self, cfg, *, timeout: float = 20.0) -> None:
        self.cfg = cfg
        self.timeout = timeout
        self.user_agent = cfg.get_path("validation.user_agent", "Mozilla/5.0")
        self.min_year = int(cfg.get_path("movies.min_year", 1920))

    def _get_json(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read(8 * 1024 * 1024).decode("utf-8", "replace"))

    def search(self, query: str, rows: int = 100, page: int = 1) -> list[dict]:
        params = [
            ("q", query), ("rows", str(rows)), ("page", str(page)),
            ("output", "json"), ("sort[]", "downloads desc"),
        ]
        for field in ("identifier", "title", "year", "description", "subject",
                      "licenseurl", "downloads", "collection", "language", "runtime"):
            params.append(("fl[]", field))
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
        payload = self._get_json(url)
        return payload.get("response", {}).get("docs", [])

    def metadata(self, identifier: str) -> dict:
        return self._get_json(f"{METADATA_URL}{urllib.parse.quote(identifier)}")

    # --- conversion ----------------------------------------------------------

    def to_movie(self, identifier: str, payload: dict) -> tuple[Movie | None, str]:
        meta = payload.get("metadata") or {}
        files = payload.get("files") or []

        video = _pick_video_file(files)
        if video is None:
            return None, "no TV-playable (h.264/MP4) derivative in the item"

        raw_title = str(meta.get("title") or identifier).strip()
        title, embedded_year = clean_source_title(raw_title)
        title = title or raw_title
        subjects_raw = meta.get("subject") or []
        if isinstance(subjects_raw, str):
            subjects_raw = [subjects_raw]
        reason = adult_reason(title, " ".join(str(s) for s in subjects_raw),
                              str(meta.get("description") or ""))
        if reason:
            return None, f"adult material (matched '{reason}')"

        rights = classify_rights(meta)
        # `publicdate` is when the file was uploaded to the Archive, not when the
        # film was made: using it dates 1940s footage to whenever someone scanned
        # it. Only fields that describe the work itself are consulted.
        year = None
        for candidate in (meta.get("year"), meta.get("date")):
            m = _YEAR_RE.search(str(candidate or ""))
            if m:
                year = int(m.group(1))
                break
        # A year the uploader put in the title is about the work itself, so it
        # beats a date field that may describe the scan.
        if embedded_year:
            year = embedded_year
        if year and year < self.min_year:
            return None, f"year {year} is before the configured minimum"

        subjects = meta.get("subject") or []
        if isinstance(subjects, str):
            subjects = [s.strip() for s in re.split(r"[;,]", subjects) if s.strip()]

        language = str(meta.get("language") or "").strip().lower()
        if language in ("eng", "english", "en"):
            language = "english"

        playback_url = (f"{DOWNLOAD_URL}{urllib.parse.quote(identifier)}/"
                        f"{urllib.parse.quote(str(video['name']))}")
        verdict = check_url(playback_url)
        if not verdict.ok:
            return None, f"playback url rejected: {verdict.reason}"

        runtime = _parse_runtime(str(meta.get("runtime") or ""))

        synopsis = re.sub(r"<[^>]+>", " ", str(meta.get("description") or ""))
        synopsis = " ".join(synopsis.split())

        movie = Movie(
            id=stable_id("mv", "archive.org", identifier),
            title=title,
            year=year,
            genre=[s for s in subjects][:6],
            language=language,
            synopsis=synopsis[:600],
            poster=_pick_poster(identifier, files),
            runtime_minutes=runtime,
            rating=None,          # archive.org carries no IMDb rating; see Phase 5
            source="src-archive-org",
            source_url=f"https://archive.org/details/{urllib.parse.quote(identifier)}",
            # PLAYABLE is only asserted after the VOD validator has fetched bytes.
            playback_status="UNVERIFIED",
            playback_url=playback_url,
            file_size_bytes=_as_int(video.get("size")),
            width=_as_int(video.get("width")),
            height=_as_int(video.get("height")),
            resolution_label=resolution_label(_as_int(video.get("width")),
                                              _as_int(video.get("height"))),
            rights_status=rights.status,
            notes=f"rights[{rights.rule}]: {rights.reason}; file: {video.get('format')} "
                  f"{_as_int(video.get('size')) // (1024*1024)} MB"
                  + (f" {_as_int(video.get('width'))}x{_as_int(video.get('height'))}"
                     if _as_int(video.get('height')) else " (dimensions not declared)"),
        )
        return movie, ""

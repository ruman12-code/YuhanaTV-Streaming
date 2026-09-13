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
}

# Preferred derivative formats, best first. Archive.org's 512Kb MPEG4 derivative
# is the right trade-off for a TV: h.264, modest bitrate, always range-served.
_FORMAT_PREFERENCE = (
    "512Kb MPEG4", "MPEG4", "h.264", "H.264", "HiRes MPEG4", "512Kb MPEG4 Video",
)
_VIDEO_EXT = (".mp4", ".m4v", ".webm", ".ogv")

_YEAR_RE = re.compile(r"(1[89]\d{2}|20\d{2})")


@dataclass
class RightsVerdict:
    status: str          # CLEARED | UNVERIFIED
    reason: str


def classify_rights(meta: dict) -> RightsVerdict:
    licence = str(meta.get("licenseurl") or "")
    if _PD_LICENCE.search(licence):
        return RightsVerdict("CLEARED", f"declared licence {licence}")

    rights_text = " ".join(
        str(meta.get(k) or "") for k in ("rights", "usage", "possible-copyright-status"))
    if _PD_RIGHTS_TEXT.search(rights_text):
        return RightsVerdict("CLEARED", f"rights metadata states: {rights_text[:80]}")

    collections = meta.get("collection") or []
    if isinstance(collections, str):
        collections = [collections]
    hit = next((c for c in collections if c in PD_COLLECTIONS), None)
    if hit:
        return RightsVerdict("CLEARED", f"member of curated public-domain collection '{hit}'")

    return RightsVerdict("UNVERIFIED",
                         "no licence or public-domain statement in item metadata")


def _pick_video_file(files: list[dict]) -> dict | None:
    """Choose the derivative a TV should play."""
    videos = [
        f for f in files
        if str(f.get("name", "")).lower().endswith(_VIDEO_EXT)
        and int(f.get("size") or 0) > 0
    ]
    if not videos:
        return None
    def rank(f):
        fmt = str(f.get("format", ""))
        try:
            pref = _FORMAT_PREFERENCE.index(fmt)
        except ValueError:
            pref = len(_FORMAT_PREFERENCE)
        return (pref, -int(f.get("size") or 0))
    videos.sort(key=rank)
    return videos[0]


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
            return None, "no playable video derivative in the item"

        rights = classify_rights(meta)

        title = str(meta.get("title") or identifier).strip()
        year = None
        for candidate in (meta.get("year"), meta.get("date"), meta.get("publicdate")):
            m = _YEAR_RE.search(str(candidate or ""))
            if m:
                year = int(m.group(1))
                break
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

        runtime = None
        raw_runtime = str(meta.get("runtime") or "")
        rm = re.match(r"^(\d+):(\d{2})", raw_runtime)
        if rm:
            runtime = int(rm.group(1)) * 60 + int(rm.group(2))
        elif raw_runtime.isdigit():
            runtime = int(raw_runtime) // 60 or None

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
            rights_status=rights.status,
            notes=f"rights: {rights.reason}; file: {video.get('format')} "
                  f"{int(video.get('size') or 0) // (1024*1024)} MB",
        )
        return movie, ""

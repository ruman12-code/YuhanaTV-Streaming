"""Normalised data model (spec section 30).

Deliberately plain dataclasses over JSON. No ORM, no external schema library:
the pipeline runs in CI where every dependency is a failure mode.

Three status axes are kept strictly separate, because conflating them is how
playlists end up lying to the user:

  * `status`        - does the stream currently respond? (set by the validator)
  * `rights_status` - are we permitted to redistribute it? (set by a human)
  * `playback_status` (movies) - do we hold a direct playable URL at all?
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any, Iterable

# --- controlled vocabularies -------------------------------------------------

STREAM_STATUS = ("ACTIVE", "DEGRADED", "OFFLINE", "INVALID", "UNVERIFIED")
RIGHTS_STATUS = ("CLEARED", "UNVERIFIED", "RESTRICTED", "EXCLUDED")
PLAYBACK_STATUS = ("PLAYABLE", "DISCOVERABLE", "UNVERIFIED", "EXCLUDED")
STREAM_TYPES = ("hls", "dash", "mp4", "ts", "unknown")

# Canonical live categories (spec section 6).
LIVE_CATEGORIES = (
    "bangladesh", "news", "sports", "entertainment", "movies", "series", "music",
    "kids", "documentary", "educational", "business", "lifestyle",
    "religious", "international", "other",
)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """ASCII slug. Also folds the mathematical-bold letters used as group titles
    in the imported playlist (e.g. '𝐁𝐚𝐧𝐠𝐥𝐚𝐝𝐞𝐬𝐡' -> 'bangladesh')."""
    folded = unicodedata.normalize("NFKC", value or "")
    folded = folded.encode("ascii", "ignore").decode("ascii")
    return _SLUG_STRIP.sub("-", folded.lower()).strip("-") or "unknown"


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("\x1f".join(p or "" for p in parts).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFKC", value).strip()
    return value


@dataclass
class Channel:
    id: str
    name: str
    country: str = ""            # ISO-3166 alpha-2, lower-case ("bd", "in", "qa")
    language: str = ""           # ISO-639-1 ("bn", "en", "hi")
    category: str = "other"      # one of LIVE_CATEGORIES
    stream_url: str = ""
    stream_type: str = "unknown"  # STREAM_TYPES
    resolution: str = ""          # "1920x1080" or "" - never a marketing label
    resolution_label: str = ""    # "1080p"/"720p" - ONLY set from measured resolution
    codec: str = ""
    bitrate_bps: int = 0
    logo: str = ""
    epg_id: str = ""
    source: str = ""              # Source.id
    status: str = "UNVERIFIED"    # STREAM_STATUS
    rights_status: str = "UNVERIFIED"   # RIGHTS_STATUS
    last_verified: str = ""       # ISO-8601 UTC, or "" if never verified
    # Playback hints some origins require. Recorded because they are part of the
    # stream's identity, but see docs/LIMITATIONS.md: SS IPTV cannot send them.
    http_referrer: str = ""
    http_user_agent: str = ""
    consecutive_failures: int = 0
    # Reliability history, accumulated across validation runs. A channel that
    # passes once and fails four times is not the same asset as one that always
    # passes, and the difference has to survive into the published playlist.
    checks_total: int = 0
    checks_ok: int = 0
    first_seen: str = ""
    last_status_change: str = ""
    notes: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for f in fields(self):
            setattr(self, f.name, _clean(getattr(self, f.name)))
        if self.category not in LIVE_CATEGORIES:
            self.category = "other"
        if self.status not in STREAM_STATUS:
            self.status = "UNVERIFIED"
        if self.rights_status not in RIGHTS_STATUS:
            self.rights_status = "UNVERIFIED"
        if self.stream_type not in STREAM_TYPES:
            self.stream_type = "unknown"

    @property
    def requires_custom_headers(self) -> bool:
        return bool(self.http_referrer or self.http_user_agent)

    @property
    def reliability(self) -> float:
        """Fraction of validation runs in which this channel was usable (0.0-1.0).

        Returns 0.0 when never checked; callers must distinguish that from a
        measured 0.0 using `checks_total`.
        """
        return (self.checks_ok / self.checks_total) if self.checks_total else 0.0

    @property
    def is_flapping(self) -> bool:
        """Intermittent rather than reliably up or reliably down."""
        return self.checks_total >= 4 and 0.25 <= self.reliability <= 0.75

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Channel":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class Movie:
    id: str
    title: str
    year: int | None = None
    genre: list[str] = field(default_factory=list)
    language: str = ""
    synopsis: str = ""
    poster: str = ""
    backdrop: str = ""
    runtime_minutes: int | None = None
    rating: float | None = None          # IMDb-style 0-10
    source: str = ""
    source_url: str = ""
    playback_status: str = "UNVERIFIED"  # PLAYBACK_STATUS
    playback_url: str = ""
    file_size_bytes: int = 0
    width: int = 0
    height: int = 0
    resolution_label: str = ""     # only ever set from measured pixels
    rights_status: str = "UNVERIFIED"    # RIGHTS_STATUS
    last_verified: str = ""
    imdb_id: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        for f in fields(self):
            setattr(self, f.name, _clean(getattr(self, f.name)))
        if self.playback_status not in PLAYBACK_STATUS:
            self.playback_status = "UNVERIFIED"
        if self.rights_status not in RIGHTS_STATUS:
            self.rights_status = "UNVERIFIED"

    @property
    def publishable_as_vod(self) -> bool:
        """A movie enters a VOD M3U only when BOTH gates are green (spec 11/28)."""
        return (
            self.playback_status == "PLAYABLE"
            and self.rights_status == "CLEARED"
            and bool(self.playback_url)
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Movie":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class Source:
    id: str
    name: str
    url: str = ""
    type: str = "m3u"            # m3u | api | scrape | manual
    authorization_status: str = "UNVERIFIED"   # UNVERIFIED | AUTHORISED | DENIED
    redistribution_allowed: bool = False
    trust_tier: str = "unknown"  # see docs/ARCHITECTURE.md - drives publish gating
    last_checked: str = ""
    status: str = "UNKNOWN"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Source":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


# --- persistence -------------------------------------------------------------

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_channels(channels_dir: Path) -> list[Channel]:
    out: list[Channel] = []
    for file in sorted(channels_dir.glob("*.json")):
        payload = read_json(file, {})
        for raw in payload.get("channels", []):
            out.append(Channel.from_dict(raw))
    return out


def save_channels(channels_dir: Path, channels: Iterable[Channel]) -> dict[str, int]:
    """Group channels by category, one JSON file per category."""
    buckets: dict[str, list[Channel]] = {}
    for ch in channels:
        buckets.setdefault(ch.category, []).append(ch)
    for existing in channels_dir.glob("*.json"):
        existing.unlink()
    counts = {}
    for category, items in sorted(buckets.items()):
        items.sort(key=lambda c: c.name.lower())
        write_json(
            channels_dir / f"{category}.json",
            {"category": category, "count": len(items),
             "channels": [c.to_dict() for c in items]},
        )
        counts[category] = len(items)
    return counts

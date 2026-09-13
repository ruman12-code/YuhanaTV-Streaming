"""Map our channels onto canonical XMLTV ids from the iptv-org open database.

Why this matters even before any programme data exists: SS IPTV supplies guide
information of its own for channels it recognises, and recognition happens through
`tvg-id`. An invented id matches nothing. A canonical one — `SomoyTV.bd`,
`AlJazeeraEnglish.qa` — is what lets the app fill in a guide we never had to host,
which is exactly what the specification asks for when it says not to load EPG for
channels the app already covers.

Matching is name-and-country based and deliberately conservative: an id attached
to the wrong channel produces a guide showing the wrong programmes, which is
worse than showing none.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
from dataclasses import dataclass

CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
GUIDES_URL = "https://iptv-org.github.io/api/guides.json"

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")

# Quality markers are never part of a channel's identity, so they always go.
_QUALITY = {"hd", "fhd", "uhd", "sd", "4k", "8k", "1080p", "1080i", "720p",
            "576p", "480p", "360p"}
# Generic words that one source includes and another omits. Stripping them helps
# ("Somoy TV" vs "Somoy") but destroys short names outright ("Channel i" -> "i"),
# so both forms are produced and the strict one is tried first.
_GENERIC = {"tv", "television", "channel", "network", "live"}


def normalise_name(name: str) -> str:
    """Strict key: quality markers removed, everything else kept."""
    n = unicodedata.normalize("NFKC", name or "")
    n = n.encode("ascii", "ignore").decode("ascii").lower()
    n = _PUNCT.sub(" ", n)
    tokens = [t for t in _WS.sub(" ", n).split() if t and t not in _QUALITY]
    return " ".join(tokens)


def loose_name(name: str) -> str:
    """Loose key: generic words removed too, but never down to nothing.

    'Channel i' keeps both tokens because dropping 'channel' would leave a
    single letter that matches almost anything.
    """
    tokens = normalise_name(name).split()
    stripped = [t for t in tokens if t not in _GENERIC]
    return " ".join(stripped) if len(stripped) >= 2 else " ".join(tokens)


@dataclass
class CanonicalChannel:
    id: str
    name: str
    country: str
    logo: str = ""
    alt_names: tuple[str, ...] = ()
    closed: bool = False


def fetch_json(url: str, *, user_agent: str, timeout: float = 60.0):
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(64 * 1024 * 1024).decode("utf-8", "replace"))


def load_catalog(rows) -> dict[tuple[str, str], list[CanonicalChannel]]:
    """Index the database by (normalised name, lower-case country)."""
    index: dict[tuple[str, str], list[CanonicalChannel]] = {}
    for row in rows:
        cid = row.get("id")
        if not cid:
            continue
        country = str(row.get("country") or "").lower()
        chan = CanonicalChannel(
            id=cid,
            name=str(row.get("name") or ""),
            country=country,
            logo=str(row.get("logo") or ""),
            alt_names=tuple(row.get("alt_names") or ()),
            closed=bool(row.get("closed")),
        )
        for candidate in (chan.name, *chan.alt_names):
            for key_fn in (normalise_name, loose_name):
                key = (key_fn(candidate), country)
                if key[0] and chan not in index.setdefault(key, []):
                    index[key].append(chan)
    return index


def match_channel(channel, index) -> CanonicalChannel | None:
    """Find the canonical entry for one of our channels, or None.

    Country must agree when we know ours: 'Somoy TV' in Bangladesh and a
    same-named channel elsewhere are different services, and guessing between
    them would attach the wrong schedule.
    """
    strict = normalise_name(channel.name)
    loose = loose_name(channel.name)
    if not strict:
        return None

    country = (channel.country or "").lower()
    candidates: list[CanonicalChannel] = []
    if country:
        for key in ((strict, country), (loose, country)):
            candidates = list(index.get(key, []))
            if candidates:
                break
    else:
        # No country recorded on our side: accept only an unambiguous global match,
        # because the same name in two countries is two different services.
        for form in (strict, loose):
            hits = [c for (n, _), entries in index.items() if n == form for c in entries]
            if {c.id for c in hits} and len({c.id for c in hits}) == 1:
                candidates = hits
                break

    live = [c for c in candidates if not c.closed]
    pool = live or candidates
    if not pool:
        return None
    # Prefer an exact display-name match over an alias hit.
    pool.sort(key=lambda c: (normalise_name(c.name) != strict, c.id))
    return pool[0]


def guide_coverage(guides_rows, wanted_ids: set[str]) -> dict[str, list[dict]]:
    """Which of our canonical ids any public guide source actually covers."""
    coverage: dict[str, list[dict]] = {}
    for row in guides_rows:
        cid = row.get("channel")
        if cid in wanted_ids:
            coverage.setdefault(cid, []).append({
                "site": row.get("site"),
                "lang": row.get("lang"),
                "site_id": row.get("site_id"),
            })
    return coverage

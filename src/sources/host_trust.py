"""Classify a stream host into a provenance tier.

This is evidence about *where a stream is served from*, not a licensing opinion.
A `fast_platform` match means the host belongs to a free ad-supported streaming
platform that distributes channels under its own agreements; `aggregator` and
`unknown` mean we cannot see the rights position from here, which is exactly the
thing a human has to decide before anything is redistributed further.
"""

from __future__ import annotations

import re

# Free ad-supported streaming TV platforms and their CDNs.
FAST_PATTERNS = (
    r"\.amagi\.tv$", r"amagi\.tv$", r"\.wurl\.tv$", r"\.frequency\.stream$",
    r"samsungtv\.plus$", r"\.samsungtvplus\.", r"tubi\.video$", r"\.pluto\.tv$",
    r"stitcher\.pluto\.tv$", r"\.rakuten\.tv$", r"xumo", r"\.plex\.tv$",
    r"\.roku\.com$", r"\.crackle\.com$", r"cineverse", r"\.tsv2\.amagi\.tv$",
)

# Broadcaster-operated origins (the rights holder serving its own signal).
BROADCASTER_PATTERNS = (
    r"\.pbs\.org$", r"getaj\.net$", r"aljazeera", r"\.dw\.com$", r"\.france24\.com$",
    r"\.bbc\.co\.uk$", r"cctvplus\.com$", r"\.nhk\.or\.jp$", r"\.trt\.", r"\.rt\.com$",
    r"herringnetwork\.com$", r"\.euronews\.com$", r"\.abc\.net\.au$",
)

# Generic CDNs: the CDN itself says nothing about provenance.
CDN_PATTERNS = (
    r"cloudfront\.net$", r"akamaized\.net$", r"\.akamai", r"mediapackage\..*\.amazonaws\.com$",
    r"mediatailor\..*\.amazonaws\.com$", r"\.wowza\.com$", r"\.5centscdn\.com$",
    r"\.gpcdn\.net$", r"\.streamhoster\.com$", r"\.kwikmotion\.com$", r"\.cdn01\.net$",
)

_IP_LITERAL = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def classify(host: str) -> tuple[str, str]:
    """Return (trust_tier, reason)."""
    h = (host or "").lower().strip()
    if not h:
        return "unknown", "no host"
    for pat in BROADCASTER_PATTERNS:
        if re.search(pat, h):
            return "broadcaster_official", f"host matches broadcaster pattern /{pat}/"
    for pat in FAST_PATTERNS:
        if re.search(pat, h):
            return "fast_platform", f"host matches FAST-platform pattern /{pat}/"
    if _IP_LITERAL.match(h.split(":")[0]):
        return "unknown", "bare IP address origin: no domain, no identifiable operator"
    for pat in CDN_PATTERNS:
        if re.search(pat, h):
            return "aggregator", f"generic CDN /{pat}/: operator not identifiable from the host"
    return "unknown", "host not recognised"

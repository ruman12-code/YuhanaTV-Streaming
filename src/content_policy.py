"""Content suitability screening.

This library is browsed on a family television and carries a Kids category, so
adult material must not reach it. The Internet Archive is an open repository and
does host such items, several of them tagged as animation or cartoon, which is
precisely where a child would find them.

Screening is keyword-based over a title, its subject tags and its description.
That is blunt, and blunt is the right bias here: a wrongly excluded public-domain
film costs one entry out of hundreds, while a wrongly included one is on the
family TV under Animation.

IMDb's `isAdult` flag is applied separately during enrichment as a second gate.
"""

from __future__ import annotations

import re

# Matched as whole words against title, subjects and description.
_ADULT_TERMS = (
    r"porn\w*", r"xxx", r"erotic\w*", r"hardcore", r"softcore", r"nudie",
    r"nudist", r"nudity", r"striptease", r"stripper", r"burlesque",
    r"sexploitation", r"grindhouse", r"adults?[\s-]only", r"x[\s-]rated",
    r"blue movie", r"stag film", r"stag reel", r"smut", r"fetish",
    r"bdsm", r"orgy", r"orgies", r"brothel", r"prostitut\w*",
)
_ADULT_RE = re.compile(r"(?<![\w])(?:" + "|".join(_ADULT_TERMS) + r")(?![\w])", re.IGNORECASE)

# Phrases that look adult but are ordinary in film metadata.
_ALLOWLIST_RE = re.compile(
    r"\b(sex(ual)? education|sex hygiene|social hygiene|anti[\s-]?prostitution)\b",
    re.IGNORECASE)


def adult_reason(*fields: str) -> str:
    """Return the matched term if anything reads as adult material, else ''."""
    haystack = " ".join(f for f in fields if f)
    if not haystack:
        return ""
    if _ALLOWLIST_RE.search(haystack):
        return ""
    match = _ADULT_RE.search(haystack)
    return match.group(0).lower() if match else ""


def is_adult(*fields: str) -> bool:
    return bool(adult_reason(*fields))

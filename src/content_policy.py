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


# Live-TV screening needs more than the keyword list above. An adult channel is
# usually named after its brand, which contains none of those words, and the
# aggregators label the whole category rather than the channel. Both are checked
# because either alone misses: iptv-org's group is "XXX", while a channel picked
# up from an uncategorised index carries only its brand name.
_ADULT_GROUPS = frozenset({
    "xxx", "adult", "adults", "porn", "erotic", "erotica", "18", "18plus",
    "for-adults", "adult-channels",
})

# Brands whose entire output is pornographic. Matched as whole words against the
# channel name. Kept deliberately short and specific: every entry here is a
# channel that exists in public aggregator lists, not a guess.
_ADULT_BRANDS = (
    r"brazzers", r"penthouse", r"playboy", r"hustler", r"vivid(?:\s?tv)?",
    r"dorcel", r"private\s?tv", r"redlight", r"red\s?light\s?hd",
    r"sextreme", r"sexy\s?hot", r"venus\s?tv", r"blue\s?hustler",
    r"dusk\s?tv", r"passion\s?(?:tv|xxx)", r"eroxxx", r"leo\s?tv",
    r"barely\s?legal", r"naked\s?news", r"babes?\s?tv", r"pink\s?erotic",
    r"french\s?lover", r"hot\s?xxx", r"extasy\s?tv", r"sct\s?erotic",
    r"o[\s-]?la[\s-]?la", r"visit[\s-]?x", r"daring\s?tv", r"sexstation",
)
_ADULT_BRAND_RE = re.compile(
    r"(?<![\w])(?:" + "|".join(_ADULT_BRANDS) + r")(?![\w])", re.IGNORECASE)


def adult_channel_reason(name: str, group_slug: str = "", tags=()) -> str:
    """Return why a LIVE channel reads as adult, or '' if it does not.

    Three independent signals, because no one of them is sufficient:
      * the aggregator's own category (iptv-org files these under "XXX"),
      * a known pornographic brand in the channel name,
      * the generic keyword list used for the film library.
    """
    # Group titles are compound in the aggregator feeds ("Movies;Series" comes
    # through as "movies-series"), so every token is checked, not the whole slug.
    slug = (group_slug or "").strip().lower()
    for token in [slug] + slug.split("-"):
        if token in _ADULT_GROUPS:
            return f"category '{token}'"
    for t in tags or ():
        if str(t).strip().lower() in _ADULT_GROUPS:
            return f"tag '{t}'"
    brand = _ADULT_BRAND_RE.search(name or "")
    if brand:
        return f"brand '{brand.group(0).lower()}'"
    return adult_reason(name)


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

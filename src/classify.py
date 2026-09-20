"""Decide what a channel actually is, from its name and its source's group title.

Two problems this solves.

A source's `group-title` is often wrong or too coarse. Zee Cinema, Star Gold and
Sony Max arrived tagged "Entertainment"; they are movie channels and belong with
the movie channels. A viewer looking for a film should not have to know that a
particular aggregator filed it under Entertainment.

And iptv-org writes compound group titles — `classic-movies`, `animation-kids`,
`movies-series`, `news-public`. None of those matched a single-value lookup, so
185 channels fell into "other", which is the bucket nobody browses.

Name evidence wins over group evidence: the broadcaster's own name is the most
reliable signal available, and a list's tagging is the least.
"""

from __future__ import annotations

import re

# Ordered: the first pattern that matches decides. More specific first, so
# "Disney Junior" is kids rather than being caught by a general entertainment rule.
NAME_RULES: tuple[tuple[str, str], ...] = (
    ("kids", r"\b(kids?|cartoon(s|ito)?|junior|jr\.?|nick(elodeon|jr|toons)?|"
             r"disney|baby|toon(ami|s)?|boomerang|pogo|chutti|cbeebies|"
             r"pbs kids|duck tv|babytv|kidz|nursery|peppa)\b"),
    ("sports", r"\b(sports?|espn|willow|supersport|eurosport|fight ?sports|ufc|"
               r"golf|racing|motogp|formula ?1|f1 ?tv|cricket|football|soccer|"
               r"nba|nfl|nhl|mlb|tennis|olympic|dazn|astro (supersport|arena)|"
               r"star sports|ten sports|sky sports|bein)\b"),
    ("news", r"\b(news|24x7|24/7 news|aaj tak|ndtv|republic|times ?now|"
             r"al ?jazeera|cnn|bbc (world|news)|dw|france ?24|euronews|sky news|"
             r"newsmax|cgtn|rt news|trt world|abp|india today|wion|noticias)\b"),
    ("movies", r"\b(cinema|cinemax|movies?|movie ?club|film(s|x|box|rise)?|"
               r"pictures|hbo|star gold|sony max|zee (cinema|action|classic|bollywood)|"
               r"&pictures|utv (movies|action)|bolly(wood)?|kino|cine(ma|plex|vault)?|"
               r"screen|showtime|mgm|paramount|sony pix|bflix|talkies)\b"),
    ("music", r"\b(music|mtv|vh1|9xm|mastiii|b4u music|zing|channel ?\[?v\]?|"
              r"kiss ?tv|trace|stingray|vevo|clubbing|hits)\b"),
    ("documentary", r"\b(documentar(y|ies)|discovery|nat ?geo|national geographic|"
                    r"history|animal planet|curiosity|smithsonian|pbs|"
                    r"love nature|viasat (explore|history|nature))\b"),
    ("religious", r"\b(islam(ic)?|quran|peace tv|madani|church|gospel|christian|"
                  r"bible|ewtn|god ?tv|hillsong|bhakti|aastha|sanskar)\b"),
    ("series", r"\b(series|sitcom|drama ?channel|telenovela|soap)\b"),
)
_COMPILED = tuple((cat, re.compile(pat, re.IGNORECASE)) for cat, pat in NAME_RULES)

# When a group title names several things, the most specific wins.
# "classic" is deliberately absent: it modifies whatever it sits beside rather
# than naming a category of its own, so "classic-series" is a series channel and
# "classic-movies" a film one. It still resolves to movies when it stands alone,
# via the fallback scan below.
GROUP_TOKEN_PRIORITY = (
    "kids", "animation", "sports", "movies", "series",
    "news", "music", "documentary", "culture", "religious", "lifestyle",
    "business", "comedy", "entertainment", "family", "general", "public",
)

TOKEN_TO_CATEGORY = {
    "kids": "kids", "animation": "kids", "family": "kids",
    "sports": "sports",
    "movies": "movies", "classic": "movies",
    "series": "series",
    "news": "news", "public": "news", "legislative": "news", "weather": "news",
    "music": "music",
    "documentary": "documentary", "culture": "documentary", "science": "documentary",
    "history": "documentary", "education": "documentary",
    "religious": "religious",
    "lifestyle": "lifestyle", "travel": "lifestyle", "cooking": "lifestyle",
    "outdoor": "lifestyle", "auto": "lifestyle",
    "business": "business",
    "comedy": "entertainment", "entertainment": "entertainment", "general": "entertainment",
    "shop": "other",
}

# iptv-org annotates the display name; these are facts about playability, not decoration.
GEO_BLOCKED_RE = re.compile(r"\[\s*geo[\s-]?blocked\s*\]", re.IGNORECASE)
NOT_24_7_RE = re.compile(r"\[\s*not\s*24/7\s*\]", re.IGNORECASE)
_NAME_ANNOTATION = re.compile(
    r"\s*[\(\[]\s*(?:\d{3,4}[pi]|4k|uhd|geo[\s-]?blocked|not\s*24/7|"
    r"timeshift[^\)\]]*|\d+\s*h)\s*[\)\]]", re.IGNORECASE)


def is_geo_blocked(name: str) -> bool:
    return bool(GEO_BLOCKED_RE.search(name or ""))


def is_not_24_7(name: str) -> bool:
    return bool(NOT_24_7_RE.search(name or ""))


def clean_display_name(name: str) -> str:
    """Strip the annotations before the name reaches a tile."""
    out = _NAME_ANNOTATION.sub("", name or "")
    return " ".join(out.split()).strip(" -–—|")


def from_name(name: str) -> str:
    """Category implied by the channel's own name, or '' when nothing matches."""
    cleaned = clean_display_name(name)
    for category, pattern in _COMPILED:
        if pattern.search(cleaned):
            return category
    return ""


def from_group(group_slug: str) -> str:
    """Category implied by a group title, including compound ones."""
    if not group_slug:
        return ""
    tokens = [t for t in group_slug.split("-") if t]
    for candidate in GROUP_TOKEN_PRIORITY:
        if candidate in tokens:
            mapped = TOKEN_TO_CATEGORY.get(candidate, "")
            if mapped and mapped != "other":
                return mapped
    for token in tokens:
        mapped = TOKEN_TO_CATEGORY.get(token, "")
        if mapped and mapped != "other":
            return mapped
    return ""


def categorise(name: str, group_slug: str, *, default: str = "other") -> str:
    """Name first, then group title. See the module docstring for why."""
    return from_name(name) or from_group(group_slug) or default

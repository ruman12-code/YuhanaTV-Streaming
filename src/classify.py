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
    # "republic" is anchored to the Indian news brands. Bare, it matched every
    # "(Czech Republic)" and "(Dominican Republic)" in the catalogue and filed
    # National Geographic and Film+ under News.
    ("news", r"\b(news|24x7|24/7 news|aaj tak|ndtv|republic (tv|bharat|bangla|world)|"
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
    "undefined",
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
    "comedy": "entertainment", "entertainment": "entertainment",
    # Not entertainment: see LIVE_CATEGORIES. Name-based rules run first, so a
    # channel in one of these groups whose name says "News" or "Sports" is still
    # classified on its name; only the genuinely unclassifiable land here.
    "general": "general", "undefined": "general", "other": "general",
    "shop": "other",
}

# Channels that are Bangladeshi but whose source records no country. Matched on
# the name so they join the other Bangladeshi channels rather than sitting under
# a genre with the rest of the world.
# Two tiers, because the tokens are not equally reliable.
#
# Unambiguous: these names mean a Bangladeshi channel wherever it is uplinked.
# The declared country does not overrule them, so the London-based diaspora
# feeds (Channel S UK, ATN Bangla UK) stay on the Bangladesh screen.
BD_NAME_STRONG_RE = re.compile(
    r"\b(t[\s-]?sports|atn|somoy|jamuna|ekushey|ekattor|maasranga|boishakhi|"
    r"bijoy|deepto|banglavision|bangla\s*vision|channel\s*i|channel\s*s|dbc|"
    r"news24 bd|nagorik|desh tv|gtv bangla|duronto|asian tv|mohona|sa tv)\b",
    re.IGNORECASE)

# Ambiguous: call signs Bangladesh shares with other broadcasters. BTV is also
# Uganda Broadcasting and Bulgarian National TV; NTV is Kenya, Russia and
# Latvia; RTV is the Netherlands, Indonesia and Serbia. These only count when
# the channel declares Bangladesh or declares nothing - "BTV (Uganda)" was
# sitting on the Bangladesh screen because of this.
BD_NAME_WEAK_RE = re.compile(
    r"\b(btv|n[\s-]?tv|rtv|my tv|independent tv)\b", re.IGNORECASE)

# Kept as the union, for callers that only want "does this name look Bangladeshi".
BD_NAME_RE = re.compile(
    f"(?:{BD_NAME_STRONG_RE.pattern})|(?:{BD_NAME_WEAK_RE.pattern})", re.IGNORECASE)


def is_bangladeshi(name: str, country: str = "") -> bool:
    """True when the channel belongs on the Bangladesh screen.

    A strong name wins outright. A weak one needs the country to agree, or to
    be absent.
    """
    cleaned = clean_display_name(name)
    if BD_NAME_STRONG_RE.search(cleaned):
        return True
    c = (country or "").strip().lower()
    if c and c != "bd":
        return False
    return bool(BD_NAME_WEAK_RE.search(cleaned))


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


# A trailing "(Country)" disambiguates two feeds of one brand; it says nothing
# about genre. Stripped before the genre rules run, so "History (Czech Republic)"
# is classified on "History".
_TRAILING_COUNTRY = None


def _strip_country_suffix(name: str) -> str:
    global _TRAILING_COUNTRY
    if _TRAILING_COUNTRY is None:
        from .generators.countries import COUNTRY_NAMES
        alts = sorted({re.escape(v) for v in COUNTRY_NAMES.values()},
                      key=len, reverse=True)
        _TRAILING_COUNTRY = re.compile(
            r"\s*\((?:" + "|".join(alts) + r")\)\s*$", re.IGNORECASE)
    return _TRAILING_COUNTRY.sub("", name or "").strip()


def categorise(name: str, group_slug: str, *, default: str = "other") -> str:
    """Name first, then group title. See the module docstring for why."""
    return (from_name(_strip_country_suffix(name))
            or from_group(group_slug) or default)


# --- language buckets for the Movie Channels screen -------------------------
# Four flat folders, no nesting: the owner browses by what language a film is in,
# not by which country licensed the feed.

_BANGLA_RE = re.compile(
    r"\b(bangla|bengali|jalsha|zee bangla|star jalsha|colors bangla|sony aath|"
    r"enterr10 bangla|bhojpuri bangla|dhallywood)\b", re.IGNORECASE)
_INDIAN_RE = re.compile(
    r"\b(hindi|bollywood|zee|sony (max|pix|wah|set)|star (gold|plus|bharat|pravah|utsav)|"
    r"colors|&pictures|and pictures|utv|b4u|sahara|enterr10|dangal|goldmines|"
    r"filmy|cinema ?tv|manoranjan|shemaroo|tamil|telugu|kannada|malayalam|"
    r"marathi|punjabi|bhojpuri|gujarati|asianet|sun tv|zee5|maa |gemini|udaya)\b",
    re.IGNORECASE)
_ENGLISH_RE = re.compile(
    r"\b(hbo|axn|amc|paramount|sony movie|warner|wb |mgm|cinemax|showtime|"
    r"hollywood|starz|epix|tcm|turner classic|fox movies|universal|sundance|"
    r"lionsgate|screenpix|movies! |grit|charge|comet|cinevault|filmrise|"
    r"hallmark|lifetime|syfy|tnt|usa network)\b", re.IGNORECASE)

INDIAN_COUNTRIES = {"in"}
BANGLA_COUNTRIES = {"bd"}
ENGLISH_COUNTRIES = {"us", "gb", "uk", "ca", "au", "nz", "ie", "za"}


def movie_language_bucket(name: str, country: str = "") -> str:
    """Which of the four Movie Channels folders a channel belongs in.

    Name first again: a Bollywood channel licensed from Dubai is still Bollywood,
    and the feed's country says nothing about the language of the films.
    """
    cleaned = clean_display_name(name)
    if _BANGLA_RE.search(cleaned):
        return "bangla"
    if _INDIAN_RE.search(cleaned):
        return "indian"
    if _ENGLISH_RE.search(cleaned):
        return "english"
    c = (country or "").lower()
    if c in BANGLA_COUNTRIES:
        return "bangla"
    if c in INDIAN_COUNTRIES:
        return "indian"
    if c in ENGLISH_COUNTRIES:
        return "english"
    return "others"


# Trailing tokens that name a feed variant rather than a different channel.
_DISPLAY_SUFFIX = re.compile(
    r"(?:\s|^)(?:tv|hd|sd|fhd|uhd|4k|channel)$", re.IGNORECASE)
_DISPLAY_STRIP = re.compile(r"[^a-z0-9()]+")


def display_key(name: str) -> str:
    """Identity of a channel for "is this the same tile twice?".

    Spacing and a trailing feed-variant token are not identity: "Banglavision"
    and "Bangla Vision", "Boishakhi" and "Boishakhi TV", "Maasranga" and
    "Maasranga TV" are each one channel carried by two sources.

    A parenthetical IS kept, because that is where the sources put the thing
    that genuinely distinguishes two feeds - Channel S (Bangladesh) and
    Channel S (United Kingdom) are different channels and need separate tiles.
    """
    cleaned = clean_display_name(name).lower()
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _DISPLAY_SUFFIX.sub("", cleaned).strip()
    return _DISPLAY_STRIP.sub("", cleaned)

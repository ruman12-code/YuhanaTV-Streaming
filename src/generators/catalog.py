"""Presentation metadata for playlist tiles.

Kept out of the data model on purpose: a channel's category is data, but the
emoji, tile colour and ordering are presentation and change without touching
the registry.
"""

from __future__ import annotations

from .countries import country_label

# order, label, tile background colour
LIVE_CATEGORY_META: dict[str, dict] = {
    "bangladesh":    {"order": 10, "label": "🇧🇩 Bangladesh",   "bg": "#006a4e"},
    "news":          {"order": 20, "label": "📰 News",          "bg": "#8b1e1e"},
    "sports":        {"order": 30, "label": "🏆 Sports",        "bg": "#0b5d3b"},
    "entertainment": {"order": 40, "label": "🎭 Entertainment", "bg": "#5a2a82"},
    "movies":        {"order": 50, "label": "🎬 Movies on TV",  "bg": "#1f3a93"},
    "series":        {"order": 55, "label": "📺 TV Series",      "bg": "#6a2c70"},
    "music":         {"order": 60, "label": "🎵 Music",         "bg": "#b5341a"},
    "kids":          {"order": 70, "label": "🧸 Kids",          "bg": "#d4820a"},
    "documentary":   {"order": 80, "label": "🌍 Documentary",   "bg": "#14607a"},
    "educational":   {"order": 90, "label": "📚 Educational",   "bg": "#2d6a4f"},
    "business":      {"order": 100, "label": "💼 Business",     "bg": "#34495e"},
    "lifestyle":     {"order": 110, "label": "✨ Lifestyle",    "bg": "#a1246b"},
    "religious":     {"order": 120, "label": "🕌 Religious",    "bg": "#1e6b52"},
    "international": {"order": 130, "label": "🌐 International", "bg": "#2c3e75"},
    "other":         {"order": 999, "label": "📺 Other",        "bg": "#444444"},
}

# Genres for the Movie Library root (spec section 13). Phase 3 populates them.
MOVIE_CATEGORY_META: dict[str, dict] = {
    "4k":             {"order": 3,  "label": "💎 4K Ultra HD",     "bg": "#3b1c6b"},
    "hd":             {"order": 5,  "label": "🎞️ HD (720p+)",     "bg": "#0e5c8a"},
    "fullhd":         {"order": 6,  "label": "✨ Full HD (1080p+)", "bg": "#123a6b"},
    "trending":       {"order": 10, "label": "🔥 Trending",       "bg": "#c0392b"},
    "top-rated":      {"order": 20, "label": "⭐ Top Rated",      "bg": "#b7950b"},
    "recently-added": {"order": 30, "label": "🆕 Recently Added", "bg": "#1e8449"},
    "bengali":        {"order": 40, "label": "🇧🇩 Bengali",       "bg": "#006a4e"},
    "english":        {"order": 50, "label": "🇺🇸 English",       "bg": "#1f3a93"},
    "hindi":          {"order": 60, "label": "🇮🇳 Hindi",         "bg": "#d35400"},
    "korean":         {"order": 70, "label": "🇰🇷 Korean",        "bg": "#7d3c98"},
    "japanese":       {"order": 80, "label": "🇯🇵 Japanese",      "bg": "#a93226"},
    "action":         {"order": 90, "label": "🎬 Action",         "bg": "#922b21"},
    "adventure":      {"order": 95, "label": "🧭 Adventure",      "bg": "#1a5276"},
    "animation":      {"order": 100, "label": "🎨 Animation",     "bg": "#b9770e"},
    "comedy":         {"order": 110, "label": "😂 Comedy",        "bg": "#b7950b"},
    "crime":          {"order": 115, "label": "🚔 Crime",         "bg": "#4a235a"},
    "documentary":    {"order": 120, "label": "🌍 Documentary",   "bg": "#14607a"},
    "drama":          {"order": 130, "label": "🎭 Drama",         "bg": "#5a2a82"},
    "family":         {"order": 140, "label": "👨‍👩‍👧 Family",      "bg": "#1e8449"},
    "fantasy":        {"order": 145, "label": "🧝 Fantasy",       "bg": "#6c3483"},
    "horror":         {"order": 150, "label": "👻 Horror",        "bg": "#212121"},
    "romance":        {"order": 160, "label": "❤️ Romance",       "bg": "#a93226"},
    "scifi":          {"order": 170, "label": "🚀 Sci-Fi",        "bg": "#1a5276"},
    "thriller":       {"order": 180, "label": "🔪 Thriller",      "bg": "#616a6b"},
}


def live_meta(category: str) -> dict:
    return LIVE_CATEGORY_META.get(category, LIVE_CATEGORY_META["other"])


# Labels for the sub-groups a large live category is split into. The key is the
# `tags[0]` slug carried over from the source playlist's group-title.
SUBGROUP_META: dict[str, dict] = {
    "hindi":         {"order": 10, "label": "🇮🇳 Hindi",          "bg": "#d35400"},
    "indian-bangla": {"order": 20, "label": "🇮🇳 Indian Bangla",  "bg": "#8e44ad"},
    "general":       {"order": 30, "label": "📺 General",         "bg": "#5a2a82"},
    "series":        {"order": 40, "label": "📚 Series",          "bg": "#2471a3"},
    "comedy":        {"order": 50, "label": "😂 Comedy",          "bg": "#b7950b"},
    "animation":     {"order": 60, "label": "🎨 Animation",       "bg": "#b9770e"},
    "movies":        {"order": 70, "label": "🎬 Movies",          "bg": "#1f3a93"},
    "music":         {"order": 80, "label": "🎵 Music",           "bg": "#b5341a"},
    "kids":          {"order": 90, "label": "🧸 Kids",            "bg": "#d4820a"},
    "documentary":   {"order": 100, "label": "🌍 Documentary",    "bg": "#14607a"},
    "sports":        {"order": 110, "label": "🏆 Sports",         "bg": "#0b5d3b"},
    "lifestyle":     {"order": 120, "label": "✨ Lifestyle",      "bg": "#a1246b"},
    "islamic":       {"order": 130, "label": "🕌 Islamic",        "bg": "#1e6b52"},
    "international-news": {"order": 140, "label": "🌐 World News", "bg": "#8b1e1e"},
    "hindi-news":    {"order": 150, "label": "🇮🇳 Hindi News",    "bg": "#a04000"},
}


LANGUAGE_META: dict[str, dict] = {
    "lang-bangla":  {"order": 10, "label": "🇧🇩 Bangla",             "bg": "#006a4e"},
    "lang-indian":  {"order": 20, "label": "🇮🇳 Indian / Bollywood", "bg": "#d35400"},
    "lang-english": {"order": 30, "label": "🇬🇧 English",            "bg": "#1f3a93"},
    "lang-others":  {"order": 40, "label": "🌍 Others",              "bg": "#5d6d7e"},
}


def subgroup_meta(slug: str) -> dict:
    if slug.startswith("lang-"):
        return LANGUAGE_META.get(slug, {"order": 900, "label": "🌍 Others", "bg": "#5d6d7e"})
    if slug.startswith("region-"):
        return region_meta(slug[len("region-"):])
    return SUBGROUP_META.get(slug, {"order": 900, "label": slug.replace("-", " ").title(),
                                    "bg": "#444444"})


# Region labels for grouping a large category by where its channels come from.
# Keyed by ISO-3166 alpha-2 as it appears in an iptv-org tvg-id.
REGION_META: dict[str, dict] = {
    "bd": {"order": 5,  "label": "🇧🇩 Bangladesh", "bg": "#006a4e"},
    "in": {"order": 10, "label": "🇮🇳 India",      "bg": "#d35400"},
    "pk": {"order": 15, "label": "🇵🇰 Pakistan",   "bg": "#1e6b52"},
    "us": {"order": 20, "label": "🇺🇸 United States", "bg": "#1f3a93"},
    "gb": {"order": 25, "label": "🇬🇧 United Kingdom", "bg": "#2c3e75"},
    "ca": {"order": 30, "label": "🇨🇦 Canada",     "bg": "#a93226"},
    "au": {"order": 35, "label": "🇦🇺 Australia",  "bg": "#117a65"},
    "br": {"order": 40, "label": "🇧🇷 Brazil",     "bg": "#1e8449"},
    "mx": {"order": 45, "label": "🇲🇽 Mexico",     "bg": "#148f77"},
    "es": {"order": 50, "label": "🇪🇸 Spain",      "bg": "#b9770e"},
    "fr": {"order": 55, "label": "🇫🇷 France",     "bg": "#2471a3"},
    "de": {"order": 60, "label": "🇩🇪 Germany",    "bg": "#4a235a"},
    "it": {"order": 65, "label": "🇮🇹 Italy",      "bg": "#196f3d"},
    "ru": {"order": 70, "label": "🇷🇺 Russia",     "bg": "#7b241c"},
    "tr": {"order": 75, "label": "🇹🇷 Turkey",     "bg": "#922b21"},
    "ae": {"order": 80, "label": "🇦🇪 Middle East", "bg": "#7d6608"},
    "sa": {"order": 82, "label": "🇸🇦 Saudi Arabia", "bg": "#0e6251"},
    "id": {"order": 85, "label": "🇮🇩 Indonesia",  "bg": "#b03a2e"},
    "ph": {"order": 87, "label": "🇵🇭 Philippines", "bg": "#1a5276"},
    "kr": {"order": 90, "label": "🇰🇷 Korea",      "bg": "#7d3c98"},
    "jp": {"order": 92, "label": "🇯🇵 Japan",      "bg": "#a93226"},
    "cn": {"order": 94, "label": "🇨🇳 China",      "bg": "#943126"},
}


# Deterministic tile colours for the countries REGION_META does not hand-pick.
# Muted enough to sit behind white SS IPTV label text.
_AUTO_BG = ("#1f3a93", "#0b5d3b", "#7d1128", "#8e1b1b", "#4a235a", "#0e6251",
            "#7d6608", "#1a5276", "#7b241c", "#196f3d", "#5b2c6f", "#154360")


def region_meta(code: str) -> dict:
    """Label, order and colour for one country folder.

    REGION_META hand-places the countries that should come first - Bangladesh,
    India, Pakistan, then the big broadcasters. Every other ISO country gets a
    generated entry: a real flag and a real name, ordered alphabetically after
    the hand-picked ones.

    This used to fall through to a single "Other regions" bucket. That was fine
    for a 1,500-channel catalogue with 22 countries in it, and useless against
    the full index, where it would have swept a hundred countries into one
    unnavigable screen.
    """
    c = (code or "").strip().lower()
    hand = REGION_META.get(c)
    if hand:
        return hand
    label = country_label(c)
    if label:
        # One order value for the whole generated block; callers break the tie
        # on the label, so these come out alphabetically after the hand-picked
        # countries rather than in whatever order the channels were read.
        return {"order": 100, "label": label,
                "bg": _AUTO_BG[sum(ord(x) for x in c) % len(_AUTO_BG)]}
    return {"order": 900, "label": "🌐 Other regions", "bg": "#444444"}

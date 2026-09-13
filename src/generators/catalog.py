"""Presentation metadata for playlist tiles.

Kept out of the data model on purpose: a channel's category is data, but the
emoji, tile colour and ordering are presentation and change without touching
the registry.
"""

from __future__ import annotations

# order, label, tile background colour
LIVE_CATEGORY_META: dict[str, dict] = {
    "bangladesh":    {"order": 10, "label": "🇧🇩 Bangladesh",   "bg": "#006a4e"},
    "news":          {"order": 20, "label": "📰 News",          "bg": "#8b1e1e"},
    "sports":        {"order": 30, "label": "🏆 Sports",        "bg": "#0b5d3b"},
    "entertainment": {"order": 40, "label": "🎭 Entertainment", "bg": "#5a2a82"},
    "movies":        {"order": 50, "label": "🎬 Movies on TV",  "bg": "#1f3a93"},
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


def subgroup_meta(slug: str) -> dict:
    return SUBGROUP_META.get(slug, {"order": 900, "label": slug.replace("-", " ").title(),
                                    "bg": "#444444"})

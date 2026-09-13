"""Generate the nested Movie Library (spec sections 5, 13, 16, 22).

Shape produced:

    playlists/movies/movies.m3u              root: collection + language + genre tiles
    playlists/movies/trending.m3u            collections
    playlists/movies/top-rated.m3u
    playlists/movies/recently-added.m3u
    playlists/movies/<language>.m3u          bengali, english, hindi, korean, japanese
    playlists/movies/<genre>.m3u             action, comedy, drama, ...

Every leaf holds `type="video"` entries with duration 0, a poster in `tvg-logo`
and a year/genre/rating line in `description` — the SS IPTV Video Library form.

The gate is deliberately narrow: `Movie.publishable_as_vod` requires BOTH a
verified direct playback URL AND cleared redistribution rights. A film that is
merely catalogued, or catalogued and playable but not cleared, never reaches a
tile. Discoverable titles stay in the database for the optional web catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import Movie, slugify
from .m3u import M3UBuilder
from .catalog import MOVIE_CATEGORY_META

# Which buckets are language buckets rather than genres.
LANGUAGE_BUCKETS = {
    "bengali": ("bn", "bengali", "bangla"),
    "english": ("en", "english"),
    "hindi": ("hi", "hindi"),
    "korean": ("ko", "korean"),
    "japanese": ("ja", "japanese"),
}

# Normalise the many spellings a source may use for one genre.
# Keys are compared against slugify() output, so they must themselves be slugs:
# spaces become hyphens and case is folded before the lookup.
GENRE_ALIASES = {
    "sci-fi": "scifi", "science-fiction": "scifi", "sciencefiction": "scifi",
    "kids": "family", "children": "family", "childrens": "family",
    "animated": "animation", "cartoon": "animation", "cartoons": "animation",
    "docu": "documentary", "documentaries": "documentary",
    "thrillers": "thriller", "mystery": "thriller", "suspense": "thriller",
    "romantic": "romance", "war": "action", "western": "adventure",
    "musical": "drama", "biography": "documentary", "history": "documentary",
    "noir": "crime", "film-noir": "crime", "horror-films": "horror",
    "short-films": "drama", "silent-film": "drama", "silent-films": "drama",
}

COLLECTIONS = ("trending", "top-rated", "recently-added")


@dataclass
class MovieBuildResult:
    files: dict[str, int]
    published: list[Movie]
    withheld: dict[str, list[Movie]]
    buckets: dict[str, int]


def normalise_genre(raw: str) -> str:
    slug = slugify(raw)
    slug = GENRE_ALIASES.get(slug, slug)
    return slug if slug in MOVIE_CATEGORY_META else ""


def language_bucket(movie: Movie) -> str:
    value = (movie.language or "").strip().lower()
    for bucket, aliases in LANGUAGE_BUCKETS.items():
        if value in aliases:
            return bucket
    return ""


class MovieGenerator:
    def __init__(self, cfg, playlists_root: Path) -> None:
        self.cfg = cfg
        self.root = Path(playlists_root)
        self.movies_dir = self.root / "movies"
        self.max_items = int(cfg.get_path("ssiptv.max_items_per_playlist", 120))
        self.size_cat = cfg.get_path("ssiptv.tile_size_category", "medium")
        self.size_movie = cfg.get_path("ssiptv.tile_size_movie", "medium")
        self.collection_size = int(cfg.get_path("ssiptv.collection_size", 24))
        self.min_rating_top = float(cfg.get_path("movies.min_rating_top_rated", 7.0))
        self.require_rights = bool(cfg.get_path("policy.vod_requires_playable_and_rights", True))
        # A film RATED below this is withheld. An UNRATED film is kept: absence of
        # a rating is absence of evidence, not evidence of a bad film, and most
        # public-domain titles are simply not rated.
        self.min_rating_publish = float(cfg.get_path("movies.min_rating_publish", 0) or 0)

    # --- gating --------------------------------------------------------------

    def _partition(self, movies: list[Movie]):
        published, withheld = [], {}

        def hold(reason, m):
            withheld.setdefault(reason, []).append(m)

        for m in movies:
            if m.playback_status == "EXCLUDED" or m.rights_status == "EXCLUDED":
                hold("excluded", m)
            elif not m.playback_url:
                hold("no_playback_url", m)
            elif m.playback_status != "PLAYABLE":
                hold(f"playback_{m.playback_status.lower()}", m)
            elif self.require_rights and m.rights_status != "CLEARED":
                hold(f"rights_{m.rights_status.lower()}", m)
            elif (self.min_rating_publish
                  and m.rating is not None
                  and m.rating < self.min_rating_publish):
                hold(f"rating_below_{self.min_rating_publish:g}", m)
            else:
                published.append(m)
        return published, withheld

    # --- presentation --------------------------------------------------------

    @staticmethod
    def description(movie: Movie) -> str:
        bits = []
        if movie.year:
            bits.append(str(movie.year))
        genres = [g for g in (normalise_genre(g) for g in movie.genre) if g]
        if genres:
            bits.append(MOVIE_CATEGORY_META[genres[0]]["label"].split(" ", 1)[-1])
        if movie.rating:
            bits.append(f"★ {movie.rating:.1f}")
        if movie.runtime_minutes:
            bits.append(f"{movie.runtime_minutes} min")
        if movie.resolution_label:
            bits.append(movie.resolution_label)   # measured, never claimed
        return " • ".join(bits)

    @staticmethod
    def _page_label(chunk: list[Movie]) -> str:
        """An A-F style range label from the titles a page actually holds."""
        def initial(m: Movie) -> str:
            for ch in m.title.strip():
                if ch.isalnum():
                    return ch.upper()
            return "#"
        first, last = initial(chunk[0]), initial(chunk[-1])
        return first if first == last else f"{first}-{last}"

    def _paginate(self, movies: list[Movie]) -> list[tuple[str, list[Movie]]]:
        """Split an oversized bucket into alphabetical pages.

        Previously the leaf simply sliced to the cap, which dropped titles with no
        record that it had happened - 31 films vanished from the library that way.
        Pagination keeps every title and keeps each screen within the Smart-TV
        budget, which is what the cap was for.
        """
        ordered = sorted(movies, key=lambda m: m.title.lower())
        if len(ordered) <= self.max_items:
            return [("", ordered)]
        pages = (len(ordered) + self.max_items - 1) // self.max_items
        per = (len(ordered) + pages - 1) // pages
        chunks = [ordered[i:i + per] for i in range(0, len(ordered), per)]
        return [(self._page_label(c), c) for c in chunks if c]

    def _leaf(self, movies: list[Movie], title: str) -> M3UBuilder:
        b = M3UBuilder(default_size=self.size_movie, default_description=title)
        for m in movies:
            b.add_video(
                m.title if not m.year else f"{m.title} ({m.year})",
                m.playback_url,
                logo=m.poster,
                description=self.description(m),
                size=self.size_movie,
            )
        return b

    # --- bucketing -----------------------------------------------------------

    def _buckets(self, movies: list[Movie]) -> dict[str, list[Movie]]:
        buckets: dict[str, list[Movie]] = {}

        # Track membership by id: several of a film's genres can alias to the
        # same bucket ("Crime" and "Film-Noir" both -> crime), which would
        # otherwise put the same film on the same screen twice.
        seen: dict[str, set[str]] = {}

        def place(bucket: str, m: Movie) -> None:
            members = seen.setdefault(bucket, set())
            if m.id in members:
                return
            members.add(m.id)
            buckets.setdefault(bucket, []).append(m)

        for m in movies:
            lang = language_bucket(m)
            if lang:
                place(lang, m)
            for raw in m.genre:
                g = normalise_genre(raw)
                if g:
                    place(g, m)

        # Quality tiers, from the height the file actually declares. A film whose
        # dimensions the source never stated is absent from both, rather than
        # optimistically counted as HD.
        hd = [m for m in movies if m.height >= 700]
        if hd:
            buckets["hd"] = sorted(hd, key=lambda m: (-(m.rating or 0), m.title.lower()))
        fullhd = [m for m in movies if m.height >= 1000]
        if fullhd:
            buckets["fullhd"] = sorted(fullhd, key=lambda m: (-(m.rating or 0), m.title.lower()))

        # Collections are views over the same films, not extra copies.
        rated = [m for m in movies if m.rating is not None]
        top = sorted((m for m in rated if m.rating >= self.min_rating_top),
                     key=lambda m: -(m.rating or 0))
        if top:
            buckets["top-rated"] = top[: self.collection_size]

        recent = sorted((m for m in movies if m.last_verified),
                        key=lambda m: m.last_verified, reverse=True)
        if recent:
            buckets["recently-added"] = recent[: self.collection_size]

        # "Trending" without usage data would be a fabrication, so it is defined
        # as the best-rated of the recently added. With nothing rated, that
        # ordering collapses into Recently Added itself — and shipping the same
        # 24 films twice under two names is worse than not shipping the row.
        if recent and rated:
            trending = sorted(recent[: self.collection_size * 2],
                              key=lambda m: -(m.rating or 0))[: self.collection_size]
            if trending and [m.id for m in trending] != [m.id for m in recent[:len(trending)]]:
                buckets["trending"] = trending

        # A one-title screen is a wasted click; collections are exempt because
        # they are curated views rather than accidental leftovers.
        floor = int(self.cfg.get_path("ssiptv.min_bucket_size", 3))
        exempt = set(COLLECTIONS) | {"hd", "fullhd"}
        return {k: v for k, v in buckets.items()
                if len(v) >= floor or k in exempt}

    # --- build ---------------------------------------------------------------

    def build(self, movies: list[Movie]) -> MovieBuildResult:
        published, withheld = self._partition(movies)
        if not published:
            return MovieBuildResult({}, [], withheld, {})

        buckets = self._buckets(published)
        if not buckets:
            return MovieBuildResult({}, [], withheld, {})

        if self.movies_dir.exists():
            for stale in self.movies_dir.rglob("*.m3u"):
                stale.unlink()
            for d in sorted((d for d in self.movies_dir.rglob("*") if d.is_dir()),
                            reverse=True):
                if not any(d.iterdir()):
                    d.rmdir()

        files: dict[str, int] = {}
        for slug, items in buckets.items():
            meta = MOVIE_CATEGORY_META.get(slug, {"label": slug.title(), "bg": "#444444"})
            pages = self._paginate(items)

            if len(pages) == 1:
                ranked = sorted(items, key=lambda m: (-(m.rating or 0), m.title.lower()))
                files[f"movies/{slug}.m3u"] = self._leaf(ranked, meta["label"]).write(
                    self.movies_dir / f"{slug}.m3u")
                continue

            # Too many for one screen: an index of alphabetical pages.
            index = M3UBuilder(default_size=self.size_cat,
                               default_description=meta["label"])
            for n, (label, chunk) in enumerate(pages, start=1):
                rel = f"movies/{slug}/{n:02d}.m3u"
                files[rel] = self._leaf(
                    chunk, f"{meta['label']} {label}").write(
                    self.movies_dir / slug / f"{n:02d}.m3u")
                index.add_playlist(
                    f"{meta['label']} {label} ({len(chunk)})",
                    self.cfg.playlist_url(rel),
                    description=f"{len(chunk)} titles",
                    size=self.size_cat, background=meta.get("bg", "#444444"))
            files[f"movies/{slug}.m3u"] = index.write(self.movies_dir / f"{slug}.m3u")

        root = M3UBuilder(
            default_size=self.size_cat,
            default_description="🎬 Movies",
        )
        for slug, items in sorted(
            buckets.items(),
            key=lambda kv: (MOVIE_CATEGORY_META.get(kv[0], {}).get("order", 999), kv[0]),
        ):
            meta = MOVIE_CATEGORY_META.get(slug, {"label": slug.title(), "bg": "#444444"})
            root.add_playlist(
                f"{meta['label']} ({len(items)})",
                self.cfg.playlist_url(f"movies/{slug}.m3u"),
                description=f"{len(items)} titles",
                size=self.size_cat,
                background=meta.get("bg", "#444444"),
            )
        files["movies/movies.m3u"] = root.write(self.movies_dir / "movies.m3u")

        return MovieBuildResult(files, published, withheld,
                                {k: len(v) for k, v in buckets.items()})

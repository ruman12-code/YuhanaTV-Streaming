"""IMDb non-commercial dataset adapter.

IMDb publishes `title.basics.tsv.gz` and `title.ratings.tsv.gz` for personal and
non-commercial use with no API key. That is what makes a real rating filter
possible here without asking the owner to register for anything.

Memory discipline matters: `title.basics` carries roughly eleven million rows and
would be about a gigabyte held in a dict. Instead the catalogue's own titles are
normalised into a small set of lookup keys first, and the dataset is streamed
through gzip line by line, keeping only rows that match one of those keys. Peak
memory stays proportional to the catalogue, not to IMDb.

Matching is deliberately strict. A wrong match puts a confident, wrong rating on
a film, which is worse than no rating: titles must agree after normalisation AND
the year must agree within one, and only film-shaped title types are considered.
"""

from __future__ import annotations

import gzip
import re
import urllib.request
from dataclasses import dataclass

BASICS_URL = "https://datasets.imdbws.com/title.basics.tsv.gz"
RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"

# Title types that can plausibly be one of our films.
FILM_TYPES = {"movie", "short", "tvMovie", "video", "documentary", "tvShort"}

# Archive titles routinely carry a trailing "(1968)" or "[1949]"; IMDb's do not.
_TRAILING_YEAR = re.compile(r"\s*[\(\[]\s*(1[89]\d{2}|20\d{2})\s*[\)\]]\s*$")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_WS = re.compile(r"\s+")


def normalise_title(title: str) -> str:
    """Fold a title to a comparison key.

    Punctuation, case, a leading article and repeated whitespace all vary freely
    between the Archive's metadata and IMDb's, and none of them carry meaning
    for identity.
    """
    t = (title or "").strip()
    t = _TRAILING_YEAR.sub("", t).lower()
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    t = _LEADING_ARTICLE.sub("", t)
    return _WS.sub(" ", t).strip()


@dataclass
class ImdbTitle:
    tconst: str
    title: str
    year: int | None
    title_type: str
    genres: list[str]
    is_adult: bool = False
    rating: float | None = None
    votes: int = 0


def _stream_tsv(url: str, timeout: float, user_agent: str):
    """Yield each row of a gzipped TSV as a list of fields, without buffering it."""
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with gzip.GzipFile(fileobj=resp) as gz:
            header = gz.readline()
            if not header:
                return
            for raw in gz:
                yield raw.decode("utf-8", "replace").rstrip("\n").split("\t")


def build_lookup_keys(movies) -> dict[tuple[str, int], list]:
    """Map (normalised title, year) -> the catalogue entries wanting that key.

    Each film registers its own year plus the two neighbouring years, because
    release-year disagreements of one are routine between sources.
    """
    keys: dict[tuple[str, int], list] = {}
    for m in movies:
        norm = normalise_title(m.title)
        if not norm or m.year is None:
            continue
        for delta in (0, -1, 1):
            keys.setdefault((norm, m.year + delta), []).append(m)
    return keys


class ImdbDatasets:
    def __init__(self, cfg, *, timeout: float = 300.0) -> None:
        self.timeout = timeout
        self.user_agent = cfg.get_path("validation.user_agent", "Mozilla/5.0")
        self.min_votes = int(cfg.get_path("movies.imdb_min_votes", 0))

    def find_titles(self, keys: dict[tuple[str, int], list]) -> dict[str, ImdbTitle]:
        """Stream title.basics, keeping only rows a catalogue entry asked for."""
        found: dict[str, ImdbTitle] = {}
        if not keys:
            return found
        for row in _stream_tsv(BASICS_URL, self.timeout, self.user_agent):
            # tconst titleType primaryTitle originalTitle isAdult startYear endYear runtime genres
            if len(row) < 9:
                continue
            if row[1] not in FILM_TYPES:
                continue
            year_raw = row[5]
            if not year_raw.isdigit():
                continue
            year = int(year_raw)
            for candidate in (row[2], row[3]):
                key = (normalise_title(candidate), year)
                if key in keys:
                    found[row[0]] = ImdbTitle(
                        tconst=row[0], title=row[2], year=year, title_type=row[1],
                        genres=[g for g in row[8].split(",") if g and g != r"\N"],
                        is_adult=(row[4] == "1"),
                    )
                    break
        return found

    def attach_ratings(self, titles: dict[str, ImdbTitle]) -> dict[str, ImdbTitle]:
        """Stream title.ratings, filling in the ones we matched."""
        if not titles:
            return titles
        wanted = set(titles)
        for row in _stream_tsv(RATINGS_URL, self.timeout, self.user_agent):
            if len(row) < 3 or row[0] not in wanted:
                continue
            try:
                titles[row[0]].rating = float(row[1])
                titles[row[0]].votes = int(row[2])
            except ValueError:
                continue
        return titles


def best_match(movie, candidates: list[ImdbTitle]) -> ImdbTitle | None:
    """Pick one IMDb title for a film, or none if the evidence is weak.

    Preference order: exact year, then most votes. A candidate whose year is more
    than one away is rejected outright rather than accepted as the only option.
    """
    norm = normalise_title(movie.title)
    viable = [
        c for c in candidates
        if normalise_title(c.title) == norm
        and c.year is not None and movie.year is not None
        and abs(c.year - movie.year) <= 1
    ]
    if not viable:
        return None
    viable.sort(key=lambda c: (0 if c.year == movie.year else 1, -c.votes))
    return viable[0]

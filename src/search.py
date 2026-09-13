"""Full-text search index for the catalogue (spec section 25).

SS IPTV has no global search, and nothing in the M3U layer can give it one. The
requirement is therefore that the *database* supports search even though the TV
cannot, so the optional web catalogue (Phase 10) has something to query.

The index is a plain inverted index in JSON: a token -> list of document
positions, with the documents alongside. No search engine, no service to run,
and small enough for a browser to fetch and query entirely client-side.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

_TOKEN = re.compile(r"[a-z0-9]+")

# Words that match almost everything and so carry no discriminating power.
STOPWORDS = frozenset("""
a an and are as at be by for from has he in is it its of on or that the to was
were will with this these those there their them then than
""".split())

MIN_TOKEN = 2


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return [t for t in _TOKEN.findall(folded.lower())
            if len(t) >= MIN_TOKEN and t not in STOPWORDS]


def build_index(movies, channels=None) -> dict:
    """Return {documents: [...], index: {token: [doc_index, ...]}}."""
    documents: list[dict] = []
    postings: dict[str, set[int]] = defaultdict(set)

    def add(doc: dict, searchable: str) -> None:
        idx = len(documents)
        documents.append(doc)
        for token in set(tokenize(searchable)):
            postings[token].add(idx)

    for m in movies:
        add({
            "kind": "movie",
            "id": m.id,
            "title": m.title,
            "year": m.year,
            "genre": m.genre,
            "language": m.language,
            "rating": m.rating,
            "imdb_id": m.imdb_id,
            "poster": m.poster,
            "url": m.playback_url if m.publishable_as_vod else "",
            "source_url": m.source_url,
            "playable": m.publishable_as_vod,
        }, " ".join(filter(None, [
            m.title, str(m.year or ""), m.language, " ".join(m.genre), m.synopsis])))

    for c in (channels or []):
        add({
            "kind": "channel",
            "id": c.id,
            "title": c.name,
            "category": c.category,
            "language": c.language,
            "country": c.country,
            "resolution": c.resolution_label,
            "logo": c.logo,
            "status": c.status,
            "playable": c.status == "ACTIVE",
        }, " ".join(filter(None, [
            c.name, c.category, c.language, c.country, c.resolution_label,
            " ".join(c.tags)])))

    return {
        "version": 1,
        "note": "Inverted index. `index` maps a token to positions in `documents`. "
                "Intersect postings for AND, union for OR. Prefix search: scan keys.",
        "document_count": len(documents),
        "token_count": len(postings),
        "documents": documents,
        "index": {token: sorted(ids) for token, ids in sorted(postings.items())},
    }


def search(index: dict, query: str, limit: int = 25) -> list[dict]:
    """Reference implementation of querying the index, and what the tests pin."""
    tokens = tokenize(query)
    if not tokens:
        return []
    postings = index.get("index", {})
    hits: set[int] | None = None
    for token in tokens:
        # Exact token, else every token starting with it (prefix search).
        ids = set(postings.get(token, []))
        if not ids:
            for key, value in postings.items():
                if key.startswith(token):
                    ids.update(value)
        hits = ids if hits is None else (hits & ids)
        if not hits:
            return []
    docs = index.get("documents", [])
    out = [docs[i] for i in sorted(hits or []) if i < len(docs)]
    out.sort(key=lambda d: (not d.get("playable"), -(d.get("rating") or 0),
                            str(d.get("title", "")).lower()))
    return out[:limit]

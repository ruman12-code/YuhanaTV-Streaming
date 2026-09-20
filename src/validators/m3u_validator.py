"""Playlist structure validator (spec section 33).

Checks a generated playlist tree for the faults that actually break SS IPTV:

  * not valid UTF-8, or carrying a BOM
  * missing/misplaced #EXTM3U header
  * malformed #EXTINF (bad duration, missing comma, unbalanced quotes)
  * attributes SS IPTV does not understand
  * #EXTSIZE / #EXTBG with an illegal value or in the wrong position
  * an #EXTINF with no URL, or a URL with no #EXTINF
  * unsafe URLs (delegated to util.urls)
  * duplicate titles and duplicate URLs inside one playlist
  * a child playlist reference that does not resolve to a file we generated
  * oversized playlists (Smart-TV memory budget)
  * CIRCULAR playlist references, e.g. A -> B -> C -> A
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..util.urls import check_url

# Attributes SS IPTV documents for #EXTINF.
KNOWN_EXTINF_ATTRS = {
    "type", "content-type", "tvg-id", "tvg-name", "tvg-logo", "tvg-shift",
    "audio-track", "aspect-ratio", "description", "group-title",
}
KNOWN_HEADER_ATTRS = {"x-tvg-url", "url-tvg", "size", "background", "description"}
VALID_SIZES = {"small", "medium", "big"}
VALID_TYPES = {"stream", "video", "playlist"}

_EXTINF_RE = re.compile(r"^#EXTINF\s*:\s*(-?\d+(?:\.\d+)?)(.*)$", re.IGNORECASE)
_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"')
_SIZE_RE = re.compile(r"^#EXTSIZE\s*:?\s*(.+)$", re.IGNORECASE)
_BG_RE = re.compile(r"^#EXTBG\s*:?\s*(.+)$", re.IGNORECASE)
_COLOUR_RE = re.compile(r"^(#[0-9a-fA-F]{6}|rgba?\([^)]*\))$")


@dataclass
class Issue:
    severity: str   # error | warning
    file: str
    line: int
    code: str
    message: str


@dataclass
class PlaylistReport:
    path: str
    item_count: int = 0
    playlist_refs: list[str] = field(default_factory=list)
    stream_refs: list[str] = field(default_factory=list)
    bytes: int = 0


class M3UValidator:
    def __init__(self, cfg, playlists_root: Path) -> None:
        self.cfg = cfg
        self.root = Path(playlists_root)
        self.base_playlist_url = cfg.playlist_url("").rstrip("/")
        self.max_items = int(cfg.get_path("ssiptv.max_items_per_playlist", 120))
        self.issues: list[Issue] = []
        self.reports: dict[str, PlaylistReport] = {}

    # --- helpers -------------------------------------------------------------

    def _add(self, severity, file, line, code, message) -> None:
        self.issues.append(Issue(severity, str(file), line, code, message))

    def _url_to_local(self, url: str) -> Path | None:
        """Map a published child-playlist URL back onto the file we generate."""
        if not url.startswith(self.base_playlist_url + "/"):
            return None
        rel = url[len(self.base_playlist_url) + 1:].split("?")[0]
        return self.root / rel

    # --- single file ---------------------------------------------------------

    def validate_file(self, path: Path) -> PlaylistReport:
        rel = path.relative_to(self.root).as_posix()
        report = PlaylistReport(path=rel)
        raw = path.read_bytes()
        report.bytes = len(raw)

        if raw.startswith(b"\xef\xbb\xbf"):
            self._add("error", rel, 1, "BOM",
                      "file starts with a UTF-8 BOM; SS IPTV expects plain UTF-8")
            raw = raw[3:]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._add("error", rel, 1, "ENCODING", f"file is not valid UTF-8: {exc}")
            self.reports[rel] = report
            return report

        lines = text.splitlines()
        if not lines or not lines[0].startswith("#EXTM3U"):
            self._add("error", rel, 1, "HEADER", "first line must be #EXTM3U")
        else:
            for key, value in _ATTR_RE.findall(lines[0]):
                if key.lower() not in KNOWN_HEADER_ATTRS:
                    self._add("warning", rel, 1, "HEADER_ATTR",
                              f"unknown #EXTM3U attribute '{key}'")
                if key.lower() == "size" and value.lower() not in VALID_SIZES:
                    self._add("error", rel, 1, "SIZE_VALUE",
                              f"header size='{value}' must be one of {sorted(VALID_SIZES)}")

        pending_extinf: tuple[int, str, dict] | None = None
        saw_size = saw_bg = False
        titles: dict[str, int] = {}
        urls: dict[str, int] = {}

        for no, line in enumerate(lines[1:], start=2):
            stripped = line.strip()
            if not stripped:
                continue

            m = _EXTINF_RE.match(stripped)
            if m:
                if pending_extinf is not None:
                    self._add("error", rel, pending_extinf[0], "ORPHAN_EXTINF",
                              "#EXTINF is not followed by a URL")
                duration, remainder = m.group(1), m.group(2)
                if remainder.count('"') % 2:
                    self._add("error", rel, no, "QUOTES", "unbalanced quotes in #EXTINF")
                if "," not in _ATTR_RE.sub("", remainder):
                    self._add("error", rel, no, "NO_TITLE",
                              "#EXTINF has no comma-separated title")
                attrs = {k.lower(): v for k, v in _ATTR_RE.findall(remainder)}
                for key in attrs:
                    if key not in KNOWN_EXTINF_ATTRS:
                        self._add("warning", rel, no, "UNKNOWN_ATTR",
                                  f"attribute '{key}' is not documented for SS IPTV")
                kind = attrs.get("type") or attrs.get("content-type") or ""
                if kind and kind.lower() not in VALID_TYPES:
                    self._add("error", rel, no, "TYPE_VALUE",
                              f"type='{kind}' must be one of {sorted(VALID_TYPES)}")
                if kind.lower() == "video" and duration not in ("0", "-1"):
                    self._add("warning", rel, no, "DURATION",
                              f"library item uses duration {duration}; 0 is documented")
                title = remainder.split(",", 1)[-1].strip() if "," in remainder else ""
                # Title uniqueness: a duplicate tile is indistinguishable on a TV.
                if title:
                    if title.lower() in titles:
                        self._add("warning", rel, no, "DUP_TITLE",
                                  f"title '{title}' already used on line {titles[title.lower()]}")
                    else:
                        titles[title.lower()] = no
                pending_extinf = (no, kind.lower(), attrs)
                saw_size = saw_bg = False
                continue

            sm = _SIZE_RE.match(stripped)
            if sm:
                value = sm.group(1).strip().lower()
                if pending_extinf is None:
                    self._add("error", rel, no, "SIZE_ORPHAN",
                              "#EXTSIZE must follow an #EXTINF line")
                if saw_size:
                    self._add("warning", rel, no, "SIZE_DUP", "duplicate #EXTSIZE for one item")
                if value not in VALID_SIZES:
                    self._add("error", rel, no, "SIZE_VALUE",
                              f"#EXTSIZE '{value}' must be one of {sorted(VALID_SIZES)}")
                saw_size = True
                continue

            bm = _BG_RE.match(stripped)
            if bm:
                value = bm.group(1).strip()
                if pending_extinf is None:
                    self._add("error", rel, no, "BG_ORPHAN",
                              "#EXTBG must follow an #EXTINF line")
                if saw_bg:
                    self._add("warning", rel, no, "BG_DUP", "duplicate #EXTBG for one item")
                if not _COLOUR_RE.match(value):
                    verdict = check_url(value)
                    if not verdict.ok:
                        self._add("error", rel, no, "BG_VALUE",
                                  f"#EXTBG must be #rrggbb, rgb(a)(...) or an image URL: {verdict.reason}")
                saw_bg = True
                continue

            if stripped.startswith("#"):
                continue

            # A bare line: this is the URL for the pending #EXTINF.
            if pending_extinf is None:
                self._add("error", rel, no, "ORPHAN_URL", "URL without a preceding #EXTINF")
                continue

            verdict = check_url(stripped)
            if not verdict.ok:
                self._add("error", rel, no, "BAD_URL", f"{verdict.reason}: {stripped[:80]}")
            if stripped in urls:
                self._add("warning", rel, no, "DUP_URL",
                          f"URL already used on line {urls[stripped]}")
            else:
                urls[stripped] = no

            kind = pending_extinf[1]
            is_playlist = kind == "playlist" or stripped.lower().split("?")[0].endswith((".m3u", ".m3u8", ".xspf"))
            if kind == "playlist" or (not kind and stripped.lower().split("?")[0].endswith((".m3u", ".xspf"))):
                report.playlist_refs.append(stripped)
            else:
                report.stream_refs.append(stripped)
            report.item_count += 1
            pending_extinf = None

        if pending_extinf is not None:
            self._add("error", rel, pending_extinf[0], "ORPHAN_EXTINF",
                      "#EXTINF is not followed by a URL")

        if report.item_count == 0:
            self._add("error", rel, 1, "EMPTY", "playlist contains no items")
        if report.item_count > self.max_items:
            self._add("warning", rel, 1, "TOO_LARGE",
                      f"{report.item_count} items exceeds the configured "
                      f"{self.max_items}-item Smart-TV budget; split it further")

        self.reports[rel] = report
        return report

    # --- whole tree ----------------------------------------------------------

    def validate_tree(self, entry: str = "master.m3u") -> dict:
        files = sorted(self.root.rglob("*.m3u"))
        for f in files:
            self.validate_file(f)

        # Resolve child references to real files.
        graph: dict[str, list[str]] = {}
        for rel, report in self.reports.items():
            graph[rel] = []
            for url in report.playlist_refs:
                local = self._url_to_local(url)
                if local is None:
                    self._add("warning", rel, 0, "EXTERNAL_CHILD",
                              f"child playlist is not served from our base URL: {url}")
                    continue
                child_rel = local.relative_to(self.root).as_posix()
                if not local.exists():
                    self._add("error", rel, 0, "MISSING_CHILD",
                              f"child playlist does not exist: {child_rel}")
                    continue
                graph[rel].append(child_rel)

        cycles = self._find_cycles(graph)
        for cycle in cycles:
            self._add("error", cycle[0], 0, "CYCLE",
                      "circular playlist reference: " + " -> ".join(cycle + [cycle[0]]))

        reachable = self._reachable(graph, entry)
        for rel in sorted(self.reports):
            if rel not in reachable:
                # An error, not a warning: an orphaned playlist is a file the TV
                # can never open, and it still gets deployed. A tree that grew
                # them was published once while CI reported success.
                self._add("error", rel, 0, "UNREACHABLE",
                          f"playlist is not reachable from {entry}; "
                          f"it would be deployed but never shown")

        errors = [i for i in self.issues if i.severity == "error"]
        return {
            "entry": entry,
            "files_checked": len(self.reports),
            "total_items": sum(r.item_count for r in self.reports.values()),
            "errors": len(errors),
            "warnings": len(self.issues) - len(errors),
            "cycles": [" -> ".join(c + [c[0]]) for c in cycles],
            "unreachable": sorted(set(self.reports) - reachable),
            "issues": [i.__dict__ for i in self.issues],
            "ok": not errors,
        }

    @staticmethod
    def _find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
        """Iterative DFS with an explicit stack; returns each distinct cycle once."""
        WHITE, GREY, BLACK = 0, 1, 2
        colour = {n: WHITE for n in graph}
        cycles: list[list[str]] = []
        seen: set[frozenset] = set()

        for start in sorted(graph):
            if colour[start] != WHITE:
                continue
            stack: list[tuple[str, int]] = [(start, 0)]
            path: list[str] = [start]
            colour[start] = GREY
            while stack:
                node, idx = stack[-1]
                children = graph.get(node, [])
                if idx < len(children):
                    stack[-1] = (node, idx + 1)
                    child = children[idx]
                    if colour.get(child, WHITE) == GREY:
                        cut = path.index(child)
                        cycle = path[cut:]
                        key = frozenset(cycle)
                        if key not in seen:
                            seen.add(key)
                            cycles.append(cycle)
                    elif colour.get(child, WHITE) == WHITE:
                        colour[child] = GREY
                        path.append(child)
                        stack.append((child, 0))
                else:
                    colour[node] = BLACK
                    stack.pop()
                    path.pop()
        return cycles

    @staticmethod
    def _reachable(graph: dict[str, list[str]], entry: str) -> set[str]:
        if entry not in graph:
            return set()
        seen = {entry}
        queue = [entry]
        while queue:
            node = queue.pop()
            for child in graph.get(node, []):
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
        return seen

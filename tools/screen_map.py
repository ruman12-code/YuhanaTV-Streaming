#!/usr/bin/env python3
"""Walk the published playlist tree and emit every screen as JSON.

The owner asked to see each screen before opening the app on the TV. Nothing
here can photograph a Toshiba in Dhaka, but the tiles on every screen are fully
determined by the playlists this pipeline writes, so they can be reproduced
exactly: same order, same labels, same artwork, same counts.

Output: {"screens": [{path, title, tiles:[{label, kind, target, description}]}]}
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLAYLISTS = REPO / "playlists"

_EXTINF = re.compile(r'^#EXTINF:(?P<dur>-?\d+)\s*(?P<attrs>[^,]*),(?P<title>.*)$')
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')


def parse(path: Path) -> dict:
    """One playlist file -> its title and the tiles on it."""
    title, tiles = "", []
    pending = None
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {"title": "", "tiles": []}
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTM3U"):
            m = re.search(r'description="([^"]*)"', line)
            title = m.group(1) if m else ""
            continue
        m = _EXTINF.match(line)
        if m:
            attrs = dict(_ATTR.findall(m.group("attrs")))
            pending = {
                "label": m.group("title").strip(),
                "kind": attrs.get("type", "stream"),
                "description": attrs.get("description", ""),
                "logo": attrs.get("tvg-logo", ""),
                "art": "",
            }
            continue
        if line.startswith("#EXTBG:") and pending is not None:
            pending["art"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("#") or not line:
            continue
        if pending is not None:
            pending["target"] = line
            tiles.append(pending)
            pending = None
    return {"title": title, "tiles": tiles}


def rel_of(url: str) -> str:
    """The repo-relative playlist path a tile points at, or '' for a stream."""
    m = re.search(r"/playlists/(.+\.m3u)$", url)
    return m.group(1) if m else ""


def main() -> int:
    if not (PLAYLISTS / "master.m3u").exists():
        print("no playlists built yet", file=sys.stderr)
        return 1

    screens, queue, seen = [], ["master.m3u"], set()
    while queue:
        rel = queue.pop(0)
        if rel in seen:
            continue
        seen.add(rel)
        data = parse(PLAYLISTS / rel)
        for t in data["tiles"]:
            child = rel_of(t.get("target", "")) if t["kind"] == "playlist" else ""
            t["child"] = child
            if child and child not in seen:
                queue.append(child)
        screens.append({
            "path": rel,
            "title": data["title"],
            "tiles": data["tiles"],
            "channels": sum(1 for t in data["tiles"] if t["kind"] != "playlist"),
            "folders": sum(1 for t in data["tiles"] if t["kind"] == "playlist"),
        })

    out = {
        "generated_from": "playlists/",
        "screen_count": len(screens),
        "total_tiles": sum(len(s["tiles"]) for s in screens),
        "screens": screens,
    }
    dest = REPO / "data" / "status" / "screen-map.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False) + "\n")
    print(f"{len(screens)} screens, {out['total_tiles']} tiles -> "
          f"{dest.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

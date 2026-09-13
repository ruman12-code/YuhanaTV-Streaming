"""Generate the landing page served at the site root.

Without this, GitHub Pages answers `/` with its own 404 even though every
playlist beneath it is being served correctly — which looks exactly like a
broken deployment.

The page is also the most convenient place to read the current state of the
library and to copy the URL that goes into the TV. It is built from
validation-report.json, so it cannot drift from what was actually measured.

Self-contained by design: no external CSS, fonts or scripts, so it loads on a
phone over a slow connection and cannot break because a CDN changed.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .catalog import LIVE_CATEGORY_META, MOVIE_CATEGORY_META


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _stat(label: str, value, note: str = "") -> str:
    return (f'<div class="stat"><div class="n">{_esc(value)}</div>'
            f'<div class="l">{_esc(label)}</div>'
            + (f'<div class="s">{_esc(note)}</div>' if note else "")
            + "</div>")


def render(report: dict, cfg, playlists_root: Path) -> str:
    base = cfg.base_url
    brand = cfg.get_path("site.brand", "YuhanaTV")
    lc = report.get("live_channels", {}) or {}
    mv = report.get("movies", {}) or {}
    res = report.get("resolution", {}) or {}
    health = report.get("run_health", {}) or {}
    rel = report.get("reliability", {}) or {}
    generated = report.get("generated_at", "")

    master_url = f"{base}/iptv/master.m3u"

    def count_items(rel_path: str) -> int:
        p = playlists_root / rel_path
        if not p.exists():
            return 0
        return p.read_text(encoding="utf-8").count("#EXTINF")

    # Rows for whatever actually exists on disk.
    live_rows = []
    for slug, meta in sorted(LIVE_CATEGORY_META.items(), key=lambda kv: kv[1]["order"]):
        n = count_items(f"live/{slug}.m3u")
        if n:
            live_rows.append((meta["label"], n, f"{base}/playlists/live/{slug}.m3u"))

    movie_rows = []
    for slug, meta in sorted(MOVIE_CATEGORY_META.items(), key=lambda kv: kv[1]["order"]):
        n = count_items(f"movies/{slug}.m3u")
        if n:
            movie_rows.append((meta["label"], n, f"{base}/playlists/movies/{slug}.m3u"))

    endpoints = [
        ("Master playlist — put this one in SS IPTV", f"{base}/iptv/master.m3u"),
        ("Live TV only", f"{base}/iptv/live-tv.m3u"),
        ("Bangladesh only", f"{base}/iptv/bangladesh.m3u"),
        ("International only", f"{base}/iptv/international.m3u"),
    ]
    if (playlists_root / "movies" / "movies.m3u").exists():
        endpoints.append(("Movies only", f"{base}/iptv/movies.m3u"))
    endpoints.append(("Validation report (JSON)", f"{base}/validation-report.json"))

    def rows(items):
        return "\n".join(
            f'<tr><td>{_esc(label)}</td><td class="num">{_esc(n)}</td>'
            f'<td><a href="{_esc(url)}">open</a></td></tr>'
            for label, n, url in items)

    gate = health.get("ok")
    gate_text = {True: "open", False: "shut", None: "not run"}.get(gate, "not run")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(brand)} — personal media library</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #f6f7f9; --fg: #14171a; --muted: #5b6570; --card: #ffffff;
    --line: #dfe3e8; --accent: #1f3a93; --ok: #1e7a46; --warn: #9a6a00;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #10131a; --fg: #e8eaed; --muted: #9aa4b2; --card: #171b24;
      --line: #262c38; --accent: #7fa0ff; --ok: #4bbd7c; --warn: #d9a33a;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--fg);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 28px 18px 64px; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 4px; letter-spacing: -.01em; }}
  h2 {{ font-size: 1.05rem; margin: 34px 0 12px; }}
  .sub {{ color: var(--muted); margin: 0 0 22px; font-size: .9rem; }}
  .card {{ background: var(--card); border: 1px solid var(--line);
    border-radius: 10px; padding: 16px; }}
  .hero {{ border-left: 4px solid var(--accent); }}
  .url {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .87rem; word-break: break-all; background: var(--bg);
    border: 1px solid var(--line); border-radius: 7px; padding: 10px 12px;
    margin: 10px 0 0; }}
  .stats {{ display: grid; gap: 10px; margin: 16px 0 0;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }}
  .stat {{ background: var(--card); border: 1px solid var(--line);
    border-radius: 9px; padding: 12px 14px; }}
  .stat .n {{ font-size: 1.5rem; font-weight: 650; letter-spacing: -.02em; }}
  .stat .l {{ color: var(--muted); font-size: .82rem; margin-top: 2px; }}
  .stat .s {{ color: var(--muted); font-size: .74rem; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }}
  th {{ color: var(--muted); font-weight: 600; font-size: .78rem;
    text-transform: uppercase; letter-spacing: .04em; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; width: 5em; }}
  a {{ color: var(--accent); }}
  .cols {{ display: grid; gap: 18px; grid-template-columns: 1fr; }}
  @media (min-width: 720px) {{ .cols {{ grid-template-columns: 1fr 1fr; }} }}
  .note {{ color: var(--muted); font-size: .84rem; margin-top: 10px; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85em; }}
  .pill {{ display: inline-block; padding: 1px 8px; border-radius: 999px;
    font-size: .75rem; border: 1px solid var(--line); color: var(--muted); }}
</style>
</head>
<body>
<div class="wrap">

  <h1>{_esc(brand)}</h1>
  <p class="sub">Personal SS IPTV library · last built {_esc(generated)}
     · health gate <span class="pill">{_esc(gate_text)}</span></p>

  <div class="card hero">
    <strong>Paste this into SS IPTV</strong>
    <div class="note">Settings → Content → External playlists → Add</div>
    <div class="url">{_esc(master_url)}</div>
  </div>

  <div class="stats">
    {_stat("Live channels", lc.get("active", 0), f'of {lc.get("total", 0)} verified reachable')}
    {_stat("1080p", res.get("measured_1080p", 0), "measured, not claimed")}
    {_stat("720p", res.get("measured_720p", 0), "measured, not claimed")}
    {_stat("Movies", mv.get("published_as_vod", 0), "rights cleared and playable")}
  </div>

  <div class="cols">
    <div>
      <h2>Live TV</h2>
      <div class="card"><table>
        <tr><th>Category</th><th class="num">Ch.</th><th></th></tr>
        {rows(live_rows)}
      </table></div>
    </div>
    <div>
      <h2>Movies</h2>
      <div class="card"><table>
        <tr><th>Category</th><th class="num">Titles</th><th></th></tr>
        {rows(movie_rows) or '<tr><td colspan="3">No movie catalogue built.</td></tr>'}
      </table></div>
    </div>
  </div>

  <h2>All endpoints</h2>
  <div class="card"><table>
    <tr><th>What</th><th></th></tr>
    {"".join(f'<tr><td>{_esc(l)}</td><td><a href="{_esc(u)}">{_esc(u.rsplit("/", 1)[-1])}</a></td></tr>' for l, u in endpoints)}
  </table>
  <p class="note">These paths are stable. The content behind them refreshes
     automatically; the URLs never change, so SS IPTV is configured once.</p>
  </div>

  <h2>What "verified" means here</h2>
  <div class="card">
    <p class="note" style="margin-top:0">
      A channel is published only after its manifest, its variant playlist and a
      real media segment were all fetched successfully from a GitHub runner.
      Resolutions come from the stream's own <code>EXT-X-STREAM-INF</code>, never
      from a channel's name. A film is published only when its rights are cleared
      <em>and</em> its first bytes were fetched with <code>Range</code> support.
    </p>
    <p class="note">
      Validation runs outside Bangladesh, so a channel that is reachable here can
      still be geo-blocked on your network, and the reverse. Mean reliability
      across recent runs: {_esc(round(rel.get("mean_reliability") or 0, 2))}.
    </p>
  </div>

</div>
</body>
</html>
"""


def write(report: dict, cfg, playlists_root: Path, out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    data = render(report, cfg, playlists_root).encode("utf-8")
    out.write_bytes(data)
    return len(data)

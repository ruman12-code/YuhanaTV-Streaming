# Hosting

## Chosen default: GitHub Pages from this repository

`config/config.json → site.base_url` is
`https://ruman12-code.github.io/YuhanaTV-Streaming`. Changing that one value
re-points every generated URL in the tree.

Why Pages: it serves `Access-Control-Allow-Origin: *`, it is free, it is already
where the repository lives, and the CI job that regenerates the playlists can
deploy them in the same run — which is precisely the "configure SS IPTV once, never
upload a file again" requirement.

**One-time setup:** repository *Settings → Pages → Build and deployment → Source:
GitHub Actions*. Until this is enabled the `deploy` job will fail while the `build`
job keeps working.

## The URLs to configure in SS IPTV

Configure the first one only; the rest exist as convenience shortcuts.

```
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/master.m3u      ← enter this
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/live-tv.m3u
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/bangladesh.m3u
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/international.m3u
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/movies.m3u      (Phase 3)
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/epg.xml         (Phase 7)
```

These paths are stable by contract. Content behind them changes on every scheduled run;
the URLs do not.

## Alternatives, and when they would be better

| Option | Use it when | Cost |
|---|---|---|
| **GitHub Pages** (default) | normal case | free |
| `raw.githubusercontent.com/...` | Pages is not enabled | free; ~5 min CDN cache, `text/plain` content type |
| **Cloudflare Pages** | you need control over response headers — the likely Phase 7 answer if GitHub Pages' gzip negotiation breaks XMLTV | free tier |
| **Own domain + CDN** | you want a short URL to type on a TV remote | domain cost |

## Custom domain

Add `CNAME` to the deployed site and set `site.base_url` to match. Nothing else in the
codebase refers to a hostname.

## What is *not* hosted here

No stream is proxied, relayed, cached or re-encoded by this project. Every playlist entry
points at its origin, and the TV connects to that origin directly. This repository
publishes text files, and nothing else.

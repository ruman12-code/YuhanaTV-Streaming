# Limitations, and what is done about each

Written up front rather than discovered later. Each entry states the constraint,
its consequence, and the mitigation actually implemented.

## 1. Stream validation cannot run in the authoring environment

The environment this repository was authored in blocks all outbound HTTPS except
an allow-listed set (GitHub and package registries). Every attempt to reach a
stream host returns `403` from the egress proxy before TLS is negotiated:

```
CONNECT tunnel failed, response 403      # cdn/cloudfront, aynaott, jagobd, akamai — all hosts
```

**Consequence.** No stream in this repository has been reachability-tested yet.
Every channel carries `status: "UNVERIFIED"` and the Phase 1 playlists are stamped
as a pre-validation seed build.

**Mitigation.** The validator is written, and is proven correct against a local HLS
origin that reproduces nine real-world outcomes (master→variant→segment, dead
variant, unfetchable segment, slow origin, redirect, 503, non-manifest body, empty
manifest, DNS failure, unsafe URL). It runs unmodified in GitHub Actions, which has
open egress. Run `Actions → Update IPTV → Run workflow` to produce the first real
validation report.

## 2. GitHub-hosted runners are not in Bangladesh

CI runs in a US/EU datacentre. Channels served only to Bangladeshi IP ranges will be
reported `OFFLINE` by CI even though they play on the owner's TV — a false negative
that would silently delete working channels.

**Decision (Phase 2): validation runs on GitHub-hosted runners.** The false-negative risk
is accepted in exchange for zero infrastructure. Three mechanisms contain it:

1. `consecutive_failures_before_offline` (default 3) — one failure demotes a channel to
   `DEGRADED`; only sustained failure across separate runs marks it `OFFLINE`.
2. The **run-health gate** (`src/validators/health.py`) — if more than 40% of
   previously-`ACTIVE` channels fail in a single run, the run is treated as a fault in the
   vantage point rather than in the channels: statuses are not updated and the published
   playlists are left exactly as they were. This is precisely the "US runner briefly cannot
   reach Bangladesh" scenario.
3. **Reliability counters** — `checks_ok / checks_total` accrues on every run, including
   runs the gate rejected. A channel that is genuinely BD-locked will show a stable low
   reliability rather than flapping, which distinguishes it from an intermittent origin.

If BD channels turn out to be systematically unreachable from CI, the remaining honest fix
is to run `scripts/pipeline.py check` from a machine on the target network and commit the
resulting status file. The pipeline already supports this: `data/status/` is the only thing
that would need to come from elsewhere.

## 3. SS IPTV cannot send per-stream HTTP headers

Three channels in the imported list carry `#EXTVLCOPT:http-referrer` /
`http-user-agent`. That directive is VLC's; SS IPTV has no documented equivalent.
Such a stream will 403 on the TV no matter how healthy the origin is.

**Mitigation.** They are withheld from every published playlist with the reason
`requires_custom_http_headers`, and listed in `validation-report.json`. They are not
deleted — if a header-free URL for the same channel turns up, it can replace them.
Publishing them as tiles that cannot play would be worse than omitting them.

## 4. Provenance of most imported channels is not established

Of 171 imported channels, host classification identifies 5 as broadcaster-operated
and 16 as free ad-supported platforms (Amagi, Rakuten, Samsung TV Plus, Tubi, Xumo,
Wurl, Frequency). The remaining 150 come from generic CDNs (32) or hosts with no
identifiable operator, including bare IP-address origins (118).

**Mitigation.** `data/sources/host-trust.json` records the tier and the reason for
every host, so the position is visible rather than implied. `trust_tier` is
provenance evidence, not a licensing determination, and the pipeline does not treat
it as one.

## 5. XMLTV delivery has a hosting constraint (Phase 7)

SS IPTV requires XMLTV to be **ungzipped**, under ~5 MB, with `Access-Control-Allow-Origin: *`
and Range-related CORS headers, and it is fetched **by SS IPTV's server rather than by
the TV**. GitHub Pages sets `Access-Control-Allow-Origin: *` and supports Range, but it
negotiates gzip when the client offers `Accept-Encoding: gzip`, which is outside our
control.

**Mitigation.** Deferred to Phase 7, where the hosted EPG URL must be tested from an
external network before being attached (spec section 18). If gzip negotiation turns out
to break it, the EPG moves to a host where the response headers are controllable. The
playlists already emit `x-tvg-url`, so only the host changes.

## 6. Codec support is narrower than the catalogue

A Smart TV decodes far less than a desktop player. Ogg Theora, WebM/VP9, MPEG-2
programme streams and Matroska are all either unsupported or erratic across VIDAA
builds, and the Internet Archive serves plenty of each.

**Consequence.** Selecting a derivative on resolution alone produced a library
where a quarter of the titles could not play at all.

**Mitigation.** Decodability is the first gate, ahead of resolution; see
[MOVIE-QUALITY.md](MOVIE-QUALITY.md). The residual risk is that a file passes the
format check and still fails on this particular set, since nothing here can test
a real decoder. That surfaces as a title that looks right and will not play, and
the fix is to narrow the accepted formats further.

## 7. Smart-TV memory

Not a limitation so much as a budget that shapes the design: every generated playlist is
≤31 items and ≤8 KB, and any category that grows past `subsplit_threshold` is split
automatically. Posters for Phase 3 must be thumbnail-sized, not full-resolution artwork.

## 8. Favourites, continue-watching and global search

These are application features. They cannot be expressed in M3U, and faking them would
make the playlist layer non-deterministic. They belong to the optional web catalogue
(Phase 10). The movie database is built with full-text search in mind so that the web
layer can provide search even though SS IPTV cannot.

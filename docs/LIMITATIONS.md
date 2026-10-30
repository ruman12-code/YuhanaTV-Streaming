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

## What "playable" can and cannot mean here (2026-09-23)

The owner asked for every playable channel, with no adult content. Two of those
three words are measurable from CI and one is not.

**Not measurable: playable on the owner's TV.** Validation runs on a
GitHub-hosted runner in the United States. The TV is in South Asia. Nothing in
this repository has ever opened a stream from the owner's network, and no
amount of green in `validation-report.json` changes that. The probe answers a
different question — "does this origin serve a US datacentre?" — and the two
answers diverge in both directions:

* A South Asian broadcaster fenced to its home region refuses the runner and
  serves the owner. These used to be dropped at ingest on the source's
  `[Geo-blocked]` tag, which threw away exactly the channels the owner can
  watch. They are now kept, and published when the channel is in the viewer's
  region and the failure looks like a refusal (403/451) rather than an absence
  (DNS failure, refused connection, 404). See `validation.publish_region_blocked`.
* A US or European FAST service answers the runner and will very likely refuse
  Dhaka. Roughly a thousand channels reach this library through iptv-org's
  redirector to Pluto and similar platforms, filed under United States, Sweden
  and Germany. They probe ACTIVE at 92% reliability *from the runner*. Whether
  any of them plays on the owner's TV is unknown and untestable from here. They
  are published because the measurement available says they work and the owner
  asked not to have playable channels withheld; the country folders keep them
  out of the way of anyone browsing Bangladesh or India.

**Measurable: structurally playable on SS IPTV.** A stream that needs a
per-request `Referer` or `User-Agent` cannot work in SS IPTV, which sends
neither. Those are withheld on a capability limit, not a guess.

**Measurable: not adult.** Screened at ingest on three independent signals —
the aggregator's own category, a known pornographic brand in the channel name,
and the keyword list used for the film library. Screening previously existed
only for the film library, because the narrow category feeds this project
started with contained no adult channels. The full aggregator index has a
category of them.

### Gates that are policy, not measurement

Two exclusions stand whatever a probe says, and they are not up for
re-litigation by a reliability score:

* A stream URL carrying subscriber credentials — a portal MAC or an account
  login — only works by presenting someone's paid subscription.
* Adult material, on the owner's explicit instruction.

### A note on blanket bans

Three gates in this project's history were assumptions dressed as rules: an
HTTPS requirement (withheld 333 channels; the owner's own working playlist was
31% plain HTTP), rejecting `[Geo-blocked]` channels at ingest, and a
denied-host list covering iptv-org's redirector (withheld 1,020 channels that
probe ACTIVE). Each was replaced by a measurement — respectively none, the
viewer's region, and a safety check applied to the recorded redirect
destination rather than the advertised URL. Where a rule cannot be replaced by
a measurement, it belongs in the section above and needs a reason that does not
depend on a probe.

## Repository growth (known, not yet a problem)

`data/channels/*.json` is the registry, and it is committed on every run so the
reliability history survives. At roughly twelve thousand channels, a run that
re-probes most of them rewrites that many `last_verified` timestamps, and there
are eight runs a day. Compressed, that is on the order of a megabyte or two per
run.

Extrapolated, the repository reaches GitHub's 5GB soft limit somewhere inside a
year. Nothing needs doing now, and there are two straightforward remedies when
it does: squash the history of the output commits, or move the registry out of
git and into a workflow cache or release asset. Recording it here so that it is
a decision rather than a surprise.

## When a channel's URL dies

A dead URL is not a dead channel. An operator rotates a path, the aggregator is
slow to notice, and the tile sits there failing. T Sports HD is the case that
prompted this: 404 on eighteen consecutive probes while the same operator went
on serving twenty other Bangladeshi channels, and the only URL iptv-org lists
for `TSports.bd` is that same dead one.

`data/candidates.json` holds a short list of alternative URLs per channel. Any
channel already condemned has its candidates probed on the normal cycle, each
through exactly the same validation as everything else, and the first that
passes replaces the dead URL. The swap is recorded on the channel and in
`validation-report.json`; the reliability counters restart, because a new
endpoint has no history and inheriting the old one's record would misstate
both.

Each entry records where its candidates came from, because a published listing
and an informed guess are not the same kind of evidence and the difference
should survive in the file. The lists stay short on purpose: a handful of
plausible endpoints for a free-to-air channel on hosts this project already
uses is a reasonable thing to try, and a scan of somebody's CDN is not.

## What a decoder adds that nothing else can

Every check before this one reasons about a stream from the outside: the URL is
safe, the host resolves, the manifest parses, a segment is fetched and its bytes
look like a container. None of that proves a television will show a picture.
Two kinds of channel pass all of it and play as a black screen:

* the container carries no video stream at all, only sound;
* the video decodes perfectly and every frame is black, because the encoder is
  pushing a dead feed behind a CDN that is still serving happily.

`src/validators/decode.py` runs ffmpeg against the stream, decodes a few
seconds and reads what came out: frames, black time, and the real codec and
picture size. A manifest's `RESOLUTION` is a claim; a decoded size is a
measurement, and where the two differ the measurement wins.

It is bounded, because a decode costs seconds and bandwidth where an HTTP probe
costs milliseconds. Bangladesh and the owner's own playlist are decoded every
run, so a black screen on the list he actually watches is caught within hours.
Everything else joins a rotation of a few hundred per run, oldest first, so the
backlog of channels published on nothing stronger than "the segment was real
bytes" clears over about three days instead of never.

Three things it deliberately does not do. It does not treat a dark scene as a
dead feed: the black threshold is 95% of the decoded window with a near-zero
pixel threshold, so a film can be dark without being pulled. It does not hold a
failed decode against a channel: a decode that timed out is recorded as
transient, exactly like a timed-out HTTP probe, because an instrument that
could not take a reading has not taken one. And it never runs with `file` in
ffmpeg's protocol whitelist, because the URLs come from aggregators and a
crafted playlist must not be able to read the runner's disk; arguments are
passed as a list rather than through a shell, and output goes to the null muxer
so nothing is ever written.

### What it still cannot tell you

That the stream decodes on a GitHub runner, not that it decodes on a Toshiba.
The TV's own decoder has its own limits, and the geography problem at the top of
this file is untouched by any of this.

## Self-audit, 2026-10-30: flaws found in my own gates

An audit of every rule in the pipeline against the published data, from the
television's point of view. Each of these was compromising playback, and each is
fixed with a test that fails if it returns.

**The TLS fallback never ran.** urllib wraps a certificate failure inside
`URLError`, so `except ssl.SSLCertVerificationError` around a fetch is
unreachable code. The relaxation the owner approved had not taken effect once;
220 channels sat failing with `CERTIFICATE_VERIFY_FAILED`. Fixed by recognising
the failure by inspection. And because a TV's player almost certainly validates
certificates itself, an https stream with an untrusted chain is also tried over
plain http on the same path, and the http form is published when it serves.

**Reliability counted black screens as successes.** Every DEGRADED result
incremented `checks_ok`, and DEGRADED had come to include "decoded video is
black", "segment is not media" and "no video stream". A channel stuck in one of
those accrued a perfect record and was trusted on it. A probe is now graded
playable, broken or unknown: only playable earns credit, only broken costs a
strike, and a timeout or a decoder that could not get in does neither.

**A geo tag was publishing 404s.** The source's `[Geo-blocked]` tag admitted a
channel whatever its recorded failure, so 27 tiles reached the TV whose last
probe said 404, an unfetchable segment, or bytes that were not media. The tag
now only excuses a failure that could be a refusal.

**Stream dedup was global.** The same stream filed under News and under
Bangladesh lost one of its two doors, and the one kept was not necessarily the
Bangladesh one. Dedup is now per category, plus a per-screen pass so no screen
carries one stream or one label twice.

**An empty guide was attached to 916 playlists.** 917 KB of XMLTV holding 6,214
channel entries and zero programmes, which SS IPTV was told to fetch for every
screen. Now attached only when it contains at least one programme.

## Startup time

Two things shorten the time from pressing a tile to seeing a picture, and both
are measured rather than assumed:

* A redirect that returns the same destination on two consecutive probes, with
  no query string (where expiring tokens live), is published as its
  destination. Every press otherwise pays an extra round trip to the redirector,
  and from Dhaka to a redirector abroad that is real time. If the destination
  ever changes, the next probe sees it and the tile reverts.
* On every screen, channels that have been confirmed to play come first. Nothing
  is hidden - the never-confirmed ones follow, alphabetically, marked
  "Unverified" - but the first tiles a viewer tries are ones known to work.

One thing that would seem to help and deliberately does not: ranking feeds by
how quickly they answer. That latency is measured from a US datacentre, and for
Bangladeshi content it is close to backwards - a feed hosted in Dhaka looks slow
from Virginia and is fast from Dhaka. Ranking on it would push the best feeds
for this viewer to the bottom. The home probe records latency from the right
place; until it runs, picture size and reliability decide.

## Detection speed

The full pass is forty minutes over twelve thousand channels and GitHub runs it
every four to eleven hours. `quick-check.yml` probes only what the owner watches
- Bangladesh, his own playlist and his favourites, about seventy channels -
hourly, and rebuilds. A Bangladeshi channel that dies is pulled, and one that
recovers is restored, within an hour or two instead of a day.

It shares the content-write concurrency group with the full pass, which is
required: both rewrite the registry, and two runs racing would let the older
snapshot overwrite the newer probe results. The cost is that GitHub cancels a
*pending* run in a group when another is queued, so an hourly quick run can
occasionally displace a pending full pass. That skips one full pass; it never
loses data.

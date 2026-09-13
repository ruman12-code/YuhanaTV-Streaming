# EPG (XMLTV)

## What is generated

`epg/epg.xml`, published at:

```
https://ruman12-code.github.io/YuhanaTV-Streaming/iptv/epg.xml
```

and attached to every live playlist through the header directive SS IPTV
documents for this purpose:

```
#EXTM3U x-tvg-url="https://ruman12-code.github.io/YuhanaTV-Streaming/epg/epg.xml"
```

## The rule that shapes everything here

**Schedules are never fabricated.** A channel with no real programme data gets a
`<channel>` entry — so the TV can name and log it — and no `<programme>` elements
at all. There is no filler, no "No information available" block, no synthesised
24-hour grid. An invented schedule is worse than an empty one: it is wrong at a
glance and it trains you to distrust the guide.

`tests/test_epg.py` pins this, along with the rejection of programmes that are
orphaned, untitled, or end before they start.

## Canonical channel ids matter more than hosted data

SS IPTV supplies guide information of its own for channels it recognises, and
recognition happens through `tvg-id`. An invented id matches nothing; a canonical
one — `SomoyTV.bd`, `AlJazeeraEnglish.qa` — lets the app fill in a guide we never
had to host. The specification asks for exactly this: don't load EPG for channels
the app already covers.

`scripts/build_epg.py` therefore does two separate jobs. First it maps each
channel onto a canonical id from the [iptv-org open database](https://iptv-org.github.io/api/channels.json),
matching on name and country. Then it emits the XMLTV file.

Matching is conservative. Country must agree when we know ours, because "Somoy TV"
in Bangladesh and a same-named channel elsewhere are different services and
guessing between them attaches the wrong schedule. Quality suffixes are stripped
("Star Movies HD" → "star movies") but short names are protected: folding
"Channel i" to "i" would match almost anything.

## Constraints taken from SS IPTV's documentation

| Constraint | Where it is enforced |
|---|---|
| XMLTV, UTF-8 | `src/epg/xmltv.py` |
| **Must not be gzipped** | written as plain bytes; a test asserts no gzip magic |
| Under ~5 MB | `ssiptv.max_xmltv_bytes`, enforced during render |
| `Access-Control-Allow-Origin: *` | GitHub Pages sets this on every response |
| `Access-Control-Allow-Methods`, `Allow-Headers: Range`, `Expose-Headers` | see the open risk below |

When the budget is reached the file truncates rather than overflowing, and
programmes are emitted in time order so truncation costs next week's listings
rather than tonight's.

## Open risk: gzip negotiation

SS IPTV states that gzip is **not** allowed for XMLTV, and that the file is
fetched **by SS IPTV's server rather than by the TV**, because connected-TV
devices block cross-domain requests. GitHub Pages sets
`Access-Control-Allow-Origin: *` and supports Range, but it negotiates gzip
whenever a client sends `Accept-Encoding: gzip` — which is outside our control.

This cannot be settled from here; it needs the hosted URL tested from an external
network, which is what the spec asks for. If gzip negotiation does break it, the
fix is to move the EPG to a host where response headers are controllable
(Cloudflare Pages, for instance). Nothing else changes: the playlists already
carry `x-tvg-url`, so only the value moves.

## Programme data

No public source publishes ready-made XMLTV for these channels. iptv-org
publishes the channel database and an index of which sites *have* guides, but the
XML itself has to be grabbed per site with their Node toolchain.
`data/status/epg.json` records how many of our channels have a guide source
available, which is the input to deciding whether running that grabber in CI is
worth it.

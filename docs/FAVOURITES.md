# Favourites

## The constraint, first

SS IPTV is somebody else's application. This project writes M3U playlist files
and hosts them; it has no way to add a button to an SS IPTV screen, and no way
to be told that one was pressed. There is no hook, no callback, no API. So a
"like" button *inside* SS IPTV is not something that can be built from here, by
anyone, at any amount of effort.

Two things can be done instead, and they work together.

## 1. SS IPTV's own favourites (works today, nothing to install)

Most SS IPTV builds have a favourites feature of their own: highlight a channel,
press and hold **OK** (or press the menu/info key, depending on the remote), and
choose *Add to favourites*. The app keeps that list on the television.

This is instant and needs nothing from this repository. Its limits are worth
knowing: the list lives only on that TV, it is lost if the app's data is
cleared, and it can lose track of a channel when a playlist is regenerated and
the channel moves. Use it for convenience; do not rely on it as the record.

## 2. The Favourites tile in this playlist (durable, survives everything)

The home screen carries a **❤️ Favourites** tile, built from
`data/favourites.json`:

```json
{
  "channel_ids": ["ch-7ccfcd9b22", "ch-a41b0c9e10"]
}
```

Those are `Channel.id` values, the same ones in `data/channels/*.json`. The
order is the order they appear on the tile, so the most recently marked go
first.

Because this list is in the repository it survives a factory reset, an app
reinstall, a new television, and every playlist rebuild. A favourite whose
origin has gone off the air is skipped on that build rather than deleted, so it
comes back by itself when the origin does.

### Marking a channel

Three ways, in order of convenience:

**The companion page.** A web page listing every channel with a ♥ button,
opened on a phone or laptop while watching. Marking a channel there records it,
and the next build picks it up. This is the closest thing to the button you
wanted, and the reason it lives on a second screen rather than on the TV is the
constraint at the top of this file.

**Ask.** Name the channels in a message and they are added.

**By hand.** Find the id in `data/channels/*.json` and add it to the array.
`tools/find_channel.py "star sports"` prints the ids for a name.

### Why ids and not names

Two channels share a name often enough that it matters — Channel S (Bangladesh)
and Channel S (United Kingdom), ATN Bangla and ATN Bangla UK. An id is
unambiguous and it does not change when a channel is relabelled or moves
between folders.

"""XMLTV writer for SS IPTV (spec sections 17 and 18).

Constraints taken from SS IPTV's operator documentation, not invented here:

  * the file must be XMLTV, UTF-8, and **must not be gzipped**
  * it should stay under about 5 MB
  * it must be served with `Access-Control-Allow-Origin: *` plus the Range-related
    CORS headers, because SS IPTV's own server fetches it rather than the TV
  * it is attached with `#EXTM3U x-tvg-url="..."`, already emitted by the generator

The hard rule this module enforces is the one the specification states plainly:
**do not fabricate EPG schedules.** A channel with no real programme data gets a
`<channel>` entry so the TV can still name and log it, and no `<programme>`
elements at all. There is no filler, no "No information available" block, and no
synthesised 24-hour grid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr

XMLTV_TIME = "%Y%m%d%H%M%S %z"

# XML 1.0 forbids most control characters outright; they cannot be escaped.
_ILLEGAL_XML = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f﷐-﷟￾￿]")


def clean_xml_text(value: str) -> str:
    return _ILLEGAL_XML.sub("", str(value or ""))


def fmt_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime(XMLTV_TIME)


@dataclass
class Programme:
    channel_id: str
    start: datetime
    stop: datetime
    title: str
    description: str = ""
    category: str = ""
    language: str = ""
    episode: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.channel_id and self.title and self.stop > self.start)


@dataclass
class EpgChannel:
    id: str
    display_names: list[str] = field(default_factory=list)
    icon: str = ""
    language: str = ""


class XmltvWriter:
    """Builds an XMLTV document, bounded by a byte budget."""

    def __init__(self, *, generator_name: str = "YuhanaTV",
                 max_bytes: int = 5 * 1024 * 1024) -> None:
        self.generator_name = generator_name
        self.max_bytes = max_bytes
        self.channels: dict[str, EpgChannel] = {}
        self.programmes: list[Programme] = []
        self.dropped_programmes = 0

    def add_channel(self, channel: EpgChannel) -> None:
        if channel.id:
            self.channels[channel.id] = channel

    def add_programme(self, programme: Programme) -> bool:
        """Reject anything invalid or orphaned rather than emitting a broken entry."""
        if not programme.valid or programme.channel_id not in self.channels:
            self.dropped_programmes += 1
            return False
        self.programmes.append(programme)
        return True

    # --- rendering -----------------------------------------------------------

    def _channel_xml(self, ch: EpgChannel) -> str:
        parts = [f"  <channel id={quoteattr(clean_xml_text(ch.id))}>"]
        for name in (ch.display_names or [ch.id]):
            name = clean_xml_text(name)
            if name:
                parts.append(f"    <display-name>{escape(name)}</display-name>")
        if ch.icon:
            parts.append(f"    <icon src={quoteattr(clean_xml_text(ch.icon))} />")
        parts.append("  </channel>")
        return "\n".join(parts)

    def _programme_xml(self, p: Programme) -> str:
        lang = f' lang={quoteattr(p.language)}' if p.language else ""
        parts = [
            f'  <programme start="{fmt_time(p.start)}" stop="{fmt_time(p.stop)}" '
            f'channel={quoteattr(clean_xml_text(p.channel_id))}>',
            f"    <title{lang}>{escape(clean_xml_text(p.title))}</title>",
        ]
        if p.description:
            parts.append(f"    <desc{lang}>{escape(clean_xml_text(p.description))}</desc>")
        if p.category:
            parts.append(f"    <category{lang}>{escape(clean_xml_text(p.category))}</category>")
        if p.episode:
            parts.append('    <episode-num system="onscreen">'
                         f"{escape(clean_xml_text(p.episode))}</episode-num>")
        parts.append("  </programme>")
        return "\n".join(parts)

    def render(self) -> str:
        head = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
            f'<tv generator-info-name={quoteattr(self.generator_name)} '
            f'date="{datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S +0000")}">\n'
        )
        tail = "</tv>\n"

        body = [self._channel_xml(c) for c in self.channels.values()]
        size = len(head.encode()) + len(tail.encode()) + sum(len(b.encode()) + 1 for b in body)

        # Programmes are added newest-first within the budget, so truncation costs
        # the far future rather than tonight's listings.
        for p in sorted(self.programmes, key=lambda p: (p.start, p.channel_id)):
            chunk = self._programme_xml(p)
            chunk_size = len(chunk.encode()) + 1
            if size + chunk_size > self.max_bytes:
                self.dropped_programmes += 1
                continue
            body.append(chunk)
            size += chunk_size

        return head + "\n".join(body) + ("\n" if body else "") + tail

    def write(self, path) -> int:
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = self.render().encode("utf-8")   # never gzipped: SS IPTV forbids it
        p.write_bytes(data)
        return len(data)

    # --- reporting -----------------------------------------------------------

    def stats(self, total_channels: int) -> dict:
        with_programmes = {p.channel_id for p in self.programmes}
        return {
            "channels_in_epg": len(self.channels),
            "channels_with_programmes": len(with_programmes),
            "channels_without_programmes": len(self.channels) - len(with_programmes),
            "programmes": len(self.programmes),
            "dropped": self.dropped_programmes,
            "coverage_percent": (round(100 * len(with_programmes) / total_channels, 1)
                                 if total_channels else 0.0),
            "note": "A channel with no real programme data carries a <channel> entry "
                    "and no <programme> elements. Schedules are never fabricated.",
        }

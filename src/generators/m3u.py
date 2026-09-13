"""SS IPTV Extended M3U writer.

Grammar implemented (from SS IPTV operator documentation):

    #EXTM3U [x-tvg-url="..."] [size="..."] [background="..."] [description="..."]
    #EXTINF:<duration> [attr="value" ...],<Title>
    [#EXTSIZE: small|medium|big]
    [#EXTBG: <url or #rrggbb or rgba(...)>]
    <URL>

The #EXTSIZE / #EXTBG directives sit BETWEEN the #EXTINF line and the URL line;
that ordering is taken from the documented Video Library example, not guessed.

Output is always UTF-8, LF-terminated, and every URL occupies a line of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Characters that would break the grammar if they reached the output.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NEWLINES = re.compile(r"[\r\n  ]+")

VALID_SIZES = ("small", "medium", "big")
VALID_TYPES = ("stream", "video", "playlist")


def clean_text(value: str, *, limit: int = 0) -> str:
    """Make a string safe for a single M3U line."""
    if not value:
        return ""
    out = _NEWLINES.sub(" ", str(value))
    out = _CONTROL.sub("", out)
    out = " ".join(out.split())
    if limit and len(out) > limit:
        out = out[: limit - 1].rstrip() + "…"
    return out


def clean_attr(value: str, *, limit: int = 0) -> str:
    """Attribute values are double-quoted, so an embedded quote must go."""
    return clean_text(value, limit=limit).replace('"', "'")


@dataclass
class Item:
    title: str
    url: str
    kind: str = "stream"                 # VALID_TYPES
    duration: str = "-1"
    attrs: dict[str, str] = field(default_factory=dict)
    size: str = ""
    background: str = ""


class M3UBuilder:
    """Accumulates items, then renders one SS IPTV playlist."""

    def __init__(
        self,
        *,
        tvg_url: str = "",
        default_size: str = "",
        default_background: str = "",
        default_description: str = "",
        emit_type_attr: bool = True,
        type_attr_name: str = "type",
        header_comment: str = "",
    ) -> None:
        self.tvg_url = tvg_url
        self.default_size = default_size if default_size in VALID_SIZES else ""
        self.default_background = default_background
        self.default_description = default_description
        self.emit_type_attr = emit_type_attr
        # SS IPTV documents the parameter as "content-type" in prose but every
        # official example writes it as `type="..."`. Configurable for that reason.
        self.type_attr_name = type_attr_name
        self.header_comment = header_comment
        self.items: list[Item] = []

    # --- item constructors ---------------------------------------------------

    def add_playlist(
        self, title: str, url: str, *, logo: str = "", description: str = "",
        size: str = "", background: str = "",
    ) -> None:
        attrs = {}
        if logo:
            attrs["tvg-logo"] = logo
        if description:
            attrs["description"] = description
        self.items.append(Item(title, url, "playlist", "0", attrs, size, background))

    def add_stream(
        self, title: str, url: str, *, tvg_id: str = "", tvg_name: str = "",
        logo: str = "", description: str = "", audio_track: str = "",
        aspect_ratio: str = "", size: str = "", background: str = "",
        duration: str = "-1",
    ) -> None:
        attrs = {}
        if tvg_id:
            attrs["tvg-id"] = tvg_id
        if tvg_name:
            attrs["tvg-name"] = tvg_name
        if logo:
            attrs["tvg-logo"] = logo
        if audio_track:
            attrs["audio-track"] = audio_track
        if aspect_ratio:
            attrs["aspect-ratio"] = aspect_ratio
        if description:
            attrs["description"] = description
        self.items.append(Item(title, url, "stream", duration, attrs, size, background))

    def add_video(
        self, title: str, url: str, *, logo: str = "", description: str = "",
        audio_track: str = "", aspect_ratio: str = "", size: str = "",
        background: str = "",
    ) -> None:
        attrs = {}
        if logo:
            attrs["tvg-logo"] = logo
        if audio_track:
            attrs["audio-track"] = audio_track
        if aspect_ratio:
            attrs["aspect-ratio"] = aspect_ratio
        if description:
            attrs["description"] = description
        self.items.append(Item(title, url, "video", "0", attrs, size, background))

    # --- rendering -----------------------------------------------------------

    def _header(self) -> str:
        parts = ["#EXTM3U"]
        if self.tvg_url:
            parts.append(f'x-tvg-url="{clean_attr(self.tvg_url)}"')
        if self.default_size:
            parts.append(f'size="{self.default_size}"')
        if self.default_background:
            parts.append(f'background="{clean_attr(self.default_background)}"')
        if self.default_description:
            parts.append(f'description="{clean_attr(self.default_description, limit=160)}"')
        return " ".join(parts)

    def render(self) -> str:
        lines: list[str] = [self._header()]
        if self.header_comment:
            for comment_line in self.header_comment.splitlines():
                lines.append(f"# {clean_text(comment_line)}")

        for item in self.items:
            attr_parts = []
            if self.emit_type_attr and item.kind in VALID_TYPES:
                attr_parts.append(f'{self.type_attr_name}="{item.kind}"')
            for key, value in item.attrs.items():
                cleaned = clean_attr(value, limit=300 if key == "description" else 0)
                if cleaned:
                    attr_parts.append(f'{key}="{cleaned}"')

            attr_blob = (" " + " ".join(attr_parts)) if attr_parts else ""
            # Quotes are stripped from titles too: a title containing an
            # attribute-looking fragment could confuse a quote-state parser.
            title = clean_attr(item.title, limit=120) or "Untitled"
            lines.append(f"#EXTINF:{item.duration}{attr_blob},{title}")

            size = item.size if item.size in VALID_SIZES else ""
            if size:
                lines.append(f"#EXTSIZE: {size}")
            if item.background:
                lines.append(f"#EXTBG: {clean_text(item.background)}")

            lines.append(item.url)

        return "\n".join(lines) + "\n"

    def write(self, path) -> int:
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = self.render().encode("utf-8")   # no BOM: SS IPTV expects plain UTF-8
        p.write_bytes(data)
        return len(data)

    def __len__(self) -> int:
        return len(self.items)

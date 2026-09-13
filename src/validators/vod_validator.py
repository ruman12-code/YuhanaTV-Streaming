"""Validate a direct video URL before a film may be called PLAYABLE (spec 11, 16).

Live streams and VOD files fail differently, so this is separate from
stream_validator. What matters for VOD, and what SS IPTV needs in order to offer
pause and seek, is:

  * the URL is safe and reachable
  * the response is actually video, not an HTML error page or a login wall
  * the server honours a Range request, which is what makes seeking work
  * the declared length is plausible for a feature film

A file served without `Accept-Ranges` will still play, but the user cannot seek
in it. That is recorded as DEGRADED rather than passed off as fully playable.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

from ..util.urls import check_url, host_of

VIDEO_CONTENT_TYPES = (
    "video/", "application/vnd.apple.mpegurl", "application/x-mpegurl",
    "application/dash+xml", "application/octet-stream",
)

# Container magic bytes. `ftyp` at offset 4 is MP4/MOV; 0x1A45DFA3 is Matroska/WebM.
_MP4_BRAND = b"ftyp"
_MKV_MAGIC = b"\x1a\x45\xdf\xa3"


@dataclass
class VodResult:
    movie_id: str
    url: str
    ok: bool = False
    status: str = "UNVERIFIED"       # PLAYABLE | DEGRADED | UNREACHABLE | INVALID
    http_status: int = 0
    content_type: str = ""
    content_length: int = 0
    accepts_ranges: bool = False
    seekable: bool = False
    latency_ms: float = 0.0
    error: str = ""
    checked_at: str = field(default_factory=
                            lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


def _looks_like_video(head: bytes, content_type: str) -> bool:
    if head[4:8] == _MP4_BRAND or head.startswith(_MKV_MAGIC):
        return True
    if head.lstrip().startswith(b"#EXTM3U"):
        return True                      # an HLS VOD playlist is fine
    if head[:1] == b"\x47":
        return True                      # MPEG-TS
    ct = (content_type or "").lower()
    return any(ct.startswith(t) for t in VIDEO_CONTENT_TYPES if t != "application/octet-stream")


class VodValidator:
    def __init__(self, cfg) -> None:
        v = cfg.get_path("validation", {})
        s = cfg.get_path("security", {})
        self.timeout = float(v.get("timeout_read_seconds", 12))
        self.user_agent = v.get("user_agent", "Mozilla/5.0")
        self.allowed_schemes = tuple(s.get("allowed_url_schemes", ("http", "https")))
        self.denied_schemes = tuple(s.get("denied_url_schemes", ()))
        self.block_private = bool(s.get("block_private_ip_targets", True))
        self.min_bytes = int(cfg.get_path("movies.min_playable_bytes", 2_000_000))
        self.probe_bytes = 4096

    def validate(self, movie) -> VodResult:
        result = VodResult(movie_id=movie.id, url=movie.playback_url)

        verdict = check_url(movie.playback_url, allowed_schemes=self.allowed_schemes,
                            denied_schemes=self.denied_schemes,
                            block_private=self.block_private)
        if not verdict.ok:
            result.status, result.error = "INVALID", verdict.reason
            return result

        req = urllib.request.Request(movie.playback_url, headers={
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Range": f"bytes=0-{self.probe_bytes - 1}",
            "Connection": "close",
        })
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                head = resp.read(self.probe_bytes)
                result.http_status = resp.status
                result.content_type = resp.headers.get("Content-Type", "")
                result.accepts_ranges = (
                    resp.status == 206
                    or (resp.headers.get("Accept-Ranges", "").lower() == "bytes")
                )
                # With a 206 the length header describes the slice, so read the
                # full size out of Content-Range instead.
                crange = resp.headers.get("Content-Range", "")
                if "/" in crange:
                    try:
                        result.content_length = int(crange.rsplit("/", 1)[1])
                    except ValueError:
                        pass
                if not result.content_length:
                    try:
                        result.content_length = int(resp.headers.get("Content-Length", 0))
                    except ValueError:
                        result.content_length = 0
        except urllib.error.HTTPError as exc:
            result.http_status = exc.code
            result.status, result.error = "UNREACHABLE", f"HTTP {exc.code} {exc.reason}"
            return result
        except Exception as exc:  # noqa: BLE001
            result.status = "UNREACHABLE"
            result.error = f"{type(exc).__name__}: {exc}"
            return result
        finally:
            result.latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if not _looks_like_video(head, result.content_type):
            result.status = "INVALID"
            result.error = (f"response is not video (content-type "
                            f"{result.content_type!r}, starts {head[:16]!r})")
            return result

        is_playlist = head.lstrip().startswith(b"#EXTM3U")
        if not is_playlist and result.content_length and result.content_length < self.min_bytes:
            result.status = "INVALID"
            result.error = (f"file is only {result.content_length} bytes; too small "
                            f"to be a feature")
            return result

        result.ok = True
        result.seekable = result.accepts_ranges or is_playlist
        if result.seekable:
            result.status = "PLAYABLE"
        else:
            result.status = "DEGRADED"
            result.error = "server does not advertise Range support; seeking will not work"
        return result

    def validate_all(self, movies) -> list[VodResult]:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(self.validate, movies))

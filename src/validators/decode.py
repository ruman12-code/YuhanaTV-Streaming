"""Decode a few seconds of a stream and see whether there is a picture in it.

Everything before this stage reasons about the stream from the outside: the
manifest parses, the segment is fetched and its bytes look like a container.
None of that proves a television will show anything. Two kinds of channel pass
every outside check and play as a black screen:

  * the container is real but carries no video stream at all, or video in a form
    the decoder cannot open;
  * the video decodes perfectly and every frame is black, because the encoder is
    pushing a dead feed.

A decoder is the only instrument that tells those from a working channel, so
this runs ffmpeg against the stream and reads what came out.

Cost and scope. A decode costs seconds and bandwidth, so it is spent where the
owner actually watches rather than across twelve thousand channels - see
`validation.deep_check_categories`. It is also strictly additive: when ffmpeg
is absent, every function here reports "not measured" and nothing is withheld
on its account.

Safety. ffmpeg is handed a URL from an aggregator, which is untrusted input.
Arguments are passed as a list and never through a shell, the protocol
whitelist excludes `file` so a crafted playlist cannot read the runner's disk,
output goes to the null muxer so nothing is ever written, and every call has a
hard wall-clock timeout.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field, asdict

# No `file`, `pipe` or `concat`: an HLS playlist can point its segments
# anywhere, and this one is not allowed to point at the filesystem.
_PROTOCOLS = "http,https,tcp,tls,crypto,httpproxy"

_BLACK_RE = re.compile(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)")
_FRAME_RE = re.compile(r"frame=\s*(\d+)")


@dataclass
class DecodeResult:
    """What a decoder made of the stream. `measured` False means ffmpeg could
    not be run at all, which is never held against a channel."""

    measured: bool = False
    has_video: bool = False
    has_audio: bool = False
    codec: str = ""
    width: int = 0
    height: int = 0
    frames: int = 0
    decoded_seconds: float = 0.0
    black_seconds: float = 0.0
    error: str = ""
    stages: list[str] = field(default_factory=list)

    @property
    def black_fraction(self) -> float:
        if self.decoded_seconds <= 0:
            return 0.0
        return min(1.0, self.black_seconds / self.decoded_seconds)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["black_fraction"] = round(self.black_fraction, 3)
        return d


def available() -> bool:
    """True when both binaries are present. Cached by the caller if it cares."""
    return bool(shutil.which("ffprobe")) and bool(shutil.which("ffmpeg"))


def _headers(referrer: str = "", agent: str = "") -> list[str]:
    args: list[str] = []
    if agent:
        args += ["-user_agent", agent]
    if referrer:
        args += ["-headers", f"Referer: {referrer}\r\n"]
    return args


def _run(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, check=False)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timed out"
    except OSError as exc:
        return -1, "", f"{type(exc).__name__}: {exc}"


def describe(url: str, *, timeout: float = 25.0, referrer: str = "",
             agent: str = "") -> DecodeResult:
    """Ask ffprobe what streams the container holds, without decoding."""
    r = DecodeResult()
    if not available():
        r.error = "ffmpeg not installed"
        return r
    # No -nostdin here: ffprobe does not have that option, and passing it made
    # ffprobe swallow the next argument and report every stream as videoless.
    # stdin is closed by the caller instead.
    cmd = ["ffprobe", "-v", "error", "-hide_banner",
           "-protocol_whitelist", _PROTOCOLS,
           "-rw_timeout", str(int(timeout * 1_000_000)),
           *_headers(referrer, agent),
           "-print_format", "json", "-show_streams", "-show_format", url]
    code, out, err = _run(cmd, timeout)
    r.measured = True
    r.stages.append(f"ffprobe rc={code}")
    if code != 0:
        r.error = (err or "ffprobe failed").strip().splitlines()[-1][:200]
        return r
    try:
        payload = json.loads(out or "{}")
    except ValueError:
        r.error = "ffprobe returned unparseable json"
        return r
    for st in payload.get("streams", []):
        kind = st.get("codec_type")
        if kind == "video" and not r.has_video:
            r.has_video = True
            r.codec = str(st.get("codec_name") or "")
            r.width = int(st.get("width") or 0)
            r.height = int(st.get("height") or 0)
        elif kind == "audio":
            r.has_audio = True
    if not r.has_video:
        r.error = "container carries no video stream"
    return r


def decode(url: str, *, seconds: float = 4.0, timeout: float = 45.0,
           referrer: str = "", agent: str = "") -> DecodeResult:
    """Decode `seconds` of the stream and report frames and black time.

    Decoding is what separates a channel with a picture from one that only
    looks like it has one. `-f null -` means every frame is decoded and
    discarded, so nothing is written and no codec output is kept.
    """
    r = describe(url, timeout=min(timeout, 25.0), referrer=referrer, agent=agent)
    if not r.measured or not r.has_video:
        return r

    cmd = ["ffmpeg", "-v", "info", "-hide_banner", "-nostdin",
           "-protocol_whitelist", _PROTOCOLS,
           "-rw_timeout", str(int(timeout * 1_000_000)),
           *_headers(referrer, agent),
           "-i", url,
           "-t", str(seconds),
           "-an", "-sn", "-dn",
           # pix=0.00 so a frame counts as black only when essentially every
           # pixel is; a dark scene in a film is not a dead feed.
           "-vf", "blackdetect=d=0.1:pix_th=0.10",
           "-f", "null", "-"]
    code, _, err = _run(cmd, timeout)
    r.stages.append(f"ffmpeg rc={code}")

    frames = _FRAME_RE.findall(err or "")
    r.frames = int(frames[-1]) if frames else 0
    r.black_seconds = sum(float(m[2]) for m in _BLACK_RE.findall(err or ""))

    # What the decoder actually got through, which is not always what we asked
    # for: a stalled origin yields a fraction of a second.
    m = re.findall(r"time=(\d+):(\d+):([\d.]+)", err or "")
    if m:
        h, mi, s = m[-1]
        r.decoded_seconds = int(h) * 3600 + int(mi) * 60 + float(s)
    elif r.frames:
        r.decoded_seconds = seconds

    if code != 0 and r.frames == 0:
        r.error = (err or "decode failed").strip().splitlines()[-1][:200]
    elif r.frames == 0:
        r.error = "no frame could be decoded"
    return r


def verdict(r: DecodeResult, *, min_frames: int = 10,
            max_black_fraction: float = 0.95) -> str:
    """'' when the channel is fine or was not measured, else why it is not.

    Silence is the default on purpose: a decode that could not be taken is not
    evidence against a channel, and this gate only ever speaks when a decoder
    actually looked and found nothing worth watching.
    """
    if not r.measured:
        return ""
    if not r.has_video:
        return "no video stream in the container (plays as a black screen)"
    if r.error and r.frames == 0:
        return f"no frame could be decoded ({r.error})"
    if r.frames < min_frames:
        return (f"only {r.frames} frame(s) decoded in "
                f"{r.decoded_seconds:.1f}s - the feed is stalling")
    if r.black_fraction >= max_black_fraction:
        return (f"{r.black_fraction:.0%} of the decoded video is black - "
                f"the feed is dead behind a working stream")
    return ""

"""Live stream validator (spec section 10).

Stages, each reported separately so a failure is diagnosable rather than a
generic "offline":

    1. url_safety   - scheme/host allow-list (src/util/urls.py)
    2. dns          - name resolves
    3. connect/tls  - TCP + TLS handshake reaches the origin
    4. http         - status code after following redirects
    5. manifest     - body really is an HLS/DASH manifest
    6. variants     - #EXT-X-STREAM-INF RESOLUTION/BANDWIDTH/CODECS, when present
    7. segment      - the first media segment is actually fetchable

Classification:
    ACTIVE    - manifest valid and (if probed) a segment was fetchable
    DEGRADED  - manifest valid but slow, or the segment probe failed/was skipped
    OFFLINE   - DNS/connect/HTTP failure, or an empty/stale manifest
    INVALID   - the URL is unusable or the body is not a manifest at all

Politeness: requests are serialised per host with a delay, and the whole run is
bounded by a global concurrency cap, so we do not hammer a source (spec 10).

Nothing here ever *claims* a resolution. `resolution` is only set from a value
the manifest actually declared.
"""

from __future__ import annotations

import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from ..util.urls import check_url, resolves, host_of, absolutise

_STREAM_INF = re.compile(r"^#EXT-X-STREAM-INF:(.*)$", re.MULTILINE)
_ATTR = re.compile(r'([A-Z0-9-]+)=("([^"]*)"|[^,]*)')
_RESOLUTION = re.compile(r"^(\d{2,5})x(\d{2,5})$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# An MPEG-TS packet is 188 bytes and starts with the sync byte 0x47.
_TS_PACKET = 188
_TS_SYNC = 0x47


def looks_like_mpegts(body: bytes) -> bool:
    """True if the bytes are a raw MPEG transport stream rather than a playlist.

    Several channels are served as a continuous HTTP TS stream with no manifest at
    all. SS IPTV plays those, so reporting them INVALID because they are not HLS
    would be the validator's mistake. Three consecutive sync bytes at the packet
    stride is the standard test and is not plausibly a coincidence.
    """
    if len(body) < _TS_PACKET * 3:
        return False
    return all(body[i * _TS_PACKET] == _TS_SYNC for i in range(3))


def resolution_label(width: int, height: int) -> str:
    """Marketing label derived ONLY from measured pixels (spec sections 7 and 9)."""
    if height >= 2000 or width >= 3600:
        return "4K"
    if height >= 1000:
        return "1080p"
    if height >= 700:
        return "720p"
    if height >= 560:
        return "576p"
    if height >= 400:
        return "480p"
    if height > 0:
        return f"{height}p"
    return ""


@dataclass
class StageResult:
    name: str
    ok: bool
    detail: str = ""
    ms: float = 0.0


@dataclass
class ValidationResult:
    channel_id: str
    url: str
    status: str = "OFFLINE"
    http_status: int = 0
    final_url: str = ""
    redirects: int = 0
    latency_ms: float = 0.0
    content_type: str = ""
    manifest_kind: str = ""          # master | media | dash | ts | none
    stream_kind_note: str = ""
    variant_count: int = 0
    width: int = 0
    height: int = 0
    resolution: str = ""
    resolution_label: str = ""
    codec: str = ""
    bitrate_bps: int = 0
    variant_url: str = ""
    segment_ok: bool | None = None
    error: str = ""
    checked_at: str = field(default_factory=_now_iso)
    stages: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class StreamValidator:
    def __init__(self, cfg) -> None:
        v = cfg.get_path("validation", {})
        s = cfg.get_path("security", {})
        self.connect_timeout = float(v.get("timeout_connect_seconds", 8))
        self.read_timeout = float(v.get("timeout_read_seconds", 12))
        self.max_redirects = int(v.get("max_redirects", 5))
        self.retries = int(v.get("retries", 2))
        self.backoff = list(v.get("retry_backoff_seconds", [2, 5]))
        self.concurrency = int(v.get("concurrency", 8))
        self.per_host_delay = float(v.get("per_host_delay_seconds", 1.0))
        self.check_segment = bool(v.get("check_first_segment", True))
        self.segment_bytes = int(v.get("segment_probe_bytes", 65536))
        self.degraded_ms = float(v.get("degraded_latency_ms", 4000))
        self.user_agent = v.get("user_agent", "Mozilla/5.0")
        self.max_manifest_bytes = int(s.get("max_manifest_bytes", 2 * 1024 * 1024))
        self.allowed_schemes = tuple(s.get("allowed_url_schemes", ("http", "https")))
        self.denied_schemes = tuple(s.get("denied_url_schemes", ()))
        self.block_private = bool(s.get("block_private_ip_targets", True))

        self._host_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._host_last: dict[str, float] = defaultdict(float)

        # TLS: verify normally. Many IPTV origins have imperfect chains; those are
        # reported as failures rather than silently trusted.
        self._ssl_ctx = ssl.create_default_context()

    # --- low level -----------------------------------------------------------

    def _throttle(self, host: str) -> None:
        with self._host_locks[host]:
            wait = self._host_last[host] + self.per_host_delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._host_last[host] = time.monotonic()

    def _fetch(self, url: str, *, max_bytes: int, referrer: str = "", agent: str = "",
               range_bytes: int = 0):
        """Return (http_status, final_url, headers, body_bytes, redirect_count)."""
        recorder = _RedirectRecorder()
        recorder.max_redirections = self.max_redirects
        opener = urllib.request.build_opener(
            recorder, urllib.request.HTTPSHandler(context=self._ssl_ctx)
        )
        headers = {
            "User-Agent": agent or self.user_agent,
            "Accept": "*/*",
            "Connection": "close",
        }
        if referrer:
            headers["Referer"] = referrer
        if range_bytes:
            headers["Range"] = f"bytes=0-{range_bytes - 1}"

        req = urllib.request.Request(url, headers=headers, method="GET")
        with opener.open(req, timeout=self.read_timeout) as resp:
            body = resp.read(max_bytes + 1)
            return resp.status, resp.geturl(), dict(resp.headers), body, recorder.count

    def _fetch_retry(self, url: str, *, max_bytes: int, referrer: str = "",
                     agent: str = "", range_bytes: int = 0, attempts: int = 2):
        """_fetch with a retry. Sub-resources need it as much as the manifest does:
        each one opens a fresh TLS session, and an origin with an unstable chain
        fails some handshakes while serving the very next request fine."""
        last = None
        for attempt in range(max(1, attempts)):
            if attempt:
                time.sleep(self.backoff[min(attempt - 1, len(self.backoff) - 1)])
            try:
                self._throttle(host_of(url))
                return self._fetch(url, max_bytes=max_bytes, referrer=referrer,
                                   agent=agent, range_bytes=range_bytes)
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise last

    # --- manifest analysis ---------------------------------------------------

    def _analyse_manifest(self, text: str, base_url: str, result: ValidationResult) -> str:
        # str.lstrip() does not remove U+FEFF, so name the characters explicitly.
        head = text.lstrip("\ufeff \t\r\n")
        if head.startswith("<?xml") and "MPD" in head[:400]:
            result.manifest_kind = "dash"
            return ""
        if not head.startswith("#EXTM3U"):
            return "body is not an HLS manifest (no #EXTM3U)"

        # Collect each variant's attributes together with the URI on the next
        # non-comment line, so the best variant can be followed to a real segment.
        variants = []
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF:"):
                continue
            attrs = {}
            for m in _ATTR.finditer(line[len("#EXT-X-STREAM-INF:"):]):
                key = m.group(1)
                val = m.group(3) if m.group(3) is not None else m.group(2)
                attrs[key] = val.strip().strip('"')
            uri = ""
            for follower in lines[idx + 1:]:
                follower = follower.strip()
                if follower and not follower.startswith("#"):
                    uri = follower
                    break
            attrs["__uri"] = uri
            variants.append(attrs)

        if variants:
            result.manifest_kind = "master"
            result.variant_count = len(variants)
            best = None
            best_px = -1
            for attrs in variants:
                res = attrs.get("RESOLUTION", "")
                m = _RESOLUTION.match(res)
                px = (int(m.group(1)) * int(m.group(2))) if m else -1
                if px > best_px:
                    best_px, best = px, attrs
            if best:
                m = _RESOLUTION.match(best.get("RESOLUTION", ""))
                if m:
                    result.width, result.height = int(m.group(1)), int(m.group(2))
                    result.resolution = f"{result.width}x{result.height}"
                    result.resolution_label = resolution_label(result.width, result.height)
                result.codec = best.get("CODECS", "")
                if best.get("__uri"):
                    result.variant_url = absolutise(base_url, best["__uri"])
                try:
                    result.bitrate_bps = int(best.get("BANDWIDTH", "0") or 0)
                except ValueError:
                    result.bitrate_bps = 0
            return ""

        # A media playlist: must carry at least one segment.
        if "#EXTINF" in text:
            result.manifest_kind = "media"
            return ""
        return "HLS manifest contains neither variants nor segments"

    def _probe_segment_url(self, text: str, base_url: str) -> str:
        """Pick the segment to probe from a media playlist.

        The LAST listed segment, not the first. A live HLS playlist is a sliding
        window a few segments wide: the oldest entry expires within seconds, so
        probing it produces a 404 that says nothing about the channel's health —
        only about how long we queued. The newest segment is the one the origin
        is certainly still serving, and is what a player would load on join.
        """
        segments = [
            line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not segments:
            return ""
        # An ENDLIST playlist is VOD: it does not slide, so the first entry is
        # the one a player actually starts with.
        newest = segments[0] if "#EXT-X-ENDLIST" in text else segments[-1]
        return absolutise(base_url, newest)

    # --- public --------------------------------------------------------------

    def validate(self, channel) -> ValidationResult:
        result = ValidationResult(channel_id=channel.id, url=channel.stream_url)
        stages: list[StageResult] = []

        verdict = check_url(
            channel.stream_url,
            allowed_schemes=self.allowed_schemes,
            denied_schemes=self.denied_schemes,
            block_private=self.block_private,
        )
        stages.append(StageResult("url_safety", verdict.ok, verdict.reason))
        if not verdict.ok:
            result.status, result.error = "INVALID", verdict.reason
            result.stages = [asdict(s) for s in stages]
            return result

        host = host_of(channel.stream_url)
        t0 = time.monotonic()
        dns_ok, dns_detail = resolves(host, timeout=self.connect_timeout)
        stages.append(StageResult("dns", dns_ok, dns_detail, (time.monotonic() - t0) * 1000))
        if not dns_ok:
            result.status, result.error = "OFFLINE", f"DNS failure: {dns_detail}"
            result.stages = [asdict(s) for s in stages]
            return result

        body = b""
        last_error = ""
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(self.backoff[min(attempt - 1, len(self.backoff) - 1)])
            self._throttle(host)
            t0 = time.monotonic()
            try:
                status, final_url, headers, body, redirects = self._fetch(
                    channel.stream_url,
                    max_bytes=self.max_manifest_bytes,
                    referrer=channel.http_referrer,
                    agent=channel.http_user_agent,
                )
                result.latency_ms = round((time.monotonic() - t0) * 1000, 1)
                result.http_status = status
                result.final_url = final_url
                result.redirects = redirects
                result.content_type = headers.get("Content-Type", "")
                last_error = ""
                break
            except urllib.error.HTTPError as exc:
                result.http_status = exc.code
                last_error = f"HTTP {exc.code} {exc.reason}"
            except ssl.SSLError as exc:
                last_error = f"TLS error: {exc}"
            except urllib.error.URLError as exc:
                last_error = f"connection error: {exc.reason}"
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"

        stages.append(StageResult("http", not last_error, last_error or f"HTTP {result.http_status}",
                                  result.latency_ms))
        if last_error:
            result.status, result.error = "OFFLINE", last_error
            result.stages = [asdict(s) for s in stages]
            return result

        # A direct transport stream, not a playlist. Detected before the size
        # check, because an endless TS stream is exactly what overruns it.
        if looks_like_mpegts(body) or "mp2t" in result.content_type.lower():
            result.manifest_kind = "ts"
            result.stream_kind_note = "direct MPEG-TS stream (no manifest)"
            stages.append(StageResult("manifest", True,
                                      f"MPEG-TS, {len(body)} bytes read"))
            result.status = ("DEGRADED" if result.latency_ms > self.degraded_ms
                             else "ACTIVE")
            if result.status == "DEGRADED":
                result.error = f"slow origin ({result.latency_ms:.0f} ms)"
            result.stages = [asdict(s) for s in stages]
            return result

        if len(body) > self.max_manifest_bytes:
            result.status = "INVALID"
            result.error = ("response exceeds the manifest size limit and is not a "
                            "transport stream either")
            stages.append(StageResult("manifest", False, result.error))
            result.stages = [asdict(s) for s in stages]
            return result

        # utf-8-sig: some origins prepend a BOM. Refusing those as "not a
        # manifest" would be our bug, not theirs.
        text = body.decode("utf-8-sig", errors="replace")
        manifest_error = self._analyse_manifest(text, result.final_url or channel.stream_url, result)
        stages.append(StageResult("manifest", not manifest_error,
                                  manifest_error or f"{result.manifest_kind} "
                                                    f"({result.variant_count} variants)"))
        if manifest_error:
            result.status, result.error = "INVALID", manifest_error
            result.stages = [asdict(s) for s in stages]
            return result

        # Segment probe. For a master playlist, follow the highest-resolution
        # variant first: a reachable master with a dead variant is a black screen
        # on the TV, and must not be reported as ACTIVE.
        probe_text, probe_base = text, (result.final_url or channel.stream_url)
        if self.check_segment and result.manifest_kind == "master" and result.variant_url:
            try:
                vs, vfinal, _, vbody, _ = self._fetch_retry(
                    result.variant_url, max_bytes=self.max_manifest_bytes,
                    referrer=channel.http_referrer, agent=channel.http_user_agent,
                )
                # Tolerate a BOM and leading blank lines before #EXTM3U.
                probe_text = vbody.decode("utf-8-sig", errors="replace")
                probe_base = vfinal
                head = probe_text.lstrip("\ufeff \t\r\n")
                variant_ok = vs == 200 and head.startswith("#EXTM3U")
                detail = f"HTTP {vs}"
                if not variant_ok:
                    detail += f", body starts {head[:60]!r}"
                stages.append(StageResult("variant", variant_ok, detail))
                if not variant_ok:
                    result.status = "DEGRADED"
                    result.error = "master manifest served but its variant playlist was not usable"
                    result.stages = [asdict(s) for s in stages]
                    return result
            except Exception as exc:  # noqa: BLE001
                stages.append(StageResult("variant", False, f"{type(exc).__name__}: {exc}"))
                result.status = "DEGRADED"
                result.error = f"variant playlist unreachable: {exc}"
                result.stages = [asdict(s) for s in stages]
                return result

        if self.check_segment and result.manifest_kind in ("media", "master"):
            seg_url = self._probe_segment_url(probe_text, probe_base)
            if seg_url:
                t0 = time.monotonic()
                try:
                    st, _, _, seg_body, _ = self._fetch_retry(
                        seg_url, max_bytes=self.segment_bytes,
                        referrer=channel.http_referrer, agent=channel.http_user_agent,
                        range_bytes=self.segment_bytes,
                    )
                    result.segment_ok = st in (200, 206) and len(seg_body) > 0
                    stages.append(StageResult("segment", bool(result.segment_ok),
                                              f"HTTP {st}, {len(seg_body)} bytes",
                                              (time.monotonic() - t0) * 1000))
                except Exception as exc:  # noqa: BLE001
                    result.segment_ok = False
                    stages.append(StageResult("segment", False, f"{type(exc).__name__}: {exc}"))

        if result.segment_ok is False:
            result.status = "DEGRADED"
            result.error = "manifest served but the first segment was not fetchable"
        elif result.latency_ms > self.degraded_ms:
            result.status = "DEGRADED"
            result.error = f"slow origin ({result.latency_ms:.0f} ms)"
        else:
            result.status = "ACTIVE"

        result.stages = [asdict(s) for s in stages]
        return result

    def validate_all(self, channels) -> list[ValidationResult]:
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            return list(pool.map(self.validate, channels))

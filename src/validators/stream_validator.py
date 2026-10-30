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
from . import decode as _decode

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


def _is_cert_failure(exc: BaseException) -> bool:
    """True when the failure was an untrusted certificate, however it arrived.

    urllib does not raise ssl.SSLCertVerificationError to its caller: the
    handshake error is an OSError, and AbstractHTTPHandler wraps every OSError
    in URLError. So an `except ssl.SSLCertVerificationError` around urlopen is
    unreachable. That is exactly how the TLS fallback the owner approved never
    ran once - 220 channels sat there failing with CERTIFICATE_VERIFY_FAILED in
    their recorded reason while the code meant to rescue them waited for an
    exception that was never going to come.
    """
    seen = 0
    node = exc
    while node is not None and seen < 6:
        if isinstance(node, ssl.SSLCertVerificationError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(node):
            return True
        if not isinstance(node, BaseException):
            # URLError.reason is sometimes a plain string; it has been read.
            return False
        node = (getattr(node, "reason", None) or node.__cause__ or node.__context__)
        seen += 1
    return False


def _http_twin(url: str) -> str:
    """The plain-http form of an https URL, or '' if there is none."""
    return "http://" + url[len("https://"):] if url.lower().startswith("https://") else ""


def looks_like_media_segment(body: bytes) -> bool:
    """True if these bytes are decodable media rather than something else.

    The segment probe used to accept any non-empty body with a 200. An origin
    that answers every path with an HTML error page, a JSON blob or a "stream
    offline" placeholder therefore passed, and the channel reached the TV as a
    black screen. Two containers are legitimate here: MPEG-TS, and fragmented
    MP4, which HLS uses for its CMAF variants.
    """
    if len(body) < 8:
        return False
    if looks_like_mpegts(body):
        return True
    # fMP4: a box header in the first few bytes - styp for a segment, ftyp,
    # moof for the fragment itself, sidx/moov/init for an init segment.
    head = body[:64]
    for box in (b"styp", b"ftyp", b"moof", b"sidx", b"moov", b"emsg"):
        if box in head:
            return True
    # ID3 can prefix an AAC-in-HLS segment, and ADTS is a bare AAC frame.
    if head.startswith(b"ID3") or (body[0] == 0xFF and (body[1] & 0xF0) == 0xF0):
        return True
    return False


_MEDIA_SEQUENCE = re.compile(r"^#EXT-X-MEDIA-SEQUENCE:\s*(\d+)", re.MULTILINE)


_TARGET_DURATION = re.compile(r"^#EXT-X-TARGETDURATION:\s*([\d.]+)", re.MULTILINE)


def _target_duration(text: str) -> float:
    """The playlist's own segment length, or 0 when it does not say.

    The liveness check has to wait longer than this or the window has not had
    time to move and a perfectly good channel reads as stalled.
    """
    m = _TARGET_DURATION.search(text or "")
    try:
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return 0.0


def live_edge_token(text: str) -> str:
    """A fingerprint of where a live playlist's window currently sits.

    A dead encoder behind a live CDN is the other way a channel becomes a black
    screen: the playlist is served, the segments fetch, and the window never
    moves, so the player buffers forever with nothing to show. Comparing this
    token across two fetches a few seconds apart tells the two apart. VOD is
    exempt - an ENDLIST playlist is not supposed to move.
    """
    m = _MEDIA_SEQUENCE.search(text or "")
    seq = m.group(1) if m else ""
    segs = [ln.strip() for ln in (text or "").splitlines()
            if ln.strip() and not ln.startswith("#")]
    return f"{seq}|{segs[-1] if segs else ''}|{len(segs)}"


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
    # True when the origin answered only after certificate verification was
    # skipped. Recorded per channel rather than hidden, so the report can say
    # exactly which streams are on an untrusted chain.
    tls_unverified: bool = False
    # Liveness of a live playlist: whether it was checked at all, and whether
    # its window had moved when re-read a few seconds later.
    live_checked: bool = False
    live_advancing: bool | None = None
    segment_not_media: bool = False
    # Plain-http form of an https stream whose certificate is untrusted, when
    # the origin serves the same object in the clear.
    http_twin: str = ""
    segment_http_status: int = 0
    # Decoder findings. decode_measured False means ffmpeg was never run, which
    # is never held against a channel.
    decode_measured: bool = False
    video_confirmed: bool = False
    black_fraction: float = 0.0
    decode_note: str = ""
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

        # Set by _fetch_retry for the call that just returned. Read immediately
        # after, on the same thread, before any other fetch on this validator.
        self._tls_local = threading.local()
        self._host_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._host_last: dict[str, float] = defaultdict(float)

        # TLS: verify first, always.
        self._ssl_ctx = ssl.create_default_context()
        # Then, only for a certificate-verification failure, probe again without
        # verification and record that we did.
        #
        # The reason is consistency, not indifference. This playlist already
        # carries 902 plain-http streams, which have no certificate and no
        # encryption at all, and the owner's own working playlist was 31% http.
        # Refusing an https stream because its chain is untrusted, while
        # publishing an unencrypted one beside it, withheld 195 channels on a
        # standard the rest of the list was never held to. The residual risk -
        # an interposed server serving different video - is identical for every
        # plain-http channel already published, and these streams carry no
        # credentials, because a URL that does is excluded outright.
        #
        # This is a fallback, never the first attempt: a channel that verifies
        # normally is never probed this way, and the exception is recorded per
        # channel rather than turned into a blanket setting.
        self.tls_fallback = bool(v.get("allow_unverified_tls_fallback", True))
        # The liveness re-read costs a few seconds per channel, so it is spent
        # where the owner actually watches rather than across twelve thousand
        # channels. Everything still gets the segment-content check, which is free.
        self.live_edge_wait = float(v.get("live_edge_wait_seconds", 6))
        self.live_edge_wait_max = float(v.get("live_edge_wait_max_seconds", 14))
        self.deep_categories = {str(c).lower() for c in
                                (v.get("deep_check_categories") or ())}
        self.deep_sources = {str(c) for c in (v.get("deep_check_sources") or ())}
        # Decoding is the only check that proves a television will show
        # something. It is also the most expensive, so it is bounded.
        self.decode_enabled = bool(v.get("decode_check", True))
        self.decode_seconds = float(v.get("decode_seconds", 4))
        self.decode_timeout = float(v.get("decode_timeout_seconds", 45))
        self.decode_min_frames = int(v.get("decode_min_frames", 10))
        self.decode_max_black = float(v.get("decode_max_black_fraction", 0.95))
        self._decode_ids: set[str] = set()

    def set_decode_set(self, ids) -> None:
        """Channels this run may decode, chosen by the caller.

        The caller owns the choice because it can see the whole registry: the
        categories the owner watches, plus a rotating slice of everything whose
        video has never been confirmed, so that backlog clears over a few runs
        instead of never.
        """
        self._decode_ids = {str(i) for i in ids}

    def _should_decode(self, channel) -> bool:
        if not self.decode_enabled or not _decode.available():
            return False
        return channel.id in self._decode_ids or self.deep_check(channel)
        self._ssl_ctx_unverified = ssl.create_default_context()
        self._ssl_ctx_unverified.check_hostname = False
        self._ssl_ctx_unverified.verify_mode = ssl.CERT_NONE

    def deep_check(self, channel) -> bool:
        """Whether this channel earns the slower liveness re-read."""
        return ((channel.category or "").lower() in self.deep_categories
                or (channel.source or "") in self.deep_sources)

    # --- low level -----------------------------------------------------------

    def _throttle(self, host: str) -> None:
        with self._host_locks[host]:
            wait = self._host_last[host] + self.per_host_delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._host_last[host] = time.monotonic()

    def _fetch(self, url: str, *, max_bytes: int, referrer: str = "", agent: str = "",
               range_bytes: int = 0, verify_tls: bool = True):
        """Return (http_status, final_url, headers, body_bytes, redirect_count)."""
        recorder = _RedirectRecorder()
        recorder.max_redirections = self.max_redirects
        ctx = self._ssl_ctx if verify_tls else self._ssl_ctx_unverified
        opener = urllib.request.build_opener(
            recorder, urllib.request.HTTPSHandler(context=ctx)
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
        verify = True
        for attempt in range(max(1, attempts)):
            if attempt:
                time.sleep(self.backoff[min(attempt - 1, len(self.backoff) - 1)])
            try:
                self._throttle(host_of(url))
                body = self._fetch(url, max_bytes=max_bytes, referrer=referrer,
                                   agent=agent, range_bytes=range_bytes,
                                   verify_tls=verify)
                self._tls_local.unverified = not verify
                return body
            except Exception as exc:  # noqa: BLE001
                last = exc
                if self.tls_fallback and verify and _is_cert_failure(exc):
                    # Untrusted chain, not an unreachable host. Try once more
                    # without verification; see __init__ for why.
                    verify = False
                    attempts = max(attempts, attempt + 2)
        self._tls_local.unverified = False
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
        verify_tls = True
        attempts_allowed = self.retries + 1
        attempt = -1
        while True:
            attempt += 1
            if attempt >= attempts_allowed:
                break
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
                    verify_tls=verify_tls,
                )
                result.tls_unverified = not verify_tls
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
            except Exception as exc:  # noqa: BLE001
                # Certificate failures arrive wrapped in URLError, so they are
                # recognised by inspection rather than by exception type.
                if _is_cert_failure(exc):
                    last_error = f"TLS certificate not trusted: {exc}"
                    if self.tls_fallback and verify_tls:
                        verify_tls = False
                        attempts_allowed = max(attempts_allowed, attempt + 2)
                elif isinstance(exc, ssl.SSLError):
                    last_error = f"TLS error: {exc}"
                elif isinstance(exc, urllib.error.URLError):
                    last_error = f"connection error: {exc.reason}"
                else:
                    last_error = f"{type(exc).__name__}: {exc}"
                continue

        if not last_error and result.tls_unverified:
            twin = _http_twin(channel.stream_url)
            if twin:
                try:
                    self._throttle(host)
                    tst, tfinal, _, tbody, _ = self._fetch(
                        twin, max_bytes=self.max_manifest_bytes,
                        referrer=channel.http_referrer, agent=channel.http_user_agent)
                    head = tbody.lstrip(b"\xef\xbb\xbf \t\r\n")[:7]
                    if tst == 200 and (head.startswith(b"#EXTM3U") or looks_like_mpegts(tbody)):
                        # Served in the clear on the same path. Publish that:
                        # it plays on a validating player where the https
                        # URL, with its untrusted chain, would not.
                        result.http_twin = twin
                        stages.append(StageResult("http_twin", True,
                                                  "same stream served over plain http"))
                except Exception:                              # noqa: BLE001
                    pass

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
        probe_started = time.monotonic()
        deep = self.deep_check(channel)
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
                    fetched = st in (200, 206) and len(seg_body) > 0
                    is_media = fetched and looks_like_media_segment(seg_body)
                    if fetched and not is_media:
                        # Some origins mishandle a Range request and answer with
                        # a redirect body, a padded response or the playlist
                        # again. Ask once more for the plain object before
                        # concluding anything about the stream: this sniff was
                        # rejecting ATN News and Nexus TV, both of which ffmpeg
                        # decodes without complaint.
                        try:
                            st2, _, _, body2, _ = self._fetch_retry(
                                seg_url, max_bytes=self.segment_bytes,
                                referrer=channel.http_referrer,
                                agent=channel.http_user_agent, attempts=1)
                            if st2 in (200, 206) and looks_like_media_segment(body2):
                                is_media, seg_body = True, body2
                        except Exception:                      # noqa: BLE001
                            pass
                    result.segment_ok = bool(is_media)
                    if fetched and not is_media:
                        # An origin that answers every path with an HTML error
                        # page or a placeholder passed the old check and reached
                        # the TV as a black screen.
                        result.segment_not_media = True
                        detail = (f"HTTP {st}, {len(seg_body)} bytes but not a "
                                  f"media segment ({seg_body[:16]!r})")
                    else:
                        detail = f"HTTP {st}, {len(seg_body)} bytes"
                    stages.append(StageResult("segment", bool(result.segment_ok),
                                              detail, (time.monotonic() - t0) * 1000))
                except Exception as exc:  # noqa: BLE001
                    result.segment_ok = False
                    result.segment_http_status = int(getattr(exc, "code", 0) or 0)
                    stages.append(StageResult("segment", False, f"{type(exc).__name__}: {exc}"))

        # Liveness: a dead encoder behind a live CDN serves a playlist whose
        # window never moves. Everything above passes and the TV shows black.
        will_decode = self._should_decode(channel)
        if (deep and not will_decode and result.segment_ok
                and result.manifest_kind in ("media", "master")
                and "#EXT-X-ENDLIST" not in probe_text):
            result.live_checked = True
            before = live_edge_token(probe_text)
            # Wait longer than the playlist's own segment length, or the window
            # legitimately has not moved yet and a working channel reads as
            # stalled. Six seconds against a ten-second target duration was
            # exactly that mistake.
            target = _target_duration(probe_text)
            need = max(self.live_edge_wait, target + 1.5)
            waited = need - (time.monotonic() - probe_started)
            if waited > 0:
                time.sleep(min(waited, self.live_edge_wait_max))
            try:
                _, _, _, again, _ = self._fetch_retry(
                    probe_base, max_bytes=self.max_manifest_bytes,
                    referrer=channel.http_referrer, agent=channel.http_user_agent,
                    attempts=1)
                after = live_edge_token(again.decode("utf-8-sig", errors="replace"))
                result.live_advancing = after != before
            except Exception:                              # noqa: BLE001
                # Could not re-read it; say nothing rather than condemn.
                result.live_checked = False

        # Decode. Everything above reasons about the stream from the outside;
        # only a decoder can say whether there is a picture in it. A container
        # with no video stream, and one whose every frame is black, both pass
        # every earlier check and play as a black screen.
        # Run even when the segment sniff failed. That sniff reads 64KB of one
        # object and guesses; the decoder opens the stream and counts frames.
        # Letting the guess suppress the measurement is how ATN News - 96 of 96
        # probes good, video confirmed, 720p - ended up off the Bangladesh
        # screen.
        if self._should_decode(channel):
            d = _decode.decode(result.final_url or channel.stream_url,
                               seconds=self.decode_seconds,
                               timeout=self.decode_timeout,
                               referrer=channel.http_referrer,
                               agent=channel.http_user_agent)
            result.decode_measured = d.measured
            if d.measured:
                result.video_confirmed = bool(d.has_video and d.frames)
                result.black_fraction = round(d.black_fraction, 3)
                # A decoded size is measured; a manifest's RESOLUTION is a claim.
                if d.width and d.height:
                    result.width, result.height = d.width, d.height
                    result.resolution = f"{d.width}x{d.height}"
                    result.resolution_label = resolution_label(d.width, d.height)
                if d.codec and not result.codec:
                    result.codec = d.codec
                note = _decode.verdict(d, min_frames=self.decode_min_frames,
                                       max_black_fraction=self.decode_max_black)
                # A decoder that could not get in is, by itself, the runner
                # failing. But if the segment probe ALSO got a 404 for the same
                # stream, two independent instruments agree the media is not
                # there - which is a finding, not a failure to take one. Only a
                # 404 or 410 counts: a 403 is the refusal a geo-fence gives, and
                # treating that as proof would pull Bangladeshi channels for
                # being watched from the wrong continent.
                if (note.startswith("no frame could be decoded")
                        and result.segment_ok is False
                        and result.segment_http_status in (404, 410)):
                    note = ("segment returned 404 and the stream would not open "
                            "in a decoder - two independent checks agree the "
                            "media is gone")
                result.decode_note = note
                stages.append(StageResult("decode", not note,
                                          note or f"{d.frames} frames, "
                                                  f"{d.black_fraction:.0%} black"))

        if result.decode_measured:
            # A decoder looked. Its answer is the answer: either it got a
            # watchable picture out of the stream or it said why not. The
            # outside checks below are proxies for this measurement and must
            # not be allowed to override it - DBC News and Ekushe TV both
            # decoded real 720p and 1080p frames and were still being pulled
            # by the liveness heuristic, which is a CDN serving a cached
            # playlist twice, not a dead encoder.
            if result.decode_note:
                result.status = "DEGRADED"
                result.error = result.decode_note
            elif result.latency_ms > self.degraded_ms:
                result.status = "DEGRADED"
                result.error = f"slow origin ({result.latency_ms:.0f} ms)"
            else:
                result.status = "ACTIVE"
            result.stages = [asdict(s) for s in stages]
            return result

        if result.segment_ok is False:
            result.status = "DEGRADED"
            result.error = (
                "manifest and segment served but the segment is not media "
                "(an error page or placeholder, which plays as a black screen)"
                if getattr(result, "segment_not_media", False)
                else "manifest served but the first segment was not fetchable")
        elif result.live_checked and result.live_advancing is False:
            result.status = "DEGRADED"
            result.error = ("live playlist is not advancing: the window did not "
                            "move, so the encoder is down behind a CDN that is "
                            "still serving - this plays as a black screen")
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

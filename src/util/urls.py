"""URL safety and normalisation (spec section 27).

Everything that ever reaches an M3U or the validator passes through `check_url`.
The rules are deliberately conservative: an unknown scheme is a rejection, not a
warning, because SS IPTV runs on a TV where a bad URL is invisible to the user.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit, quote

# Schemes a TV player can meaningfully open, and schemes that are outright dangerous.
DEFAULT_ALLOWED = ("http", "https")
DEFAULT_DENIED = (
    "javascript", "data", "file", "vbscript", "about", "blob",
    "ftp", "smb", "gopher", "ws", "wss",
)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_PATH = re.compile(r"^[a-zA-Z]:[\\/]")

# Extensions we refuse to treat as playable media.
_EXECUTABLE_SUFFIXES = (
    ".exe", ".msi", ".apk", ".dmg", ".sh", ".bat", ".cmd", ".ps1",
    ".jar", ".scr", ".com", ".deb", ".rpm",
)


@dataclass(frozen=True)
class UrlVerdict:
    ok: bool
    url: str
    reason: str = ""

    def __bool__(self) -> bool:  # allows `if check_url(...):`
        return self.ok


def _is_private_host(host: str) -> bool:
    """True if the host is a literal private/loopback/link-local address.

    DNS names are *not* resolved here: resolution belongs to the validator, which
    has a timeout budget. This only blocks the obvious `http://192.168.1.10/...`
    and `http://[::1]/...` cases that would let a playlist probe a home LAN.
    """
    candidate = host.strip("[]")
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def check_url(
    raw: str,
    *,
    allowed_schemes=DEFAULT_ALLOWED,
    denied_schemes=DEFAULT_DENIED,
    block_private: bool = True,
    allow_executables: bool = False,
) -> UrlVerdict:
    if raw is None:
        return UrlVerdict(False, "", "url is null")
    url = raw.strip()
    if not url:
        return UrlVerdict(False, "", "url is empty")
    if _CONTROL_CHARS.search(url):
        return UrlVerdict(False, url, "url contains control characters")
    if _WINDOWS_PATH.match(url) or url.startswith(("/", "./", "../", "\\\\")):
        return UrlVerdict(False, url, "url is a local filesystem path")

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if not scheme:
        return UrlVerdict(False, url, "url has no scheme")
    if scheme in {s.lower() for s in denied_schemes}:
        return UrlVerdict(False, url, f"scheme '{scheme}' is denied")
    if scheme not in {s.lower() for s in allowed_schemes}:
        return UrlVerdict(False, url, f"scheme '{scheme}' is not in the allow-list")
    if not parts.netloc:
        return UrlVerdict(False, url, "url has no host")

    host = parts.hostname or ""
    if not host:
        return UrlVerdict(False, url, "url host could not be parsed")
    if block_private and _is_private_host(host):
        return UrlVerdict(False, url, f"host '{host}' is a private/loopback address")

    if not allow_executables:
        path_lower = parts.path.lower()
        if path_lower.endswith(_EXECUTABLE_SUFFIXES):
            return UrlVerdict(False, url, "url points at an executable/installer file")

    return UrlVerdict(True, url, "")


def resolves(host: str, timeout: float = 5.0) -> tuple[bool, str]:
    """DNS check, separated so the validator can report it as its own stage."""
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(host, None)
        addrs = sorted({i[4][0] for i in infos})
        return True, ",".join(addrs[:4])
    except Exception as exc:  # noqa: BLE001 - any resolver error is a DNS failure
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        socket.setdefaulttimeout(None)


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def absolutise(base: str, ref: str) -> str:
    """Resolve an HLS sub-resource against its manifest URL."""
    from urllib.parse import urljoin

    return urljoin(base, ref)


def sanitise_for_m3u(url: str) -> str:
    """Percent-encode characters that would break the one-URL-per-line M3U grammar."""
    parts = urlsplit(url)
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        quote(parts.path, safe="/%:@!$&'()*+,;=~-._"),
        parts.query,
        parts.fragment,
    )).replace(" ", "%20")

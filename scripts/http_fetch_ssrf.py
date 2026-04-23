"""Guardrails for server-side HTTP fetches driven by untrusted URLs (SSRF mitigation).

Used when embedding remote card images and when resolving NFT metadata URIs from
on-chain / DAS responses. Resolves the hostname and refuses any address that is
not a globally routable unicast IP (blocks loopback, RFC1918, link-local
including cloud metadata, CGNAT, multicast, etc.). HTTP redirects are followed
only after the redirect target passes the same checks.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
import urllib.request
from typing import Optional

_MAX_URL_BYTES = 8192


class SsrfUnsafeUrlError(ValueError):
    """The URL must not be fetched by this server."""


def _ipv4_is_blocked(addr: ipaddress.IPv4Address) -> bool:
    return not addr.is_global


def _ipv6_is_blocked(addr: ipaddress.IPv6Address) -> bool:
    mapped = addr.ipv4_mapped
    if mapped is not None:
        return _ipv4_is_blocked(mapped)
    return not addr.is_global


def _ip_is_blocked(addr: ipaddress._BaseAddress) -> bool:
    if isinstance(addr, ipaddress.IPv4Address):
        return _ipv4_is_blocked(addr)
    if isinstance(addr, ipaddress.IPv6Address):
        return _ipv6_is_blocked(addr)
    return True


def assert_http_url_safe_against_ssrf(url: str) -> None:
    """Raise ``SsrfUnsafeUrlError`` unless ``url`` is safe to fetch over HTTP(S).

    Performs DNS resolution and checks every resolved address. Only ``http`` and
    ``https`` schemes are allowed; credentials in the URL are rejected.
    """
    if url is None:
        raise SsrfUnsafeUrlError("URL is empty")
    text = str(url).strip()
    if not text:
        raise SsrfUnsafeUrlError("URL is empty")
    if len(text.encode("utf-8")) > _MAX_URL_BYTES:
        raise SsrfUnsafeUrlError("URL exceeds maximum length")

    parsed = urllib.parse.urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SsrfUnsafeUrlError("Only http/https URLs may be fetched")

    host = parsed.hostname
    if not host:
        raise SsrfUnsafeUrlError("URL has no hostname")

    if parsed.username is not None and parsed.username != "":
        raise SsrfUnsafeUrlError("URL credentials are not allowed")
    if parsed.password is not None and parsed.password != "":
        raise SsrfUnsafeUrlError("URL credentials are not allowed")

    port = parsed.port
    if port is None:
        port = 443 if scheme == "https" else 80

    try:
        infos = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise SsrfUnsafeUrlError(f"DNS resolution failed: {exc}") from exc

    if not infos:
        raise SsrfUnsafeUrlError("No addresses resolved for host")

    for _family, _type, _proto, _canon, sockaddr in infos:
        ip_str: Optional[str] = None
        if len(sockaddr) >= 2 and isinstance(sockaddr[0], str):
            ip_str = sockaddr[0]
        if ip_str is None:
            continue
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SsrfUnsafeUrlError(f"Unparseable IP from resolver: {ip_str!r}") from None
        if _ip_is_blocked(addr):
            raise SsrfUnsafeUrlError(
                f"Refusing to fetch URL that resolves to non-public address: {addr}"
            )


class _SsrfCheckingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect target the same way as the original URL."""

    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Optional[urllib.request.Request]:
        resolved = urllib.parse.urljoin(req.get_full_url(), newurl)
        assert_http_url_safe_against_ssrf(resolved)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_HTTP_SSRF_SAFE_OPENER = urllib.request.build_opener(_SsrfCheckingRedirectHandler())


def urlopen_after_ssrf_check(
    req: urllib.request.Request,
    *,
    timeout: float,
) -> object:
    """``urllib`` ``open()`` after ``assert_http_url_safe_against_ssrf`` on the request URL."""
    assert_http_url_safe_against_ssrf(req.get_full_url())
    return _HTTP_SSRF_SAFE_OPENER.open(req, timeout=timeout)

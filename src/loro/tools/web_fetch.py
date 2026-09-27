"""Fetch a web page for the agent without becoming an SSRF gadget.

Every hop (including redirects) must be https (unless ``allow_http``), on an allowlisted domain,
and resolve only to public addresses; the address actually connected to is checked again before
any body is read, which defeats DNS rebinding. Bodies are size- and time-bounded and only text
content types are returned.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from loro.config import WebFetchConfig

TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml")
Resolver = Callable[[str, int], list[str]]


class WebFetchError(ValueError):
    """The request was refused or failed; the message is safe to show the model."""


@dataclass(frozen=True)
class WebPage:
    url: str
    status: int
    content_type: str
    text: str
    bytes_read: int
    truncated: bool
    redirects: int


def _default_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise WebFetchError(f"Could not resolve {host}: {error}") from error
    return sorted({str(info[4][0]) for info in infos})


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def domain_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for entry in allowed:
        entry = entry.lower().rstrip(".")
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and host != entry[2:]:
                return True
        elif host == entry:
            return True
    return False


class WebFetcher:
    def __init__(
        self,
        config: WebFetchConfig,
        *,
        resolver: Resolver | None = None,
        transport: httpx.BaseTransport | None = None,
        peer_check: bool = True,
    ) -> None:
        self.config = config
        self.resolver = resolver or _default_resolver
        self.transport = transport
        self.peer_check = peer_check

    def _check_url(self, url: str) -> tuple[str, int]:
        parts = urlsplit(url)
        allowed_schemes = {"https", "http"} if self.config.allow_http else {"https"}
        if parts.scheme not in allowed_schemes:
            raise WebFetchError(f"Only {' and '.join(sorted(allowed_schemes))} URLs are allowed.")
        if parts.username or parts.password:
            raise WebFetchError("URLs with embedded credentials are not allowed.")
        host = parts.hostname or ""
        if not host:
            raise WebFetchError("URL has no host.")
        try:
            ipaddress.ip_address(host)
            literal = True
        except ValueError:
            literal = False
        if literal:
            raise WebFetchError("Fetch by domain name, not a literal IP address.")
        if not domain_allowed(host, self.config.allowed_domains):
            raise WebFetchError(
                f"{host} is not in web_fetch.allowed_domains; ask an operator to add it."
            )
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = self.resolver(host, port)
        if not addresses:
            raise WebFetchError(f"{host} did not resolve.")
        private = [address for address in addresses if not public_address(address)]
        if private:
            raise WebFetchError(
                f"{host} resolves to a non-public address ({private[0]}); refusing to fetch."
            )
        return host, port

    def fetch(self, url: str) -> WebPage:
        deadline = time.monotonic() + self.config.timeout_seconds
        redirects = 0
        client_options: dict[str, Any] = {
            "timeout": httpx.Timeout(self.config.timeout_seconds),
            "follow_redirects": False,
            "headers": {"User-Agent": self.config.user_agent, "Accept": "text/*, application/json"},
            "trust_env": False,  # no ambient proxies: the checks above must hold for the request
        }
        if self.transport is not None:
            client_options["transport"] = self.transport
        with httpx.Client(**client_options) as client:
            while True:
                self._check_url(url)
                if time.monotonic() > deadline:
                    raise WebFetchError("Fetch timed out.")
                with client.stream("GET", url) as response:
                    self._check_peer(response)
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise WebFetchError("Redirect without a Location header.")
                        redirects += 1
                        if redirects > self.config.max_redirects:
                            raise WebFetchError("Too many redirects.")
                        url = urljoin(url, location)
                        continue
                    content_type = response.headers.get("content-type", "").split(";")[0].strip()
                    if not content_type.lower().startswith(TEXT_TYPES):
                        raise WebFetchError(
                            f"Content type {content_type or 'unknown'} is not text; not returned."
                        )
                    body = bytearray()
                    truncated = False
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise WebFetchError("Fetch timed out while reading the body.")
                        room = self.config.max_bytes - len(body)
                        if len(chunk) > room:
                            body.extend(chunk[:room])
                            truncated = True
                            break
                        body.extend(chunk)
                    encoding = response.encoding or "utf-8"
                    return WebPage(
                        url=str(response.url),
                        status=response.status_code,
                        content_type=content_type,
                        text=bytes(body).decode(encoding, errors="replace"),
                        bytes_read=len(body),
                        truncated=truncated,
                        redirects=redirects,
                    )

    def _check_peer(self, response: httpx.Response) -> None:
        if not self.peer_check:
            return
        stream = response.extensions.get("network_stream")
        peer = stream.get_extra_info("server_addr") if stream is not None else None
        if not peer:
            raise WebFetchError("Could not confirm the connected address; refusing to read.")
        if not public_address(str(peer[0])):
            raise WebFetchError("Connected to a non-public address (DNS rebinding?); refusing.")

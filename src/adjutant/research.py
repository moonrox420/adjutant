import hashlib
import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from adjutant.errors import DomainError

MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class WebsiteEvidence:
    source_url: str
    title: str
    text: str
    content_hash: str


class VisibleText(HTMLParser):
    """Extract bounded visible text without executing scripts or rendering remote assets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden_depth += 1
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text and not self.hidden_depth:
            if self.in_title:
                self.title_parts.append(text)
            else:
                self.parts.append(text)


def public_target(url: str) -> tuple[str, int, str]:
    """Validate all resolved addresses and return a pinned public IP to prevent DNS rebinding."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DomainError("UnsafeWebsite", "Use a public HTTP or HTTPS website URL.", 422)
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise DomainError("UnsafeWebsite", "Website port is invalid.", 422) from exc
    if port != (443 if parsed.scheme == "https" else 80):
        raise DomainError("UnsafeWebsite", "Only standard web ports are permitted.", 422)
    hostname = parsed.hostname.encode("idna").decode("ascii")
    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DomainError(
            "WebsiteUnavailable", "The website hostname could not be resolved.", 422
        ) from exc
    ips: list[str] = list(dict.fromkeys(str(address[4][0]) for address in addresses))
    parsed_ips = [ipaddress.ip_address(ip) for ip in ips]
    if not ips or any(not ip.is_global or ip.is_multicast or ip.is_reserved for ip in parsed_ips):
        raise DomainError(
            "UnsafeWebsite",
            "Private, loopback, and reserved network addresses are blocked.",
            422,
        )
    return hostname, port, ips[0]


def fetch_website(url: str) -> WebsiteEvidence:
    """Read one public page, revalidate redirects, cap bytes, and preserve source evidence."""
    current = url
    for _ in range(4):
        hostname, port, ip = public_target(current)
        parsed = urlsplit(current)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection = http.client.HTTPConnection(hostname, port, timeout=12)
        try:
            sock = socket.create_connection((ip, port), timeout=12)
            if parsed.scheme == "https":
                try:
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=hostname)
                except BaseException:
                    sock.close()
                    raise
            connection.sock = sock
            connection.request(
                "GET",
                path,
                headers={
                    "Host": hostname,
                    "User-Agent": "Adjutant/0.1",
                    "Accept": "text/html",
                    "Accept-Encoding": "identity",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise DomainError(
                        "WebsiteUnavailable",
                        "Website redirect has no destination.",
                        422,
                    )
                destination = urljoin(current, location)
                if parsed.scheme == "https" and urlsplit(destination).scheme != "https":
                    raise DomainError("UnsafeWebsite", "HTTPS downgrades are blocked.", 422)
                current = destination
                continue
            if response.status != 200:
                raise DomainError(
                    "WebsiteUnavailable",
                    f"Website returned HTTP {response.status}.",
                    422,
                )
            if "text/html" not in (response.getheader("Content-Type") or "").lower():
                raise DomainError(
                    "UnsupportedContent", "The website must return an HTML page.", 422
                )
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise DomainError(
                    "UnsupportedContent",
                    "The website ignored uncompressed transfer.",
                    422,
                )
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise DomainError(
                    "WebsiteTooLarge",
                    "Page exceeds the two-megabyte import limit.",
                    422,
                )
            parser = VisibleText()
            parser.feed(raw.decode("utf-8", errors="replace"))
            text = "\n".join(parser.parts)[:16000]
            if len(text) < 40:
                raise DomainError(
                    "WebsiteEmpty",
                    "No usable page text was found. Add brand facts manually.",
                    422,
                )
            return WebsiteEvidence(
                current,
                " ".join(parser.title_parts)[:300],
                text,
                hashlib.sha256(raw).hexdigest(),
            )
        except (OSError, http.client.HTTPException) as exc:
            raise DomainError(
                "WebsiteUnavailable", "The website request failed or timed out.", 422
            ) from exc
        finally:
            connection.close()
    raise DomainError("TooManyRedirects", "Website exceeded the redirect limit.", 422)

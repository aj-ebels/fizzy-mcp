"""Follow Fizzy list pagination without sending the bearer token off-origin.

Fizzy signals another page with a Link header rel=next. Page length varies, so
the header is the only completeness signal. Next links and redirects are
requested only when they stay on the configured HTTPS origin.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Literal, TypedDict
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

MAX_PAGES = 25
MAX_REDIRECTS = 5
_MAX_LINK_HEADER = 8192
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_PARAM = re.compile(
    r"""([!#$%&'*+\-.0-9A-Z^_`a-z|~]+)\s*=\s*(?:"((?:\\.|[^"\\])*)"|([^";,\s]*))"""
)
_REL_NEXT = re.compile(r"(?i)(?:^|[;\s,])rel\s*=\s*\"?next(?:[\s\";,]|$)")


class ListResult(TypedDict):
    items: list[Any]
    completeness: Literal["complete", "truncated", "error"]
    next: str | None
    error: str | None


class _PageStop(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def collect_pages(
    url: str,
    *,
    headers: Mapping[str, str],
    origin: str,
    params: dict | None = None,
    max_pages: int = MAX_PAGES,
) -> ListResult:
    """Read a list until the last page, the page limit, or a hard stop."""
    try:
        current = _resolve(url, origin)
    except _PageStop as exc:
        return _error([], exc.message)

    seen_urls: set[str] = set()
    seen_records: set[str] = set()
    items: list[Any] = []
    pages = 0
    current_params = params

    while True:
        page_key = _loop_key(current)
        if page_key in seen_urls:
            return _error(items, "pagination loop")
        if pages >= max_pages:
            return _truncated(items, current)
        seen_urls.add(page_key)
        try:
            response = _get_following_redirects(
                current,
                headers=headers,
                params=current_params,
                origin=origin,
            )
        except _PageStop as exc:
            return _error(items, exc.message)
        except httpx.HTTPError as exc:
            return _error(items, f"request failed: {type(exc).__name__}")

        seen_urls.add(_loop_key(str(response.url)))
        pages += 1
        if response.status_code != 200:
            return _error(items, f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            return _error(items, "response was not JSON")
        if not isinstance(body, list):
            return _error(items, "list response was not a JSON array")
        for item in body:
            identity = _record_identity(item)
            if identity in seen_records:
                return _error(items, "duplicate record across pages")
            seen_records.add(identity)
            items.append(item)

        try:
            nxt = _next_url(response, origin)
        except _PageStop as exc:
            return _error(items, exc.message)
        if nxt is None:
            return _complete(items)
        current = nxt
        current_params = None


def _get_following_redirects(
    url: str,
    *,
    headers: Mapping[str, str],
    params: dict | None,
    origin: str,
) -> httpx.Response:
    current = url
    current_params = params
    seen: set[str] = set()
    for _ in range(MAX_REDIRECTS + 1):
        key = _loop_key(current)
        if key in seen:
            raise _PageStop("redirect loop")
        seen.add(key)
        safe = _require_origin(current, origin)
        response = httpx.get(
            safe,
            headers=dict(headers),
            params=current_params,
            follow_redirects=False,
        )
        if response.status_code not in _REDIRECT_STATUSES:
            return response
        location = response.headers.get("location")
        if not location or any(char in location for char in "\r\n"):
            raise _PageStop("redirect location is unusable")
        location = location.strip()
        if not location or len(location) > _MAX_LINK_HEADER:
            raise _PageStop("redirect location is unusable")
        current = urljoin(str(response.url), location)
        current_params = None
    raise _PageStop("too many redirects")


def _next_url(response: httpx.Response, origin: str) -> str | None:
    raw = response.headers.get("link")
    if not raw:
        return None
    if len(raw) > _MAX_LINK_HEADER:
        raise _PageStop("unusable Link rel=next")
    found = _parse_next(raw)
    if found is None:
        if _REL_NEXT.search(raw):
            raise _PageStop("unusable Link rel=next")
        return None
    resolved = urljoin(str(response.url), found)
    return _require_origin(resolved, origin)


def _parse_next(header: str) -> str | None:
    for part in _split_links(header):
        match = re.match(r"\s*<([^>]*)>\s*(.*)$", part, re.DOTALL)
        if not match:
            continue
        target = match.group(1).strip()
        rels = [token.lower() for token in _rel_values(match.group(2))]
        if "next" in rels:
            return target or None
    return None


def _rel_values(rest: str) -> list[str]:
    values: list[str] = []
    for match in _PARAM.finditer(rest):
        if match.group(1).lower() != "rel":
            continue
        raw = match.group(2) if match.group(2) is not None else match.group(3) or ""
        values.extend(raw.split())
    return values


def _split_links(header: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    in_angles = False
    in_quotes = False
    escaped = False
    for char in header:
        if escaped:
            buf.append(char)
            escaped = False
            continue
        if in_quotes and char == "\\":
            buf.append(char)
            escaped = True
            continue
        if char == '"' and not in_angles:
            in_quotes = not in_quotes
        elif char == "<" and not in_quotes:
            in_angles = True
        elif char == ">" and in_angles and not in_quotes:
            in_angles = False
        elif char == "," and not in_angles and not in_quotes:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    if buf:
        parts.append("".join(buf))
    return parts


def _resolve(url: str, origin: str) -> str:
    base = origin if origin.endswith("/") else f"{origin}/"
    return _require_origin(urljoin(base, url), origin)


def _require_origin(url: str, origin: str) -> str:
    if not url or url.strip() != url or any(char in url for char in "\r\n\t "):
        raise _PageStop("next link is not a usable URL")
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise _PageStop("next link contains userinfo")
    if parts.scheme != "https":
        raise _PageStop("next link is not https")
    host = _idna_host(parts.hostname)
    origin_parts = urlsplit(origin)
    origin_host = _idna_host(origin_parts.hostname)
    if (
        origin_parts.scheme != "https"
        or host is None
        or origin_host is None
        or host != origin_host
        or (parts.port or 443) != (origin_parts.port or 443)
    ):
        raise _PageStop("next link is off the configured Fizzy origin")
    if parts.fragment:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    return url


def _idna_host(host: str | None) -> str | None:
    if not host:
        return None
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise _PageStop("next link is off the configured Fizzy origin") from exc


def _loop_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port
    if parts.scheme == "https" and port in (None, 443):
        netloc = host
    elif port is None:
        netloc = host
    else:
        netloc = f"{host}:{port}"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", query, ""))


def _record_identity(item: Any) -> str:
    if isinstance(item, dict):
        if item.get("id") is not None:
            return "id:" + json.dumps(item["id"], sort_keys=True, default=str)
        if item.get("number") is not None:
            return "number:" + json.dumps(item["number"], sort_keys=True, default=str)
    return "json:" + json.dumps(item, sort_keys=True, default=str)


def _complete(items: list[Any]) -> ListResult:
    return {"items": items, "completeness": "complete", "next": None, "error": None}


def _truncated(items: list[Any], nxt: str) -> ListResult:
    return {"items": items, "completeness": "truncated", "next": nxt, "error": None}


def _error(items: list[Any], message: str) -> ListResult:
    return {"items": items, "completeness": "error", "next": None, "error": message}

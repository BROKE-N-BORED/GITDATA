"""Live network calls for Dataset Scout (stdlib urllib only).

Nothing here runs at import time. The GUI calls these from worker
threads so the window never freezes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import core

USER_AGENT = f"DatasetScout/{core.VERSION}"
TIMEOUT = 20


class SearchError(Exception):
    """A search failed; the message is written for the user."""


def _read_json(url: str, headers: dict | None = None) -> tuple[object, dict]:
    """GET a URL, return (parsed JSON, response headers)."""
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")), dict(response.headers)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from exc
    except urllib.error.URLError as exc:
        raise SearchError(
            f"Could not reach the internet: {exc.reason}. "
            "Check the connection and try again."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SearchError(
            "The reply was not readable. Try the search again."
        ) from exc


def _http_error(exc: urllib.error.HTTPError) -> SearchError:
    if exc.code == 403:
        reset = exc.headers.get("X-RateLimit-Reset")
        reset_at = None
        if reset:
            try:
                reset_at = datetime.fromtimestamp(int(reset), tz=timezone.utc)
            except (ValueError, OverflowError):
                reset_at = None
        return core.RateLimitedError(reset_at)
    if exc.code == 401:
        return SearchError(
            "GitHub did not accept that token. Check it in the "
            "Sources tab, or clear it to search without one."
        )
    return SearchError(f"Search failed (HTTP {exc.code}). Try again.")


def search_github(query: str, category: str, token: str = "",
                  per_page: int = 30) -> list[dict]:
    """Search GitHub repos live. Token is optional."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload, _headers = _read_json(
        core.github_search_url(query, per_page), headers
    )
    if not isinstance(payload, dict):
        raise SearchError("GitHub sent back something unexpected.")
    return core.parse_github_results(payload, category)


def search_huggingface(query: str, category: str,
                       limit: int = 30) -> list[dict]:
    """Search Hugging Face datasets live. No key needed."""
    headers = {"User-Agent": USER_AGENT}
    payload, _headers = _read_json(core.hf_search_url(query, limit), headers)
    if not isinstance(payload, list):
        raise SearchError("Hugging Face sent back something unexpected.")
    return core.parse_hf_results(payload, category)


def check_link(url: str) -> str:
    """Check one link. Returns "ok", "dead", or "unknown".

    Tries a HEAD request first; falls back to a ranged GET for
    servers that refuse HEAD.
    """
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return "ok" if 200 <= response.status < 400 else "dead"
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403, 405):
            return _check_with_get(url)
        if 200 <= exc.code < 400:
            return "ok"
        return "dead"
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return "unknown"


def _check_with_get(url: str) -> str:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return "ok" if 200 <= response.status < 400 else "dead"
    except urllib.error.HTTPError as exc:
        if exc.code == 416:  # range not satisfiable: the page exists
            return "ok"
        return "dead" if exc.code >= 400 else "ok"
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return "unknown"

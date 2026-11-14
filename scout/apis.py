"""Live network calls for Dataset Scout (stdlib urllib only).

Nothing here runs at import time. The GUI calls these from worker
threads so the window never freezes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from . import convert, core

USER_AGENT = f"DatasetScout/{core.VERSION}"
TIMEOUT = 20

# GitHub rate-limit state, updated on every GitHub reply (T16).
# {"remaining": int | None, "reset": datetime | None}
GITHUB_RATE: dict = {"remaining": None, "reset": None}

# Extensions that count as data files when sniffing (T29).
DATA_EXTENSIONS = (".jsonl", ".json", ".parquet", ".csv", ".tsv", ".arrow")


def _note_github_rate(headers: dict) -> None:
    """Record the rate-limit headers from a GitHub reply."""
    remaining = headers.get("X-RateLimit-Remaining")
    if remaining is not None:
        try:
            GITHUB_RATE["remaining"] = int(remaining)
        except ValueError:
            pass
    reset = headers.get("X-RateLimit-Reset")
    if reset:
        try:
            GITHUB_RATE["reset"] = datetime.fromtimestamp(
                int(reset), tz=timezone.utc)
        except (ValueError, OverflowError):
            pass


class SearchError(Exception):
    """A search failed; the message is written for the user."""


def _read_json(url: str, headers: dict | None = None) -> tuple[object, dict]:
    """GET a URL, return (parsed JSON, response headers)."""
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
            headers = dict(response.headers)
            _note_github_rate(headers)
            return json.loads(raw.decode("utf-8")), headers
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
    _note_github_rate(dict(exc.headers))
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


# ---------------------------------------------------------------- update check (T21)

def latest_release_tag(owner: str = "BROKE-N-BORED",
                       repo: str = "GITDATA") -> str | None:
    """Latest release tag from GitHub, or None when it cannot be read."""
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": USER_AGENT}
    try:
        payload, _headers = _read_json(
            f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
            headers)
    except SearchError:
        return None
    if isinstance(payload, dict):
        return payload.get("tag_name") or None
    return None


# ---------------------------------------------------------------- STELLA-ready sniffing (T29)

def _is_data_file(name: str) -> bool:
    return name.lower().endswith(DATA_EXTENSIONS)


def _peek_url_text(url: str, limit: int = 8192) -> str | None:
    """Read the first bytes of a URL as text, or None on failure.

    Asks for a range, but caps the read either way, so a server
    that ignores ranges cannot make us download the whole file.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT,
                      "Range": f"bytes=0-{limit - 1}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(limit)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return raw.decode("utf-8", errors="replace")


def _sniff_files(names: list[str], total_size, peek) -> dict:
    """Shared sniff logic. peek(filename) judges one .jsonl file."""
    data_files = [name for name in names if _is_data_file(name)]
    if not data_files:
        return {"status": "none", "files": [], "total_size": total_size,
                "detail": "no data files found"}
    jsonl = [name for name in data_files
             if name.lower().endswith(".jsonl")]
    if jsonl:
        shape, detail = peek(jsonl[0])
        if shape == "ready":
            return {"status": "ready", "files": data_files,
                    "total_size": total_size, "detail": detail}
        if shape == "convert":
            return {"status": "convert", "files": data_files,
                    "total_size": total_size, "detail": detail}
        return {"status": "convert", "files": data_files,
                "total_size": total_size,
                "detail": f"has .jsonl ({jsonl[0]}), shape not checked: "
                          f"{detail}"}
    kinds = sorted({name.rsplit(".", 1)[-1].lower()
                    for name in data_files if "." in name})
    return {"status": "convert", "files": data_files,
            "total_size": total_size,
            "detail": f"has data files ({', '.join(kinds)}), no .jsonl"}


def hf_file_url(dataset_id: str, filename: str,
                branch: str = "main") -> str:
    return ("https://huggingface.co/datasets/"
            f"{dataset_id}/resolve/{branch}/{quote(filename, safe='/')}")


def _hf_peek(dataset_id: str, filename: str) -> tuple[str, str]:
    for branch in ("main", "master"):
        text = _peek_url_text(hf_file_url(dataset_id, filename, branch))
        if text is not None:
            return convert.peek_shape(text)
    return "unknown", "could not read the file to check its shape"


def sniff_huggingface(dataset_id: str) -> dict:
    """Check what data files a Hugging Face dataset holds.

    Returns {"status", "files", "total_size", "detail"}. Siblings
    carry filenames only, no sizes; usedStorage is the whole
    dataset size in bytes.
    """
    try:
        payload, _headers = _read_json(
            f"https://huggingface.co/api/datasets/{dataset_id}",
            {"User-Agent": USER_AGENT})
    except SearchError as exc:
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": str(exc)}
    if not isinstance(payload, dict):
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": "Hugging Face sent back something unexpected."}
    names = [entry.get("rfilename") or ""
             for entry in payload.get("siblings") or []
             if isinstance(entry, dict)]
    total = payload.get("usedStorage")
    return _sniff_files(
        names, total if isinstance(total, int) else None,
        lambda filename: _hf_peek(dataset_id, filename))


def github_archive_url(owner_repo: str, branch: str) -> str:
    return (f"https://codeload.github.com/{owner_repo}/zip/"
            f"refs/heads/{branch}")


def _github_peek(owner_repo: str, branch: str,
                 path: str) -> tuple[str, str]:
    url = ("https://raw.githubusercontent.com/"
           f"{owner_repo}/{branch}/{quote(path, safe='/')}")
    text = _peek_url_text(url)
    if text is None:
        return "unknown", "could not read the file to check its shape"
    return convert.peek_shape(text)


def sniff_github(owner_repo: str, token: str = "") -> dict:
    """Check what data files a GitHub repo holds (T29).

    Reads the repo (for its default branch) then the recursive
    file tree. On demand only, one repo at a time.
    """
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        repo, _headers = _read_json(
            f"https://api.github.com/repos/{owner_repo}", headers)
    except SearchError as exc:
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": str(exc)}
    if not isinstance(repo, dict):
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": "GitHub sent back something unexpected."}
    branch = repo.get("default_branch") or "main"
    try:
        tree, _headers = _read_json(
            f"https://api.github.com/repos/{owner_repo}/git/trees/"
            f"{branch}?recursive=1", headers)
    except SearchError as exc:
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": str(exc)}
    if not isinstance(tree, dict):
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": "GitHub sent back something unexpected."}
    entries = [entry for entry in tree.get("tree") or []
               if isinstance(entry, dict) and entry.get("type") == "blob"]
    names = [entry.get("path") or "" for entry in entries]
    total = sum(entry.get("size") or 0 for entry in entries
                if isinstance(entry.get("size"), int))
    result = _sniff_files(
        names, total,
        lambda path: _github_peek(owner_repo, branch, path))
    result["branch"] = branch
    if tree.get("truncated"):
        result["detail"] += " (GitHub cut the file list short)"
    return result


def sniff_item(item: dict, token: str = "") -> dict:
    """Sniff one result or list item by its source and URL."""
    source = item.get("source") or ""
    url = item.get("url") or ""
    if source == "Hugging Face":
        dataset_id = ""
        if "/datasets/" in url:
            dataset_id = url.split("/datasets/", 1)[-1].strip("/")
        if not dataset_id:
            dataset_id = (item.get("title") or "").strip()
        if dataset_id:
            return sniff_huggingface(dataset_id)
    elif source == "GitHub":
        repo = ""
        if "github.com/" in url:
            parts = url.split("github.com/", 1)[-1].split("/")
            if len(parts) >= 2:
                repo = f"{parts[0]}/{parts[1]}"
        if repo:
            return sniff_github(repo, token)
    return {"status": "unknown", "files": [], "total_size": None,
            "detail": "Scout can only check GitHub and "
                      "Hugging Face links."}


# ---------------------------------------------------------------- downloads (T35)

def download_file(url: str, dest: Path, progress=None,
                  chunk: int = 1024 * 256) -> int:
    """Download a URL to dest, streamed. progress(downloaded, total
    or None) is called as bytes arrive. Returns bytes written."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = response.headers.get("Content-Length")
            try:
                total = int(total) if total else None
            except ValueError:
                total = None
            downloaded = 0
            with open(dest, "wb") as handle:
                while True:
                    data = response.read(chunk)
                    if not data:
                        break
                    handle.write(data)
                    downloaded += len(data)
                    if progress is not None:
                        progress(downloaded, total)
            return downloaded
    except urllib.error.HTTPError as exc:
        raise SearchError(f"Download failed (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SearchError(f"Download failed: {exc}.") from exc


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

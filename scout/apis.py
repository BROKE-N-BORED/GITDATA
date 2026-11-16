"""Live network calls for Dataset Scout (stdlib urllib only).

Nothing here runs at import time. The GUI calls these from worker
threads so the window never freezes.
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from . import convert, core, parquet

USER_AGENT = f"DatasetScout/{core.VERSION}"
TIMEOUT = 20

# GitHub rate-limit state, updated on every GitHub reply (T16).
# {"remaining": int | None, "reset": datetime | None}
# GitHub's search quota (10/min unauthenticated, 30/min with a
# token) is a separate bucket from the core-API quota (60/hr or
# 5000/hr), so the two are tracked separately (v0.1.3 T14): only
# search replies update "search_remaining", which is what the rate
# meter shows. "remaining"/"reset" keep the last reply of either
# kind, as before.
GITHUB_RATE: dict = {"remaining": None, "reset": None,
                     "search_remaining": None, "search_reset": None,
                     "core_remaining": None, "core_reset": None}

# Extensions that count as data files when sniffing (T29).
DATA_EXTENSIONS = (".jsonl", ".json", ".parquet", ".csv", ".tsv", ".arrow")


def _note_github_rate(headers: dict, endpoint: str = "core") -> None:
    """Record the rate-limit headers from a GitHub reply.

    endpoint is "search" for the search API, "core" for every other
    GitHub API reply; the matching bucket is updated, and the
    legacy "remaining"/"reset" keys track the latest reply of
    either kind.
    """
    remaining = headers.get("X-RateLimit-Remaining")
    reset = headers.get("X-RateLimit-Reset")
    parsed_remaining = None
    if remaining is not None:
        try:
            parsed_remaining = int(remaining)
        except ValueError:
            pass
    parsed_reset = None
    if reset:
        try:
            parsed_reset = datetime.fromtimestamp(
                int(reset), tz=timezone.utc)
        except (ValueError, OverflowError):
            pass
    if parsed_remaining is not None:
        GITHUB_RATE["remaining"] = parsed_remaining
        GITHUB_RATE[f"{endpoint}_remaining"] = parsed_remaining
    if parsed_reset is not None:
        GITHUB_RATE["reset"] = parsed_reset
        GITHUB_RATE[f"{endpoint}_reset"] = parsed_reset


class SearchError(Exception):
    """A search failed; the message is written for the user."""


def _read_json(url: str, headers: dict | None = None,
               source: str = "") -> tuple[object, dict]:
    """GET a URL, return (parsed JSON, response headers).

    source names who is being asked ("GitHub" or "Hugging Face"):
    error messages name the right service (T6), and GitHub replies
    update the right rate bucket (T14).
    """
    request = urllib.request.Request(url, headers=headers or {})
    if source == "GitHub" and "/search/" in url:
        endpoint = "search"
    elif source == "GitHub":
        endpoint = "core"
    else:
        endpoint = ""
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
            headers = dict(response.headers)
            if endpoint:
                _note_github_rate(headers, endpoint)
            return json.loads(raw.decode("utf-8")), headers
    except urllib.error.HTTPError as exc:
        raise _http_error(exc, source, endpoint) from exc
    except urllib.error.URLError as exc:
        raise SearchError(
            f"Could not reach the internet: {exc.reason}. "
            "Check the connection and try again."
        ) from exc
    except http.client.HTTPException as exc:
        # A truncated read or a garbled reply (IncompleteRead,
        # BadStatusLine) is a failed request, not a crash (T7).
        raise SearchError(
            "The reply was cut short. Try the search again."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SearchError(
            "The reply was not readable. Try the search again."
        ) from exc


def _error_body(exc: urllib.error.HTTPError) -> str:
    """The response body of a failed request, lowercased, or "".

    The body says what a 403 really is; it is never logged
    anywhere, only read for a few known phrases.
    """
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001 - any read failure means "no body"
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").lower()
    return str(raw or "").lower()


def _http_error(exc: urllib.error.HTTPError, source: str = "",
                endpoint: str = "") -> SearchError:
    """Turn an HTTP failure into a plain-words error (T6).

    A 403 is not always a rate limit, and a Hugging Face failure
    is never a GitHub one: the source and the response body decide
    what the message says.
    """
    if endpoint:
        _note_github_rate(dict(exc.headers), endpoint)
    if exc.code == 403:
        reset = exc.headers.get("X-RateLimit-Reset")
        reset_at = None
        if reset:
            try:
                reset_at = datetime.fromtimestamp(int(reset), tz=timezone.utc)
            except (ValueError, OverflowError):
                reset_at = None
        if source == "Hugging Face":
            return SearchError(
                "Hugging Face refused this request. The dataset may "
                "be private, or Hugging Face may be limiting requests "
                "for a while. Try again later."
            )
        body = _error_body(exc)
        if "abuse" in body:
            return SearchError(
                "GitHub briefly blocked these requests. Wait a few "
                "minutes and try again."
            )
        if "rate limit" in body or not body:
            # The usual 403 on the search API is the rate limit;
            # with no body to say otherwise, that is the honest
            # guess, and the remedy (wait, or add a token) is right.
            return core.RateLimitedError(reset_at)
        return SearchError(
            "GitHub refused this request (403). It may be a private "
            "repo or a permission problem, not the search limit."
        )
    if exc.code == 401:
        if source == "Hugging Face":
            return SearchError(
                "Hugging Face asked for a sign-in for this request. "
                "Try again later."
            )
        return SearchError(
            "GitHub did not accept that token. Check it in the "
            "Sources tab, or clear it to search without one."
        )
    return SearchError(f"Search failed (HTTP {exc.code}). Try again.")


def search_github(query: str, category: str, token: str = "",
                  per_page: int = 30, page: int = 1) -> list[dict]:
    """Search GitHub repos live. Token is optional."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload, _headers = _read_json(
        core.github_search_url(query, per_page, page), headers,
        source="GitHub",
    )
    if not isinstance(payload, dict):
        raise SearchError("GitHub sent back something unexpected.")
    return core.parse_github_results(payload, category)


def search_huggingface(query: str, category: str,
                       limit: int = 30) -> list[dict]:
    """Search Hugging Face datasets live. No key needed."""
    headers = {"User-Agent": USER_AGENT}
    payload, _headers = _read_json(core.hf_search_url(query, limit),
                                   headers, source="Hugging Face")
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
        # 404 joins the GET-fallback codes (v0.1.3 T20): Kaggle
        # answers HEAD 404 on live pages that GET returns fine.
        if exc.code in (400, 401, 403, 404, 405):
            return _check_with_get(url)
        if 200 <= exc.code < 400:
            return "ok"
        return "dead"
    except (urllib.error.URLError, ValueError, TimeoutError, OSError,
            http.client.HTTPException):
        return "unknown"


# ---------------------------------------------------------------- update check (T21)

def latest_release_tag(owner: str = "BROKE-N-BORED",
                       repo: str = "GITDATA") -> str | None:
    """Latest release tag from GitHub, or None when it cannot be read.

    A rate limit is just "cannot be read" (T5): the update check
    never crashes or hangs on one.
    """
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": USER_AGENT}
    try:
        payload, _headers = _read_json(
            f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
            headers, source="GitHub")
    except (SearchError, core.RateLimitedError):
        return None
    if isinstance(payload, dict):
        return payload.get("tag_name") or None
    return None


# ---------------------------------------------------------------- STELLA-ready sniffing (T29)

def _is_data_file(name: str) -> bool:
    return name.lower().endswith(DATA_EXTENSIONS)


def _total_from_headers(headers, status: int) -> int | None:
    """The full size of the file behind a ranged peek (T15).

    A 206 reply carries it in Content-Range ("bytes 0-8191/525629");
    a 200 reply (the server ignored the range) carries it in
    Content-Length. A 206 Content-Length is only the range length,
    never the total.
    """
    if status == 206:
        content_range = headers.get("Content-Range") or ""
        if "/" in content_range:
            total = content_range.rsplit("/", 1)[-1].strip()
            if total.isdigit():
                return int(total)
        return None
    if status == 200:
        try:
            length = headers.get("Content-Length")
            return int(length) if length else None
        except ValueError:
            return None
    return None


def _peek_url(url: str, limit: int = 8192) -> tuple[str | None, int | None]:
    """Read the first bytes of a URL as text, plus the file's full
    size when the server says it. (None, None) on failure.

    Asks for a range, but caps the read either way, so a server
    that ignores ranges cannot make us download the whole file.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT,
                      "Range": f"bytes=0-{limit - 1}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(limit)
            total = _total_from_headers(response.headers,
                                        getattr(response, "status", 0))
    except (urllib.error.URLError, OSError, ValueError,
            http.client.HTTPException):
        return None, None
    return raw.decode("utf-8", errors="replace"), total


def _peek_url_text(url: str, limit: int = 8192) -> str | None:
    """Read the first bytes of a URL as text, or None on failure."""
    text, _total = _peek_url(url, limit)
    return text


# Compressed data files Scout recognises but cannot convert yet
# (v0.1.3 T34): the inner extension is the data file inside.
COMPRESSED_SUFFIXES = {
    ".gz": "gzipped",
    ".bz2": "bzip2-compressed",
    ".xz": "xz-compressed",
}


def compressed_data_ext(name: str) -> str:
    """The data extension inside a compressed file, or "".

    "train.jsonl.gz" holds a .jsonl; "model.bin.gz" holds nothing
    Scout knows, so it is not a data file at all.
    """
    lowered = name.lower()
    for suffix in COMPRESSED_SUFFIXES:
        if lowered.endswith(suffix):
            inner = lowered[:-len(suffix)]
            for ext in DATA_EXTENSIONS:
                if inner.endswith(ext):
                    return ext
    return ""


def compressed_description(name: str) -> str:
    """The plain-words description of a compressed data file, or "".

    "train.jsonl.gz" -> "a gzipped .jsonl (train.jsonl.gz)".
    """
    lowered = name.lower()
    for suffix, word in COMPRESSED_SUFFIXES.items():
        if lowered.endswith(suffix):
            inner = compressed_data_ext(name)
            if inner:
                return f"a {word} {inner} ({name})"
    return ""


def compressed_note(name: str) -> str:
    """One plain-words sentence naming a compressed data file."""
    description = compressed_description(name)
    if not description:
        return ""
    return (f"This dataset holds {description}, which Scout "
            "cannot convert yet.")


def _can_convert_file(name: str) -> bool:
    """True when Scout can convert this file right now.

    Parquet converts only when its reader is installed (T62);
    every other convertible extension converts as-is.
    """
    lowered = name.lower()
    if lowered.endswith(".parquet"):
        return parquet.reader_available()
    return any(lowered.endswith(ext) for ext in convert.CONVERTIBLE)


def _sniff_files(names: list[str], total_size, peek) -> dict:
    """Shared sniff logic. peek(filename) judges one .jsonl file."""
    data_files = [name for name in names if _is_data_file(name)]
    compressed = [name for name in names if compressed_data_ext(name)]
    files = data_files + compressed
    if not files:
        return {"status": "none", "files": [], "total_size": total_size,
                "detail": "no data files found"}
    if not data_files:
        # Only compressed data files (v0.1.3 T34): name them
        # instead of denying they exist.
        return {"status": "unsupported", "files": files,
                "total_size": total_size,
                "detail": compressed_note(compressed[0])}
    jsonl = [name for name in data_files
             if name.lower().endswith(".jsonl")]
    if jsonl:
        shape, detail = peek(jsonl[0])
        if shape == "ready":
            return {"status": "ready", "files": files,
                    "total_size": total_size, "detail": detail}
        if shape == "convert":
            return {"status": "convert", "files": files,
                    "total_size": total_size, "detail": detail}
        # A peek that failed is "unknown", not "convert" (v0.1.3
        # T26): Scout must not promise a conversion it could not
        # even check.
        return {"status": "unknown", "files": files,
                "total_size": total_size,
                "detail": f"has .jsonl ({jsonl[0]}), shape not checked: "
                          f"{detail}"}
    kinds = sorted({name.rsplit(".", 1)[-1].lower()
                    for name in data_files if "." in name})
    if all(name.lower().endswith(".parquet") for name in data_files):
        # Parquet-only (T62): it genuinely converts when the
        # reader is installed; without it, the status says so
        # honestly instead of promising a conversion that will
        # refuse. Mixed layouts are judged below.
        if parquet.reader_available():
            return {"status": "convert", "files": files,
                    "total_size": total_size,
                    "detail": "has data files (parquet), converts to "
                              "STELLA shape when downloaded"}
        return {"status": "needs-reader", "files": files,
                "total_size": total_size,
                "detail": ("has Parquet files, but the Parquet "
                           "reader is not installed, so Scout "
                           "cannot convert them yet")}
    if any(_can_convert_file(name) for name in data_files):
        return {"status": "convert", "files": files,
                "total_size": total_size,
                "detail": f"has data files ({', '.join(kinds)}), no .jsonl"}
    if any(name.lower().endswith(".parquet") for name in data_files):
        # Parquet is here but its reader is not installed, and
        # nothing else converts (v0.1.3 T26).
        return {"status": "needs-reader", "files": files,
                "total_size": total_size,
                "detail": (f"has data files ({', '.join(kinds)}), "
                           "no .jsonl. The Parquet files need the "
                           "Parquet reader, which is not installed.")}
    # Data files Scout cannot convert at all (v0.1.3 T26): Arrow
    # and the like. Name what is there and why.
    detail = f"has data files ({', '.join(kinds)}), no .jsonl"
    for kind in kinds:
        note = convert.UNSUPPORTED_NOTE.get("." + kind)
        if note:
            detail += f". {note}"
            break
    return {"status": "unsupported", "files": files,
            "total_size": total_size, "detail": detail}


def hf_file_url(dataset_id: str, filename: str,
                branch: str = "main") -> str:
    return ("https://huggingface.co/datasets/"
            f"{dataset_id}/resolve/{branch}/{quote(filename, safe='/')}")


def _hf_peek(dataset_id: str, filename: str,
             size_holder: dict | None = None) -> tuple[str, str]:
    for branch in ("main", "master"):
        text, total = _peek_url(hf_file_url(dataset_id, filename, branch))
        if text is not None:
            if total is not None and size_holder is not None:
                size_holder["total"] = total
            return convert.peek_shape(text)
    return "unknown", "could not read the file to check its shape"


def sniff_huggingface(dataset_id: str) -> dict:
    """Check what data files a Hugging Face dataset holds.

    Returns {"status", "files", "total_size", "detail"}. Siblings
    carry filenames only, no sizes; the true size comes from the
    ranged peek's Content-Range header (T15), because the API's
    usedStorage is wrong by ~10x. A rate limit is an "unknown"
    result in plain words, never an exception (T5).
    """
    dataset_id = core.clean_source_id(dataset_id)
    try:
        payload, _headers = _read_json(
            f"https://huggingface.co/api/datasets/{dataset_id}",
            {"User-Agent": USER_AGENT}, source="Hugging Face")
    except (SearchError, core.RateLimitedError) as exc:
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": str(exc)}
    if not isinstance(payload, dict):
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": "Hugging Face sent back something unexpected."}
    names = [entry.get("rfilename") or ""
             for entry in payload.get("siblings") or []
             if isinstance(entry, dict)]
    total = payload.get("usedStorage")
    peeked_size: dict = {}
    result = _sniff_files(
        names, total if isinstance(total, int) else None,
        lambda filename: _hf_peek(dataset_id, filename, peeked_size))
    if peeked_size.get("total") is not None:
        result["total_size"] = peeked_size["total"]
    return result


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
    owner_repo = core.clean_source_id(owner_repo)
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        repo, _headers = _read_json(
            f"https://api.github.com/repos/{owner_repo}", headers,
            source="GitHub")
    except (SearchError, core.RateLimitedError) as exc:
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": str(exc)}
    if not isinstance(repo, dict):
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": "GitHub sent back something unexpected."}
    branch = repo.get("default_branch") or "main"
    try:
        tree, _headers = _read_json(
            f"https://api.github.com/repos/{owner_repo}/git/trees/"
            f"{branch}?recursive=1", headers, source="GitHub")
    except (SearchError, core.RateLimitedError) as exc:
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": str(exc)}
    if not isinstance(tree, dict):
        return {"status": "unknown", "files": [], "total_size": None,
                "detail": "GitHub sent back something unexpected."}
    entries = [entry for entry in tree.get("tree") or []
               if isinstance(entry, dict) and entry.get("type") == "blob"]
    names = [entry.get("path") or "" for entry in entries]
    # Only data files count towards the size (v0.1.3 T43): a
    # repo's README and code are not training data.
    total = sum(entry.get("size") or 0 for entry in entries
                if isinstance(entry.get("size"), int)
                and _is_data_file(entry.get("path") or ""))
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


# ---------------------------------------------------------------- offline detection (T67)

# The last connectivity check, cached for a minute so the app
# never hammers the network asking if it is online.
_ONLINE_CACHE: dict = {"at": None, "online": None}
ONLINE_CACHE_SECONDS = 60
ONLINE_CHECK_URL = "https://huggingface.co"


def is_online(timeout: int = 5, force: bool = False) -> bool:
    """True when the internet answers (T67).

    Any HTTP reply counts as online, even an error page: only a
    failed connection means offline. The answer is cached for a
    minute unless forced, so a check never blocks the window
    repeatedly.
    """
    now = datetime.now(timezone.utc)
    cached_at = _ONLINE_CACHE["at"]
    if (not force and _ONLINE_CACHE["online"] is not None
            and cached_at is not None
            and (now - cached_at).total_seconds() < ONLINE_CACHE_SECONDS):
        return _ONLINE_CACHE["online"]
    request = urllib.request.Request(
        ONLINE_CHECK_URL, method="HEAD",
        headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            online = True
    except urllib.error.HTTPError:
        online = True  # a reply, even an error page, means online
    except (urllib.error.URLError, OSError, ValueError,
            http.client.HTTPException):
        online = False
    _ONLINE_CACHE["at"] = now
    _ONLINE_CACHE["online"] = online
    return online


# ---------------------------------------------------------------- downloads (T35)

def content_length(url: str, timeout: int = 10) -> int | None:
    """The size a URL declares, via HEAD, or None when unknown.

    Used to refuse an over-large archive before downloading it
    (T10). Any failure means "unknown", never an exception.
    """
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except (urllib.error.URLError, OSError, ValueError,
            http.client.HTTPException):
        return None


class DownloadStopped(Exception):
    """The user stopped the download part way through (v0.1.3 T28).

    Not a SearchError: the partial file is kept on purpose, so a
    later run can resume from it.
    """


def _content_range_total(value) -> int | None:
    """The total size in a Content-Range header ("bytes 0-9/525"),
    or None when the header does not carry one."""
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[-1].strip()
    return int(total) if total.isdigit() else None


def _finish_part(part: Path, dest: Path) -> None:
    """Move a finished .part file into place, or raise SearchError."""
    try:
        os.replace(part, dest)
    except OSError as exc:
        raise SearchError(f"Download failed: {exc}.") from exc


def download_file(url: str, dest: Path, progress=None,
                  chunk: int = 1024 * 256,
                  limit: int | None = None,
                  stop: threading.Event | None = None) -> int:
    """Download a URL to dest, streamed. progress(downloaded, total
    or None) is called as bytes arrive. Returns bytes written.

    Downloads go to a ".part" file and move into place only when
    complete, so a half-finished download never looks done; a
    later run resumes from the .part file with a Range request
    (v0.1.3 T27). When `stop` is set, the download ends as soon
    as the current read notices, the .part file is kept, and
    DownloadStopped is raised (v0.1.3 T28).

    A short download is refused, not treated as complete (T8):
    when fewer bytes arrive than Content-Length promised, the
    partial file is kept for the next retry and SearchError is
    raised. With limit set, the download stops as soon as it
    passes the limit (T10), and the partial file is removed:
    resuming a file that is too big is pointless.
    """
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    existing = part.stat().st_size if part.is_file() else 0
    if limit is not None and existing > limit:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise SearchError(
            "Download stopped: the file is bigger than "
            f"{core.format_size(limit)}.")
    response = None
    for attempt in range(2):
        headers = {"User-Agent": USER_AGENT}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=60)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and existing and attempt == 0:
                total = _content_range_total(
                    exc.headers.get("Content-Range"))
                if total is not None and total == existing:
                    # The server says the file is exactly the size
                    # we already have: the download is done.
                    _finish_part(part, dest)
                    return existing
                # The .part file does not match the file on the
                # server: drop it and start over.
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
                existing = 0
                continue
            raise SearchError(
                f"Download failed (HTTP {exc.code}).") from exc
        except http.client.HTTPException as exc:
            raise SearchError(
                "Download failed: the reply was cut short.") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise SearchError(f"Download failed: {exc}.") from exc
    if response is None:  # pragma: no cover - the loop always breaks or raises
        raise SearchError("Download failed.")
    status = getattr(response, "status", 0)
    length = response.headers.get("Content-Length")
    try:
        remaining = int(length) if length else None
    except ValueError:
        remaining = None
    if status == 206 and existing:
        total = (existing + remaining) if remaining is not None else None
        downloaded = existing
        mode = "ab"
    else:
        # 200 (or anything else): the server ignored the Range, so
        # the download starts over from the beginning.
        total = remaining
        downloaded = 0
        mode = "wb"
    # Small reads, so a stop is noticed promptly even on a slow
    # connection (v0.1.3 T28).
    read_size = min(chunk, 32 * 1024)
    too_big = False
    try:
        with response, open(part, mode) as handle:
            while True:
                if stop is not None and stop.is_set():
                    raise DownloadStopped(
                        "Download stopped part way through.")
                data = response.read(read_size)
                if not data:
                    break
                handle.write(data)
                downloaded += len(data)
                if limit is not None and downloaded > limit:
                    too_big = True
                    break
                if progress is not None:
                    progress(downloaded, total)
    except DownloadStopped:
        raise
    except http.client.HTTPException as exc:
        raise SearchError(
            "Download failed: the reply was cut short.") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SearchError(f"Download failed: {exc}.") from exc
    if too_big:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise SearchError(
            "Download stopped: the file is bigger than "
            f"{core.format_size(limit)}.")
    if total is not None and downloaded < total:
        raise SearchError(
            "Download stopped part way through "
            f"({core.format_size(downloaded)} of "
            f"{core.format_size(total)}). Try again.")
    _finish_part(part, dest)
    return downloaded


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
    except (urllib.error.URLError, ValueError, TimeoutError, OSError,
            http.client.HTTPException):
        return "unknown"

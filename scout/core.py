"""Dataset Scout — core logic (stdlib only, no Qt, no network at import).

One job: find training datasets for STELLA and build lists of links.

A STELLA dataset is a .jsonl file: one JSON object per line, either
{"prompt", "completion"} or {"messages": [{"role", "content"}, ...]},
one format per file, at least 2 rows. Scout finds LINKS to datasets;
it never trains, and every export says plainly that downloaded data
must be converted to that .jsonl shape before a STELLA project can
use it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, urlunsplit

VERSION = "0.1.5"

APP_NAME = "Dataset Scout"

# Plain-words note carried by every export (README + exports).
STELLA_FORMAT_NOTE = (
    "STELLA trains on a .jsonl file with one JSON object per line, "
    "either {\"prompt\", \"completion\"} or {\"messages\": [{\"role\", "
    "\"content\"}, ...]}, one format per file, at least 2 rows. "
    "Download and convert in Dataset Scout writes that shape into "
    "a stella-ready folder."
)

# The same note in plain words for the top of My List (v0.1.4
# T28): the schema above is for an export file, not for a person
# reading the tab.
STELLA_FORMAT_NOTE_SHORT = (
    "STELLA learns from simple question-and-answer files. "
    "Download and convert makes those files for you."
)

# ---------------------------------------------------------------- home dir

DEFAULT_HOME = Path.home() / ".dataset-scout"


def home_dir() -> Path:
    """Scout's data dir. SCOUT_HOME overrides it (used by tests)."""
    override = os.environ.get("SCOUT_HOME")
    if override:
        return Path(override)
    return DEFAULT_HOME


def ensure_home() -> Path:
    home = home_dir()
    (home / "logs").mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs()
    return home


def _write_json_atomic(path: Path, payload: dict,
                       backup: bool = False) -> None:
    """Write JSON via a tmp file + replace, so a crash mid-write
    never leaves a half-written file (T2). With backup=True the
    previous file is kept as <name>.bak first, but only when the
    previous file still parses: a corrupt file must never overwrite
    the good backup (v0.1.3 T4). The tmp name is unique per save,
    so two simultaneous saves cannot interleave into one temp file
    (v0.1.3 T13)."""
    ensure_home()
    if backup and path.is_file():
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass  # corrupt current file: leave the old backup alone
        else:
            try:
                shutil.copyfile(path, path.with_name(path.name + ".bak"))
            except OSError:
                pass
    tmp = path.with_name(
        f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_text_atomic(path: Path, text: str) -> None:
    """Write text via a tmp file + replace (v0.1.3 T21).

    The same crash safety as _write_json_atomic, for converted
    .jsonl files, exports, and the STELLA manifest: a crash
    mid-write never leaves a half-written file behind. Parent
    folders are created; a failed write removes the tmp file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# STELLA-ready check results (T29). "" means not checked yet.
STELLA_STATUS_LABELS = {
    "": "Not checked",
    "ready": "STELLA-ready",
    "convert": "Needs conversion",
    "none": "No data files",
    "unknown": "Check failed",
    # Parquet-only, and the optional Parquet reader is not
    # installed (T62): the check itself worked, the reader is
    # what is missing.
    "needs-reader": "Reader not installed",
    # Data files Scout cannot convert at all (Arrow-only,
    # compressed-only): the check worked, conversion is the
    # missing part (v0.1.3 T26/T34).
    "unsupported": "Cannot convert",
    # The check ran and the files have no fields Scout can map
    # (v0.1.4 T12): a final answer, not a failed check.
    "no-fields": "No usable fields",
}

# Workflow states for a saved link (T30).
WORKFLOW_STATES = {
    "found": "Found",
    "downloaded": "Downloaded",
    "converted": "Converted",
    "in-stella": "In STELLA",
}

# ---------------------------------------------------------------- categories

# category key -> (label, query suffix). "all" adds nothing.
CATEGORIES = {
    "all": ("All", ""),
    "post-training": ("Post-training", "post-training dataset"),
    "instruction-tuning": ("Instruction tuning", "instruction tuning dataset"),
    "reasoning": ("Reasoning", "reasoning dataset"),
    "training": ("Training datasets", "training dataset"),
}

CATEGORY_KEYS = list(CATEGORIES.keys())


def category_label(key: str) -> str:
    """Display label for a category key.

    Known keys get their proper label. Anything else is shown as-is,
    title-cased: categories are data, not a hardcoded list, so an
    unknown key is never dropped or mislabelled as something else.
    """
    if not key:
        return "All"
    known = CATEGORIES.get(key)
    if known:
        return known[0]
    return str(key).replace("-", " ").replace("_", " ").title()


def category_key_for_label(text: str) -> str:
    """Turn typed category text into a key (T12).

    A known label ("Reasoning") or key ("reasoning") maps to its
    key; anything else is slugified into a custom key, so the
    hand-add form and the edit dialog accept custom categories.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return "all"
    lowered = cleaned.lower()
    for key, (label, _suffix) in CATEGORIES.items():
        if lowered in (key, label.lower()):
            return key
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-") or "all"


def parse_version(tag: str) -> tuple:
    """A release tag ("v0.1.2") as a comparable tuple (T21).

    A release sorts after its own prereleases but before the
    next release: "v0.1.2-beta" is older than "v0.1.2" and newer
    than "v0.1.1" (v0.1.3 T43). The last element marks that:
    1 for a release, 0 for a prerelease. Build metadata after
    "+" does not change the order.
    """
    text = str(tag).lstrip("v")
    prerelease = "-" in text
    for marker in ("-", "+"):
        if marker in text:
            text = text.split(marker, 1)[0]
    try:
        parts = tuple(int(part) for part in text.split("."))
    except ValueError:
        return (0,)
    return parts + ((0,) if prerelease else (1,))


def format_size(num_bytes) -> str:
    """A byte count as plain words ("12 MB", "800 bytes")."""
    try:
        size = int(num_bytes)
    except (TypeError, ValueError):
        return ""
    if size < 0:
        return ""
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"

# Active window choices: label -> months (None = any age).
ACTIVE_WINDOWS = {
    "3 months": 3,
    "6 months": 6,
    "12 months": 12,
    "24 months": 24,
    "Any age": None,
}
DEFAULT_ACTIVE_MONTHS = 12

MIN_STARS_CHOICES = (0, 10, 100, 500)


def build_query(text: str, category: str) -> str:
    """Combine the typed text with the category's preset query."""
    text = (text or "").strip()
    suffix = CATEGORIES.get(category, ("", ""))[1]
    if text and suffix:
        return f"{text} {suffix}"
    if text:
        return text
    if suffix:
        return suffix
    return "dataset"


def source_for_url(url: str) -> str:
    """The source of a link, from its address (v0.1.4 T3).

    A Hugging Face dataset link is "Hugging Face" and a GitHub
    link is "GitHub", so a pasted link is checked and downloaded
    like a searched one; anything else stays "Found by hand" (a
    direct link to a data file downloads through the direct
    branch either way).
    """
    text = (url or "").strip()
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return "Found by hand"
    host = (parts.hostname or "").lower()
    if host in ("huggingface.co", "www.huggingface.co") \
            and "/datasets/" in parts.path:
        return "Hugging Face"
    if host in ("github.com", "www.github.com"):
        return "GitHub"
    return "Found by hand"


# ---------------------------------------------------------------- dates

def parse_iso_date(value: str | None) -> datetime | None:
    """Parse an ISO-8601 date (GitHub/HF style) to an aware datetime.

    Anything that is not a string (a damaged cache or list file can
    hold a number here) is not a date: None, never an exception.
    """
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def updated_sort_key(item: dict):
    """Newest-first sort key from the parsed updated time.

    Raw date strings misorder across timezones ("+12:00" sorts
    after "Z" for the same instant); the parsed time does not
    (v0.1.3 T37). An unparseable date sorts last.
    """
    parsed = parse_iso_date(item.get("updated"))
    return parsed or datetime.min.replace(tzinfo=timezone.utc)


def is_active(updated: str | None, months: int | None,
              now: datetime | None = None) -> bool:
    """True if `updated` falls inside the last `months` months.

    months=None means any age (always active). A missing or
    unparseable date counts as NOT active when a window is set —
    we cannot prove it is fresh.
    """
    if months is None:
        return True
    dt = parse_iso_date(updated)
    if dt is None:
        return False
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=months * 30)
    return dt >= cutoff


def format_date(value: str | None) -> str:
    """Short display date (2026-11-13) or a dash when unknown."""
    dt = parse_iso_date(value)
    return dt.strftime("%Y-%m-%d") if dt else "-"


# ---------------------------------------------------------------- GitHub

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


def github_search_url(query: str, per_page: int = 30,
                      page: int = 1) -> str:
    params = (
        f"q={quote_plus(query)}"
        "&sort=stars&order=desc"
        f"&per_page={per_page}"
        f"&page={page}"
    )
    return f"{GITHUB_SEARCH_URL}?{params}"


class RateLimitedError(Exception):
    """GitHub said slow down. Carries the plain-words message."""

    def __init__(self, reset_at: datetime | None):
        if reset_at is not None:
            when = reset_at.astimezone().strftime("%H:%M")
            msg = (
                "GitHub search limit reached. Try again after "
                f"{when}, or add a GitHub token in the Sources tab "
                "for more searches per minute."
            )
        else:
            msg = (
                "GitHub search limit reached. Wait a little while, "
                "or add a GitHub token in the Sources tab for more "
                "searches per minute."
            )
        super().__init__(msg)
        self.reset_at = reset_at


def parse_github_results(payload: dict, category: str) -> list[dict]:
    """Turn a GitHub search response into Scout result dicts.

    Malformed rows are skipped, never fatal: a payload with a
    non-list `items`, a non-dict row, or a string `license` must
    not raise (v0.1.3 T7).
    """
    results = []
    items = payload.get("items", []) or []
    if not isinstance(items, list):
        return results
    for item in items:
        if not isinstance(item, dict):
            continue
        license_info = item.get("license") or {}
        if isinstance(license_info, dict):
            license_text = (license_info.get("spdx_id")
                            or license_info.get("name") or "")
        elif isinstance(license_info, str):
            license_text = license_info
        else:
            license_text = ""
        topics = item.get("topics") or []
        if not isinstance(topics, list):
            topics = []
        results.append({
            "title": item.get("full_name") or item.get("name") or "",
            "url": item.get("html_url") or "",
            "category": category,
            "source": "GitHub",
            "stars": item.get("stargazers_count") or 0,
            "updated": item.get("pushed_at") or item.get("updated_at") or "",
            "description": item.get("description") or "",
            "license": license_text,
            "topics": list(topics),
            "downloads": 0,
            "format": "",
            "size_category": "",
            "notes": "",
        })
    return results


# ---------------------------------------------------------------- Hugging Face

HF_DATASETS_URL = "https://huggingface.co/api/datasets"


def hf_search_url(query: str, limit: int = 30) -> str:
    return f"{HF_DATASETS_URL}?search={quote_plus(query)}&limit={limit}"


def _tag_value(tags: list, prefix: str) -> str:
    """First tag value after a prefix (license:mit -> mit)."""
    for tag in tags or []:
        if isinstance(tag, str) and tag.startswith(prefix):
            return tag[len(prefix):]
    return ""


# Hugging Face license tags are lowercase ("mit"); GitHub shows
# the SPDX display case ("MIT"). Show the same case for both, so
# the license column and the license filter agree (v0.1.3 T43).
_LICENSE_DISPLAY = {
    "mit": "MIT",
    "apache-2.0": "Apache-2.0",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "cc0-1.0": "CC0-1.0",
    "cc-by-4.0": "CC-BY-4.0",
    "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "gpl-3.0": "GPL-3.0",
    "lgpl-3.0": "LGPL-3.0",
    "mpl-2.0": "MPL-2.0",
    "unlicense": "Unlicense",
}


def _display_license(value: str) -> str:
    """A license tag in its display case, or as-is if unknown."""
    return _LICENSE_DISPLAY.get((value or "").lower(), value or "")


def parse_hf_results(payload: list, category: str) -> list[dict]:
    """Turn a Hugging Face datasets response into Scout result dicts.

    The tags carry the license, the file format, and the size
    category (T5): license:mit, format:parquet,
    size_categories:1K<n<10K. All three are shown in the table.
    """
    results = []
    for item in payload or []:
        if not isinstance(item, dict):
            continue
        dataset_id = str(item.get("id") or "")
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        results.append({
            "title": dataset_id,
            "url": f"https://huggingface.co/datasets/{dataset_id}" if dataset_id else "",
            "category": category,
            "source": "Hugging Face",
            "stars": item.get("likes") or 0,
            "updated": item.get("lastModified") or "",
            "description": "",
            "license": _display_license(_tag_value(tags, "license:")),
            "topics": list(tags),
            "downloads": item.get("downloads") or 0,
            "format": _tag_value(tags, "format:"),
            "size_category": _tag_value(tags, "size_categories:"),
            "notes": "",
        })
    return results


def filter_results(results: list[dict], active_only: bool,
                   months: int | None, min_stars: int,
                   now: datetime | None = None,
                   license_filter: str = "") -> list[dict]:
    """Apply the Active-only window, the min-stars floor, and the
    license filter (T32). A license filter keeps only results whose
    license matches exactly; an unknown license cannot prove a
    match, so it is hidden while a filter is set."""
    kept = []
    wanted = (license_filter or "").strip().lower()
    for item in results:
        if not isinstance(item, dict):
            continue  # a damaged cache row is skipped, never fatal
        if _safe_int(item.get("stars")) < min_stars:
            continue
        if wanted:
            lic = str(item.get("license") or "").strip().lower()
            if lic != wanted:
                continue
        if active_only and not is_active(item.get("updated"), months, now):
            continue
        kept.append(item)
    return kept


# ---------------------------------------------------------------- custom sites

DEFAULT_SOURCES = [
    {
        "name": "Hugging Face Datasets",
        "url": "https://huggingface.co/datasets?search={q}",
    },
    {
        "name": "Kaggle Datasets",
        "url": "https://www.kaggle.com/datasets?search={q}",
    },
]

# Dead sources (T31): Papers With Code shut down entirely when
# Hugging Face bought it — the site and the API both redirect to
# Hugging Face now, so a saved entry for it is a dead button.
# Dropped on load; the file itself is cleaned on the next save.
DEAD_SOURCE_URLS = {
    "https://paperswithcode.com/search?q={q}",
}


def sources_path() -> Path:
    return home_dir() / "sources.json"


def load_sources() -> list[dict]:
    """Read the user's custom sites; defaults on first run."""
    path = sources_path()
    if not path.is_file():
        return [dict(s) for s in DEFAULT_SOURCES]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [dict(s) for s in DEFAULT_SOURCES]
    sites = data.get("sites") if isinstance(data, dict) else None
    if not isinstance(sites, list):
        return [dict(s) for s in DEFAULT_SOURCES]
    out = []
    for site in sites:
        if isinstance(site, dict) and site.get("name") and site.get("url"):
            if str(site["url"]) in DEAD_SOURCE_URLS:
                continue
            out.append({"name": str(site["name"]), "url": str(site["url"])})
    return out


def save_sources(sites: list[dict]) -> None:
    _write_json_atomic(sources_path(), {"sites": sites})


def restore_default_sources() -> list[dict]:
    sites = [dict(s) for s in DEFAULT_SOURCES]
    save_sources(sites)
    return sites


def build_site_url(template: str, query: str) -> str:
    """Fill {q} in a site template, or return a plain URL as-is."""
    if "{q}" in template:
        return template.replace("{q}", quote_plus(query))
    return template


# ---------------------------------------------------------------- config (token)

def config_path() -> Path:
    return home_dir() / "config.json"


def load_config() -> dict:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config: dict) -> None:
    _write_json_atomic(config_path(), config)


def get_github_token() -> str:
    return str(load_config().get("github_token") or "").strip()


def set_github_token(token: str) -> None:
    config = load_config()
    config["github_token"] = token.strip()
    save_config(config)


def scrub_token(text: str, token: str | None = None) -> str:
    """Replace the GitHub token with *** wherever it appears."""
    if token is None:
        token = get_github_token()
    if not token:
        return text
    return text.replace(token, "***")


# ---------------------------------------------------------------- my list

def list_path() -> Path:
    return home_dir() / "mylist.json"


# Plain-words notes from the last list load: a corrupt file that
# was recovered from the backup, rows dropped while loading (T4).
# Consecutive duplicates collapse (the file is read several times
# at startup); list_load_notice() consumes them.
_LIST_LOAD_NOTES: list[str] = []


def _note_list_load(text: str) -> None:
    if not _LIST_LOAD_NOTES or _LIST_LOAD_NOTES[-1] != text:
        _LIST_LOAD_NOTES.append(text)


def list_load_notice() -> str:
    """What went wrong loading the list, in plain words, or "".

    Consumes the notes: each notice is reported once.
    """
    notes = list(_LIST_LOAD_NOTES)
    del _LIST_LOAD_NOTES[:]
    return " ".join(notes)


class ListChangedError(Exception):
    """The list file changed on disk since it was last read (T13)."""


def list_generation() -> str:
    """A fingerprint of the saved list file ("" when there is none).

    Two reads with the same fingerprint saw the same file; a
    different fingerprint means something else saved since.
    """
    path = list_path()
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _migrate_list_data(data) -> dict:
    """Accept the old {"items": [...]} shape or the collections
    shape; always return {"active": name, "collections": [...]}."""
    default = {"active": "My List",
               "collections": [{"name": "My List", "items": []}]}
    if not isinstance(data, dict):
        return default
    raw = data.get("collections")
    if isinstance(raw, list):
        collections = []
        for col in raw:
            if isinstance(col, dict) and col.get("name"):
                items = col.get("items")
                if not isinstance(items, list):
                    items = []
                collections.append({"name": str(col["name"]),
                                    "items": items})
        if not collections:
            return default
        active = str(data.get("active") or collections[0]["name"])
        if active not in [c["name"] for c in collections]:
            active = collections[0]["name"]
        return {"active": active, "collections": collections}
    items = data.get("items")
    if not isinstance(items, list):
        items = []
    return {"active": "My List",
            "collections": [{"name": "My List", "items": items}]}


def _read_list_file() -> dict:
    """The saved list file, migrated to collections shape (T30).

    When the file is corrupt, the .bak backup is loaded instead
    (T4): the backup exists precisely for this, and the first save
    after corruption must not destroy it (see _write_json_atomic).
    The user is told through list_load_notice().
    """
    path = list_path()
    if not path.is_file():
        return _migrate_list_data(None)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        bak = path.with_name(path.name + ".bak")
        if bak.is_file():
            try:
                bak_data = json.loads(bak.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                bak_data = None
            if bak_data is not None:
                recovered = _migrate_list_data(bak_data)
                count = sum(len(col["items"])
                            for col in recovered["collections"])
                word = "link" if count == 1 else "links"
                note = ("The saved list was damaged, so Dataset Scout "
                        f"loaded the backup copy instead ({count} {word} "
                        "recovered).")
                _note_list_load(note)
                log(note, level="WARNING")
                # A persistent record (T53): the status-bar notice
                # is gone with the next status update, so the
                # recovery is also kept in the config, where the
                # About box can show it. One record per incident:
                # the same damaged file loads several times at
                # startup, and re-stamping the date each time
                # would rewrite history.
                config = load_config()
                record = config.get("list_recovered")
                if (not isinstance(record, dict)
                        or record.get("links") != count):
                    config["list_recovered"] = {
                        "date": datetime.now(timezone.utc).isoformat(),
                        "links": count,
                    }
                    save_config(config)
                return recovered
        note = ("The saved list was damaged and could not be read, "
                "so Dataset Scout started with an empty list.")
        _note_list_load(note)
        log(note, level="WARNING")
        return _migrate_list_data(None)
    return _migrate_list_data(data)


def _clean_items(items) -> list[dict]:
    out = []
    dropped = 0
    for item in items or []:
        if not isinstance(item, dict):
            dropped += 1
            log("Skipping a saved row that is not a link entry.",
                level="WARNING")
            continue
        if not item.get("url"):
            dropped += 1
            log("Skipping a saved row with no link.",
                level="WARNING")
            continue
        try:
            out.append(normalize_item(item))
        except Exception as exc:  # noqa: BLE001 - one bad row must not lose the list
            dropped += 1
            log(f"Skipping a bad row in My List: {exc}", level="WARNING")
    if dropped:
        if dropped == 1:
            note = ("1 saved row could not be read and was left out.")
        else:
            note = (f"{dropped} saved rows could not be read and were "
                    "left out.")
        _note_list_load(note)
    return out


def load_list_data() -> dict:
    """The whole saved list file: active collection + all lists."""
    return _read_list_file()


def load_collections() -> tuple[str, list[str]]:
    """(active collection name, all collection names)."""
    data = _read_list_file()
    return data["active"], [c["name"] for c in data["collections"]]


def load_list() -> list[dict]:
    """Items in the active collection (the old single list)."""
    data = _read_list_file()
    for col in data["collections"]:
        if col["name"] == data["active"]:
            return _clean_items(col["items"])
    return []


def save_list(items: list[dict],
              expected_generation: str | None = None) -> None:
    """Save items into the active collection, keeping the others.

    With expected_generation set, the save refuses (ListChangedError)
    when the file changed on disk since the caller last read it, so
    a GUI save cannot silently clobber changes made via the CLI
    while the GUI was open (T13). The caller then merges and retries.
    """
    data = _read_list_file()
    for col in data["collections"]:
        if col["name"] == data["active"]:
            col["items"] = [normalize_item(item) for item in items]
            break
    if (expected_generation is not None
            and list_generation() != expected_generation):
        raise ListChangedError(
            "The list changed on disk since it was last read.")
    _write_json_atomic(list_path(), data, backup=True)


def save_collections(active: str, collections: list[dict],
                     expected_generation: str | None = None) -> None:
    """Save the full collections file.

    expected_generation works as in save_list (T13).
    """
    data = {
        "active": active,
        "collections": [
            {"name": str(col.get("name") or "My List"),
             "items": [normalize_item(item)
                       for item in col.get("items") or []]}
            for col in collections
        ],
    }
    if (expected_generation is not None
            and list_generation() != expected_generation):
        raise ListChangedError(
            "The list changed on disk since it was last read.")
    _write_json_atomic(list_path(), data, backup=True)


def _safe_int(value) -> int:
    """Coerce to int; anything unparseable becomes 0 (T1: a bad
    number in a saved file must never crash the app)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_item(item: dict) -> dict:
    """Fill every field a list item carries, with safe defaults."""
    files = item.get("stella_files") or []
    if not isinstance(files, list):
        files = []
    topics = item.get("topics") or []
    if not isinstance(topics, list):
        topics = []
    return {
        "title": str(item.get("title") or item.get("url") or ""),
        "url": str(item.get("url") or ""),
        "category": str(item.get("category") or "all"),
        "source": str(item.get("source") or ""),
        "stars": _safe_int(item.get("stars") or 0),
        "updated": str(item.get("updated") or ""),
        "description": str(item.get("description") or ""),
        "license": str(item.get("license") or ""),
        "notes": str(item.get("notes") or ""),
        "status": str(item.get("status") or ""),
        "downloads": _safe_int(item.get("downloads") or 0),
        "format": str(item.get("format") or ""),
        "size_category": str(item.get("size_category") or ""),
        "topics": [str(entry) for entry in topics],
        "stella_status": str(item.get("stella_status") or ""),
        "stella_files": [str(entry) for entry in files],
        "stella_size": _safe_int(item.get("stella_size") or 0),
        "state": str(item.get("state") or "found"),
    }


def normalize_url(url: str) -> str:
    """Canonical form of a URL, for dedup only (T7).

    Lowercase scheme and host, drop default ports and a leading
    www., strip a trailing slash and the fragment. http and https
    are the same link, and a bare host is the root path, so both
    collapse (v0.1.3 T24). The stored URL keeps its original
    shape; only comparisons use this form.
    """
    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text
    scheme = parsed.scheme.lower()
    if scheme in ("http", "https"):
        scheme = "https"
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parsed.port
    except ValueError:
        port = None
    # http and https are the same link now, so both default
    # ports collapse (T24).
    if scheme == "https" and port in (80, 443):
        port = None
    netloc = host
    if port:
        netloc += f":{port}"
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        netloc = f"{auth}@{netloc}"
    path = parsed.path or ""
    if path == "/":
        path = ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def clean_source_id(text: str) -> str:
    """A dataset id or repo path cleaned for API use (v0.1.3 T43).

    Strips a query string, a fragment, trailing slashes, and a
    ".git" suffix: "owner/repo.git?tab=readme" becomes
    "owner/repo". An id copied from a browser address bar is
    often dirty this way.
    """
    cleaned = (text or "").strip()
    for marker in ("?", "#"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0]
    cleaned = cleaned.rstrip("/")
    if cleaned.lower().endswith(".git"):
        cleaned = cleaned[:-4]
    return cleaned


def add_to_list(items: list[dict], new_item: dict) -> tuple[list[dict], bool]:
    """Add an item unless its URL is already in the list.

    Returns (items, added). The list is saved by the caller.
    Comparison uses the normalised URL, so a trailing slash or a
    www. does not create a second entry (T7).
    """
    item = normalize_item(new_item)
    key = normalize_url(item["url"])
    if any(normalize_url(existing["url"]) == key for existing in items):
        return items, False
    return items + [item], True


def remove_from_list(items: list[dict], url: str) -> list[dict]:
    key = normalize_url(url)
    return [item for item in items
            if normalize_url(item["url"]) != key]


# ---------------------------------------------------------------- exports

def export_markdown(items: list[dict], name: str = "Dataset Scout list") -> str:
    """Markdown list of links, grouped by category."""
    lines = [f"# {name}", ""]
    lines.append(STELLA_FORMAT_NOTE)
    lines.append("")
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item.get("category") or "all", []).append(item)
    # Known categories first, in their usual order, then anything
    # else alphabetically. Every category present gets a section:
    # categories are data, not a hardcoded list, so nothing is ever
    # dropped for being unknown.
    ordered = [key for key in CATEGORY_KEYS if key in grouped]
    ordered += sorted(key for key in grouped if key not in CATEGORY_KEYS)
    for key in ordered:
        group = grouped[key]
        label = category_label(key)
        lines.append(f"## {label}")
        lines.append("")
        for item in group:
            title = item.get("title") or item.get("url") or ""
            url = item.get("url") or ""
            lines.append(f"- [{title}]({url})")
            details = []
            if item.get("source"):
                details.append(item["source"])
            if item.get("stars"):
                details.append(f"{item['stars']} stars")
            if item.get("updated"):
                details.append(f"updated {format_date(item['updated'])}")
            if item.get("license"):
                details.append(f"license {item['license']}")
            if item.get("format"):
                details.append(f"format {item['format']}")
            if item.get("downloads"):
                details.append(f"{item['downloads']} downloads")
            if item.get("stella_status"):
                label = STELLA_STATUS_LABELS.get(
                    item["stella_status"], item["stella_status"])
                details.append(label)
            if details:
                lines.append(f"  {', '.join(details)}")
            if item.get("notes"):
                lines.append(f"  Note: {item['notes']}")
        lines.append("")
    return "\n".join(lines)


def export_json(items: list[dict]) -> str:
    """Plain JSON export of the list."""
    payload = {
        "name": "Dataset Scout list",
        "created": datetime.now(timezone.utc).isoformat(),
        "stella_format_note": STELLA_FORMAT_NOTE,
        "items": [normalize_item(item) for item in items],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def export_stella_manifest(items: list[dict],
                           name: str = "Dataset Scout list") -> str:
    """STELLA manifest JSON: the list in a shape STELLA can read."""
    payload = {
        "stella_dataset_sources_version": 1,
        "name": name,
        "created": datetime.now(timezone.utc).isoformat(),
        "stella_format_note": STELLA_FORMAT_NOTE,
        "items": [
            {
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "category": item.get("category") or "all",
                "source": item.get("source") or "",
                "stars": _safe_int(item.get("stars") or 0),
                "updated": item.get("updated") or "",
                "license": item.get("license") or "",
                "notes": item.get("notes") or "",
                "downloads": _safe_int(item.get("downloads") or 0),
                "format": item.get("format") or "",
                "size_category": item.get("size_category") or "",
                "stella_status": item.get("stella_status") or "",
                "stella_size": _safe_int(item.get("stella_size") or 0),
                "state": item.get("state") or "found",
            }
            for item in items
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- import (T13)

def import_json_text(text: str) -> list[dict]:
    """Parse a JSON or STELLA-manifest export back into items."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("That file is not a JSON list.") from exc
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("That file does not hold a Dataset Scout list.")
    return [normalize_item(item) for item in items
            if isinstance(item, dict) and item.get("url")]


def import_markdown_text(text: str) -> list[dict]:
    """Parse a Markdown export back into items.

    Links come from "- [title](url)" lines; the category comes from
    the "## Heading" above them, mapped back to a key when the
    heading is a known label, slugified otherwise. A "Note: ..."
    line after a link becomes that item's note.
    """
    label_to_key = {label.lower(): key
                    for key, (label, _suffix) in CATEGORIES.items()}
    link_re = re.compile(r"^-\s*\[(?P<title>.*?)\]\((?P<url>.*?)\)\s*$")
    items: list[dict] = []
    category = "all"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            category = label_to_key.get(
                heading.lower(),
                re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
                or "all")
            continue
        match = link_re.match(stripped)
        if match:
            items.append(normalize_item({
                "title": match.group("title") or match.group("url"),
                "url": match.group("url"),
                "category": category,
            }))
            continue
        if stripped.startswith("Note: ") and items:
            items[-1]["notes"] = stripped[len("Note: "):].strip()
    return items


def import_file_text(text: str) -> list[dict]:
    """Import from export text: JSON when it parses as JSON, else
    Markdown. A byte-order mark at the start is stripped first,
    so a BOM does not break the JSON check (v0.1.3 T35). Raises
    ValueError with a plain message when the text holds no usable
    list."""
    stripped = text.lstrip("\ufeff").strip()
    if stripped.startswith("{"):
        items = import_json_text(stripped)
        if not items:
            raise ValueError("That JSON file holds no links.")
        return items
    items = import_markdown_text(stripped)
    if not items:
        raise ValueError("No links found in that file.")
    return items


# ---------------------------------------------------------------- search cache + history (T14)

SEARCH_CACHE_TTL = 3600  # 1 hour
SEARCH_CACHE_MAX = 20
HISTORY_MAX = 10


def _cache_key(query: str, category: str) -> str:
    return hashlib.sha256(
        f"{category}\0{query}".encode("utf-8")).hexdigest()


def load_search_cache(query: str, category: str) -> list[dict] | None:
    """Cached results for an identical search, or None.

    Cached rows are validated like list rows (T12): rows must be
    dicts, and a date that is not a string becomes unknown rather
    than crashing the search.
    """
    path = home_dir() / "cache.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return None
    entry = entries.get(_cache_key(query, category))
    if not isinstance(entry, dict):
        return None
    saved = parse_iso_date(entry.get("saved"))
    if saved is None:
        return None
    age = (datetime.now(timezone.utc) - saved).total_seconds()
    if age > SEARCH_CACHE_TTL:
        return None
    results = entry.get("results")
    if not isinstance(results, list):
        return None
    cleaned = []
    for row in results:
        if not isinstance(row, dict):
            continue
        if "updated" in row and not isinstance(row["updated"], str):
            row = dict(row)
            row["updated"] = ""
        cleaned.append(row)
    return cleaned


def save_search_cache(query: str, category: str,
                      results: list[dict]) -> None:
    """Cache one search's results; oldest entries drop past the cap."""
    path = home_dir() / "cache.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        entries = {}
    entries[_cache_key(query, category)] = {
        "query": query,
        "category": category,
        "saved": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    while len(entries) > SEARCH_CACHE_MAX:
        oldest = min(
            entries,
            key=lambda k: str(entries[k].get("saved") or "")
            if isinstance(entries[k], dict) else "")
        del entries[oldest]
    _write_json_atomic(path, {"entries": entries})


def load_history() -> list[dict]:
    """Recent searches, most recent first."""
    path = home_dir() / "history.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    recent = data.get("recent") if isinstance(data, dict) else None
    if not isinstance(recent, list):
        return []
    return [entry for entry in recent
            if isinstance(entry, dict) and entry.get("query")]


def save_history(query: str, category: str) -> None:
    """Remember one search; newest first, no repeats, capped."""
    query = (query or "").strip()
    if not query:
        return
    recent = [entry for entry in load_history()
              if not (entry.get("query") == query
                      and entry.get("category") == category)]
    recent.insert(0, {"query": query, "category": category})
    _write_json_atomic(home_dir() / "history.json",
                       {"recent": recent[:HISTORY_MAX]})


# ---------------------------------------------------------------- stats + STELLA home

def searches_run() -> int:
    """How many searches this install has run (T25 stats strip)."""
    return _safe_int(load_config().get("searches_run"))


def bump_search_count() -> int:
    count = searches_run() + 1
    config = load_config()
    config["searches_run"] = count
    save_config(config)
    return count


def stella_home() -> Path | None:
    """STELLA's home dir when STELLA is installed, else None (T33).

    STELLA_HOME wins; otherwise ~/.stella. "Installed" means the
    directory exists. STELLA reads datasets by file path, so the
    manifest is written here for the user to find, not consumed
    by STELLA itself.
    """
    override = os.environ.get("STELLA_HOME")
    if override:
        path = Path(override)
        return path if path.is_dir() else None
    path = Path.home() / ".stella"
    return path if path.is_dir() else None


# ---------------------------------------------------------------- debug log

_RING: list[str] = []
_RING_MAX = 200


_LOG_CLEANUP_DONE = False


def _cleanup_old_logs() -> None:
    """Delete daily log files older than 7 days (T8).

    Runs once per process; a failure here never breaks anything.
    """
    global _LOG_CLEANUP_DONE
    if _LOG_CLEANUP_DONE:
        return
    _LOG_CLEANUP_DONE = True
    try:
        logs_dir = home_dir() / "logs"
        if not logs_dir.is_dir():
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        for path in logs_dir.glob("scout-*.log"):
            try:
                stamp = path.stem.split("-", 1)[1]
                day = datetime.strptime(stamp, "%Y%m%d")
                day = day.replace(tzinfo=timezone.utc)
            except (IndexError, ValueError):
                continue
            if day < cutoff:
                path.unlink()
    except OSError:
        pass


def _log_file() -> Path:
    ensure_home()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return home_dir() / "logs" / f"scout-{today}.log"


def log(message: str, level: str = "INFO") -> None:
    """Write one timestamped line to the log file and the ring buffer.

    The GitHub token is scrubbed from every line before it is stored.
    """
    clean = scrub_token(str(message))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} [{level}] {clean}"
    _RING.append(line)
    del _RING[:-_RING_MAX]
    try:
        with _log_file().open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def log_error(message: str, exc: BaseException | None = None) -> None:
    """Log an error, with a traceback when one is given."""
    if exc is not None:
        detail = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        log(f"{message}\n{detail}", level="ERROR")
    else:
        log(message, level="ERROR")


def _log_tail_lines(limit: int = _RING_MAX) -> list[str]:
    """The last `limit` log lines from the log files on disk,
    oldest first (v0.1.4 T7).

    The on-disk log is what a user reporting a problem from a
    previous session needs; the in-memory ring only covers this
    run. Falls back to the ring when no log file can be read, and
    says so when there are no log lines at all.
    """
    logs_dir = home_dir() / "logs"
    tail: list[str] = []
    if logs_dir.is_dir():
        for path in sorted(logs_dir.glob("scout-*.log"), reverse=True):
            if len(tail) >= limit:
                break
            try:
                lines = path.read_text(encoding="utf-8",
                                       errors="replace").splitlines()
            except OSError:
                continue
            take = lines[-(limit - len(tail)):]
            tail = take + tail
    if tail:
        return tail
    if _RING:
        return list(_RING)
    return ["(No log lines yet.)"]


def debug_report() -> str:
    """A scrubbed report of the recent log, safe to share."""
    lines = [
        f"Dataset Scout {VERSION} debug report",
        f"Created: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Recent log (newest last):",
    ]
    lines.extend(_log_tail_lines())
    return scrub_token("\n".join(lines) + "\n")


def ring_lines() -> list[str]:
    return list(_RING)

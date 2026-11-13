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

import json
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

VERSION = "0.1.1"

APP_NAME = "Dataset Scout"

# Plain-words note carried by every export (README + exports).
STELLA_FORMAT_NOTE = (
    "Before STELLA can use a dataset from one of these links, the "
    "downloaded data must be converted to STELLA's format: a .jsonl "
    "file with one JSON object per line, either {\"prompt\", "
    "\"completion\"} or {\"messages\": [{\"role\", \"content\"}, ...]}, "
    "one format per file, at least 2 rows. Dataset Scout only finds "
    "links. It does not download or convert the data."
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
    return home


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


# ---------------------------------------------------------------- dates

def parse_iso_date(value: str | None) -> datetime | None:
    """Parse an ISO-8601 date (GitHub/HF style) to an aware datetime."""
    if not value:
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


def github_search_url(query: str, per_page: int = 30) -> str:
    params = (
        f"q={quote_plus(query)}"
        "&sort=stars&order=desc"
        f"&per_page={per_page}"
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
                "for more searches per hour."
            )
        else:
            msg = (
                "GitHub search limit reached. Wait a little while, "
                "or add a GitHub token in the Sources tab for more "
                "searches per hour."
            )
        super().__init__(msg)
        self.reset_at = reset_at


def parse_github_results(payload: dict, category: str) -> list[dict]:
    """Turn a GitHub search response into Scout result dicts."""
    results = []
    for item in payload.get("items", []) or []:
        license_info = item.get("license") or {}
        results.append({
            "title": item.get("full_name") or item.get("name") or "",
            "url": item.get("html_url") or "",
            "category": category,
            "source": "GitHub",
            "stars": item.get("stargazers_count") or 0,
            "updated": item.get("pushed_at") or item.get("updated_at") or "",
            "description": item.get("description") or "",
            "license": license_info.get("spdx_id") or license_info.get("name") or "",
            "topics": list(item.get("topics") or []),
            "notes": "",
        })
    return results


# ---------------------------------------------------------------- Hugging Face

HF_DATASETS_URL = "https://huggingface.co/api/datasets"


def hf_search_url(query: str, limit: int = 30) -> str:
    return f"{HF_DATASETS_URL}?search={quote_plus(query)}&limit={limit}"


def parse_hf_results(payload: list, category: str) -> list[dict]:
    """Turn a Hugging Face datasets response into Scout result dicts."""
    results = []
    for item in payload or []:
        dataset_id = item.get("id") or ""
        results.append({
            "title": dataset_id,
            "url": f"https://huggingface.co/datasets/{dataset_id}" if dataset_id else "",
            "category": category,
            "source": "Hugging Face",
            "stars": item.get("likes") or 0,
            "updated": item.get("lastModified") or "",
            "description": "",
            "license": "",
            "topics": list(item.get("tags") or []),
            "downloads": item.get("downloads") or 0,
            "notes": "",
        })
    return results


def filter_results(results: list[dict], active_only: bool,
                   months: int | None, min_stars: int,
                   now: datetime | None = None) -> list[dict]:
    """Apply the Active-only window and the min-stars floor."""
    kept = []
    for item in results:
        if (item.get("stars") or 0) < min_stars:
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
    {
        "name": "Papers With Code",
        "url": "https://paperswithcode.com/search?q={q}",
    },
]


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
            out.append({"name": str(site["name"]), "url": str(site["url"])})
    return out


def save_sources(sites: list[dict]) -> None:
    ensure_home()
    sources_path().write_text(
        json.dumps({"sites": sites}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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
    ensure_home()
    config_path().write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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


def load_list() -> list[dict]:
    path = list_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if isinstance(item, dict) and item.get("url"):
            out.append(normalize_item(item))
    return out


def save_list(items: list[dict]) -> None:
    ensure_home()
    list_path().write_text(
        json.dumps({"items": items}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def normalize_item(item: dict) -> dict:
    """Fill every field a list item carries, with safe defaults."""
    return {
        "title": str(item.get("title") or item.get("url") or ""),
        "url": str(item.get("url") or ""),
        "category": str(item.get("category") or "all"),
        "source": str(item.get("source") or ""),
        "stars": int(item.get("stars") or 0),
        "updated": str(item.get("updated") or ""),
        "license": str(item.get("license") or ""),
        "notes": str(item.get("notes") or ""),
        "status": str(item.get("status") or ""),
    }


def add_to_list(items: list[dict], new_item: dict) -> tuple[list[dict], bool]:
    """Add an item unless its URL is already in the list.

    Returns (items, added). The list is saved by the caller.
    """
    item = normalize_item(new_item)
    if any(existing["url"] == item["url"] for existing in items):
        return items, False
    return items + [item], True


def remove_from_list(items: list[dict], url: str) -> list[dict]:
    return [item for item in items if item["url"] != url]


# ---------------------------------------------------------------- exports

def export_markdown(items: list[dict], name: str = "Dataset Scout list") -> str:
    """Markdown list of links, grouped by category."""
    lines = [f"# {name}", ""]
    lines.append(STELLA_FORMAT_NOTE)
    lines.append("")
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item.get("category") or "all", []).append(item)
    for key in CATEGORY_KEYS:
        group = grouped.get(key)
        if not group:
            continue
        label = CATEGORIES[key][0]
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
                "stars": int(item.get("stars") or 0),
                "updated": item.get("updated") or "",
                "license": item.get("license") or "",
                "notes": item.get("notes") or "",
            }
            for item in items
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- debug log

_RING: list[str] = []
_RING_MAX = 200


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


def debug_report() -> str:
    """A scrubbed report of the recent log, safe to share."""
    lines = [
        f"Dataset Scout {VERSION} debug report",
        f"Created: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Recent log (newest last):",
    ]
    lines.extend(_RING)
    return scrub_token("\n".join(lines) + "\n")


def ring_lines() -> list[str]:
    return list(_RING)

"""Dataset watch (T63): re-run saved searches, flag what is new.

Off by default: nothing is watched until the user saves a search
as a watch. A watch re-runs only while the app is open: once at
launch when it is due, then on a timer while the app stays open.
There is no system timer and no background network — close the
app and the watching stops. No watch ever runs without the user
having opted that search in.

A watch is a saved search (query + category + how often to check)
plus the list of links it has already seen. A re-run compares
the fresh results against the seen links; anything not seen
before is new, and the new items are kept on the watch so the
window can show them with a "new since last run" marker. The
first run only sets the baseline: everything would be "new"
otherwise, which would be noise, not news.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import apis, core

DEFAULT_INTERVAL_HOURS = 24

# Bounds so the watches file stays small: the seen list is a
# sliding window, and only the newest batch of new items is kept.
MAX_SEEN_URLS = 500
MAX_NEW_ITEMS = 50


def watches_path() -> Path:
    return core.home_dir() / "watches.json"


def load_watches() -> list[dict]:
    """The saved watches, most recently added first."""
    path = watches_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    watches = data.get("watches") if isinstance(data, dict) else None
    if not isinstance(watches, list):
        return []
    out = []
    for watch in watches:
        if not isinstance(watch, dict):
            continue
        if not watch.get("query"):
            continue
        out.append(_clean_watch(watch))
    return out


def _clean_watch(watch: dict) -> dict:
    seen = watch.get("last_seen") or []
    if not isinstance(seen, list):
        seen = []
    new = watch.get("new") or []
    if not isinstance(new, list):
        new = []
    return {
        "id": str(watch.get("id") or uuid.uuid4().hex),
        "query": str(watch.get("query") or ""),
        "category": str(watch.get("category") or "all"),
        "interval_hours": max(
            1, core._safe_int(watch.get("interval_hours")
                              or DEFAULT_INTERVAL_HOURS)),
        "enabled": bool(watch.get("enabled", True)),
        "last_run": str(watch.get("last_run") or ""),
        "last_seen": [str(url) for url in seen
                      if isinstance(url, str)],
        "new": [item for item in new if isinstance(item, dict)],
        # Whether the user has seen the new items (T42): old
        # watches without the flag count as seen when they hold
        # nothing new, and unseen when they do.
        "new_seen": bool(watch.get("new_seen", not new)),
    }


def save_watches(watches: list[dict]) -> None:
    core._write_json_atomic(watches_path(), {"watches": watches})


def new_watch(query: str, category: str = "all",
              interval_hours: int = DEFAULT_INTERVAL_HOURS) -> dict:
    """A watch for a search, never run yet (no baseline, no news)."""
    return {
        "id": uuid.uuid4().hex,
        "query": (query or "").strip(),
        "category": category or "all",
        "interval_hours": max(1, int(interval_hours)),
        "enabled": True,
        "last_run": "",
        "last_seen": [],
        "new": [],
        "new_seen": True,
    }


def find_watch(watches: list[dict], watch_id: str) -> dict | None:
    for watch in watches:
        if watch.get("id") == watch_id:
            return watch
    return None


def add_watch(watches: list[dict], watch: dict) -> tuple[list[dict], bool]:
    """Add a watch unless the same search is already watched."""
    query = (watch.get("query") or "").strip()
    category = watch.get("category") or "all"
    for existing in watches:
        if (existing.get("query") or "").strip() == query \
                and (existing.get("category") or "all") == category:
            return watches, False
    return [watch] + list(watches), True


def remove_watch(watches: list[dict], watch_id: str) -> list[dict]:
    return [watch for watch in watches if watch.get("id") != watch_id]


def watch_due(watch: dict, now: datetime | None = None) -> bool:
    """True when an enabled watch has never run, or its interval
    has passed since its last run."""
    if not watch.get("enabled", True):
        return False
    last = core.parse_iso_date(watch.get("last_run") or "")
    if last is None:
        return True
    now = now or datetime.now(timezone.utc)
    hours = core._safe_int(watch.get("interval_hours")
                           or DEFAULT_INTERVAL_HOURS)
    return (now - last).total_seconds() >= max(1, hours) * 3600


def due_watches(watches: list[dict],
                now: datetime | None = None) -> list[dict]:
    return [watch for watch in watches if watch_due(watch, now)]


def run_watch_search(query: str, category: str,
                     token: str = "") -> tuple[list[dict], list]:
    """Run one search the way the Search tab does: both sources
    tried, failures named. Returns (results, failures)."""
    results: list[dict] = []
    failures: list[tuple[str, str]] = []
    try:
        results.extend(apis.search_github(
            query, category, token=token))
    except (apis.SearchError, core.RateLimitedError) as exc:
        failures.append(("GitHub", str(exc)))
    try:
        results.extend(apis.search_huggingface(query, category))
    except (apis.SearchError, core.RateLimitedError) as exc:
        failures.append(("Hugging Face", str(exc)))
    return results, failures


def apply_watch_results(watch: dict, results: list[dict],
                        now: datetime | None = None) -> list[dict]:
    """Fold a re-run's results into a watch, in place.

    Returns the items that are new since the last run. The first
    run sets the baseline and reports nothing new. The watch's
    seen list and last-run time are updated either way, so a
    re-run never reports the same items twice.

    New items are kept until the user has seen them (T42): a run
    that finds nothing new must not wipe flags from an earlier
    run, so the fresh items join the unseen ones instead of
    replacing them. The dialog marks them seen when the user
    picks the watch, and adding them to My List clears them.
    """
    now = now or datetime.now(timezone.utc)
    first_run = not watch.get("last_run")
    seen = {core.normalize_url(url)
            for url in watch.get("last_seen") or []}
    fresh: list[dict] = []
    fresh_urls: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or ""
        if not url:
            continue
        fresh_urls.append(url)
        if core.normalize_url(url) not in seen:
            fresh.append(item)
    # Union, not replace: a run where one source failed must not
    # drop the other source's links from the seen list, or they
    # would come back as "new" next time.
    merged = list(dict.fromkeys(
        (watch.get("last_seen") or []) + fresh_urls))
    watch["last_seen"] = merged[-MAX_SEEN_URLS:]
    watch["last_run"] = now.isoformat()
    if first_run:
        watch["new"] = []
        watch["new_seen"] = True
        return []
    if fresh:
        # Keep what the user has not seen yet, plus the fresh
        # items, newest last, without duplicates.
        kept = [item for item in watch.get("new") or []
                if isinstance(item, dict)]
        kept_urls = {core.normalize_url(item.get("url") or "")
                     for item in kept}
        for item in fresh:
            if core.normalize_url(item.get("url") or "") not in kept_urls:
                kept.append(item)
                kept_urls.add(core.normalize_url(item.get("url") or ""))
        watch["new"] = kept[-MAX_NEW_ITEMS:]
        watch["new_seen"] = False
    return fresh

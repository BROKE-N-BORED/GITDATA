"""Bulk download queue (T66): several datasets, pause, resume
across sessions.

The queue lives in the app's data home (download-queue.json) and
is saved on every change, so closing the app mid-queue loses
nothing: done items stay done, the rest wait for the next run.
An item left "running" when the app closed (a crash, or a close
mid-run) comes back as "queued" on the next load, so it runs
again instead of staying stuck as running forever.

Pause is the queue simply not running: pausing stops the
download in progress at once and queues that item again, so
starting again resumes it from the half-downloaded file; every
finished item keeps its state. Each item downloads through the
same download_and_convert path as a one-off download, so
byte-level resume (T27) applies to queued items exactly as it
applies to single downloads.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import core

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

STATES = (QUEUED, RUNNING, DONE, FAILED)

STATE_LABELS = {
    QUEUED: "Waiting",
    RUNNING: "Downloading",
    DONE: "Done",
    FAILED: "Failed",
}


def queue_path() -> Path:
    return core.home_dir() / "download-queue.json"


def _clean_entry(entry: dict) -> dict:
    item = entry.get("item") if isinstance(entry.get("item"), dict) \
        else {}
    state = entry.get("state") or QUEUED
    if state not in STATES:
        state = QUEUED
    outputs = entry.get("outputs") or []
    if not isinstance(outputs, list):
        outputs = []
    return {
        "id": str(entry.get("id") or uuid.uuid4().hex),
        "item": core.normalize_item(item),
        "state": state,
        "outputs": [str(path) for path in outputs],
        "rows": core._safe_int(entry.get("rows") or 0),
        "error": str(entry.get("error") or ""),
        "added": str(entry.get("added") or ""),
    }


def load_queue() -> tuple[list[dict], int]:
    """(entries, interrupted): entries saved on disk, with any
    item left "running" reset to "queued". `interrupted` counts
    the reset items, so the window can say what happened."""
    path = queue_path()
    if not path.is_file():
        return [], 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], 0
    raw = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return [], 0
    entries = [_clean_entry(entry) for entry in raw
               if isinstance(entry, dict)]
    interrupted = 0
    for entry in entries:
        if entry["state"] == RUNNING:
            entry["state"] = QUEUED
            entry["error"] = ("stopped part way through; "
                              "queued again")
            interrupted += 1
    if interrupted:
        save_queue(entries)
    return entries, interrupted


def save_queue(entries: list[dict]) -> None:
    core._write_json_atomic(queue_path(), {"entries": entries})


def new_entry(item: dict) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "item": core.normalize_item(item),
        "state": QUEUED,
        "outputs": [],
        "rows": 0,
        "error": "",
        "added": datetime.now(timezone.utc).isoformat(),
    }


def enqueue(entries: list[dict], item: dict) -> tuple[list[dict], bool]:
    """Queue an item. An item already waiting (or running) is not
    added twice; a done or failed item is queued again, keeping
    one entry per link."""
    key = core.normalize_url(item.get("url") or "")
    for entry in entries:
        if core.normalize_url(entry["item"].get("url") or "") == key:
            if entry["state"] in (QUEUED, RUNNING):
                return entries, False
            entry["state"] = QUEUED
            entry["outputs"] = []
            entry["rows"] = 0
            entry["error"] = ""
            return entries, True
    return entries + [new_entry(item)], True


def remove_entry(entries: list[dict], entry_id: str) -> list[dict]:
    return [entry for entry in entries if entry.get("id") != entry_id]


def clear_finished(entries: list[dict]) -> list[dict]:
    """Drop done and failed entries; waiting ones stay."""
    return [entry for entry in entries
            if entry["state"] in (QUEUED, RUNNING)]


def retry_failed(entries: list[dict],
                 entry_ids: list[str] | None = None) -> int:
    """Queue failed entries again (T45): back to waiting, with
    the old error, outputs, and row count cleared. With no ids,
    every failed entry is retried. Returns how many were queued."""
    wanted = None if entry_ids is None else set(entry_ids)
    retried = 0
    for entry in entries:
        if entry.get("state") != FAILED:
            continue
        if wanted is not None and entry.get("id") not in wanted:
            continue
        entry["state"] = QUEUED
        entry["outputs"] = []
        entry["rows"] = 0
        entry["error"] = ""
        retried += 1
    return retried


def next_queued(entries: list[dict]) -> dict | None:
    for entry in entries:
        if entry["state"] == QUEUED:
            return entry
    return None


def counts(entries: list[dict]) -> dict:
    total = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    for entry in entries:
        state = entry.get("state")
        if state in total:
            total[state] += 1
    total["total"] = len(entries)
    return total

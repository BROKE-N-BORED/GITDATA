"""Download history (T61): what was converted, when, how many rows.

One JSON file in the app's data home (download-history.json),
newest first, capped so the file stays small. A download, a
queue run, and a local convert each add one entry when they
convert at least one file; a failed run adds nothing. Nothing
here touches the network or Qt.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import core

# The history is a record, not an archive: the newest 200
# conversions are plenty, and the cap keeps the file small.
MAX_ENTRIES = 200


def history_path() -> Path:
    return core.home_dir() / "download-history.json"


def _clean_entry(entry: dict) -> dict:
    files = entry.get("files") or []
    if not isinstance(files, list):
        files = []
    return {
        "title": str(entry.get("title") or ""),
        "url": str(entry.get("url") or ""),
        "source": str(entry.get("source") or ""),
        "when": str(entry.get("when") or ""),
        "rows": core._safe_int(entry.get("rows") or 0),
        "files": [str(name) for name in files],
        "folder": str(entry.get("folder") or ""),
    }


def load_history() -> list[dict]:
    """The saved entries, newest first (empty when there are none)."""
    path = history_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [_clean_entry(entry) for entry in raw
            if isinstance(entry, dict)]


def save_history(entries: list[dict]) -> None:
    core._write_json_atomic(history_path(), {"entries": entries})


def record_conversion(title: str, url: str, source: str, rows: int,
                      outputs: list) -> dict:
    """Add one conversion to the history and save it.

    outputs are the converted .jsonl paths: the entry keeps their
    names and the folder they sit in, so the history window can
    open the folder again later. Returns the entry.
    """
    names = [Path(str(path)).name for path in outputs]
    folder = ""
    if outputs:
        folder = str(Path(str(outputs[0])).parent)
    entry = _clean_entry({
        "title": title,
        "url": url,
        "source": source,
        "when": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "files": names,
        "folder": folder,
    })
    entries = [entry] + load_history()
    save_history(entries[:MAX_ENTRIES])
    return entry


def clear_history() -> None:
    """Drop every history entry."""
    save_history([])

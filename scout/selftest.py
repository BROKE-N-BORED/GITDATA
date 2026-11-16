"""A self-test that exercises search, sniff, and conversion (T42).

Run from the command line as `scout-cli --self-test`. Three checks,
in plain words: search both sources for a common query, sniff the
first result, and convert a built-in sample offline. Nothing is
written to the real list or cache: the search results are held in
memory and thrown away.
"""

from __future__ import annotations

import threading

from . import apis, convert, core

QUERY = "math word problems"

# A small sample with two good rows plus one that only maps
# through the instruction fallback, converted offline (no network).
SAMPLE_JSONL = (
    '{"prompt": "What is 2 + 2?", "completion": "4"}\n'
    '{"prompt": "What is 3 + 3?", "completion": "6"}\n'
    '{"prompt": "", "completion": "", '
    '"instruction": "Add 1 and 1", "output": "2"}\n'
)


def run_checks() -> list[dict]:
    """Run the three checks.

    Each returns {"name", "ok", "detail"}: ok is True when the
    check passed, detail says what happened in plain words.
    """
    checks: list[dict] = []
    # 1. Search: both sources in parallel, pass if either answers
    #    with at least one result.
    results: list[dict] = []
    failures: list[str] = []
    lock = threading.Lock()

    def run_one(name: str, func):
        try:
            found = func()
        except Exception as exc:  # noqa: BLE001 - a dead source is a failed check, not a crash
            core.log_error(f"Self-test search failed ({name}): {exc}")
            with lock:
                failures.append(name)
            return
        with lock:
            results.extend(found)

    token = core.get_github_token()
    threads = [
        threading.Thread(
            target=run_one,
            args=("GitHub", lambda: apis.search_github(
                QUERY, "all", token=token, per_page=5)),
            daemon=True),
        threading.Thread(
            target=run_one,
            args=("Hugging Face", lambda: apis.search_huggingface(
                QUERY, "all", limit=5)),
            daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if results:
        word = "result" if len(results) == 1 else "results"
        detail = f"found {len(results)} {word}"
        if failures:
            detail += f" ({failures[0]} did not answer)"
        checks.append({"name": "Search", "ok": True, "detail": detail})
    else:
        checks.append({"name": "Search", "ok": False,
                       "detail": "neither GitHub nor Hugging Face "
                                 "answered"})
    # 2. Sniff: check the first result from the search.
    if results:
        item = results[0]
        try:
            found = apis.sniff_item(item, token=token)
        except Exception as exc:  # noqa: BLE001 - a failed sniff is a failed check
            found = {"status": "unknown", "detail": str(exc)}
        status = found.get("status") or "unknown"
        label = core.STELLA_STATUS_LABELS.get(status, status)
        name = item.get("title") or item.get("url") or "the first result"
        if status != "unknown":
            checks.append({"name": "Sniff", "ok": True,
                           "detail": f"{name}: {label}"})
        else:
            checks.append({"name": "Sniff", "ok": False,
                           "detail": found.get("detail")
                           or f"could not check {name}"})
    else:
        checks.append({"name": "Sniff", "ok": False,
                       "detail": "no search result to check"})
    # 3. Convert: the built-in sample, offline.
    rows, _report = convert.convert_text(SAMPLE_JSONL, "jsonl")
    ok, problem = convert.validate_rows(rows)
    if ok:
        word = "row" if len(rows) == 1 else "rows"
        checks.append({"name": "Convert", "ok": True,
                       "detail": f"{len(rows)} {word} converted from "
                                 "the sample"})
    else:
        checks.append({"name": "Convert", "ok": False, "detail": problem})
    return checks


def format_report(checks: list[dict]) -> str:
    """The checks as plain-words lines, with a one-line verdict."""
    lines = []
    for check in checks:
        mark = "PASS" if check["ok"] else "FAIL"
        lines.append(f"{mark}: {check['name']} - {check['detail']}")
    passed = sum(1 for check in checks if check["ok"])
    lines.append(f"Self-test: {passed} of {len(checks)} checks passed.")
    return "\n".join(lines)

"""Convert downloaded dataset files to STELLA's .jsonl shape (T35).

Stdlib only. No network here: the caller downloads the files, this
module turns their text into STELLA rows.

STELLA's rules (from stella/train.py load_dataset, verified
2026-11-13): one JSON object per line; either {"prompt",
"completion"} or {"messages": [...]}; one format per file; at
least 2 rows; prompt and completion must be non-empty strings;
messages need at least 2 entries, each with a role of system, user
or assistant and non-empty content, and the last message must be
from the assistant. Rows that break these rules are skipped, and
the report says how many and why. Nothing is invented: a row with
only a "text" field has no prompt to pair it with, so it is
skipped, not faked.

Parquet is supported through the optional pyarrow reader
(scout/parquet.py), imported lazily only when a Parquet file is
actually converted. Without it, Parquet files are refused in
plain words instead of failing.
"""

from __future__ import annotations

import csv
import io
import json

MESSAGE_ROLES = ("system", "user", "assistant")

# Extensions the converter handles, and what they are read as.
# "parquet" is read from a file path by scout.parquet, not from
# text: convert_text still raises for it (see below).
CONVERTIBLE = {
    ".jsonl": "jsonl",
    ".json": "json",
    ".csv": "csv",
    ".tsv": "tsv",
    ".parquet": "parquet",
}

# Extensions Scout recognises as data files but cannot convert.
UNSUPPORTED_NOTE = {
    ".parquet": "Parquet needs the Parquet reader (pyarrow), "
                "which is not installed. Run the installer again "
                "to add it.",
    ".arrow": "Arrow needs conversion elsewhere: Scout cannot "
              "read Arrow files.",
    ".txt": "Plain text files have no rows to convert.",
}


def _nonempty(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _is_prompt_row(obj: dict) -> bool:
    return bool(_nonempty(obj.get("prompt"))
                and _nonempty(obj.get("completion")))


def _is_messages_row(obj: dict) -> bool:
    messages = obj.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return False
    for msg in messages:
        if not isinstance(msg, dict):
            return False
        if msg.get("role") not in MESSAGE_ROLES:
            return False
        if not _nonempty(msg.get("content")):
            return False
    return messages[-1].get("role") == "assistant"


def map_row(obj: dict) -> tuple[dict | None, str]:
    """Map one source row to a STELLA row.

    Returns (row, "") on success, (None, reason) when the row
    cannot be used. The reason names what was wrong, so the
    report can say what was skipped and why.
    """
    if not isinstance(obj, dict):
        return None, "not an object"
    if _is_prompt_row(obj):
        return {"prompt": _nonempty(obj["prompt"]),
                "completion": _nonempty(obj["completion"])}, ""
    if _is_messages_row(obj):
        messages = [{"role": msg["role"],
                     "content": _nonempty(msg["content"])}
                    for msg in obj["messages"]]
        return {"messages": messages}, ""
    instruction = _nonempty(obj.get("instruction"))
    output = _nonempty(obj.get("output"))
    answer = _nonempty(obj.get("answer"))
    response = _nonempty(obj.get("response"))
    ground_truth = _nonempty(obj.get("ground_truth"))
    if instruction and output:
        extra = _nonempty(obj.get("input"))
        prompt = instruction if not extra else f"{instruction}\n\n{extra}"
        return {"prompt": prompt, "completion": output}, ""
    # instruction/answer and instruction/response are common
    # instruction-tuning layouts: many datasets use "answer" or
    # "response" where others use "output".
    if instruction and answer:
        extra = _nonempty(obj.get("input"))
        prompt = instruction if not extra else f"{instruction}\n\n{extra}"
        return {"prompt": prompt, "completion": answer}, ""
    if instruction and response:
        extra = _nonempty(obj.get("input"))
        prompt = instruction if not extra else f"{instruction}\n\n{extra}"
        return {"prompt": prompt, "completion": response}, ""
    if instruction and ground_truth:
        extra = _nonempty(obj.get("input"))
        prompt = instruction if not extra else f"{instruction}\n\n{extra}"
        return {"prompt": prompt, "completion": ground_truth}, ""
    question = _nonempty(obj.get("question"))
    if question and answer:
        return {"prompt": question, "completion": answer}, ""
    if question and response:
        return {"prompt": question, "completion": response}, ""
    if question and ground_truth:
        return {"prompt": question, "completion": ground_truth}, ""
    prompt = _nonempty(obj.get("prompt"))
    if prompt and response:
        return {"prompt": prompt, "completion": response}, ""
    if prompt and ground_truth:
        return {"prompt": prompt, "completion": ground_truth}, ""
    source = _nonempty(obj.get("input"))
    if source and output:
        return {"prompt": source, "completion": output}, ""
    # A broken prompt/messages field vetoes the row only when no
    # other mapping worked (v0.1.3 T30): a row with an empty prompt
    # but a good instruction/output pair still converts. The
    # reason names which half is missing (v0.1.4 T19): a prompt
    # with no completion is not "prompt or completion is empty".
    if "prompt" in obj or "completion" in obj:
        completion = _nonempty(obj.get("completion"))
        if prompt and not completion:
            if "completion" in obj:
                return None, "completion is empty"
            return None, "prompt has no completion to pair it with"
        if completion and not prompt:
            if "prompt" in obj:
                return None, "prompt is empty"
            return None, "completion has no prompt to pair it with"
        if "prompt" in obj and "completion" not in obj:
            return None, "prompt is empty"
        if "completion" in obj and "prompt" not in obj:
            return None, "completion is empty"
        return None, "prompt or completion is empty"
    if "messages" in obj:
        return None, "messages are not a valid conversation"
    fields = sorted(str(key) for key in obj.keys())
    if fields and set(fields) <= {"text"}:
        return None, "text only, no prompt to pair it with"
    return None, "no mapping for fields: " + (
        ", ".join(fields) if fields else "(none)")


def _rows_from_objects(objects: list) -> tuple[list[dict], dict]:
    """Map parsed objects to STELLA rows, one format per file."""
    rows: list[dict] = []
    skipped: dict[str, int] = {}
    fmt: str | None = None
    for obj in objects:
        row, reason = map_row(obj)
        if row is None:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        row_fmt = "messages" if "messages" in row else "prompt"
        if fmt is None:
            fmt = row_fmt
        if row_fmt != fmt:
            key = "different shape from the rest of the file"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        rows.append(row)
    report = {"seen": len(objects), "kept": len(rows),
              "skipped": skipped, "format": fmt or ""}
    return rows, report


def convert_jsonl_text(text: str) -> tuple[list[dict], dict]:
    """Convert .jsonl text to STELLA rows. Returns (rows, report)."""
    objects = []
    bad_json = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            objects.append(json.loads(line))
        except json.JSONDecodeError:
            bad_json += 1
    rows, report = _rows_from_objects(objects)
    report["seen"] = len(objects) + bad_json
    if bad_json:
        report["skipped"]["not valid JSON"] = bad_json
    return rows, report


def convert_json_text(text: str) -> tuple[list[dict], dict]:
    """Convert a .json document to STELLA rows."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], {"seen": 0, "kept": 0,
                    "skipped": {"not valid JSON": 1}, "format": ""}
    if isinstance(data, list):
        return _rows_from_objects(data)
    if isinstance(data, dict):
        for key in ("data", "rows", "examples", "dataset", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return _rows_from_objects(value)
        return _rows_from_objects([data])
    return [], {"seen": 1, "kept": 0,
                "skipped": {"not an object": 1}, "format": ""}


# Fields map_row understands: a first row holding any of these is
# a header row; anything else is data (v0.1.3 T33).
_CSV_KNOWN_FIELDS = frozenset({
    "prompt", "completion", "messages", "instruction", "input",
    "output", "answer", "response", "question", "ground_truth",
    "text",
})


def convert_csv_text(text: str, delimiter: str = ",") -> tuple[list[dict], dict]:
    """Convert .csv/.tsv text to STELLA rows.

    A first row with no recognised field name is data, not a
    header: it is kept, under prompt/completion headers (v0.1.3
    T33). Header names are lowercased, so "Prompt" maps the same
    as "prompt". Blank rows are skipped.
    """
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        return _rows_from_objects([])
    first = [field.strip().lstrip("\ufeff").lower()
             for field in rows[0]]
    if any(field in _CSV_KNOWN_FIELDS for field in first):
        headers = first
        data = rows[1:]
    else:
        headers = ["prompt", "completion"][:len(rows[0])]
        data = rows
    objects = []
    for raw in data:
        if not raw or all(not field.strip() for field in raw):
            continue
        obj = {}
        for index, header in enumerate(headers):
            if index < len(raw):
                obj[header] = raw[index]
        objects.append(obj)
    return _rows_from_objects(objects)


def convert_text(text: str, kind: str) -> tuple[list[dict], dict]:
    """Convert file text by kind: jsonl, json, csv, or tsv.

    "parquet" is not a text kind: Parquet is read from a file
    path by scout.parquet.convert_parquet_file, so it raises
    here like any other unknown kind.
    """
    if kind == "jsonl":
        return convert_jsonl_text(text)
    if kind == "json":
        return convert_json_text(text)
    if kind == "csv":
        return convert_csv_text(text, delimiter=",")
    if kind == "tsv":
        return convert_csv_text(text, delimiter="\t")
    raise ValueError(f"Unknown kind: {kind}")


def validate_rows(rows: list[dict]) -> tuple[bool, str]:
    """Check converted rows against STELLA's file rules."""
    if len(rows) < 2:
        return False, (
            f"only {len(rows)} usable row(s); STELLA needs at least 2")
    formats = {"messages" if "messages" in row else "prompt"
               for row in rows}
    if len(formats) > 1:
        return False, "mixed prompt and messages rows in one file"
    return True, ""


def rows_to_jsonl(rows: list[dict]) -> str:
    """Render STELLA rows as .jsonl text."""
    return "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def columns_map(columns) -> bool:
    """True when a file with these column names could map to
    STELLA rows (v0.1.4 T10).

    A probe row with a value in every column is run through
    map_row, so the Parquet sniff judges the same mappings the
    converter uses. The messages column gets a valid conversation
    as its probe value, since a plain string never maps.
    """
    probe: dict = {}
    for name in columns or []:
        if name == "messages":
            probe[name] = [{"role": "user", "content": "x"},
                           {"role": "assistant", "content": "y"}]
        else:
            probe[name] = "x"
    row, _reason = map_row(probe)
    return row is not None


def peek_shape(text: str) -> tuple[str, str]:
    """Judge a file's first lines for the STELLA-ready check (T29).

    Returns ("ready"|"convert"|"unmappable"|"unknown", detail).
    "ready" means the first rows are already in STELLA's shape;
    "convert" means they map to it with conversion; "unmappable"
    means the file was read and its rows have no fields Scout can
    map (v0.1.4 T12: checked, not failed); "unknown" means the
    peek could not tell (empty, not JSON, or no recognised
    fields).
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "unknown", "the file looks empty"
    parsed = []
    for line in lines[:20]:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not parsed:
        return "unknown", "the first lines are not JSON"
    ready = 0
    mappable = 0
    for obj in parsed:
        if not isinstance(obj, dict):
            continue
        if _is_prompt_row(obj) or _is_messages_row(obj):
            ready += 1
        else:
            row, _reason = map_row(obj)
            if row is not None:
                mappable += 1
    if ready:
        return "ready", (
            f"{ready} of the first rows are already in STELLA's shape")
    if mappable:
        return "convert", "rows map to STELLA's shape with conversion"
    return "unmappable", "rows have no fields Scout can map"


def describe_report(report: dict) -> str:
    """One plain-words line summarising a conversion report."""
    kept = report.get("kept", 0)
    skipped = report.get("skipped") or {}
    total_skipped = sum(skipped.values())
    text = f"{kept} rows kept"
    if total_skipped:
        text += f", {total_skipped} skipped"
    if skipped:
        worst = max(skipped, key=lambda key: skipped[key])
        text += f" (mostly: {worst})"
    return text + "."

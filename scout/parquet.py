"""Read Parquet files for conversion (T62).

Parquet support is optional and lazy: the reader (pyarrow) is
imported only when a Parquet file is actually converted, so the
app starts, searches, and converts everything else without it.
When it is not installed, Parquet files are refused in plain
words instead of failing.

Why pyarrow — measured 2026-11-15 in the test venv:
- pyarrow 26.0.1: 169 MB on disk. `import pyarrow.parquet`
  adds about 34 MB peak RSS. It bundles every codec (snappy,
  zstd, gzip), so any Parquet file Hugging Face serves can be
  read, and it streams row batches without pandas.
- fastparquet 2026.9.0: 9 MB on disk, but it needs numpy (69 MB)
  and pandas (78 MB) to hand back rows, so the real stack is
  about 156 MB on disk, and importing it added 51-76 MB peak
  RSS in the same measurement — heavier than pyarrow in memory,
  with weaker codec coverage (snappy/zstd need extra packages).
So pyarrow is the reader: no heavier in practice, more capable.
A 20,000-row Parquet file converts through the streaming reader
below with a 6.8 MB peak RSS rise over the import baseline.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from . import convert

READER_NAME = "pyarrow"
BATCH_SIZE = 1000

READER_MISSING_MESSAGE = (
    "Scout needs the Parquet reader (pyarrow) to convert this "
    "file, and it is not installed."
)


class ParquetError(Exception):
    """A Parquet file could not be read; the message is plain words."""


def _load_pyarrow():
    """Import pyarrow.parquet, lazily (the seam tests patch to
    simulate a missing reader). Raises ImportError when absent."""
    import pyarrow.parquet as pq

    return pq


def reader_available() -> bool:
    """True when the Parquet reader can be imported."""
    try:
        _load_pyarrow()
    except ImportError:
        return False
    return True


def _plain_value(value):
    """A Parquet cell value as plain Python (JSON-safe).

    Bytes decode as text, dates become ISO strings, anything else
    unusual becomes its string form — a row must never crash the
    converter on a type it did not expect.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain_value(item)
                for key, item in value.items()}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def convert_parquet_file(path: Path | str,
                         batch_size: int = BATCH_SIZE
                         ) -> tuple[list[dict], dict]:
    """Convert a .parquet file to STELLA rows. Returns (rows, report).

    Reads in batches so a big file never sits whole in memory.
    The converter's rules hold: text-only rows are skipped, never
    faked; one format per file; at least 2 rows required (checked
    by the caller with convert.validate_rows, as for every kind).
    Raises ParquetError when the reader is missing or the file
    cannot be read.
    """
    try:
        pq = _load_pyarrow()
    except ImportError as exc:
        raise ParquetError(READER_MISSING_MESSAGE) from exc
    path = Path(path)
    try:
        parquet_file = pq.ParquetFile(path)
    except Exception as exc:  # noqa: BLE001 - pyarrow raises its own errors
        raise ParquetError(
            f"could not read {path.name} as a Parquet file: {exc}"
        ) from exc
    rows: list[dict] = []
    skipped: dict[str, int] = {}
    fmt: str | None = None
    seen = 0
    try:
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            for raw in batch.to_pylist():
                seen += 1
                # A Parquet column holds null where a row has no
                # value; a null is an absent field, not an empty
                # one, so drop nulls before mapping. Otherwise a
                # text-only row in a file with prompt columns
                # would read as "prompt is empty" instead of
                # "text only", and a messages row would never
                # reach the one-format check.
                obj = {str(key): _plain_value(value)
                       for key, value in raw.items()
                       if value is not None}
                row, reason = convert.map_row(obj)
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
    except Exception as exc:  # noqa: BLE001 - a bad batch must not crash the run
        raise ParquetError(
            f"could not read {path.name} as a Parquet file: {exc}"
        ) from exc
    report = {"seen": seen, "kept": len(rows),
              "skipped": skipped, "format": fmt or ""}
    return rows, report

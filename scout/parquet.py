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

import http.client
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

from . import convert

READER_NAME = "pyarrow"
BATCH_SIZE = 1000

READER_MISSING_MESSAGE = (
    "Scout needs the Parquet reader (pyarrow) to convert this "
    "file, and it is not installed. Run the installer again to "
    "add it."
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


class _RangedFile:
    """A seekable read-only file over HTTP Range requests.

    Parquet keeps its metadata (including the column names) in a
    footer at the end of the file, so reading just the footer
    answers "what columns does this file hold" without
    downloading the data. Each read is one ranged GET.
    """

    def __init__(self, url: str, size: int, timeout: int = 20):
        self._url = url
        self._size = size
        self._timeout = timeout
        self._pos = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        # pyarrow checks this before reading (NativeFile._assert_open).
        return self._closed

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            target = offset
        elif whence == 1:
            target = self._pos + offset
        elif whence == 2:
            target = self._size + offset
        else:
            raise ValueError(f"Unknown whence: {whence}")
        self._pos = max(0, min(self._size, target))
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self._size - self._pos
        if size <= 0 or self._pos >= self._size:
            return b""
        end = min(self._size - 1, self._pos + size - 1)
        request = urllib.request.Request(
            self._url, method="GET",
            headers={"Range": f"bytes={self._pos}-{end}",
                     "User-Agent": "DatasetScout"})
        with urllib.request.urlopen(request,
                                    timeout=self._timeout) as response:
            data = response.read()
        self._pos += len(data)
        return data

    def close(self) -> None:
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _remote_size(url: str, timeout: int = 20) -> int | None:
    """The size a URL declares, via HEAD, or None when unknown."""
    request = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "DatasetScout"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except (urllib.error.URLError, OSError, ValueError,
            http.client.HTTPException):
        return None


def read_remote_columns(url: str, timeout: int = 20) -> list[str]:
    """Column names of a Parquet file at a URL (v0.1.4 T10).

    Only the file's footer is fetched, never its rows. Raises
    ParquetError when the reader is missing, the size is unknown,
    or the footer cannot be read.
    """
    try:
        pq = _load_pyarrow()
    except ImportError as exc:
        raise ParquetError(READER_MISSING_MESSAGE) from exc
    size = _remote_size(url, timeout)
    if size is None or size <= 0:
        raise ParquetError(
            "could not read the file to check its shape")
    try:
        parquet_file = pq.ParquetFile(_RangedFile(url, size, timeout))
        return list(parquet_file.schema_arrow.names)
    except Exception as exc:  # noqa: BLE001 - pyarrow raises its own errors
        raise ParquetError(
            f"could not read the file as a Parquet file: {exc}"
        ) from exc


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

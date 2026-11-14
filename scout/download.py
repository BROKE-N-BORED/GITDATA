"""Download datasets and convert them to STELLA .jsonl (T35).

No Qt here: the GUI worker calls download_and_convert() and gets a
plain report back. Stdlib plus apis and convert only.

What it handles:
- Hugging Face datasets: lists the files through the API, downloads
  the convertible ones (.jsonl, .json, .csv, .tsv), converts each.
- GitHub repos: downloads the repo archive, finds the convertible
  files inside, converts each.
- A direct link to a .jsonl, .json, .csv, or .tsv file.

What it does NOT handle, said plainly in the report:
- Parquet and Arrow files (need a heavy dependency Scout does not
  ship). The report names them as skipped, with the reason.
- Anything else: a plain message saying what Scout can download.

Safety caps: a repo archive over 500 MB is refused; a data file
over 200 MB is skipped; at most 20 files per dataset are converted.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from . import apis, convert, core

MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
MAX_DATA_FILE_BYTES = 200 * 1024 * 1024
MAX_FILES_PER_ITEM = 20


def safe_name(text: str) -> str:
    """A title or id as a safe folder name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip())
    cleaned = cleaned.strip("-._") or "dataset"
    return cleaned[:80]


def _extension(name: str) -> str:
    lowered = name.lower()
    for ext in (".jsonl", ".json", ".csv", ".tsv", ".parquet", ".arrow"):
        if lowered.endswith(ext):
            return ext
    return ""


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _convert_file(path: Path, out_path: Path) -> tuple[bool, str, int]:
    """Convert one downloaded file. Returns (ok, message, rows)."""
    ext = _extension(path.name)
    kind = convert.CONVERTIBLE.get(ext)
    if kind is None:
        note = convert.UNSUPPORTED_NOTE.get(
            ext, "Scout cannot convert this kind of file.")
        return False, f"skipped {path.name}: {note}", 0
    try:
        text = _read_text(path)
        rows, report = convert.convert_text(text, kind)
    except OSError as exc:
        return False, f"could not read {path.name}: {exc}", 0
    ok, problem = convert.validate_rows(rows)
    if not ok:
        return False, (
            f"skipped {path.name}: {problem}. "
            f"{convert.describe_report(report)}"), 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(convert.rows_to_jsonl(rows), encoding="utf-8")
    return True, (f"{path.name}: {len(rows)} rows. "
                  f"{convert.describe_report(report)}"), len(rows)


def _hf_dataset_id(item: dict) -> str:
    url = item.get("url") or ""
    if "/datasets/" in url:
        return url.split("/datasets/", 1)[-1].strip("/")
    return (item.get("title") or "").strip()


def _github_repo(item: dict) -> str:
    url = item.get("url") or ""
    if "github.com/" not in url:
        return ""
    parts = url.split("github.com/", 1)[-1].split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def download_and_convert(item: dict, out_dir: Path, token: str = "",
                         progress=None, stop=None) -> dict:
    """Download one item's data files and convert them.

    progress(stage, downloaded, total) reports plain-words stages
    ("Downloading openhermes2_5.json", ...) with byte counts; total
    is None when the server does not say. stop is a threading.Event;
    when set, the run ends after the current file.

    Returns {"ok", "message", "outputs", "rows", "state"} where
    state is "downloaded" (files fetched, nothing converted) or
    "converted" (at least one .jsonl written), or "found" when
    nothing could be fetched at all.
    """
    report: dict = {"ok": False, "message": "", "outputs": [],
                    "rows": 0, "state": "found"}
    notes: list[str] = []
    source = item.get("source") or ""
    work_dir = Path(out_dir) / (".tmp-" + safe_name(item.get("title") or "item"))
    target_dir = Path(out_dir) / safe_name(item.get("title") or "item")
    files: list[tuple[str, Path]] = []  # (display name, local path)

    def stopped() -> bool:
        return stop is not None and stop.is_set()

    def say(stage: str, downloaded: int = 0, total=None):
        if progress is not None:
            progress(stage, downloaded, total)

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        if source == "Hugging Face":
            dataset_id = _hf_dataset_id(item)
            if not dataset_id:
                report["message"] = ("Could not tell which Hugging Face "
                                     "dataset this link is.")
                return report
            say(f"Listing files for {dataset_id}")
            found = apis.sniff_huggingface(dataset_id)
            if found["status"] == "unknown":
                report["message"] = (f"Could not list the dataset files: "
                                     f"{found['detail']}")
                return report
            picked = [name for name in found["files"]
                      if _extension(name) in convert.CONVERTIBLE]
            for name in found["files"]:
                ext = _extension(name)
                if ext and ext not in convert.CONVERTIBLE:
                    notes.append(
                        f"skipped {name}: "
                        f"{convert.UNSUPPORTED_NOTE.get(ext, 'Scout cannot convert this kind of file.')}")
            if not picked:
                report["message"] = ("No convertible files found. " +
                                     (" ".join(notes) if notes else
                                      "The dataset holds no .jsonl, .json, .csv, or .tsv files."))
                return report
            for name in picked[:MAX_FILES_PER_ITEM]:
                if stopped():
                    break
                dest = work_dir / Path(name).name
                say(f"Downloading {name}")
                fetched = False
                last_error = ""
                for branch in ("main", "master"):
                    try:
                        apis.download_file(
                            apis.hf_file_url(dataset_id, name, branch),
                            dest,
                            progress=lambda got, total, n=name: say(
                                f"Downloading {n}", got, total))
                        fetched = True
                        break
                    except apis.SearchError as exc:
                        last_error = str(exc)
                if not fetched:
                    notes.append(f"could not download {name}: {last_error}")
                    continue
                files.append((name, dest))
            if len(picked) > MAX_FILES_PER_ITEM:
                notes.append(
                    f"only the first {MAX_FILES_PER_ITEM} files were tried")
        elif source == "GitHub":
            repo = _github_repo(item)
            if not repo:
                report["message"] = ("Could not tell which GitHub repo "
                                     "this link is.")
                return report
            say(f"Listing files for {repo}")
            found = apis.sniff_github(repo, token)
            if found["status"] == "unknown":
                report["message"] = (f"Could not list the repo files: "
                                     f"{found['detail']}")
                return report
            branch = found.get("branch") or "main"
            archive = work_dir / "repo.zip"
            say(f"Downloading {repo} ({branch})")
            try:
                size = apis.download_file(
                    apis.github_archive_url(repo, branch), archive,
                    progress=lambda got, total: say(
                        f"Downloading {repo}", got, total))
            except apis.SearchError as exc:
                report["message"] = str(exc)
                return report
            if size > MAX_ARCHIVE_BYTES:
                report["message"] = (
                    f"The repo archive is {core.format_size(size)}; "
                    "Scout stops at 500 MB.")
                return report
            extract_dir = work_dir / "repo"
            extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(archive) as zf:
                    for member in zf.infolist():
                        dest = (extract_dir / member.filename).resolve()
                        if not str(dest).startswith(str(extract_dir.resolve())):
                            continue  # zip-slip guard
                        zf.extract(member, extract_dir)
            except zipfile.BadZipFile:
                report["message"] = "The downloaded archive is not a zip file."
                return report
            for path in sorted(extract_dir.rglob("*")):
                if stopped():
                    break
                if not path.is_file():
                    continue
                if len(files) >= MAX_FILES_PER_ITEM:
                    notes.append(
                        f"only the first {MAX_FILES_PER_ITEM} files were tried")
                    break
                ext = _extension(path.name)
                if not ext:
                    continue
                if ext not in convert.CONVERTIBLE:
                    notes.append(
                        f"skipped {path.name}: "
                        f"{convert.UNSUPPORTED_NOTE.get(ext, 'Scout cannot convert this kind of file.')}")
                    continue
                if path.stat().st_size > MAX_DATA_FILE_BYTES:
                    notes.append(
                        f"skipped {path.name}: over the 200 MB file limit")
                    continue
                files.append((str(path.relative_to(extract_dir)), path))
            if not files and not notes:
                report["message"] = ("No convertible files found in the repo.")
                return report
        else:
            url = item.get("url") or ""
            ext = _extension(url.split("?", 1)[0])
            if not ext or ext not in convert.CONVERTIBLE:
                report["message"] = (
                    "Scout can only download Hugging Face and GitHub "
                    "links, or a direct link to a .jsonl, .json, .csv, "
                    "or .tsv file.")
                return report
            dest = work_dir / Path(url.split("?", 1)[0]).name
            say(f"Downloading {dest.name}")
            try:
                apis.download_file(
                    url, dest,
                    progress=lambda got, total: say(
                        f"Downloading {dest.name}", got, total))
            except apis.SearchError as exc:
                report["message"] = str(exc)
                return report
            files.append((dest.name, dest))

        if not files:
            report["message"] = ("Nothing could be downloaded. " +
                                 " ".join(notes) if notes else
                                 "Nothing could be downloaded.")
            return report
        report["state"] = "downloaded"
        outputs: list[str] = []
        total_rows = 0
        for display, path in files:
            if stopped():
                notes.append("stopped part way through")
                break
            say(f"Converting {display}")
            out_path = target_dir / (Path(display).stem + ".jsonl")
            ok, message, rows = _convert_file(path, out_path)
            notes.append(message)
            if ok:
                outputs.append(str(out_path))
                total_rows += rows
        if outputs:
            report.update({"ok": True, "outputs": outputs,
                           "rows": total_rows, "state": "converted"})
            report["message"] = (
                f"Wrote {len(outputs)} file(s), {total_rows} rows, to "
                f"{target_dir}. " + " ".join(notes))
        else:
            report["message"] = "Nothing converted. " + " ".join(notes)
        return report
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

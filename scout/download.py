"""Download datasets and convert them to STELLA .jsonl (T35).

No Qt here: the GUI worker calls download_and_convert() and gets a
plain report back. Stdlib plus apis and convert only.

What it handles:
- Hugging Face datasets: lists the files through the API, downloads
  the convertible ones (.jsonl, .json, .csv, .tsv, .parquet),
  converts each.
- GitHub repos: downloads the repo archive, finds the convertible
  files inside, converts each.
- A direct link to a .jsonl, .json, .csv, .tsv, or .parquet file.

What it does NOT handle, said plainly in the report:
- Arrow files (Scout cannot read Arrow). The report names them
  as skipped, with the reason.
- Parquet files when the optional Parquet reader (pyarrow) is
  not installed: the report says the reader is missing instead
  of downloading a file it cannot convert.
- Anything else: a plain message saying what Scout can download.

Safety caps: a repo archive over 500 MB is refused; a data file
over 200 MB is skipped; at most 20 files per dataset are converted;
an archive may expand to at most 2 GB across 10,000 files.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from . import apis, convert, core, parquet

MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
MAX_DATA_FILE_BYTES = 200 * 1024 * 1024
MAX_FILES_PER_ITEM = 20
# How much an archive may expand on extraction (v0.1.3 T29):
# a zip bomb must not fill the disk.
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_EXTRACTED_MEMBERS = 10000


def safe_name(text: str) -> str:
    """A title or id as a safe folder name."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip())
    cleaned = cleaned.strip("-._") or "dataset"
    return cleaned[:80]


def dataset_folder_name(item: dict) -> str:
    """The output folder for one dataset, unique per dataset (T2).

    The title plus a short hash of the source and URL: two different
    datasets that share a title get different folders, and the same
    dataset re-run lands in the same folder as before.
    """
    title = safe_name(item.get("title") or "item")
    key = f"{item.get('source') or ''}\0{item.get('url') or ''}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"{title}-{digest}"


def output_name(display: str) -> str:
    """A converted output name that keeps the source folder in it.

    "train/train.jsonl" becomes "train-train.jsonl" and
    "validation/train.jsonl" becomes "validation-train.jsonl", so
    two files that share a basename never write to the same output
    (T1). Dots in folder names are kept; the suffix is .jsonl.
    """
    parts = [part for part in Path(display).parts
             if part not in ("", ".", "..")]
    if not parts:
        return "file.jsonl"
    stem = Path(parts[-1]).stem or "file"
    base = "-".join(parts[:-1] + [stem]) if len(parts) > 1 else stem
    return safe_name(base) + ".jsonl"


def _unconvertible_description(files: list[str]) -> str:
    """What a file listing holds, in plain words (v0.1.4 T30).

    "only .png and .jpg files" — so an image-only dataset is
    named, not just denied. "" when there is nothing to name.
    """
    kinds = sorted({Path(name).suffix.lower() for name in files
                    if Path(name).suffix})
    if not kinds:
        return ""
    if len(kinds) == 1:
        return f"only {kinds[0]} files"
    if len(kinds) > 4:
        return f"mostly {', '.join(kinds[:4])} files"
    return "only " + ", ".join(kinds[:-1]) + f" and {kinds[-1]} files"


def _over_cap_message() -> str:
    """The plain refusal for a file over the 200 MB cap."""
    return ("Download stopped: the file is bigger than "
            f"{core.format_size(MAX_DATA_FILE_BYTES)}.")


def _safe_relative(name: str) -> Path:
    """A file path from a dataset listing, kept inside its folder."""
    parts = [part for part in Path(name).parts
             if part not in ("", ".", "..")]
    return Path(*parts) if parts else Path("file")


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


def _convert_file(path: Path, out_path: Path,
                  display: str | None = None) -> tuple[bool, str, int]:
    """Convert one downloaded file. Returns (ok, message, rows).

    A bad file is a per-file failure, never an exception out (T9):
    a giant CSV field, a disk-full write, or a permission error is
    reported and the rest of the dataset still converts. A failed
    write leaves no partial output behind. `display` is the name
    shown in messages: the folder-qualified path, so two files
    that share a basename are told apart (v0.1.4 T30).
    """
    shown = display or path.name
    ext = _extension(path.name)
    kind = convert.CONVERTIBLE.get(ext)
    if kind is None:
        note = convert.UNSUPPORTED_NOTE.get(
            ext, "Scout cannot convert this kind of file.")
        return False, f"skipped {shown}: {note}", 0
    if kind == "parquet":
        # Parquet is read from the file, not from text, and needs
        # the optional reader (T62): a missing reader is a plain
        # refusal, not a crash.
        try:
            rows, report = parquet.convert_parquet_file(path)
        except parquet.ParquetError as exc:
            return False, f"skipped {shown}: {exc}", 0
    else:
        try:
            text = _read_text(path)
            rows, report = convert.convert_text(text, kind)
        except OSError as exc:
            return False, f"could not read {shown}: {exc}", 0
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the run
            return False, f"could not convert {shown}: {exc}", 0
    ok, problem = convert.validate_rows(rows)
    if not ok:
        return False, (
            f"skipped {shown}: {problem}. "
            f"{convert.describe_report(report)}"), 0
    try:
        # Atomic (v0.1.3 T21): a failed write leaves no partial
        # output behind, and a crash never leaves a half-written
        # .jsonl.
        core.write_text_atomic(
            out_path, convert.rows_to_jsonl(rows))
    except OSError as exc:
        return False, f"could not write {out_path.name}: {exc}", 0
    # A clean convert adds no note: the run's one-line summary
    # carries the row count once (T50), so repeating it here
    # would print the same number three times. Skipped rows are
    # still named (U32: never silently skip).
    skipped = report.get("skipped") or {}
    total_skipped = sum(skipped.values())
    if total_skipped:
        worst = max(skipped, key=lambda key: skipped[key])
        row_word = "row" if total_skipped == 1 else "rows"
        note = (f"{shown}: {total_skipped} {row_word} skipped "
                f"(mostly: {worst}).")
    else:
        note = ""
    return True, note, len(rows)


def _hf_dataset_id(item: dict) -> str:
    url = item.get("url") or ""
    if "/datasets/" in url:
        return core.clean_source_id(url.split("/datasets/", 1)[-1])
    return core.clean_source_id(item.get("title") or "")


def _github_repo(item: dict) -> str:
    url = item.get("url") or ""
    if "github.com/" not in url:
        return ""
    parts = url.split("github.com/", 1)[-1].split("/")
    if len(parts) >= 2:
        return core.clean_source_id(f"{parts[0]}/{parts[1]}")
    return ""


def _cleanup_work_dir(work_dir: Path) -> None:
    """Clear the work folder, keeping .part files (v0.1.3 T27).

    A failed download's .part file is the resume state for the
    next run, so it stays; everything else goes, and empty
    folders go with it. The work folder itself is removed only
    when no .part file remains.
    """
    if not work_dir.is_dir():
        return
    for path in sorted(work_dir.rglob("*"), reverse=True):
        try:
            if path.is_dir():
                path.rmdir()
            elif path.suffix != ".part":
                path.unlink()
        except OSError:
            pass
    try:
        work_dir.rmdir()
    except OSError:
        pass


def download_and_convert(item: dict, out_dir: Path, token: str = "",
                         progress=None, stop=None) -> dict:
    """Download one item's data files and convert them.

    progress(stage, downloaded, total) reports plain-words stages
    ("Downloading openhermes2_5.json", ...) with byte counts; total
    is None when the server does not say. stop is a threading.Event;
    when set, the run ends as soon as the current download or step
    notices it.

    Returns {"ok", "message", "outputs", "rows", "state"} where
    state is "converted" (at least one .jsonl written) or "found"
    (nothing converted: the fetched files are deleted after the
    run, so "downloaded" would be a lie).
    """
    report: dict = {"ok": False, "message": "", "outputs": [],
                    "rows": 0, "state": "found"}
    notes: list[str] = []
    source = item.get("source") or ""
    if source not in ("Hugging Face", "GitHub"):
        # A hand-added link (v0.1.4 T3): the source follows the
        # URL, so a list saved before the fix still downloads.
        source = core.source_for_url(item.get("url") or "")
    folder = dataset_folder_name(item)
    work_dir = Path(out_dir) / (".tmp-" + folder)
    target_dir = Path(out_dir) / folder
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
            try:
                found = apis.sniff_huggingface(dataset_id)
            except core.RateLimitedError as exc:
                # The sniff itself treats a rate limit as "unknown";
                # this is a second net so a download never dies on one.
                found = {"status": "unknown", "files": [],
                         "total_size": None, "detail": str(exc)}
            # "unknown" with files means the peek failed, not the
            # listing (v0.1.3 T26): the download is still tried.
            if found["status"] == "unknown" and not found["files"]:
                report["message"] = (f"Could not list the dataset files: "
                                     f"{found['detail']}")
                return report
            if found["status"] == "needs-reader":
                # Parquet-only, and the reader is not installed
                # (T62): say so before downloading anything.
                report["message"] = found["detail"]
                return report
            picked = [name for name in found["files"]
                      if _extension(name) in convert.CONVERTIBLE]
            for name in found["files"]:
                ext = _extension(name)
                if ext and ext not in convert.CONVERTIBLE:
                    notes.append(
                        f"skipped {name}: "
                        f"{convert.UNSUPPORTED_NOTE.get(ext, 'Scout cannot convert this kind of file.')}")
                elif not ext and apis.compressed_data_ext(name):
                    # A compressed data file is named, not denied
                    # (v0.1.3 T34).
                    notes.append(apis.compressed_note(name))
            if not picked:
                if notes:
                    detail = " ".join(notes)
                else:
                    # Name what the dataset holds (v0.1.4 T30):
                    # an image-only dataset says so, instead of
                    # just listing the extensions it lacks.
                    held = _unconvertible_description(found["files"])
                    if held:
                        detail = f"The dataset holds {held}."
                    else:
                        detail = ("The dataset holds no .jsonl, "
                                  ".json, .csv, .tsv, or .parquet files.")
                report["message"] = ("No convertible files found. "
                                     + detail)
                return report
            for name in picked[:MAX_FILES_PER_ITEM]:
                if stopped():
                    break
                # Refuse an over-cap file from its declared size
                # before downloading it (v0.1.4 T25): the download
                # itself still stops at the cap if the server lied.
                declared = apis.content_length(
                    apis.hf_file_url(dataset_id, name, "main"))
                if (declared is not None
                        and declared > MAX_DATA_FILE_BYTES):
                    notes.append(
                        f"skipped {name}: over the 200 MB file limit")
                    continue
                # Keep the folder in the work path: two files that
                # share a basename in different folders must not
                # overwrite each other before converting (T1).
                dest = work_dir / _safe_relative(name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                say(f"Downloading {name}")
                fetched = False
                last_error = ""
                for branch in ("main", "master"):
                    try:
                        apis.download_file(
                            apis.hf_file_url(dataset_id, name, branch),
                            dest,
                            progress=lambda got, total, n=name: say(
                                f"Downloading {n}", got, total),
                            limit=MAX_DATA_FILE_BYTES,
                            stop=stop)
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
            try:
                found = apis.sniff_github(repo, token)
            except core.RateLimitedError as exc:
                found = {"status": "unknown", "files": [],
                         "total_size": None, "detail": str(exc)}
            # "unknown" with files means the peek failed, not the
            # listing (v0.1.3 T26): the download is still tried.
            if found["status"] == "unknown" and not found["files"]:
                report["message"] = (f"Could not list the repo files: "
                                     f"{found['detail']}")
                return report
            if found["status"] == "needs-reader":
                # Parquet-only, and the reader is not installed
                # (T62): say so before downloading anything.
                report["message"] = found["detail"]
                return report
            branch = found.get("branch") or "main"
            archive = work_dir / "repo.zip"
            say(f"Downloading {repo} ({branch})")
            # Refuse an over-large archive before downloading it
            # (T10): the declared size is checked first, and the
            # download itself stops at the cap if the server lied.
            archive_url = apis.github_archive_url(repo, branch)
            declared = apis.content_length(archive_url)
            if declared is not None and declared > MAX_ARCHIVE_BYTES:
                report["message"] = (
                    f"The repo archive is {core.format_size(declared)}; "
                    "Scout stops at 500 MB.")
                return report
            try:
                size = apis.download_file(
                    archive_url, archive,
                    progress=lambda got, total: say(
                        f"Downloading {repo}", got, total),
                    limit=MAX_ARCHIVE_BYTES,
                    stop=stop)
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
            # Extraction caps (v0.1.3 T29): total expanded bytes
            # and member count, checked as extraction runs, so a
            # zip bomb cannot fill the disk. The header size is
            # checked before writing each member, and the real
            # size after, in case the header lied.
            extracted_total = 0
            member_count = 0
            try:
                with zipfile.ZipFile(archive) as zf:
                    root = extract_dir.resolve()
                    for member in zf.infolist():
                        if member.is_dir():
                            continue
                        member_count += 1
                        if member_count > MAX_EXTRACTED_MEMBERS:
                            report["message"] = (
                                "The repo archive holds too many files "
                                f"(over {MAX_EXTRACTED_MEMBERS}); Scout "
                                "stops there.")
                            return report
                        dest = (extract_dir / member.filename).resolve()
                        if not dest.is_relative_to(root):
                            continue  # zip-slip guard (v0.1.3 T38)
                        if (extracted_total + member.file_size
                                > MAX_EXTRACTED_BYTES):
                            report["message"] = (
                                "The repo archive would expand to over "
                                f"{core.format_size(MAX_EXTRACTED_BYTES)}; "
                                "Scout stops there.")
                            return report
                        zf.extract(member, extract_dir)
                        try:
                            extracted_total += dest.stat().st_size
                        except OSError:
                            pass
            except zipfile.BadZipFile:
                report["message"] = "The downloaded archive is not a zip file."
                return report
            seen_names: list[str] = []
            for path in sorted(extract_dir.rglob("*")):
                if stopped():
                    break
                if not path.is_file():
                    continue
                seen_names.append(path.name)
                if len(files) >= MAX_FILES_PER_ITEM:
                    notes.append(
                        f"only the first {MAX_FILES_PER_ITEM} files were tried")
                    break
                ext = _extension(path.name)
                if not ext:
                    if apis.compressed_data_ext(path.name):
                        # A compressed data file is named, not
                        # denied (v0.1.3 T34).
                        notes.append(apis.compressed_note(path.name))
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
            if not files:
                # The archive downloaded and opened: say what it
                # held (or did not), not that nothing downloaded.
                if notes:
                    detail = " " + " ".join(notes)
                else:
                    held = _unconvertible_description(seen_names)
                    if held:
                        detail = f" The repo holds {held}."
                    else:
                        detail = ""
                report["message"] = (
                    "No convertible files found in the repo." + detail)
                return report
        else:
            url = item.get("url") or ""
            clean_url = url.split("?", 1)[0]
            ext = _extension(clean_url)
            if not ext or ext not in convert.CONVERTIBLE:
                description = apis.compressed_description(
                    Path(clean_url).name)
                if description:
                    # A compressed data file is named, not denied
                    # (v0.1.3 T34).
                    report["message"] = (
                        f"That link points to {description}, which "
                        "Scout cannot convert yet.")
                else:
                    report["message"] = (
                        "Scout can only download Hugging Face and GitHub "
                        "links, or a direct link to a .jsonl, .json, .csv, "
                        ".tsv, or .parquet file.")
                return report
            if ext == ".parquet" and not parquet.reader_available():
                # Refuse before downloading a file that cannot be
                # converted (T62).
                report["message"] = parquet.READER_MISSING_MESSAGE
                return report
            # Refuse an over-cap file from its declared size
            # before downloading it (v0.1.4 T25): the download
            # itself still stops at the cap if the server lied.
            declared = apis.content_length(url)
            if declared is not None and declared > MAX_DATA_FILE_BYTES:
                report["message"] = _over_cap_message()
                return report
            dest = work_dir / Path(clean_url).name
            say(f"Downloading {dest.name}")
            try:
                apis.download_file(
                    url, dest,
                    progress=lambda got, total: say(
                        f"Downloading {dest.name}", got, total),
                    limit=MAX_DATA_FILE_BYTES,
                    stop=stop)
            except apis.SearchError as exc:
                report["message"] = str(exc)
                return report
            files.append((dest.name, dest))

        if not files:
            report["message"] = ("Nothing could be downloaded. " +
                                 " ".join(notes) if notes else
                                 "Nothing could be downloaded.")
            return report
        outputs: list[str] = []
        total_rows = 0
        used_names: set[str] = set()
        for display, path in files:
            if stopped():
                notes.append("stopped part way through")
                break
            say(f"Converting {display}")
            # The 200 MB cap is enforced here too, for every branch
            # (T10): a file that arrived over the cap is skipped
            # before it is read into memory.
            try:
                file_size = path.stat().st_size
            except OSError:
                file_size = 0
            if file_size > MAX_DATA_FILE_BYTES:
                notes.append(
                    f"skipped {display}: over the 200 MB file limit")
                continue
            out_name = output_name(display)
            if out_name in used_names:
                # Two different folders that flatten to the same
                # name ("a-b/train.jsonl" vs "a/b-train.jsonl") get
                # a number, so no output ever overwrites another.
                stem = out_name[:-len(".jsonl")]
                number = 2
                while f"{stem}-{number}.jsonl" in used_names:
                    number += 1
                out_name = f"{stem}-{number}.jsonl"
            used_names.add(out_name)
            out_path = target_dir / out_name
            try:
                ok, message, rows = _convert_file(path, out_path, display)
            except Exception as exc:  # noqa: BLE001 - one bad file must not kill the run
                notes.append(f"could not convert {display}: {exc}")
                continue
            if message:
                notes.append(message)
            if ok:
                outputs.append(str(out_path))
                total_rows += rows
        if outputs:
            # A re-run must not leave last run's outputs behind
            # (v0.1.3 T43): .jsonl files in the target folder that
            # this run did not write are stale, so they go. Only
            # on success: a failed run keeps the previous outputs.
            fresh = {Path(out).name for out in outputs}
            for stale in target_dir.glob("*.jsonl"):
                if stale.name not in fresh:
                    try:
                        stale.unlink()
                    except OSError:
                        pass
            report.update({"ok": True, "outputs": outputs,
                           "rows": total_rows, "state": "converted"})
            # One line, one number (T50): the count appears once,
            # with no full path. Problem notes (skipped files)
            # follow it; clean converts add nothing.
            word = "file" if len(outputs) == 1 else "files"
            report["message"] = (
                f"Converted {total_rows} rows into {len(outputs)} "
                f"{word}.")
            if notes:
                report["message"] += " " + " ".join(notes)
        else:
            report["message"] = "Nothing converted. " + " ".join(notes)
        return report
    except apis.DownloadStopped:
        # The user stopped the run mid-download (v0.1.3 T28): the
        # .part file stays for the next run, and the report says
        # plainly that the run stopped.
        report["ok"] = False
        report["state"] = "found"
        report["message"] = "Stopped part way through."
        return report
    finally:
        _cleanup_work_dir(work_dir)

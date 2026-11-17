"""Hand a converted dataset to a STELLA project (T64).

STELLA's project format, read from the STELLA repo
(stella/model.py, STELLA 0.24.0):

  ~/.stella/profiles/<profile-slug>/profile.json
  ~/.stella/profiles/<profile-slug>/projects/<project-slug>/project.json
  project.json holds: stella_version, kind "project", name,
  display_name, profile, dataset_ref, settings, settings_source,
  metamorph {mode, notes}, status "draft", created. A project
  folder also holds a checkpoints/ folder and notes.md.

dataset_ref resolves as an absolute path, or relative to the
project dir, then the STELLA home. Scout copies the .jsonl into
the project folder and sets dataset_ref to the file name, so
the project is self-contained: deleting Scout's output folder
later does not break the training run.

Safety, in order: Scout never writes into a project it cannot
read and validate. Anything it overwrites is backed up first
(<name>.scout-bak). A project whose manifest does not look like
the format above is left alone, and the caller falls back to
writing the manifest link. A project that already points at a
different dataset is left alone unless the user explicitly
chooses to replace it. After writing, Scout re-reads the
manifest and checks the dataset resolves, and restores the
backup if the check fails.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import core

# STELLA's slug rule (model.py SLUG_RE): lowercase letters,
# digits, dash, underscore; must start alnum; max 64 chars.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_SLUG_LEN = 64

# The STELLA version this writer was built against. A project
# manifest with a different major version is not touched: the
# format may have changed.
KNOWN_STELLA_MAJOR = 0

# STELLA's default training settings (model.py DEFAULT_SETTINGS,
# STELLA 0.24.0), copied verbatim so a project Scout creates
# trains the way a project STELLA creates trains. They are
# stored with settings_source "manual": Scout chose them, they
# were not measured from this PC the way STELLA's auto-config
# would measure them.
STELLA_DEFAULT_SETTINGS = {
    "method": "lora",
    "rank": 8,
    "learning_rate": 0.0002,
    "epochs": 3,
    "batch_size": 1,
    "grad_accum": 8,
    "max_seq_len": 1024,
    "checkpoint_steps": 50,
    "val_fraction": 0.1,
    "warmup_steps": 5,
    "seed": 42,
    "target_modules": [],
}

BACKUP_SUFFIX = ".scout-bak"


def stella_slug(text: str, fallback: str = "dataset") -> str:
    """A title as a valid STELLA slug (STELLA's own rule)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower())
    slug = slug.strip("-")[:MAX_SLUG_LEN].rstrip("-")
    return slug or fallback


def _stella_home(home: Path | str | None) -> Path | None:
    if home is not None:
        path = Path(home)
        return path if path.is_dir() else None
    return core.stella_home()


def _read_manifest(path: Path) -> dict | None:
    """A STELLA manifest as a dict, or None when it cannot be read
    as one (missing, broken JSON, or not an object)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_manifest(path: Path, data: dict) -> None:
    """Write a STELLA manifest the way STELLA does: sorted keys,
    indent 2, tmp file + replace, so a crash never leaves a
    half-written manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True,
                      allow_nan=False) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def list_profiles(home: Path | str | None = None) -> list[dict]:
    """STELLA's profiles: [{"name", "display_name"}].

    A profile whose manifest cannot be read is skipped (STELLA
    itself flags it as damaged); it is never offered as a target.
    """
    stella = _stella_home(home)
    if stella is None:
        return []
    profiles_dir = stella / "profiles"
    if not profiles_dir.is_dir():
        return []
    out = []
    for folder in sorted(profiles_dir.iterdir()):
        if not folder.is_dir():
            continue
        data = _read_manifest(folder / "profile.json")
        if data is None:
            continue
        if data.get("kind") not in (None, "profile"):
            continue
        name = data.get("name") or folder.name
        if not SLUG_RE.match(str(name)):
            continue
        out.append({"name": str(name),
                    "display_name": str(data.get("display_name") or name)})
    return out


def list_projects(profile: str,
                  home: Path | str | None = None) -> list[str]:
    """Project slugs under one profile (readable manifests only)."""
    stella = _stella_home(home)
    if stella is None:
        return []
    projects_dir = stella / "profiles" / profile / "projects"
    if not projects_dir.is_dir():
        return []
    out = []
    for folder in sorted(projects_dir.iterdir()):
        if not folder.is_dir():
            continue
        data = _read_manifest(folder / "project.json")
        if data is None:
            continue
        out.append(str(data.get("name") or folder.name))
    return out


def validate_dataset_file(path: Path | str) -> tuple[bool, str, int, str]:
    """Check a .jsonl file against STELLA's file rules, streaming.

    Returns (ok, problem, rows, format). The file must hold at
    least 2 rows, one format (prompt or messages) throughout.
    """
    path = Path(path)
    if not path.is_file():
        return False, "that file does not exist", 0, ""
    fmt: str | None = None
    count = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return False, (
                        f"line {count + 1} is not valid JSON"), count, ""
                if not isinstance(obj, dict):
                    return False, (
                        f"line {count + 1} is not a JSON object"), count, ""
                if "messages" in obj:
                    row_fmt = "messages"
                elif "prompt" in obj or "completion" in obj:
                    row_fmt = "prompt"
                else:
                    return False, (
                        f"line {count + 1} has neither prompt nor "
                        "messages"), count, ""
                if fmt is None:
                    fmt = row_fmt
                if row_fmt != fmt:
                    return False, (
                        "the file mixes prompt rows and messages rows"
                    ), count, ""
                count += 1
    except OSError as exc:
        return False, f"could not read the file: {exc}", 0, ""
    if count < 2:
        return False, (
            f"only {count} usable row(s); STELLA needs at least 2"), count, ""
    return True, "", count, fmt or ""


def _version_major(version) -> int | None:
    try:
        return int(str(version).lstrip("v").split(".")[0])
    except (ValueError, IndexError):
        return None


def link_dataset(jsonl_path: Path | str, profile: str, project: str,
                 home: Path | str | None = None,
                 replace: bool = False,
                 display_name: str = "") -> dict:
    """Register a converted .jsonl in a STELLA project.

    Creates the project when it does not exist; links the dataset
    into it when it does. Returns {"ok", "message",
    "project_dir", "dataset_path", "backup", "created_project"}.
    On any doubt about the STELLA data, nothing is written and
    the message says so in plain words.
    """
    result: dict = {"ok": False, "message": "", "project_dir": None,
                    "dataset_path": None, "backup": None,
                    "created_project": False}
    stella = _stella_home(home)
    if stella is None:
        result["message"] = (
            "STELLA is not installed on this PC, so there is no "
            "STELLA project to add the dataset to. Export the "
            "STELLA manifest instead.")
        return result
    profile_slug = stella_slug(profile)
    project_slug = stella_slug(project)
    profile_dir = stella / "profiles" / profile_slug
    profile_data = _read_manifest(profile_dir / "profile.json")
    if profile_data is None:
        result["message"] = (
            f"STELLA has no profile called {profile_slug!r}. Create "
            "the profile in STELLA first, then send the dataset "
            "again.")
        return result
    if profile_data.get("kind") not in (None, "profile"):
        result["message"] = (
            f"The STELLA profile {profile_slug!r} does not look like "
            "a profile, so Scout left it alone.")
        return result
    ok, problem, rows, _fmt = validate_dataset_file(jsonl_path)
    if not ok:
        result["message"] = (
            f"That file cannot go into STELLA: {problem}.")
        return result
    jsonl_path = Path(jsonl_path)
    project_dir = profile_dir / "projects" / project_slug
    manifest_path = project_dir / "project.json"
    dataset_name = f"{project_slug}.jsonl"
    dataset_path = project_dir / dataset_name

    existing = None
    if manifest_path.is_file():
        existing = _read_manifest(manifest_path)
        if existing is None:
            result["message"] = (
                f"The STELLA project {project_slug!r} has a damaged "
                "record, so Scout left it alone. Fix it in STELLA "
                "first.")
            return result
        if existing.get("kind") not in (None, "project"):
            result["message"] = (
                f"The STELLA project {project_slug!r} does not look "
                "like a project, so Scout left it alone.")
            return result
        major = _version_major(existing.get("stella_version"))
        if major is not None and major != KNOWN_STELLA_MAJOR:
            result["message"] = (
                f"The STELLA project {project_slug!r} was made by a "
                "newer STELLA than Scout knows, so Scout left it "
                "alone rather than risk breaking it.")
            return result
        if "settings" not in existing:
            result["message"] = (
                f"The STELLA project {project_slug!r} is missing its "
                "settings, so Scout left it alone.")
            return result
        current_ref = str(existing.get("dataset_ref") or "").strip()
        if current_ref and current_ref != dataset_name and not replace:
            result["message"] = (
                f"The project {project_slug!r} already uses the "
                f"dataset {current_ref!r}. Pick another project, or "
                "choose to replace its dataset.")
            return result
        if current_ref == dataset_name and not replace:
            # The same-name case (v0.1.4 T2): the project already
            # holds a dataset file under this name, so compare
            # contents, not names. A different file is refused
            # unless the user chose to replace; the same file
            # links again harmlessly, and a missing file means
            # there is nothing to protect.
            try:
                same = (dataset_path.is_file()
                        and dataset_path.read_bytes()
                        == jsonl_path.read_bytes())
            except OSError:
                same = False
            if dataset_path.is_file() and not same:
                result["message"] = (
                    f"The project {project_slug!r} already holds a "
                    "different dataset. Pick another project, or "
                    "choose to replace its dataset.")
                return result

    backup_path = None
    try:
        if existing is not None:
            backup_path = manifest_path.with_name(
                manifest_path.name + BACKUP_SUFFIX)
            shutil.copyfile(manifest_path, backup_path)
            data = dict(existing)
            created = False
        else:
            stella_version = profile_data.get("stella_version") \
                or "0.24.0"
            data = {
                "stella_version": stella_version,
                "kind": "project",
                "name": project_slug,
                "display_name": display_name or project_slug,
                "profile": profile_slug,
                "dataset_ref": dataset_name,
                "settings": dict(STELLA_DEFAULT_SETTINGS),
                "settings_source": "manual",
                "metamorph": {"mode": "morph", "notes": ""},
                "status": "draft",
                "created": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
            }
            created = True
        data["dataset_ref"] = dataset_name
        if created:
            (project_dir / "checkpoints").mkdir(parents=True,
                                                exist_ok=True)
            (project_dir / "notes.md").write_text(
                f"# {display_name or project_slug}\n\n"
                "Notes for this fine-tune.\n",
                encoding="utf-8")
        # Copy the dataset in. An existing dataset file with
        # different content is backed up first; identical content
        # needs no copy at all.
        same = False
        if dataset_path.is_file():
            try:
                same = (dataset_path.read_bytes()
                        == jsonl_path.read_bytes())
            except OSError:
                same = False
            if not same:
                dataset_backup = dataset_path.with_name(
                    dataset_path.name + BACKUP_SUFFIX)
                shutil.copyfile(dataset_path, dataset_backup)
                if backup_path is None:
                    backup_path = dataset_backup
        if not dataset_path.is_file() or not same:
            shutil.copyfile(jsonl_path, dataset_path)
        _write_manifest(manifest_path, data)
    except OSError as exc:
        result["message"] = (
            f"Could not write into the STELLA project: {exc}")
        return result

    # Verify what was written, the way STELLA reads it back: the
    # manifest parses, and the dataset reference resolves to a
    # file. A failed check restores the backup, when there is one.
    check = _read_manifest(manifest_path)
    resolved = project_dir / str(check.get("dataset_ref") or "") \
        if check is not None else None
    if check is None or resolved is None or not resolved.is_file():
        if backup_path is not None and manifest_path.is_file():
            try:
                shutil.copyfile(backup_path, manifest_path)
            except OSError:
                pass
        result["message"] = (
            "Scout wrote the project but the check after writing "
            "failed, so the old project record was put back. "
            "Nothing was changed.")
        return result

    result["ok"] = True
    result["project_dir"] = str(project_dir)
    result["dataset_path"] = str(dataset_path)
    result["backup"] = str(backup_path) if backup_path else None
    result["created_project"] = created
    word = "row" if rows == 1 else "rows"
    if created:
        message = (
            f"Created the STELLA project {project_slug!r} in profile "
            f"{profile_slug!r} with {rows} {word} of training data. "
            "Open STELLA to train on it.")
    else:
        message = (
            f"Added {rows} {word} of training data to the STELLA "
            f"project {project_slug!r} in profile {profile_slug!r}. "
            "Open STELLA to train on it.")
    if backup_path is not None:
        message += f" The old file was backed up as {backup_path.name}."
    result["message"] = message
    return result

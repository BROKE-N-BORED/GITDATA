"""Dataset Scout self-updater (v0.1.5 U3-U6).

The flow, in plain words:

1. Download the release zip AND its SHA-256 sidecar from the same
   GitHub Release, and verify the zip against the sidecar before
   anything is installed. A mismatch, a missing sidecar, or a
   missing zip is a refusal: nothing changes.
2. A verified zip is staged as a pending update under
   ~/.dataset-scout/update/pending/ (never inside the installed
   tree, and it survives an app restart).
3. The user is asked: restart now, or later. Restart now applies
   the staged update and re-execs into the new version. Later
   leaves it staged; the next launch applies it before the window
   opens (see scout/__main__.py) and re-execs too.

The download uses GitHub's stable release-download URLs, not the
API, so the pull never touches the API rate limit:

    https://github.com/BROKE-N-BORED/GITDATA/releases/download/<tag>/<asset>

The zip asset is ``dataset-scout-<tag>.zip`` (tag with its "v",
e.g. ``dataset-scout-v0.1.5.zip``) and the sidecar is that name
plus ``.sha256``. The sidecar holds one line: the hex SHA-256
hash, bare or in ``sha256sum`` style ("<hash>  <filename>").

The apply is crash-safe: the zip is extracted to a staging folder
beside the installed tree (same filesystem, so the renames are
atomic), the old tree is renamed aside, the new tree is renamed
in, and the old tree is deleted only after the new one is verified
in place. The installed ``.venv`` is carried over into the new
tree, because the release zip never ships one and the launcher
needs it. Any failure mid-swap rolls back to the old tree, so a
failed apply leaves the old version runnable. User data in
~/.dataset-scout/ is never touched: the swap only replaces the
program folder.

No Qt is imported at module level: the download, verify, stage,
and apply logic is plain stdlib, so the launch hook and the tests
can use it without a GUI. The progress dialog, the restart
prompt, and the message boxes live in start_update() and import
Qt lazily.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import core

OWNER = "BROKE-N-BORED"
REPO = "GITDATA"

# Stable release-download URLs (no API call, no rate limit).
RELEASE_DOWNLOAD_BASE = (
    f"https://github.com/{OWNER}/{REPO}/releases/download"
)

USER_AGENT = f"DatasetScout/{core.VERSION}"

# A sidecar is about 100 bytes; anything over 1 MB is not a sidecar.
_SIDECAR_MAX_BYTES = 1024 * 1024

# A pending update that fails to apply this many times is dropped,
# so a permanently broken swap does not retry silently forever.
_MAX_APPLY_ATTEMPTS = 3

_HASH_RE = re.compile(r"[0-9a-fA-F]{64}")

# Plain-words refusals. Every one ends on "nothing was changed",
# because that is the fact the user needs.
MSG_NOT_VERIFIED = (
    "The update could not be verified. The download may be damaged. "
    "Nothing was changed."
)
MSG_NO_SIDECAR = (
    "The update could not be verified because its checksum file is "
    "missing. Nothing was changed."
)
MSG_BAD_SIDECAR = (
    "The update could not be verified because its checksum file "
    "could not be read. Nothing was changed."
)
MSG_NO_ZIP = "The update file could not be found. Nothing was changed."
MSG_DOWNLOAD_FAILED = (
    "The update could not be downloaded. Check the network and try "
    "again. Nothing was changed."
)
MSG_SAVE_FAILED = (
    "The update could not be saved. Check the disk space and try "
    "again. Nothing was changed."
)
MSG_APPLY_FAILED = (
    "The update could not be installed. The old version is still "
    "running and nothing was changed."
)


class UpdateError(Exception):
    """A plain-words refusal: the update stops, nothing changes."""


class UpdateCancelled(Exception):
    """The user stopped the download from the progress dialog."""


# ---------------------------------------------------------------- names + paths


def zip_asset_name(tag: str) -> str:
    """The release zip for a tag, e.g. dataset-scout-v0.1.5.zip."""
    return f"dataset-scout-{tag}.zip"


def sidecar_asset_name(tag: str) -> str:
    """The checksum sidecar: the zip name plus .sha256."""
    return zip_asset_name(tag) + ".sha256"


def release_url(tag: str, asset: str, base_url: str | None = None) -> str:
    """The download URL for one asset of a release."""
    base = base_url or RELEASE_DOWNLOAD_BASE
    return f"{base.rstrip('/')}/{tag}/{asset}"


def install_dir() -> Path:
    """The installed program folder. SCOUT_INSTALL_DIR overrides it
    (used by tests, so they never touch the real install)."""
    override = os.environ.get("SCOUT_INSTALL_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent


def update_dir() -> Path:
    """Staging area for updates, under the user's data dir. It
    survives an app restart, so a deferred update can complete on
    the next launch."""
    return core.home_dir() / "update"


def pending_dir() -> Path:
    return update_dir() / "pending"


def _marker_path() -> Path:
    return pending_dir() / "pending.json"


# ---------------------------------------------------------------- sidecar + hash


def parse_sidecar(text: str) -> str:
    """The expected hash from a sidecar's text.

    Accepts a bare hex hash and the ``sha256sum`` style
    "<hash>  <filename>" line: the first whitespace-separated
    token is the hash. Anything else is a refusal.
    """
    tokens = text.split()
    if not tokens or not _HASH_RE.fullmatch(tokens[0]):
        raise UpdateError(MSG_BAD_SIDECAR)
    return tokens[0].lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- download (U3)


def _download_text(url: str, kind: str) -> str:
    """A small text file (the sidecar) as a string."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(_SIDECAR_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and kind == "sidecar":
            raise UpdateError(MSG_NO_SIDECAR) from exc
        raise UpdateError(MSG_DOWNLOAD_FAILED) from exc
    except (urllib.error.URLError, TimeoutError, OSError,
            http.client.HTTPException) as exc:
        raise UpdateError(MSG_DOWNLOAD_FAILED) from exc
    if len(body) > _SIDECAR_MAX_BYTES:
        raise UpdateError(MSG_BAD_SIDECAR)
    return body.decode("utf-8", errors="replace")


def _download_file(url: str, dest: Path, kind: str,
                   progress=None) -> None:
    """Download a file in chunks. progress(downloaded, total) is
    called after each chunk and may return False to cancel."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and kind == "zip":
            raise UpdateError(MSG_NO_ZIP) from exc
        raise UpdateError(MSG_DOWNLOAD_FAILED) from exc
    except (urllib.error.URLError, TimeoutError, OSError,
            http.client.HTTPException) as exc:
        raise UpdateError(MSG_DOWNLOAD_FAILED) from exc
    total = response.headers.get("Content-Length")
    total = int(total) if total and total.isdigit() else None
    downloaded = 0
    try:
        with response, dest.open("wb") as handle:
            while True:
                try:
                    chunk = response.read(65536)
                except (TimeoutError, OSError,
                        http.client.HTTPException) as exc:
                    raise UpdateError(MSG_DOWNLOAD_FAILED) from exc
                if not chunk:
                    break
                try:
                    handle.write(chunk)
                except OSError as exc:
                    raise UpdateError(MSG_SAVE_FAILED) from exc
                downloaded += len(chunk)
                if progress is not None and not progress(downloaded, total):
                    raise UpdateCancelled()
    except (UpdateError, UpdateCancelled):
        raise
    except OSError as exc:
        raise UpdateError(MSG_SAVE_FAILED) from exc


def _write_marker(marker: dict) -> None:
    """Write the pending marker atomically (tmp file + replace)."""
    pending = pending_dir()
    pending.mkdir(parents=True, exist_ok=True)
    tmp = pending / f".pending.json.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(marker), encoding="utf-8")
    os.replace(tmp, _marker_path())


def _clear_pending() -> None:
    """Drop a pending update: the zip and the marker go together."""
    shutil.rmtree(pending_dir(), ignore_errors=True)


def stage_pending_update(tag: str, zip_path: Path, sha256: str) -> Path:
    """Record a verified zip as the pending update. Returns the
    staged copy's path. Any older pending update is replaced."""
    _clear_pending()
    pending = pending_dir()
    pending.mkdir(parents=True, exist_ok=True)
    dest = pending / zip_path.name
    shutil.copyfile(zip_path, dest)
    _write_marker({"tag": tag, "zip": zip_path.name,
                   "sha256": sha256, "attempts": 0})
    return dest


def download_release(tag: str, base_url: str | None = None,
                     progress=None) -> Path:
    """Download and verify one release (U3). The sidecar comes
    first, so a release with nothing to verify against is refused
    before the zip is even downloaded. On success the verified zip
    is staged as the pending update and its path is returned."""
    zip_name = zip_asset_name(tag)
    sidecar_name = sidecar_asset_name(tag)
    sidecar_text = _download_text(
        release_url(tag, sidecar_name, base_url), kind="sidecar")
    expected = parse_sidecar(sidecar_text)
    # Download beside the pending folder, not in it: a failed
    # re-download must not destroy a good staged update.
    update = update_dir()
    update.mkdir(parents=True, exist_ok=True)
    part = update / (zip_name + f".part-{os.getpid()}")
    try:
        _download_file(release_url(tag, zip_name, base_url), part,
                       kind="zip", progress=progress)
        actual = _sha256_file(part)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    if actual != expected:
        part.unlink(missing_ok=True)
        raise UpdateError(MSG_NOT_VERIFIED)
    _clear_pending()
    pending = pending_dir()
    pending.mkdir(parents=True, exist_ok=True)
    dest = pending / zip_name
    os.replace(part, dest)
    _write_marker({"tag": tag, "zip": zip_name,
                   "sha256": expected, "attempts": 0})
    core.log(f"update: downloaded and verified {tag}")
    return dest


# ---------------------------------------------------------------- pending state


def pending_update_version() -> str | None:
    """The tag of a staged update, or None when there is none.
    Fast on purpose: the launch hook calls this on every start."""
    marker_path = _marker_path()
    if not marker_path.is_file():
        return None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        tag = marker["tag"]
        zip_name = marker["zip"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not tag or not zip_name:
        return None
    if not (pending_dir() / zip_name).is_file():
        return None
    return tag


# ---------------------------------------------------------------- apply (U5)


def _extract_zip_safe(zip_path: Path, dest: Path) -> None:
    """Extract a zip, refusing any entry that would escape the
    destination (absolute paths, ".."), and restoring the
    executable bits the release zip stores."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdateError(
            "The update file could not be opened. Nothing was changed."
        ) from exc
    with archive:
        for info in archive.infolist():
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(root):
                raise UpdateError(
                    "The update file was not safe to install. "
                    "Nothing was changed."
                )
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out)
            except (zipfile.BadZipFile, OSError) as exc:
                raise UpdateError(
                    "The update file could not be opened. "
                    "Nothing was changed."
                ) from exc
            mode = (info.external_attr >> 16) & 0o7777
            if mode:
                try:
                    os.chmod(target, mode)
                except OSError:
                    pass


def _verify_tree(tree: Path) -> Path:
    """The extracted folder that is actually the program tree.

    Normally the zip's root is the tree. A zip that wraps the
    tree in one top-level folder is accepted too (the folder
    becomes the tree). Anything else is a refusal: installing
    it would leave no runnable program behind.
    """
    if (tree / "scout" / "__init__.py").is_file() and \
            (tree / "run.sh").is_file():
        return tree
    children = [child for child in tree.iterdir() if child.is_dir()]
    if len(children) == 1:
        inner = children[0]
        if (inner / "scout" / "__init__.py").is_file() and \
                (inner / "run.sh").is_file():
            return inner
    raise UpdateError(
        "The update file did not hold a Dataset Scout program. "
        "Nothing was changed."
    )


def _swap_tree(staged: Path, target: Path) -> None:
    """Replace the installed tree with the staged one, crash-safe.

    Old aside, new in, old deleted only once the new tree is
    verified in place. The old tree's .venv is carried into the
    new tree (the zip never ships one). Any failure rolls the old
    tree back into place, so the old version stays runnable.
    """
    parent = target.parent
    old_aside = parent / f"{target.name}.old-{os.getpid()}"
    if old_aside.exists():
        shutil.rmtree(old_aside, ignore_errors=True)
    had_venv = (target / ".venv").is_dir()
    venv_moved = False
    swapped = False
    try:
        os.rename(target, old_aside)
        try:
            os.rename(staged, target)
        except OSError:
            # The new tree never landed: put the old one back.
            try:
                os.rename(old_aside, target)
            except OSError:
                core.log_error(
                    "update: could not roll back after a failed swap; "
                    f"old tree is at {old_aside}")
                raise
            raise
        swapped = True
        old_venv = old_aside / ".venv"
        if had_venv and not (target / ".venv").exists():
            shutil.move(str(old_venv), str(target / ".venv"))
            venv_moved = True
        if not (target / "scout" / "__init__.py").is_file():
            raise OSError("new tree missing after swap")
        if had_venv and not (target / ".venv").is_dir():
            raise OSError("venv missing after swap")
    except BaseException:
        if swapped:
            # Roll back: new tree out, old tree back, venv home.
            if venv_moved:
                try:
                    if (target / ".venv").is_dir():
                        shutil.move(str(target / ".venv"),
                                    str(old_aside / ".venv"))
                except OSError:
                    pass
            try:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                os.rename(old_aside, target)
            except OSError:
                core.log_error(
                    "update: rollback failed; old tree is at "
                    f"{old_aside}")
        raise
    shutil.rmtree(old_aside, ignore_errors=True)


def _clean_stale_siblings(target: Path) -> None:
    """Best-effort tidy of staging/backup folders left beside the
    install by a crashed earlier run. Never raises."""
    try:
        for pattern in (f".{target.name}.new-*", f"{target.name}.old-*"):
            for leftover in target.parent.glob(pattern):
                shutil.rmtree(leftover, ignore_errors=True)
    except OSError:
        pass


def apply_pending_update() -> bool:
    """Apply a staged update, if one is pending.

    True when an update was applied. False when there was nothing
    pending, or the pending update could not be applied (a bad
    pending update is dropped; a filesystem failure keeps the
    pending update for the next launch, up to three tries). The
    old version stays runnable on every failure path.
    """
    marker_path = _marker_path()
    if not marker_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        tag = marker["tag"]
        zip_name = marker["zip"]
        expected = marker["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        _clear_pending()
        return False
    # Already on this version or newer: the pending update is stale.
    if core.parse_version(tag) <= core.parse_version(core.VERSION):
        _clear_pending()
        return False
    zip_path = pending_dir() / zip_name
    try:
        if not zip_path.is_file() or _sha256_file(zip_path) != expected:
            _clear_pending()
            return False
    except OSError:
        _clear_pending()
        return False
    target = install_dir()
    _clean_stale_siblings(target)
    staging = target.parent / f".{target.name}.new-{os.getpid()}"
    try:
        _extract_zip_safe(zip_path, staging)
        root = _verify_tree(staging)
        _swap_tree(root, target)
        # A wrapped zip leaves the (now empty) staging folder behind.
        shutil.rmtree(staging, ignore_errors=True)
    except UpdateError as exc:
        # Bad zip contents: retrying would fail the same way.
        core.log_error(f"update: refusing pending {tag}", exc)
        _clear_pending()
        shutil.rmtree(staging, ignore_errors=True)
        return False
    except OSError as exc:
        # Filesystem trouble (disk full, permissions): keep the
        # pending update and try again next launch, up to a point.
        core.log_error(f"update: apply of {tag} failed", exc)
        shutil.rmtree(staging, ignore_errors=True)
        attempts = marker.get("attempts", 0)
        try:
            attempts = int(attempts) + 1
        except (TypeError, ValueError):
            attempts = 1
        if attempts >= _MAX_APPLY_ATTEMPTS:
            _clear_pending()
        else:
            marker["attempts"] = attempts
            try:
                _write_marker(marker)
            except OSError:
                pass
        return False
    _clear_pending()
    core.log(f"update: applied {tag}")
    return True


# ---------------------------------------------------------------- restart (U6)


def restart_app() -> None:
    """Re-exec the app so the new version actually runs.

    The working directory is moved to the installed folder first:
    the running process started in the old tree (renamed aside by
    the swap), and ``python -m scout`` finds its package from the
    working directory, so without the move the re-exec would
    import the old code. The launcher's arguments are preserved.
    """
    target = install_dir()
    os.chdir(target)
    argv = [sys.executable, "-m", "scout"] + sys.argv[1:]
    core.log(f"update: restarting into {target}")
    os.execv(sys.executable, argv)


# ---------------------------------------------------------------- Qt prompts (lazy)


def _show_info(parent, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.information(parent, "Dataset Scout", text)


def _show_error(parent, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.warning(parent, "Dataset Scout", text)


def ask_restart(parent, tag: str) -> bool:
    """The restart prompt (U6): yes restarts now, no waits until
    the next launch. The buttons say what they do."""
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setWindowTitle("Restart to finish the update")
    box.setText(
        f"Dataset Scout {tag} is downloaded and ready.\n\n"
        "Restart now to use it?")
    restart_button = box.addButton(
        "Restart now", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is restart_button


def start_update(parent, tag: str, base_url: str | None = None) -> bool:
    """The whole update flow, from the update-found popup (U2).

    Download with progress, verify, stage, then ask about the
    restart. Yes applies the staged update and restarts into the
    new version; no leaves it staged for the next launch.
    Returns True when the update is staged or applied, False when
    it was refused, cancelled, or could not be installed.
    ``base_url`` overrides the GitHub download base (tests only).
    """
    if core.parse_version(tag) <= core.parse_version(core.VERSION):
        _show_info(parent, "You already have the newest version.")
        return False
    dialog = None
    progress = None
    try:
        from PySide6.QtWidgets import QApplication, QProgressDialog
        from PySide6.QtCore import Qt

        app = QApplication.instance()
        if app is not None:
            dialog = QProgressDialog(
                "Downloading the update...", "Cancel", 0, 0, parent)
            dialog.setWindowTitle("Updating Dataset Scout")
            dialog.setWindowModality(Qt.WindowModal)
            dialog.setMinimumDuration(0)
            dialog.show()

            def progress(downloaded: int, total: int | None) -> bool:
                if total:
                    dialog.setMaximum(total)
                    dialog.setValue(downloaded)
                    label = (
                        "Downloading the update... "
                        f"{core.format_size(downloaded)} of "
                        f"{core.format_size(total)}"
                    )
                else:
                    label = (
                        "Downloading the update... "
                        f"{core.format_size(downloaded)}"
                    )
                dialog.setLabelText(label)
                QApplication.processEvents()
                return not dialog.wasCanceled()
    except ImportError:
        dialog = None
        progress = None
    try:
        download_release(tag, base_url=base_url, progress=progress)
    except UpdateCancelled:
        return False
    except UpdateError as exc:
        _show_error(parent, str(exc))
        return False
    finally:
        if dialog is not None:
            dialog.close()
    if ask_restart(parent, tag):
        if apply_pending_update():
            try:
                restart_app()
            except OSError:
                _show_info(
                    parent,
                    "The update is installed. "
                    "Restart Dataset Scout to use it.")
            return True
        _show_error(parent, MSG_APPLY_FAILED)
        return False
    _show_info(
        parent,
        "The update is downloaded. It will finish the next time "
        "Dataset Scout starts.")
    return True

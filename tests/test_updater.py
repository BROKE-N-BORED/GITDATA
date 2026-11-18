"""Tests for the updater (v0.1.5 U3-U7): verified pull, sidecar,
crash-safe apply, restart prompt, launch hook.

A local HTTP server stands in for GitHub: the tests never touch
the real network. A fake install tree and a fake ~/.dataset-scout/
stand in for the real ones (SCOUT_INSTALL_DIR / SCOUT_HOME), so
no test ever touches the real install or the real user data.
"""

import functools
import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QApplication

from scout import core, updater

# One QApplication for the module, as in test_gui.py: a second
# QApplication raises (libshiboken).
APP = QApplication.instance() or QApplication([])

HERE = Path(__file__).resolve().parent.parent
REAL_SCOUT = HERE / "scout"

def _next_patch(version: str) -> str:
    """One patch newer than the app version. The tests' fake
    release must always be newer than the app under test, or the
    updater correctly treats it as stale and refuses it (the
    hardcoded v0.1.5 tag went stale the day VERSION became
    0.1.5). Deriving it here keeps the tests green across
    version bumps."""
    parts = [int(part) for part in version.split(".")]
    parts[-1] += 1
    return ".".join(str(part) for part in parts)


NEW_VERSION = _next_patch(core.VERSION)
TAG = "v" + NEW_VERSION
OLD_VERSION = "0.1.4"

STUB_GUI = '''"""Stub window for the updater launch-hook test."""

from . import core


def main():
    print("GUI RAN " + core.VERSION)
    return 0
'''


# ---------------------------------------------------------------- helpers


def _zip_bytes(version, wrap=False, extra=None, omit=()):
    """A release zip's bytes, built the way build_zip.py builds
    one: the tree at the zip root, exec bits on the scripts.
    extra is {name: bytes} for hostile or extra entries; omit
    drops tree files by name."""
    files = {
        "scout/__init__.py": (
            f"from .core import VERSION\nVERSION = \"{version}\"\n"
        ).encode("utf-8"),
        "scout/gui.py": b"# stub window\n",
        "scout/core.py": (
            f"VERSION = \"{version}\"\n"
        ).encode("utf-8"),
        "run.sh": b"#!/usr/bin/env bash\n",
        "install.sh": b"#!/usr/bin/env bash\n",
        "README.md": b"# Dataset Scout\n",
    }
    for name in omit:
        files.pop(name, None)
    if extra:
        files.update(extra)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(files.items()):
            arcname = f"dataset-scout-{version}/{name}" if wrap else name
            info = zipfile.ZipInfo(arcname)
            mode = 0o100755 if name.endswith(".sh") else 0o100644
            info.external_attr = mode << 16
            zf.writestr(info, data)
    return buffer.getvalue()


def _make_stub_tree(root: Path, version: str) -> None:
    """A fake installed tree: the program files plus the .venv
    install.sh creates (the zip never ships one)."""
    (root / "scout").mkdir(parents=True)
    (root / "scout" / "__init__.py").write_text(
        f"from .core import VERSION\nVERSION = \"{version}\"\n",
        encoding="utf-8")
    (root / "scout" / "gui.py").write_text("# stub window\n",
                                           encoding="utf-8")
    (root / "scout" / "core.py").write_text(
        f"VERSION = \"{version}\"\n", encoding="utf-8")
    for name in ("run.sh", "install.sh"):
        path = root / name
        path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        path.chmod(0o755)
    (root / "README.md").write_text("# Dataset Scout\n", encoding="utf-8")
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("# fake venv python\n",
                                    encoding="utf-8")
    (root / ".venv" / "kept.txt").write_text(
        "the venv survives the swap\n", encoding="utf-8")


def _make_real_tree(root: Path, version: str) -> None:
    """A fake installed tree built from the REAL scout package
    (minus gui.py, replaced by a stub that prints its version),
    for the launch-hook subprocess test: the subprocess runs
    `python -m scout` from this tree for real."""
    (root / "scout").mkdir(parents=True)
    for name in ("__init__.py", "__main__.py", "core.py", "updater.py"):
        shutil.copyfile(REAL_SCOUT / name, root / "scout" / name)
    if version != core.VERSION:
        core_path = root / "scout" / "core.py"
        text = core_path.read_text(encoding="utf-8")
        old = f'VERSION = "{core.VERSION}"'
        assert old in text
        core_path.write_text(text.replace(old, f'VERSION = "{version}"', 1),
                             encoding="utf-8")
    (root / "scout" / "gui.py").write_text(STUB_GUI, encoding="utf-8")
    for name in ("run.sh", "install.sh"):
        shutil.copyfile(HERE / name, root / name)
        (root / name).chmod(0o755)
    venv_bin = root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("# fake venv python\n",
                                    encoding="utf-8")
    (root / ".venv" / "kept.txt").write_text(
        "the venv survives the swap\n", encoding="utf-8")


def _tree_zip_bytes(root: Path) -> bytes:
    """Zip a tree on disk the way build_zip.py does (no .venv,
    exec bits on the scripts)."""
    buffer = io.BytesIO()
    scripts = {"install.sh", "run.sh"}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_dir() or ".venv" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(rel)
            mode = 0o100755 if path.name in scripts else 0o100644
            info.external_attr = mode << 16
            zf.writestr(info, path.read_bytes())
    return buffer.getvalue()


def _quiet_handler(directory):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, *args):
            pass

    return Handler


class _FakeReleaseServer:
    """A local stand-in for github.com's release downloads."""

    def __init__(self, root: Path):
        self.root = root
        self.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _quiet_handler(str(root)))
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}/releases/download"

    def publish(self, tag, zip_bytes=None, sidecar=None):
        """Put a release's assets on the server. Either may be
        omitted to simulate a missing asset (404)."""
        folder = self.root / "releases" / "download" / tag
        folder.mkdir(parents=True, exist_ok=True)
        if zip_bytes is not None:
            (folder / updater.zip_asset_name(tag)).write_bytes(zip_bytes)
        if sidecar is not None:
            (folder / updater.sidecar_asset_name(tag)).write_text(
                sidecar, encoding="utf-8")


class UpdaterTestCase(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        (self.home / "logs").mkdir()
        self._seed_user_data()
        self.install_parent = base / "installs"
        self.install_parent.mkdir()
        self.install = self.install_parent / "dataset-scout"
        _make_stub_tree(self.install, OLD_VERSION)
        self._old_env = {key: os.environ.get(key) for key in
                         ("SCOUT_HOME", "SCOUT_INSTALL_DIR")}
        os.environ["SCOUT_HOME"] = str(self.home)
        os.environ["SCOUT_INSTALL_DIR"] = str(self.install)
        self.server = _FakeReleaseServer(base / "server")
        self.server.start()

    def tearDown(self):
        self.server.stop()
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        os.chdir(self._old_cwd)
        self.tmp.cleanup()

    def _seed_user_data(self):
        (self.home / "config.json").write_text(
            '{"theme": "dark", "token": "seeded"}', encoding="utf-8")
        (self.home / "mylist.json").write_text(
            '[{"url": "https://example.com/data", "title": "mine"}]',
            encoding="utf-8")
        (self.home / "sources.json").write_text(
            '{"sites": ["https://example.com/search?q={q}"]}',
            encoding="utf-8")
        (self.home / "logs" / "scout-20261118.log").write_text(
            "seeded log line\n", encoding="utf-8")

    def _user_data_hashes(self):
        """SHA-256 of each user-data file (not the logs: the
        updater appends its own lines there, which is Scout's
        normal logging, not user data being changed)."""
        hashes = {}
        for name in ("config.json", "mylist.json", "sources.json"):
            hashes[name] = hashlib.sha256(
                (self.home / name).read_bytes()).hexdigest()
        return hashes

    def _installed_version(self):
        text = (self.install / "scout" / "__init__.py").read_text(
            encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("VERSION"):
                return line.split('"')[1]
        return None

    def _stage(self, tag=TAG, zip_bytes=None, version=NEW_VERSION,
               wrap=False, extra=None, omit=()):
        """Stage a pending update directly (no network): build a
        zip, hash it honestly, and record it as pending."""
        if zip_bytes is None:
            zip_bytes = _zip_bytes(version, wrap=wrap, extra=extra,
                                   omit=omit)
        zip_path = Path(self.tmp.name) / updater.zip_asset_name(tag)
        zip_path.write_bytes(zip_bytes)
        digest = hashlib.sha256(zip_bytes).hexdigest()
        return updater.stage_pending_update(tag, zip_path, digest)


# ---------------------------------------------------------------- names


class NamingTests(UpdaterTestCase):

    def test_zip_and_sidecar_names(self):
        self.assertEqual(updater.zip_asset_name("v0.1.5"),
                         "dataset-scout-v0.1.5.zip")
        self.assertEqual(updater.sidecar_asset_name("v0.1.5"),
                         "dataset-scout-v0.1.5.zip.sha256")

    def test_release_url_pattern(self):
        url = updater.release_url("v0.1.5", "dataset-scout-v0.1.5.zip")
        self.assertEqual(
            url,
            "https://github.com/BROKE-N-BORED/GITDATA/releases/download/"
            "v0.1.5/dataset-scout-v0.1.5.zip")
        sidecar = updater.release_url(
            "v0.1.5", "dataset-scout-v0.1.5.zip.sha256")
        self.assertTrue(sidecar.endswith(
            "v0.1.5/dataset-scout-v0.1.5.zip.sha256"))

    def test_parse_sidecar_bare_and_sha256sum_styles(self):
        digest = "a" * 64
        self.assertEqual(updater.parse_sidecar(digest + "\n"), digest)
        self.assertEqual(
            updater.parse_sidecar(
                digest + "  dataset-scout-v0.1.5.zip\n"), digest)
        self.assertEqual(updater.parse_sidecar(digest.upper()), digest)

    def test_parse_sidecar_refuses_garbage(self):
        for bad in ("", "hello\n", "abc\n", "z" * 64 + "\n",
                    "a" * 63 + "\n"):
            with self.assertRaises(updater.UpdateError, msg=repr(bad)):
                updater.parse_sidecar(bad)


# ---------------------------------------------------------------- download (U3)


class DownloadTests(UpdaterTestCase):

    def test_verified_pull_stages_pending(self):
        zip_bytes = _zip_bytes(NEW_VERSION)
        digest = hashlib.sha256(zip_bytes).hexdigest()
        self.server.publish(TAG, zip_bytes, digest + "\n")
        staged = updater.download_release(TAG, base_url=self.server.base_url)
        self.assertTrue(staged.is_file())
        self.assertEqual(staged.read_bytes(), zip_bytes)
        self.assertEqual(updater.pending_update_version(), TAG)
        # The install is untouched until the update is applied.
        self.assertEqual(self._installed_version(), OLD_VERSION)

    def test_verified_pull_accepts_sha256sum_style_sidecar(self):
        zip_bytes = _zip_bytes(NEW_VERSION)
        digest = hashlib.sha256(zip_bytes).hexdigest()
        sidecar = f"{digest}  {updater.zip_asset_name(TAG)}\n"
        self.server.publish(TAG, zip_bytes, sidecar)
        updater.download_release(TAG, base_url=self.server.base_url)
        self.assertEqual(updater.pending_update_version(), TAG)

    def test_refusal_on_bad_checksum(self):
        zip_bytes = _zip_bytes(NEW_VERSION)
        # The sidecar hashes DIFFERENT bytes: the zip was tampered
        # with (or damaged) after the sidecar was made.
        digest = hashlib.sha256(b"the original bytes").hexdigest()
        self.server.publish(TAG, zip_bytes, digest + "\n")
        with self.assertRaises(updater.UpdateError) as caught:
            updater.download_release(TAG, base_url=self.server.base_url)
        self.assertIn("could not be verified", str(caught.exception))
        self.assertIn("Nothing was changed", str(caught.exception))
        self.assertIsNone(updater.pending_update_version())
        self.assertEqual(list(updater.pending_dir().glob("*")), [])
        self.assertEqual(self._installed_version(), OLD_VERSION)

    def test_refusal_on_missing_sidecar(self):
        zip_bytes = _zip_bytes(NEW_VERSION)
        self.server.publish(TAG, zip_bytes, sidecar=None)
        with self.assertRaises(updater.UpdateError) as caught:
            updater.download_release(TAG, base_url=self.server.base_url)
        self.assertIn("checksum file is missing", str(caught.exception))
        self.assertIn("Nothing was changed", str(caught.exception))
        # The zip was never downloaded: no point without a sidecar.
        self.assertFalse(
            (updater.pending_dir() / updater.zip_asset_name(TAG)).exists())
        self.assertIsNone(updater.pending_update_version())

    def test_refusal_on_missing_zip_asset(self):
        digest = hashlib.sha256(b"whatever").hexdigest()
        self.server.publish(TAG, zip_bytes=None, sidecar=digest + "\n")
        with self.assertRaises(updater.UpdateError) as caught:
            updater.download_release(TAG, base_url=self.server.base_url)
        self.assertIn("could not be found", str(caught.exception))
        self.assertIsNone(updater.pending_update_version())

    def test_refusal_on_garbage_sidecar(self):
        zip_bytes = _zip_bytes(NEW_VERSION)
        self.server.publish(TAG, zip_bytes, "not a hash\n")
        with self.assertRaises(updater.UpdateError) as caught:
            updater.download_release(TAG, base_url=self.server.base_url)
        self.assertIn("could not be verified", str(caught.exception))
        self.assertIsNone(updater.pending_update_version())

    def test_cancelled_download_leaves_nothing(self):
        zip_bytes = _zip_bytes(NEW_VERSION)
        digest = hashlib.sha256(zip_bytes).hexdigest()
        self.server.publish(TAG, zip_bytes, digest + "\n")
        with self.assertRaises(updater.UpdateCancelled):
            updater.download_release(
                TAG, base_url=self.server.base_url,
                progress=lambda downloaded, total: False)
        self.assertIsNone(updater.pending_update_version())
        leftovers = [path for path in updater.update_dir().rglob("*")
                     if path.is_file()]
        self.assertEqual(leftovers, [])

    def test_failed_redownload_keeps_good_pending(self):
        zip_bytes = _zip_bytes(NEW_VERSION)
        digest = hashlib.sha256(zip_bytes).hexdigest()
        self.server.publish(TAG, zip_bytes, digest + "\n")
        updater.download_release(TAG, base_url=self.server.base_url)
        # The release changes under us: the new zip is tampered.
        self.server.publish(TAG, b"tampered bytes", digest + "\n")
        with self.assertRaises(updater.UpdateError):
            updater.download_release(TAG, base_url=self.server.base_url)
        # The good staged update from the first pull survives.
        self.assertEqual(updater.pending_update_version(), TAG)


# ---------------------------------------------------------------- apply (U5)


class ApplyTests(UpdaterTestCase):

    def test_nothing_pending_is_silent_false(self):
        self.assertFalse(updater.apply_pending_update())
        self.assertIsNone(updater.pending_update_version())

    def test_successful_apply(self):
        before = self._user_data_hashes()
        self._stage()
        self.assertTrue(updater.apply_pending_update())
        self.assertEqual(self._installed_version(), NEW_VERSION)
        # Exec bits survive the swap: the launcher still works.
        self.assertTrue(os.access(self.install / "run.sh", os.X_OK))
        self.assertTrue(os.access(self.install / "install.sh", os.X_OK))
        # The venv was carried into the new tree.
        self.assertTrue((self.install / ".venv" / "kept.txt").is_file())
        self.assertTrue(
            (self.install / ".venv" / "bin" / "python").is_file())
        # Pending is cleared, no staging or backup left behind.
        self.assertIsNone(updater.pending_update_version())
        leftovers = [path.name for path in self.install_parent.iterdir()
                     if path.name != self.install.name]
        self.assertEqual(leftovers, [])
        # User data is byte-identical.
        self.assertEqual(self._user_data_hashes(), before)
        log = (self.home / "logs" / "scout-20261118.log").read_text(
            encoding="utf-8")
        self.assertTrue(log.startswith("seeded log line\n"))

    def test_apply_accepts_wrapped_zip(self):
        self._stage(wrap=True)
        self.assertTrue(updater.apply_pending_update())
        self.assertEqual(self._installed_version(), NEW_VERSION)

    def test_corrupt_zip_leaves_old_version_runnable(self):
        self._stage(zip_bytes=b"this is not a zip at all")
        self.assertFalse(updater.apply_pending_update())
        self.assertEqual(self._installed_version(), OLD_VERSION)
        self.assertTrue((self.install / "run.sh").is_file())
        self.assertTrue((self.install / ".venv" / "kept.txt").is_file())
        # A bad zip is dropped, never retried.
        self.assertIsNone(updater.pending_update_version())

    def test_zip_slip_is_refused(self):
        self._stage(extra={"../evil.txt": b"escape\n",
                           "/tmp/evil2.txt": b"escape\n"})
        self.assertFalse(updater.apply_pending_update())
        self.assertFalse((self.install_parent / "evil.txt").exists())
        self.assertFalse(Path("/tmp/evil2.txt").exists())
        self.assertEqual(self._installed_version(), OLD_VERSION)
        self.assertIsNone(updater.pending_update_version())

    def test_zip_without_a_program_is_refused(self):
        self._stage(omit=("scout/__init__.py", "scout/core.py",
                          "scout/gui.py", "run.sh", "install.sh"))
        self.assertFalse(updater.apply_pending_update())
        self.assertEqual(self._installed_version(), OLD_VERSION)
        self.assertIsNone(updater.pending_update_version())

    def test_stale_pending_is_dropped(self):
        self._stage(tag="v0.1.4", version=OLD_VERSION)
        self.assertFalse(updater.apply_pending_update())
        self.assertIsNone(updater.pending_update_version())
        self._stage(tag="v0.1.3", version="0.1.3")
        self.assertFalse(updater.apply_pending_update())
        self.assertIsNone(updater.pending_update_version())

    def test_tampered_pending_zip_is_dropped(self):
        staged = self._stage()
        staged.write_bytes(b"tampered after staging")
        self.assertFalse(updater.apply_pending_update())
        self.assertEqual(self._installed_version(), OLD_VERSION)
        self.assertIsNone(updater.pending_update_version())

    def test_swap_failure_rolls_back_to_old_version(self):
        before = self._user_data_hashes()
        self._stage()
        real_rename = os.rename
        calls = {"n": 0}

        def failing_rename(src, dst):
            # The first rename (old tree aside) works; the second
            # (new tree in) fails mid-swap.
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated disk failure")
            return real_rename(src, dst)

        with mock.patch.object(os, "rename", side_effect=failing_rename):
            self.assertFalse(updater.apply_pending_update())
        # The old version is intact and runnable.
        self.assertEqual(self._installed_version(), OLD_VERSION)
        self.assertTrue((self.install / "run.sh").is_file())
        self.assertTrue((self.install / ".venv" / "kept.txt").is_file())
        # No backup folder left behind: the rollback put it back.
        leftovers = [path.name for path in self.install_parent.iterdir()
                     if path.name != self.install.name]
        self.assertEqual(leftovers, [])
        # The pending update is kept for the next launch.
        self.assertEqual(updater.pending_update_version(), TAG)
        marker = json.loads(
            (updater.pending_dir() / "pending.json").read_text(
                encoding="utf-8"))
        self.assertEqual(marker["attempts"], 1)
        self.assertEqual(self._user_data_hashes(), before)

    def test_venv_move_failure_rolls_back(self):
        self._stage()
        real_move = shutil.move

        def failing_move(src, dst):
            if str(src).endswith(".venv"):
                raise OSError("simulated move failure")
            return real_move(src, dst)

        with mock.patch.object(shutil, "move", side_effect=failing_move):
            self.assertFalse(updater.apply_pending_update())
        self.assertEqual(self._installed_version(), OLD_VERSION)
        self.assertTrue((self.install / ".venv" / "kept.txt").is_file())
        leftovers = [path.name for path in self.install_parent.iterdir()
                     if path.name != self.install.name]
        self.assertEqual(leftovers, [])
        self.assertEqual(updater.pending_update_version(), TAG)

    def test_apply_gives_up_after_three_failures(self):
        self._stage()
        with mock.patch.object(updater, "_swap_tree",
                               side_effect=OSError("always fails")):
            self.assertFalse(updater.apply_pending_update())
            self.assertEqual(updater.pending_update_version(), TAG)
            self.assertFalse(updater.apply_pending_update())
            self.assertEqual(updater.pending_update_version(), TAG)
            self.assertFalse(updater.apply_pending_update())
        # Three strikes: the pending update is dropped, and the
        # old version is still intact.
        self.assertIsNone(updater.pending_update_version())
        self.assertEqual(self._installed_version(), OLD_VERSION)


# ---------------------------------------------------------------- restart (U6)


class RestartTests(UpdaterTestCase):

    def _publish_good_release(self):
        zip_bytes = _zip_bytes(NEW_VERSION)
        digest = hashlib.sha256(zip_bytes).hexdigest()
        self.server.publish(TAG, zip_bytes, digest + "\n")

    def test_restart_app_reexecs_into_the_install(self):
        with mock.patch.object(os, "execv") as execv:
            updater.restart_app()
        execv.assert_called_once()
        executable, argv = execv.call_args.args
        self.assertEqual(executable, sys.executable)
        self.assertEqual(argv, [sys.executable, "-m", "scout"]
                         + sys.argv[1:])
        self.assertEqual(Path(os.getcwd()), self.install)

    def test_start_update_restart_yes_applies_and_reexecs(self):
        self._publish_good_release()
        with mock.patch.object(updater, "ask_restart",
                               return_value=True), \
             mock.patch.object(updater, "_show_info") as info, \
             mock.patch.object(updater, "_show_error") as error, \
             mock.patch.object(os, "execv") as execv:
            result = updater.start_update(
                None, TAG, base_url=self.server.base_url)
        self.assertTrue(result)
        error.assert_not_called()
        info.assert_not_called()
        self.assertEqual(self._installed_version(), NEW_VERSION)
        self.assertTrue((self.install / ".venv" / "kept.txt").is_file())
        execv.assert_called_once()
        executable, argv = execv.call_args.args
        self.assertEqual(executable, sys.executable)
        self.assertEqual(argv[:3], [sys.executable, "-m", "scout"])
        self.assertEqual(Path(os.getcwd()), self.install)
        self.assertIsNone(updater.pending_update_version())

    def test_start_update_restart_no_defers_to_next_launch(self):
        before = self._user_data_hashes()
        self._publish_good_release()
        with mock.patch.object(updater, "ask_restart",
                               return_value=False), \
             mock.patch.object(updater, "_show_info") as info, \
             mock.patch.object(updater, "_show_error") as error, \
             mock.patch.object(os, "execv") as execv:
            result = updater.start_update(
                None, TAG, base_url=self.server.base_url)
        self.assertTrue(result)
        error.assert_not_called()
        execv.assert_not_called()
        info.assert_called_once()
        self.assertIn("next time", info.call_args.args[1])
        # The old version is still running; the update is staged.
        self.assertEqual(self._installed_version(), OLD_VERSION)
        self.assertEqual(updater.pending_update_version(), TAG)
        # The next launch applies it.
        self.assertTrue(updater.apply_pending_update())
        self.assertEqual(self._installed_version(), NEW_VERSION)
        self.assertEqual(self._user_data_hashes(), before)

    def test_start_update_refusal_shows_plain_words(self):
        zip_bytes = _zip_bytes(NEW_VERSION)
        digest = hashlib.sha256(b"the original bytes").hexdigest()
        self.server.publish(TAG, zip_bytes, digest + "\n")
        with mock.patch.object(updater, "ask_restart") as ask, \
             mock.patch.object(updater, "_show_error") as error, \
             mock.patch.object(os, "execv") as execv:
            result = updater.start_update(
                None, TAG, base_url=self.server.base_url)
        self.assertFalse(result)
        ask.assert_not_called()
        execv.assert_not_called()
        error.assert_called_once()
        message = error.call_args.args[1]
        self.assertIn("could not be verified", message)
        self.assertIn("Nothing was changed", message)
        self.assertNotIn("\u2014", message)
        self.assertEqual(self._installed_version(), OLD_VERSION)

    def test_start_update_when_already_current(self):
        with mock.patch.object(updater, "_show_info") as info, \
             mock.patch.object(updater, "download_release") as download:
            result = updater.start_update(None, "v0.1.4")
        self.assertFalse(result)
        download.assert_not_called()
        info.assert_called_once()
        self.assertIn("newest version", info.call_args.args[1])


# ---------------------------------------------------------------- launch hook


class LaunchHookTests(UpdaterTestCase):
    """The __main__ hook, exercised for real: a subprocess runs
    `python -m scout` from a fake install tree built from the real
    scout package, with a stub window that prints its version."""

    def _run_scout(self, install):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env["SCOUT_HOME"] = str(self.home)
        env["SCOUT_INSTALL_DIR"] = str(install)
        return subprocess.run(
            [sys.executable, "-m", "scout"], cwd=install, env=env,
            capture_output=True, text=True, timeout=60)

    def test_pending_update_completes_on_next_launch(self):
        install = self.install_parent / "real-tree"
        _make_real_tree(install, OLD_VERSION)
        new_tree = Path(self.tmp.name) / "new-tree"
        _make_real_tree(new_tree, NEW_VERSION)
        zip_bytes = _tree_zip_bytes(new_tree)
        zip_path = Path(self.tmp.name) / updater.zip_asset_name(TAG)
        zip_path.write_bytes(zip_bytes)
        digest = hashlib.sha256(zip_bytes).hexdigest()
        updater.stage_pending_update(TAG, zip_path, digest)
        result = self._run_scout(install)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The new version actually runs: the apply re-execs.
        self.assertIn(f"GUI RAN {NEW_VERSION}", result.stdout)
        self.assertNotIn("GUI RAN 0.1.4", result.stdout)
        self.assertTrue((install / ".venv" / "kept.txt").is_file())
        self.assertIsNone(updater.pending_update_version())

    def test_no_pending_update_starts_silently(self):
        install = self.install_parent / "real-tree"
        _make_real_tree(install, OLD_VERSION)
        result = self._run_scout(install)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GUI RAN 0.1.4", result.stdout)


if __name__ == "__main__":
    unittest.main()

"""Packaging checks (v0.1.3 T23): the double-click installer
the README promises, and the pip-resume claim in install.sh."""

import configparser
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
LAUNCHER = HERE / "Install Dataset Scout.desktop"


def _launcher_config():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(LAUNCHER, encoding="utf-8")
    return parser["Desktop Entry"]


def _run_exec(exec_line, launcher_path, cwd):
    """Run a .desktop Exec line the way a launcher would: %k
    expands to the launcher's own path, as one argument."""
    command = exec_line.replace("%k", shlex.quote(str(launcher_path)))
    return subprocess.run(command, shell=True, cwd=cwd,
                          capture_output=True, text=True, timeout=30)


class LauncherTests(unittest.TestCase):

    def test_launcher_exists_and_is_executable(self):
        self.assertTrue(LAUNCHER.is_file())
        mode = LAUNCHER.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)

    def test_launcher_entry(self):
        entry = _launcher_config()
        self.assertEqual(entry["Type"], "Application")
        self.assertEqual(entry["Name"], "Install Dataset Scout")
        self.assertEqual(entry["Terminal"], "true")
        self.assertIn("%k", entry["Exec"])
        # GLib rejects the whole file when the Exec line has a
        # nested escaped quote.
        self.assertNotIn('\\"', entry["Exec"])

    def test_launcher_in_build_zip_scripts(self):
        text = (HERE / "build_zip.py").read_text(encoding="utf-8")
        self.assertIn('"Install Dataset Scout.desktop"', text)

    def test_launch_runs_install_sh_in_its_folder(self):
        # A folder with a space in its name, launched from a
        # different working directory: the stub must run.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "my folder"
            folder.mkdir()
            marker = folder / "ran.txt"
            (folder / "install.sh").write_text(
                "#!/bin/bash\necho ran > "
                + shlex.quote(str(marker)) + "\n", encoding="utf-8")
            launcher = folder / LAUNCHER.name
            launcher.write_text(LAUNCHER.read_text(encoding="utf-8"),
                                encoding="utf-8")
            entry = _launcher_config()
            result = _run_exec(entry["Exec"], launcher, cwd=tmp)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(marker.is_file())

    def test_launch_with_empty_k_falls_back_to_cwd(self):
        # Some launchers expand %k to empty: the code then falls
        # back to the working directory.
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "ran.txt"
            (Path(tmp) / "install.sh").write_text(
                "#!/bin/bash\necho ran > "
                + shlex.quote(str(marker)) + "\n", encoding="utf-8")
            entry = _launcher_config()
            result = _run_exec(entry["Exec"], "", cwd=tmp)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(marker.is_file())


class InstallScriptTests(unittest.TestCase):

    def test_no_false_pip_resume_claim(self):
        text = (HERE / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn("picks up where", text)
        self.assertIn("pip does not resume", text)

    def test_install_script_parses(self):
        result = subprocess.run(
            ["bash", "-n", str(HERE / "install.sh")],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

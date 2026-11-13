#!/usr/bin/env python3
"""Build dataset-scout-v0.1.1.zip for verification / release.

Correct exec bits via external_attr: 0o100755 for scripts, 0o100644
for plain files. Bare 0o755 (no file-type bits) extracts fine under
the `unzip` CLI but GUI extractors drop the execute bit.
"""
import hashlib
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ZIP_NAME = "dataset-scout-v0.1.1.zip"

SCRIPTS = {"install.sh", "run.sh", "scout-cli", "run_tests.sh", "build_zip.py"}

SKIP_DIRS = {"__pycache__", ".forge", ".venv", "venv", ".git"}
SKIP_FILES = {".DS_Store"}


def collect() -> list[Path]:
    files = []
    for path in sorted(HERE.rglob("*")):
        if path.is_dir():
            continue
        if path.name == ZIP_NAME:
            continue
        if path.name in SKIP_FILES or path.suffix == ".zip":
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(HERE).parts):
            continue
        files.append(path)
    return files


def main() -> int:
    files = collect()
    out = HERE / ZIP_NAME
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            rel = path.relative_to(HERE).as_posix()
            info = zipfile.ZipInfo(rel)
            mode = 0o100755 if path.name in SCRIPTS else 0o100644
            info.external_attr = mode << 16
            zf.writestr(info, path.read_bytes())
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"wrote {out} ({out.stat().st_size} bytes, {len(files)} files)")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

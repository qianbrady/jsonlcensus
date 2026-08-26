"""Shared fixtures/helpers for the jsonlcensus test-suite (stdlib only).

Every temporary directory is created under ``<workspace>/.build-tmp`` so the
session workspace stays clean (iron rule: temp data lives only in
.build-tmp).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = PROJECT_ROOT.name  # "jsonlcensus"
BUILD_TMP = PROJECT_ROOT.parents[1] / ".build-tmp"


def fresh_dir(prefix: str = "jlc") -> Path:
    """Create an isolated temp dir under ``.build-tmp``."""
    BUILD_TMP.mkdir(parents=True, exist_ok=True)
    while True:
        candidate = BUILD_TMP / f"{prefix}-{uuid.uuid4().hex[:12]}"
        try:
            candidate.mkdir()  # default permissions -> inherit parent ACL
            return candidate
        except FileExistsError:  # pragma: no cover - astronomically unlikely
            continue


def write_jsonl(path: Path, rows) -> None:
    """Write *rows* (JSON strings or objects auto-dumped) as UTF-8 JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            if not isinstance(row, str):
                row = json.dumps(row, ensure_ascii=False)
            fh.write(row + "\n")


def jsonl_file(name: str, rows) -> Path:
    """Fresh temp dir + a JSONL file with *rows*; returns the file path."""
    path = fresh_dir() / name
    write_jsonl(path, rows)
    return path


def run_cli(args, cwd=None, env_extra=None) -> subprocess.CompletedProcess:
    """Run ``python -m jsonlcensus ...`` as a real subprocess."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    for noisy in ("PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO"):
        env.pop(noisy, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", PACKAGE_NAME, *args],
        capture_output=True,
        cwd=str(cwd or PROJECT_ROOT),
        env=env,
    )
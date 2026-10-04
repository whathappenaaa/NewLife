"""Portable music location and a real create/write check for recording targets."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


def application_root() -> Path:
    """The project root in source runs, or the directory containing the exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def default_music_folder() -> Path:
    return application_root() / "MUSIC"


def is_readable_folder(folder: str | Path) -> bool:
    try:
        candidate = Path(folder).expanduser()
        if not candidate.is_dir():
            return False
        with os.scandir(candidate):
            return True
    except OSError:
        return False


def check_folder_writable(folder: str | Path) -> None:
    """Raise on missing/readonly/full targets without trusting os.access or ACL bits."""
    with tempfile.TemporaryFile(mode="w+b", dir=Path(folder), prefix=".newlife-write-check-") as probe:
        probe.write(b"\0")
        probe.flush()
        os.fsync(probe.fileno())

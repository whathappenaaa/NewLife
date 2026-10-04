"""Copy only user-designated direct MUSIC files into the portable release."""
from __future__ import annotations

from pathlib import Path
import shutil
import sys

from niulai_player.models import SUPPORTED_EXTENSIONS


def copy_music(source: str | Path, destination: str | Path) -> list[Path]:
    """Replace direct files in the dedicated release MUSIC, leaving source untouched."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("Source MUSIC and release MUSIC must be different directories")
    if not source.is_dir():
        raise NotADirectoryError(source)
    destination.mkdir(parents=True, exist_ok=True)
    # Every unlink is a direct child of this explicit output directory. Never
    # recurse into folders or follow a file symlink to remove its destination.
    for stale in destination.iterdir():
        if stale.parent != destination:
            raise ValueError("Release MUSIC cleanup escaped its output directory")
        if stale.is_file() or stale.is_symlink():
            stale.unlink()
    copied = []
    for path in sorted(source.iterdir(), key=lambda entry: entry.name.casefold()):
        if path.is_symlink() or not path.is_file():
            continue
        if (path.suffix.lower() in SUPPORTED_EXTENSIONS
                or path.name.lower().endswith(".newlife") or path.name == "说明.txt"):
            target = destination / path.name
            shutil.copy2(path, target)
            copied.append(target)
    return copied


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: package_music.py SOURCE_MUSIC RELEASE_MUSIC")
    copy_music(sys.argv[1], sys.argv[2])

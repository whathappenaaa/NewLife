"""Fetch the pinned LGPL shared build; verify before extracting any executable."""
import hashlib
from pathlib import Path
import urllib.request
import zipfile

TAG = "autobuild-2026-09-30-13-08"
ASSET = "ffmpeg-n9.0.2-17-g2a571b6068-win64-lgpl-shared-9.0.zip"
SHA256 = "7157177b8a6cb2174c1650ba8c71b363f2c78cba5330f88c4c02cf5b2b880646"
URL = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/{TAG}/{ASSET}"


def main():
    root = Path(__file__).resolve().parents[1]
    directory = root / "vendor" / "ffmpeg"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / ASSET
    if not archive.exists():
        partial = archive.with_suffix(".zip.part")
        request = urllib.request.Request(URL, headers={"User-Agent": "NewLife-build"})
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                while block := response.read(1024 * 1024):
                    digest.update(block); output.write(block)
            if digest.hexdigest() != SHA256:
                raise RuntimeError("FFmpeg archive SHA256 mismatch")
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("FFmpeg archive SHA256 mismatch")
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            relative = Path(*Path(entry.filename).parts[1:])
            if not relative.parts:
                continue
            target = (directory / relative).resolve()
            if not target.is_relative_to(directory.resolve()):
                raise RuntimeError("Unsafe archive path")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, target.open("wb") as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
    print(f"Verified FFmpeg: {directory / 'bin'}")


if __name__ == "__main__":
    main()

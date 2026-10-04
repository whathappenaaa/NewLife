"""Archive the exact FFmpeg source and pinned build recipes alongside the binary."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

from fetch_ffmpeg import ASSET, SHA256, TAG, URL


def download(url, path):
    if path.exists():
        return
    partial = path.with_suffix(path.suffix + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "NewLife-build"})
        with urllib.request.urlopen(request, timeout=60) as source, partial.open("wb") as output:
            while block := source.read(1024 * 1024):
                output.write(block)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)


def main():
    root = Path(__file__).resolve().parents[1]
    vendor = root / "vendor" / "ffmpeg"
    target = vendor / "sources"
    target.mkdir(exist_ok=True)
    references = {
        "FFmpeg-2a571b6068.tar.gz": "https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/2a571b6068",
        f"FFmpeg-Builds-{TAG}.tar.gz": f"https://codeload.github.com/BtbN/FFmpeg-Builds/tar.gz/refs/tags/{TAG}",
    }
    records = []
    for name, url in references.items():
        path = target / name
        download(url, path)
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        records.append({"file": name, "url": url, "sha256": digest})
    configuration = subprocess.run([str(vendor / "bin" / "ffmpeg.exe"), "-hide_banner", "-buildconf"],
                                   capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    options = (configuration.stdout + configuration.stderr).decode("utf-8", "replace").split()
    if "--enable-gpl" in options or "--enable-nonfree" in options:
        raise RuntimeError("The release requires the pinned LGPL build")
    (vendor / "build-configuration.txt").write_bytes(configuration.stdout + configuration.stderr)
    (vendor / "build-manifest.json").write_text(json.dumps({
        "binary": {"asset": ASSET, "url": URL, "sha256": SHA256, "variant": "LGPL shared, version 3"},
        "sources": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()

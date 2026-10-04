from pathlib import Path
import importlib.metadata
import shutil
import sys

target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)
for package in ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6", "PyAudioWPatch", "numpy", "soundfile", "soxr", "Send2Trash", "pyinstaller"):
    distribution = importlib.metadata.distribution(package)
    for entry in distribution.files or []:
        if not any(word in str(entry).lower() for word in ("license", "copying", "notice")):
            continue
        source = Path(distribution.locate_file(entry))
        if source.is_file():
            destination = target / package / str(entry).replace("../", "")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    shutil.copyfile(python_license, target / "Python-LICENSE.txt")
vendor = Path(__file__).resolve().parents[1] / "vendor" / "ffmpeg"
ffmpeg_target = target / "FFmpeg"
ffmpeg_target.mkdir(exist_ok=True)
for name in ("LICENSE.txt", "build-manifest.json", "build-configuration.txt"):
    shutil.copyfile(vendor / name, ffmpeg_target / name)
shutil.copytree(vendor / "sources", ffmpeg_target / "sources", dirs_exist_ok=True)
print(target)

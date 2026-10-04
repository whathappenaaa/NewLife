"""Verify the frozen application with isolated test data and no microphone capture."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import struct
import subprocess
import uuid

import numpy as np
import soundfile as sf
from PySide6.QtGui import QImage, QColor
from niulai_player.library import Library
from niulai_player.models import path_key


def verify_icons(exe: Path, root: Path):
    """Compare the PE icon images with the same ICO shipped for Qt and the tray."""
    import pefile
    original = (root / "assets" / "app.ico").read_bytes()
    bundled = exe.parent / "_internal" / "assets" / "app.ico"
    assert bundled.read_bytes() == original, "Bundled Qt/tray icon differs from the source icon"
    count = struct.unpack_from("<H", original, 4)[0]
    expected = {}
    for index in range(count):
        width, height, _, _, _, _, size, offset = struct.unpack_from("<BBBBHHII", original, 6 + index * 16)
        expected[width or 256] = original[offset:offset + size]
        assert (width or 256) == (height or 256)
    resources, groups = {}, []
    with pefile.PE(str(exe), fast_load=True) as pe:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        for kind in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            if kind.id not in (3, 14):
                continue
            for identity in kind.directory.entries:
                for language in identity.directory.entries:
                    entry = language.data.struct
                    payload = pe.get_data(entry.OffsetToData, entry.Size)
                    if kind.id == 3:
                        resources[identity.id] = payload
                    else:
                        groups.append(payload)
    assert groups, "Executable does not contain a Windows icon group"
    group = groups[0]
    assert struct.unpack_from("<H", group, 4)[0] == count
    for index in range(count):
        width, _, _, _, _, _, _, resource_id = struct.unpack_from("<BBBBHHIH", group, 6 + index * 14)
        assert resources[resource_id] == expected[width or 256], "EXE and Qt/tray icon pixels differ"
    return {"sizes": sorted(expected), "sha256": hashlib.sha256(original).hexdigest(), "exe_matches_runtime": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    icons = verify_icons(args.exe.absolute(), root)
    run = root / "artifacts" / "release-verification" / uuid.uuid4().hex[:8]
    folder = run / "媒体格式验证"
    folder.mkdir(parents=True)
    for extension, rate in (("wav", 44100), ("mp3", 44100), ("flac", 48000), ("ogg", 32000)):
        axis = np.arange(round(rate * 3.5)) / rate
        envelope = np.minimum(axis / .1, 1) * np.minimum((3.5 - axis) / .2, 1)
        mono = .025 * envelope * np.sin(axis * 2 * np.pi * 660)
        sf.write(folder / f"格式验证.{extension}", np.column_stack([mono, mono]), rate)
    decoder = args.exe.absolute().parent / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
    def make_media(arguments):
        completed = subprocess.run([str(decoder), "-v", "error", "-nostdin", *arguments],
                                   capture_output=True, timeout=30)
        assert completed.returncode == 0, completed.stderr
    # First row exercises AAC decode in the frozen app despite a WAV suffix.
    make_media(["-i", str(folder / "格式验证.wav"), "-c:a", "aac", "-f", "mp4", str(folder / "00实际AAC.wav")])
    make_media(["-f", "lavfi", "-i", "color=size=32x32:rate=10", "-i", str(folder / "格式验证.wav"),
                "-t", "3.5", "-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", "-c:a", "aac",
                str(folder / "带音轨的视频.mp4")])
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PATH"] = str(Path(env.get("WINDIR", "C:/Windows")) / "System32")
    env["QT_QPA_PLATFORM"] = "offscreen"
    results = []
    # Normal audio backend enumerates WASAPI devices but opens no recording stream.
    checks = [("main", "1", "zh_CN", "dark"), ("settings", "1", "zh_CN", "dark"), ("mini", "1", "zh_CN", "dark"),
              ("main", "1.5", "zh_CN", "dark"), ("main", "2", "zh_CN", "dark"), ("mini", "1.5", "zh_CN", "dark"),
              ("mini", "2", "en", "dark"), ("main", "1", "en", "dark"), ("settings", "1", "en", "dark"),
              ("main", "1", "en", "light"), ("settings", "1.5", "zh_CN", "light"), ("mini", "2", "en", "light"),
              ("shortcuts", "1", "zh_CN", "dark"), ("shortcuts", "1.5", "en", "light"), ("settings", "2", "en", "light")]
    for view, scale, language, theme in checks:
        config = run / f"config-{view}-{scale}-{language}-{theme}"
        profile = Library(config)
        profile.set_setting("theme", theme)
        profile.close()
        screenshot = run / f"{view}-{scale}-{language}-{theme}.png"
        env["QT_SCALE_FACTOR"] = scale
        command = [str(args.exe.absolute()), "--folder", str(folder), "--data-dir", str(config),
                   "--screenshot", str(screenshot), "--view", view, "--language", language]
        completed = subprocess.run(command, cwd=run, env=env, timeout=45, capture_output=True)
        if completed.returncode or not screenshot.is_file():
            raise RuntimeError(f"Frozen application failed: {view} scale={scale}, exit={completed.returncode}, {completed.stderr!r}")
        with sqlite3.connect(config / "library.sqlite3") as db:
            count = db.execute("SELECT COUNT(*) FROM items WHERE present=1 AND probe_ok=1 AND duration>=1").fetchone()[0]
            settings = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM settings")}
        assert count == 6, f"Codec scan failed: {count}/6"
        log = (config / "logs" / "application.log").read_text(encoding="utf-8")
        assert "ERROR" not in log and "Traceback" not in log, log
        assert settings.get("local_device"), "WASAPI enumeration did not select a real default output"
        assert settings.get("ui_language") == language, "Language setting did not persist"
        assert settings.get("theme") == theme
        results.append({"view": view, "scale": scale, "language": language, "theme": theme, "exit_code": 0, "valid_audio_files": count,
                        "local_device": settings["local_device"], "screenshot": str(screenshot)})
    # Relocate the complete release. A new profile must choose exe/MUSIC, not cwd
    # or the frozen _internal resource directory, and load portable appearance.
    relocated = run / "relocated" / "NewLife"
    shutil.copytree(args.exe.absolute().parent, relocated)
    music = relocated / "MUSIC"
    music.mkdir(exist_ok=True)
    media = music / "portable-default.wav"
    shutil.copy2(folder / "格式验证.wav", media)
    artwork = QImage(100, 80, QImage.Format.Format_RGB32)
    artwork.fill(QColor("#2db5a4"))
    artwork_path = run / "generated-artwork.png"
    assert artwork.save(str(artwork_path))
    prepared = Library(run / "prepare-portable")
    try:
        item = next(entry for entry in prepared.scan(str(music)) if entry.path == str(media.absolute()))
        item.start, item.end, item.color = .5, 2.5, "#3d6870"
        prepared.save_item(item)
        prepared.apply_avatar_crop(item, str(artwork_path), (8, 8, 48, 48))
        prepared.apply_background_crop(item, str(artwork_path), (0, 0, 96, 48))
        assert not prepared.flush_pending(timeout=10)
    finally:
        prepared.close()
    default_profile = run / "config-default-music"
    env["QT_SCALE_FACTOR"] = "1"
    output = run / "default-music-relocated.png"
    completed = subprocess.run([str(relocated / args.exe.name), "--no-audio", "--data-dir", str(default_profile),
                                "--screenshot", str(output)], cwd=folder, env=env, timeout=45, capture_output=True)
    assert completed.returncode == 0 and output.is_file(), completed.stderr
    with sqlite3.connect(default_profile / "library.sqlite3") as db:
        settings = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM settings")}
        row = db.execute("SELECT start,end,color,avatar,background FROM items WHERE path=?", (path_key(media),)).fetchone()
    assert Path(settings["folder"]) == music.absolute(), settings
    assert row[:3] == (.5, 2.5, "#3d6870"), row
    for asset in row[3:]:
        assert asset and (default_profile / asset).is_file(), row
    assert "ERROR" not in (default_profile / "logs" / "application.log").read_text(encoding="utf-8")
    results.append({"view": "relocated-default-music", "exit_code": 0, "folder": settings["folder"],
                    "portable_range_color_avatar_background_loaded": True, "screenshot": str(output)})
    report = run / "results.json"
    report.write_text(json.dumps({"exe": str(args.exe.absolute()), "path_excludes_python": True,
                                 "physical_microphone_opened": False, "system_settings_changed": False,
                                 "icons": icons, "checks": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()

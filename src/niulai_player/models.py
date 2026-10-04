from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

MIN_SEGMENT_SECONDS = 1.0
SUPPORTED_EXTENSIONS = frozenset({
    ".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".m4b", ".aac", ".wma", ".aif", ".aiff", ".ape",
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".wmv", ".flv", ".ts", ".mts", ".m2ts", ".m4v", ".vob",
    ".mpeg", ".mpg", ".3gp",
})
PLAY_MODES = ("单次播放", "单段循环", "顺序播放", "列表循环")
RECORD_SOURCES = ("电脑", "麦克风", "都录制")


def path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


@dataclass
class AudioItem:
    path: str
    name: str
    duration: float
    start: float = 0.0
    end: float = 0.0
    color: str = "#202832"
    avatar: str = "🐮"
    background: str = ""
    hotkey: str = ""
    order: int = 0
    error: str = ""
    peaks: list[float] = field(default_factory=list)
    # Coordinates refer to the EXIF-oriented original, never the 256px preview.
    avatar_source: str = ""
    avatar_crop: tuple[int, int, int, int] | None = None
    background_source: str = ""
    background_crop: tuple[int, int, int, int] | None = None
    audio_stream: int | None = None
    audio_streams: list[dict] = field(default_factory=list)
    portable_id: str = ""
    portable_status: str = ""

    def __post_init__(self) -> None:
        if not self.end and self.duration:
            self.end = self.duration

    @property
    def key(self) -> str:
        return path_key(self.path)

    @property
    def segment_duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def playable(self) -> bool:
        return not self.error and self.duration >= MIN_SEGMENT_SECONDS


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    name: str
    kind: str  # input / output / loopback
    rate: float = 48000.0
    channels: int = 2
    is_default: bool = False
    is_virtual: bool = False


def valid_range(duration: float, start: float, end: float) -> tuple[float, float]:
    if duration < MIN_SEGMENT_SECONDS:
        raise ValueError("音频不足 1 秒，无法创建播放片段")
    start = max(0.0, min(float(start), duration - MIN_SEGMENT_SECONDS))
    end = min(duration, max(float(end), start + MIN_SEGMENT_SECONDS))
    return start, end

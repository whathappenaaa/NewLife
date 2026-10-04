"""生成原创合成音效，作为开发用样例，不使用个人录音。"""
from pathlib import Path
import numpy as np
import soundfile as sf

folder = Path(__file__).resolve().parents[1] / "examples" / "audio"
folder.mkdir(parents=True, exist_ok=True)
rate = 48000
for name, seconds, frequencies in [
    ("牛来了", 4.0, [330, 440, 550]),
    ("清脆提示", 3.0, [880, 1100, 660]),
    ("开场音效", 8.0, [220, 330, 440, 660]),
    ("收到，收到", 2.5, [520, 780]),
]:
    path = folder / (name + ".wav")
    if path.exists():
        continue
    t = np.arange(int(seconds * rate), dtype=np.float32) / rate
    envelope = np.minimum(1, t / .03) * np.minimum(1, (seconds - t) / .12)
    index = np.minimum((t / (seconds / len(frequencies))).astype(int), len(frequencies) - 1)
    tones = np.array(frequencies)[index]
    audio = np.sin(2 * np.pi * tones * t) * envelope * .13
    sf.write(path, np.column_stack((audio, audio)), rate, subtype="PCM_16")
print(folder)

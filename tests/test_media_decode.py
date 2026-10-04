"""Real bundled decoder tests using generated signals, never user recordings."""
import threading
import time

import numpy as np
import pytest
import soundfile as sf

from niulai_player.audio_processing import RATE
from niulai_player.media_decode import (CHUNK_FRAMES, DecodeCancelled, MediaDecoder,
                                        MediaDecodeError, PcmChunkCache, ProgressiveSamples)


@pytest.fixture
def decoder(tmp_path):
    return MediaDecoder(cache_dir=tmp_path / "pcm")


@pytest.fixture
def long_signal(tmp_path):
    rate = 44100
    t = np.arange(round(rate * 32.25)) / rate
    signal = .2 * np.sin(2 * np.pi * (311 * t + 9 * t * t))
    path = tmp_path / "reference.wav"
    sf.write(path, np.column_stack([signal, -.7 * signal]), rate, subtype="PCM_16")
    return path


def encode(decoder, source, target, codec):
    process = decoder._spawn("ffmpeg", ["-v", "error", "-i", str(source), "-c:a", codec, str(target)])
    _, error = process.communicate(timeout=30)
    assert process.returncode == 0, error
    return target


@pytest.mark.parametrize("extension,codec", [("wav", "pcm_s16le"), ("mp3", "libmp3lame"),
                                            ("m4a", "aac"), ("opus", "libopus")])
def test_seek_and_thirty_second_boundary_match_full_decode(decoder, long_signal, tmp_path, extension, codec):
    path = encode(decoder, long_signal, tmp_path / ("encoded." + extension), codec)
    full = np.concatenate(list(decoder.iter_pcm(path)))
    for start, end in [(5.375, 6.375), (30.0, 31.0)]:
        segment = np.concatenate(list(decoder.iter_pcm(path, start=start, end=end)))
        reference = full[round(start * RATE):round(end * RATE)]
        assert len(segment) == len(reference) == RATE
        # Resampler edge transients are excluded; timing/phase errors remain visible.
        np.testing.assert_allclose(segment[256:-256], reference[256:-256], atol=.002)


def test_progressive_playback_seek_and_bounded_window(decoder, long_signal):
    samples = ProgressiveSamples(decoder, long_signal, None, threading.Event())
    try:
        assert samples.first_ready.wait(10)
        assert samples.error is None
        assert samples.take(0, 480).shape == (480, 2)
        samples.seek(30 * RATE)
        deadline = time.monotonic() + 10
        while not len(block := samples.take(30 * RATE, 960)):
            assert samples.error is None
            assert time.monotonic() < deadline
            time.sleep(.01)
        reference = np.concatenate(list(decoder.iter_pcm(long_signal, start=30, end=30.02)))
        np.testing.assert_allclose(block, reference, atol=.002)
        assert len(samples._chunks) <= 2
        assert len(samples) > 31 * RATE
    finally:
        samples.close()
        samples._thread.join(10)
        assert not samples._thread.is_alive()


def test_cancellation_releases_decode_slot_and_temporary_export(decoder, long_signal, tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DecodeCancelled):
        list(decoder.iter_pcm(long_signal, cancelled=cancel))
    with pytest.raises(DecodeCancelled):
        decoder.export_segment(long_signal, tmp_path / "export.wav", 0, 2, cancelled=cancel)
    assert not list(tmp_path.glob("*.part"))
    assert not (tmp_path / "export.wav").exists()
    assert next(decoder.iter_pcm(long_signal)).shape[1] == 2


def test_cache_lru_corruption_and_file_change_identity(tmp_path, long_signal):
    cache = PcmChunkCache(tmp_path / "cache", limit=128)
    data = np.ones((8, 2), np.float32)
    identity = cache.identity(long_signal, 0, "decoder1")
    cache.store(identity, 0, data)
    cache.store(identity, 1, data * 2)
    cache.load(identity, 0)
    cache.store(identity, 2, data * 3)
    assert cache.load(identity, 1) is None
    np.testing.assert_array_equal(cache.load(identity, 0), data)
    cache._path(identity, 0).write_bytes(b"broken")
    assert cache.load(identity, 0) is None
    with long_signal.open("ab") as output:
        output.write(b"changed")
    assert cache.identity(long_signal, 0, "decoder1") != identity


def test_video_audio_track_selection_and_no_audio_error(decoder, tmp_path):
    video = tmp_path / "tracks.mp4"
    process = decoder._spawn("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "color=size=32x32:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-f", "lavfi", "-i",
        "sine=frequency=880:sample_rate=48000", "-t", "2", "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-c:v", "mpeg4", "-c:a", "aac", "-disposition:a:0", "0", "-disposition:a:1", "default",
        "-metadata:s:a:1", "language=eng", str(video)])
    _, error = process.communicate(timeout=30)
    assert process.returncode == 0, error
    info = decoder.probe(video)
    assert [track.index for track in info.tracks] == [1, 2]
    assert info.default_stream == 2
    for stream, frequency in [(1, 440), (2, 880)]:
        samples = np.concatenate(list(decoder.iter_pcm(video, stream_index=stream, end=1)))
        dominant = np.argmax(np.abs(np.fft.rfft(samples[:, 0])))
        assert abs(dominant - frequency) <= 1
    with pytest.raises(MediaDecodeError, match="音轨"):
        list(decoder.iter_pcm(video, stream_index=0))
    silent = tmp_path / "silent.mp4"
    process = decoder._spawn("ffmpeg", ["-v", "error", "-i", str(video), "-an", "-c:v", "copy", str(silent)])
    process.communicate(timeout=30)
    with pytest.raises(MediaDecodeError, match="音轨"):
        decoder.probe(silent)

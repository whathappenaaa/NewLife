import numpy as np
import pytest

from niulai_player.audio_processing import (
    RATE, AudioTimeline, CaptureConverter, ClipCursor, RoutedBuffer,
    StreamingResampler, CaptureTimestampClock, SmoothLimiter, pcm16, stereo,
)


def test_pcm_saturates_and_sanitizes():
    data = np.array([[-4, 4], [np.nan, np.inf], [-1, 1]], np.float32)
    result = np.frombuffer(pcm16(data), "<i2").reshape(-1, 2)
    np.testing.assert_array_equal(result, [[-32768, 32767], [0, 0], [-32768, 32767]])
    np.testing.assert_allclose(stereo(np.array([.2, .3])), [[.2, .2], [.3, .3]])


def test_resampler_preserves_duration_and_chunk_boundaries():
    native_rate = 44100
    tone = np.sin(np.arange(native_rate) * 2 * np.pi * 440 / native_rate).astype(np.float32)
    source = stereo(tone)
    whole = StreamingResampler(native_rate).process(source, final=True)
    chunked = StreamingResampler(native_rate)
    parts = [chunked.process(source[i:i + 441]) for i in range(0, len(source), 441)]
    parts.append(chunked.process(np.empty((0, 2), np.float32), final=True))
    result = np.concatenate(parts)
    assert abs(len(result) - RATE) <= 1
    np.testing.assert_allclose(result, whole, atol=1e-6)
    assert np.max(np.abs(np.diff(result[100:-100, 0]))) < .07


def test_timestamp_ring_has_silence_for_gaps_and_is_nonconsuming():
    ring = AudioTimeline(16)
    ring.add(100, np.ones((5, 2), np.float32))
    ring.add(109, np.full((3, 2), .5, np.float32))
    result = ring.read(99, 14)
    np.testing.assert_array_equal(result[:, 0], [0, 1, 1, 1, 1, 1, 0, 0, 0, 0, .5, .5, .5, 0])
    np.testing.assert_array_equal(ring.read(99, 14), result)
    ring.add(116, np.ones((4, 2), np.float32))
    assert not ring.read(100, 4).any()


def test_capture_preserves_missing_packet_interval():
    converter = CaptureConverter(44100)
    source = np.ones((441, 1), np.float32)
    for index in range(30):
        if index != 10:
            converter.feed(source, 100 + index / 100)
    gap = converter.timeline.read(round(100.101 * RATE), round(.008 * RATE))
    assert np.max(np.abs(gap)) < .02
    after = converter.timeline.read(round(100.12 * RATE), 480)
    assert np.mean(after) > .98


def test_capture_tracks_a_fast_hardware_clock():
    converter = CaptureConverter(48000)
    samples = np.full((480, 1), .1, np.float32)
    for index in range(1000):
        converter.feed(samples, 100 + index * 480 / (48000 * 1.001))
    assert 990 < converter.correction_ppm < 1010
    assert np.isfinite(converter.timeline.read(round(109 * RATE), 480)).all()


def test_clearing_clip_keeps_microphone_queued_and_outputs_independent():
    local, call = RoutedBuffer(20), RoutedBuffer(20)
    mic, clip = np.full((10, 2), .2, np.float32), np.full((10, 2), .3, np.float32)
    local.levels(0, 1)
    call.levels(1, 1)
    local.add(mic, clip)
    call.add(mic, clip)
    np.testing.assert_allclose(local.read(10), .3)
    call.clear_clip()
    np.testing.assert_allclose(call.read(10), .2)
    assert not call.read(5).any()


def test_independent_resampler_lane_lengths_do_not_discard_samples():
    output = RoutedBuffer(20)
    output.levels(1, 1)
    output.add(np.full((4, 2), .1, np.float32), np.full((7, 2), .3, np.float32))
    np.testing.assert_allclose(output.read(4), .4)
    np.testing.assert_allclose(output.read(3), .3)


def test_range_edit_during_play_keeps_or_jumps_cursor_and_pause_is_silent():
    samples = np.repeat(np.arange(100, dtype=np.float32)[:, None], 2, axis=1)
    cursor = ClipCursor(samples, 20, 60)
    np.testing.assert_array_equal(cursor.read(10)[:, 0], np.arange(20, 30))
    assert not cursor.set_range(25, 70)
    assert cursor.position == 30
    cursor.paused = True
    assert not cursor.read(5).any()
    assert cursor.position == 30
    assert cursor.set_range(40, 45)
    cursor.paused = False
    np.testing.assert_array_equal(cursor.read(8)[:, 0], [40, 41, 42, 43, 44, 0, 0, 0])
    assert cursor.finished


def test_fallback_capture_clock_ignores_arrival_jitter_but_keeps_adc_gap():
    clock = CaptureTimestampClock(RATE)
    stamps = [clock.stamp(480, arrival) for arrival in [100, 100.009, 100.025, 100.032, 100.048]]
    np.testing.assert_allclose(np.diff(stamps), .01, atol=1e-9)
    assert clock.fallback_packets == 5
    # A timestamp source change must not reinterpret latency as missing audio.
    assert clock.stamp(480, 100.20, 100.18) == pytest.approx(100.04)
    assert clock.stamp(480, 100.24) == pytest.approx(100.05)
    adc_clock = CaptureTimestampClock(RATE)
    assert adc_clock.stamp(480, 100, 99.98) == pytest.approx(99.98)
    # An actual discontinuity while ADC remains reliable is preserved.
    assert adc_clock.stamp(480, 100.10, 100.08) == pytest.approx(100.08)


@pytest.mark.parametrize("ppm", [-100, 100])
def test_fallback_clock_tracks_long_term_drift_without_packet_holes(ppm):
    native_rate, frames = 44100, 2205
    clock = CaptureTimestampClock(native_rate)
    converter = CaptureConverter(native_rate)
    samples = np.full((frames, 1), .2, np.float32)
    rng = np.random.default_rng(24)
    origin, initial = 100.0, None
    for index in range(3600):  # Three minutes, including delayed callback bursts.
        arrival = origin + (index + 1) * frames / (native_rate * (1 + ppm / 1e6))
        arrival += float(rng.uniform(0, .002)) + (.025 if index % 71 == 0 else 0)
        if index == 0:
            arrival = origin + frames / (native_rate * (1 + ppm / 1e6))
        stamp = clock.stamp(frames, arrival)
        initial = stamp if initial is None else initial
        converter.feed(samples, stamp)
    expected = 3600 * frames / (native_rate * (1 + ppm / 1e6))
    assert abs(clock.correction_ppm - ppm) < 15
    assert abs(clock.next_stamp - initial - expected) < .004
    assert abs(converter.correction_ppm - ppm) < 22
    assert converter.gap_resets == 0
    # Resampling stays continuous: no silent holes, including the recent tail.
    assert np.min(converter.timeline.read(converter.next_frame - RATE, RATE)) > .19


def test_switching_adc_and_fallback_preserves_continuity_with_latency_difference():
    clock = CaptureTimestampClock(RATE)
    converter = CaptureConverter(RATE)
    samples = np.full((480, 1), .2, np.float32)
    for index in range(2000):
        arrival = 100 + (index + 1) / 100
        adc = arrival - .08 if index < 500 or index >= 1000 else None
        converter.feed(samples, clock.stamp(480, arrival, adc))
    assert converter.gap_resets == 0
    assert np.min(converter.timeline.read(converter.next_frame - RATE, RATE)) > .19


def test_limiter_prevents_overload_and_releases_smoothly_across_blocks():
    limiter = SmoothLimiter()
    overloaded = np.tile(np.array([[2, 1]], np.float32), (480, 1))
    limited = limiter.process(overloaded)
    assert np.max(np.abs(limited)) <= .981
    np.testing.assert_allclose(limited[:, 0], limited[:, 1] * 2)
    quiet = limiter.process(np.full((480, 2), .2, np.float32))
    assert quiet[0, 0] < quiet[-1, 0] < .2
    assert np.max(np.abs(np.diff(quiet[:, 0]))) < .0001
    assert limiter.limited_frames > 480



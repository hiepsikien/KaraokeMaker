"""Tests for the short waveform module (plan section 7)."""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from karaoke.presentation import DEFAULT_WAVEFORM
from karaoke.profiles import get_profile
from karaoke.waveform import (
    PEAKS_SMOOTHING_VERSION,
    WAVEFORM_MODULE_VERSION,
    WaveformError,
    load_peaks,
    peaks_digest,
    render_waveform_clip,
    waveform_identity,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_FFPROBE = shutil.which("ffprobe") is not None
FFMPEG_REQUIRED = unittest.skipUnless(
    HAS_FFMPEG and HAS_FFPROBE, "ffmpeg/ffprobe required"
)


def small_profile():
    return replace(
        get_profile("landscape_1080p"),
        width=160,
        height=90,
        fps=10,
        strip_rect=(0, 60, 160, 30),
        lyric_font_size=20,
        max_lyric_width=140,
        lyric_row_gap=4,
        cover_title_size=24,
        cover_artist_size=16,
        cover_line_size=12,
        cover_max_width=140,
    )


def make_wav(
    path: Path,
    seconds: float = 2.0,
    rate: int = 8000,
    frequency: float = 220.0,
    amplitude: float = 0.5,
    envelope: bool = False,
) -> Path:
    """Write a small mono 16-bit WAV without external tools."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    total = max(0, int(round(seconds * rate)))
    for index in range(total):
        moment = index / float(rate)
        gain = amplitude
        if envelope:
            gain *= 0.2 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * 0.7 * moment))
        value = int(round(gain * 32767 * math.sin(2 * math.pi * frequency * moment)))
        frames += struct.pack("<h", max(-32768, min(32767, value)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


def make_silent_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\0\0" * int(seconds * rate))
    return path


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,nb_frames,r_frame_rate"
            ":format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def read_frame(path: Path, timestamp: float) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-ss",
            f"{timestamp:g}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode("utf-8", "replace"))
    return result.stdout


def waveform_plan(**overrides) -> dict:
    plan = dict(DEFAULT_WAVEFORM)
    plan.update(
        {
            "enabled": True,
            "style": "bars",
            "x": 0.5,
            "y": 0.84,
            "width": 0.5,
            "height": 0.2,
            "color": "#ffc448",
            "accent": "#ff7a59",
            "opacity": 0.9,
            "background": True,
            "mode": "progress",
        }
    )
    plan.update(overrides)
    return plan


class WaveformTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.cache = self.root / "cache"
        self.audio = make_wav(self.root / "song.wav", seconds=2.0)
        self.profile = small_profile()

    def patched_cache(self):
        return patch.dict(os.environ, {"KARAOKE_CACHE_DIR": str(self.cache)})


class PeaksTests(WaveformTestBase):
    @FFMPEG_REQUIRED
    def test_load_peaks_normalises_and_reports_buckets(self) -> None:
        with self.patched_cache():
            peaks = load_peaks(self.audio, rate=50)

        self.assertEqual(peaks["rate"], 50)
        self.assertEqual(peaks["sample_rate"], 8000)
        self.assertAlmostEqual(peaks["duration"], 2.0, places=2)
        self.assertEqual(peaks["count"], len(peaks["rms"]))
        self.assertEqual(peaks["count"], len(peaks["peak"]))
        self.assertGreaterEqual(peaks["count"], 95)
        self.assertLessEqual(peaks["count"], 101)
        self.assertAlmostEqual(max(peaks["rms"]), 1.0, places=3)
        self.assertAlmostEqual(max(peaks["peak"]), 1.0, places=3)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in peaks["rms"]))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in peaks["peak"]))
        self.assertEqual(peaks["smoothing_version"], PEAKS_SMOOTHING_VERSION)

    @FFMPEG_REQUIRED
    def test_peaks_cache_is_reused_and_keyed_by_rate(self) -> None:
        with self.patched_cache():
            first = load_peaks(self.audio, rate=50)
            cache_dir = self.cache / "waveform"
            files = sorted(cache_dir.glob("peaks-*.json"))
            self.assertEqual(len(files), 1)
            first_mtime = files[0].stat().st_mtime_ns

            second = load_peaks(self.audio, rate=50)
            self.assertEqual(first, second)
            self.assertEqual(
                sorted(cache_dir.glob("peaks-*.json"))[0].stat().st_mtime_ns,
                first_mtime,
            )

            coarse = load_peaks(self.audio, rate=25)
            self.assertEqual(len(sorted(cache_dir.glob("peaks-*.json"))), 2)
            self.assertEqual(coarse["rate"], 25)
            self.assertLess(coarse["count"], first["count"])

    @FFMPEG_REQUIRED
    def test_silent_audio_returns_zero_peaks(self) -> None:
        silent = make_silent_wav(self.root / "silent.wav", seconds=0.5)
        with self.patched_cache():
            peaks = load_peaks(silent, rate=50)
        self.assertEqual(peaks["count"], 25)
        self.assertEqual(max(peaks["rms"]), 0.0)
        self.assertEqual(max(peaks["peak"]), 0.0)

    @FFMPEG_REQUIRED
    def test_short_audio_still_has_one_bucket(self) -> None:
        short = make_wav(self.root / "short.wav", seconds=0.01)
        with self.patched_cache():
            peaks = load_peaks(short)
        self.assertEqual(peaks["count"], 1)
        self.assertAlmostEqual(peaks["duration"], 0.01, places=3)

    @FFMPEG_REQUIRED
    def test_empty_audio_is_reported_as_empty_peaks(self) -> None:
        empty = self.root / "empty.wav"
        with wave.open(str(empty), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"")
        with self.patched_cache():
            peaks = load_peaks(empty)
        self.assertEqual(peaks["count"], 0)
        self.assertEqual(peaks["rms"], [])
        self.assertEqual(peaks["peak"], [])

    @FFMPEG_REQUIRED
    def test_invalid_audio_raises_clear_error(self) -> None:
        broken = self.root / "broken.wav"
        broken.write_text("not audio", encoding="utf-8")
        with self.patched_cache():
            with self.assertRaises(WaveformError):
                load_peaks(broken)

    def test_missing_audio_raises_clear_error(self) -> None:
        with self.assertRaises(WaveformError):
            load_peaks(self.root / "does-not-exist.wav")

    def test_missing_ffmpeg_raises_clear_error(self) -> None:
        with self.patched_cache():
            with patch("karaoke.waveform.shutil.which", return_value=None):
                with self.assertRaises(WaveformError) as caught:
                    load_peaks(self.audio)
        self.assertIn("ffmpeg", str(caught.exception))

    @FFMPEG_REQUIRED
    def test_peaks_digest_is_stable_and_content_based(self) -> None:
        other = make_wav(self.root / "other.wav", seconds=1.0, envelope=True)
        with self.patched_cache():
            first = load_peaks(self.audio)
            second = load_peaks(self.audio)
            third = load_peaks(other)
        self.assertEqual(peaks_digest(first), peaks_digest(second))
        self.assertNotEqual(peaks_digest(first), peaks_digest(third))


class IdentityTests(WaveformTestBase):
    @FFMPEG_REQUIRED
    def test_identity_is_stable_and_covers_visuals_and_frames(self) -> None:
        plan = waveform_plan()
        with self.patched_cache():
            first = waveform_identity(plan, self.audio, self.profile, 6, 30, 6)
            same = waveform_identity(plan, self.audio, self.profile, 6, 30, 6)
            recoloured = waveform_identity(
                waveform_plan(color="#00ff00"), self.audio, self.profile, 6, 30, 6
            )
            taller = waveform_identity(
                waveform_plan(height=0.3), self.audio, self.profile, 6, 30, 6
            )
            other_frames = waveform_identity(
                plan, self.audio, self.profile, 6, 31, 6
            )
            other_audio = waveform_identity(
                plan,
                make_wav(self.root / "other.wav", seconds=1.0, envelope=True),
                self.profile,
                6,
                30,
                6,
            )
        self.assertEqual(first, same)
        self.assertTrue(first.startswith("sha256:"))
        self.assertNotEqual(first, recoloured)
        self.assertNotEqual(first, taller)
        self.assertNotEqual(first, other_frames)
        self.assertNotEqual(first, other_audio)
        self.assertGreaterEqual(WAVEFORM_MODULE_VERSION, 1)


class RenderTests(WaveformTestBase):
    @FFMPEG_REQUIRED
    def test_render_bars_is_a_valid_mp4_with_full_timeline(self) -> None:
        destination = self.root / "out" / "waveform.mp4"
        messages: list[str] = []
        with self.patched_cache():
            result = render_waveform_clip(
                waveform_plan(),
                self.audio,
                self.profile,
                0.2,
                1.0,
                0.2,
                destination,
                messages.append,
            )
        self.assertEqual(result, destination)
        self.assertTrue(destination.is_file())
        self.assertGreater(destination.stat().st_size, 0)
        self.assertTrue(messages)

        info = probe(destination)
        stream = info["streams"][0]
        self.assertEqual(stream["codec_name"], "h264")
        self.assertEqual(stream["pix_fmt"], "yuv420p")
        self.assertEqual(stream["width"], 80)
        self.assertEqual(stream["height"], 18)
        self.assertAlmostEqual(float(info["format"]["duration"]), 1.4, places=1)
        self.assertEqual(int(stream["nb_frames"]), 14)

    @FFMPEG_REQUIRED
    def test_render_line_and_dots_styles(self) -> None:
        for style in ("line", "dots"):
            with self.subTest(style=style):
                destination = self.root / f"waveform-{style}.mp4"
                with self.patched_cache():
                    render_waveform_clip(
                        waveform_plan(style=style),
                        self.audio,
                        self.profile,
                        0.0,
                        0.6,
                        0.0,
                        destination,
                        None,
                    )
                info = probe(destination)
                self.assertEqual(info["streams"][0]["codec_name"], "h264")
                self.assertGreater(destination.stat().st_size, 0)

    @FFMPEG_REQUIRED
    def test_transparent_background_uses_qtrle_mov(self) -> None:
        destination = self.root / "waveform.mp4"
        with self.patched_cache():
            result = render_waveform_clip(
                waveform_plan(background=False),
                self.audio,
                self.profile,
                0.0,
                0.5,
                0.0,
                destination,
                None,
            )
        self.assertEqual(result.suffix.lower(), ".mov")
        self.assertTrue(result.is_file())
        info = probe(result)
        stream = info["streams"][0]
        self.assertEqual(stream["codec_name"], "qtrle")
        self.assertEqual(stream["pix_fmt"], "argb")
        self.assertEqual(stream["width"], 80)
        self.assertEqual(stream["height"], 18)

    @FFMPEG_REQUIRED
    def test_only_song_window_has_waveform_content(self) -> None:
        destination = self.root / "timeline.mp4"
        with self.patched_cache():
            render_waveform_clip(
                waveform_plan(),
                self.audio,
                self.profile,
                0.2,
                0.8,
                0.2,
                destination,
                None,
            )
        cover_frame = read_frame(destination, 0.0)
        song_frame = read_frame(destination, 0.6)
        self.assertTrue(cover_frame)
        self.assertTrue(song_frame)
        # The cover frame is blank black; the song frame carries the chip and
        # waveform, so it cannot be byte-for-byte the same.
        self.assertNotEqual(cover_frame, song_frame)
        cover_image = Image.frombytes("RGB", (80, 18), cover_frame)
        song_image = Image.frombytes("RGB", (80, 18), song_frame)
        self.assertEqual(cover_image.getextrema()[0][1], 0)
        self.assertGreater(song_image.convert("L").getextrema()[1], 0)

    @FFMPEG_REQUIRED
    def test_zero_cover_and_outro_still_render(self) -> None:
        destination = self.root / "no-pages.mp4"
        with self.patched_cache():
            render_waveform_clip(
                waveform_plan(),
                self.audio,
                self.profile,
                0.0,
                0.5,
                0.0,
                destination,
                None,
            )
        info = probe(destination)
        self.assertEqual(int(info["streams"][0]["nb_frames"]), 5)

    @FFMPEG_REQUIRED
    def test_silent_audio_and_short_audio_render(self) -> None:
        for name, audio in (
            ("silent", make_silent_wav(self.root / "silent.wav", seconds=0.5)),
            ("short", make_wav(self.root / "short.wav", seconds=0.01)),
        ):
            with self.subTest(audio=name):
                destination = self.root / f"{name}.mp4"
                with self.patched_cache():
                    render_waveform_clip(
                        waveform_plan(),
                        audio,
                        self.profile,
                        0.0,
                        0.5,
                        0.0,
                        destination,
                        None,
                    )
                self.assertTrue(destination.is_file())
                self.assertGreater(destination.stat().st_size, 0)

    def test_zero_duration_raises_clear_error(self) -> None:
        with patch("karaoke.waveform.shutil.which", return_value="/usr/bin/ffmpeg"):
            with self.assertRaises(WaveformError):
                render_waveform_clip(
                    waveform_plan(),
                    self.audio,
                    self.profile,
                    0.0,
                    0.0,
                    0.0,
                    self.root / "nothing.mp4",
                    None,
                )

    def test_missing_ffmpeg_render_raises_clear_error(self) -> None:
        with patch("karaoke.waveform.shutil.which", return_value=None):
            with self.assertRaises(WaveformError) as caught:
                render_waveform_clip(
                    waveform_plan(),
                    self.audio,
                    self.profile,
                    0.0,
                    0.5,
                    0.0,
                    self.root / "nothing.mp4",
                    None,
                )
        self.assertIn("ffmpeg", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

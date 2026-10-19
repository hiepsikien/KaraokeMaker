import os
import tempfile
import unittest
from pathlib import Path

from karaoke.access import AccessError, audio_root, clear_grants, ensure_allowed, grant
import math
import struct
import wave

from karaoke.align import _segments_match_lines, fit_lines_to_voice, loosen_against_next_line, repair_stretched_words
from karaoke.lyrics import extract_sung_lines
from karaoke.render import COUNTDOWN, LONG_GAP, cue_at


class LyricsTests(unittest.TestCase):
    def test_skips_blank_lines_and_stage_directions(self):
        lyrics = "\n[Intro]\n[Tiếng trống]\n\nMười tám con người\n"
        self.assertEqual(extract_sung_lines(lyrics), ["Mười tám con người"])


class RepairTests(unittest.TestCase):
    def test_pulls_a_word_back_from_a_long_gap(self):
        words = [
            {"text": "TRỜI", "start": 121.30, "end": 122.28},
            {"text": "NAM!", "start": 191.46, "end": 192.68},
        ]
        repaired, moved = repair_stretched_words(words)
        self.assertEqual(moved, 1)
        self.assertLess(repaired[1]["start"], 123)
        self.assertGreater(repaired[1]["end"], repaired[1]["start"])

    def test_keeps_a_short_pause(self):
        words = [
            {"text": "vọng", "start": 10.0, "end": 10.4},
            {"text": "trời", "start": 10.9, "end": 11.4},
        ]
        repaired, moved = repair_stretched_words(words)
        self.assertEqual(moved, 0)
        self.assertEqual(repaired[1]["start"], 10.9)

    def test_loosens_a_line_after_pulling_the_last_word_back(self):
        words = [
            {"text": "BÌNH", "start": 118.56, "end": 118.84},
            {"text": "NAM!", "start": 191.46, "end": 192.68},
        ]
        repaired, moved = repair_stretched_words(words)
        self.assertEqual(moved, 1)
        lines = [
            {"text": "điệp khúc", "start": repaired[0]["start"], "end": repaired[-1]["end"], "words": repaired, "repaired": True},
            {"text": "sau", "start": 192.68, "end": 195.0, "words": [{"text": "sau", "start": 192.68, "end": 195.0}]},
        ]
        loosened = loosen_against_next_line(lines)
        self.assertLess(loosened[0]["words"][1]["start"], 130)
        self.assertGreaterEqual(loosened[0]["words"][0]["end"] - loosened[0]["words"][0]["start"], 0.42)
        self.assertLess(loosened[0]["end"], 192.68)


class CueTests(unittest.TestCase):
    def test_constants_match_the_typescript_preview(self):
        source = Path("web/src/lib/cue.ts").read_text(encoding="utf-8")
        self.assertIn(f"export const LONG_GAP_SECONDS = {int(LONG_GAP)};", source)
        self.assertIn(f"export const COUNTDOWN_SECONDS = {int(COUNTDOWN)};", source)

    def test_countdown_only_after_a_long_gap(self):
        lines = [
            {"text": "đầu", "start": 46.0, "end": 50.0},
            {"text": "sau", "start": 120.0, "end": 124.0},
        ]
        self.assertEqual(cue_at(lines, 40.0)[0], "blank")
        mode, _line, number = cue_at(lines, 41.5)
        self.assertEqual(mode, "countdown")
        self.assertEqual(number, 5)
        self.assertEqual(cue_at(lines, 46.2)[0], "sing")
        self.assertEqual(cue_at(lines, 80.0)[0], "blank")
        self.assertEqual(cue_at(lines, 115.5)[0], "countdown")
        self.assertEqual(cue_at(lines, 120.1)[0], "sing")

    def test_shows_the_next_line_as_soon_as_the_previous_one_ends(self):
        lines = [
            {"text": "trước", "start": 86.0, "end": 89.72},
            {"text": "Tốt", "start": 90.02, "end": 92.9},
        ]
        mode, line, _number = cue_at(lines, 89.5)
        self.assertEqual(mode, "sing")
        self.assertEqual(line["text"], "trước")
        mode, line, _number = cue_at(lines, 89.8)
        self.assertEqual(mode, "sing")
        self.assertEqual(line["text"], "Tốt")

    def test_switches_as_soon_as_the_note_ends(self):
        lines = [
            {"text": "trước", "start": 48.0, "end": 52.24},
            {"text": "sau", "start": 54.06, "end": 57.0},
        ]
        mode, line, _number = cue_at(lines, 52.1)
        self.assertEqual(mode, "sing")
        self.assertEqual(line["text"], "trước")
        mode, line, _number = cue_at(lines, 52.3)
        self.assertEqual(mode, "sing")
        self.assertEqual(line["text"], "sau")


class VoiceFitTests(unittest.TestCase):
    def _tone(self, path: Path, spans: list[tuple[float, float]], seconds: float = 3.0) -> None:
        rate = 8000
        count = int(seconds * rate)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            frames = bytearray()
            for index in range(count):
                moment = index / rate
                loud = any(start <= moment < end for start, end in spans)
                sample = int(12000 * math.sin(2 * math.pi * 220 * moment)) if loud else 0
                frames += struct.pack("<h", sample)
            handle.writeframes(frames)

    def test_pulls_a_word_out_of_the_silence_after_the_phrase(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            self._tone(vocal, [(0.40, 1.30)])
            lines = [
                {
                    "text": "bình ngô đại cáo",
                    "start": 0.40,
                    "end": 2.60,
                    "words": [
                        {"text": "bình", "start": 0.40, "end": 2.05},
                        {"text": "ngô", "start": 2.05, "end": 2.20},
                        {"text": "đại", "start": 2.20, "end": 2.40},
                        {"text": "cáo", "start": 2.40, "end": 2.60},
                    ],
                }
            ]
            fitted = fit_lines_to_voice(lines, vocal)
            self.assertLess(fitted[0]["end"], 1.45)
            self.assertGreater(fitted[0]["words"][-1]["start"], 0.9)
            self.assertLess(fitted[0]["words"][0]["end"] - fitted[0]["words"][0]["start"], 0.5)

    def test_keeps_the_last_word_on_screen_while_the_note_is_held(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            self._tone(vocal, [(0.40, 1.30)])
            lines = [
                {
                    "text": "một hai",
                    "start": 0.40,
                    "end": 0.90,
                    "words": [
                        {"text": "một", "start": 0.40, "end": 0.70},
                        {"text": "hai", "start": 0.70, "end": 0.90},
                    ],
                }
            ]
            fitted = fit_lines_to_voice(lines, vocal)
            self.assertAlmostEqual(fitted[0]["words"][0]["start"], 0.40, delta=0.08)
            self.assertGreater(fitted[0]["end"], 1.15)
            self.assertLess(fitted[0]["end"], 1.45)


class SegmentMatchTests(unittest.TestCase):
    def test_marks_a_line_when_the_word_count_differs(self):
        class Word:
            def __init__(self, word, start, end):
                self.word = word
                self.start = start
                self.end = end

        class Segment:
            def __init__(self, words):
                self.words = words
                self.text = " ".join(word.word for word in words)
                self.start = words[0].start
                self.end = words[-1].end

        segment = Segment([Word("mot", 1.0, 1.8)])
        matched = _segments_match_lines(["một hai"], [segment])
        self.assertIsNotNone(matched)
        self.assertTrue(matched[0]["uncertain"])
        self.assertEqual([word["text"] for word in matched[0]["words"]], ["một", "hai"])
        self.assertEqual(matched[0]["words"][0]["start"], 1.0)
        self.assertEqual(matched[0]["words"][1]["end"], 1.8)


class AccessTests(unittest.TestCase):
    def tearDown(self):
        clear_grants()

    def test_rejects_a_path_outside_the_audio_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inside = root / "song.wav"
            inside.write_bytes(b"")
            outside = Path(tempfile.mkdtemp()) / "other.wav"
            outside.write_bytes(b"")
            previous = os.environ.get("KARAOKE_AUDIO_ROOT")
            os.environ["KARAOKE_AUDIO_ROOT"] = str(root)
            try:
                self.assertEqual(audio_root(), root)
                self.assertEqual(ensure_allowed(inside), inside.resolve())
                with self.assertRaises(AccessError):
                    ensure_allowed(outside)
                grant(outside)
                self.assertEqual(ensure_allowed(outside), outside.resolve())
            finally:
                clear_grants()
                if previous is None:
                    os.environ.pop("KARAOKE_AUDIO_ROOT", None)
                else:
                    os.environ["KARAOKE_AUDIO_ROOT"] = previous


if __name__ == "__main__":
    unittest.main()

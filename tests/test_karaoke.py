import os
import tempfile
import unittest
from pathlib import Path

from karaoke.access import AccessError, audio_root, clear_grants, ensure_allowed, grant
from karaoke.align import _segments_match_lines, repair_stretched_words
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

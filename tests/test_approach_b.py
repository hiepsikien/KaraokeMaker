import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from karaoke.approach_b import align_with_gemini_lines
from karaoke.errors import JobError
from karaoke.gemini_lines import GeminiLineError
from karaoke.pipeline import align_job, align_options


def tone(path: Path, spans: list[tuple[float, float]], seconds: float = 7.0) -> None:
    rate = 8000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(int(seconds * rate)):
            moment = index / rate
            loud = any(start <= moment < end for start, end in spans)
            sample = int(12000 * math.sin(2 * math.pi * 220 * moment)) if loud else 0
            frames += struct.pack("<h", sample)
        handle.writeframes(frames)


def words(items):
    return [{"text": text, "start": start, "end": end} for text, start, end in items]


class ApproachBTests(unittest.TestCase):
    def test_line_boundary_is_recomputed_from_words(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 2.0), (2.5, 4.0), (4.5, 6.0)])
            segments = [
                {"words": words([("Màn", 0.5, 0.9), ("sương", 0.9, 1.4), ("buông", 1.4, 2.0)])},
                {"words": words([("Anh", 4.5, 4.9), ("yêu", 4.9, 5.4), ("em", 5.4, 6.0)])},
            ]
            gemini = [
                {"text": "màn sương buông", "start": 0.1, "end": 2.5, "status": "matched", "confidence": 0.9, "reference_index": 0},
                {"text": "hò ơi hò khoan", "start": 2.5, "end": 4.0, "status": "extra", "confidence": 0.8},
                {"text": "anh yêu em", "start": 3.8, "end": 6.4, "status": "matched", "confidence": 0.9, "reference_index": 1},
            ]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", return_value=gemini
            ):
                lines = align_with_gemini_lines(
                    vocal,
                    "Màn sương buông\nAnh yêu em",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
            lyric = [line for line in lines if line.get("kind") != "nlv"]
            self.assertEqual([line["text"] for line in lyric], ["Màn sương buông", "Anh yêu em"])
            self.assertAlmostEqual(lyric[0]["start"], 0.5, delta=0.08)
            self.assertAlmostEqual(lyric[1]["start"], 4.5, delta=0.08)
            self.assertTrue(any(line.get("kind") == "nlv" for line in lines))

    def test_short_full_song_answer_triggers_chunked(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 1.0), (2.5, 3.0), (4.5, 5.0)])
            segments = [
                {
                    "words": words(
                        [("một", 0.5, 1.0), ("hai", 2.5, 3.0), ("ba", 4.5, 5.0)]
                    )
                }
            ]
            short = [
                {
                    "text": "một",
                    "start": 0.5,
                    "end": 1.0,
                    "status": "matched",
                    "confidence": 0.9,
                    "reference_index": 0,
                }
            ]
            full = [
                {"text": "một", "start": 0.5, "end": 1.0, "status": "matched", "confidence": 0.9, "reference_index": 0},
                {"text": "hai", "start": 2.5, "end": 3.0, "status": "matched", "confidence": 0.9, "reference_index": 1},
                {"text": "ba", "start": 4.5, "end": 5.0, "status": "matched", "confidence": 0.9, "reference_index": 2},
            ]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", return_value=short
            ), patch(
                "karaoke.approach_b.transcribe_lines_chunked", return_value=full
            ) as chunked:
                lines = align_with_gemini_lines(
                    vocal,
                    "một\nhai\nba",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
        self.assertTrue(chunked.called)
        self.assertEqual([line["text"] for line in lines], ["một", "hai", "ba"])

    def test_gemini_failure_falls_back_to_whisper(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 2.0)])
            segments = [{"words": words([("Màn", 0.5, 0.9), ("sương", 0.9, 1.4), ("buông", 1.4, 2.0)])}]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", side_effect=GeminiLineError("429")
            ):
                lines = align_with_gemini_lines(
                    vocal,
                    "Màn sương buông",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["text"], "Màn sương buông")
            self.assertAlmostEqual(lines[0]["start"], 0.5, delta=0.1)

    def test_garbage_extra_is_not_promoted_to_nlv(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 2.0)])
            segments = [{"words": words([("Màn", 0.5, 0.9), ("sương", 0.9, 1.4), ("buông", 1.4, 2.0)])}]
            gemini = [
                {"text": "màn sương buông", "start": 0.5, "end": 2.0, "status": "matched", "confidence": 0.9, "reference_index": 0},
                {"text": "H creepy iftsalay", "start": 2.5, "end": 3.9, "status": "extra", "confidence": 0.4, "kind": "nlv"},
            ]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", return_value=gemini
            ), patch(
                "karaoke.approach_b.transcribe_lines_chunked", return_value=gemini
            ):
                lines = align_with_gemini_lines(
                    vocal,
                    "Màn sương buông",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
        self.assertFalse(any(line.get("kind") == "nlv" for line in lines))
        self.assertEqual([line["text"] for line in lines], ["Màn sương buông"])

    def test_nlv_text_matches_the_saved_word_list(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.5, 2.0), (2.5, 4.0)])
            segments = [
                {
                    "words": words(
                        [
                            ("Màn", 0.5, 0.9),
                            ("sương", 0.9, 1.4),
                            ("buông", 1.4, 2.0),
                            ("hò", 2.5, 2.9),
                            ("ơi", 2.9, 3.9),
                        ]
                    )
                }
            ]
            gemini = [
                {"text": "màn sương buông", "start": 0.5, "end": 2.0, "status": "matched", "confidence": 0.9, "reference_index": 0},
                {"text": "hò ơi hò khoan", "start": 2.5, "end": 3.9, "status": "extra", "confidence": 0.8, "kind": "nlv"},
            ]
            with patch("karaoke.approach_b.transcribe_whisper", return_value=segments), patch(
                "karaoke.approach_b.transcribe_lines", return_value=gemini
            ), patch(
                "karaoke.approach_b.transcribe_lines_chunked", return_value=gemini
            ):
                lines = align_with_gemini_lines(
                    vocal,
                    "Màn sương buông",
                    "vi",
                    "small",
                    "cpu",
                    lambda message: None,
                )
        chant = [line for line in lines if line.get("kind") == "nlv"]
        self.assertEqual(len(chant), 1)
        self.assertEqual(chant[0]["text"], "hò ơi")
        self.assertEqual(chant[0]["text"], " ".join(word["text"] for word in chant[0]["words"]))

    def test_word_times_never_overlap_after_snap(self):
        from karaoke.approach_b import _words_for_line

        def snap(moment, *_args):
            return moment - 0.3 if moment >= 1.25 else moment

        heard = [
            {"text": "a", "start": 1.0, "end": 1.2},
            {"text": "b", "start": 1.3, "end": 1.5},
        ]
        with patch("karaoke.approach_b._snap_time", side_effect=snap):
            result = _words_for_line("a b", 0.5, 2.0, heard, [1.0] * 100, 0.03, 0.5)
        self.assertEqual(len(result), 2)
        self.assertGreaterEqual(result[1]["start"], result[0]["end"])


class AlignStrategyTests(unittest.TestCase):
    def test_align_options_validates_strategy_and_consent(self):
        options = align_options(
            {
                "align_strategy": "gemini_lines",
                "gemini_model": "gemini-3.8-flash",
                "google_audio_consent": "yes",
            }
        )
        self.assertEqual(options["align_strategy"], "gemini_lines")
        self.assertTrue(options["google_audio_consent"])

    def test_rejects_unknown_strategy(self):
        with self.assertRaises(JobError):
            align_options({"align_strategy": "magic"})

    def test_rejects_unknown_gemini_model_for_a_new_job(self):
        with self.assertRaises(JobError):
            align_options({"gemini_model": "gemini-old"})

    def test_old_project_model_is_sanitized_when_loaded(self):
        from karaoke.project import project_from_fields

        project = project_from_fields(
            {
                "title": "x",
                "artist": "y",
                "lyrics": "một",
                "align_strategy": "gemini_lines",
                "gemini_model": "gemini-old",
                "google_audio_consent": True,
                "audio_consent_receipt": {"model": "gemini-old", "vocal_hash": "sha256:x"},
            }
        )
        self.assertEqual(project["gemini_model"], "gemini-3.8-flash")
        self.assertTrue(project["google_audio_consent"])
        self.assertEqual(project["audio_consent_receipt"]["vocal_hash"], "sha256:x")

    def test_align_job_uses_gemini_when_consented(self):
        prepared = {
            "vocal_audio": "/tmp/vocal.wav",
            "lyrics": "một",
            "language": "vi",
            "model_name": "small",
            "device": "cpu",
            "align_strategy": "gemini_lines",
            "gemini_model": "gemini-3.8-flash",
            "google_audio_consent": True,
        }
        with patch("karaoke.pipeline.align_with_gemini_lines", return_value=[{"text": "B"}]) as gemini:
            self.assertEqual(align_job(prepared, lambda message: None), [{"text": "B"}])
            self.assertTrue(gemini.called)

    def test_align_job_falls_back_without_consent(self):
        prepared = {
            "vocal_audio": "/tmp/vocal.wav",
            "lyrics": "một",
            "language": "vi",
            "model_name": "small",
            "device": "cpu",
            "align_strategy": "gemini_lines",
            "gemini_model": "gemini-3.8-flash",
            "google_audio_consent": False,
        }
        with patch("karaoke.pipeline.align_with_gemini_lines") as gemini, patch(
            "karaoke.pipeline.align_lyrics", return_value=[{"text": "A"}]
        ) as whisper:
            self.assertEqual(align_job(prepared, lambda message: None), [{"text": "A"}])
            self.assertFalse(gemini.called)
            self.assertTrue(whisper.called)


if __name__ == "__main__":
    unittest.main()

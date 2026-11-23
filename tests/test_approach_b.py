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
                {"text": "anh yêu em", "start": 4.2, "end": 6.4, "status": "matched", "confidence": 0.9, "reference_index": 1},
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

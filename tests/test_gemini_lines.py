import json
import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from karaoke.gemini_lines import (
    GeminiLineError,
    _dedupe_lines,
    _normalize_lines,
    plan_chunks,
    transcribe_lines,
    transcribe_lines_chunked,
)


def tone(path: Path, spans, seconds: float = 40.0) -> None:
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


class GeminiLinesTests(unittest.TestCase):
    def test_plan_chunks_cuts_in_a_long_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.0, 18.0), (22.0, 40.0)])
            chunks = plan_chunks(
                vocal,
                target_seconds=20,
                max_seconds=30,
                min_seconds=8,
                min_silence=0.8,
            )
        self.assertGreaterEqual(len(chunks), 2)
        first_end = chunks[0][1]
        second_start = chunks[1][0]
        self.assertLess(first_end, 22.5)
        self.assertGreater(second_start, 17.5)
        # 1.5s total overlap around the cut, per the chunk policy.
        self.assertTrue(1.0 < abs(first_end - second_start) <= 2.0)

    def test_normalize_sorts_out_of_order_lines(self):
        raw = {
            "lines": [
                {"heard_text": "câu ba", "start": 9.0, "end": 10.0},
                {"heard_text": "câu một", "start": 1.0, "end": 2.0},
                {"heard_text": "câu hai", "start": 5.0, "end": 6.0},
            ]
        }
        lines = _normalize_lines(raw, 0.0, 20.0)
        self.assertEqual([line["text"] for line in lines], ["câu một", "câu hai", "câu ba"])
        self.assertEqual([line["start"] for line in lines], [1.0, 5.0, 9.0])

    def test_dedupe_keeps_different_lines_with_close_starts(self):
        lines = _dedupe_lines(
            [
                {"text": "anh yêu em", "start": 10.0, "end": 11.0},
                {"text": "em yêu anh", "start": 10.7, "end": 11.5},
            ]
        )
        self.assertEqual(len(lines), 2)

    def test_dedupe_merges_the_same_line_across_chunk_overlap(self):
        lines = _dedupe_lines(
            [
                {"text": "điệp khúc", "start": 10.0, "end": 11.0, "confidence": 0.7},
                {"text": "điệp khúc", "start": 10.5, "end": 11.5, "confidence": 0.9},
            ]
        )
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["confidence"], 0.9)

    def test_failed_chunk_is_reported_instead_of_silently_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.0, 5.0)], seconds=5.0)
            with patch(
                "karaoke.gemini_lines.plan_chunks", return_value=[(0.0, 2.5), (2.5, 5.0)]
            ), patch(
                "karaoke.gemini_lines.transcribe_lines",
                side_effect=[
                    [{"text": "a", "start": 0.0, "end": 1.0}],
                    GeminiLineError("429"),
                ],
            ):
                with self.assertRaises(GeminiLineError) as raised:
                    transcribe_lines_chunked(vocal, "a", use_cache=False)
        self.assertIn("1/2", str(raised.exception))
    def _response(self, lines):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps({"lines": lines}, ensure_ascii=False)}]
                    }
                }
            ]
        }

    def test_parses_offset_status_and_clamps(self):
        captured = {}

        def generate(model, payload, key):
            captured["model"] = model
            captured["payload"] = payload
            return self._response(
                [
                    {
                        "reference_index": 2,
                        "heard_text": "anh thương em",
                        "start": 0.5,
                        "end": 2.0,
                        "status": "omitted",
                        "confidence": 0.8,
                    }
                ]
            )

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), patch(
            "karaoke.gemini_lines._audio_clip", return_value=(b"audio", "audio/mpeg", 10.0)
        ), patch("karaoke.gemini_lines.probe_duration", return_value=30.0):
            lines = transcribe_lines(
                Path("vocal.mp3"),
                "[Intro]\nMàn sương\nAnh yêu em",
                start=10.0,
                end=20.0,
                use_cache=False,
                generate=generate,
            )
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["start"], 10.5)
        self.assertEqual(lines[0]["end"], 12.0)
        self.assertEqual(lines[0]["status"], "unsung")
        self.assertEqual(lines[0]["reference_index"], 2)
        self.assertIn("Màn sương", captured["payload"]["contents"][0]["parts"][0]["text"])

    def test_rejects_model_not_allowed_for_audio(self):
        with self.assertRaises(GeminiLineError):
            transcribe_lines(Path("vocal.mp3"), "", model="gemini-3.1-flash-lite")

    def test_empty_lines_are_dropped(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), patch(
            "karaoke.gemini_lines._audio_clip", return_value=(b"audio", "audio/mpeg", 0.0)
        ), patch("karaoke.gemini_lines.probe_duration", return_value=10.0):
            lines = transcribe_lines(
                Path("vocal.mp3"),
                "một câu",
                use_cache=False,
                generate=lambda *args: self._response(
                    [
                        {"heard_text": "  ", "start": 0, "end": 1},
                        {"heard_text": "câu thật", "start": 1, "end": 2},
                    ]
                ),
            )
        self.assertEqual([line["text"] for line in lines], ["câu thật"])

    def test_out_of_clip_response_is_retried(self):
        calls: list[int] = []

        def generate(model, payload, key):
            calls.append(1)
            if len(calls) == 1:
                return self._response(
                    [
                        {"heard_text": "sai", "start": 102.3, "end": 105.0},
                        {"heard_text": "sai nữa", "start": 110.0, "end": 115.0},
                    ]
                )
            return self._response([{"heard_text": "câu thật", "start": 1.0, "end": 2.0}])

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), patch(
            "karaoke.gemini_lines._audio_clip", return_value=(b"audio", "audio/mpeg", 0.0)
        ), patch("karaoke.gemini_lines.probe_duration", return_value=10.0):
            lines = transcribe_lines(
                Path("vocal.mp3"),
                "câu thật",
                use_cache=False,
                generate=generate,
            )
        self.assertEqual([line["text"] for line in lines], ["câu thật"])
        self.assertEqual(len(calls), 2)

    def test_invalid_cached_response_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "lines.json"
            cache.write_text(
                json.dumps(
                    {
                        "lines": [
                            {"heard_text": "sai", "start": 102.3, "end": 105.0},
                            {"heard_text": "sai nữa", "start": 110.0, "end": 115.0},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), patch(
                "karaoke.gemini_lines._audio_clip",
                return_value=(b"audio", "audio/mpeg", 0.0),
            ), patch("karaoke.gemini_lines.probe_duration", return_value=10.0), patch(
                "karaoke.gemini_lines._cache_path", return_value=cache
            ):
                lines = transcribe_lines(
                    Path("vocal.mp3"),
                    "câu thật",
                    use_cache=True,
                    generate=lambda *args: self._response(
                        [{"heard_text": "câu thật", "start": 1.0, "end": 2.0}]
                    ),
                )
        self.assertEqual([line["text"] for line in lines], ["câu thật"])


if __name__ == "__main__":
    unittest.main()

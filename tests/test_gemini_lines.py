import json
import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from karaoke.director import GeminiHTTPError
from karaoke.gemini_lines import (
    GeminiBudget,
    GeminiLineError,
    _post_generate_retry,
    _dedupe_lines,
    _normalize_lines,
    _timestamps_out_of_clip,
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

    def test_chunked_parallel_keeps_chunk_order(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.0, 6.0)], seconds=6.0)

            def fake(*_args, start, **_kwargs):
                return [{"text": f"c{int(start)}", "start": start, "end": start + 0.5}]

            with patch(
                "karaoke.gemini_lines.plan_chunks",
                return_value=[(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)],
            ), patch("karaoke.gemini_lines.transcribe_lines", side_effect=fake):
                lines, failures = transcribe_lines_chunked(
                    vocal, "x", use_cache=False, workers=3
                )
        self.assertEqual(failures, [])
        self.assertEqual([line["text"] for line in lines], ["c0", "c2", "c4"])

    def test_failed_chunk_is_reported_without_dropping_good_chunks(self):
        with tempfile.TemporaryDirectory() as folder:
            vocal = Path(folder) / "vocal.wav"
            tone(vocal, [(0.0, 5.0)], seconds=5.0)

            def fake(*_args, start, end, **_kwargs):
                if start == 0.0:
                    return [{"text": "a", "start": 0.0, "end": 1.0}]
                raise GeminiLineError("429")

            with patch(
                "karaoke.gemini_lines.plan_chunks", return_value=[(0.0, 2.5), (2.5, 5.0)]
            ), patch("karaoke.gemini_lines.transcribe_lines", side_effect=fake):
                lines, failures = transcribe_lines_chunked(
                    vocal, "a", use_cache=False, workers=1
                )
        self.assertEqual([line["text"] for line in lines], ["a"])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0], 1)
        self.assertIn("429", failures[0][1])
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

    def test_end_past_clip_is_out_of_clip(self):
        self.assertTrue(
            _timestamps_out_of_clip(
                {
                    "lines": [
                        {"heard_text": "a", "start": 1.0, "end": 500.0},
                        {"heard_text": "b", "start": 2.0, "end": 501.0},
                    ]
                },
                10.0,
            )
        )

    def test_zero_length_line_is_repaired_not_rejected(self):
        self.assertFalse(
            _timestamps_out_of_clip(
                {"lines": [{"heard_text": "a", "start": 1.0, "end": 1.0}]},
                10.0,
            )
        )

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

    def test_end_past_clip_response_is_retried(self):
        calls: list[int] = []

        def generate(model, payload, key):
            calls.append(1)
            if len(calls) == 1:
                return self._response(
                    [
                        {"heard_text": "sai", "start": 1.0, "end": 500.0},
                        {"heard_text": "sai nữa", "start": 2.0, "end": 501.0},
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

    def test_retry_uses_shared_budget_for_transient_errors(self):
        calls: list[int] = []

        def fake(*_args, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise GeminiHTTPError("429", status=429, transient=True)
            return {"lines": []}

        with patch("karaoke.gemini_lines._post_generate", side_effect=fake), patch(
            "karaoke.gemini_lines.time.sleep"
        ):
            result = _post_generate_retry(
                "m", {}, "key", budget=GeminiBudget(2), log=None
            )
        self.assertEqual(result, {"lines": []})
        self.assertEqual(len(calls), 2)

    def test_retry_does_not_repeat_non_transient_errors(self):
        calls: list[int] = []

        def fake(*_args, **_kwargs):
            calls.append(1)
            raise GeminiHTTPError("400", status=400, transient=False)

        with patch("karaoke.gemini_lines._post_generate", side_effect=fake):
            with self.assertRaises(GeminiHTTPError):
                _post_generate_retry("m", {}, "key", budget=GeminiBudget(4), log=None)
        self.assertEqual(len(calls), 1)

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

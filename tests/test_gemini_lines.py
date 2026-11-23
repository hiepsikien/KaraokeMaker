import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from karaoke.gemini_lines import GeminiLineError, transcribe_lines


class GeminiLinesTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

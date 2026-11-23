import unittest

from karaoke.line_match import match_lines, sim_text


def heard(text, start, end, confidence=0.9):
    return {"text": text, "start": start, "end": end, "confidence": confidence}


class LineMatchTests(unittest.TestCase):
    def test_low_similarity_is_unsung_not_forced(self):
        result = match_lines(
            ["Màn sương buông"],
            [heard("la la la", 1.0, 2.0)],
        )
        self.assertEqual(result["matched"], [])
        self.assertEqual(len(result["unsung"]), 1)
        self.assertTrue(result["extra"])

    def test_similar_line_matches(self):
        result = match_lines(
            ["Màn sương buông"],
            [heard("màn sương buông", 1.0, 2.4)],
        )
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["reference_index"], 0)
        self.assertGreater(result["matched"][0]["confidence"], 0)

    def test_whisper_second_opinion_can_rescue_gemini_text(self):
        result = match_lines(
            ["Màn sương buông"],
            [heard("nghe sai", 1.0, 2.4)],
            whisper_words=[
                {"text": "màn", "start": 1.0, "end": 1.4},
                {"text": "sương", "start": 1.4, "end": 1.8},
                {"text": "buông", "start": 1.8, "end": 2.4},
            ],
        )
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["reference_index"], 0)

    def test_repeat_is_separate_from_nlv(self):
        result = match_lines(
            ["Điệp khúc"],
            [
                heard("điệp khúc", 1.0, 2.5),
                heard("điệp khúc", 10.0, 11.5),
            ],
        )
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(len(result["repeat"]), 1)
        self.assertEqual(result["repeat"][0]["status"], "repeat")
        self.assertFalse(any(item.get("kind") == "nlv" for item in result["extra"]))

    def test_similarity_is_normalized(self):
        self.assertEqual(sim_text("Màn sương", "màn sương"), 1.0)
        self.assertGreater(sim_text("Màn sương", "màn sương"), sim_text("Màn sương", "hoàn toàn khác"))


if __name__ == "__main__":
    unittest.main()

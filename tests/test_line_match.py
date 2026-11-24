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

    def test_first_occurrence_is_matched_not_repeat(self):
        result = match_lines(
            ["Điệp khúc"],
            [
                heard("điệp khúc", 1.0, 2.5),
                heard("điệp khúc", 10.0, 11.5),
            ],
        )
        self.assertAlmostEqual(result["matched"][0]["start"], 1.0, delta=0.01)
        self.assertAlmostEqual(result["repeat"][0]["start"], 10.0, delta=0.01)

    def test_weak_pair_after_many_unsung_lines_stays_unsung(self):
        # Regression: the DP used to compare only the local match score against
        # the accumulated gap. After several unsung lines that made any weak
        # pair win and forced a line that was not sung.
        reference = [
            "zzz một",
            "zzz hai",
            "zzz ba",
            "zzz bốn",
            "zzz năm",
            "câu sáu cuối",
        ]
        result = match_lines(reference, [heard("câu", 1.0, 11.0)])
        self.assertEqual(result["matched"], [])
        self.assertEqual(len(result["unsung"]), len(reference))

    def test_split_fragment_is_merged_into_matched_line(self):
        result = match_lines(
            ["Văn Lang trường tồn... ngàn năm..."],
            [
                heard("văn lang trường tố", 245.82, 248.43),
                heard("Văn Lang trường tồn... ngàn năm...", 252.75, 255.21),
            ],
        )
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["extra"], [])
        self.assertAlmostEqual(result["matched"][0]["start"], 245.82, delta=0.1)
        self.assertAlmostEqual(result["matched"][0]["end"], 255.21, delta=0.1)

    def test_similarity_is_normalized(self):
        self.assertEqual(sim_text("Màn sương", "màn sương"), 1.0)
        self.assertGreater(sim_text("Màn sương", "màn sương"), sim_text("Màn sương", "hoàn toàn khác"))


if __name__ == "__main__":
    unittest.main()

import unittest

from karaoke.voiced import flat_clip, voiced_spans

HOP = 0.1


def levels_for(seconds: float, voiced: list[tuple[int, int]], loud: float = 500.0) -> list[float]:
    """Build per-hop RMS levels with loud samples at the given index ranges."""
    levels = [10.0] * int(round(seconds / HOP))
    for start, end in voiced:
        for index in range(start, end):
            levels[index] = loud
    return levels


class VoicedSpansTests(unittest.TestCase):
    def test_empty_levels_do_not_raise(self):
        self.assertEqual(voiced_spans([], HOP), [])
        spans = voiced_spans([500.0], HOP)
        self.assertEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0][0], 0.0)
        self.assertAlmostEqual(spans[0][1], HOP)

    def test_short_gap_is_merged(self):
        # 0.5-1.5 loud, one 0.1s quiet hop, then 1.6-2.6 loud.
        levels = levels_for(4.0, [(5, 15), (16, 26)])
        spans = voiced_spans(levels, HOP, pad=0.0)
        self.assertEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0][0], 0.5)
        self.assertAlmostEqual(spans[0][1], 2.6)

    def test_padding_overlap_merges_spans(self):
        # A 0.4s gap is wider than min_gap, but pad=0.3 closes it.
        levels = levels_for(4.0, [(5, 10), (14, 19)])
        spans = voiced_spans(levels, HOP)
        self.assertEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0][0], 0.2)
        self.assertAlmostEqual(spans[0][1], 2.2)

    def test_span_shorter_than_min_span_is_dropped(self):
        levels = levels_for(4.0, [(5, 7), (15, 25)])
        spans = voiced_spans(levels, HOP, pad=0.0)
        self.assertEqual(len(spans), 1)
        self.assertAlmostEqual(spans[0][0], 1.5)
        self.assertAlmostEqual(spans[0][1], 2.5)

    def test_mostly_silent_falls_back_to_full_file(self):
        levels = levels_for(10.0, [(5, 8)])  # 0.3s voiced of 10s = 3%
        self.assertEqual(voiced_spans(levels, HOP, pad=0.0), [(0.0, 10.0)])

    def test_almost_all_voiced_falls_back_to_full_file(self):
        levels = [500.0] * 100
        self.assertEqual(voiced_spans(levels, HOP), [(0.0, 10.0)])

    def test_long_span_is_split_at_lowest_rms_near_middle(self):
        levels = levels_for(20.0, [(20, 190)])
        levels[105] = 90.0  # quietest voiced point, exactly at 10.5s
        spans = voiced_spans(levels, HOP, pad=0.0, max_span=6.0)
        self.assertGreaterEqual(len(spans), 3)
        self.assertAlmostEqual(spans[0][0], 2.0)
        self.assertAlmostEqual(spans[-1][1], 19.0)
        self.assertTrue(any(abs(end - 10.5) < 0.11 for _, end in spans))
        for start, end in spans:
            self.assertLessEqual(end - start, 6.0 + 1e-9)
            self.assertGreaterEqual(end - start, 0.25 - 1e-9)


class FlatClipTests(unittest.TestCase):
    def test_flattens_spans(self):
        self.assertEqual(
            flat_clip([(1.0, 2.0), (3.0, 4.0)], 10.0),
            [1.0, 2.0, 3.0, 4.0],
        )

    def test_clamps_final_end_past_duration(self):
        flat = flat_clip([(1.0, 2.0), (8.0, 45.0)], 10.0)
        self.assertEqual(flat[:-1], [1.0, 2.0, 8.0])
        self.assertAlmostEqual(flat[-1], 9.9)

    def test_drops_invalid_pairs(self):
        self.assertEqual(
            flat_clip([(2.0, 2.0), (3.0, 1.0), (4.0, 5.0)], 10.0),
            [4.0, 5.0],
        )
        self.assertEqual(flat_clip([], 10.0), [])


if __name__ == "__main__":
    unittest.main()

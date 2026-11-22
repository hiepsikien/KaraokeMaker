import unittest

from PIL import Image

from karaoke.render import _draw_interlude, _fit_lyric_font_size, _frame_at, _waveform_rect
from karaoke.profiles import get_profile


class RenderTests(unittest.TestCase):
    def test_lyric_size_override_can_be_smaller(self):
        profile = get_profile("landscape_1080p")
        lines = [{"text": "hello", "start": 0, "end": 1, "words": [{"text": "hello", "start": 0, "end": 1}]}]
        size = _fit_lyric_font_size(lines, profile, base_size=24)
        self.assertLessEqual(size, 24)
        self.assertGreaterEqual(size, 12)

    def test_waveform_moves_above_lyric_strip(self):
        profile = get_profile("landscape_1080p")
        rect = _waveform_rect({"enabled": True, "x": 0.5, "y": 0.9, "width": 0.3, "height": 0.06}, profile)
        self.assertIsNotNone(rect)
        _x, y, _w, h = rect
        self.assertLessEqual(y + h, profile.strip_rect[1])

    def test_interlude_banner_is_not_blank(self):
        profile = get_profile("landscape_1080p")
        image = _draw_interlude(
            "Nhạc dạo",
            "Câu tiếp theo",
            True,
            profile,
            lyric_color=(255, 255, 255, 255),
            bar_color=(8, 6, 4, 196),
        )
        self.assertEqual(image.size, (profile.width, profile.strip_rect[3]))
        self.assertGreater(image.getbbox()[2], 0)

    def test_frame_at_returns_interlude_during_long_gap(self):
        profile = get_profile("landscape_1080p")
        blank = Image.new("RGBA", (profile.width, profile.strip_rect[3]), (0, 0, 0, 0))
        lines = [
            {"text": "a", "start": 0.0, "end": 1.0, "words": [{"text": "a", "start": 0.0, "end": 1.0}]},
            {"text": "b", "start": 20.0, "end": 21.0, "words": [{"text": "b", "start": 20.0, "end": 21.0}]},
        ]
        layouts = [
            {
                "white": Image.new("RGBA", (1, 1)),
                "gold": Image.new("RGBA", (1, 1)),
                "boxes": [],
                "words": line["words"],
                "start": line["start"],
                "end": line["end"],
            }
            for line in lines
        ]
        frame = _frame_at(
            layouts,
            10.0,
            blank,
            profile,
            interludes=[{"start": 5.0, "end": 15.0, "text": "Nhạc dạo", "next_line": "", "show_next_line": False}],
            lyric_color=(255, 255, 255, 255),
            sung_color=(255, 200, 60, 255),
            bar_color=(8, 6, 4, 196),
        )
        self.assertGreater(frame.getbbox()[2], 0)


if __name__ == "__main__":
    unittest.main()

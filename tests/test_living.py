import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from karaoke import living


def make_source(path: Path, size=(640, 360)) -> Path:
    image = Image.new("RGB", size, (30, 60, 90))
    for x in range(0, size[0], 16):
        for y in range(0, size[1], 16):
            image.putpixel((x, y), (120, 140, 160))
    image.save(path)
    return path


def base_plan() -> dict:
    return {
        "version": 1,
        "image_hash": "sha256:" + "c" * 64,
        "profile_id": "landscape_1080p",
        "fit_transform": {
            "mode": "contain",
            "source_width": 640,
            "source_height": 360,
            "frame_width": 1920,
            "frame_height": 1080,
            "scale": 3.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
        },
        "regions": [
            {
                "id": "outdoor",
                "polygon": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 1.0, "y": 0.0},
                    {"x": 1.0, "y": 1.0},
                    {"x": 0.0, "y": 1.0},
                ],
            },
            {
                "id": "dry",
                "polygon": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 0.5, "y": 0.0},
                    {"x": 0.5, "y": 1.0},
                    {"x": 0.0, "y": 1.0},
                ],
            },
        ],
        "protect": [
            {"label": "face", "box": {"x1": 0.4, "y1": 0.4, "x2": 0.6, "y2": 0.6}}
        ],
        "items": [
            {
                "effect": "rain",
                "target_ids": [],
                "params": {"intensity": 0.5, "loop_duration": 8, "audio": False},
            }
        ],
    }


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,pix_fmt",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class MaskTests(unittest.TestCase):
    def test_dry_protect_and_strip_are_zero_but_outdoor_is_visible(self):
        plan = base_plan()
        transform = living._fit_transform(plan, (640, 360))
        mask = living._build_mask(plan, transform, 640, 360)

        self.assertEqual(mask.shape, (360, 640))
        self.assertIsInstance(mask, np.ndarray)

        # Left half is the dry region.
        self.assertEqual(float(mask[108, 160]), 0.0)
        # Face protect box.
        self.assertEqual(float(mask[180, 320]), 0.0)
        # Lyric strip (bottom 280 px at 1080p -> ~93 px at 360p).
        self.assertEqual(float(mask[342, 512]), 0.0)
        # Allowed outdoor area.
        self.assertGreater(float(mask[108, 480]), 0.0)


class RainLoopTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.image = make_source(self.root / "scene.png")
        self.cache = self.root / "cache"

    def tearDown(self):
        self.folder.cleanup()

    def test_render_writes_a_yuv420p_loop_with_the_requested_shape(self):
        destination = self.root / "rain.mp4"
        with patch.object(living, "CACHE_ROOT", self.cache):
            result = living.render_rain_loop(
                self.image,
                base_plan(),
                destination,
                duration=1.0,
                width=640,
                height=360,
                fps=15,
            )

        self.assertIsNotNone(result)
        self.assertTrue(Path(result).is_file())
        self.assertTrue(destination.is_file())
        self.assertGreater(destination.stat().st_size, 0)

        facts = probe(destination)
        stream = facts["streams"][0]
        self.assertEqual(stream["width"], 640)
        self.assertEqual(stream["height"], 360)
        self.assertEqual(stream["pix_fmt"], "yuv420p")
        self.assertEqual(stream["r_frame_rate"], "15/1")
        self.assertAlmostEqual(float(facts["format"]["duration"]), 1.0, delta=0.15)

    def test_a_plan_without_rain_returns_none(self):
        plan = base_plan()
        plan["items"] = [{"effect": "steam", "target_ids": [], "params": {}}]
        destination = self.root / "steam.mp4"
        with patch.object(living, "CACHE_ROOT", self.cache):
            result = living.render_rain_loop(
                self.image,
                plan,
                destination,
                duration=1.0,
                width=640,
                height=360,
                fps=15,
            )
        self.assertIsNone(result)
        self.assertFalse(destination.exists())

    def test_invalid_duration_and_width_raise_value_error(self):
        destination = self.root / "bad.mp4"
        with patch.object(living, "CACHE_ROOT", self.cache):
            with self.assertRaises(ValueError):
                living.render_rain_loop(
                    self.image,
                    base_plan(),
                    destination,
                    duration=0.0,
                    width=640,
                    height=360,
                    fps=15,
                )
            with self.assertRaises(ValueError):
                living.render_rain_loop(
                    self.image,
                    base_plan(),
                    destination,
                    duration=-1.0,
                    width=640,
                    height=360,
                    fps=15,
                )
            with self.assertRaises(ValueError):
                living.render_rain_loop(
                    self.image,
                    base_plan(),
                    destination,
                    duration=1.0,
                    width=641,
                    height=360,
                    fps=15,
                )


if __name__ == "__main__":
    unittest.main()

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
        plan = base_plan()
        plan["image_hash"] = living._file_hash(self.image)
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

    def test_changed_image_contents_skip_the_stale_plan(self):
        destination = self.root / "stale.mp4"
        plan = base_plan()
        plan["image_hash"] = "sha256:" + "f" * 64
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


def object_entry(
    object_id: str,
    label: str,
    box: tuple[float, float, float, float],
    *,
    affordances: list[str] | None = None,
    pivot: tuple[float, float] | None = None,
    opening: tuple[float, float] | None = None,
) -> dict:
    return {
        "id": object_id,
        "label": label,
        "box": {"x1": box[0], "y1": box[1], "x2": box[2], "y2": box[3]},
        "anchors": {
            "pivot": {"x": pivot[0], "y": pivot[1]} if pivot else None,
            "opening": {"x": opening[0], "y": opening[1]} if opening else None,
        },
        "affordances": affordances or [],
    }


def effect_plan(items: list[dict], **extra) -> dict:
    plan = {
        "version": 1,
        "image_hash": "sha256:" + "c" * 64,
        "fit_transform": {
            "mode": "contain",
            "source_width": 100,
            "source_height": 100,
            "frame_width": 100,
            "frame_height": 100,
            "scale": 1.0,
            "offset_x": 0.0,
            "offset_y": 0.0,
        },
        "objects": [],
        "regions": [],
        "protect": [],
        "items": items,
    }
    plan.update(extra)
    return plan


class WaterMotionTests(unittest.TestCase):
    def test_liquid_region_is_kept_when_items_target_an_object(self):
        region = [
            (0.0, 0.5),
            (1.0, 0.5),
            (1.0, 1.0),
            (0.0, 1.0),
        ]
        plan = effect_plan(
            [{"effect": "water", "target_ids": ["sea-1"], "params": {"intensity": 0.55}}],
            regions=[
                {
                    "id": "liquid",
                    "polygon": [{"x": x, "y": y} for x, y in region],
                }
            ],
            objects=[
                object_entry("sea-1", "liquid surface", (0.0, 0.45, 1.0, 1.0))
            ],
        )
        shapes = living._water_shapes(plan, {"sea-1"})
        self.assertEqual(len(shapes), 1)
        self.assertEqual([(p[0], p[1]) for p in shapes[0]], region)

    def test_water_shimmer_moves_dark_foreground_rows(self):
        plan = effect_plan(
            [{"effect": "water", "target_ids": [], "params": {"intensity": 0.55}}],
            regions=[
                {
                    "id": "liquid",
                    "polygon": [
                        {"x": 0.0, "y": 0.5},
                        {"x": 1.0, "y": 0.5},
                        {"x": 1.0, "y": 1.0},
                        {"x": 0.0, "y": 1.0},
                    ],
                }
            ],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        prevent = np.ones((100, 100), dtype=np.float32)
        spec = living._water_spec(plan, transform, 100, 100, 4.0, prevent, lambda _m: None)
        self.assertIsNotNone(spec)

        # A vertical gradient makes the per-column vertical bob measurable even
        # in the dark foreground water.
        base = np.zeros((100, 100, 3), dtype=np.float32)
        base[50:, :, :] = np.arange(50, 100, dtype=np.float32)[:, None, None] * 2.0
        first = living._apply_water(base.copy(), spec, 0.0)
        second = living._apply_water(base.copy(), spec, 0.5)
        difference = np.abs(second[60:, :] - first[60:, :]).mean()
        self.assertGreater(float(difference), 0.5)

    def test_water_mask_does_not_warp_objects_standing_in_it(self):
        plan = effect_plan(
            [{"effect": "water", "target_ids": [], "params": {"intensity": 0.55}}],
            regions=[
                {
                    "id": "liquid",
                    "polygon": [
                        {"x": 0.0, "y": 0.5},
                        {"x": 1.0, "y": 0.5},
                        {"x": 1.0, "y": 1.0},
                        {"x": 0.0, "y": 1.0},
                    ],
                }
            ],
            objects=[
                object_entry("boat-1", "boat", (0.2, 0.6, 0.4, 0.8)),
            ],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        prevent = np.ones((100, 100), dtype=np.float32)
        mask = living._build_water_mask(plan, transform, 100, 100, prevent)
        self.assertIsNotNone(mask)
        # Inside the boat: no shimmer. Away from it: shimmer is allowed.
        self.assertEqual(float(mask[70, 30]), 0.0)
        self.assertGreater(float(mask[90, 80]), 0.0)


class SmokeSourceTests(unittest.TestCase):
    def sources(self, objects: list[dict], base_value: float = 60.0):
        plan = effect_plan(
            [{"effect": "steam", "target_ids": [], "params": {"intensity": 0.55}}],
            objects=objects,
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        base = np.full((100, 100, 3), base_value, dtype=np.float32)
        return living._steam_sources(
            plan, transform, 100, 100, 4.0, 1234, base, lambda _m: None
        )

    def test_every_smoke_box_is_an_emitter_even_without_an_opening(self):
        sources = self.sources(
            [
                object_entry("smoke-1", "smoke", (0.1, 0.1, 0.3, 0.45)),
                object_entry("smoke-2", "smoke plume", (0.6, 0.1, 0.8, 0.45)),
            ]
        )
        self.assertEqual(len(sources), 2)
        self.assertTrue(all(source["smoke"] for source in sources))

    def test_smoke_wisps_are_not_clipped_by_object_rectangles(self):
        plan = effect_plan(
            [{"effect": "steam", "target_ids": [], "params": {"intensity": 0.55}}],
            objects=[
                object_entry("smoke-1", "smoke", (0.1, 0.1, 0.6, 0.7)),
                object_entry("boat-1", "boat", (0.25, 0.3, 0.4, 0.5)),
            ],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        base = np.full((100, 100, 3), 70.0, dtype=np.float32)
        sources = living._steam_sources(
            plan, transform, 100, 100, 4.0, 1234, base, lambda _m: None
        )
        self.assertEqual(len(sources), 1)
        # No painted-smoke warp: it swirled in place instead of rising.
        self.assertIsNone(sources[0].get("warp"))
        # No rectangular occluder holes that would show as a visible box.
        self.assertEqual(float(sources[0]["mask"].min()), 1.0)
        self.assertEqual(float(sources[0]["mask"][40, 32]), 1.0)

    def test_sky_backdrop_does_not_zero_the_smoke_mask(self):
        plan = effect_plan(
            [{"effect": "steam", "target_ids": [], "params": {"intensity": 0.55}}],
            objects=[
                object_entry("smoke-1", "smoke", (0.1, 0.1, 0.5, 0.6)),
                object_entry("sky-1", "sky backdrop", (0.0, 0.0, 1.0, 0.55)),
            ],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        base = np.full((100, 100, 3), 70.0, dtype=np.float32)
        sources = living._steam_sources(
            plan, transform, 100, 100, 4.0, 1234, base, lambda _m: None
        )
        self.assertEqual(len(sources), 1)
        self.assertGreater(float(sources[0]["mask"].max()), 0.0)
        self.assertIsNone(sources[0].get("warp"))

    def test_fire_is_only_a_fallback_when_no_smoke_object_covers_it(self):
        covered = self.sources(
            [
                object_entry("smoke-1", "smoke", (0.1, 0.1, 0.7, 0.7)),
                object_entry("fire-1", "fire", (0.5, 0.5, 0.7, 0.7), affordances=["fire"]),
            ]
        )
        self.assertEqual(len(covered), 1)
        self.assertEqual(covered[0]["cx"], 40.0)

        separate_fire = self.sources(
            [
                object_entry("smoke-1", "smoke", (0.1, 0.1, 0.3, 0.45)),
                object_entry("fire-1", "fire", (0.5, 0.5, 0.7, 0.7), affordances=["fire"]),
            ]
        )
        self.assertEqual(len(separate_fire), 2)

        without_smoke = self.sources(
            [
                object_entry("fire-1", "fire", (0.5, 0.5, 0.7, 0.7), affordances=["fire"]),
            ]
        )
        self.assertEqual(len(without_smoke), 1)
        self.assertTrue(without_smoke[0]["smoke"])


class SelectedObjectFilterTests(unittest.TestCase):
    def test_plan_selection_filters_effect_objects(self):
        plan = effect_plan(
            [{"effect": "wind", "target_ids": [], "params": {"intensity": 0.35}}],
            objects=[
                object_entry("flag-1", "flag", (0.6, 0.05, 0.9, 0.3)),
                object_entry("pot-1", "pot", (0.1, 0.1, 0.3, 0.3)),
            ],
            selected_object_ids=["flag-1"],
        )
        self.assertEqual([item["id"] for item in living._plan_objects(plan)], ["flag-1"])

        plan["selected_object_ids"] = []
        self.assertEqual(living._plan_objects(plan), [])


class WindFlagTests(unittest.TestCase):
    def test_flag_is_treated_as_cloth_and_moves_around_its_pivot(self):
        plan = effect_plan(
            [{"effect": "wind", "target_ids": ["flag-1"], "params": {"intensity": 0.35}}],
            objects=[
                object_entry("flag-1", "flag", (0.6, 0.05, 0.9, 0.3), pivot=(0.6, 0.05))
            ],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        prevent = np.ones((100, 100), dtype=np.float32)
        specs = living._wind_specs(
            plan, transform, 100, 100, 4.0, 1234, prevent, lambda _m: None
        )
        self.assertEqual(len(specs), 1)
        self.assertTrue(specs[0]["cloth"])
        self.assertGreater(specs[0]["amplitude"], 1.0)

        image = Image.new("RGB", (100, 100), (20, 20, 20))
        for x in range(60, 91):
            for y in range(5, 31):
                image.putpixel((x, y), (200, 80, 40))
        first = np.asarray(living._wind_frame(image.copy(), specs, 0.0), dtype=np.float32)
        second = np.asarray(living._wind_frame(image.copy(), specs, 0.25), dtype=np.float32)
        self.assertGreater(float(np.abs(second - first).max()), 0.0)

    def test_protected_flag_sways_rigidly_instead_of_being_skipped(self):
        plan = effect_plan(
            [{"effect": "wind", "target_ids": ["flag-1"], "params": {"intensity": 0.35}}],
            objects=[
                object_entry("flag-1", "flag", (0.6, 0.05, 0.9, 0.3), pivot=(0.6, 0.05))
            ],
            protect=[
                {
                    "label": "calligraphy",
                    "box": {"x1": 0.6, "y1": 0.05, "x2": 0.9, "y2": 0.3},
                }
            ],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        prevent = living._build_protect_mask(plan, transform, 100, 100, (0, 100, 0, 0))
        self.assertEqual(float(prevent[10, 70]), 0.0)
        specs = living._wind_specs(
            plan, transform, 100, 100, 4.0, 1234, prevent, lambda _m: None
        )
        self.assertEqual(len(specs), 1)
        self.assertTrue(specs[0]["rigid"])
        self.assertGreater(float(specs[0]["mask"].max()), 0.0)

        image = Image.new("RGB", (100, 100), (20, 20, 20))
        for x in range(60, 91):
            for y in range(5, 31):
                image.putpixel((x, y), (200, 80, 40))
        first = np.asarray(living._wind_frame(image.copy(), specs, 0.0), dtype=np.float32)
        second = np.asarray(living._wind_frame(image.copy(), specs, 0.25), dtype=np.float32)
        self.assertGreater(float(np.abs(second - first).max()), 0.0)


class EmbersTests(unittest.TestCase):
    def test_embers_use_fire_object_and_rise(self):
        plan = effect_plan(
            [
                {
                    "effect": "embers",
                    "target_ids": [],
                    "params": {"intensity": 0.6, "loop_duration": 4, "audio": False},
                }
            ],
            objects=[
                object_entry(
                    "fire-1",
                    "fire",
                    (0.4, 0.5, 0.6, 0.8),
                    affordances=["burn"],
                )
            ],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        prevent = np.ones((100, 100), dtype=np.float32)
        spec = living._embers_spec(
            plan, transform, 100, 100, 4.0, prevent, 1234, lambda _m: None
        )
        self.assertIsNotNone(spec)
        self.assertGreater(len(spec["particles"]), 0)
        first = living._apply_embers(np.zeros((100, 100, 3), np.float32), spec, 0.1, {})
        second = living._apply_embers(np.zeros((100, 100, 3), np.float32), spec, 0.6, {})
        self.assertGreater(float(first.max()), 0.0)
        self.assertGreater(float(np.abs(second - first).max()), 0.0)


class BirdsTests(unittest.TestCase):
    def test_birds_are_gated_by_the_prompt_effect_item(self):
        plan = effect_plan(
            [],
            objects=[object_entry("flock-1", "bird flock", (0.1, 0.1, 0.4, 0.25))],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        specs = living._birds_spec(
            plan, transform, 100, 100, 4.0, None, 1234, lambda _m: None
        )
        self.assertEqual(specs, [])

    def test_birds_drift_moves_only_the_flock_patch(self):
        plan = effect_plan(
            [
                {
                    "effect": "birds",
                    "target_ids": [],
                    "params": {"intensity": 0.35, "loop_duration": 4, "audio": False},
                }
            ],
            objects=[object_entry("flock-1", "bird flock", (0.35, 0.2, 0.65, 0.4))],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        specs = living._birds_spec(
            plan, transform, 100, 100, 4.0, None, 1234, lambda _m: None
        )
        self.assertEqual(len(specs), 1)

        frame = np.full((100, 100, 3), 180.0, dtype=np.float32)
        frame[28:34, 42:58] = 20.0  # bird silhouettes on a bright sky
        first = living._apply_birds(frame.copy(), specs, 0.0)
        second = living._apply_birds(frame.copy(), specs, 0.25)
        self.assertGreater(float(np.abs(second - first).max()), 0.0)
        # Pixels far from the flock stay untouched.
        self.assertTrue(np.allclose(second[90, 5], frame[90, 5]))


class SunRaysTests(unittest.TestCase):
    def test_sunrays_are_gated_by_the_prompt_effect_item(self):
        base = np.full((100, 100, 3), 40.0, dtype=np.float32)
        plan = effect_plan(
            [],
            objects=[object_entry("sun-1", "sun", (0.6, 0.05, 0.8, 0.25))],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        spec = living._sunrays_spec(
            plan, transform, base, 100, 100, 4.0, None, 1234, lambda _m: None
        )
        self.assertIsNone(spec)

    def test_sunrays_anchor_on_sun_and_brighten_nearby_sky(self):
        base = np.full((100, 100, 3), 40.0, dtype=np.float32)
        plan = effect_plan(
            [
                {
                    "effect": "sunrays",
                    "target_ids": [],
                    "params": {"intensity": 0.4, "loop_duration": 4, "audio": False},
                }
            ],
            objects=[object_entry("sun-1", "sun", (0.6, 0.05, 0.8, 0.25))],
        )
        transform = living._fit_transform(plan, (100, 100), (100, 100), "contain")
        prevent = np.ones((100, 100), dtype=np.float32)
        spec = living._sunrays_spec(
            plan, transform, base, 100, 100, 4.0, prevent, 1234, lambda _m: None
        )
        self.assertIsNotNone(spec)
        first = living._apply_sunrays(base.copy(), spec, 0.0)
        second = living._apply_sunrays(base.copy(), spec, 0.25)
        self.assertGreater(float(np.abs(second - first).max()), 0.0)
        self.assertGreater(float(first[15:25, 65:75].max()), 40.0)
        # Far corners are outside the ray footprint and stay unchanged.
        self.assertTrue(np.allclose(second[95, 5], 40.0))


if __name__ == "__main__":
    unittest.main()

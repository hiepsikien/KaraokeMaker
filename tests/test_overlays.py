"""Unit tests for the manual whole-video overlays (no Gemini, no network)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from karaoke import overlays, particles


@dataclass
class TinyProfile:
    id: str = "test_320"
    width: int = 320
    height: int = 180
    fps: int = 10


def make_video(path: Path, *, seconds: float = 1.0, width: int = 320, height: int = 180, fps: int = 10) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x203040:s={width}x{height}:r={fps}:d={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def decode(path: Path) -> np.ndarray:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    )
    facts = overlays.probe_video(path)
    width = int(facts["width"])
    height = int(facts["height"])
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape((-1, height, width, 3))


def spec(kind: str, *, seed: int = 7, mask: str = "none", **params) -> dict:
    return {
        "id": f"ovl_{kind}_1",
        "kind": kind,
        "enabled": True,
        "params": params,
        "mask": mask,
        "seed": seed,
    }


class OverlayNoopTests(unittest.TestCase):
    def test_no_enabled_effect_returns_base_without_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = make_video(root / "base.mp4")
            destination = root / "background.mp4"
            disabled = [{**spec("snow"), "enabled": False}]
            path, identity, degraded = overlays.apply_overlays(
                base, disabled, TinyProfile(), (0, 100, 320, 80), destination, lambda _m: None
            )
            self.assertEqual(path, base)
            self.assertFalse(degraded)
            self.assertFalse(destination.exists())
            self.assertEqual(len(identity), 64)

    def test_hash_changes_with_effect_seed_and_strip(self):
        snow = spec("snow", seed=1)
        first = overlays.overlay_hash([snow], profile=TinyProfile(), strip_rect=(0, 0, 0, 0))
        second = overlays.overlay_hash([spec("snow", seed=2)], profile=TinyProfile(), strip_rect=(0, 0, 0, 0))
        third = overlays.overlay_hash([snow], profile=TinyProfile(), strip_rect=(0, 100, 320, 80))
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(
            first,
            overlays.overlay_hash([snow], profile=TinyProfile(), strip_rect=(0, 0, 0, 0)),
        )


class OverlayRenderTests(unittest.TestCase):
    def test_snow_leaf_bokeh_and_leak_change_the_base_video(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = make_video(root / "base.mp4", seconds=1.0)
            specs = [
                spec("bokeh"),
                spec("lightleak"),
                spec("snow"),
                spec("leaf", palette="autumn"),
            ]
            destination = root / "background.mp4"
            calls: list[tuple[int, int]] = []
            path, identity, degraded = overlays.apply_overlays(
                base,
                specs,
                TinyProfile(),
                None,
                destination,
                lambda _m: None,
                progress=lambda done, total: calls.append((done, total)),
            )
            self.assertFalse(degraded)
            self.assertEqual(path, destination)
            self.assertTrue(destination.is_file())
            self.assertTrue(calls)
            self.assertEqual(calls[-1][0], calls[-1][1])
            facts = overlays.probe_video(destination)
            self.assertEqual((facts["width"], facts["height"]), (320, 180))
            self.assertAlmostEqual(float(facts["duration"]), 1.0, delta=0.2)
            before = decode(base)
            after = decode(destination)
            self.assertEqual(before.shape, after.shape)
            self.assertGreater(float(np.abs(after.astype(int) - before.astype(int)).mean()), 0.5)

    def test_avoid_lyrics_leaves_the_strip_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = make_video(root / "base.mp4", seconds=1.0)
            destination = root / "snow.mp4"
            _path, _identity, degraded = overlays.apply_overlays(
                base,
                [spec("snow", mask="avoid_lyrics")],
                TinyProfile(),
                (0, 100, 320, 80),
                destination,
                lambda _m: None,
            )
            self.assertFalse(degraded)
            before = decode(base)
            after = decode(destination)
            # The strip starts at y=100: nothing should land there.
            strip_difference = float(
                np.abs(after[:, 105:175].astype(int) - before[:, 105:175].astype(int)).mean()
            )
            self.assertLess(strip_difference, 0.05)
            upper_difference = float(
                np.abs(after[:, :90].astype(int) - before[:, :90].astype(int)).mean()
            )
            self.assertGreater(upper_difference, 0.05)

    def test_mask_none_draws_over_the_lyric_strip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = make_video(root / "base.mp4", seconds=1.0)
            destination = root / "snow.mp4"
            _path, _identity, degraded = overlays.apply_overlays(
                base,
                [spec("snow", mask="none")],
                TinyProfile(),
                (0, 100, 320, 80),
                destination,
                lambda _m: None,
            )
            self.assertFalse(degraded)
            before = decode(base)
            after = decode(destination)
            # Snow is random, but with the default density some flakes land in
            # the strip over a one-second clip; the mask-off path must allow it.
            self.assertGreater(
                float(np.abs(after[:, 110:].astype(int) - before[:, 110:].astype(int)).mean()),
                0.01,
            )

    def test_failed_base_degrades_to_the_base_video(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / "missing.mp4"
            destination = root / "background.mp4"
            path, identity, degraded = overlays.apply_overlays(
                base, [spec("snow")], TinyProfile(), None, destination, lambda _m: None
            )
            self.assertTrue(degraded)
            self.assertEqual(path, base)
            self.assertEqual(len(identity), 64)


class LoopSafetyTests(unittest.TestCase):
    def test_bokeh_and_leaf_frames_match_at_the_loop_seam(self):
        bokeh = spec("bokeh", seed=11)
        layers = overlays._bokeh_layers(160, 90, 2.0, 11, bokeh)
        colors = overlays.resolve_palette("warm")
        first = np.full((90, 160, 3), 20.0, dtype=np.float32)
        seam = np.full((90, 160, 3), 20.0, dtype=np.float32)
        overlays._apply_bokeh(first, layers, bokeh, colors, 0.0, 0.0, None)
        overlays._apply_bokeh(seam, layers, bokeh, colors, 2.0, 1.0, None)
        self.assertTrue(np.allclose(first, seam, atol=1e-3))

        leaf_spec = spec("leaf", seed=12, palette="autumn")
        leaf_layers = particles.build_leaf_layers(160, 90, 2.0, 12, palette="autumn")
        first = np.full((90, 160, 3), 20.0, dtype=np.float32)
        seam = np.full((90, 160, 3), 20.0, dtype=np.float32)
        overlays._apply_leaves(first, leaf_layers, leaf_spec, 160, 90, 2.0, 0.0, 0.0, None)
        overlays._apply_leaves(seam, leaf_layers, leaf_spec, 160, 90, 2.0, 2.0, 1.0, None)
        self.assertTrue(np.allclose(first, seam, atol=1e-3))


class PaletteTests(unittest.TestCase):
    def test_presets_and_auto_palette_are_stable(self):
        self.assertEqual(len(overlays.resolve_palette("neon")), 4)
        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        frame[:, :16] = (220, 40, 60)
        frame[:, 16:] = (40, 160, 220)
        first = overlays.resolve_palette("auto", frame)
        second = overlays.resolve_palette("auto", frame)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)


class ParticleSpriteTests(unittest.TestCase):
    def test_snow_layers_have_dot_and_crystal_sprites_and_loop(self):
        layers = particles.build_snow_layers(320, 180, 4.0, 1234)
        self.assertTrue(layers)
        kinds = np.concatenate([layer["kinds"] for layer in layers])
        self.assertGreater(int((kinds == 0).sum()), 0)
        self.assertGreater(int((kinds == 1).sum()), 0)
        # Whole number of span cycles -> seam matches the first frame.
        first = particles.snow_layer_canvas(layers[-1], 320, 180, 0.0, 0.0)
        seam = particles.snow_layer_canvas(layers[-1], 320, 180, 4.0, 1.0)
        self.assertTrue(np.allclose(first, seam, atol=1e-2))

    def test_leaf_sprites_carry_palette_colour_and_rotate(self):
        sprite = particles.leaf_sprite(0, 24, 0.0, False, (201, 162, 39))
        self.assertEqual(sprite.shape[2], 4)
        alpha = sprite[:, :, 3]
        self.assertGreater(int(alpha.max()), 0)
        colours = sprite[:, :, :3][alpha > 200]
        self.assertGreater(len(colours), 0)
        # Autumn yellow-ish leaf: red channel dominates blue.
        self.assertGreater(float(colours[:, 0].mean()), float(colours[:, 2].mean()))


if __name__ == "__main__":
    unittest.main()

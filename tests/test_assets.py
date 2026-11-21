import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from karaoke.assets import (
    AssetError,
    delete_asset,
    list_assets,
    load_asset,
    save_asset_from_preview,
    validate_asset,
)
from karaoke.background import build_background, background_status, cleanup_backgrounds
from karaoke.living import _file_hash, MOTION_CACHE_VERSION
from karaoke.profiles import get_profile


def small_profile():
    return replace(
        get_profile("landscape_1080p"),
        width=320,
        height=180,
        fps=10,
        strip_rect=(0, 120, 320, 60),
        cover_title_size=24,
        cover_artist_size=16,
        cover_line_size=12,
        cover_max_width=280,
    )


def make_image(path: Path, color=(30, 60, 90), size=(320, 180)) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def make_audio(path: Path, seconds: float = 1.0) -> Path:
    import struct
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"".join(struct.pack("<h", 0) for _ in range(int(seconds * 8000))))
    return path


class AssetStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.image = make_image(self.root / "bg.png")
        self.preview = self.root / "preview.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=320x180:d=2:r=10",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(self.preview),
            ],
            check=True,
            capture_output=True,
        )
        self.profile = small_profile()
        self.env = patch.dict(os.environ, {"KARAOKE_DATA_DIR": str(self.root / "data")})
        self.env.start()
        self.preview_patch = patch("karaoke.assets.cache_path", return_value=self.preview)
        self.preview_patch.start()
        self.profile_patch = patch("karaoke.assets.get_profile", return_value=self.profile)
        self.profile_patch.start()
        self.preview_id_patch = patch("karaoke.assets.preview_id_for", return_value="a" * 64)
        self.preview_id_patch.start()

    def tearDown(self):
        self.preview_id_patch.stop()
        self.profile_patch.stop()
        self.preview_patch.stop()
        self.env.stop()
        self.folder.cleanup()

    def test_save_list_validate_and_delete_asset(self):
        meta = save_asset_from_preview(
            preview_id="a" * 64,
            name="Nền thử",
            image_path=self.image,
            image_hash=_file_hash(self.image),
            effect_plan={"items": [{"effect": "rain", "params": {"intensity": 0.5}}]},
            duration=2.0,
            output_profile="landscape_1080p",
        )
        self.assertTrue(meta["id"].startswith("bg_"))
        self.assertEqual(meta["asset_frames"], 20)
        self.assertEqual(meta["renderer_version"], MOTION_CACHE_VERSION)
        self.assertEqual(len(list_assets()), 1)
        loaded = load_asset(meta["id"])
        self.assertEqual(loaded["name"], "Nền thử")

        valid, reason = validate_asset(
            loaded,
            image_hash=_file_hash(self.image),
            image_frames=20,
            profile=self.profile,
        )
        self.assertTrue(valid, reason)

        invalid, reason = validate_asset(
            loaded,
            image_hash="sha256:" + "b" * 64,
            image_frames=20,
            profile=self.profile,
        )
        self.assertFalse(invalid)

        self.assertTrue(delete_asset(meta["id"]))
        self.assertEqual(list_assets(), [])

    def test_background_uses_a_valid_asset_clip(self):
        import subprocess

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=320x180:d=2:r=10",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(self.preview),
            ],
            check=True,
            capture_output=True,
        )
        meta = save_asset_from_preview(
            preview_id="a" * 64,
            name="Nền asset",
            image_path=self.image,
            image_hash=_file_hash(self.image),
            effect_plan={"items": [{"effect": "rain", "params": {"intensity": 0.5}}]},
            duration=2.0,
            output_profile="landscape_1080p",
        )
        audio = make_audio(self.root / "a.wav")
        prepared = {
            "title": "Bài",
            "artist": "Tác giả",
            "cover_line": "",
            "cover_image": str(self.image),
            "background_images": [str(self.image)],
            "background_effects": [
                {"image_path": str(self.image), "asset_id": meta["id"]}
            ],
            "original_audio": str(audio),
            "instrumental_audio": str(audio),
            "output_dir": str(self.root),
            "cover_duration": 0.2,
            "image_duration": 2.0,
            "output_profile": "landscape_1080p",
        }
        build = build_background(
            prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        self.assertEqual(build.segments[0]["kind"], "asset")
        self.assertEqual(build.segments[0]["asset_id"], meta["id"])

    def test_asset_rejects_preview_id_that_does_not_match_inputs(self):
        with patch("karaoke.assets.preview_id_for", return_value="b" * 64):
            with self.assertRaises(AssetError):
                save_asset_from_preview(
                    preview_id="a" * 64,
                    name="x",
                    image_path=self.image,
                    image_hash=_file_hash(self.image),
                    effect_plan={"items": [{"effect": "rain", "params": {"intensity": 0.5}}]},
                    duration=2.0,
                    output_profile="landscape_1080p",
                )

    def test_asset_rejects_wrong_duration(self):
        import subprocess

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=320x180:d=2:r=10",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(self.preview),
            ],
            check=True,
            capture_output=True,
        )
        with self.assertRaises(AssetError):
            save_asset_from_preview(
                preview_id="a" * 64,
                name="x",
                image_path=self.image,
                image_hash=_file_hash(self.image),
                effect_plan={"items": [{"effect": "rain", "params": {"intensity": 0.5}}]},
                duration=5.0,
                output_profile="landscape_1080p",
            )

    def test_asset_rejects_missing_preview(self):
        self.preview.unlink()
        with self.assertRaises(Exception):
            save_asset_from_preview(
                preview_id="a" * 64,
                name="x",
                image_path=self.image,
                image_hash=_file_hash(self.image),
                effect_plan={},
                duration=2.0,
                output_profile="landscape_1080p",
            )


class BackgroundStatusTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.image = make_image(self.root / "bg.png")
        self.audio = make_audio(self.root / "a.wav")
        self.profile = small_profile()
        self.prepared = {
            "title": "Bài",
            "artist": "Tác giả",
            "cover_line": "",
            "cover_image": str(self.image),
            "background_images": [str(self.image)],
            "background_effects": [],
            "original_audio": str(self.audio),
            "instrumental_audio": str(self.audio),
            "output_dir": str(self.root),
            "cover_duration": 0.2,
            "image_duration": 0.5,
            "output_profile": "landscape_1080p",
        }
        self.build = build_background(
            self.prepared,
            output_dir=self.root,
            profile=self.profile,
            log=lambda _message: None,
        )
        self.profile_patch = patch("karaoke.background.get_profile", return_value=self.profile)
        self.profile_patch.start()
        self.prepared["background_render"] = self.build.to_project()

    def tearDown(self):
        self.profile_patch.stop()
        self.folder.cleanup()

    def test_status_ready_then_stale_when_image_changes(self):
        ready = background_status(self.prepared, self.root)
        self.assertEqual(ready["state"], "ready", ready)

        make_image(self.image, color=(200, 10, 10), size=(321, 180))
        stale = background_status(self.prepared, self.root)
        self.assertEqual(stale["state"], "stale", stale)

    def test_status_ready_when_file_is_valid_but_metadata_is_missing(self):
        prepared = dict(self.prepared)
        prepared.pop("background_render")
        status = background_status(prepared, self.root)
        self.assertEqual(status["state"], "ready", status)

    def test_status_missing_when_file_is_removed(self):
        self.build.path.unlink()
        status = background_status(self.prepared, self.root)
        self.assertEqual(status["state"], "missing", status)

    def test_cleanup_aborts_when_a_project_json_is_broken(self):
        bad = self.root / "broken.karaoke.json"
        bad.write_text("{not-json", encoding="utf-8")
        old = self.root / ".karaoke-media/backgrounds" / f"background-{'d' * 64}.mp4"
        old.write_bytes(b"old")
        os.utime(old, (0, 0))
        self.assertEqual(cleanup_backgrounds(self.root, min_age_days=1), [])
        self.assertTrue(old.is_file())

    def test_cleanup_keeps_referenced_and_recent_files(self):
        old_unreferenced = self.root / ".karaoke-media/backgrounds" / f"background-{'b' * 64}.mp4"
        old_unreferenced.write_bytes(b"old")
        os.utime(old_unreferenced, (0, 0))
        recent_unreferenced = self.root / ".karaoke-media/backgrounds" / f"background-{'c' * 64}.mp4"
        recent_unreferenced.write_bytes(b"recent")

        deleted = cleanup_backgrounds(self.root, keep_hashes=[self.build.hash], min_age_days=1)
        self.assertEqual(deleted, [old_unreferenced.name])
        self.assertTrue(self.build.path.is_file())
        self.assertTrue(recent_unreferenced.is_file())


if __name__ == "__main__":
    unittest.main()

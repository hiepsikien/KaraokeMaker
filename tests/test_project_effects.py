import tempfile
import unicodedata
import unittest
from pathlib import Path

from karaoke.access import clear_grants, grant
from karaoke.project import (
    _path_key,
    _same_path,
    load_project,
    prepared_from_project,
    project_from_fields,
    project_path_for,
    public_project,
    save_project,
    sync_project_file,
)


def effect_item(image: Path) -> dict:
    return {
        "image_path": str(image),
        "image_hash": "sha256:" + "b" * 64,
        "effect_prompt": "mưa nhẹ, khói bát mỳ",
        "effect_consent_receipt": {
            "timestamp": "2026-11-21T10:00:00Z",
            "image_hash": "sha256:" + "b" * 64,
            "provider": "gemini",
            "model": "gemini-3.8-flash",
        },
        "selected_object_ids": ["pot-1", "lantern-1"],
        "effect_plan": {
            "version": 1,
            "image_hash": "sha256:" + "b" * 64,
            "profile_id": "landscape_1080p",
            "fit_transform": {
                "mode": "contain",
                "source_width": 1280,
                "source_height": 720,
                "frame_width": 1920,
                "frame_height": 1080,
                "scale": 1.5,
                "offset_x": 0.0,
                "offset_y": 0.0,
            },
            "regions": [
                {
                    "id": "outdoor",
                    "polygon": [
                        {"x": 0.5, "y": 0.0},
                        {"x": 1.0, "y": 0.0},
                        {"x": 1.0, "y": 1.0},
                        {"x": 0.5, "y": 1.0},
                    ],
                }
            ],
            "protect": [
                {"label": "face", "box": {"x1": 0.37, "y1": 0.43, "x2": 0.44, "y2": 0.57}}
            ],
            "items": [
                {
                    "effect": "rain",
                    "target_ids": [],
                    "params": {"intensity": 0.55, "loop_duration": 8, "audio": False},
                },
                {
                    "effect": "steam",
                    "target_ids": ["pot-1"],
                    "params": {"intensity": 0.35, "loop_duration": 8, "audio": False},
                },
            ],
        },
        "model": "gemini-3.8-flash",
        "created": "2026-11-21T10:00:05Z",
        "asset_id": None,
    }


class BackgroundEffectProjectTests(unittest.TestCase):
    def tearDown(self):
        clear_grants()

    def test_malformed_effect_plan_is_normalized(self):
        project = project_from_fields(
            {
                "title": "Bài",
                "background_effects": [
                    {"image_path": "/tmp/a.png", "effect_plan": None},
                    {"image_path": "/tmp/b.png", "effect_plan": {"items": "not-a-list"}},
                ],
            }
        )
        for effect in project["background_effects"]:
            self.assertIsInstance(effect["effect_plan"], dict)
            self.assertIsInstance(effect["effect_plan"]["items"], list)
        self.assertEqual(
            [effect["effect_plan"]["items"] for effect in project["background_effects"]],
            [[], []],
        )

    def test_project_from_fields_and_public_project_keep_the_effect(self):
        image = Path("/tmp/bg.png")
        item = effect_item(image)
        project = project_from_fields(
            {
                "title": "Bài",
                "artist": "Tác giả",
                "background_images": [str(image)],
                "background_effects": [item],
                "output_profile": "landscape_1080p",
            }
        )
        self.assertEqual(len(project["background_effects"]), 1)
        self.assertEqual(project["background_effects"][0]["image_path"], str(image))
        self.assertEqual(project["background_effects"][0]["image_hash"], item["image_hash"])
        self.assertEqual(
            project["background_effects"][0]["effect_consent_receipt"]["model"],
            "gemini-3.8-flash",
        )

        public = public_project(project)
        self.assertEqual(len(public["background_effects"]), 1)
        effect = public["background_effects"][0]
        self.assertEqual(effect["image_hash"], item["image_hash"])
        self.assertEqual(effect["effect_consent_receipt"], item["effect_consent_receipt"])
        self.assertEqual(
            [plan_item["effect"] for plan_item in effect["effect_plan"]["items"]],
            ["rain", "steam"],
        )
        self.assertEqual(effect["effect_plan"]["regions"], item["effect_plan"]["regions"])
        self.assertEqual(effect["effect_plan"]["protect"], item["effect_plan"]["protect"])

    def test_prepared_from_project_includes_effects_and_profile(self):
        item = effect_item(Path("/tmp/bg.png"))
        project = project_from_fields(
            {
                "title": "Bài",
                "artist": "Tác giả",
                "background_effects": [item],
                "output_profile": "landscape_1080p",
            }
        )
        prepared = prepared_from_project(project)
        self.assertEqual(prepared["output_profile"], "landscape_1080p")
        self.assertEqual(len(prepared["background_effects"]), 1)
        self.assertEqual(prepared["background_effects"][0]["effect_plan"]["items"][0]["effect"], "rain")

    def test_old_payload_without_the_fields_gets_defaults(self):
        project = project_from_fields({"title": "Cũ", "artist": "Ai đó"})
        self.assertEqual(project["background_effects"], [])
        self.assertEqual(project["output_profile"], "landscape_1080p")

        public = public_project(project)
        self.assertEqual(public["background_effects"], [])
        self.assertEqual(public["output_profile"], "landscape_1080p")

        prepared = prepared_from_project(project)
        self.assertEqual(prepared["background_effects"], [])
        self.assertEqual(prepared["output_profile"], "landscape_1080p")

    def test_save_and_load_round_trip_keeps_the_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            image = root / "bg.png"
            image.write_bytes(b"not-an-image")
            item = effect_item(image)
            project = project_from_fields(
                {
                    "title": "Vòng",
                    "artist": "Tác giả",
                    "background_images": [str(image)],
                    "background_effects": [item],
                    "output_profile": "landscape_1080p",
                    "output_dir": str(root),
                },
                status="draft",
            )
            saved = save_project(project_path_for(root, "Vòng"), project)
            self.assertTrue(saved.is_file())

            loaded, loaded_path = load_project(saved)
            self.assertEqual(loaded_path, saved.resolve())
            self.assertEqual(len(loaded["background_effects"]), 1)
            effect = loaded["background_effects"][0]
            self.assertEqual(effect["image_path"], str(image))
            self.assertEqual(effect["image_hash"], item["image_hash"])
            self.assertEqual(effect["effect_consent_receipt"], item["effect_consent_receipt"])
            self.assertEqual(
                [plan_item["effect"] for plan_item in effect["effect_plan"]["items"]],
                ["rain", "steam"],
            )
            self.assertEqual(effect["effect_plan"]["regions"][0]["id"], "outdoor")
            self.assertEqual(effect["effect_plan"]["protect"][0]["label"], "face")

    def test_sync_does_not_delete_same_file_with_nfd_spelling(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            grant(root)
            title = "Đại Việt Trường Tồn"
            prepared = {
                "title": title,
                "artist": "Andy Nguyen",
                "output_dir": str(root),
                "project_path": "",
            }
            first = sync_project_file(prepared, status="draft")
            self.assertTrue(first.is_file())
            composed = str(first)
            decomposed = unicodedata.normalize("NFD", composed)
            self.assertEqual(_path_key(Path(composed)), _path_key(Path(decomposed)))
            prepared["project_path"] = decomposed
            again = sync_project_file(prepared, status="aligned")
            self.assertTrue(again.is_file())
            self.assertTrue(first.is_file())

    def test_same_path_does_not_merge_distinct_existing_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "A.karaoke.json"
            second = root / "B.karaoke.json"
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")
            self.assertFalse(_same_path(first, second))


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from karaoke import director
from karaoke.access import clear_grants, grant
from karaoke.director import DirectorError, analyze_scene, normalize_inventory


class FakeResponse:
    """Minimal requests.Response stand-in (no sockets, no network)."""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def gemini_payload(data, *, prompt=100, output=20, thoughts=10, finish="STOP"):
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": json.dumps(data)}]},
                "finishReason": finish,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": prompt,
            "candidatesTokenCount": output,
            "thoughtsTokenCount": thoughts,
            "totalTokenCount": prompt + output + thoughts,
        },
    }


def make_png(path: Path, size=(64, 36)) -> Path:
    Image.new("RGB", size, (40, 80, 120)).save(path)
    return path


class NormalizeInventoryTests(unittest.TestCase):
    def test_box_2d_uses_ymin_xmin_ymax_xmax_order(self):
        inventory = normalize_inventory(
            {
                "objects": [
                    {"id": "boat-1", "label": "boat", "box_2d": [100, 200, 300, 400]},
                ]
            }
        )
        self.assertEqual(len(inventory["objects"]), 1)
        self.assertEqual(
            inventory["objects"][0]["box"],
            {"x1": 0.2, "y1": 0.1, "x2": 0.4, "y2": 0.3},
        )

    def test_point_is_transposed_when_it_falls_outside_its_box(self):
        inventory = normalize_inventory(
            {
                "objects": [
                    {
                        "id": "banner-1",
                        "label": "banner",
                        "box_2d": [100, 100, 300, 900],  # y 0.1..0.3, x 0.1..0.9
                        # Read as [x, y] this is (0.2, 0.5): y is outside.
                        # Its transpose (0.5, 0.2) is inside.
                        "pivot_2d": [200, 500],
                    }
                ]
            }
        )
        anchor = inventory["objects"][0]["anchors"]["pivot"]
        self.assertAlmostEqual(anchor["x"], 0.5)
        self.assertAlmostEqual(anchor["y"], 0.2)

    def test_point_inside_the_box_is_kept_as_given(self):
        inventory = normalize_inventory(
            {
                "objects": [
                    {
                        "id": "banner-1",
                        "label": "banner",
                        "box_2d": [0, 0, 1000, 1000],
                        "pivot_2d": [250, 750],
                    }
                ]
            }
        )
        anchor = inventory["objects"][0]["anchors"]["pivot"]
        self.assertAlmostEqual(anchor["x"], 0.25)
        self.assertAlmostEqual(anchor["y"], 0.75)

    def test_invalid_point_outside_its_box_becomes_null(self):
        inventory = normalize_inventory(
            {
                "objects": [
                    {
                        "id": "rock-1",
                        "label": "rock",
                        "box_2d": [100, 100, 300, 300],
                        # (0.8, 0.8) is outside; the transpose is outside too.
                        "pivot_2d": [800, 800],
                        "opening_2d": [900, 100],
                    }
                ]
            }
        )
        anchors = inventory["objects"][0]["anchors"]
        self.assertIsNone(anchors["pivot"])
        self.assertIsNone(anchors["opening"])

    def test_region_with_fewer_than_three_points_is_dropped(self):
        inventory = normalize_inventory(
            {
                "objects": [],
                "regions": [
                    {"id": "sky", "polygon_2d": [[100, 100], [900, 100]]},
                    {
                        "id": "outdoor",
                        "polygon_2d": [[0, 0], [1000, 0], [1000, 1000]],
                    },
                ],
            }
        )
        self.assertEqual([region["id"] for region in inventory["regions"]], ["outdoor"])
        self.assertEqual(len(inventory["regions"][0]["polygon"]), 3)
        self.assertAlmostEqual(inventory["regions"][0]["polygon"][1]["x"], 1.0)

    def test_protect_boxes_are_normalized(self):
        inventory = normalize_inventory(
            {
                "objects": [],
                "protect": [
                    {"label": "face", "box_2d": [400, 500, 600, 700]},
                    {"label": "text", "box_2d": [0, 0, 0, 0]},
                ],
            }
        )
        self.assertEqual(len(inventory["protect"]), 1)
        self.assertEqual(
            inventory["protect"][0],
            {"label": "face", "box": {"x1": 0.5, "y1": 0.4, "x2": 0.7, "y2": 0.6}},
        )

    def test_object_without_a_box_is_dropped(self):
        inventory = normalize_inventory(
            {
                "objects": [
                    {"id": "ghost", "label": "ghost", "pivot_2d": [100, 100]},
                    {"id": "real", "label": "real", "box_2d": [100, 100, 200, 200]},
                ]
            }
        )
        self.assertEqual([item["id"] for item in inventory["objects"]], ["real"])


class AnalyzeSceneTests(unittest.TestCase):
    PROMPT = "mưa nhẹ, khói bốc"

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.cache = self.root / "cache"
        self.image = make_png(self.root / "scene.png")

        self.intent_payload = {
            "effects": [
                {"type": "rain", "intensity": 0.35, "audio": False, "target_labels": []},
                {"type": "steam", "intensity": 0.55, "audio": False, "target_labels": ["pot"]},
            ]
        }
        self.inventory_payload = {
            "scene": "A cook pot beside an open window.",
            "style": "painterly",
            "objects": [
                {
                    "id": "pot-1",
                    "label": "pot",
                    "box_2d": [500, 200, 800, 500],
                    "opening_2d": [650, 500],
                    "confidence": 0.9,
                },
                {"id": "person-1", "label": "person", "box_2d": [100, 100, 500, 900], "confidence": 0.8},
            ],
            "regions": [
                {
                    "id": "outdoor",
                    "polygon_2d": [[0, 0], [1000, 0], [1000, 400], [0, 400]],
                },
                {"id": "dry", "polygon_2d": [[0, 0], [500, 0]]},
            ],
            "protect": [{"label": "face", "box_2d": [200, 150, 320, 260]}],
        }

    def tearDown(self):
        self.folder.cleanup()

    def _post(
        self,
        intent_payload=None,
        inventory_payload="default",
        inventory_status=200,
        inventory_finish="STOP",
    ):
        intent_payload = self.intent_payload if intent_payload is None else intent_payload
        inventory_payload = self.inventory_payload if inventory_payload == "default" else inventory_payload

        def fake_post(url, headers=None, json=None, timeout=None):
            if director.INTENT_MODEL in url:
                return FakeResponse(200, gemini_payload(intent_payload, prompt=100, output=20, thoughts=5))
            if inventory_status != 200:
                return FakeResponse(inventory_status, {"error": {"message": "quota"}})
            if inventory_payload is None:
                return FakeResponse(
                    200,
                    {
                        "candidates": [{"content": {}, "finishReason": inventory_finish}],
                        "usageMetadata": {"promptTokenCount": 2000, "totalTokenCount": 2000},
                    },
                )
            return FakeResponse(
                200,
                gemini_payload(
                    inventory_payload,
                    prompt=2000,
                    output=300,
                    thoughts=500,
                    finish=inventory_finish,
                ),
            )

        return patch("karaoke.director.requests.post", side_effect=fake_post)

    def test_analyze_scene_returns_the_full_contract_and_caches(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with patch.object(director, "_cache_dir", return_value=self.cache):
                with self._post():
                    first = analyze_scene(self.image, self.PROMPT, consent=True)

        self.assertTrue(first["image_hash"].startswith("sha256:"))
        self.assertEqual(len(first["image_hash"]), len("sha256:") + 64)
        self.assertEqual((first["width"], first["height"]), (64, 36))
        self.assertEqual(first["model"], director.DEFAULT_MODEL)
        self.assertFalse(first["cached"])
        self.assertEqual(first["usage"], {"prompt": 2100, "output": 320, "thoughts": 505, "total": 2925})

        labels = [item["label"] for item in first["objects"]]
        self.assertEqual(labels, ["pot", "person"])
        self.assertEqual([region["id"] for region in first["regions"]], ["outdoor"])
        self.assertEqual(first["protect"][0]["label"], "face")
        self.assertEqual(first["effect_plan"]["profile_id"], "landscape_1080p")
        self.assertEqual(first["effect_plan"]["fit_transform"]["mode"], "contain")
        self.assertEqual(
            [item["effect"] for item in first["effect_plan"]["items"]],
            ["rain", "steam"],
        )
        self.assertEqual(first["effect_plan"]["items"][1]["target_ids"], ["pot-1"])

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with patch.object(director, "_cache_dir", return_value=self.cache):
                with self._post():
                    second = analyze_scene(self.image, self.PROMPT, consent=True)
        self.assertTrue(second["cached"])
        self.assertEqual(second["usage"]["total"], 0)
        self.assertEqual(second["effect_plan"], first["effect_plan"])

    def test_profile_and_intent_fallback_are_reported(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with patch.object(director, "_cache_dir", return_value=self.cache):
                with patch.object(
                    director,
                    "_load_intent",
                    return_value=(
                        director.keyword_intent("mưa nhẹ"),
                        {},
                        False,
                        True,
                    ),
                ), patch.object(
                    director,
                    "_load_inventory",
                    return_value=(self.inventory_payload, {}, False),
                ):
                    result = analyze_scene(
                        self.image,
                        "mưa nhẹ",
                        consent=True,
                        output_profile="portrait_1080x1920",
                    )
        self.assertTrue(result["intent_fallback"])
        self.assertEqual(result["effect_plan"]["profile_id"], "portrait_1080x1920")

    def test_consent_false_is_refused_before_any_call(self):
        with patch("karaoke.director.requests.post") as post:
            with self.assertRaises(DirectorError) as caught:
                analyze_scene(self.image, self.PROMPT, consent=False)
        self.assertIn("đồng ý", str(caught.exception))
        post.assert_not_called()

    def test_missing_key_is_refused(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
            with patch("karaoke.director.requests.post") as post:
                with self.assertRaises(DirectorError) as caught:
                    analyze_scene(self.image, self.PROMPT, consent=True)
        self.assertIn("GEMINI_API_KEY", str(caught.exception))
        post.assert_not_called()

    def test_max_tokens_finish_reason_raises_actionable_error(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with patch.object(director, "_cache_dir", return_value=self.cache):
                with self._post(inventory_payload=None, inventory_finish="MAX_TOKENS"):
                    with self.assertRaises(DirectorError) as caught:
                        analyze_scene(self.image, self.PROMPT, consent=True)
        message = str(caught.exception)
        self.assertIn("MAX_TOKENS", message)
        self.assertIn("thử lại", message)

    def test_rate_limit_429_raises(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with patch.object(director, "_cache_dir", return_value=self.cache):
                with self._post(inventory_status=429):
                    with self.assertRaises(DirectorError) as caught:
                        analyze_scene(self.image, self.PROMPT, consent=True)
        self.assertIn("429", str(caught.exception))


class DirectorEndpointTests(unittest.TestCase):
    """Call the route function directly; no server is started."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.image = make_png(self.root / "scene.png")
        # Use the real access check: a user-granted folder is enough.
        grant(self.root)

    def tearDown(self):
        clear_grants()
        self.folder.cleanup()

    def test_consent_false_returns_http_400(self):
        from fastapi import HTTPException

        from karaoke.server import DirectorRequest, director_analyze

        body = DirectorRequest(image_path=str(self.image), prompt="mưa nhẹ", consent=False)
        with self.assertRaises(HTTPException) as caught:
            director_analyze(body)
        self.assertEqual(caught.exception.status_code, 400)

    def test_full_resolution_render_returns_a_job_and_polls_to_done(self):
        from karaoke import server

        body = server.StudioRenderRequest(
            image_path=str(self.image),
            effect_plan={"items": [{"effect": "rain", "target_ids": [], "params": {}}]},
            duration=16,
            output_profile="landscape_1080p",
            full_resolution=True,
        )
        result = {
            "id": "a" * 64,
            "path": "/tmp/preview.mp4",
            "width": 1920,
            "height": 1080,
            "duration": 16.0,
        }
        with patch("karaoke.server.render_preview", return_value=result) as fake:
            response = server.studio_render(body)
            self.assertEqual(response.status_code, 202)
            payload = json.loads(response.body)
            self.assertEqual(payload["status"], "rendering")
            job_id = payload["job_id"]
            self.assertRegex(job_id, r"^[0-9a-f]{32}$")

            status = None
            for _ in range(200):
                status = server.studio_render_job(job_id)
                if status["status"] == "done":
                    break
                time.sleep(0.01)
            self.assertEqual(status["status"], "done")
            self.assertEqual(status["result"]["width"], 1920)
            self.assertEqual(status["result"]["height"], 1080)
            fake.assert_called_once()
            self.assertTrue(fake.call_args.kwargs["full_resolution"])
        server._studio_jobs.pop(job_id, None)

    def test_success_returns_the_analyze_dict(self):
        from karaoke.server import DirectorRequest, director_analyze

        expected = {
            "image_hash": "sha256:" + "a" * 64,
            "width": 640,
            "height": 360,
            "objects": [],
            "regions": [],
            "protect": [],
            "effect_plan": {"items": []},
            "usage": {"total": 1},
            "cached": False,
        }
        body = DirectorRequest(image_path=str(self.image), prompt="mưa nhẹ", consent=True)
        with patch("karaoke.server.analyze_scene", return_value=expected) as fake:
            result = director_analyze(body)
        self.assertIs(result, expected)
        fake.assert_called_once()
        kwargs = fake.call_args.kwargs
        self.assertTrue(kwargs["consent"])
        self.assertIsNone(kwargs["model"])


class TargetResolutionTests(unittest.TestCase):
    def test_vietnamese_flag_and_sail_labels_resolve_to_their_objects(self):
        objects = [
            {"id": "flag-1", "label": "flag"},
            {"id": "sail-1", "label": "sail"},
            {"id": "pot-1", "label": "pot"},
        ]
        plan = director.build_effect_plan(
            "sha256:" + "e" * 64,
            {
                "effects": [
                    {
                        "type": "wind",
                        "target_labels": ["cờ", "buồm"],
                        "intensity": 0.35,
                        "audio": False,
                    }
                ]
            },
            objects,
            [],
            [],
            640,
            360,
        )
        self.assertEqual(
            set(plan["items"][0]["target_ids"]), {"flag-1", "sail-1"}
        )


class EffectParamPlanTests(unittest.TestCase):
    def test_plan_items_include_density_and_speed_defaults(self):
        plan = director.build_effect_plan(
            "sha256:" + "a" * 64,
            {"effects": [{"type": "rain", "intensity": 0.5, "audio": False}]},
            [],
            [],
            [],
            640,
            360,
        )
        params = plan["items"][0]["params"]
        self.assertEqual(params["intensity"], 0.5)
        self.assertEqual(params["density"], 1.0)
        self.assertEqual(params["speed"], 1.0)


class EnvLoadingTests(unittest.TestCase):
    def test_non_empty_environment_wins_and_blank_values_are_filled(self):
        from karaoke.server import _load_env

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text(
                "GEMINI_API_KEY=from-file\nGOOGLE_API_KEY=from-file\nEMPTY_KEY=filled\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "from-shell",
                    "GOOGLE_API_KEY": "",
                    "EMPTY_KEY": "",
                },
                clear=False,
            ):
                _load_env(path)
                self.assertEqual(os.environ["GEMINI_API_KEY"], "from-shell")
                self.assertEqual(os.environ["GOOGLE_API_KEY"], "from-file")
                self.assertEqual(os.environ["EMPTY_KEY"], "filled")


if __name__ == "__main__":
    unittest.main()

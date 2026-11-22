import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from karaoke.settings import DEFAULT_SETTINGS, load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.data_root = Path(self.folder.name) / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.env = patch.dict(os.environ, {"KARAOKE_DATA_DIR": str(self.data_root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def test_defaults_when_file_missing_or_invalid(self):
        self.assertEqual(load_settings(), DEFAULT_SETTINGS)
        self.assertFalse((self.data_root / "settings.json").exists())

        path = self.data_root / "settings.json"
        path.write_text("{not-json", encoding="utf-8")
        self.assertEqual(load_settings(), DEFAULT_SETTINGS)

    def test_round_trip(self):
        payload = {
            "gemini_consent": True,
            "device": "mps",
            "model_name": "medium",
            "output_dir": "/tmp/karaoke out",
        }
        saved = save_settings(payload)
        self.assertEqual(saved, payload)
        self.assertEqual(load_settings(), payload)

        path = self.data_root / "settings.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)

    def test_validation_falls_back_to_defaults(self):
        saved = save_settings(
            {
                "gemini_consent": "yes",
                "device": "cuda",
                "model_name": "   ",
                "output_dir": 42,
                "unknown": "drop me",
            }
        )
        self.assertEqual(saved, DEFAULT_SETTINGS)
        self.assertNotIn("unknown", saved)

    def test_unknown_and_invalid_stored_keys_are_dropped_on_load(self):
        path = self.data_root / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "gemini_consent": False,
                    "device": "mps",
                    "model_name": "small",
                    "output_dir": "",
                    "api_key": "secret",
                }
            ),
            encoding="utf-8",
        )
        settings = load_settings()
        self.assertNotIn("api_key", settings)
        self.assertEqual(settings["device"], "mps")


if __name__ == "__main__":
    unittest.main()

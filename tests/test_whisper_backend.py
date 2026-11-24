import builtins
import types
import unittest
from unittest.mock import Mock, patch

from karaoke import whisper_backend


def fake_imports(mlx_module=None, stable_module=None):
    """Return an ``__import__`` replacement for the two optional packages."""
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mlx_whisper":
            if mlx_module is None:
                raise ImportError("missing mlx_whisper")
            return mlx_module
        if name == "stable_whisper":
            if stable_module is None:
                raise ImportError("missing stable_whisper")
            return stable_module
        return real_import(name, globals, locals, fromlist, level)

    return fake_import


class MlxAvailableTests(unittest.TestCase):
    def test_false_off_darwin(self):
        with (
            patch.object(whisper_backend.sys, "platform", "linux"),
            patch.object(whisper_backend.platform, "machine", return_value="arm64"),
        ):
            self.assertFalse(whisper_backend.mlx_available())

    def test_false_on_intel_mac(self):
        with (
            patch.object(whisper_backend.sys, "platform", "darwin"),
            patch.object(whisper_backend.platform, "machine", return_value="x86_64"),
        ):
            self.assertFalse(whisper_backend.mlx_available())

    def test_false_when_mlx_whisper_import_fails(self):
        with (
            patch.object(whisper_backend.sys, "platform", "darwin"),
            patch.object(whisper_backend.platform, "machine", return_value="arm64"),
            patch("builtins.__import__", side_effect=fake_imports(mlx_module=None)),
        ):
            self.assertFalse(whisper_backend.mlx_available())

    def test_false_when_stable_mlx_loader_is_missing(self):
        stable_module = types.ModuleType("stable_whisper")  # no load_mlx_whisper
        with (
            patch.object(whisper_backend.sys, "platform", "darwin"),
            patch.object(whisper_backend.platform, "machine", return_value="arm64"),
            patch(
                "builtins.__import__",
                side_effect=fake_imports(
                    mlx_module=types.ModuleType("mlx_whisper"),
                    stable_module=stable_module,
                ),
            ),
        ):
            self.assertFalse(whisper_backend.mlx_available())

    def test_true_on_apple_silicon_with_both_imports(self):
        stable_module = types.ModuleType("stable_whisper")
        stable_module.load_mlx_whisper = lambda name: name
        with (
            patch.object(whisper_backend.sys, "platform", "darwin"),
            patch.object(whisper_backend.platform, "machine", return_value="arm64"),
            patch(
                "builtins.__import__",
                side_effect=fake_imports(
                    mlx_module=types.ModuleType("mlx_whisper"),
                    stable_module=stable_module,
                ),
            ),
        ):
            self.assertTrue(whisper_backend.mlx_available())


class LoadWhisperModelTests(unittest.TestCase):
    def test_auto_on_non_arm64_uses_stable_without_trying_mlx(self):
        with (
            patch.object(whisper_backend.sys, "platform", "linux"),
            patch.object(whisper_backend.platform, "machine", return_value="x86_64"),
            patch.object(whisper_backend, "load_mlx_whisper") as load_mlx,
            patch.object(
                whisper_backend, "load_stable_model", return_value="stable-model"
            ) as load_stable,
        ):
            model, backend = whisper_backend.load_whisper_model(
                "medium", "cpu", "auto", None
            )

        self.assertEqual((model, backend), ("stable-model", "stable"))
        load_mlx.assert_not_called()
        load_stable.assert_called_once_with("medium", "cpu")

    def test_auto_uses_mlx_when_available(self):
        log = Mock()
        with (
            patch.object(whisper_backend, "mlx_available", return_value=True),
            patch.object(
                whisper_backend, "load_mlx_whisper", return_value="mlx-model"
            ) as load_mlx,
            patch.object(whisper_backend, "load_stable_model") as load_stable,
        ):
            model, backend = whisper_backend.load_whisper_model(
                "medium", "cpu", "auto", log
            )

        self.assertEqual((model, backend), ("mlx-model", "mlx"))
        load_mlx.assert_called_once_with("medium")
        load_stable.assert_not_called()
        log.assert_called()

    def test_auto_falls_back_to_stable_when_mlx_load_raises(self):
        log = Mock()
        with (
            patch.object(whisper_backend, "mlx_available", return_value=True),
            patch.object(
                whisper_backend,
                "load_mlx_whisper",
                side_effect=RuntimeError("mlx boom"),
            ),
            patch.object(
                whisper_backend, "load_stable_model", return_value="stable-model"
            ) as load_stable,
        ):
            model, backend = whisper_backend.load_whisper_model(
                "medium", "cpu", "auto", log
            )

        self.assertEqual((model, backend), ("stable-model", "stable"))
        load_stable.assert_called_once_with("medium", "cpu")
        messages = [call.args[0] for call in log.call_args_list if call.args]
        self.assertTrue(any("mlx boom" in message for message in messages))
        self.assertTrue(any("fallback" in message for message in messages))

    def test_engine_mlx_falls_back_when_unavailable(self):
        log = Mock()
        with (
            patch.object(whisper_backend, "mlx_available", return_value=False),
            patch.object(whisper_backend, "load_mlx_whisper") as load_mlx,
            patch.object(
                whisper_backend, "load_stable_model", return_value="stable-model"
            ) as load_stable,
        ):
            model, backend = whisper_backend.load_whisper_model(
                "medium", "mps", "mlx", log
            )

        self.assertEqual((model, backend), ("stable-model", "stable"))
        load_mlx.assert_not_called()
        load_stable.assert_called_once_with("medium", "mps")
        self.assertTrue(log.called)

    def test_engine_stable_uses_load_model(self):
        log = Mock()
        with (
            patch.object(
                whisper_backend, "mlx_available", return_value=True
            ) as mlx_available,
            patch.object(whisper_backend, "load_mlx_whisper") as load_mlx,
            patch.object(
                whisper_backend, "load_stable_model", return_value="stable-model"
            ) as load_stable,
        ):
            model, backend = whisper_backend.load_whisper_model(
                "large-v3", "mps", "stable", log
            )

        self.assertEqual((model, backend), ("stable-model", "stable"))
        load_stable.assert_called_once_with("large-v3", "mps")
        mlx_available.assert_not_called()
        load_mlx.assert_not_called()

    def test_unknown_engine_behaves_like_auto(self):
        with (
            patch.object(whisper_backend, "mlx_available", return_value=True),
            patch.object(
                whisper_backend, "load_mlx_whisper", return_value="mlx-model"
            ) as load_mlx,
            patch.object(whisper_backend, "load_stable_model") as load_stable,
        ):
            model, backend = whisper_backend.load_whisper_model(
                "medium", "cpu", "turbo", None
            )

        self.assertEqual((model, backend), ("mlx-model", "mlx"))
        load_mlx.assert_called_once_with("medium")
        load_stable.assert_not_called()

        with (
            patch.object(whisper_backend, "mlx_available", return_value=False),
            patch.object(whisper_backend, "load_mlx_whisper") as load_mlx,
            patch.object(
                whisper_backend, "load_stable_model", return_value="stable-model"
            ) as load_stable,
        ):
            model, backend = whisper_backend.load_whisper_model(
                "medium", "cpu", "turbo", None
            )

        self.assertEqual((model, backend), ("stable-model", "stable"))
        load_mlx.assert_not_called()
        load_stable.assert_called_once_with("medium", "cpu")


if __name__ == "__main__":
    unittest.main()

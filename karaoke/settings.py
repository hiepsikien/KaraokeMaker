"""Small persisted app settings (consent, device, default transcription model).

The Gemini key may be stored here so a desktop user can override an inherited
shell/environment key. The settings file is written with ``0600`` and the key
is never returned to the browser; the API only reports whether one is set.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - macOS/Linux
    msvcrt = None

from karaoke.cache import data_root

SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {
    "gemini_consent": False,
    "gemini_api_key": "",
    "device": "cpu",
    "model_name": "small",
    "output_dir": "",
}
DEVICES = frozenset({"cpu", "mps"})
_LOCK = threading.Lock()


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            handle.seek(0)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


def settings_path() -> Path:
    return data_root() / SETTINGS_FILE


def _clean(payload: object) -> dict:
    """Merge defaults and silently drop unknown/invalid values."""
    data = payload if isinstance(payload, dict) else {}
    result = dict(DEFAULT_SETTINGS)
    consent = data.get("gemini_consent", result["gemini_consent"])
    if isinstance(consent, bool):
        result["gemini_consent"] = consent
    api_key = data.get("gemini_api_key", result["gemini_api_key"])
    if isinstance(api_key, str):
        result["gemini_api_key"] = api_key.strip()
    device = data.get("device", result["device"])
    if isinstance(device, str) and device.strip() in DEVICES:
        result["device"] = device.strip()
    model = data.get("model_name", result["model_name"])
    if isinstance(model, str) and model.strip():
        result["model_name"] = model.strip()
    output_dir = data.get("output_dir", result["output_dir"])
    if isinstance(output_dir, str):
        result["output_dir"] = output_dir.strip()
    return result


def load_settings() -> dict:
    """Read settings, falling back to defaults for missing/invalid files."""
    path = settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return dict(DEFAULT_SETTINGS)
    return _clean(payload)


def public_settings(data: dict | None = None) -> dict:
    """Settings safe to send to the browser: never include the key itself."""
    values = dict(data if isinstance(data, dict) else load_settings())
    has_key = bool(str(values.pop("gemini_api_key", "") or "").strip())
    values["gemini_api_key_set"] = has_key
    return values


def save_settings(payload: dict) -> dict:
    """Validate and atomically persist settings; return the stored values."""
    data = _clean(payload)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{SETTINGS_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    with _LOCK:
        with _file_lock(path.parent / f".{SETTINGS_FILE}.lock"):
            try:
                temporary.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    return data

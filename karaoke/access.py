from __future__ import annotations

import os
import threading
from pathlib import Path


class AccessError(RuntimeError):
    pass


_granted: list[Path] = []
_lock = threading.Lock()


def audio_root() -> Path:
    """Folder that holds Songs, Separated, and Karaoke. Override with KARAOKE_AUDIO_ROOT."""
    raw = os.environ.get("KARAOKE_AUDIO_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser()
    downloads = Path.home() / "Downloads"
    for name in ("Audio", "Audio "):
        candidate = downloads / name
        if candidate.is_dir():
            return candidate
    return downloads / "Audio"


def clear_grants() -> None:
    with _lock:
        _granted.clear()


def grant(path: Path) -> None:
    """Remember a directory the user picked in this session."""
    resolved = path.expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        return
    if resolved.is_file():
        resolved = resolved.parent
    with _lock:
        if resolved not in _granted:
            _granted.append(resolved)


def grant_project_paths(payload: dict) -> None:
    """Allow media paths stored in a project the user just opened via the file dialog."""
    for key in (
        "original_audio",
        "vocal_audio",
        "instrumental_audio",
        "cover_image",
        "output_dir",
        "path",
        "project_path",
        "timing_path",
    ):
        text = str(payload.get(key) or "").strip()
        if text:
            grant(Path(text))
    for item in payload.get("background_images") or []:
        text = str(item or "").strip()
        if text:
            grant(Path(text))
    restore = payload.get("restore")
    if isinstance(restore, dict):
        stem = str(restore.get("instrumental_stem") or "").strip()
        if stem:
            grant(Path(stem))


def allowed_roots() -> list[Path]:
    roots: list[Path] = []
    root = audio_root()
    if root.exists():
        try:
            roots.append(root.resolve())
        except OSError:
            pass
    extra = os.environ.get("KARAOKE_ALLOWED_ROOTS", "")
    for part in extra.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        path = Path(part).expanduser()
        if not path.exists():
            continue
        try:
            roots.append(path.resolve())
        except OSError:
            continue
    with _lock:
        roots.extend(_granted)
    return roots


def ensure_allowed(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    for root in allowed_roots():
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise AccessError(
        "File nằm ngoài thư mục được phép. Chọn lại bằng hộp thoại, "
        "hoặc đặt KARAOKE_AUDIO_ROOT trỏ tới thư mục nhạc."
    )

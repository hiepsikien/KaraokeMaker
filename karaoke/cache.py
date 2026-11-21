"""Platform cache locations and simple eviction for generated artefacts.

``KARAOKE_CACHE_DIR`` overrides the base directory (useful for tests and for
users who keep caches on another drive). Otherwise the usual per-platform
location is used, so the app no longer writes ``~/Library/Caches`` on Linux or
Windows.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

APP_NAME = "KaraokeMaker"
_PRUNE_LOCK = threading.Lock()


def cache_root(*parts: str) -> Path:
    override = os.environ.get("KARAOKE_CACHE_DIR", "").strip()
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / APP_NAME
    elif os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        base = Path(local) / APP_NAME / "Cache"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "karaokemaker"
    return base.joinpath(*parts)


def _stat_mtime(entry: Path) -> float | None:
    try:
        return entry.stat().st_mtime
    except OSError:
        return None


def prune(directory: Path, *, max_files: int, max_bytes: int | None = None) -> None:
    """Delete the oldest files until ``max_files`` / ``max_bytes`` hold.

    Best effort: cache cleanup must never break a render, so every OS error is
    swallowed. Files can also disappear between listing and stat while another
    render prunes the same directory; those entries are simply dropped.
    """
    with _PRUNE_LOCK:
        try:
            entries = [
                entry
                for entry in Path(directory).iterdir()
                if entry.is_file() and not entry.name.startswith(".")
            ]
        except OSError:
            return
        mtimes: dict[Path, float] = {}
        for entry in entries:
            mtime = _stat_mtime(entry)
            if mtime is not None:
                mtimes[entry] = mtime
        entries = list(mtimes)
        entries.sort(key=lambda entry: mtimes[entry], reverse=True)
        kept = 0
        total = 0
        for entry in entries:
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            too_many = kept >= max_files
            too_large = max_bytes is not None and total + size > max_bytes
            if too_many or too_large:
                try:
                    entry.unlink()
                except OSError:
                    pass
                continue
            kept += 1
            total += size

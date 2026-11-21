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
import time
from pathlib import Path

APP_NAME = "KaraokeMaker"
_PRUNE_LOCK = threading.Lock()
# Pinned cache files are useful while a project is active. After this age the
# marker no longer protects the file, so an abandoned project cannot grow the
# cache without bound.
PIN_MAX_AGE_SECONDS = 60 * 86400


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


def data_root(*parts: str) -> Path:
    """Stable app data (assets) that must not be pruned like a cache."""
    override = os.environ.get("KARAOKE_DATA_DIR", "").strip()
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        base = Path(appdata) / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "karaokemaker"
    return base.joinpath(*parts)


def _stat_mtime(entry: Path) -> float | None:
    try:
        return entry.stat().st_mtime
    except OSError:
        return None


def _pin_marker(entry: Path) -> Path:
    return entry.with_name(f".{entry.name}.pin")


def _is_pinned(entry: Path) -> bool:
    marker = _pin_marker(entry)
    try:
        return marker.exists() and time.time() - marker.stat().st_mtime < PIN_MAX_AGE_SECONDS
    except OSError:
        return False


def prune(
    directory: Path,
    *,
    max_files: int,
    max_bytes: int | None = None,
    min_age_seconds: float = 0.0,
) -> None:
    """Delete the oldest files until ``max_files`` / ``max_bytes`` hold.

    A file with a fresh ``.<name>.pin`` sidecar is pinned: it is always kept and
    still counts toward the limits, so a pinned segment survives even when it is
    older than the newest unpinned files. Markers older than
    ``PIN_MAX_AGE_SECONDS`` no longer protect the file. Files newer than
    ``min_age_seconds`` are never deleted, which protects a clip that is still
    being materialised by another thread. Pin sidecars themselves are hidden
    files and are never listed or deleted here.
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
        cutoff = time.time() - max(0.0, float(min_age_seconds))
        # Pinned files are kept unconditionally and counted first, so the
        # remaining slots go to the newest unpinned files.
        pinned: list[Path] = []
        unpinned: list[Path] = []
        for entry in entries:
            (pinned if _is_pinned(entry) else unpinned).append(entry)
        kept = 0
        total = 0
        for entry in pinned:
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            kept += 1
            total += size
        for entry in unpinned:
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            too_young = mtimes.get(entry, 0.0) > cutoff
            too_many = kept >= max_files
            too_large = max_bytes is not None and total + size > max_bytes
            if (too_many or too_large) and not too_young:
                try:
                    entry.unlink()
                    _pin_marker(entry).unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            kept += 1
            total += size

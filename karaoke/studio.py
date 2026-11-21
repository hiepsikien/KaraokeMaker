"""AI Studio preview renders (P1c).

The Studio tab is an independent workspace: it can analyze a still image and
render a short motion loop without saving anything into a karaoke project. The
loop itself is produced by :func:`karaoke.living.render_motion_loop`; this
module only validates the request and gives each preview a stable cache id.

Previews live in ``~/Library/Caches/KaraokeMaker/studio`` and are keyed by
``sha256(image_hash|plan_hash|duration|profile_id)``. The filename is the id, so
the HTTP layer can serve a preview without keeping server-side state.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from pathlib import Path

from karaoke.cache import cache_root, prune
from karaoke.living import plan_has_motion, render_motion_loop
from karaoke.profiles import get_profile

CACHE_ROOT = cache_root("studio")
PREVIEW_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MIN_DURATION = 0.5
MAX_DURATION = 30.0
# The Studio is a preview surface; full-resolution export stays in Karaoke.
# 640 px keeps a 4 s multi-effect preview well under the proxy timeout.
PREVIEW_MAX_SIDE = 640
PREVIEW_VERSION = "preview_v26"
# Serialize preview renders in this process. Two simultaneous 1080p renders
# otherwise fight over CPU and can race on cache materialization.
_PREVIEW_LOCK = threading.Lock()


class StudioError(Exception):
    """Raised for invalid Studio preview requests."""


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _plan_hash(plan: dict) -> str:
    try:
        payload = json.dumps(plan, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise StudioError("Kế hoạch hiệu ứng không phải JSON hợp lệ.") from error
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_duration(duration) -> float:
    try:
        seconds = float(duration)
    except (TypeError, ValueError) as error:
        raise StudioError("Thời lượng preview phải là số giây.") from error
    if not math.isfinite(seconds) or seconds < MIN_DURATION or seconds > MAX_DURATION:
        raise StudioError(
            f"Thời lượng preview phải trong khoảng {MIN_DURATION:g}–{MAX_DURATION:g} giây."
        )
    return seconds


def _preview_size(profile) -> tuple[int, int]:
    """Scale a profile down so its longest side is at most PREVIEW_MAX_SIDE."""
    scale = min(1.0, PREVIEW_MAX_SIDE / max(profile.width, profile.height))
    width = max(2, int(profile.width * scale) // 2 * 2)
    height = max(2, int(profile.height * scale) // 2 * 2)
    return width, height


def is_preview_id(preview_id: str) -> bool:
    return bool(PREVIEW_ID_PATTERN.fullmatch(str(preview_id or "")))


def cache_path(preview_id: str) -> Path:
    """Return the cache file for a preview id; reject anything but 64 hex chars."""
    if not is_preview_id(preview_id):
        raise StudioError("Mã preview không hợp lệ.")
    return CACHE_ROOT / f"{preview_id}.mp4"


def render_preview(
    image_path: Path,
    plan: dict,
    *,
    duration: float,
    output_profile: str,
    full_resolution: bool = False,
    progress=None,
) -> dict:
    """Render a cached preview loop for one image and effect plan.

    ``full_resolution=False`` keeps the fast 640 px preview. ``True`` renders at
    the selected profile's real resolution (for the 16 s standard preview); the
    HTTP layer should run that as a background job.

    Returns ``{"id", "path", "duration", "width", "height"}``. Raises
    :class:`StudioError` for invalid input; filesystem/FFmpeg failures propagate
    as their native exceptions.
    """
    if not isinstance(plan, dict):
        raise StudioError("Kế hoạch hiệu ứng phải là một object JSON.")
    items = plan.get("items")
    if not isinstance(items, list):
        raise StudioError("Kế hoạch hiệu ứng thiếu danh sách items.")
    if not plan_has_motion(plan):
        raise StudioError(
            "Kế hoạch chưa có hiệu ứng nào render được "
            "(mưa/khói/gió/sáng/tuyết/nước/lửa/tàn lửa/chim/tia nắng/sương mù)."
        )

    seconds = _validate_duration(duration)
    profile = get_profile(output_profile)

    source = Path(image_path).expanduser()
    if not source.is_file():
        raise StudioError(f"Không thấy ảnh: {source}")

    image_hash = _file_hash(source)
    plan_hash = _plan_hash(plan)
    duration_key = f"{seconds:g}"
    if full_resolution:
        preview_width, preview_height = profile.width, profile.height
    else:
        preview_width, preview_height = _preview_size(profile)
    preview_id = hashlib.sha256(
        f"{image_hash}|{plan_hash}|{duration_key}|{profile.id}|"
        f"{PREVIEW_VERSION}|{preview_width}x{preview_height}".encode("utf-8")
    ).hexdigest()

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    destination = CACHE_ROOT / f"{preview_id}.mp4"

    with _PREVIEW_LOCK:
        result = render_motion_loop(
            source,
            plan,
            destination,
            duration=seconds,
            width=preview_width,
            height=preview_height,
            fps=profile.fps,
            fit_mode=profile.fit_mode,
            # Studio preview has no lyric strip. A zero-height strip explicitly
            # disables masking; None would fall back to the legacy bottom mask.
            strip_rect=(0, 0, 0, 0),
            progress=progress,
        )
    if result is None:
        raise StudioError(
            "Kế hoạch chưa có hiệu ứng nào render được "
            "(mưa/khói/gió/sáng/tuyết/nước/lửa/tàn lửa/chim/tia nắng/sương mù)."
        )

    if not destination.is_file() or destination.stat().st_size <= 0:
        raise StudioError("Không tạo được file preview.")

    prune(CACHE_ROOT, max_files=100, max_bytes=1_500_000_000)
    return {
        "id": preview_id,
        "path": str(destination),
        "duration": seconds,
        "width": preview_width,
        "height": preview_height,
    }

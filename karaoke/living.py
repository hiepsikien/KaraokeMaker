"""Procedural motion loops for the "living still" effect plans.

P0 shipped rain only: three depth layers, a loop that is seamless by
construction (``speed * duration`` is an integer number of vertical spans), an
effect mask built from the plan (dry regions and protect boxes), and an
H.264/yuv420p/faststart encode streamed through FFmpeg. Effects render before
the lyric overlay, so they may animate behind the lyric strip.

P1a adds three micro-motion recipes that share the same encode/mask/cache
pipeline:

* ``steam`` - soft sprites rising from an object opening, loop-quantized. Smoke
  boxes (including fire fallbacks) use their sampled local colour, a darkness-
  weighted mask, and a full-plume rise so the motion matches painted smoke.
* ``wind``  - a local cloth warp of sails/flags/curtains pinned at their pivot,
  or a small rotation for other hanging objects.
* ``light`` - a very subtle brightness/glow pulse, emitted on bright pixels.

P2a adds two more recipes on the same pipeline:

* ``snow``  - slow flakes falling through the rain outdoor/dry mask, with a
  gentle periodic sideways drift. Like rain it never touches the lyric strip,
  protect boxes, or dry/indoor areas.
* ``water`` - W1 surface shimmer: a per-row horizontal sine displacement plus a
  per-column vertical bob and soft specular highlights inside liquid/water
  regions (or objects advertising a water/liquid affordance). No surf, spray,
  or caustics.

P2b adds two atmospheric recipes:

* ``fire`` - a warm, low-frequency additive glow/flicker around fire objects
  (or a ``fire`` region when the inventory has one). Subtle, loop-quantized.
* ``fog``  - a soft low-alpha noise layer drifting slowly over a fog/mist/ground
  region (or the lower half of the frame when no region matches). Subtle blend,
  never over protect boxes or the lyric strip.

Rain behaviour is preserved exactly through :func:`render_rain_loop`, which is
now a thin compatibility wrapper around :func:`render_motion_loop` and stays
rain-only for P0 callers.

Only Pillow + numpy are used. The demo prototype in /tmp used OpenCV; this
module deliberately avoids that dependency because .venv-karaoke does not
ship it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from karaoke.cache import cache_root, prune
from karaoke.media import HEIGHT, WIDTH, MediaError, fit_image

CACHE_ROOT = cache_root("living")
STRIP_HEIGHT_1080 = 280
# count at 1080p, speed px/s at 1080p, streak length px, alpha 0..255, blur px
RAIN_LAYERS = (
    (320, 900.0, 24.0, 42, 0.8),
    (260, 1200.0, 36.0, 60, 0.6),
    (180, 1500.0, 50.0, 82, 0.4),
)
# Rain catches a little of the ambient light instead of glowing warm; the
# cool-neutral tint keeps it separate from fire embers.
RAIN_COLOR = (206, 218, 230)
SLANT = 0.12  # horizontal drift per vertical pixel
DEFAULT_INTENSITY = 0.55
DEFAULT_LIGHT_INTENSITY = 0.3
DEFAULT_WATER_INTENSITY = 0.45
DEFAULT_FIRE_INTENSITY = 0.5
DEFAULT_FOG_INTENSITY = 0.45
FIRE_RGB = (1.0, 0.57, 0.24)
FOG_RGB = (0.84, 0.88, 0.92)

# Snow layers: count at 1080p, fall speed px/s at 1080p, flake radius px,
# alpha 0..255, blur px. Flakes drift sideways with a loop-quantized sine.
SNOW_LAYERS = (
    (240, 20.0, 1.1, 80, 0.9),
    (180, 34.0, 1.9, 120, 0.6),
    (120, 52.0, 3.1, 165, 0.4),
)
SNOW_COLOR = (250, 252, 255)

# Water regions match on id/label substrings; objects match on affordances.
WATER_REGION_HINTS = (
    "liquid",
    "water",
    "sea",
    "river",
    "ocean",
    "lake",
    "pond",
    "stream",
    "nước",
    "biển",
    "sông",
    "hồ",
    "suối",
)
WATER_AFFORDANCES = ("water", "liquid")
WATER_LABELS = (
    "water",
    "sea",
    "ocean",
    "liquid",
    "river",
    "lake",
    "pond",
    "stream",
    "nước",
    "biển",
    "sông",
    "hồ",
    "suối",
)

# Fire targets match on affordances/labels; a `fire` region may substitute.
FIRE_REGION_HINTS = ("fire", "flame", "hearth", "burn", "ember", "lửa", "cháy")
FIRE_AFFORDANCES = (
    "fire",
    "flame",
    "burn",
    "hearth",
    "ember",
    "emit_light",
    "emit light",
    "emissive",
    "glow",
)
FIRE_LABELS = (
    "fire",
    "flame",
    "campfire",
    "bonfire",
    "hearth",
    "brazier",
    "torch",
    "candle",
    "burn",
    "emit light",
    "lửa",
    "đuốc",
    "lò sưởi",
)
# Fog/mist regions; plain "low" is token-matched to avoid matching "yellow".
# Vietnamese "sương" alone stays steam, so only the "sương mù" phrase matches.
FOG_REGION_HINTS = ("fog", "mist", "sương mù")
FOG_REGION_TOKENS = ("ground", "low", "lower", "bottom", "floor", "nền", "đất")

# Birds/flocks and the sun are only targets; the effects themselves are gated
# by explicit prompt intent, never enabled just because the scene contains them.
BIRD_AFFORDANCES = ("bird", "flock", "fly", "flying")
BIRD_LABELS = ("bird", "birds", "flock", "chim", "đàn chim")
SUN_AFFORDANCES = ("sun", "sunlight", "light_source", "light source")
SUN_LABELS = (
    "sun",
    "sunlight",
    "sun light",
    "sunray",
    "sun rays",
    "god rays",
    "light source",
    "nắng",
    "mặt trời",
)

# Every renderer recipe supported by this module. Order is the compositing
# order (base -> rain -> snow -> water -> steam -> wind -> birds -> fire ->
# embers -> sunrays -> fog -> light).
SUPPORTED_EFFECTS = (
    "rain",
    "snow",
    "water",
    "steam",
    "wind",
    "birds",
    "fire",
    "embers",
    "sunrays",
    "fog",
    "light",
)
EFFECT_LABELS = {
    "rain": "mưa",
    "snow": "tuyết",
    "water": "nước",
    "steam": "khói",
    "wind": "gió",
    "birds": "chim",
    "fire": "lửa",
    "embers": "tàn lửa",
    "sunrays": "tia nắng",
    "fog": "sương mù",
    "light": "sáng",
}
# Bump whenever the procedural output changes so old caches are not reused.
# v3: adds snow flakes and water W1 shimmer.
# v4: adds fire glow/flicker and slow fog drift.
# v5: first AI Studio / inventory-v3 pass.
# v6: full-surface water bob, smoke-aware plumes, cloth/flag sway, rain streaks.
# v7: object-aware water/smoke masks.
# v17: sparse box-free smoke, birds, sunrays, platform cache and protect fixes.
# v31: visual plan hash ignores non-visual params (audio, legacy loop_duration).
MOTION_CACHE_VERSION = 33

# Shared x264 parameters for motion clips. They are part of the cache key via
# ``ENCODER_VERSION``: bump it whenever any value below changes so old clips are
# not silently reused with a different encode profile.
ENCODER_VERSION = 2
X264_PRESET = "veryfast"
X264_CRF = 18
X264_GOP = 60
X264_PIX_FMT = "yuv420p"
# B-frames make concat-copy timestamps unreliable when segments have different
# durations (cover, tail slots). Disable them so the demuxer can copy safely.
X264_BFRAMES = 0

STEAM_COLOR = (238.0, 241.0, 244.0)
STEAM_AFFORDANCES = (
    "steam",
    "steam_source",
    "smoke",
    "vapor",
    "vapour",
    "container",
    "pot",
    "bowl",
    "cup",
    "kettle",
    "opening",
    "food",
    "soup",
)
STEAM_LABELS = (
    "pot",
    "bowl",
    "cup",
    "kettle",
    "food",
    "soup",
    "ramen",
    "pho",
    "nồi",
    "bát",
    "tô",
    "chén",
    "ấm",
    "khói",
    "hơi",
)
# Smoke behaves differently from kitchen steam: its box already contains the
# plume, it is dark and turbulent, and it must not be forced to an "opening".
SMOKE_AFFORDANCES = ("smoke", "exhaust", "plume", "soot")
SMOKE_LABELS = ("smoke", "plume", "khói", "khói lửa", "khói đen", "cột khói")
WIND_AFFORDANCES = (
    "hanging",
    "hang",
    "swayable",
    "sway",
    "cloth",
    "curtain",
    "wind_movable",
    "wind",
    "flutter",
    "flowing",
    "loose",
    "flag",
    "banner",
    "noren",
    "lantern",
    "plant",
)
WIND_LABELS = (
    "lantern",
    "curtain",
    "flag",
    "banner",
    "pennant",
    "ensign",
    "sail",
    "cloth",
    "noren",
    "plant",
    "tree",
    "headband",
    "bandana",
    "scarf",
    "turban",
    "hair",
    "ponytail",
    "braid",
    "strand",
    "coat",
    "robe",
    "cape",
    "cloak",
    "sleeve",
    "hem",
    "tunic",
    "dress",
    "sash",
    "ribbon",
    "fabric",
    "garment",
    "lồng đèn",
    "đèn lồng",
    "rèm",
    "cờ",
    "buồm",
    "cánh buồm",
    "cây",
    "khăn",
    "băng đô",
    "tóc",
    "lọn tóc",
    "đuôi ngựa",
    "vạt áo",
    "tà áo",
    "áo choàng",
    "ống tay",
    "dải lụa",
)
CLOTH_HINTS = (
    "cloth",
    "curtain",
    "flag",
    "banner",
    "pennant",
    "ensign",
    "sail",
    "noren",
    "headband",
    "bandana",
    "scarf",
    "turban",
    "hair",
    "ponytail",
    "braid",
    "strand",
    "coat",
    "robe",
    "cape",
    "cloak",
    "sleeve",
    "hem",
    "tunic",
    "dress",
    "sash",
    "ribbon",
    "fabric",
    "garment",
    "rèm",
    "cờ",
    "buồm",
    "vải",
    "khăn",
    "băng đô",
    "tóc",
    "lọn tóc",
    "vạt áo",
    "tà áo",
    "áo choàng",
    "ống tay",
    "dải lụa",
)


def _effect_items(plan: dict | None, effect: str) -> list[dict]:
    if not isinstance(plan, dict):
        return []
    wanted = str(effect or "").strip().casefold()
    items: list[dict] = []
    for item in plan.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("effect") or "").strip().casefold() == wanted:
            items.append(item)
    return items


def _rain_item(plan: dict | None) -> dict | None:
    items = _effect_items(plan, "rain")
    return items[0] if items else None


def plan_has_rain(plan: dict | None) -> bool:
    """True when the effect plan asks for a rain item."""
    return _rain_item(plan) is not None


def _active_effects(plan: dict | None) -> tuple[str, ...]:
    """Supported recipes present in the plan, in compositing order."""
    if not isinstance(plan, dict):
        return ()
    return tuple(effect for effect in SUPPORTED_EFFECTS if _effect_items(plan, effect))


def plan_has_motion(plan: dict | None) -> bool:
    """True for any supported motion item (rain/snow/water/steam/wind/birds/fire/
    embers/sunrays/fog/light).

    ``water`` counts here even when the plan has no liquid region: the renderer
    logs and falls back to a static slide, while Studio can still validate the
    request shape.
    """
    return bool(_active_effects(plan))


def _positive_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} phải là số nguyên dương.")
    if not math.isfinite(float(value)) or float(value) != int(value):
        raise ValueError(f"{name} phải là số nguyên dương.")
    number = int(value)
    if number <= 0:
        raise ValueError(f"{name} phải là số nguyên dương.")
    return number


def _validate(duration, width, height, fps) -> tuple[float, int, int, int]:
    try:
        seconds = float(duration)
    except (TypeError, ValueError) as error:
        raise ValueError("duration phải là số giây lớn hơn 0.") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("duration phải là số giây lớn hơn 0.")
    w = _positive_int(width, "width")
    h = _positive_int(height, "height")
    if w % 2 or h % 2:
        raise ValueError("width và height phải là số chẵn (yuv420p).")
    rate = _positive_int(fps, "fps")
    return seconds, w, h, rate


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


NON_VISUAL_PLAN_PARAMS = frozenset({"audio", "loop_duration"})


def _visual_plan(plan: dict) -> dict:
    """Return a copy of the plan with non-visual params removed.

    ``audio`` only drives the mux stage and ``loop_duration`` is legacy metadata;
    changing either must not invalidate a visual clip or change its seed.
    """
    if not isinstance(plan, dict):
        return {}
    data = dict(plan)
    items: list[dict] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        params = entry.get("params")
        if isinstance(params, dict):
            entry["params"] = {
                key: value
                for key, value in params.items()
                if key not in NON_VISUAL_PLAN_PARAMS
            }
        items.append(entry)
    data["items"] = items
    return data


def visual_plan_hash(plan: dict) -> str:
    """Hash only the parts of the plan that affect rendered pixels."""
    payload = json.dumps(_visual_plan(plan), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _plan_hash(plan: dict) -> str:
    """Backward-compatible alias for the visual-only plan hash."""
    return visual_plan_hash(plan)


def _cache_strip_key(strip_rect: tuple[int, int, int, int] | None) -> str:
    if strip_rect is None:
        return "-"
    return ",".join(str(value) for value in strip_rect)


def _cache_fit_mode(fit_mode: str | None) -> str:
    mode = str(fit_mode or "contain").strip().lower()
    return mode if mode in {"contain", "cover"} else "contain"


def motion_cache_key(
    *,
    image_hash: str,
    plan: dict,
    duration: float,
    width: int,
    height: int,
    fps: int,
    fit_mode: str = "contain",
    strip_rect: tuple[int, int, int, int] | None = None,
    effects: frozenset[str] | set[str] | None = None,
) -> str:
    """Stable cache key for one rendered motion clip.

    Mirrors the key that :func:`_render_loop` builds (visual plan hash, duration,
    frame size, fps, selected effects, fit mode and strip), plus the encoder
    version. ``effects`` defaults to all active effects in the plan; wrappers
    such as :func:`render_rain_loop` pass a narrower set so a rain-only clip is
    never reused for a plan that also asks for snow.
    """
    seconds, out_width, out_height, rate = _validate(duration, width, height, fps)
    selected = effects if effects is not None else _active_effects(plan)
    selection = ",".join(effect for effect in SUPPORTED_EFFECTS if effect in selected)
    mode = _cache_fit_mode(fit_mode)
    strip_key = _cache_strip_key(strip_rect)
    return hashlib.sha256(
        (
            f"v{MOTION_CACHE_VERSION}|{image_hash}|{visual_plan_hash(plan)}|{seconds}|"
            f"{out_width}|{out_height}|{rate}|{selection}|{mode}|{strip_key}|"
            f"enc{ENCODER_VERSION}"
        ).encode("utf-8")
    ).hexdigest()


def motion_cache_path(**kwargs) -> Path:
    """Cache file for a motion clip, keyed by :func:`motion_cache_key`."""
    return CACHE_ROOT / f"{motion_cache_key(**kwargs)}.mp4"


# Process-local single-flight locks, one per cache key. Two threads rendering the
# same clip must not run ``_encode`` twice; different keys still render in
# parallel. Cross-process locking is intentionally out of scope.
_MOTION_LOCKS: dict[str, threading.Lock] = {}
_MOTION_LOCKS_GUARD = threading.Lock()


def _motion_lock(key: str) -> threading.Lock:
    with _MOTION_LOCKS_GUARD:
        lock = _MOTION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _MOTION_LOCKS[key] = lock
        return lock


def _pin_marker(path: Path) -> Path:
    path = Path(path)
    return path.with_name(f".{path.name}.pin")


def pin_cache_file(path: Path) -> None:
    """Pin a cache file so :func:`karaoke.cache.prune` keeps it.

    The marker lives next to the file as ``.<filename>.pin`` and is written
    atomically (temporary file + ``os.replace``) so a concurrent prune never
    sees a half-written sidecar.
    """
    path = Path(path)
    marker = _pin_marker(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(
        f".{marker.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_bytes(b"")
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def unpin_cache_file(path: Path) -> None:
    """Remove the ``.<filename>.pin`` sidecar for a cache file, if present."""
    try:
        _pin_marker(path).unlink()
    except OSError:
        pass


def _finite(value, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _positive(value, fallback: float) -> float:
    number = _finite(value, fallback)
    return number if number > 0 else fallback


def _fit_transform(
    plan: dict,
    source_size: tuple[int, int],
    frame_size: tuple[int, int] | None = None,
    fit_mode: str | None = None,
) -> dict:
    """Recompute the original→frame mapping for the actual output frame.

    Scale and offsets are always recomputed from the real source size and output
    frame. The plan's stored ``fit_transform`` only supplies defaults for the
    frame size/mode when the caller does not pass them, so a landscape plan can
    never distort a portrait export.
    """
    source_width = max(1.0, float(source_size[0]))
    source_height = max(1.0, float(source_size[1]))
    raw = plan.get("fit_transform") if isinstance(plan, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    if frame_size is not None:
        frame_width = max(1.0, float(frame_size[0]))
        frame_height = max(1.0, float(frame_size[1]))
    else:
        frame_width = _positive(raw.get("frame_width"), float(WIDTH))
        frame_height = _positive(raw.get("frame_height"), float(HEIGHT))
    mode = str(
        fit_mode if fit_mode is not None else (raw.get("mode") or "contain")
    ).strip().lower()
    if mode == "cover":
        scale = max(frame_width / source_width, frame_height / source_height)
    else:
        scale = min(frame_width / source_width, frame_height / source_height)
    offset_x = (frame_width - source_width * scale) / 2.0
    offset_y = (frame_height - source_height * scale) / 2.0
    return {
        "source_width": source_width,
        "source_height": source_height,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "scale": scale,
        "offset_x": offset_x,
        "offset_y": offset_y,
    }


def _points(value) -> list[tuple[float, float]] | None:
    """Accept [{'x','y'}...] or [[x, y]...] and return at least 3 points."""
    if not isinstance(value, list):
        return None
    points: list[tuple[float, float]] = []
    for entry in value:
        if isinstance(entry, dict):
            try:
                points.append((float(entry["x"]), float(entry["y"])))
            except (KeyError, TypeError, ValueError):
                return None
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            try:
                points.append((float(entry[0]), float(entry[1])))
            except (TypeError, ValueError):
                return None
        else:
            return None
    return points if len(points) >= 3 else None


def _box_points(value) -> list[tuple[float, float]] | None:
    if not isinstance(value, dict):
        return None
    try:
        x1 = float(value["x1"])
        y1 = float(value["y1"])
        x2 = float(value["x2"])
        y2 = float(value["y2"])
    except (KeyError, TypeError, ValueError):
        return None
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def _shape_points(entry) -> list[tuple[float, float]] | None:
    if isinstance(entry, list):
        return _points(entry)
    if not isinstance(entry, dict):
        return None
    for key in ("polygon", "points", "vertices"):
        points = _points(entry.get(key))
        if points:
            return points
    for key in ("box", "rect"):
        points = _box_points(entry.get(key))
        if points:
            return points
    return _box_points(entry)


def _dry_shapes(plan: dict) -> list[list[tuple[float, float]]]:
    shapes: list[list[tuple[float, float]]] = []
    raw = plan.get("dry") if isinstance(plan, dict) else None
    if isinstance(raw, dict):
        points = _shape_points(raw)
        if points:
            shapes.append(points)
    elif isinstance(raw, list):
        if raw and all(isinstance(entry, dict) and "x" in entry and "y" in entry for entry in raw):
            points = _points(raw)  # a bare polygon
            if points:
                shapes.append(points)
        else:
            for entry in raw:
                points = _shape_points(entry)
                if points:
                    shapes.append(points)
    regions = plan.get("regions") if isinstance(plan, dict) else None
    if isinstance(regions, list):
        for entry in regions:
            if not isinstance(entry, dict):
                continue
            identity = " ".join(
                str(entry.get(key) or "") for key in ("id", "label", "kind", "type")
            ).casefold()
            if entry.get("dry") is True or any(word in identity for word in ("dry", "indoor", "sheltered")):
                points = _shape_points(entry)
                if points:
                    shapes.append(points)
    return shapes


def _outdoor_shapes(plan: dict) -> list[list[tuple[float, float]]]:
    """Regions where rain is allowed. When present, rain starts masked out."""
    shapes: list[list[tuple[float, float]]] = []
    raw = plan.get("outdoor") if isinstance(plan, dict) else None
    if isinstance(raw, dict):
        points = _shape_points(raw)
        if points:
            shapes.append(points)
    elif isinstance(raw, list):
        for entry in raw:
            points = _shape_points(entry)
            if points:
                shapes.append(points)
    regions = plan.get("regions") if isinstance(plan, dict) else None
    if isinstance(regions, list):
        for entry in regions:
            if not isinstance(entry, dict):
                continue
            identity = " ".join(
                str(entry.get(key) or "") for key in ("id", "label", "kind", "type")
            ).casefold()
            if any(word in identity for word in ("outdoor", "outside", "open air", "open_air")):
                points = _shape_points(entry)
                if points:
                    shapes.append(points)
    return shapes


def _protect_shapes(plan: dict) -> list[list[tuple[float, float]]]:
    shapes: list[list[tuple[float, float]]] = []
    protect = plan.get("protect") if isinstance(plan, dict) else None
    if isinstance(protect, list):
        for entry in protect:
            points = _shape_points(entry)
            if points:
                shapes.append(points)
    return shapes


def _map_point(point: tuple[float, float], transform: dict, out_width: int, out_height: int) -> tuple[float, float]:
    x, y = point
    frame_x = x * transform["source_width"] * transform["scale"] + transform["offset_x"]
    frame_y = y * transform["source_height"] * transform["scale"] + transform["offset_y"]
    return (
        frame_x / transform["frame_width"] * out_width,
        frame_y / transform["frame_height"] * out_height,
    )


def _zero_strip(
    draw: ImageDraw.ImageDraw,
    plan: dict,
    width: int,
    height: int,
    big_width: int,
    big_height: int,
    scale: int,
    strip_rect: tuple[int, int, int, int] | None = None,
) -> tuple[int, list[tuple[int, int]]]:
    """Paint the lyric strip (and any explicit ``strip_rect``) solid zero.

    When ``strip_rect`` is supplied it is already in output pixels for the
    selected profile, so it is used exactly (no 1080p rescaling). Otherwise the
    legacy behaviour (bottom 280 px at 1080p, plan strip_rect scaled from 1080p)
    is preserved. The strip starts at a macroblock-aligned row so H.264 motion
    in a block that straddles the boundary cannot bleed into the lyric area.
    Returns ``(protected_top, extra_zero_ranges)`` for the post-resize guard.
    """
    block = 16  # H.264 macroblock is 16 px regardless of output size
    extra_zero: list[tuple[int, int]] = []

    if strip_rect is not None:
        try:
            x0, y0, strip_w, strip_h = (float(value) for value in strip_rect)
        except (TypeError, ValueError):
            x0 = y0 = strip_w = strip_h = 0.0
        # Explicitly zero-height strip means "preview: do not mask the bottom".
        if strip_h <= 0:
            return height, []
        strip_top = max(0, int(round(y0)))
        protected_top = (strip_top // block) * block
        draw.rectangle([0, protected_top * scale, big_width, big_height], fill=0)
        extra_zero.append((protected_top, height))
        if strip_w > 0 and strip_h > 0:
            left = max(0, int(round(x0)))
            top = max(0, int(round(y0)))
            right = min(width, int(round(x0 + strip_w)))
            bottom = min(height, int(round(y0 + strip_h)))
            if right > left and bottom > top:
                draw.rectangle(
                    [left * scale, top * scale, right * scale, bottom * scale],
                    fill=0,
                )
        return protected_top, extra_zero

    strip_height = max(1, int(round(STRIP_HEIGHT_1080 * height / HEIGHT)))
    strip_top = height - strip_height
    protected_top = (strip_top // block) * block
    draw.rectangle([0, protected_top * scale, big_width, big_height], fill=0)
    extra_zero.append((protected_top, height))

    raw_strip = plan.get("strip_rect") if isinstance(plan, dict) else None
    if isinstance(raw_strip, (list, tuple)) and len(raw_strip) == 4:
        try:
            x0, y0, strip_w, strip_h = (float(value) for value in raw_strip)
        except (TypeError, ValueError):
            x0 = y0 = strip_w = strip_h = 0.0
        if strip_w > 0 and strip_h > 0:
            left = int(round(x0 / WIDTH * big_width))
            top = int(round(y0 / HEIGHT * big_height))
            right = int(round((x0 + strip_w) / WIDTH * big_width))
            bottom = int(round((y0 + strip_h) / HEIGHT * big_height))
            draw.rectangle([left, top, right, bottom], fill=0)
            block_top = max(0, int(round(y0 / HEIGHT * height)))
            block_top = (block_top // block) * block
            extra_zero.append((block_top, min(height, int(round((y0 + strip_h) / HEIGHT * height)))))
    return protected_top, extra_zero


def _force_zero(array: np.ndarray, protected_top: int, extra_zero: list[tuple[int, int]]) -> None:
    # BOX downsampling can leave a fractional row at the boundary; force zero.
    array[protected_top:, :] = 0.0
    for top, bottom in extra_zero:
        array[max(0, top) : max(0, bottom), :] = 0.0


def _build_mask(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    strip_rect: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Float32 0..1 effect mask. Lyric strip is exactly zero."""
    scale = 2
    big_width, big_height = width * scale, height * scale
    outdoor = _outdoor_shapes(plan)
    mask = Image.new("L", (big_width, big_height), 0 if outdoor else 255)
    draw = ImageDraw.Draw(mask)

    # When the inventory gives an outdoor/rain-allowed region, rain starts only
    # inside it; otherwise the whole frame is allowed (subject to dry/protect).
    for points in outdoor:
        mapped = [_map_point(point, transform, big_width, big_height) for point in points]
        draw.polygon(mapped, fill=255)

    for points in _dry_shapes(plan) + _protect_shapes(plan):
        mapped = [_map_point(point, transform, big_width, big_height) for point in points]
        draw.polygon(mapped, fill=0)

    protected_top, extra_zero = _zero_strip(
        draw, plan, width, height, big_width, big_height, scale, strip_rect
    )
    mask = mask.resize((width, height), Image.Resampling.BOX)
    array = np.asarray(mask, dtype=np.float32) / 255.0
    _force_zero(array, protected_top, extra_zero)
    return array


def _build_protect_mask(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    strip_rect: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Float32 0..1 mask that is zero on protect boxes and the lyric strip.

    Steam/wind/light are not subject to the rain ``dry``/``outdoor`` regions
    (steam usually starts indoors), but they must never cover protected faces,
    text, or the lyric strip.
    """
    scale = 2
    big_width, big_height = width * scale, height * scale
    mask = Image.new("L", (big_width, big_height), 255)
    draw = ImageDraw.Draw(mask)
    for points in _protect_shapes(plan):
        mapped = [_map_point(point, transform, big_width, big_height) for point in points]
        draw.polygon(mapped, fill=0)
    protected_top, extra_zero = _zero_strip(
        draw, plan, width, height, big_width, big_height, scale, strip_rect
    )
    mask = mask.resize((width, height), Image.Resampling.BOX)
    array = np.asarray(mask, dtype=np.float32) / 255.0
    _force_zero(array, protected_top, extra_zero)
    return array


def _make_layers(
    width: int,
    height: int,
    duration: float,
    seed: int,
    density_multiplier: float = 1.0,
    speed_multiplier: float = 1.0,
) -> list[dict]:
    size_scale = height / HEIGHT
    span = height + 200.0 * size_scale
    density = max(0.25, min(1.0, math.sqrt((width * height) / (WIDTH * HEIGHT))))
    rng = np.random.default_rng(seed)
    layers: list[dict] = []
    for count, speed, length, alpha, blur in RAIN_LAYERS:
        particles = max(0, int(round(count * density * density_multiplier)))
        if particles == 0:
            continue
        # Quantize so each particle travels a whole number of spans in the loop.
        cycles = max(
            1,
            int(round(speed * size_scale * duration / span * speed_multiplier)),
        )
        quantized = cycles * span / duration
        xs = rng.uniform(-0.06 * width, 1.06 * width, particles).astype(np.float32)
        ys = rng.uniform(0.0, span, particles).astype(np.float32)
        # Uneven drop lengths and opacities avoid the "parallel scratches" look
        # of a single uniform streak template.
        lengths = (
            max(2.0, length * size_scale) * rng.uniform(0.55, 1.45, particles)
        ).astype(np.float32)
        alphas = (alpha * rng.uniform(0.60, 1.15, particles)).astype(np.float32)
        layers.append(
            {
                "xs": xs,
                "ys": ys,
                "speed": quantized,
                "span": span,
                "lengths": lengths,
                "alphas": alphas,
                "blur": blur * max(0.35, size_scale),
                # A slow integer-cycle gust makes the whole curtain lean
                # together instead of falling in perfectly straight lines.
                "gust": (1.5 + 3.5 * size_scale) * rng.uniform(0.5, 1.0),
                "gust_cycles": max(1, int(round(duration / 4.0))),
                "gust_phase": float(rng.uniform(0.0, 2.0 * math.pi)),
            }
        )
    return layers


def _draw_layer(
    draw: ImageDraw.ImageDraw,
    layer: dict,
    t: float,
    u: float,
    height: int,
) -> None:
    """Draw one rain layer as grayscale intensity into an ``L`` canvas.

    The layer is tinted once with :data:`RAIN_COLOR` after all layers have been
    accumulated, which lets the expensive blur run on one channel instead of
    three (P0.3).
    """
    span = layer["span"]
    size_scale = height / HEIGHT
    ys = np.mod(layer["ys"] + layer["speed"] * t, span) - 100.0 * size_scale
    lengths = layer["lengths"]
    gust = layer["gust"] * math.sin(
        2.0 * math.pi * layer["gust_cycles"] * u + layer["gust_phase"]
    )
    xs = layer["xs"] + SLANT * ys + gust
    visible = (ys < height + 4.0) & (ys + lengths > -4.0)
    for x, y, length, alpha in zip(
        xs[visible], ys[visible], lengths[visible], layer["alphas"][visible]
    ):
        top = (float(x), float(y))
        bottom = (float(x) + SLANT * float(length), float(y) + float(length))
        draw.line([top, bottom], fill=int(round(float(alpha))), width=1)


# --- snow ------------------------------------------------------------------


def _make_snow_layers(
    width: int,
    height: int,
    duration: float,
    seed: int,
    density_multiplier: float = 1.0,
    speed_multiplier: float = 1.0,
) -> list[dict]:
    """Build slow flake layers whose fall and drift both loop seamlessly."""
    size_scale = height / HEIGHT
    span = height + 200.0 * size_scale
    density = max(0.25, min(1.0, math.sqrt((width * height) / (WIDTH * HEIGHT))))
    rng = np.random.default_rng(seed ^ 0x5A0F)
    # Integer sway cycles over the loop keep the sideways drift seamless.
    base_sway = max(1, int(round(duration / 4.0)))
    layers: list[dict] = []
    for count, speed, radius, alpha, blur in SNOW_LAYERS:
        particles = max(0, int(round(count * density * density_multiplier)))
        if particles == 0:
            continue
        # Quantize so every flake travels a whole number of spans in the loop.
        cycles = max(
            1,
            int(round(speed * size_scale * duration / span * speed_multiplier)),
        )
        quantized = cycles * span / duration
        xs = rng.uniform(-0.05 * width, 1.05 * width, particles).astype(np.float32)
        ys = rng.uniform(0.0, span, particles).astype(np.float32)
        radii = (radius * size_scale * rng.uniform(0.55, 1.45, particles)).astype(np.float32)
        drift = (
            max(1.0, (2.5 + radius * 1.4) * size_scale)
            * rng.uniform(0.5, 1.0, particles)
        ).astype(np.float32)
        layers.append(
            {
                "xs": xs,
                "ys": ys,
                "radii": radii,
                "drift": drift,
                "phase": rng.uniform(0.0, 2.0 * math.pi, particles).astype(np.float32),
                "speed": quantized,
                "span": span,
                "sway": rng.integers(base_sway, base_sway + 2, particles).astype(np.float32),
                "alpha": int(alpha),
                "blur": blur * max(0.35, size_scale),
            }
        )
    return layers


def _draw_snow_layer(
    draw: ImageDraw.ImageDraw,
    layer: dict,
    t: float,
    u: float,
    height: int,
) -> None:
    """Draw one snow layer as grayscale intensity into an ``L`` canvas."""
    span = layer["span"]
    ys = np.mod(layer["ys"] + layer["speed"] * t, span) - 100.0 * (height / HEIGHT)
    xs = layer["xs"] + layer["drift"] * np.sin(
        2.0 * math.pi * layer["sway"] * u + layer["phase"]
    )
    radii = layer["radii"]
    intensity = int(round(float(layer["alpha"])))
    visible = (ys + radii > -2.0) & (ys - radii < height + 2.0)
    for x, y, radius in zip(xs[visible], ys[visible], radii[visible]):
        radius = max(0.5, float(radius))
        draw.ellipse(
            [float(x) - radius, float(y) - radius, float(x) + radius, float(y) + radius],
            fill=intensity,
        )


# ---------------------------------------------------------------------------
# Object-backed recipes (steam / wind) and global light pulse


def _norm_box(value) -> tuple[float, float, float, float] | None:
    """Parse a normalized box from named or list form."""
    if isinstance(value, dict):
        try:
            values = [float(value[key]) for key in ("x1", "y1", "x2", "y2")]
        except (KeyError, TypeError, ValueError):
            return None
    elif isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            values = [float(item) for item in value[:4]]
        except (TypeError, ValueError):
            return None
    else:
        return None
    if max(abs(item) for item in values) > 1.0:
        if max(abs(item) for item in values) <= 1000.0:
            values = [item / 1000.0 for item in values]
        else:
            return None
    x1, y1, x2, y2 = (max(0.0, min(1.0, item)) for item in values)
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if x2 - x1 < 1e-6 or y2 - y1 < 1e-6:
        return None
    return x1, y1, x2, y2


def _norm_point(value) -> tuple[float, float] | None:
    if isinstance(value, dict):
        if "x" not in value or "y" not in value:
            return None
        try:
            x, y = float(value["x"]), float(value["y"])
        except (TypeError, ValueError):
            return None
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    else:
        return None
    if max(abs(x), abs(y)) > 1.0:
        if max(abs(x), abs(y)) <= 1000.0:
            x, y = x / 1000.0, y / 1000.0
        else:
            return None
    return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))


def _object_anchor(entry: dict, name: str) -> tuple[float, float] | None:
    anchors = entry.get("anchors")
    value = None
    if isinstance(anchors, dict):
        value = anchors.get(name)
    if value is None:
        value = entry.get(name)
    if value is None:
        value = entry.get(f"{name}_2d")
    return _norm_point(value)


def _plan_objects(plan: dict | None, selected_only: bool = True) -> list[dict]:
    """Read optional ``plan['objects']`` (also tolerates item/inventory copies).

    ``selected_only`` applies the UI's ``selected_object_ids`` filter. Pass
    ``False`` when a caller needs every object (for example, water occluders
    must protect deselected objects too).
    """
    if not isinstance(plan, dict):
        return []
    raw_entries: list = []
    for key in ("objects", "targets"):
        value = plan.get(key)
        if isinstance(value, list) and all(isinstance(entry, dict) for entry in value):
            raw_entries.extend(value)
    inventory = plan.get("inventory")
    if isinstance(inventory, dict) and isinstance(inventory.get("objects"), list):
        raw_entries.extend(entry for entry in inventory["objects"] if isinstance(entry, dict))
    for item in plan.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("objects"), list):
            raw_entries.extend(entry for entry in item["objects"] if isinstance(entry, dict))

    objects: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_entries):
        box = _norm_box(entry.get("box") or entry.get("rect"))
        if box is None:
            continue
        identity = str(entry.get("id") or f"object-{index + 1}")
        if identity in seen:
            continue
        seen.add(identity)
        affordances = entry.get("affordances")
        if isinstance(affordances, str):
            affordances = [part.strip() for part in affordances.split(",")]
        if not isinstance(affordances, list):
            affordances = []
        objects.append(
            {
                "id": identity,
                "label": str(entry.get("label") or ""),
                "box": box,
                "anchors": {
                    "pivot": _object_anchor(entry, "pivot"),
                    "opening": _object_anchor(entry, "opening"),
                },
                "affordances": [str(item).strip().casefold() for item in affordances if str(item).strip()],
                "source": str(entry.get("source") or ""),
            }
        )
    selected = plan.get("selected_object_ids")
    if selected_only and isinstance(selected, list):
        allowed = {str(value) for value in selected if str(value).strip()}
        objects = [entry for entry in objects if entry["id"] in allowed]
    return objects


def _matches_hints(entry: dict, affordance_hints: tuple[str, ...], label_hints: tuple[str, ...]) -> bool:
    """Match affordances on tokens and labels on substrings (avoids 'window' as 'wind')."""
    affordances = [str(value).casefold() for value in entry.get("affordances") or []]
    for affordance in affordances:
        if any(hint in affordance for hint in affordance_hints):
            return True
    label = str(entry.get("label") or "").casefold()
    return any(hint in label for hint in label_hints)


def _target_ids(items: list[dict]) -> set[str]:
    ids: set[str] = set()
    for item in items:
        raw = item.get("target_ids")
        if isinstance(raw, list):
            ids.update(str(value) for value in raw if str(value).strip())
        elif isinstance(raw, str) and raw.strip():
            ids.add(raw.strip())
    return ids


def _items_param(
    items: list[dict], name: str, default: float, low: float, high: float
) -> float:
    values: list[float] = []
    for item in items:
        params = item.get("params")
        if not isinstance(params, dict):
            continue
        raw = params.get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        number = float(raw)
        if math.isfinite(number):
            values.append(number)
    value = max(values) if values else default
    return min(high, max(low, value))


def _items_intensity(items: list[dict], default: float) -> float:
    return _items_param(items, "intensity", default, 0.0, 1.0)


def _items_speed(items: list[dict], default: float = 1.0) -> float:
    """Speed multiplier; 1.0 = the original loop timing. Zero freezes wind."""
    return _items_param(items, "speed", default, 0.0, 2.0)


def _items_density(items: list[dict], default: float = 1.0) -> float:
    """Particle-count multiplier for rain, snow, steam, embers."""
    return _items_param(items, "density", default, 0.0, 1.5)


def _object_box_px(
    box: tuple[float, float, float, float],
    transform: dict,
    width: int,
    height: int,
) -> tuple[float, float, float, float]:
    x1, y1 = _map_point((box[0], box[1]), transform, width, height)
    x2, y2 = _map_point((box[2], box[3]), transform, width, height)
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


# --- steam -----------------------------------------------------------------


def _soft_sprite(radius: int, cache: dict) -> np.ndarray:
    radius = max(2, int(radius))
    sprite = cache.get(radius)
    if sprite is None:
        axis = np.arange(-radius, radius + 1, dtype=np.float32)
        distance = np.sqrt(axis[:, None] ** 2 + axis[None, :] ** 2) / float(radius)
        sprite = np.clip(1.0 - distance, 0.0, 1.0) ** 1.8
        cache[radius] = sprite
    return sprite


def _add_alpha_sprite(
    layer: np.ndarray,
    alpha_layer: np.ndarray,
    sprite: np.ndarray,
    cx: float,
    cy: float,
    alpha: float,
    color: np.ndarray,
    protect: np.ndarray | None = None,
) -> None:
    if alpha <= 0.0:
        return
    out_height, out_width = alpha_layer.shape[:2]
    sprite_height, sprite_width = sprite.shape
    x0 = int(round(cx)) - sprite_width // 2
    y0 = int(round(cy)) - sprite_height // 2
    x1, y1 = x0 + sprite_width, y0 + sprite_height
    if x1 <= 0 or y1 <= 0 or x0 >= out_width or y0 >= out_height:
        return
    source_x0 = max(0, -x0)
    source_y0 = max(0, -y0)
    source_x1 = sprite_width - max(0, x1 - out_width)
    source_y1 = sprite_height - max(0, y1 - out_height)
    dest_x0, dest_y0 = max(0, x0), max(0, y0)
    dest_x1 = dest_x0 + (source_x1 - source_x0)
    dest_y1 = dest_y0 + (source_y1 - source_y0)
    if dest_x1 <= dest_x0 or dest_y1 <= dest_y0:
        return
    patch = sprite[source_y0:source_y1, source_x0:source_x1] * alpha
    if protect is not None:
        # Clip the whole sprite, not just its centre: a glowing particle near a
        # protected face/logo must not bleed into it.
        patch = patch * protect[dest_y0:dest_y1, dest_x0:dest_x1]
    rgb = layer[dest_y0:dest_y1, dest_x0:dest_x1]
    alpha_window = alpha_layer[dest_y0:dest_y1, dest_x0:dest_x1]
    rgb[:] = color[None, None, :] * patch[:, :, None] + rgb * (
        1.0 - patch[:, :, None]
    )
    alpha_window[:] = patch + alpha_window * (1.0 - patch)


def _sample_smoke_color(
    base_array: np.ndarray | None,
    box: tuple[float, float, float, float],
    fallback: np.ndarray,
) -> np.ndarray:
    """Sample the local plume colour so generated smoke matches the painting."""
    if base_array is None:
        return fallback
    height, width = base_array.shape[:2]
    x1, y1, x2, y2 = box
    left = max(0, min(width - 1, int(math.floor(min(x1, x2)))))
    right = max(left + 1, min(width, int(math.ceil(max(x1, x2)))))
    top = max(0, min(height - 1, int(math.floor(min(y1, y2)))))
    bottom = max(top + 1, min(height, int(math.ceil(max(y1, y2)))))
    region = base_array[top:bottom, left:right]
    if region.size == 0:
        return fallback
    mean = region.reshape(-1, 3).mean(axis=0).astype(np.float32)
    luminance = float(mean[0] * 0.2126 + mean[1] * 0.7152 + mean[2] * 0.0722)
    # Desaturate a little and lift the tone slightly: alpha-blending an exact
    # copy of the painting would otherwise erase the smoke texture it covers.
    color = mean * 0.35 + luminance * 0.65
    color = np.minimum(color, 150.0)
    return np.clip(color * 1.15 + 12.0, 0.0, 255.0)


def _smoke_candidate(entry: dict) -> bool:
    return _matches_hints(entry, SMOKE_AFFORDANCES, SMOKE_LABELS)


def _steam_candidate(entry: dict) -> bool:
    if _smoke_candidate(entry):
        return False
    if entry["anchors"].get("opening") is not None:
        return True
    return _matches_hints(entry, STEAM_AFFORDANCES, STEAM_LABELS)


def _fire_smoke_candidate(entry: dict) -> bool:
    return _matches_hints(entry, FIRE_AFFORDANCES, FIRE_LABELS)


def _steam_sources(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    seconds: float,
    seed: int,
    base_array: np.ndarray | None,
    log,
    prevent: np.ndarray | None = None,
) -> list[dict]:
    items = _effect_items(plan, "steam")
    if not items:
        return []
    intensity = _items_intensity(items, DEFAULT_INTENSITY)
    density = _items_density(items)
    if intensity <= 1e-3 or density <= 0.0:
        log("Bỏ qua khói: intensity hoặc density bằng 0.")
        return []
    objects = _plan_objects(plan)
    if not objects:
        log("Bỏ qua khói/hơi: plan không có objects.")
        return []
    targets = _target_ids(items)
    speed = _items_speed(items)
    cycles = max(1, int(round(seconds / 3.5 * speed)))
    rng = np.random.default_rng(seed ^ 0x5EA)
    sources: list[dict] = []

    def add_source(entry: dict) -> None:
        smoke = _smoke_candidate(entry) or _fire_smoke_candidate(entry)
        if not smoke and not _steam_candidate(entry):
            return
        x1, y1, x2, y2 = _object_box_px(entry["box"], transform, width, height)
        box_width = max(3.0, x2 - x1)
        box_height = max(3.0, y2 - y1)
        opening = entry["anchors"].get("opening")
        if smoke:
            # A smoke box already contains the plume: animate inside it and
            # only spill a little past the top. Fire boxes have no smoke inside
            # them, so their plume starts at the flame top and rises upward.
            fire_only = _fire_smoke_candidate(entry) and not _smoke_candidate(entry)
            cx = (x1 + x2) / 2.0
            if fire_only:
                cy = y1
                rise = max(box_height * 2.4, height * 0.18)
                rise = min(rise, height * 0.6)
                # Sparse wisps, not a solid cone of translucent smoke.
                base_radius = max(2.0, height * 0.010)
                max_radius = max(base_radius + 2.0, height * 0.035)
                spread = 0.24
                sample_box = (x1, max(0.0, y1 - box_height * 1.6), x2, y1)
            else:
                # Keep the plume inside the painted smoke body; starting at
                # the box bottom would draw over a burning ship below it.
                cy = y1 + box_height * 0.72
                rise = max(box_height * 1.2, height * 0.12)
                rise = min(rise, height * 0.6)
                # Small, sparse wisps. Large sprites accumulated into a soft
                # oval that read as a translucent rectangle over the smoke.
                base_radius = max(2.0, height * 0.010)
                max_radius = max(base_radius + 2.0, height * 0.040)
                spread = 0.30
                sample_box = (
                    x1 + box_width * 0.22,
                    y1 + box_height * 0.42,
                    x2 - box_width * 0.22,
                    y2,
                )
            count = max(
                8,
                int(
                    round(
                        16
                        * (0.5 + 0.7 * intensity)
                        * (0.55 + min(1.0, box_width / max(1.0, width * 0.24)))
                    )
                ),
            )
            base_alpha = 0.18 * max(
                0.45, min(1.0, (height * 0.16) / max(1.0, box_height))
            )
            color = _sample_smoke_color(
                base_array, sample_box, np.asarray((78.0, 73.0, 68.0))
            )
            # Dark painted smoke would hide the rising particles; keep the
            # rising wisps visibly lighter than the plume behind them.
            color = np.maximum(color, np.asarray((135.0, 128.0, 120.0)))
        else:
            if opening is not None:
                cx, cy = _map_point(opening, transform, width, height)
            else:
                cx, cy = (x1 + x2) / 2.0, y1
            rise = max(box_height * 2.2, height * 0.16)
            rise = min(rise, height * 0.6)
            base_radius = max(2.5, box_width * 0.16)
            max_radius = max(base_radius + 2.0, box_width * 0.46)
            count = max(
                7,
                int(
                    round(
                        30
                        * (0.5 + 0.7 * intensity)
                        * (0.5 + min(1.0, box_width / max(1.0, width * 0.25)))
                    )
                ),
            )
            spread = 0.10
            base_alpha = 0.16
            color = np.asarray(STEAM_COLOR, dtype=np.float32)

        if density != 1.0:
            count = max(4, int(round(count * density)))

        if smoke:
            # Rising smoke is drawn as free-floating wisps. Clipping them with
            # the smoke bounding box or with solid-object rectangles produced
            # visible straight edges ("khung chữ nhật"). The `prevent` mask is
            # applied once at composite time instead.
            stem = np.ones((height, width), dtype=np.float32)
        else:
            mask = Image.new("L", (width, height), 0)
            draw = ImageDraw.Draw(mask)
            half_bottom = max(3.0, box_width * 0.28)
            half_top = max(5.0, box_width * 0.55)
            draw.polygon(
                [
                    (cx - half_bottom, min(height, cy + box_height * 0.12)),
                    (cx + half_bottom, min(height, cy + box_height * 0.12)),
                    (cx + half_top, cy - rise),
                    (cx - half_top, cy - rise),
                ],
                fill=255,
            )
            blur = max(1.5, box_width * 0.09)
            stem = (
                np.asarray(
                    mask.filter(ImageFilter.GaussianBlur(blur)), dtype=np.float32
                )
                / 255.0
            )

        phases = rng.uniform(0.0, 1.0, count).astype(np.float32)
        jitter = rng.uniform(-1.0, 1.0, count).astype(np.float32)
        offsets = rng.uniform(-1.0, 1.0, count).astype(np.float32)
        curl_phase = rng.uniform(0.0, 2.0 * math.pi, count).astype(np.float32)
        curl_cycles = rng.integers(1, 3, count)
        strength = rng.uniform(0.55, 1.0, count).astype(np.float32)
        sources.append(
            {
                "cx": cx,
                "cy": cy,
                "rise": rise,
                "box_width": box_width,
                "base_radius": base_radius,
                "max_radius": max_radius,
                "intensity": intensity,
                "cycles": cycles,
                "phases": phases,
                "jitter": jitter,
                "offsets": offsets,
                "spread": spread,
                "curl_phase": curl_phase,
                "curl_cycles": curl_cycles,
                "strength": strength,
                "base_alpha": base_alpha,
                "smoke": smoke,
                "color": color,
                "mask": stem,
            }
        )

    has_smoke = any(_smoke_candidate(entry) for entry in objects)
    smoke_boxes = [entry["box"] for entry in objects if _smoke_candidate(entry)]

    def fire_covered_by_smoke(entry: dict) -> bool:
        x1, y1, x2, y2 = entry["box"]
        fire_area = max(1e-9, (x2 - x1) * (y2 - y1))
        for smoke_x1, smoke_y1, smoke_x2, smoke_y2 in smoke_boxes:
            width = max(0.0, min(x2, smoke_x2) - max(x1, smoke_x1))
            height = max(0.0, min(y2, smoke_y2) - max(y1, smoke_y1))
            if width * height / fire_area >= 0.25:
                return True
        return False

    for entry in objects:
        if targets:
            if entry["id"] in targets:
                add_source(entry)
            continue
        if _smoke_candidate(entry) or _steam_candidate(entry):
            add_source(entry)
        elif _fire_smoke_candidate(entry) and (
            not has_smoke or not fire_covered_by_smoke(entry)
        ):
            # Use a fire as an emitter when no smoke object covers it; otherwise
            # the same plume would be drawn twice.
            add_source(entry)
    if not sources:
        log("Bỏ qua khói/hơi: không có object opening/container/smoke/fire khớp target.")
    return sources


def _steam_frame(
    sources: list[dict], u: float, sprite_cache: dict
) -> tuple[np.ndarray, np.ndarray] | None:
    if not sources:
        return None
    shape = sources[0]["mask"].shape
    layer = np.zeros((shape[0], shape[1], 3), dtype=np.float32)
    alpha_layer = np.zeros((shape[0], shape[1]), dtype=np.float32)
    for source in sources:
        progress = np.mod(source["phases"] + source["cycles"] * u, 1.0).astype(np.float32)
        envelope = np.sin(math.pi * progress) ** 0.8
        alpha = (
            source["base_alpha"]
            * (0.55 + 0.75 * source["intensity"])
            * source["strength"]
            * envelope
        ).astype(np.float32)
        curl_amplitude = max(1.0, source["box_width"] * (0.08 if source["smoke"] else 0.20))
        curl = (
            curl_amplitude
            * np.sin(2.0 * math.pi * (source["curl_cycles"] * progress + source["curl_phase"]))
            * envelope
        )
        drift = source["jitter"] * source["box_width"] * (0.05 if source["smoke"] else 0.10) * (
            0.35 + 0.65 * progress
        )
        span = source["offsets"] * source["box_width"] * source["spread"] * (
            0.45 + 0.55 * progress
        )
        xs = source["cx"] + drift + curl + span
        ys = source["cy"] - progress * source["rise"]
        radii = source["base_radius"] + (source["max_radius"] - source["base_radius"]) * progress
        # Keep each plume inside its own stem mask before compositing it over
        # the shared layer; multiplying the accumulated layer would erase
        # earlier sources wherever this source's mask is zero.
        plume = np.zeros_like(layer)
        plume_alpha = np.zeros_like(alpha_layer)
        for x, y, radius, alpha_value in zip(xs, ys, radii, alpha):
            if alpha_value <= 0.002 or y < -radius or y > shape[0] + radius:
                continue
            sprite = _soft_sprite(int(round(float(radius))), sprite_cache)
            _add_alpha_sprite(
                plume,
                plume_alpha,
                sprite,
                float(x),
                float(y),
                float(alpha_value),
                source["color"],
            )
        plume *= source["mask"][:, :, None]
        plume_alpha *= source["mask"]
        layer = plume + layer * (1.0 - plume_alpha[:, :, None])
        alpha_layer = plume_alpha + alpha_layer * (1.0 - plume_alpha)
    return layer, alpha_layer


# --- wind ------------------------------------------------------------------


def _wind_candidate(entry: dict) -> bool:
    affordances = [str(value).casefold() for value in entry.get("affordances") or []]
    if str(entry.get("source") or "").casefold() == "manual":
        # A manual object only flutters when the user ticks wind_movable; a
        # matching label alone must not override that choice.
        return any("wind_movable" in value for value in affordances)
    return _matches_hints(entry, WIND_AFFORDANCES, WIND_LABELS)


def _cloth_candidate(entry: dict) -> bool:
    return _matches_hints(entry, CLOTH_HINTS, CLOTH_HINTS)


def _object_pivot(entry: dict) -> tuple[float, float]:
    pivot = entry["anchors"].get("pivot")
    if pivot is not None:
        return pivot
    x1, y1, x2, y2 = entry["box"]
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _same_support(first: dict, second: dict) -> bool:
    """True when two cloth props hang from the same mast/attachment.

    A flag above a sail shares the sail's pivot x; flags on other masts do not.
    This lets a wind target on a sail include the flag on the same support
    without hardcoding any object id or image.
    """
    first_x, first_y = _object_pivot(first)
    second_x, second_y = _object_pivot(second)
    first_box = first["box"]
    second_box = second["box"]
    first_height = abs(first_box[3] - first_box[1])
    second_height = abs(second_box[3] - second_box[1])
    return (
        abs(first_x - second_x) <= 0.06
        and abs(first_y - second_y)
        <= max(first_height, second_height) * 1.5 + 0.02
    )


def _warp_crop(crop: np.ndarray, spec: dict, u: float) -> np.ndarray:
    height, width = crop.shape[:2]
    grid_x, grid_y = np.meshgrid(
        np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32)
    )
    local_x = (grid_x + spec["left"] - spec["box"][0]) / max(1.0, spec["box"][2] - spec["box"][0])
    local_y = (grid_y + spec["top"] - spec["box"][1]) / max(1.0, spec["box"][3] - spec["box"][1])
    pivot_x, pivot_y = spec["pivot_local"]
    distance = np.hypot(grid_x - pivot_x, grid_y - pivot_y)
    pin = np.clip(distance / max(1.0, spec["max_distance"]), 0.0, 1.0) ** 0.85
    phase = 2.0 * math.pi * (spec["cycles"] * u) + spec["phase"]
    wave = np.sin(2.0 * math.pi * (local_x * 0.85 + local_y * 1.15) + phase)
    flutter = np.cos(2.0 * math.pi * (local_x * 1.6 - local_y * 0.6) + phase + 0.9)
    sway = float(spec.get("sway") or 0.0) * np.sin(phase)
    sway_y = float(spec.get("sway") or 0.0) * 0.25 * np.cos(phase)
    # Detected flags/lanterns stay attached at their pivot. A manual object's
    # pivot is the top-center the editor assigns, so keep a little net motion.
    weight = (0.25 + 0.75 * pin) if spec.get("manual") else pin ** 0.7
    sample_x = grid_x + sway * weight + spec["amplitude"] * pin * wave
    sample_y = grid_y + sway_y * weight + spec["amplitude"] * 0.35 * pin * flutter

    sample_x = np.clip(sample_x, 0.0, width - 1.0)
    sample_y = np.clip(sample_y, 0.0, height - 1.0)
    x0 = np.floor(sample_x).astype(np.int32)
    y0 = np.floor(sample_y).astype(np.int32)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    wx = (sample_x - x0)[:, :, None]
    wy = (sample_y - y0)[:, :, None]
    top = crop[y0, x0] * (1.0 - wx) + crop[y0, x1] * wx
    bottom = crop[y1, x0] * (1.0 - wx) + crop[y1, x1] * wx
    return top * (1.0 - wy) + bottom * wy


def _wind_specs(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    seconds: float,
    seed: int,
    prevent: np.ndarray,
    log,
) -> list[dict]:
    items = _effect_items(plan, "wind")
    if not items:
        return []
    objects = _plan_objects(plan)
    if not objects:
        log("Bỏ qua gió: plan không có objects.")
        return []
    targets = _target_ids(items)
    intensity = _items_intensity(items, DEFAULT_INTENSITY)
    if intensity <= 1e-3:
        log("Bỏ qua gió: intensity bằng 0.")
        return []
    speed = _items_speed(items)
    # A positive speed must still move at low slider values. Rounding a slow
    # wind (e.g. 10% of 8s) to zero cycles made the target look frozen.
    cycles = 0 if speed <= 0.0 else max(1, int(round(seconds / 3.0 * speed)))
    rng = np.random.default_rng(seed ^ 0x51D)
    size_scale = height / HEIGHT
    specs: list[dict] = []
    if targets:
        targeted = [entry for entry in objects if entry["id"] in targets]
        wind_entries = list(targeted)
        # A sail target should also move the flag on the same mast; generic
        # pivot-proximity expansion avoids per-image object ids. Only cloth
        # targets expand, so a lantern target does not pull in nearby curtains.
        cloth_targets = [target for target in targeted if _cloth_candidate(target)]
        if cloth_targets:
            for candidate in objects:
                if candidate["id"] in targets or not _cloth_candidate(candidate):
                    continue
                if any(_same_support(candidate, target) for target in cloth_targets):
                    wind_entries.append(candidate)
    else:
        wind_entries = [entry for entry in objects if _wind_candidate(entry)]
    for entry in wind_entries:
        x1, y1, x2, y2 = _object_box_px(entry["box"], transform, width, height)
        box_width, box_height = x2 - x1, y2 - y1
        if box_width < 4.0 or box_height < 4.0:
            continue
        manual = str(entry.get("source") or "").casefold() == "manual"
        # Manual objects are cloth only when their name says cloth/hair.
        # A hand-drawn "cây"/"hoa"/"vật thể" should sway rigidly, not ripple
        # in place. Detected wide cloth still uses the aspect heuristic.
        cloth = _matches_hints(entry, CLOTH_HINTS, CLOTH_HINTS) or (
            not manual and box_width > box_height * 1.15
        )
        feather = max(2.0, min(box_width, box_height) * 0.06)
        amplitude = max(
            1.5,
            min(24.0 * size_scale, min(box_width, box_height) * 0.12),
        ) * (0.65 + 0.55 * intensity)
        if speed <= 0.0:
            # Zero speed means "wind off": keep the target selected so the plan
            # still resolves, but do not deform the pixels.
            amplitude = 0.0
        sway = amplitude * 0.6
        # The mask must be wider than the object, otherwise the feathered edge
        # keeps the original silhouette pinned and the object only ripples
        # inside its own box.
        expand = int(math.ceil(1.6 * amplitude + feather)) if sway > 0.0 else 0
        pad = max(int(round(max(box_width, box_height) * 0.10)) + 2, expand + 2)
        left = max(0, int(math.floor(x1)) - pad)
        top = max(0, int(math.floor(y1)) - pad)
        right = min(width, int(math.ceil(x2)) + pad)
        bottom = min(height, int(math.ceil(y2)) + pad)
        if right - left < 4 or bottom - top < 4:
            continue

        mask_image = Image.new("L", (right - left, bottom - top), 0)
        draw = ImageDraw.Draw(mask_image)
        local = [
            max(0, int(math.floor(x1 - left - expand))),
            max(0, int(math.floor(y1 - top - expand))),
            min(right - left, int(math.ceil(x2 - left + expand))),
            min(bottom - top, int(math.ceil(y2 - top + expand))),
        ]
        if cloth or manual:
            draw.rectangle(local, fill=255)
        else:
            draw.ellipse(local, fill=255)
        base_mask = np.asarray(
            mask_image.filter(ImageFilter.GaussianBlur(feather)), dtype=np.float32
        ) / 255.0
        mask = base_mask * prevent[top:bottom, left:right]
        # Rigid fallback depends on protection inside the actual object box, not
        # on the expanded feather mask (which intentionally includes background
        # so the silhouette can move).
        core_top = max(0, int(math.floor(y1)))
        core_left = max(0, int(math.floor(x1)))
        core_bottom = min(height, int(math.ceil(y2)))
        core_right = min(width, int(math.ceil(x2)))
        core = prevent[core_top:core_bottom, core_left:core_right]
        coverage = float(core.mean()) if core.size else 1.0
        rigid = cloth and coverage <= 0.35
        if rigid:
            # A protected flag/banner (text or calligraphy) must still be able
            # to sway; move it rigidly so the lettering is not warped.
            mask = base_mask

        pivot = entry["anchors"].get("pivot")
        if pivot is not None:
            pivot_x, pivot_y = _map_point(pivot, transform, width, height)
        else:
            pivot_x, pivot_y = (x1 + x2) / 2.0, y1
        pivot_x = min(max(pivot_x - left, 0.0), float(max(0, right - left - 1)))
        pivot_y = min(max(pivot_y - top, 0.0), float(max(0, bottom - top - 1)))
        crop_width = right - left
        crop_height = bottom - top
        max_distance = math.hypot(
            max(pivot_x, crop_width - 1 - pivot_x),
            max(pivot_y, crop_height - 1 - pivot_y),
        )

        specs.append(
            {
                "id": entry["id"],
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "box": (x1, y1, x2, y2),
                "mask": mask,
                "cloth": cloth,
                "rigid": rigid,
                "manual": manual,
                "pivot_local": (pivot_x, pivot_y),
                "max_distance": max(1.0, max_distance),
                "phase": float(rng.uniform(0.0, 2.0 * math.pi)),
                "cycles": cycles,
                "angle": 0.0 if speed <= 0.0 else 1.5 + 3.0 * intensity,
                "amplitude": amplitude,
                "sway": sway,
            }
        )
    if not specs:
        log("Bỏ qua gió: không có object treo/vải khớp target.")
    return specs


def _wind_frame(image: Image.Image, specs: list[dict], u: float) -> Image.Image:
    for spec in specs:
        crop_box = (spec["left"], spec["top"], spec["right"], spec["bottom"])
        crop = image.crop(crop_box)
        original = np.asarray(crop, dtype=np.float32)
        if spec["cloth"] and not spec["rigid"]:
            warped = _warp_crop(original, spec, u)
        else:
            phase = 2.0 * math.pi * spec["cycles"] * u + spec["phase"]
            angle = spec["angle"] * math.sin(phase)
            fill = tuple(int(round(channel)) for channel in np.asarray(crop.resize((1, 1)))[0, 0])
            rotated = crop.rotate(
                angle,
                resample=Image.Resampling.BICUBIC,
                center=spec["pivot_local"],
                fillcolor=fill,
            )
            sway = float(spec.get("sway") or 0.0)
            crop_height, crop_width = rotated.size[1], rotated.size[0]
            shear = (sway * 0.8 / max(1, crop_height)) * math.sin(phase)
            pivot_y = spec["pivot_local"][1]
            rotated = rotated.transform(
                (crop_width, crop_height),
                Image.Transform.AFFINE,
                (1, shear, -shear * pivot_y, 0, 1, 0),
                resample=Image.Resampling.BICUBIC,
                fillcolor=fill,
            )
            warped = np.asarray(rotated, dtype=np.float32)
        mask = spec["mask"][:, :, None]
        blended = original * (1.0 - mask) + warped * mask
        # Rigid flags/banners rotate as a whole (lettering stays rigid, not
        # warped). Applying `prevent` per pixel here pinned the protected
        # calligraphy and made the flag look completely static.
        image.paste(Image.fromarray(np.clip(blended, 0.0, 255.0).astype(np.uint8), "RGB"), (spec["left"], spec["top"]))
    return image


# --- birds -----------------------------------------------------------------


def _shift_crop(crop: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Sub-pixel translate a crop with bilinear sampling (loop-safe)."""
    height, width = crop.shape[:2]
    grid_y, grid_x = np.mgrid[0:height, 0:width]
    sample_x = np.clip(grid_x.astype(np.float32) - dx, 0.0, width - 1.0)
    sample_y = np.clip(grid_y.astype(np.float32) - dy, 0.0, height - 1.0)
    x0 = np.floor(sample_x).astype(np.int32)
    y0 = np.floor(sample_y).astype(np.int32)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    wx = (sample_x - x0)[:, :, None]
    wy = (sample_y - y0)[:, :, None]
    top = crop[y0, x0] * (1.0 - wx) + crop[y0, x1] * wx
    bottom = crop[y1, x0] * (1.0 - wx) + crop[y1, x1] * wx
    return top * (1.0 - wy) + bottom * wy


def _birds_spec(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    seconds: float,
    prevent: np.ndarray | None,
    seed: int,
    log,
) -> list[dict]:
    """Gentle flock drift for detected bird/flock objects.

    The whole feathered patch is translated a pixel or two; smooth sky is
    visually unchanged while the silhouettes glide. Never enabled unless the
    prompt explicitly asks for birds, even when inventory finds them.
    """
    items = _effect_items(plan, "birds")
    if not items:
        return []
    targets = _target_ids(items)
    entries: list[dict] = []
    for entry in _plan_objects(plan):
        if targets and entry["id"] not in targets:
            continue
        if _matches_hints(entry, BIRD_AFFORDANCES, BIRD_LABELS):
            entries.append(entry)
    if not entries:
        log("Bỏ qua đàn chim: không có object chim/flock khớp target.")
        return []

    intensity = _items_intensity(items, 0.3)
    if intensity <= 1e-3:
        log("Bỏ qua đàn chim: intensity bằng 0.")
        return []
    speed = _items_speed(items)
    # The prompt's "nhẹ" default is ~0.30-0.35. Treat 0.30 as the reference
    # so the default motion is very soft and the slider has real headroom.
    level = max(0.0, min(3.5, intensity / 0.30))
    rng = np.random.default_rng(seed ^ 0xB19D)
    specs: list[dict] = []
    for entry in entries:
        x1, y1, x2, y2 = _object_box_px(entry["box"], transform, width, height)
        if x2 - x1 < 2.0 or y2 - y1 < 2.0:
            continue
        # Flight room around the flock so the drift does not clip at the box.
        # Keep the patch tight: shifting a huge sky patch reads as cloud wobble.
        pad_x = max(4.0, (x2 - x1) * 0.18)
        pad_y = max(4.0, (y2 - y1) * 0.30)
        left = max(0, int(math.floor(x1 - pad_x)))
        right = min(width, int(math.ceil(x2 + pad_x)))
        top = max(0, int(math.floor(y1 - pad_y)))
        bottom = min(height, int(math.ceil(y2 + pad_y)))
        if right - left < 6 or bottom - top < 6:
            continue
        mask_image = Image.new("L", (right - left, bottom - top), 0)
        ImageDraw.Draw(mask_image).ellipse(
            [x1 - left, y1 - top, x2 - left, y2 - top], fill=255
        )
        # A tight feather keeps most of the flock patch at full strength; a
        # huge blur made the drift practically invisible.
        feather = max(
            2.0,
            min(min(right - left, bottom - top) * 0.22, height * 0.02),
        )
        mask = (
            np.asarray(
                mask_image.filter(ImageFilter.GaussianBlur(feather)),
                dtype=np.float32,
            )
            / 255.0
        )
        if prevent is not None:
            mask *= prevent[top:bottom, left:right]
        specs.append(
            {
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "mask": mask,
                "intensity": intensity,
                # Reference level 0.30: ~0.4 px drift / 0.2 px bob at 640x360.
                # 100% gives about 3.3x that, so the slider has real headroom.
                "amp_x": max(
                    0.4, min(height * 0.0012, (right - left) * 0.02)
                )
                * level,
                "amp_y": max(
                    0.25, min(height * 0.0006, (bottom - top) * 0.012)
                )
                * level,
                # Integer cycles keep the drift seamless at the loop point.
                "cycles": max(1, int(round(seconds / 12.0 * speed))),
                "bob_cycles": max(1, int(round(seconds / 8.0 * speed))),
                "phase": float(rng.uniform(0.0, 2.0 * math.pi)),
                "bob_phase": float(rng.uniform(0.0, 2.0 * math.pi)),
            }
        )
    return specs


def _apply_birds(frame: np.ndarray, specs: list[dict], u: float) -> np.ndarray:
    for spec in specs:
        if spec.get("intensity", 1.0) <= 0.0:
            continue
        left, top = spec["left"], spec["top"]
        right, bottom = spec["right"], spec["bottom"]
        crop = frame[top:bottom, left:right]
        dx = spec["amp_x"] * math.sin(
            2.0 * math.pi * spec["cycles"] * u + spec["phase"]
        )
        dy = spec["amp_y"] * math.sin(
            2.0 * math.pi * spec["bob_cycles"] * u + spec["bob_phase"]
        )
        warped = _shift_crop(crop, dx, dy)
        mask = spec["mask"][:, :, None]
        frame[top:bottom, left:right] = crop * (1.0 - mask) + warped * mask
    return frame


# --- light -----------------------------------------------------------------


def _light_spec(
    plan: dict,
    base_array: np.ndarray,
    prevent: np.ndarray,
    seconds: float,
) -> dict | None:
    items = _effect_items(plan, "light")
    if not items:
        return None
    intensity = _items_intensity(items, DEFAULT_LIGHT_INTENSITY)
    speed = _items_speed(items)
    # Integer cycles over the loop keep the pulse seamless.
    cycles = max(1, int(round(seconds / 4.0 * speed)))
    amplitude = (
        0.0
        if intensity <= 1e-3
        else max(0.008, min(0.02, 0.02 * (intensity / DEFAULT_LIGHT_INTENSITY)))
    )
    luminance = (
        base_array[:, :, 0] * 0.2126
        + base_array[:, :, 1] * 0.7152
        + base_array[:, :, 2] * 0.0722
    ) / 255.0
    # Bright/emissive pixels pulse a little more, dark pixels a little less.
    weight = (0.45 + 0.55 * np.clip(luminance, 0.0, 1.0) ** 1.5) * prevent
    return {"amplitude": amplitude, "cycles": cycles, "weight": weight[:, :, None]}


def _apply_light(frame: np.ndarray, spec: dict | None, u: float) -> np.ndarray:
    if spec is None:
        return frame
    pulse = math.sin(2.0 * math.pi * spec["cycles"] * u)
    if pulse == 0.0:
        return frame
    factor = 1.0 + spec["amplitude"] * pulse * spec["weight"]
    frame *= factor.astype(np.float32, copy=False)
    return frame


# --- water (W1 shimmer) ----------------------------------------------------


def _water_shapes(plan: dict, targets: set[str]) -> list[list[tuple[float, float]]]:
    """Polygons/boxes that describe a liquid surface.

    Regions match on id/label/kind/type substrings (liquid, water, sea, river,
    ...). Objects only qualify through a water/liquid affordance or label, as the
    plan contract specifies. ``targets`` narrows object selection when the item
    names explicit object ids; matching liquid regions are always kept because
    they describe the surface itself, not an optional object.
    """
    shapes: list[list[tuple[float, float]]] = []
    regions = plan.get("regions") if isinstance(plan, dict) else None
    if isinstance(regions, list):
        for entry in regions:
            if not isinstance(entry, dict):
                continue
            identity = " ".join(
                str(entry.get(key) or "") for key in ("id", "label", "kind", "type")
            ).casefold()
            if not any(hint in identity for hint in WATER_REGION_HINTS):
                continue
            points = _shape_points(entry)
            if points:
                shapes.append(points)
    if shapes:
        # A liquid region is more precise than a water object's full bounding
        # box, which often includes hulls or stakes sitting in the water.
        return shapes

    for entry in _plan_objects(plan):
        if targets and entry["id"] not in targets:
            continue
        if not _matches_hints(entry, WATER_AFFORDANCES, WATER_LABELS):
            continue
        x1, y1, x2, y2 = entry["box"]
        shapes.append([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])
    return shapes


def _water_occluder_shapes(plan: dict) -> list[list[tuple[float, float]]]:
    """Boxes of non-water objects that sit in the liquid surface.

    Water shimmer is a pixel warp; without these cutouts it visibly bends
    hulls, rigging, stakes, and people standing in the water.
    """
    shapes: list[list[tuple[float, float]]] = []
    for entry in _plan_objects(plan, selected_only=False):
        if _matches_hints(entry, WATER_AFFORDANCES, WATER_LABELS):
            continue
        x1, y1, x2, y2 = entry["box"]
        inset_x = (x2 - x1) * 0.02
        inset_y = (y2 - y1) * 0.02
        shapes.append(
            [
                (x1 + inset_x, y1 + inset_y),
                (x2 - inset_x, y1 + inset_y),
                (x2 - inset_x, y2 - inset_y),
                (x1 + inset_x, y2 - inset_y),
            ]
        )
    return shapes


def _water_detail_mask(
    base_array: np.ndarray | None, width: int, height: int
) -> np.ndarray | None:
    """Protect vertical structures (people, stakes, masts) missed by inventory.

    Water shimmer is a horizontal pixel warp; a strong horizontal gradient with
    a weaker vertical gradient is a vertical edge. Dilate it so the whole small
    figure is protected, not just its outline.
    """
    if base_array is None or base_array.size == 0:
        return None
    image = base_array
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    gray = (
        image[:, :, 0].astype(np.float32) * 0.2126
        + image[:, :, 1].astype(np.float32) * 0.7152
        + image[:, :, 2].astype(np.float32) * 0.0722
    )
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:] = np.abs(np.diff(gray, axis=1))
    gy[1:, :] = np.abs(np.diff(gray, axis=0))
    score = np.clip((gx - 0.65 * gy - 8.0) / 30.0, 0.0, 1.0)
    detail = Image.fromarray((score * 255.0).astype(np.uint8), "L")
    detail = detail.filter(ImageFilter.MaxFilter(5))
    detail = detail.filter(ImageFilter.GaussianBlur(max(1.0, 2.0 * height / HEIGHT)))
    return np.asarray(detail, dtype=np.float32) / 255.0


def _build_water_mask(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    prevent: np.ndarray,
    base_array: np.ndarray | None = None,
) -> np.ndarray | None:
    """Feathered 0..1 mask of the water surfaces, already protect/strip-safe."""
    items = _effect_items(plan, "water")
    if not items:
        return None
    shapes = _water_shapes(plan, _target_ids(items))
    if not shapes:
        return None
    scale = 2
    big_width, big_height = width * scale, height * scale
    mask = Image.new("L", (big_width, big_height), 0)
    draw = ImageDraw.Draw(mask)
    for points in shapes:
        mapped = [_map_point(point, transform, big_width, big_height) for point in points]
        draw.polygon(mapped, fill=255)
    for points in _water_occluder_shapes(plan):
        mapped = [_map_point(point, transform, big_width, big_height) for point in points]
        draw.polygon(mapped, fill=0)
    mask = mask.resize((width, height), Image.Resampling.BOX)
    feather = max(1.0, 3.0 * height / HEIGHT)
    mask = mask.filter(ImageFilter.GaussianBlur(feather))
    array = np.asarray(mask, dtype=np.float32) / 255.0
    array *= prevent
    detail = _water_detail_mask(base_array, width, height)
    if detail is not None:
        array *= 1.0 - 0.85 * detail
    return array


def _water_spec(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    seconds: float,
    prevent: np.ndarray,
    log,
    base_array: np.ndarray | None = None,
) -> dict | None:
    """Prepare the W1 shimmer, or return None when there is no liquid surface."""
    items = _effect_items(plan, "water")
    if not items:
        return None
    mask = _build_water_mask(plan, transform, width, height, prevent, base_array=base_array)
    if mask is None or float(mask.max()) <= 1e-4:
        log("Bỏ qua nước: không có vùng/object nước (liquid/water/sea/river) khớp.")
        return None

    row_strength = mask.max(axis=1)
    column_strength = mask.max(axis=0)
    rows = np.where(row_strength > 1e-4)[0]
    columns = np.where(column_strength > 1e-4)[0]
    if rows.size == 0 or columns.size == 0:
        log("Bỏ qua nước: vùng nước rỗng sau khi trừ protect/strip.")
        return None
    pad = 4
    top = max(0, int(rows[0]) - pad)
    bottom = min(height, int(rows[-1]) + 1 + pad)
    left = max(0, int(columns[0]) - pad)
    right = min(width, int(columns[-1]) + 1 + pad)
    if bottom - top < 2 or right - left < 2:
        log("Bỏ qua nước: vùng nước quá nhỏ.")
        return None

    intensity = _items_intensity(items, DEFAULT_WATER_INTENSITY)
    speed = _items_speed(items)
    size_scale = height / HEIGHT
    local_height = bottom - top
    local_width = right - left
    local_rows = np.arange(local_height, dtype=np.float32)
    local_columns = np.arange(local_width, dtype=np.float32)
    # Integer cycles over the loop keep both waves seamless.
    displacement_cycles = max(1, int(round(seconds / 3.0 * speed)))
    specular_cycles = max(1, int(round(seconds / 2.0 * speed)))
    amplitude = (
        0.0
        if intensity <= 1e-3
        else max(1.5, (3.0 + 6.0 * intensity) * size_scale)
    )
    return {
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
        "mask": mask[top:bottom, left:right].astype(np.float32)[:, :, None],
        "displacement_cycles": displacement_cycles,
        "specular_cycles": specular_cycles,
        "amplitude": amplitude,
        "vertical_amplitude": amplitude * 0.75,
        "specular": 0.0 if intensity <= 1e-3 else 3.0 + 5.0 * intensity,
        "row_phase": (2.0 * math.pi * 5.0 * local_rows / local_height).astype(np.float32),
        "row_phase_specular": (
            2.0 * math.pi * 9.0 * local_rows / local_height + 0.7
        ).astype(np.float32),
        "column_phase": (
            2.0 * math.pi * 4.0 * local_columns / local_width
        ).astype(np.float32),
        "column_phase2": (
            2.0 * math.pi * 7.0 * local_columns / local_width + 1.3
        ).astype(np.float32),
        "column_phase_specular": (
            2.0 * math.pi * 5.0 * local_columns / local_width + 0.4
        ).astype(np.float32),
    }


def _warp_water(crop: np.ndarray, spec: dict, u: float) -> np.ndarray:
    """Bilinear warp with a horizontal wave per row and a vertical bob per column."""
    height, width = crop.shape[:2]
    grid_y, grid_x = np.mgrid[0:height, 0:width]
    grid_x = grid_x.astype(np.float32)
    grid_y = grid_y.astype(np.float32)

    phase = 2.0 * math.pi * spec["displacement_cycles"] * u
    amplitude = spec["amplitude"]
    dx = amplitude * np.sin(phase + spec["row_phase"])
    dx += 0.35 * amplitude * np.sin(2.0 * phase + spec["row_phase"] * 1.7 + 1.1)
    dy = spec["vertical_amplitude"] * np.sin(phase + spec["column_phase"])
    dy += 0.25 * spec["vertical_amplitude"] * np.sin(
        2.0 * phase + spec["column_phase2"]
    )

    sample_x = grid_x - dx[:, None]
    sample_y = grid_y - dy[None, :]
    np.clip(sample_x, 0.0, float(width - 1), out=sample_x)
    np.clip(sample_y, 0.0, float(height - 1), out=sample_y)
    x0 = np.floor(sample_x).astype(np.int32)
    y0 = np.floor(sample_y).astype(np.int32)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    wx = (sample_x - x0)[:, :, None]
    wy = (sample_y - y0)[:, :, None]
    top = crop[y0, x0] * (1.0 - wx) + crop[y0, x1] * wx
    bottom = crop[y1, x0] * (1.0 - wx) + crop[y1, x1] * wx
    return top * (1.0 - wy) + bottom * wy


def _apply_water(frame: np.ndarray, spec: dict | None, u: float) -> np.ndarray:
    """Apply a subtle per-row/per-column wave shimmer + soft specular highlights."""
    if spec is None:
        return frame
    top, bottom = spec["top"], spec["bottom"]
    left, right = spec["left"], spec["right"]
    crop = frame[top:bottom, left:right]
    if crop.shape[0] < 1 or crop.shape[1] < 1:
        return frame

    warped = _warp_water(crop, spec, u)
    mask = spec["mask"]
    blended = crop * (1.0 - mask) + warped * mask

    luminance = (
        blended[:, :, 0] * 0.2126
        + blended[:, :, 1] * 0.7152
        + blended[:, :, 2] * 0.0722
    ) / 255.0
    specular_phase = 2.0 * math.pi * spec["specular_cycles"] * u
    light = 0.32 + 0.68 * np.clip(luminance, 0.0, 1.0) ** 1.1
    row_mod = spec["specular"] * np.sin(
        specular_phase + spec["row_phase_specular"]
    )
    modulation = row_mod[:, None] * light
    # Moving glints keep dark foreground water alive; they are strongest on the
    # already-bright wave crests instead of painting a uniform screen-wide band.
    glint = np.maximum(
        np.sin(
            specular_phase
            + spec["row_phase_specular"][:, None] * 1.15
            + spec["column_phase_specular"][None, :] * 0.9
        ),
        0.0,
    ) ** 3
    modulation = modulation + (
        spec["specular"] * 0.55 * (glint - 0.12) * (0.35 + 0.65 * luminance)
    )
    blended += modulation[:, :, None] * mask
    frame[top:bottom, left:right] = blended
    return frame


# --- fire (P2b) ------------------------------------------------------------


def _region_identity(entry: dict) -> str:
    return " ".join(
        str(entry.get(key) or "") for key in ("id", "label", "kind", "type")
    ).casefold()


def _token_match(identity: str, tokens: tuple[str, ...]) -> bool:
    words = "".join(char if char.isalnum() else " " for char in identity).split()
    return any(word in tokens for word in words)


def _is_light_source_entry(entry: dict) -> bool:
    """True for the sun/light-source object, which must never count as fire."""
    label = str(entry.get("label") or "").casefold()
    affordances = " ".join(entry.get("affordances") or [])
    return (
        "sun" in label
        or "sunlight" in label
        or "light_source" in affordances
        or "light source" in affordances
    )


def _is_flame_object(entry: dict) -> bool:
    """Strict flame labels, so a burning-ship hull box is not an ember source."""
    label = str(entry.get("label") or "").casefold()
    return any(
        token in label
        for token in ("fire", "flame", "ember", "torch", "candle", "brazier", "hearth")
    )


def _fire_shapes(
    plan: dict,
    targets: set[str],
    *,
    flames_only: bool = False,
) -> list[list[tuple[float, float]]]:
    """Fire object boxes first; fall back to an explicit fire/flame region.

    ``flames_only`` prefers compact flame objects so embers rise from the
    fire itself instead of a whole burning-ship box (or the sun).
    """

    def collect(strict: bool) -> list[list[tuple[float, float]]]:
        shapes: list[list[tuple[float, float]]] = []
        for entry in _plan_objects(plan):
            if _is_light_source_entry(entry):
                continue
            if targets and entry["id"] not in targets:
                continue
            if not targets and not _matches_hints(entry, FIRE_AFFORDANCES, FIRE_LABELS):
                continue
            if strict and not _is_flame_object(entry):
                continue
            x1, y1, x2, y2 = entry["box"]
            shapes.append([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])
        return shapes

    shapes = collect(flames_only)
    if shapes:
        return shapes
    if flames_only:
        shapes = collect(False)
        if shapes:
            return shapes
    regions = plan.get("regions") if isinstance(plan, dict) else None
    if not isinstance(regions, list):
        return []
    for entry in regions:
        if not isinstance(entry, dict):
            continue
        identity = _region_identity(entry)
        if not any(hint in identity for hint in FIRE_REGION_HINTS):
            continue
        if targets and str(entry.get("id") or "") not in targets:
            continue
        points = _shape_points(entry)
        if points:
            shapes.append(points)
    return shapes


def _build_fire_mask(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    prevent: np.ndarray,
) -> np.ndarray | None:
    """Feathered glow footprint around fire objects/regions, protect/strip safe."""
    items = _effect_items(plan, "fire")
    if not items:
        return None
    shapes = _fire_shapes(plan, _target_ids(items))
    if not shapes:
        return None
    scale = 2
    big_width, big_height = width * scale, height * scale
    mask = Image.new("L", (big_width, big_height), 0)
    draw = ImageDraw.Draw(mask)
    for points in shapes:
        mapped = [_map_point(point, transform, big_width, big_height) for point in points]
        draw.polygon(mapped, fill=255)
    mask = mask.resize((width, height), Image.Resampling.BOX)
    feather = max(1.5, 5.0 * height / HEIGHT)
    mask = mask.filter(ImageFilter.GaussianBlur(feather))
    array = np.asarray(mask, dtype=np.float32) / 255.0
    array *= prevent
    return array


def _fire_spec(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    seconds: float,
    prevent: np.ndarray,
    seed: int,
    log,
) -> dict | None:
    """Prepare the warm flicker glow, or None when no fire target resolves."""
    items = _effect_items(plan, "fire")
    if not items:
        return None
    mask = _build_fire_mask(plan, transform, width, height, prevent)
    if mask is None or float(mask.max()) <= 1e-4:
        log("Bỏ qua lửa: không có object/vùng lửa khớp target.")
        return None
    intensity = _items_intensity(items, DEFAULT_FIRE_INTENSITY)
    speed = _items_speed(items)
    # Low-frequency flicker: ~0.4 Hz at the default 8 s loop, integer cycles.
    cycles = max(1, int(round(seconds / 2.5 * speed)))
    amplitude = 0.0 if intensity <= 1e-3 else 12.0 + 30.0 * intensity
    rng = np.random.default_rng(seed ^ 0xF12E)
    return {
        "mask": mask[:, :, None],
        "cycles": cycles,
        "amplitude": amplitude,
        "phase": float(rng.uniform(0.0, 2.0 * math.pi)),
        "color": np.asarray(FIRE_RGB, dtype=np.float32),
    }


def _apply_fire(frame: np.ndarray, spec: dict | None, u: float) -> np.ndarray:
    """Additive warm glow with a slow two-harmonic flicker."""
    if spec is None:
        return frame
    phase = 2.0 * math.pi * spec["cycles"] * u + spec["phase"]
    # Both harmonics complete whole cycles over the loop, so the seam matches.
    pulse = 0.78 + 0.22 * math.sin(phase) + 0.10 * math.sin(2.0 * phase + 1.3)
    glow = spec["mask"] * (spec["amplitude"] * max(0.0, pulse))
    frame += glow * spec["color"]
    return frame


# --- embers ----------------------------------------------------------------


def _embers_spec(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    seconds: float,
    prevent: np.ndarray,
    seed: int,
    log,
) -> dict | None:
    """Sparse rising sparks from fire objects/regions."""
    items = _effect_items(plan, "embers")
    if not items:
        return None
    intensity = _items_intensity(items, 0.5)
    density_multiplier = _items_density(items)
    if intensity <= 1e-3 or density_multiplier <= 0.0:
        log("Bỏ qua tàn lửa: intensity hoặc density bằng 0.")
        return None
    shapes = _fire_shapes(plan, _target_ids(items), flames_only=True)
    if not shapes:
        log("Bỏ qua tàn lửa: không có object/vùng lửa khớp target.")
        return None

    emitters: list[dict] = []
    for points in shapes:
        mapped = [_map_point(point, transform, width, height) for point in points]
        if not mapped:
            continue
        xs = [point[0] for point in mapped]
        ys = [point[1] for point in mapped]
        x1, x2 = max(0.0, min(xs)), min(float(width), max(xs))
        y1, y2 = max(0.0, min(ys)), min(float(height), max(ys))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        emitters.append(
            {
                "x1": x1,
                "x2": x2,
                # Start in the lower/central part of the flame, not its top.
                "y1": y1 + (y2 - y1) * 0.25,
                "y2": y2,
                "area": max(1.0, (x2 - x1) * (y2 - y1)),
            }
        )
    if not emitters:
        log("Bỏ qua tàn lửa: vùng lửa quá nhỏ.")
        return None

    speed = _items_speed(items)
    density = math.sqrt((width * height) / (WIDTH * HEIGHT)) * density_multiplier
    # 0.30 is the reference level: the default ember density is ~30% of the
    # old baseline, while the slider can still raise it toward the old max.
    level = max(0.0, min(3.5, intensity / 0.30))
    count = max(0, int(round(35 * density * level)))
    if count == 0:
        return None
    total_area = sum(emitter["area"] for emitter in emitters)
    rng = np.random.default_rng(seed ^ 0xE0B5)
    particles: list[dict] = []
    for _ in range(count):
        pick = rng.uniform(0.0, total_area)
        cursor = 0.0
        emitter = emitters[0]
        for candidate in emitters:
            cursor += candidate["area"]
            if pick <= cursor:
                emitter = candidate
                break
        span_x = max(1.0, emitter["x2"] - emitter["x1"])
        span_y = max(1.0, emitter["y2"] - emitter["y1"])
        particles.append(
            {
                "x": float(rng.uniform(emitter["x1"], emitter["x2"])),
                "y": float(rng.uniform(emitter["y1"], emitter["y2"])),
                "rise": float(rng.uniform(0.22, 0.55) * height),
                "drift": float(rng.uniform(-0.035, 0.035) * width),
                "sway": float(max(1.0, rng.uniform(2.0, 8.0) * height / HEIGHT)),
                "phase": float(rng.uniform(0.0, 1.0)),
                "cycles": max(1, int(round(int(rng.integers(2, 5)) * speed))),
                # Fixed-ish pixel size: embers are point sparks, so scaling
                # them down to sub-pixel at 640 made them invisible.
                "size": float(
                    max(
                        1.2,
                        rng.uniform(1.3, 2.8) * math.sqrt(height / HEIGHT),
                    )
                ),
                "alpha": float(rng.uniform(0.65, 1.0)),
                "color": np.asarray(
                    (
                        rng.uniform(235.0, 255.0),
                        rng.uniform(120.0, 175.0),
                        rng.uniform(35.0, 80.0),
                    ),
                    dtype=np.float32,
                ),
            }
        )
    return {
        "particles": particles,
        "intensity": intensity,
        "alpha_scale": 0.30,
        "prevent": prevent,
    }


def _apply_embers(
    frame: np.ndarray,
    spec: dict | None,
    u: float,
    sprite_cache: dict,
) -> np.ndarray:
    if spec is None:
        return frame
    height, width = frame.shape[:2]
    layer = np.zeros_like(frame, dtype=np.float32)
    alpha_layer = np.zeros((height, width), dtype=np.float32)
    prevent = spec["prevent"]
    intensity = spec["intensity"]
    for particle in spec["particles"]:
        progress = (particle["phase"] + particle["cycles"] * u) % 1.0
        envelope = math.sin(math.pi * progress) ** 1.4
        alpha = (
            particle["alpha"]
            * intensity
            * spec.get("alpha_scale", 1.0)
            * envelope
        )
        if alpha <= 0.01:
            continue
        x = (
            particle["x"]
            + particle["drift"] * progress
            + particle["sway"] * math.sin(2.0 * math.pi * progress * 2.0 + particle["phase"])
        )
        y = particle["y"] - particle["rise"] * progress
        if x < 0 or x >= width or y < 0 or y >= height:
            continue
        if prevent[int(y), int(x)] <= 0.01:
            continue
        radius = max(2, int(round(particle["size"] * 2.5)))
        sprite = _soft_sprite(radius, sprite_cache)
        _add_alpha_sprite(
            layer,
            alpha_layer,
            sprite,
            x,
            y,
            alpha,
            particle["color"],
            protect=prevent,
        )
    return np.clip(frame + layer, 0.0, 255.0).astype(np.float32)


# --- sun rays --------------------------------------------------------------


def _sun_center(
    plan: dict,
    base_array: np.ndarray,
    transform: dict,
    width: int,
    height: int,
    targets: set[str],
) -> tuple[float, float]:
    """Anchor the rays on the sun object, else the brightest warm region."""
    entries: list[dict] = []
    for entry in _plan_objects(plan):
        if targets and entry["id"] not in targets:
            continue
        if _matches_hints(entry, SUN_AFFORDANCES, SUN_LABELS):
            entries.append(entry)
    if entries:
        total = 0.0
        cx = 0.0
        cy = 0.0
        for entry in entries:
            x1, y1, x2, y2 = _object_box_px(entry["box"], transform, width, height)
            area = max(1.0, (x2 - x1) * (y2 - y1))
            total += area
            cx += area * (x1 + x2) * 0.5
            cy += area * (y1 + y2) * 0.5
        if total > 0.0:
            return cx / total, cy / total

    # Fallback: brightest warm spot in the upper part of the frame.
    limit = max(1, int(height * 0.65))
    region = base_array[:limit]
    luminance = (
        region[:, :, 0] * 0.2126
        + region[:, :, 1] * 0.7152
        + region[:, :, 2] * 0.0722
    ) / 255.0
    warmth = np.clip((region[:, :, 0] - region[:, :, 2]) / 80.0, 0.0, 1.5)
    score = luminance * (0.35 + 0.65 * warmth)
    threshold = float(np.percentile(score, 99.7))
    ys, xs = np.where(score >= threshold)
    if xs.size == 0:
        return width * 0.75, height * 0.25
    return float(xs.mean()), float(ys.mean())


def _sunrays_spec(
    plan: dict,
    transform: dict,
    base_array: np.ndarray,
    width: int,
    height: int,
    seconds: float,
    prevent: np.ndarray | None,
    seed: int,
    log,
) -> dict | None:
    """Soft god rays around the sun, breathing slowly.

    Only built when the prompt asks for sun rays/sunlight; the inventory sun is
    just an anchor, never a trigger on its own.
    """
    items = _effect_items(plan, "sunrays")
    if not items:
        return None
    targets = _target_ids(items)
    intensity = _items_intensity(items, 0.35)
    speed = _items_speed(items)
    if intensity <= 0.0:
        return None
    cx, cy = _sun_center(plan, base_array, transform, width, height, targets)
    cx = min(float(width), max(0.0, cx))
    cy = min(float(height), max(0.0, cy))
    radius = max(height * 0.24, min(width, height) * 0.30)
    left = max(0, int(math.floor(cx - radius)))
    right = min(width, int(math.ceil(cx + radius)))
    top = max(0, int(math.floor(cy - radius)))
    bottom = min(height, int(math.ceil(cy + radius)))
    if right - left < 8 or bottom - top < 8:
        log("Bỏ qua tia nắng: vùng mặt trời quá nhỏ.")
        return None

    grid_y, grid_x = np.mgrid[top:bottom, left:right]
    dx = grid_x.astype(np.float32) - cx
    dy = grid_y.astype(np.float32) - cy
    distance = np.sqrt(dx * dx + dy * dy) / max(1.0, radius)
    # Rays live in the sky ring around the core, not on the blown-out sun.
    falloff = (
        np.clip(1.0 - distance, 0.0, 1.0) ** 0.7
        * np.clip(distance / 0.10, 0.0, 1.0)
    )
    glow = np.exp(-((distance / 0.36) ** 2))
    rays = max(8, int(round(10 + 6 * intensity)))
    angle = np.arctan2(dy, dx)
    mask = np.ones((bottom - top, right - left), dtype=np.float32)
    if prevent is not None:
        mask *= prevent[top:bottom, left:right]
    rng = np.random.default_rng(seed ^ 0x5A17)
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "rays": rays,
        "angle": angle.astype(np.float32),
        # Uneven spokes break the "perfect cartoon starburst" look.
        "ray_weights": rng.uniform(0.45, 1.0, rays).astype(np.float32),
        "falloff": falloff.astype(np.float32),
        "glow": glow.astype(np.float32),
        "mask": mask,
        "base_alpha": 0.18 + 0.12 * intensity,
        "glow_alpha": 0.14 + 0.08 * intensity,
        "shadow": 0.02 + 0.025 * intensity,
        # A shorter cycle still reads in a 4 s preview while staying slow.
        "cycles": max(1, int(round(seconds / 5.0 * speed))),
        "phase": float(rng.uniform(0.0, 2.0 * math.pi)),
        "color": np.asarray((255.0, 208.0, 150.0), dtype=np.float32),
    }


def _apply_sunrays(frame: np.ndarray, spec: dict | None, u: float) -> np.ndarray:
    if spec is None:
        return frame
    cycle = 2.0 * math.pi * spec["cycles"] * u + spec["phase"]
    breathe = 0.72 + 0.28 * math.sin(cycle)
    rotate = 0.03 * math.sin(cycle + 1.7)
    phase = spec["angle"] * spec["rays"] + rotate
    spokes = (0.5 + 0.5 * np.cos(phase)) ** 2.0
    ray_index = (
        np.floor(phase / (2.0 * math.pi) + 0.5).astype(np.int32)
        % spec["rays"]
    )
    spokes *= spec["ray_weights"][ray_index]
    alpha = (
        spec["base_alpha"] * spokes * spec["falloff"]
        + spec["glow_alpha"] * spec["glow"]
    ) * breathe
    alpha *= spec["mask"]
    alpha = np.clip(alpha, 0.0, 0.42)
    # Slightly darken the gaps between rays: on a bright sunset sky an
    # additive-only ray pattern is invisible.
    shadow = (
        (1.0 - spokes) * spec["falloff"] * spec["shadow"] * breathe * spec["mask"]
    )
    shadow = np.clip(shadow, 0.0, 0.10)
    left, top = spec["left"], spec["top"]
    right, bottom = spec["right"], spec["bottom"]
    crop = frame[top:bottom, left:right]
    # Screen blend adds warm light without clipping the bright sun itself:
    # bright pixels change little, mid-tone sky/clouds show the rays clearly.
    tint = (spec["color"] / 255.0)[None, None, :]
    lit = crop + (255.0 - crop) * (alpha[:, :, None] * tint)
    frame[top:bottom, left:right] = lit * (1.0 - shadow[:, :, None])
    return frame


# --- fog (P2b) -------------------------------------------------------------


def _fog_region_mask(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    targets: set[str],
) -> np.ndarray | None:
    """Feathered union of fog/mist/ground/low regions, or None when absent."""
    regions = plan.get("regions") if isinstance(plan, dict) else None
    if not isinstance(regions, list):
        return None
    shapes: list[list[tuple[float, float]]] = []
    for entry in regions:
        if not isinstance(entry, dict):
            continue
        identity = _region_identity(entry)
        if not any(hint in identity for hint in FOG_REGION_HINTS) and not _token_match(
            identity, FOG_REGION_TOKENS
        ):
            continue
        if targets and str(entry.get("id") or "") not in targets:
            continue
        points = _shape_points(entry)
        if points:
            shapes.append(points)
    if not shapes:
        return None
    scale = 2
    big_width, big_height = width * scale, height * scale
    mask = Image.new("L", (big_width, big_height), 0)
    draw = ImageDraw.Draw(mask)
    for points in shapes:
        mapped = [_map_point(point, transform, big_width, big_height) for point in points]
        draw.polygon(mapped, fill=255)
    mask = mask.resize((width, height), Image.Resampling.BOX)
    feather = max(1.5, 6.0 * height / HEIGHT)
    mask = mask.filter(ImageFilter.GaussianBlur(feather))
    return np.asarray(mask, dtype=np.float32) / 255.0


def _build_fog_mask(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    prevent: np.ndarray,
) -> np.ndarray | None:
    items = _effect_items(plan, "fog")
    if not items:
        return None
    mask = _fog_region_mask(plan, transform, width, height, _target_ids(items))
    if mask is None:
        # No fog/ground region: fall back to a soft ramp over the lower half.
        rows = np.arange(height, dtype=np.float32)
        start = height * 0.5
        ramp = np.clip((rows - start) / max(1.0, height * 0.14), 0.0, 1.0)
        mask = np.repeat(ramp[:, None], width, axis=1)
    mask *= prevent
    return mask


def _fog_texture(width: int, height: int, seed: int, cell: float) -> np.ndarray:
    """Low-resolution random field upscaled and blurred into soft noise."""
    cell = max(4, int(round(cell)))
    small_width = max(2, int(math.ceil(width / cell)) + 1)
    small_height = max(2, int(math.ceil(height / cell)) + 1)
    rng = np.random.default_rng(seed)
    noise = rng.random((small_height, small_width)).astype(np.float32)
    image = Image.fromarray(np.clip(noise * 255.0, 0.0, 255.0).astype(np.uint8), "L")
    image = image.resize((width, height), Image.Resampling.BICUBIC)
    image = image.filter(ImageFilter.GaussianBlur(max(1.0, cell * 0.45)))
    array = np.asarray(image, dtype=np.float32) / 255.0
    low, high = float(array.min()), float(array.max())
    if high - low > 1e-4:
        array = (array - low) / (high - low)
    return array


def _fog_spec(
    plan: dict,
    transform: dict,
    width: int,
    height: int,
    seconds: float,
    prevent: np.ndarray,
    seed: int,
    log,
) -> dict | None:
    """Prepare the soft drifting fog layer (region mask or lower-half ramp)."""
    items = _effect_items(plan, "fog")
    if not items:
        return None
    mask = _build_fog_mask(plan, transform, width, height, prevent)
    if mask is None or float(mask.max()) <= 1e-4:
        log("Bỏ qua sương mù: vùng sương rỗng sau khi trừ protect/strip.")
        return None
    intensity = _items_intensity(items, DEFAULT_FOG_INTENSITY)
    speed = _items_speed(items)
    size_scale = max(0.35, height / HEIGHT)
    # Two slow integer-cycle drifts keep the noise seamless at the loop seam.
    coarse = _fog_texture(width, height, seed ^ 0xF06C, max(6.0, height / 12.0))
    fine = _fog_texture(width, height, seed ^ 0xF069, max(3.0, height / 30.0))
    rng = np.random.default_rng(seed ^ 0xF06A)
    return {
        "mask": mask.astype(np.float32),
        "coarse": coarse,
        "fine": fine,
        "drift": max(1.5, 12.0 * size_scale),
        "fine_drift": max(1.0, 6.0 * size_scale),
        "cycles": max(1, int(round(seconds / 5.0 * speed))),
        "fine_cycles": max(1, int(round(seconds / 4.0 * speed))),
        "phase": float(rng.uniform(0.0, 2.0 * math.pi)),
        "phase2": float(rng.uniform(0.0, 2.0 * math.pi)),
        "alpha": 0.0 if intensity <= 1e-3 else min(0.26, 0.08 + 0.22 * intensity),
    }


def _apply_fog(frame: np.ndarray, spec: dict | None, u: float) -> np.ndarray:
    """Blend a slow low-alpha noise layer over the fog mask."""
    if spec is None or spec["alpha"] <= 0.0:
        return frame
    phase = 2.0 * math.pi * spec["cycles"] * u + spec["phase"]
    phase2 = 2.0 * math.pi * spec["fine_cycles"] * u + spec["phase2"]
    shift_y = int(round(spec["drift"] * 0.35 * math.sin(phase + 1.7)))
    shift_x = int(round(spec["drift"] * math.sin(phase)))
    fine_y = int(round(spec["fine_drift"] * 0.40 * math.sin(phase2 + 0.6)))
    fine_x = int(round(-spec["fine_drift"] * math.sin(phase2)))
    coarse = np.roll(np.roll(spec["coarse"], shift_y, axis=0), shift_x, axis=1)
    fine = np.roll(np.roll(spec["fine"], fine_y, axis=0), fine_x, axis=1)
    noise = 0.62 * coarse + 0.38 * fine
    alpha = (spec["alpha"] * noise * spec["mask"])[:, :, None]
    frame *= 1.0 - alpha
    frame += np.asarray(FOG_RGB, dtype=np.float32)[None, None, :] * alpha
    return frame


def _encode(frames, destination: Path, width: int, height: int, fps: int, log) -> None:
    if shutil.which("ffmpeg") is None:
        raise MediaError("Chưa có ffmpeg trong PATH.")
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        X264_PRESET,
        "-crf",
        str(X264_CRF),
        "-pix_fmt",
        X264_PIX_FMT,
        "-g",
        str(X264_GOP),
        "-bf",
        str(X264_BFRAMES),
        "-movflags",
        "+faststart",
        str(destination),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stderr is not None
    tail: list[str] = []

    def read_output() -> None:
        for line in process.stderr:
            text = line.decode("utf-8", "replace").strip()
            if text:
                tail.append(text)

    reader = threading.Thread(target=read_output)
    reader.start()
    try:
        for frame in frames:
            process.stdin.write(frame)
    except BrokenPipeError:
        pass
    except BaseException:
        # A failing generator (bad plan, disk full, ...) must not leave FFmpeg
        # and the stderr reader thread alive.
        process.kill()
        raise
    finally:
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        reader.join()
        code = process.wait()
        try:
            process.stderr.close()
        except OSError:
            pass
    if code != 0:
        raise MediaError(tail[-1] if tail else "FFmpeg dừng với lỗi khi tạo vòng hiệu ứng.")


def _materialize(source: Path, destination: Path | None) -> None:
    if destination is None:
        return
    destination = Path(destination)
    try:
        if destination.resolve() == source.resolve():
            return
    except OSError:
        pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.unlink(missing_ok=True)
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _effect_label(effects: frozenset[str] | set[str]) -> str:
    return (
        " + ".join(
            EFFECT_LABELS.get(effect, effect)
            for effect in SUPPORTED_EFFECTS
            if effect in effects
        )
        or "hiệu ứng"
    )


def _render_loop(
    image_path: Path,
    plan: dict,
    destination: Path,
    *,
    duration: float,
    width: int,
    height: int,
    fps: int,
    log,
    effects: frozenset[str],
    fit_mode: str = "contain",
    strip_rect: tuple[int, int, int, int] | None = None,
    progress=None,
    pin: bool = False,
) -> Path | None:
    """Render every requested overlay in one shared encode pass.

    ``progress(done, total)`` is called periodically for long background jobs.
    """
    if not effects:
        return None
    label = _effect_label(effects)
    seconds, out_width, out_height, rate = _validate(duration, width, height, fps)
    source = Path(image_path)
    if not source.is_file():
        raise FileNotFoundError(f"Không thấy ảnh nền: {source}")

    mode = _cache_fit_mode(fit_mode)

    image_hash = _file_hash(source)
    expected_hash = str(plan.get("image_hash") or "").strip()
    if expected_hash and expected_hash != image_hash:
        # Boxes/masks were compiled for different pixels. Rendering them over a
        # replaced file would animate the wrong regions, so fall back to static.
        log(
            "Bỏ qua hiệu ứng: ảnh đã thay đổi nội dung so với lúc phân tích. "
            "Hãy phân tích lại ảnh."
        )
        return None
    plan_hash = visual_plan_hash(plan)
    key = motion_cache_key(
        image_hash=image_hash,
        plan=plan,
        duration=seconds,
        width=out_width,
        height=out_height,
        fps=rate,
        fit_mode=mode,
        strip_rect=strip_rect,
        effects=effects,
    )
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cached = motion_cache_path(
        image_hash=image_hash,
        plan=plan,
        duration=seconds,
        width=out_width,
        height=out_height,
        fps=rate,
        fit_mode=mode,
        strip_rect=strip_rect,
        effects=effects,
    )

    # Single-flight per cache key: acquire before checking the cache and
    # re-check inside the lock, so two threads rendering the same clip encode it
    # only once. Different keys use different locks and render in parallel.
    lock = _motion_lock(key)
    with lock:
        if cached.is_file() and cached.stat().st_size > 0:
            log(f"Dùng lại vòng {label} đã có cho {source.name}.")
            _materialize(cached, destination)
            return cached

        with Image.open(source) as opened:
            source_size = ImageOps.exif_transpose(opened).size
        # Fit straight into the output frame; resizing a landscape contain-letterbox
        # into 9:16 would distort the source.
        base = fit_image(source, (out_width, out_height), mode=mode)
        base_array = np.asarray(base, dtype=np.float32)

        transform = _fit_transform(plan, source_size, (out_width, out_height), mode)
        seed = int(hashlib.sha256(f"{image_hash}|{plan_hash}".encode("utf-8")).hexdigest()[:16], 16)

        # Snow shares rain's outdoor/dry + protect + strip mask: flakes never fall
        # indoors or over a protected face/text/lyric strip.
        weather_mask = None
        if effects.intersection({"rain", "snow"}):
            weather_mask = _build_mask(plan, transform, out_width, out_height, strip_rect)

        rain_layers: list[dict] = []
        rain_intensity = 1.0
        if "rain" in effects:
            rain_items = _effect_items(plan, "rain")
            rain_intensity = _items_intensity(rain_items, DEFAULT_INTENSITY)
            if rain_intensity > 1e-3:
                rain_layers = _make_layers(
                    out_width,
                    out_height,
                    seconds,
                    seed,
                    density_multiplier=_items_density(rain_items),
                    speed_multiplier=_items_speed(rain_items),
                )

        snow_layers: list[dict] = []
        snow_intensity = 1.0
        if "snow" in effects:
            snow_items = _effect_items(plan, "snow")
            snow_intensity = _items_intensity(snow_items, DEFAULT_INTENSITY)
            if snow_intensity > 1e-3:
                snow_layers = _make_snow_layers(
                    out_width,
                    out_height,
                    seconds,
                    seed,
                    density_multiplier=_items_density(snow_items),
                    speed_multiplier=_items_speed(snow_items),
                )

        prevent = None
        if effects.intersection(
            {
                "steam",
                "wind",
                "birds",
                "fire",
                "embers",
                "sunrays",
                "fog",
                "light",
                "water",
            }
        ):
            prevent = _build_protect_mask(plan, transform, out_width, out_height, strip_rect)
        prevent3 = prevent[:, :, None] if prevent is not None else None

        steam_sources = (
            _steam_sources(
                plan,
                transform,
                out_width,
                out_height,
                seconds,
                seed,
                base_array,
                log,
                prevent=prevent,
            )
            if "steam" in effects
            else []
        )
        wind_specs = (
            _wind_specs(plan, transform, out_width, out_height, seconds, seed, prevent, log)
            if "wind" in effects
            else []
        )
        water_spec = (
            _water_spec(
                plan,
                transform,
                out_width,
                out_height,
                seconds,
                prevent,
                log,
                base_array=base_array,
            )
            if "water" in effects
            else None
        )
        birds_specs = (
            _birds_spec(
                plan,
                transform,
                out_width,
                out_height,
                seconds,
                prevent,
                seed,
                log,
            )
            if "birds" in effects
            else []
        )
        fire_spec = (
            _fire_spec(plan, transform, out_width, out_height, seconds, prevent, seed, log)
            if "fire" in effects
            else None
        )
        embers_spec = (
            _embers_spec(
                plan,
                transform,
                out_width,
                out_height,
                seconds,
                prevent,
                seed,
                log,
            )
            if "embers" in effects
            else None
        )
        sunrays_spec = (
            _sunrays_spec(
                plan,
                transform,
                base_array,
                out_width,
                out_height,
                seconds,
                prevent,
                seed,
                log,
            )
            if "sunrays" in effects
            else None
        )
        fog_spec = (
            _fog_spec(plan, transform, out_width, out_height, seconds, prevent, seed, log)
            if "fog" in effects
            else None
        )
        if effects == {"water"} and water_spec is None:
            # Nothing resolvable: let the caller fall back to a static slide.
            log("Bỏ qua nước: không có vùng nước resolve được.")
        if effects == {"fire"} and fire_spec is None:
            # No fire object/region: skip gracefully and let the caller stay static.
            log("Bỏ qua lửa: không có object/vùng lửa resolve được.")
        light_spec = _light_spec(plan, base_array, prevent, seconds) if "light" in effects else None
        resolved = False
        if (rain_layers or snow_layers) and weather_mask is not None:
            resolved = float(weather_mask.max()) > 1e-4
        if water_spec is not None and water_spec["amplitude"] > 0.0:
            resolved = True
        if steam_sources or wind_specs:
            resolved = True
        if fire_spec is not None and fire_spec["amplitude"] > 0.0:
            resolved = True
        if embers_spec is not None and embers_spec["particles"]:
            resolved = True
        if birds_specs:
            resolved = True
        if sunrays_spec is not None:
            resolved = True
        if fog_spec is not None and fog_spec["alpha"] > 0.0:
            resolved = True
        if light_spec is not None and light_spec["amplitude"] > 0.0:
            resolved = True
        if not resolved:
            log("Bỏ qua vòng: không có hiệu ứng nào resolve được.")
            return None
        sprite_cache: dict = {}
        frame_count = max(1, int(round(seconds * rate)))

        def frames():
            for index in range(frame_count):
                t = index / rate
                u = (index / rate) / seconds
                frame = base_array.copy()

                # Water is a background surface: animate it before rain/snow so
                # falling streaks stay straight instead of being warped with it.
                if water_spec is not None:
                    frame = _apply_water(frame, water_spec, u)

                if weather_mask is not None and rain_layers:
                    overlay = np.zeros((out_height, out_width), dtype=np.float32)
                    for layer in rain_layers:
                        canvas = Image.new("L", (out_width, out_height), 0)
                        _draw_layer(ImageDraw.Draw(canvas), layer, t, u, out_height)
                        if layer["blur"] >= 0.3:
                            canvas = canvas.filter(ImageFilter.GaussianBlur(layer["blur"]))
                        overlay += np.asarray(canvas, dtype=np.float32)
                    overlay_rgb = overlay[:, :, None] * (
                        np.asarray(RAIN_COLOR, dtype=np.float32) / 255.0
                    )
                    overlay_rgb *= weather_mask[:, :, None]
                    if rain_intensity != 1.0:
                        overlay_rgb *= rain_intensity
                    frame += overlay_rgb

                if weather_mask is not None and snow_layers:
                    overlay = np.zeros((out_height, out_width), dtype=np.float32)
                    for layer in snow_layers:
                        canvas = Image.new("L", (out_width, out_height), 0)
                        _draw_snow_layer(ImageDraw.Draw(canvas), layer, t, u, out_height)
                        if layer["blur"] >= 0.3:
                            canvas = canvas.filter(ImageFilter.GaussianBlur(layer["blur"]))
                        overlay += np.asarray(canvas, dtype=np.float32)
                    overlay_rgb = overlay[:, :, None] * (
                        np.asarray(SNOW_COLOR, dtype=np.float32) / 255.0
                    )
                    overlay_rgb *= weather_mask[:, :, None]
                    if snow_intensity != 1.0:
                        overlay_rgb *= snow_intensity
                    frame += overlay_rgb

                if steam_sources:
                    plume = _steam_frame(steam_sources, u, sprite_cache)
                    if plume is not None:
                        steam_rgb, steam_alpha = plume
                        steam_rgb *= prevent3
                        steam_alpha *= prevent
                        frame = (
                            frame * (1.0 - steam_alpha[:, :, None])
                            + steam_rgb
                        )

                if wind_specs:
                    frame_u8 = np.clip(frame, 0.0, 255.0).astype(np.uint8)
                    warped = _wind_frame(Image.fromarray(frame_u8, "RGB"), wind_specs, u)
                    frame = np.asarray(warped, dtype=np.float32)

                if birds_specs:
                    frame = _apply_birds(frame, birds_specs, u)

                frame = _apply_fire(frame, fire_spec, u)
                frame = _apply_embers(frame, embers_spec, u, sprite_cache)
                frame = _apply_sunrays(frame, sunrays_spec, u)
                frame = _apply_fog(frame, fog_spec, u)
                frame = _apply_light(frame, light_spec, u)
                np.clip(frame, 0.0, 255.0, out=frame)
                if index % 48 == 0:
                    log(f"Vẽ {label} {index + 1}/{frame_count}.")
                if progress is not None and (
                    index % 8 == 0 or index + 1 == frame_count
                ):
                    try:
                        progress(index + 1, frame_count)
                    except Exception:
                        pass
                yield np.ascontiguousarray(frame, dtype=np.uint8).tobytes()

        temporary = CACHE_ROOT / f".{key}.{os.getpid()}.{uuid.uuid4().hex}.tmp.mp4"
        log(f"Đang tạo vòng {label} {seconds:.1f}s cho {source.name}.")
        try:
            _encode(frames(), temporary, out_width, out_height, rate, log)
            os.replace(temporary, cached)
            if pin:
                pin_cache_file(cached)
            try:
                prune(
                    CACHE_ROOT,
                    max_files=40,
                    max_bytes=2_000_000_000,
                    min_age_seconds=300,
                )
            except Exception:
                pass
        finally:
            temporary.unlink(missing_ok=True)

        _materialize(cached, destination)
        log(f"Đã tạo vòng {label} cho {source.name}.")
        return cached


def render_motion_loop(
    image_path: Path,
    plan: dict,
    destination: Path,
    *,
    duration: float,
    width: int = WIDTH,
    height: int = HEIGHT,
    fps: int = 30,
    fit_mode: str = "contain",
    strip_rect: tuple[int, int, int, int] | None = None,
    log=None,
    progress=None,
    pin: bool = False,
) -> Path | None:
    """Render all supported motion items in one loop/encode pass.

    Supports rain, snow, water (W1 shimmer), steam, wind, birds, fire, embers,
    sun rays, fog, and light.
    Returns ``None`` when the plan has no supported motion item, and also when
    no item resolves to a visible recipe (for example a water-only plan with no
    liquid region, a fire-only plan with no fire object/region, or steam/wind
    targets that no longer exist). Steam/wind items whose targets cannot be
    resolved from ``plan["objects"]`` are skipped with a log entry; the remaining
    recipes still render.
    """
    if log is None:
        log = lambda _message: None  # noqa: E731 - tiny no-op logger
    effects = frozenset(_active_effects(plan))
    if not effects:
        return None
    return _render_loop(
        image_path,
        plan,
        destination,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        log=log,
        effects=effects,
        fit_mode=fit_mode,
        strip_rect=strip_rect,
        progress=progress,
        pin=pin,
    )


def render_rain_loop(
    image_path: Path,
    plan: dict,
    destination: Path,
    *,
    duration: float,
    width: int = WIDTH,
    height: int = HEIGHT,
    fps: int = 30,
    fit_mode: str = "contain",
    strip_rect: tuple[int, int, int, int] | None = None,
    log=None,
) -> Path | None:
    """Compatibility wrapper: render only the rain item, if present.

    Kept for existing P0 callers/tests. New code should use
    :func:`render_motion_loop`, which renders rain together with steam, wind,
    and light in a single encode.
    """
    if log is None:
        log = lambda _message: None  # noqa: E731 - tiny no-op logger
    if not plan_has_rain(plan):
        return None
    return _render_loop(
        image_path,
        plan,
        destination,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        log=log,
        effects=frozenset({"rain"}),
        fit_mode=fit_mode,
        strip_rect=strip_rect,
    )

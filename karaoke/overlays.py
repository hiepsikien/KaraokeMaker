"""Manual whole-video overlays: snow, leaves, bokeh and light leak.

These effects are added **after** the background is assembled (cover + image
segments + outro) and before the lyric picture is composited.  They are the
"extra effects" group from ``docs/plan-effects-pages-publish.md`` and never
require Gemini: ``project.overlay_effects`` fully describes them.

Public API
----------

``apply_overlays(base_video, specs, profile, strip_rect, destination, log,
progress=None) -> (path, overlay_hash, degraded)``

* ``specs`` is a normalized ``project["overlay_effects"]`` list (see
  :func:`karaoke.presentation.normalize_overlay_effects`).  Disabled entries are
  ignored; ``kind`` is one of ``snow``, ``leaf``, ``bokeh``, ``lightleak``.
* ``profile`` is a :class:`karaoke.profiles.RenderProfile`, its id, or a dict
  with ``width``/``height``/``fps``.  It is only a fallback: the base video is
  probed, and decoded/encoded at its real size so small test clips work.
* ``strip_rect`` is ``(x, y, width, height)`` in output pixels.  It is zeroed
  when a spec uses ``mask = "avoid_lyrics"``; ``mask = "none"`` disables it.
* ``destination`` is the final ``background.mp4`` path.  On success it is
  replaced atomically.  On failure the base video is copied to it and the
  returned ``degraded`` flag is ``True`` so ``background_status`` can report a
  stale/degraded build instead of silently dropping the overlays.
* No enabled spec returns ``(base_video, hash, False)`` without re-encoding.
* ``progress(done_frames, total_frames)`` is best-effort and may be called from
  the streaming loop.

Compositing order is fixed (do not reorder): bokeh -> light leak -> snow ->
leaves.  Clouds are not here: clouds are a region warp inside the background
(see ``living._cloud_specs``).

The returned ``overlay_hash`` must become part of ``BackgroundBuild`` /
``BackgroundRef`` so a change in overlay settings invalidates the cached
background.  It never uses the final ``background_hash`` (that would loop).
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
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from karaoke import particles
from karaoke.presentation import normalize_overlay_effects

OVERLAY_RENDER_VERSION = 1
OVERLAY_ENCODER_VERSION = 1
X264_PRESET = "veryfast"
# A slightly lower CRF than motion clips keeps bokeh/leak gradients from banding.
X264_CRF = 16
X264_GOP = 60
X264_PIX_FMT = "yuv420p"
X264_BFRAMES = 0

PALETTES: dict[str, tuple[tuple[int, int, int], ...]] = {
    "warm": ((255, 179, 71), (255, 122, 89), (255, 214, 150), (224, 96, 48)),
    "cool": ((120, 180, 255), (80, 120, 220), (160, 220, 255), (90, 90, 200)),
    "pastel": ((255, 200, 220), (200, 230, 255), (220, 255, 220), (255, 240, 200)),
    "neon": ((255, 60, 180), (60, 255, 220), (180, 80, 255), (255, 220, 60)),
}

# 3 depth layers: count at 1080p, radius px, opacity multiplier, horizontal
# speed px/s, bob px, twinkle cycles/s.
BOKEH_LAYERS = (
    (46, 9, 0.55, 7.0, 3.0, 0.20),
    (32, 22, 0.50, 11.0, 4.0, 0.14),
    (18, 52, 0.42, 16.0, 5.0, 0.09),
)

# Light leak bands: (center 0..1, width 0..1, opacity weight).
LEAK_BANDS = ((0.38, 0.14, 1.0), (0.56, 0.08, 0.7), (0.72, 0.20, 0.45))


# ---------------------------------------------------------------------------
# Small helpers


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _profile_geometry(profile) -> dict:
    if isinstance(profile, str):
        try:
            from karaoke.profiles import get_profile

            profile = get_profile(profile)
        except Exception:  # unknown profile id: fall back to no geometry
            profile = None
    if profile is None:
        return {}
    if isinstance(profile, dict):
        width = int(profile.get("width") or 0)
        height = int(profile.get("height") or 0)
        fps = int(profile.get("fps") or 0)
        identity = str(profile.get("id") or "")
    else:
        width = int(getattr(profile, "width", 0) or 0)
        height = int(getattr(profile, "height", 0) or 0)
        fps = int(getattr(profile, "fps", 0) or 0)
        identity = str(getattr(profile, "id", "") or "")
    return {
        "id": identity,
        "width": width,
        "height": height,
        "fps": fps,
    }


def _fps_value(value) -> float:
    text = str(value or "").strip()
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        try:
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else 30.0
        except (TypeError, ValueError):
            return 30.0
    try:
        number = float(text)
    except (TypeError, ValueError):
        return 30.0
    return number if math.isfinite(number) and number > 0 else 30.0


def probe_video(path: Path) -> dict:
    """Return ``{width, height, fps, duration}`` for the base video."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or [{}]
    stream = streams[0] if isinstance(streams, list) and streams else {}
    fps = _fps_value(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or 30)
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("ffprobe không đọc được kích thước video nền.")
    return {
        "width": width,
        "height": height,
        "fps": max(1.0, fps),
        "duration": max(0.0, duration),
    }


# ---------------------------------------------------------------------------
# Identity


def overlay_hash(
    specs,
    profile=None,
    strip_rect=None,
    *,
    video_meta: dict | None = None,
    palettes: dict | None = None,
) -> str:
    """Stable hash of everything that changes the rendered overlay pixels.

    ``video_meta`` (width/height/fps/duration) is required for motion recipes
    because their cycle quantization depends on the clip length; pass the
    probed base metadata.  ``palettes`` carries the resolved ``auto`` palette so
    a new cover image changes the overlay identity.
    """
    normalized = normalize_overlay_effects(specs)
    enabled = [spec for spec in normalized if spec.get("enabled")]
    payload = {
        "overlay_render": OVERLAY_RENDER_VERSION,
        "encoder": OVERLAY_ENCODER_VERSION,
        "particle": particles.PARTICLE_VERSION,
        "profile": _profile_geometry(profile),
        "strip_rect": list(strip_rect) if strip_rect else None,
        "specs": enabled,
        "palettes": palettes or {},
        "video": {
            key: (round(float(video_meta.get(key) or 0.0), 3) if key == "duration" else video_meta.get(key))
            for key in ("width", "height", "fps", "duration")
        }
        if video_meta
        else None,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Palettes


def _color_saturation(color: tuple[int, int, int]) -> float:
    top = max(color)
    bottom = min(color)
    return 0.0 if top <= 0 else (top - bottom) / top


def auto_palette(frame: np.ndarray, count: int = 4) -> tuple[tuple[int, int, int], ...]:
    """Pick four dominant saturated colours from a frame, warm fallback."""
    try:
        image = Image.fromarray(np.asarray(frame, dtype=np.uint8), "RGB")
        image = image.resize((64, 64), Image.Resampling.BOX).quantize(colors=12)
        palette = image.getpalette() or []
        candidates = [
            tuple(int(value) for value in palette[index : index + 3])
            for index in range(0, min(len(palette), 36), 3)
        ]
    except Exception:
        candidates = []
    filtered: list[tuple[int, int, int]] = []
    for color in candidates:
        luminance = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
        if luminance < 24:
            continue
        if _color_saturation(color) < 0.16:
            continue
        filtered.append(color)
    if len(filtered) < count:
        filtered.extend(PALETTES["warm"])
    seen: set[tuple[int, int, int]] = set()
    result: list[tuple[int, int, int]] = []
    for color in filtered:
        key = tuple(min(255, int(round(channel / 16.0)) * 16) for channel in color)
        if key in seen:
            continue
        seen.add(key)
        result.append(tuple(int(channel) for channel in color))
        if len(result) >= count:
            break
    while len(result) < count:
        result.append(PALETTES["warm"][len(result) % len(PALETTES["warm"])])
    return tuple(result)


def resolve_palette(name: str, frame: np.ndarray | None = None) -> tuple[tuple[int, int, int], ...]:
    key = str(name or "auto").strip().lower()
    if key == "auto":
        return auto_palette(frame) if frame is not None else PALETTES["warm"]
    return PALETTES.get(key, PALETTES["warm"])


def _first_frame(path: Path, width: int, height: int) -> np.ndarray | None:
    if not _ffmpeg_available():
        return None
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    expected = width * height * 3
    if len(result.stdout) < expected:
        return None
    return np.frombuffer(result.stdout[:expected], dtype=np.uint8).reshape((height, width, 3))


# ---------------------------------------------------------------------------
# Masks


def _lyrics_mask(width: int, height: int, strip_rect) -> np.ndarray | None:
    if not strip_rect:
        return None
    try:
        x, y, strip_width, strip_height = (float(value) for value in strip_rect)
    except (TypeError, ValueError):
        return None
    if strip_width <= 0 or strip_height <= 0:
        return None
    mask = np.ones((height, width), dtype=np.float32)
    left = max(0, min(width, int(round(x))))
    top = max(0, min(height, int(round(y))))
    right = max(left, min(width, int(round(x + strip_width))))
    bottom = max(top, min(height, int(round(y + strip_height))))
    if right > left and bottom > top:
        mask[top:bottom, left:right] = 0.0
    return mask


def _spec_mask(mask_mode: str, width: int, height: int, strip_rect) -> np.ndarray | None:
    if str(mask_mode or "").strip().lower() == "avoid_lyrics":
        return _lyrics_mask(width, height, strip_rect)
    return None


# ---------------------------------------------------------------------------
# Bokeh


@lru_cache(maxsize=512)
def _bokeh_sprite(radius: int, variant: int) -> np.ndarray:
    """Round bokeh sprite: soft radial falloff plus a thin bright rim."""
    radius = max(2, int(radius))
    axis = np.arange(-radius, radius + 1, dtype=np.float32)
    distance = np.sqrt(axis[:, None] ** 2 + axis[None, :] ** 2) / float(radius)
    core = np.clip(1.0 - distance, 0.0, 1.0) ** (1.4 + 0.2 * (variant % 3))
    rim_radius = 0.78 + 0.05 * (variant % 3)
    rim = np.exp(-((distance - rim_radius) ** 2) / (2.0 * 0.035**2)) * 0.35
    alpha = np.clip(core * 0.85 + rim, 0.0, 1.0)
    side = radius * 2 + 1
    rgba = np.zeros((side, side, 4), dtype=np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = np.clip(alpha * 255.0, 0.0, 255.0).astype(np.uint8)
    return rgba


def _bokeh_layers(width: int, height: int, seconds: float, seed: int, spec: dict) -> list[dict]:
    params = spec.get("params") or {}
    density = max(0.0, min(2.0, float(params.get("density", 100)) / 100.0))
    speed = max(0.0, min(4.0, float(params.get("speed", 100)) / 100.0))
    size_scale = max(0.4, min(2.5, float(params.get("size", 100)) / 100.0)) * (height / 1080.0)
    frame_density = particles._density_scale(width, height)
    rng = np.random.default_rng(int(seed) ^ 0xB0CE)
    base_cycles = max(1, int(round(seconds / 4.0 * max(0.25, speed))))
    layers: list[dict] = []
    span_x = width * 1.16
    for count, radius, alpha, drift, bob, twinkle in BOKEH_LAYERS:
        particles_count = max(0, int(round(count * frame_density * density)))
        if particles_count == 0:
            continue
        raw_drift = drift * size_scale * speed * rng.uniform(0.7, 1.2, particles_count)
        # Quantize horizontal travel to whole spans over the clip so the seam
        # matches; a moving bokeh layer must not jump at loop restart.
        travel_cycles = np.maximum(
            0, np.round(raw_drift * seconds / span_x)
        ).astype(np.int16)
        quantized_drift = (
            travel_cycles.astype(np.float32) * span_x / max(0.05, seconds)
        )
        layers.append(
            {
                "xs": rng.uniform(-0.08 * width, 1.08 * width, particles_count).astype(np.float32),
                "ys": rng.uniform(0.02, 0.98, particles_count).astype(np.float32) * height,
                "radii": np.maximum(
                    3.0,
                    radius * size_scale * rng.uniform(0.65, 1.35, particles_count),
                ).astype(np.float32),
                "alpha": (alpha * rng.uniform(0.55, 1.0, particles_count)).astype(np.float32),
                "drift": quantized_drift,
                "bob": (bob * size_scale * rng.uniform(0.6, 1.2, particles_count)).astype(np.float32),
                "phase": rng.uniform(0.0, 2.0 * math.pi, particles_count).astype(np.float32),
                "cycles": (
                    rng.integers(base_cycles, base_cycles + 2, particles_count)
                    if speed > 0.0
                    else np.zeros(particles_count, dtype=np.int16)
                ),
                "twinkle_cycles": rng.integers(
                    max(1, base_cycles), max(2, base_cycles * 2 + 1), particles_count
                ).astype(np.int16),
                "twinkle_phase": rng.uniform(0.0, 2.0 * math.pi, particles_count).astype(np.float32),
                "span": span_x,
                "variants": rng.integers(0, 3, particles_count).astype(np.int8),
            }
        )
    return layers


def _apply_bokeh(
    frame: np.ndarray,
    layers: list[dict],
    spec: dict,
    colors,
    t: float,
    u: float,
    mask: np.ndarray | None,
) -> None:
    params = spec.get("params") or {}
    opacity = max(0.0, min(1.0, float(params.get("opacity", 35)) / 100.0))
    intensity = max(0.0, min(1.0, float(params.get("intensity", 45)) / 100.0))
    blend = str(params.get("blend") or "screen")
    height, width = frame.shape[:2]
    opacity *= 0.55 + 0.9 * intensity
    for layer in layers:
        xs = np.mod(layer["xs"] + layer["drift"] * t, layer["span"]) - 0.08 * width
        ys = layer["ys"] + layer["bob"] * np.sin(
            2.0 * math.pi * layer["cycles"].astype(np.float32) * u + layer["phase"]
        )
        twinkle = 0.62 + 0.38 * np.sin(
            2.0 * math.pi * layer["twinkle_cycles"].astype(np.float32) * u
            + layer["twinkle_phase"]
        )
        for index in range(len(xs)):
            x = float(xs[index])
            y = float(ys[index])
            radius = int(round(float(layer["radii"][index])))
            if x < -radius or x > width + radius or y < -radius or y > height + radius:
                continue
            color = colors[index % len(colors)]
            sprite = _bokeh_sprite(radius, int(layer["variants"][index]))
            effective = opacity * float(layer["alpha"][index]) * max(0.0, float(twinkle[index]))
            particles.blend_rgba_sprite(
                frame,
                sprite,
                x,
                y,
                effective,
                blend="screen" if blend != "soft_light" else "screen",
                screen_color=color,
                mask=mask,
            )


# ---------------------------------------------------------------------------
# Light leak


@lru_cache(maxsize=16)
def _leak_field(width: int, height: int, angle: float, variant: int) -> np.ndarray:
    """Static diagonal band field; per-frame sin only scales it."""
    angle_radians = math.radians(float(angle) + 90.0 * variant)
    xx, yy = np.meshgrid(
        np.linspace(0.0, 1.0, max(2, width), dtype=np.float32),
        np.linspace(0.0, 1.0, max(2, height), dtype=np.float32),
    )
    normal = xx * math.cos(angle_radians) + yy * math.sin(angle_radians)
    normal = (normal - normal.min()) / max(1e-5, float(normal.max() - normal.min()))
    field = np.zeros_like(normal)
    for center, width_ratio, weight in LEAK_BANDS:
        shifted_center = (center + 0.08 * variant) % 1.0
        field += weight * np.exp(
            -((normal - shifted_center) ** 2) / (2.0 * max(1e-4, width_ratio) ** 2)
        )
    field = field / max(1e-5, float(field.max()))
    image = Image.fromarray((field * 255.0).astype(np.uint8), "L")
    image = image.filter(ImageFilter.GaussianBlur(radius=max(2.0, width * 0.012)))
    return np.asarray(image, dtype=np.float32) / 255.0


def _apply_lightleak(
    frame: np.ndarray,
    spec: dict,
    colors,
    seconds: float,
    u: float,
    mask: np.ndarray | None,
) -> None:
    params = spec.get("params") or {}
    intensity = max(0.0, min(1.0, float(params.get("intensity", 35)) / 100.0))
    opacity = max(0.0, min(1.0, float(params.get("opacity", 30)) / 100.0))
    angle = float(params.get("angle", 35))
    height, width = frame.shape[:2]
    strength = 0.42 * intensity * (0.35 + 0.9 * opacity)
    if strength <= 0.0:
        return
    cycles = max(1, int(round(seconds / 7.0)))
    pulse = 0.62 + 0.38 * math.sin(2.0 * math.pi * cycles * u)
    color = np.asarray(colors[0], dtype=np.float32)
    for variant in (0, 1):
        field = _leak_field(width, height, angle, variant)
        light = field * (strength * pulse * (1.0 if variant == 0 else 0.55))
        if mask is not None:
            light = light * mask
        if not float(light.max()) > 1e-4:
            continue
        blend = np.clip(light[:, :, None] * color[None, None, :] / 255.0, 0.0, 1.0)
        frame[:] = 255.0 - (255.0 - frame) * (1.0 - blend)


# ---------------------------------------------------------------------------
# Snow + leaves


def _apply_snow(
    frame: np.ndarray,
    layers: list[dict],
    spec: dict,
    width: int,
    height: int,
    seconds: float,
    t: float,
    u: float,
    mask: np.ndarray | None,
) -> None:
    params = spec.get("params") or {}
    intensity = max(0.0, min(1.0, float(params.get("intensity", 55)) / 100.0))
    if intensity <= 0.0 or not layers:
        return
    canvas = np.zeros((height, width), dtype=np.float32)
    for layer in layers:
        positions = particles.snow_layer_positions(layer, t, u, height)
        kinds = layer.get("kinds")
        spin = layer.get("spin")
        rotation = layer.get("rotation")
        value = (float(layer.get("alpha") or 0) / 255.0) * (0.35 + 0.90 * intensity)
        for index in np.nonzero(positions["visible"])[0]:
            kind = "dot"
            if kinds is not None and int(kinds[index]) == 1:
                kind = "crystal"
            angle = None
            if rotation is not None:
                angle = float(rotation[index]) + 2.0 * math.pi * (
                    float(spin[index]) if spin is not None else 0.0
                ) * u
            sprite = particles.snow_sprite(kind, float(positions["radii"][index]), angle)
            particles.stamp_alpha(
                canvas,
                sprite,
                float(positions["xs"][index]),
                float(positions["ys"][index]),
                value,
                mask,
            )
    np.clip(canvas, 0.0, 1.0, out=canvas)
    if not float(canvas.max()) > 1e-4:
        return
    alpha = canvas[:, :, None]
    frame[:] = frame * (1.0 - alpha) + np.asarray(
        particles.SNOW_COLOR, dtype=np.float32
    )[None, None, :] * alpha


def _apply_leaves(
    frame: np.ndarray,
    layers: list[dict],
    spec: dict,
    width: int,
    height: int,
    seconds: float,
    t: float,
    u: float,
    mask: np.ndarray | None,
) -> None:
    params = spec.get("params") or {}
    intensity = max(0.0, min(1.0, float(params.get("intensity", 55)) / 100.0))
    if intensity <= 0.0 or not layers:
        return
    for layer in layers:
        positions = particles.leaf_layer_positions(layer, t, u, height)
        base_alpha = (float(layer.get("alpha") or 0) / 255.0) * (0.45 + 0.75 * intensity)
        colors = layer["colors"]
        for index in np.nonzero(positions["visible"])[0]:
            color = tuple(int(channel) for channel in colors[index])
            sprite = particles.leaf_sprite(
                int(layer["variants"][index]),
                float(positions["sizes"][index]),
                float(positions["angles"][index]),
                bool(positions["flips"][index]),
                color,
            )
            particles.blend_rgba_sprite(
                frame,
                sprite,
                float(positions["xs"][index]),
                float(positions["ys"][index]),
                base_alpha,
                blend="normal",
                mask=mask,
            )


# ---------------------------------------------------------------------------
# Streaming encode


def _decode_command(base: Path, width: int, height: int) -> list[str]:
    return [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(base),
        "-an",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-fps_mode",
        "passthrough",
        "pipe:1",
    ]


def _encode_command(destination: Path, width: int, height: int, fps: float) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        f"{fps:.6f}",
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


def _fallback(base_video: Path, destination: Path) -> Path:
    try:
        if Path(base_video).resolve() != Path(destination).resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base_video, destination)
            return destination
    except OSError:
        pass
    return Path(base_video)


def apply_overlays(
    base_video: Path,
    specs: list[dict],
    profile,
    strip_rect,
    destination: Path,
    log,
    progress=None,
) -> tuple[Path, str, bool]:
    """Render manual overlay effects over ``base_video``.

    Returns ``(path, overlay_hash, degraded)``.  See the module docstring for
    the full contract and the documented no-op/fallback behaviour.
    """
    base_video = Path(base_video)
    destination = Path(destination)
    if log is None:
        log = lambda _message: None  # noqa: E731 - tiny no-op logger
    specs = normalize_overlay_effects(specs)
    enabled = [spec for spec in specs if spec.get("enabled")]
    geometry = _profile_geometry(profile)
    if not enabled:
        return base_video, overlay_hash([], profile=profile, strip_rect=strip_rect), False

    if not _ffmpeg_available():
        log("Bỏ qua hiệu ứng thêm: chưa có ffmpeg/ffprobe trong PATH.")
        return base_video, overlay_hash(enabled, profile=profile, strip_rect=strip_rect), True
    if not base_video.is_file():
        log(f"Bỏ qua hiệu ứng thêm: không thấy video nền {base_video}.")
        return base_video, overlay_hash(enabled, profile=profile, strip_rect=strip_rect), True

    try:
        meta = probe_video(base_video)
    except Exception as error:
        log(f"Bỏ qua hiệu ứng thêm: không đọc được video nền ({error}).")
        return base_video, overlay_hash(enabled, profile=profile, strip_rect=strip_rect), True

    width, height = int(meta["width"]), int(meta["height"])
    fps = float(meta["fps"])
    seconds = float(meta["duration"]) or 0.0
    if seconds <= 0.0:
        seconds = 1.0

    # Resolve `auto` against the first frame before hashing: changing the cover
    # (and therefore the sampled palette) must change overlay identity.
    first_frame = None
    if any(
        str((spec.get("params") or {}).get("palette") or "") == "auto"
        for spec in enabled
        if spec["kind"] in {"bokeh", "lightleak"}
    ):
        first_frame = _first_frame(base_video, width, height)
    palettes: dict[str, list[list[int]]] = {}
    for spec in enabled:
        if spec["kind"] not in {"bokeh", "lightleak"}:
            continue
        key = str((spec.get("params") or {}).get("palette") or "auto")
        palettes[key] = [list(color) for color in resolve_palette(key, first_frame)]

    identity = overlay_hash(
        enabled,
        profile=profile,
        strip_rect=strip_rect,
        video_meta=meta,
        palettes=palettes,
    )

    masks = {
        mode: _spec_mask(mode, width, height, strip_rect)
        for mode in {str(spec.get("mask") or "avoid_lyrics") for spec in enabled}
    }
    colors = {key: [tuple(color) for color in value] for key, value in palettes.items()}
    for spec in enabled:
        key = str((spec.get("params") or {}).get("palette") or "auto")
        colors.setdefault(key, resolve_palette(key, first_frame))

    # Build immutable particle layers once, never inside the frame loop.
    prepared: list[tuple[dict, object]] = []
    for spec in enabled:
        params = spec.get("params") or {}
        kind = spec["kind"]
        if kind == "bokeh":
            value = _bokeh_layers(width, height, seconds, int(spec.get("seed") or 0), spec)
        elif kind == "snow":
            value = particles.build_snow_layers(
                width,
                height,
                seconds,
                int(spec.get("seed") or 0),
                density=float(params.get("density", 100)) / 100.0,
                speed=float(params.get("speed", 100)) / 100.0,
                size=float(params.get("size", 100)) / 100.0,
            )
        elif kind == "leaf":
            value = particles.build_leaf_layers(
                width,
                height,
                seconds,
                int(spec.get("seed") or 0),
                density=float(params.get("density", 100)) / 100.0,
                speed=float(params.get("speed", 100)) / 100.0,
                size=float(params.get("size", 100)) / 100.0,
                palette=str(params.get("palette") or "autumn"),
            )
        else:
            value = None
        prepared.append((spec, value))

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp.mp4"
    )
    temporary.unlink(missing_ok=True)

    frame_bytes = width * height * 3
    total_frames = max(1, int(round(seconds * fps)))
    decode = subprocess.Popen(
        _decode_command(base_video, width, height),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    encode = subprocess.Popen(
        _encode_command(temporary, width, height, fps),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert decode.stdout is not None and decode.stderr is not None
    assert encode.stdin is not None and encode.stderr is not None

    decode_tail: list[str] = []
    encode_tail: list[str] = []

    def _read(stream, tail: list[str]) -> None:
        for line in stream:
            text = line.decode("utf-8", "replace").strip()
            if text:
                tail.append(text)

    readers = [
        threading.Thread(target=_read, args=(decode.stderr, decode_tail)),
        threading.Thread(target=_read, args=(encode.stderr, encode_tail)),
    ]
    for reader in readers:
        reader.start()
    degraded = False
    error_message = ""
    done = 0
    try:
        while True:
            buffer = decode.stdout.read(frame_bytes)
            if not buffer or len(buffer) < frame_bytes:
                break
            frame = (
                np.frombuffer(buffer, dtype=np.uint8)
                .reshape((height, width, 3))
                .astype(np.float32)
            )
            t = done / fps
            u = (t / seconds) % 1.0

            # Fixed compositing order (see module docstring).
            for spec, layers in prepared:
                mask = masks.get(str(spec.get("mask") or "avoid_lyrics"))
                key = str((spec.get("params") or {}).get("palette") or "auto")
                if spec["kind"] == "bokeh":
                    _apply_bokeh(frame, layers, spec, colors[key], t, u, mask)
                elif spec["kind"] == "lightleak":
                    _apply_lightleak(frame, spec, colors[key], seconds, u, mask)
            for spec, layers in prepared:
                mask = masks.get(str(spec.get("mask") or "avoid_lyrics"))
                if spec["kind"] == "snow":
                    _apply_snow(frame, layers, spec, width, height, seconds, t, u, mask)
                elif spec["kind"] == "leaf":
                    _apply_leaves(frame, layers, spec, width, height, seconds, t, u, mask)

            np.clip(frame, 0.0, 255.0, out=frame)
            encode.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
            done += 1
            if progress is not None and (done % 8 == 0 or done >= total_frames):
                try:
                    progress(done, total_frames)
                except Exception:
                    pass
            if done % 120 == 0:
                log(f"Đang vẽ hiệu ứng thêm {done}/{total_frames} khung.")
    except BrokenPipeError:
        error_message = "FFmpeg dừng giữa chừng khi vẽ hiệu ứng thêm."
    except Exception as error:
        error_message = str(error)
    finally:
        try:
            decode.stdout.close()
        except OSError:
            pass
        try:
            encode.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        for reader in readers:
            reader.join()
        decode_code = decode.wait()
        encode_code = encode.wait()
        try:
            decode.stderr.close()
            encode.stderr.close()
        except OSError:
            pass

    if decode_code != 0:
        error_message = decode_tail[-1] if decode_tail else (error_message or "ffmpeg không giải mã được video nền.")
    if encode_code != 0:
        error_message = encode_tail[-1] if encode_tail else (error_message or "ffmpeg không mã hoá được video hiệu ứng.")
    if done == 0 and not error_message:
        error_message = "Video nền không có khung hình."

    if error_message:
        temporary.unlink(missing_ok=True)
        log(f"Không vẽ được hiệu ứng thêm: {error_message}. Dùng lại video nền.")
        return _fallback(base_video, destination), identity, True

    try:
        os.replace(temporary, destination)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        log(f"Không ghi được video hiệu ứng thêm: {error}. Dùng lại video nền.")
        return _fallback(base_video, destination), identity, True

    log(f"Đã thêm hiệu ứng ({', '.join(spec['kind'] for spec in enabled)}) cho video nền.")
    return destination, identity, False


__all__ = [
    "OVERLAY_RENDER_VERSION",
    "apply_overlays",
    "auto_palette",
    "overlay_hash",
    "probe_video",
    "resolve_palette",
]

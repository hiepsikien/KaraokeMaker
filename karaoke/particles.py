"""Shared particle sprites and loop-safe layer builders.

Both render paths for the manual weather overlays and the AI "living still"
effects must use the same particle geometry, otherwise AI snow and manual snow
look different and each caches its own copy of the sprite artwork.  This module
is the single source of truth for:

* snow flake layers (soft dots for far layers, 6-armed crystals for near ones),
* leaf layers (procedural leaf sprites, three depth layers, face flip + spin),
* sprite stamping helpers used by :mod:`karaoke.living` and
  :mod:`karaoke.overlays`.

Only Pillow + numpy are used.  Every layer quantizes its fall distance to an
integer number of span lengths over ``duration`` and its sway/spin to integer
cycle counts, so an 8-16 second clip loops without a visible seam.

``PARTICLE_VERSION`` must be part of every cache key that stores rendered
particles (see ``living.motion_cache_key`` and ``overlays.overlay_hash``).
Bump it whenever sprite artwork or motion changes.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

PARTICLE_VERSION = 4

WIDTH = 1920
HEIGHT = 1080

SNOW_COLOR = (250, 252, 255)

# count at 1080p, fall speed px/s at 1080p, flake radius px, alpha 0..255,
# blur px, sprite kind.  Far = dots, mid = dots + small crystals, near =
# crystals.  Keep this order: it is also the draw order (far first).
SNOW_LAYERS = (
    (240, 20.0, 1.1, 80, 0.9, "dot"),
    (180, 34.0, 1.9, 120, 0.6, "mixed"),
    (120, 52.0, 3.1, 165, 0.4, "crystal"),
)

# count at 1080p, fall speed px/s, sprite side px, alpha 0..255,
# sway amplitude multiplier.  Leaves fall slower than snow and are sparse.
LEAF_LAYERS = (
    (26, 11.0, 15.0, 210, 1.0),
    (20, 18.0, 22.0, 225, 1.2),
    (12, 27.0, 31.0, 235, 1.4),
)

LEAF_PALETTES: dict[str, tuple[tuple[int, int, int], ...]] = {
    "spring": ((79, 138, 61), (110, 160, 70), (140, 180, 80), (90, 150, 95)),
    "autumn": ((201, 162, 39), (201, 106, 43), (168, 50, 50), (150, 90, 35)),
    "mixed": (
        (79, 138, 61),
        (201, 162, 39),
        (201, 106, 43),
        (168, 50, 50),
        (140, 180, 80),
        (150, 90, 35),
    ),
}

LEAF_VARIANTS = 5
LEAF_ANGLE_BUCKETS = 8
LEAF_SIZE_BUCKETS = (10, 14, 18, 24, 30, 38, 48)


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _density_scale(width: int, height: int) -> float:
    return max(0.25, min(1.0, math.sqrt((width * height) / (WIDTH * HEIGHT))))


# ---------------------------------------------------------------------------
# Snow sprites


@lru_cache(maxsize=256)
def _snow_sprite_array(kind: str, radius: int) -> np.ndarray:
    """Pre-render one snow sprite as a float32 alpha mask (0..1)."""
    radius = max(1, int(radius))
    if kind == "crystal":
        # Supersample the spokes so small crystals stay legible.
        scale = 4
        side = (radius * 2 + 1) * scale
        image = Image.new("L", (side, side), 0)
        draw = ImageDraw.Draw(image)
        center = side / 2.0
        length = max(1.5, radius * 0.92) * scale
        width = max(1, int(round(scale * 0.55)))
        for spoke in range(6):
            angle = math.pi / 3.0 * spoke
            x = center + math.cos(angle) * length
            y = center + math.sin(angle) * length
            draw.line((center, center, x, y), fill=255, width=width)
        core = max(1.0, radius * 0.30) * scale
        draw.ellipse(
            (center - core, center - core, center + core, center + core),
            fill=255,
        )
        resized = image.resize((radius * 2 + 1, radius * 2 + 1), Image.Resampling.LANCZOS)
        return np.asarray(resized, dtype=np.float32) / 255.0
    # Soft round dot: same profile as the old living snow, with a slightly
    # harder core so distant flakes stay visible on bright skies.
    axis = np.arange(-radius, radius + 1, dtype=np.float32) / float(radius)
    distance = np.sqrt(axis[:, None] ** 2 + axis[None, :] ** 2)
    return np.clip(1.0 - distance, 0.0, 1.0) ** 1.35


@lru_cache(maxsize=512)
def _snow_sprite_rotated(kind: str, radius: int, angle_bucket: int) -> np.ndarray:
    sprite = _snow_sprite_array(kind, radius)
    if kind != "crystal":
        return sprite
    return _rotate_sprite(sprite, angle_bucket * (360.0 / 12.0))


def snow_sprite(kind: str, radius: float, angle: float | None = None) -> np.ndarray:
    """Return a cached alpha sprite for a snow dot/crystal at ``radius`` px.

    Crystals are 6-fold symmetric, so rotation is quantized to 30-degree
    buckets (12 cached variants) instead of rotating every particle per frame.
    """
    kind = "crystal" if str(kind).lower() == "crystal" else "dot"
    rounded = max(1, int(round(float(radius))))
    if angle is None or kind != "crystal":
        return _snow_sprite_array(kind, rounded)
    bucket = int(round((math.degrees(angle) % 60.0) / (60.0 / 12.0))) % 12
    return _snow_sprite_rotated(kind, rounded, bucket)


def build_snow_layers(
    width: int,
    height: int,
    duration: float,
    seed: int,
    *,
    density: float = 1.0,
    speed: float = 1.0,
    size: float = 1.0,
) -> list[dict]:
    """Build seamless snow layers.

    ``density``/``speed`` are multipliers (1.0 = default).  ``size`` is the
    full-size multiplier (1.0 = 100 %, the UI exposes 60-160 %).  Values are
    clamped so an invalid project field can never explode the particle count.
    """
    width = max(1, int(width))
    height = max(1, int(height))
    duration = max(0.05, float(duration))
    density = max(0.0, min(2.0, float(density)))
    speed = max(0.0, min(4.0, float(speed)))
    size = max(0.2, min(3.0, float(size)))
    size_scale = height / HEIGHT
    span = height + 200.0 * size_scale
    frame_density = _density_scale(width, height)
    rng = np.random.default_rng(int(seed) ^ 0x5A0F)
    base_sway = max(1, int(round(duration / 4.0)))
    layers: list[dict] = []
    for count, fall_speed, radius, alpha, blur, kind in SNOW_LAYERS:
        particles = max(0, int(round(count * frame_density * density)))
        if particles == 0:
            continue
        # Whole numbers of spans over the clip -> the seam matches exactly.
        cycles = (
            0
            if speed <= 0.0
            else max(1, int(round(fall_speed * size_scale * duration / span * speed)))
        )
        quantized = cycles * span / duration
        radii = (
            radius * size_scale * size * rng.uniform(0.55, 1.45, particles)
        ).astype(np.float32)
        if kind == "dot":
            kinds = np.zeros(particles, dtype=np.int8)
        elif kind == "crystal":
            kinds = np.ones(particles, dtype=np.int8)
        else:
            kinds = (rng.uniform(0.0, 1.0, particles) > 0.45).astype(np.int8)
        spin = rng.integers(base_sway, base_sway + 3, particles).astype(np.int16)
        layers.append(
            {
                "xs": rng.uniform(-0.05 * width, 1.05 * width, particles).astype(np.float32),
                "ys": rng.uniform(0.0, span, particles).astype(np.float32),
                "radii": radii,
                "drift": (
                    max(1.0, (2.5 + radius * 1.4) * size_scale)
                    * rng.uniform(0.5, 1.0, particles)
                ).astype(np.float32),
                "phase": rng.uniform(0.0, 2.0 * math.pi, particles).astype(np.float32),
                "speed": quantized,
                "span": span,
                "sway": rng.integers(base_sway, base_sway + 2, particles).astype(np.float32),
                "alpha": int(alpha),
                "blur": blur * max(0.35, size_scale),
                "kind": kind,
                "kinds": kinds,
                "spin": spin,
                "rotation": rng.uniform(0.0, 2.0 * math.pi, particles).astype(np.float32),
            }
        )
    return layers


def snow_layer_positions(layer: dict, t: float, u: float, height: int) -> dict:
    """Positions for one snow layer at time ``t``/loop phase ``u``."""
    span = layer["span"]
    ys = np.mod(layer["ys"] + layer["speed"] * t, span) - 100.0 * (height / HEIGHT)
    xs = layer["xs"] + layer["drift"] * np.sin(
        2.0 * math.pi * layer["sway"] * u + layer["phase"]
    )
    radii = layer["radii"]
    visible = (ys + radii > -2.0) & (ys - radii < height + 2.0)
    return {"xs": xs, "ys": ys, "radii": radii, "visible": visible}


def stamp_alpha(
    canvas: np.ndarray,
    sprite: np.ndarray,
    cx: float,
    cy: float,
    value: float,
    mask: np.ndarray | None = None,
) -> None:
    """Add ``value * sprite`` into a float 2D ``canvas`` at (cx, cy)."""
    if value <= 0.0:
        return
    out_height, out_width = canvas.shape[:2]
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
    patch = sprite[source_y0:source_y1, source_x0:source_x1] * value
    if mask is not None:
        patch = patch * mask[dest_y0:dest_y1, dest_x0:dest_x1]
    canvas[dest_y0:dest_y1, dest_x0:dest_x1] += patch


def snow_layer_canvas(layer: dict, width: int, height: int, t: float, u: float) -> np.ndarray:
    """Render one snow layer into a float32 intensity canvas (0..255)."""
    positions = snow_layer_positions(layer, t, u, height)
    canvas = np.zeros((height, width), dtype=np.float32)
    intensity = float(layer.get("alpha") or 0)
    kinds = layer.get("kinds")
    spin = layer.get("spin")
    rotation = layer.get("rotation")
    visible = positions["visible"]
    for index in np.nonzero(visible)[0]:
        radius = max(0.5, float(positions["radii"][index]))
        kind = "dot"
        if kinds is not None and int(kinds[index]) == 1:
            kind = "crystal"
        spin_value = float(spin[index]) if spin is not None else 0.0
        angle = None
        if rotation is not None:
            angle = float(rotation[index]) + 2.0 * math.pi * spin_value * u
        # Crystal spokes are 6-fold symmetric; rotation is quantized and cached
        # so distant dots stay cheap.
        sprite = snow_sprite(kind, radius, angle)
        stamp_alpha(
            canvas,
            sprite,
            float(positions["xs"][index]),
            float(positions["ys"][index]),
            intensity,
        )
    return canvas


def draw_snow_layer(draw, layer: dict, t: float, u: float, height: int) -> None:
    """Legacy ImageDraw renderer (grayscale L canvas) for one snow layer.

    Kept because older tests/callers draw snow through ``ImageDraw``; the
    production paths use :func:`snow_layer_canvas` and :func:`stamp_alpha`.
    """
    positions = snow_layer_positions(layer, t, u, height)
    intensity = int(round(float(layer.get("alpha") or 0)))
    kinds = layer.get("kinds")
    for index in np.nonzero(positions["visible"])[0]:
        x = float(positions["xs"][index])
        y = float(positions["ys"][index])
        radius = max(0.5, float(positions["radii"][index]))
        crystal = kinds is not None and int(kinds[index]) == 1 and radius >= 1.5
        if not crystal:
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=intensity)
            continue
        for spoke in range(6):
            angle = math.pi / 3.0 * spoke
            draw.line(
                (
                    x,
                    y,
                    x + math.cos(angle) * radius,
                    y + math.sin(angle) * radius,
                ),
                fill=intensity,
                width=1,
            )
        draw.ellipse([x - 0.6, y - 0.6, x + 0.6, y + 0.6], fill=intensity)


# ---------------------------------------------------------------------------
# Leaf sprites


@lru_cache(maxsize=64)
def _leaf_base(variant: int) -> Image.Image:
    """White leaf silhouette (alpha only) at 64x64."""
    side = 64
    image = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    # Big ellipse body tilted a little; stem + a central vein.
    draw.ellipse((14, 6, 50, 58), fill=(255, 255, 255, 255))
    draw.line((32, 8, 32, 60), fill=(255, 255, 255, 210), width=2)
    draw.line((32, 56, 38, 63), fill=(255, 255, 255, 235), width=3)
    # Vein side branches differ per variant so the set does not look cloned.
    for step in range(2 + variant % 2):
        offset = 16 + step * 12
        draw.line((32, offset, 20, offset + 7), fill=(255, 255, 255, 150), width=1)
        draw.line((32, offset + 4, 44, offset + 11), fill=(255, 255, 255, 150), width=1)
    return image


@lru_cache(maxsize=4096)
def _leaf_sprite_array(
    variant: int, size: int, angle_index: int, flip: bool, color: tuple[int, int, int]
) -> np.ndarray:
    base = _leaf_base(int(variant) % LEAF_VARIANTS).copy()
    pixels = np.asarray(base, dtype=np.uint8).copy()
    rgb = np.asarray(color, dtype=np.float32)
    alpha = pixels[:, :, 3].astype(np.float32) / 255.0
    # Keep white vein highlights readable over the palette colour.
    tinted = np.clip(
        rgb[None, None, :] * (0.72 + 0.28 * (pixels[:, :, :3].astype(np.float32) / 255.0)),
        0.0,
        255.0,
    )
    rotated = Image.fromarray(np.dstack([tinted, alpha * 255.0]).astype(np.uint8), "RGBA")
    angle = 360.0 / LEAF_ANGLE_BUCKETS * int(angle_index)
    rotated = rotated.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    if flip:
        rotated = rotated.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    side = max(4, int(size))
    rotated = rotated.resize((side, side), Image.Resampling.LANCZOS)
    return np.asarray(rotated, dtype=np.uint8)


def leaf_sprite(
    variant: int,
    size: float,
    angle: float,
    flip: bool,
    color: tuple[int, int, int],
) -> np.ndarray:
    """Return a cached RGBA leaf sprite (uint8) for one frame."""
    bucket = min(
        LEAF_SIZE_BUCKETS,
        key=lambda item: abs(item - float(size)),
    )
    angle_index = int(
        round((math.degrees(angle) % 360.0) / (360.0 / LEAF_ANGLE_BUCKETS))
    ) % LEAF_ANGLE_BUCKETS
    return _leaf_sprite_array(
        int(variant) % LEAF_VARIANTS,
        int(bucket),
        angle_index,
        bool(flip),
        tuple(int(channel) for channel in color),
    )


def resolve_leaf_palette(name: str) -> tuple[tuple[int, int, int], ...]:
    key = str(name or "autumn").strip().lower()
    return LEAF_PALETTES.get(key, LEAF_PALETTES["autumn"])


def build_leaf_layers(
    width: int,
    height: int,
    duration: float,
    seed: int,
    *,
    density: float = 1.0,
    speed: float = 1.0,
    size: float = 1.0,
    palette: str = "autumn",
) -> list[dict]:
    """Build seamless leaf layers with spin + face flip."""
    width = max(1, int(width))
    height = max(1, int(height))
    duration = max(0.05, float(duration))
    density = max(0.0, min(2.0, float(density)))
    speed = max(0.0, min(4.0, float(speed)))
    size = max(0.2, min(3.0, float(size)))
    size_scale = height / HEIGHT
    span = height + 260.0 * size_scale
    frame_density = _density_scale(width, height)
    colors = np.asarray(resolve_leaf_palette(palette), dtype=np.float32)
    rng = np.random.default_rng(int(seed) ^ 0x1EAF)
    base_sway = max(1, int(round(duration / 4.0)))
    layers: list[dict] = []
    for count, fall_speed, side, alpha, sway_scale in LEAF_LAYERS:
        particles = max(0, int(round(count * frame_density * density)))
        if particles == 0:
            continue
        cycles = (
            0
            if speed <= 0.0
            else max(1, int(round(fall_speed * size_scale * duration / span * speed)))
        )
        quantized = cycles * span / duration
        palette_index = rng.integers(0, len(colors), particles)
        layers.append(
            {
                "xs": rng.uniform(-0.08 * width, 1.08 * width, particles).astype(np.float32),
                "ys": rng.uniform(0.0, span, particles).astype(np.float32),
                "sizes": (
                    side * size_scale * size * rng.uniform(0.72, 1.28, particles)
                ).astype(np.float32),
                "speed": quantized,
                "span": span,
                "drift": (
                    max(2.0, side * size_scale * 2.2 * sway_scale)
                    * rng.uniform(0.6, 1.2, particles)
                ).astype(np.float32),
                "sway": rng.integers(base_sway, base_sway + 3, particles).astype(np.float32),
                "phase": rng.uniform(0.0, 2.0 * math.pi, particles).astype(np.float32),
                "spin": rng.integers(1, 3 + int(base_sway), particles).astype(np.int16),
                "rotation": rng.uniform(0.0, 2.0 * math.pi, particles).astype(np.float32),
                "flip": rng.integers(max(1, base_sway // 2), base_sway + 2, particles).astype(np.int16),
                "flip_phase": rng.uniform(0.0, 2.0 * math.pi, particles).astype(np.float32),
                "variants": rng.integers(0, LEAF_VARIANTS, particles).astype(np.int8),
                "colors": colors[palette_index],
                "alpha": int(alpha),
                "blur": 0.0,
                "kind": "leaf",
            }
        )
    return layers


def leaf_layer_positions(layer: dict, t: float, u: float, height: int) -> dict:
    """Positions/rotation for one leaf layer at time ``t``/loop phase ``u``."""
    span = layer["span"]
    ys = np.mod(layer["ys"] + layer["speed"] * t, span) - 130.0 * (height / HEIGHT)
    xs = layer["xs"] + layer["drift"] * np.sin(
        2.0 * math.pi * layer["sway"] * u + layer["phase"]
    )
    sizes = layer["sizes"]
    visible = (ys + sizes * 0.6 > -4.0) & (ys - sizes * 0.6 < height + 4.0)
    angles = layer["rotation"] + 2.0 * math.pi * layer["spin"].astype(np.float32) * u
    flips = np.sin(
        2.0 * math.pi * layer["flip"].astype(np.float32) * u + layer["flip_phase"]
    ) < 0.0
    return {
        "xs": xs,
        "ys": ys,
        "sizes": sizes,
        "angles": angles,
        "flips": flips,
        "visible": visible,
    }


def _rotate_sprite(sprite: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate a float alpha sprite, preserving its square footprint."""
    image = Image.fromarray(np.clip(sprite * 255.0, 0.0, 255.0).astype(np.uint8), "L")
    rotated = image.rotate(float(degrees), resample=Image.Resampling.BICUBIC, expand=False)
    return np.asarray(rotated, dtype=np.float32) / 255.0


def blend_rgba_sprite(
    frame: np.ndarray,
    sprite: np.ndarray,
    cx: float,
    cy: float,
    opacity: float = 1.0,
    *,
    blend: str = "normal",
    screen_color: tuple[float, float, float] | None = None,
    mask: np.ndarray | None = None,
) -> None:
    """Composite one uint8 RGBA sprite onto a float RGB frame.

    ``normal`` alpha-blends the sprite's own colour, ``screen`` adds light with
    ``1 - (1-a)(1-b)`` (used by bokeh/light leak), and ``add`` saturates at 255
    (used by the leaf/snow path when a solid colour is supplied).
    """
    if opacity <= 0.0:
        return
    out_height, out_width = frame.shape[:2]
    sprite_height, sprite_width = sprite.shape[:2]
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
    patch = sprite[source_y0:source_y1, source_x0:source_x1].astype(np.float32)
    alpha = patch[:, :, 3] / 255.0 * float(opacity)
    if mask is not None:
        alpha = alpha * mask[dest_y0:dest_y1, dest_x0:dest_x1]
    window = frame[dest_y0:dest_y1, dest_x0:dest_x1]
    if blend == "screen":
        color = np.asarray(screen_color if screen_color is not None else (255, 255, 255), dtype=np.float32)
        light = 1.0 - (1.0 - window / 255.0) * (1.0 - alpha[:, :, None] * color[None, None, :] / 255.0)
        window[:] = light * 255.0
    elif blend == "add":
        color = np.asarray(screen_color if screen_color is not None else (255, 255, 255), dtype=np.float32)
        window[:] = np.minimum(255.0, window + alpha[:, :, None] * color[None, None, :])
    else:
        color = patch[:, :, :3]
        window[:] = window * (1.0 - alpha[:, :, None]) + color * alpha[:, :, None]

"""Presentation settings, overlay specs and small pure helpers.

This module deliberately has no dependency on Pillow/FFmpeg or on
``karaoke.director`` so it can be imported from project loading, the render
pipeline and tests without cycles.
"""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

PROJECT_PRESENTATION_VERSION = 1

DEFAULT_COVER: dict[str, Any] = {
    "layout": "center",
    "label": "",
    "show_label": False,
    "title_size": None,
    "artist_size": None,
    "line_size": None,
    "uppercase": False,
    "text_color": "#fff4dc",
    "line_color": "#e8e0d2",
    "accent_color": "#e7b15a",
    "shadow": True,
    "stroke": 0,
    "bg_blur": 0,
    "bg_darken": 58,
    "veil": 70,
    "vignette": 0,
    "scrim": "none",
    "scrim_opacity": 0,
}

DEFAULT_INTERLUDE: dict[str, Any] = {
    "enabled": False,
    "min_gap": 8.0,
    "mode": "strip",
    "text": "Nhạc dạo",
    "show_next_line": False,
    "fade": 0.4,
}

DEFAULT_OUTRO: dict[str, Any] = {
    "enabled": False,
    "duration": 4.0,
    "image": "",
    "title": "",
    "subtext": "Cảm ơn đã xem",
    "handle": "",
    "show_title": True,
    "show_artist": True,
    "fade": 0.6,
}

DEFAULT_PRESENTATION: dict[str, Any] = {
    "cover": DEFAULT_COVER,
    "interlude": DEFAULT_INTERLUDE,
    "outro": DEFAULT_OUTRO,
}

DEFAULT_TYPOGRAPHY: dict[str, Any] = {
    "lyric_size": None,
    "lyric_color": "#fff8ec",
    "sung_color": "#ffc448",
    "bar_color": "#080604",
    "bar_opacity": 196,
    "shadow": True,
    "stroke": 0,
    "row_gap": None,
}

DEFAULT_WAVEFORM: dict[str, Any] = {
    "enabled": False,
    "source": "instrumental",
    "style": "bars",
    "anchor": "center",
    "x": 0.5,
    "y": 0.70,
    "width": 0.32,
    "height": 0.06,
    "color": "#ffc448",
    "accent": "#ff7a59",
    "opacity": 0.9,
    "background": True,
    "mode": "progress",
}

OVERLAY_DEFAULTS: dict[str, dict[str, Any]] = {
    "snow": {"intensity": 55, "density": 100, "speed": 100, "size": 100},
    "leaf": {"intensity": 55, "density": 100, "speed": 100, "size": 100, "palette": "autumn"},
    "bokeh": {
        "intensity": 45,
        "density": 100,
        "size": 100,
        "speed": 100,
        "opacity": 35,
        "blend": "screen",
        "palette": "auto",
    },
    "lightleak": {
        "intensity": 35,
        "speed": 100,
        "angle": 35,
        "opacity": 30,
        "blend": "screen",
        "palette": "warm",
    },
}

OVERLAY_KINDS = tuple(OVERLAY_DEFAULTS)
OVERLAY_PALETTES = ("auto", "warm", "cool", "pastel", "neon")
LEAF_PALETTES = ("spring", "autumn", "mixed")
OVERLAY_MASKS = ("none", "avoid_lyrics")
OVERLAY_BLENDS = ("screen", "soft_light", "normal")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return max(low, min(high, number))


def _int(value: Any, default: int, low: int, high: int) -> int:
    return int(round(_number(value, default, low, high)))


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
    return default


def _color(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if _HEX_RE.match(text) else default


def _optional_size(value: Any, low: int, high: int) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return max(low, min(high, number))


def _choice(value: Any, options: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip()
    return text if text in options else default


def _merge_known(defaults: dict[str, Any], payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    return {key: data.get(key, default) for key, default in defaults.items()}


def normalize_cover(payload: Any) -> dict[str, Any]:
    data = _merge_known(DEFAULT_COVER, payload)
    return {
        "layout": _choice(data["layout"], ("center", "bottom_left", "bottom_center"), "center"),
        "label": " ".join(str(data.get("label") or "").split())[:40],
        "show_label": _bool(data.get("show_label"), False),
        "title_size": _optional_size(data.get("title_size"), 12, 240),
        "artist_size": _optional_size(data.get("artist_size"), 10, 160),
        "line_size": _optional_size(data.get("line_size"), 10, 120),
        "uppercase": _bool(data.get("uppercase"), False),
        "text_color": _color(data.get("text_color"), DEFAULT_COVER["text_color"]),
        "line_color": _color(data.get("line_color"), DEFAULT_COVER["line_color"]),
        "accent_color": _color(data.get("accent_color"), DEFAULT_COVER["accent_color"]),
        "shadow": _bool(data.get("shadow"), True),
        "stroke": _int(data.get("stroke"), 0, 0, 12),
        "bg_blur": _int(data.get("bg_blur"), 0, 0, 40),
        "bg_darken": _int(data.get("bg_darken"), 58, 0, 100),
        "veil": _int(data.get("veil"), 70, 0, 100),
        "vignette": _int(data.get("vignette"), 0, 0, 100),
        "scrim": _choice(data.get("scrim"), ("none", "bottom", "full"), "none"),
        "scrim_opacity": _int(data.get("scrim_opacity"), 0, 0, 100),
    }


def normalize_interlude(payload: Any) -> dict[str, Any]:
    data = _merge_known(DEFAULT_INTERLUDE, payload)
    return {
        "enabled": _bool(data.get("enabled"), False),
        "min_gap": _number(data.get("min_gap"), 8.0, 8.0, 120.0),
        "mode": _choice(data.get("mode"), ("strip", "card"), "strip"),
        "text": " ".join(str(data.get("text") or "Nhạc dạo").split())[:80] or "Nhạc dạo",
        "show_next_line": _bool(data.get("show_next_line"), False),
        "fade": _number(data.get("fade"), 0.4, 0.0, 3.0),
    }


def normalize_outro(payload: Any) -> dict[str, Any]:
    data = _merge_known(DEFAULT_OUTRO, payload)
    return {
        "enabled": _bool(data.get("enabled"), False),
        "duration": _number(data.get("duration"), 4.0, 0.0, 60.0),
        "image": str(data.get("image") or "").strip(),
        "title": " ".join(str(data.get("title") or "").split())[:200],
        "subtext": " ".join(str(data.get("subtext") or "").split())[:200],
        "handle": " ".join(str(data.get("handle") or "").split())[:80],
        "show_title": _bool(data.get("show_title"), True),
        "show_artist": _bool(data.get("show_artist"), True),
        "fade": _number(data.get("fade"), 0.6, 0.0, 3.0),
    }


def normalize_presentation(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    return {
        "cover": normalize_cover(data.get("cover")),
        "interlude": normalize_interlude(data.get("interlude")),
        "outro": normalize_outro(data.get("outro")),
    }


def normalize_typography(payload: Any) -> dict[str, Any]:
    data = _merge_known(DEFAULT_TYPOGRAPHY, payload)
    raw_gap = data.get("row_gap")
    row_gap = None if raw_gap in (None, "") else _int(raw_gap, 0, 0, 80)
    return {
        "lyric_size": _optional_size(data.get("lyric_size"), 12, 160),
        "lyric_color": _color(data.get("lyric_color"), DEFAULT_TYPOGRAPHY["lyric_color"]),
        "sung_color": _color(data.get("sung_color"), DEFAULT_TYPOGRAPHY["sung_color"]),
        "bar_color": _color(data.get("bar_color"), DEFAULT_TYPOGRAPHY["bar_color"]),
        "bar_opacity": _int(data.get("bar_opacity"), 196, 0, 255),
        "shadow": _bool(data.get("shadow"), True),
        "stroke": _int(data.get("stroke"), 0, 0, 12),
        "row_gap": row_gap,
    }


def normalize_waveform(payload: Any) -> dict[str, Any]:
    data = _merge_known(DEFAULT_WAVEFORM, payload)
    return {
        "enabled": _bool(data.get("enabled"), False),
        "source": _choice(data.get("source"), ("instrumental", "original"), "instrumental"),
        "style": _choice(data.get("style"), ("bars", "line", "dots"), "bars"),
        "anchor": "center",
        "x": _number(data.get("x"), 0.5, 0.0, 1.0),
        "y": _number(data.get("y"), 0.84, 0.0, 1.0),
        "width": _number(data.get("width"), 0.34, 0.04, 1.0),
        "height": _number(data.get("height"), 0.07, 0.02, 0.4),
        "color": _color(data.get("color"), DEFAULT_WAVEFORM["color"]),
        "accent": _color(data.get("accent"), DEFAULT_WAVEFORM["accent"]),
        "opacity": _number(data.get("opacity"), 0.9, 0.0, 1.0),
        "background": _bool(data.get("background"), True),
        "mode": _choice(data.get("mode"), ("progress", "scroll"), "progress"),
    }


def normalize_overlay_effects(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind not in OVERLAY_DEFAULTS or kind in seen:
            continue
        seen.add(kind)
        defaults = OVERLAY_DEFAULTS[kind]
        raw_params = item.get("params") if isinstance(item.get("params"), dict) else {}
        params: dict[str, Any] = {}
        for key, default in defaults.items():
            if key in {"palette", "blend"}:
                continue
            high = 360 if key == "angle" else 100 if key == "opacity" else 200
            params[key] = _int(raw_params.get(key, default), int(default), 0, high)
        if "palette" in defaults:
            choices = LEAF_PALETTES if kind == "leaf" else OVERLAY_PALETTES
            params["palette"] = _choice(raw_params.get("palette"), choices, str(defaults["palette"]))
        if "blend" in defaults:
            params["blend"] = _choice(raw_params.get("blend"), OVERLAY_BLENDS, str(defaults["blend"]))
        seed = item.get("seed")
        try:
            seed_value = int(seed)
        except (TypeError, ValueError):
            seed_value = 0
        result.append(
            {
                "id": str(item.get("id") or f"ovl_{kind}_{index + 1}")[:80],
                "kind": kind,
                "enabled": _bool(item.get("enabled"), False),
                "params": params,
                "mask": _choice(item.get("mask"), OVERLAY_MASKS, "avoid_lyrics"),
                "seed": seed_value,
            }
        )
    return result


def _box_value(box: Any, key: str, default: float) -> float:
    if isinstance(box, dict):
        try:
            return float(box.get(key, default))
        except (TypeError, ValueError):
            return default
    if isinstance(box, (list, tuple)) and len(box) >= 4:
        mapping = {"y1": 0, "x1": 1, "y2": 2, "x2": 3}
        try:
            return float(box[mapping[key]])
        except (IndexError, KeyError, TypeError, ValueError):
            return default
    return default


def _display_labels(objects: list[dict[str, Any]]) -> dict[str, str]:
    order = sorted(
        range(len(objects)),
        key=lambda index: (
            _box_value(objects[index].get("box"), "y1", 0.0),
            _box_value(objects[index].get("box"), "x1", 0.0),
            index,
        ),
    )
    counts: dict[str, int] = {}
    for obj in objects:
        label = " ".join(str(obj.get("label") or "object").split()) or "object"
        counts[label] = counts.get(label, 0) + 1
    used: set[str] = set()
    labels: dict[str, str] = {}
    seen: dict[str, int] = {}
    for index in order:
        obj = objects[index]
        label = " ".join(str(obj.get("label") or "object").split()) or "object"
        if counts.get(label, 0) <= 1:
            candidate = label
        else:
            seen[label] = seen.get(label, 0) + 1
            candidate = f"{label}_{seen[label]}"
        base = candidate
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        identity = str(obj.get("id") or f"object-{index + 1}")
        labels[identity] = candidate
    return labels


def normalize_plan_objects(plan: Any) -> dict[str, Any]:
    """Return a deep-copied plan with unique display labels and deduped protect."""
    if not isinstance(plan, dict):
        return {}
    data = copy.deepcopy(plan)
    objects = data.get("objects")
    if isinstance(objects, list):
        clean_objects = [dict(item) for item in objects if isinstance(item, dict)]
        labels = _display_labels(clean_objects)
        for index, obj in enumerate(clean_objects):
            identity = str(obj.get("id") or f"object-{index + 1}")
            obj["display_label"] = labels.get(identity) or str(obj.get("label") or "object")
            group = str(obj.get("group_id") or "").strip()
            if group:
                obj["group_id"] = group[:80]
            else:
                obj.pop("group_id", None)
        data["objects"] = clean_objects
    protect = data.get("protect")
    if isinstance(protect, list):
        cleaned: list[dict[str, Any]] = []
        used: set[str] = set()
        for index, item in enumerate(entry for entry in protect if isinstance(entry, dict)):
            label = " ".join(str(item.get("label") or f"protect-{index + 1}").split()) or f"protect-{index + 1}"
            candidate = label
            suffix = 2
            while candidate in used:
                candidate = f"{label}_{suffix}"
                suffix += 1
            used.add(candidate)
            cleaned.append({**item, "label": candidate})
        data["protect"] = cleaned
    return data


def strip_display_labels(value: Any) -> Any:
    """Recursively remove UI-only ``display_label`` keys from plan copies."""
    if isinstance(value, dict):
        return {
            key: strip_display_labels(item)
            for key, item in value.items()
            if key != "display_label"
        }
    if isinstance(value, list):
        return [strip_display_labels(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def presentation_hash(presentation: Any, typography: Any = None) -> str:
    import hashlib

    payload = _canonical(
        {
            "presentation": normalize_presentation(presentation),
            "typography": normalize_typography(typography),
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cover_style(presentation: Any) -> dict[str, Any]:
    return normalize_presentation(presentation)["cover"]


def outro_style(presentation: Any) -> dict[str, Any]:
    return normalize_presentation(presentation)["outro"]


def interlude_ranges(
    lines: list[dict[str, Any]],
    presentation: Any,
    *,
    countdown: float = 5.0,
    long_gap: float = 8.0,
) -> list[dict[str, Any]]:
    """Return interlude windows for long gaps between sung lines."""
    config = normalize_presentation(presentation)["interlude"]
    if not config["enabled"]:
        return []
    min_gap = max(float(config["min_gap"]), float(long_gap))
    ranges: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if index + 1 >= len(lines):
            continue
        end = float(line.get("end") or 0.0)
        next_line = lines[index + 1]
        start = float(next_line.get("start") or 0.0)
        if start - end < min_gap:
            continue
        window_start = end + 0.3
        window_end = start - float(countdown)
        if window_end - window_start <= 0.1:
            continue
        ranges.append(
            {
                "start": window_start,
                "end": window_end,
                "text": config["text"],
                "next_line": str(next_line.get("text") or ""),
                "show_next_line": bool(config["show_next_line"]),
            }
        )
    return ranges

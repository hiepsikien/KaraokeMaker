from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import threading
import unicodedata
import wave
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from karaoke.access import audio_root
from karaoke.lyrics import display_name_from_stem, song_key_from_stem
from karaoke.presentation import cover_style, outro_style
from karaoke.profiles import RenderProfile, get_profile

WIDTH = 1920
HEIGHT = 1080
# Bump when prepare_slide()/fit logic or cover card rendering changes so cached
# background identity is invalidated.
IMAGE_PREP_VERSION = 1
COVER_RENDER_VERSION = 2
STRIP_HEIGHT = 280
FONT_PATH = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class MediaError(RuntimeError):
    pass


# ffprobe is a subprocess, so cache by file identity. A status poll must not
# spawn three probes per second for an unchanged file.
_PROBE_CACHE: dict[tuple[str, int, int], float] = {}
_CHANNEL_CACHE: dict[tuple[str, int, int], int] = {}
_PROBE_LOCK = threading.Lock()


def _probe_key(path: Path) -> tuple[str, int, int]:
    try:
        stat = Path(path).stat()
    except OSError as error:
        raise MediaError(f"Không đọc được file: {Path(path).name}") from error
    return (str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    if not FONT_PATH.is_file():
        raise MediaError(f"Không thấy font tiếng Việt tại {FONT_PATH}.")
    return ImageFont.truetype(str(FONT_PATH), size=size)


def is_pcm16_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as handle:
            return (
                handle.getsampwidth() == 2
                and handle.getcomptype() == "NONE"
                and handle.getframerate() > 0
                and handle.getnchannels() > 0
            )
    except (wave.Error, EOFError, OSError):
        return False


@contextmanager
def pcm16_wav(path: Path):
    """Yield a 16-bit PCM wav. Other audio is converted with ffmpeg for the vocal fit."""
    if is_pcm16_wav(path):
        yield path
        return
    if shutil.which("ffmpeg") is None:
        raise MediaError(f"{path.name} không phải WAV 16-bit và chưa có ffmpeg để chuyển.")
    temp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    temp.close()
    destination = Path(temp.name)
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(destination),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not destination.is_file():
            detail = result.stderr.strip().splitlines()
            raise MediaError(detail[-1] if detail else f"Không chuyển được {path.name} sang WAV 16-bit.")
        yield destination
    finally:
        destination.unlink(missing_ok=True)


def probe_duration(path: Path) -> float:
    key = _probe_key(path)
    with _PROBE_LOCK:
        cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise MediaError(result.stderr.strip() or f"Không đọc được thời lượng của {path.name}.")
    try:
        value = float(result.stdout.strip())
    except ValueError as error:
        raise MediaError(f"Thời lượng không hợp lệ: {path.name}") from error
    with _PROBE_LOCK:
        _PROBE_CACHE[key] = value
    return value


def probe_channels(path: Path) -> int:
    key = _probe_key(path)
    with _PROBE_LOCK:
        cached = _CHANNEL_CACHE.get(key)
    if cached is not None:
        return cached
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=channels",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        channels = int(result.stdout.strip())
    except ValueError:
        channels = 2
    value = channels if channels > 0 else 2
    with _PROBE_LOCK:
        _CHANNEL_CACHE[key] = value
    return value


def _open_rgb(source: Path) -> Image.Image:
    """Open an image post-EXIF and composite alpha before dropping it."""
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        has_alpha = image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        )
        if has_alpha:
            rgba = image.convert("RGBA")
            canvas = Image.new("RGBA", rgba.size, (12, 10, 8, 255))
            canvas.alpha_composite(rgba)
            return canvas.convert("RGB")
        return image.convert("RGB")


def fit_image(
    source: Path,
    size: tuple[int, int] = (WIDTH, HEIGHT),
    mode: str = "contain",
) -> Image.Image:
    """Fit an image into ``size``. ``contain`` letterboxes; ``cover`` crops."""
    image = _open_rgb(source)
    normalized = str(mode or "contain").strip().lower()
    if normalized == "cover":
        return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
    contained = ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (12, 10, 8))
    canvas.paste(contained, ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2))
    return canvas


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, limit: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= limit:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _hex_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) == 8:
        try:
            alpha = int(text[6:8], 16)
        except ValueError:
            pass
        text = text[:6]
    if len(text) != 6:
        return (255, 244, 220, alpha)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), alpha)
    except ValueError:
        return (255, 244, 220, alpha)


def _radial_overlay(size: tuple[int, int], color: tuple[int, int, int], strength: int) -> Image.Image:
    """Dark vignette: transparent centre, dark edges."""
    if strength <= 0:
        return Image.new("RGBA", size, (0, 0, 0, 0))
    gradient = Image.radial_gradient("L").resize(size, Image.Resampling.BILINEAR)
    alpha = gradient.point(lambda value: int(value * max(0, min(100, strength)) / 100))
    overlay = Image.new("RGBA", size, (*color, 0))
    overlay.putalpha(alpha)
    return overlay


def _scrim_overlay(size: tuple[int, int], mode: str, opacity: int, color=(0, 0, 0)) -> Image.Image:
    width, height = size
    if mode == "none" or opacity <= 0:
        return Image.new("RGBA", size, (0, 0, 0, 0))
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    if mode == "full":
        draw.rectangle([0, 0, width, height], fill=(*color, int(opacity * 255 / 100)))
        return overlay
    # bottom scrim: transparent at 35%, solid at bottom
    start = int(height * 0.35)
    for y in range(start, height):
        ratio = (y - start) / max(1, height - start)
        draw.line([(0, y), (width, y)], fill=(*color, int(opacity * 255 / 100 * ratio)))
    return overlay


def _prepare_card_background(photo: Path, profile: RenderProfile, style: dict) -> Image.Image:
    base = fit_image(photo, (profile.width, profile.height), mode=profile.fit_mode).convert("RGBA")
    blur = int(style.get("bg_blur") or 0)
    if blur > 0:
        base = base.filter(ImageFilter.GaussianBlur(radius=blur))
    darken = max(0, min(100, int(style.get("bg_darken", 58) or 0)))
    if darken:
        base = ImageEnhance.Brightness(base).enhance(max(0.0, 1.0 - darken / 100.0))
    veil = max(0, min(100, int(style.get("veil", 70) or 0)))
    if veil:
        base.alpha_composite(Image.new("RGBA", base.size, (0, 0, 0, int(veil * 255 / 100))))
    base.alpha_composite(_radial_overlay(base.size, (0, 0, 0), int(style.get("vignette") or 0)))
    base.alpha_composite(
        _scrim_overlay(
            base.size,
            str(style.get("scrim") or "none"),
            int(style.get("scrim_opacity") or 0),
        )
    )
    return base


def _draw_spaced_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    spacing: int,
) -> None:
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + spacing


def _card_blocks(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    max_width: int,
    uppercase: bool,
) -> list[tuple[str, ImageFont.FreeTypeFont, tuple[int, int, int, int]]]:
    value = text.upper() if uppercase else text
    return [(line, font, fill) for line in _wrap(draw, value.strip(), font, max_width)]


def _compose_card(
    photo: Path,
    blocks_spec: list[tuple[str, int, tuple[int, int, int, int]]],
    destination: Path,
    profile: RenderProfile,
    style: dict,
    *,
    label: str = "",
) -> None:
    base = _prepare_card_background(photo, profile, style)
    draw = ImageDraw.Draw(base)
    uppercase = bool(style.get("uppercase"))
    shadow = bool(style.get("shadow", True))
    stroke = max(0, int(style.get("stroke") or 0))
    max_width = max(120, min(profile.cover_max_width, int(profile.width * 0.86)))
    blocks: list[tuple[str, ImageFont.FreeTypeFont, tuple[int, int, int, int], bool]] = []
    label_font = load_font(max(14, int(profile.cover_artist_size * 0.45)))
    if label:
        blocks.append((
            label.upper(),
            label_font,
            _hex_rgba(style.get("accent_color", "#e7b15a")),
            True,
        ))
    for text, size, fill in blocks_spec:
        if not str(text or "").strip():
            continue
        font_size = size if size and size > 0 else profile.cover_title_size
        font = load_font(font_size)
        for line, _font, _fill in _card_blocks(draw, str(text), font, fill, max_width, uppercase):
            blocks.append((line, font, fill, False))
    if not blocks:
        base.convert("RGB").save(destination, quality=95)
        return
    heights: list[int] = []
    widths: list[int] = []
    for text, font, _fill, spaced in blocks:
        box = draw.textbbox((0, 0), text, font=font)
        if spaced:
            spacing = max(1, int(font.size * 0.16))
            width = int(sum(draw.textlength(char, font=font) for char in text) + spacing * max(0, len(text) - 1))
        else:
            width = int(box[2] - box[0])
        heights.append(max(1, int(box[3] - box[1])))
        widths.append(max(1, width))
    gap = max(10, int(profile.height * 0.016))
    total = sum(heights) + gap * (len(blocks) - 1)
    layout = str(style.get("layout") or "center")
    margin = max(48, int(profile.width * 0.06))
    if layout == "bottom_left":
        x_base = margin
        y = profile.height - total - max(80, int(profile.height * 0.12))
    elif layout == "bottom_center":
        x_base = profile.width // 2
        y = profile.height - total - max(80, int(profile.height * 0.12))
    else:
        x_base = profile.width // 2
        y = (profile.height - total) // 2
    for (text, font, fill, spaced), height, width in zip(blocks, heights, widths):
        x = x_base if layout == "bottom_left" else x_base - width // 2
        if spaced:
            _draw_spaced_text(draw, (x, y), text, font, fill, max(1, int(font.size * 0.16)))
        else:
            if shadow:
                draw.text(
                    (x + 2, y + 3),
                    text,
                    font=font,
                    fill=(0, 0, 0, 160),
                    stroke_width=stroke,
                    stroke_fill=(0, 0, 0, 160),
                )
            draw.text(
                (x, y),
                text,
                font=font,
                fill=fill,
                stroke_width=stroke,
                stroke_fill=(0, 0, 0, 170) if stroke else None,
            )
        y += height + gap
    destination.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(destination, quality=95)


def make_cover(
    photo: Path,
    title: str,
    artist: str,
    cover_line: str,
    destination: Path,
    profile: RenderProfile | None = None,
    style: dict | None = None,
) -> None:
    profile = profile or get_profile(None)
    config = cover_style(style or {})
    text_color = _hex_rgba(config["text_color"])
    accent = _hex_rgba(config["accent_color"])
    line_color = _hex_rgba(config["line_color"])
    blocks = [
        (title, config.get("title_size") or profile.cover_title_size, text_color),
        (artist, config.get("artist_size") or profile.cover_artist_size, accent),
        (cover_line, config.get("line_size") or profile.cover_line_size, line_color),
    ]
    label = config.get("label") if config.get("show_label") else ""
    _compose_card(photo, blocks, destination, profile, config, label=str(label or ""))


def make_outro(
    photo: Path,
    title: str,
    artist: str,
    subtext: str,
    handle: str,
    destination: Path,
    profile: RenderProfile | None = None,
    style: dict | None = None,
) -> None:
    profile = profile or get_profile(None)
    config = outro_style(style or {})
    title_color = _hex_rgba("#fff4dc")
    accent = _hex_rgba("#e7b15a")
    line_color = _hex_rgba("#e8e0d2")
    blocks: list[tuple[str, int, tuple[int, int, int, int]]] = []
    if config.get("show_title", True):
        blocks.append((config.get("title") or title, profile.cover_title_size, title_color))
    if config.get("show_artist", True):
        blocks.append((artist, profile.cover_artist_size, accent))
    blocks.append((subtext, profile.cover_artist_size, line_color))
    if handle:
        blocks.append((handle, profile.cover_line_size, line_color))
    card_style = {
        "layout": "center",
        "uppercase": False,
        "shadow": True,
        "stroke": 0,
        "bg_blur": 0,
        "bg_darken": 62,
        "veil": 72,
        "vignette": 25,
        "scrim": "none",
        "scrim_opacity": 0,
    }
    _compose_card(photo, blocks, destination, profile, card_style)


def prepare_slide(
    source: Path,
    destination: Path,
    profile: RenderProfile | None = None,
) -> None:
    profile = profile or get_profile(None)
    fit_image(
        source,
        (profile.width, profile.height),
        mode=profile.fit_mode,
    ).save(destination, quality=95)


def _ratio_label(width: int, height: int) -> str:
    if height <= 0:
        return ""
    value = width / height
    known = (
        (1, 1, "1:1"),
        (3, 2, "3:2"),
        (4, 3, "4:3"),
        (16, 9, "16:9"),
        (2, 3, "2:3"),
        (9, 16, "9:16"),
        (4, 5, "4:5"),
        (5, 4, "5:4"),
    )
    for wide, tall, label in known:
        if abs(value - (wide / tall)) < 0.04:
            return label
    return f"{value:.2f}:1"


def _data_url(image: Image.Image, size: tuple[int, int], quality: int) -> str:
    preview = image.convert("RGB")
    preview.thumbnail(size, Image.Resampling.LANCZOS)
    buffer = BytesIO()
    preview.save(buffer, format="JPEG", quality=quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def inspect_image(
    source: Path,
    *,
    output_profile: str | None = None,
    with_preview: bool = False,
) -> dict:
    """Size, aspect, a thumbnail, and optionally a ~1280 preview.

    ``fits_frame`` compares against the selected render profile instead of a
    hardcoded 16:9 frame. Thumbnails are cheap and always returned; the large
    preview is opt-in so image lists do not ship megabytes of base64.
    """
    profile = get_profile(output_profile)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        width, height = image.size
        oriented = image.convert("RGB")
    thumbnail = _data_url(oriented, (360, 202), 80)
    preview = _data_url(oriented, (1280, 1280), 85) if with_preview else ""
    ratio = _ratio_label(width, height)
    frame_ratio = profile.width / profile.height
    fits = height > 0 and abs(width / height - frame_ratio) < 0.04
    if fits:
        note = f"Đúng tỉ lệ khung {profile.width}×{profile.height}, đầy khung video."
    else:
        note = (
            f"Tỉ lệ {ratio}. Khung video là {profile.width}×{profile.height}, "
            "ảnh này nằm giữa và có viền tối."
        )
    return {
        "path": str(source),
        "width": width,
        "height": height,
        "ratio": ratio,
        "fits_frame": fits,
        "note": note,
        "thumbnail": thumbnail,
        "preview": preview,
    }


def song_dirs_near(audio_path: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    cursor = audio_path.parent
    for _ in range(5):
        for candidate in (cursor / "Songs", cursor.parent / "Songs"):
            resolved = candidate.resolve() if candidate.exists() else candidate
            if resolved in seen:
                continue
            seen.add(resolved)
            if candidate.is_dir():
                found.append(candidate)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    default = audio_root() / "Songs"
    if default.is_dir() and default.resolve() not in {item.resolve() for item in found}:
        found.append(default)
    return found


def find_stems(audio_path: Path) -> tuple[Path | None, Path | None]:
    key = song_key_from_stem(audio_path.name)
    pairs: dict[Path, dict[str, Path]] = {}
    root = audio_root() / "Separated"
    if not root.is_dir():
        return None, None
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        if song_key_from_stem(candidate.name) != key:
            continue
        slot = pairs.setdefault(candidate.parent, {})
        if "_(Vocals)" in candidate.name:
            slot["vocal"] = candidate
        elif "_(Instrumental)" in candidate.name:
            slot["instrumental"] = candidate
    if not pairs:
        return None, None

    def newest(slot: dict[str, Path]) -> float:
        return max(path.stat().st_mtime for path in slot.values())

    chosen = max(pairs.values(), key=newest)
    return chosen.get("vocal"), chosen.get("instrumental")


def find_original(stem_path: Path) -> Path | None:
    key = song_key_from_stem(stem_path.name)
    for directory in song_dirs_near(stem_path):
        for audio in directory.iterdir():
            if audio.suffix.lower() not in AUDIO_SUFFIXES or not audio.is_file():
                continue
            if song_key_from_stem(audio.name) == key:
                return audio
    return None


def sibling_stem(path: Path) -> Path | None:
    name = path.name
    if "_(Vocals)" in name:
        other = name.replace("_(Vocals)", "_(Instrumental)")
    elif "_(Instrumental)" in name:
        other = name.replace("_(Instrumental)", "_(Vocals)")
    else:
        return None
    candidate = path.with_name(other)
    return candidate if candidate.is_file() else None


def safe_title(title: str) -> str:
    name = unicodedata.normalize("NFC", title).strip()
    name = "".join(" " if char in '\\/:*?"<>|' else char for char in name)
    name = " ".join(name.split())
    return name or "karaoke"


def write_timing(path: Path, lines: list[dict]) -> None:
    payload = []
    for line in lines:
        payload.append(
            {
                "text": line["text"],
                "start": line["start"],
                "end": line["end"],
                "words": [
                    {"text": word["text"], "start": word["start"], "end": word["end"]}
                    for word in line["words"]
                ],
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def display_title_for(path: Path) -> str:
    return display_name_from_stem(path.name)

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import unicodedata
import wave
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from karaoke.access import audio_root
from karaoke.lyrics import display_name_from_stem, song_key_from_stem
from karaoke.profiles import RenderProfile, get_profile

WIDTH = 1920
HEIGHT = 1080
# Bump when prepare_slide()/fit logic or cover card rendering changes so cached
# background identity is invalidated.
IMAGE_PREP_VERSION = 1
COVER_RENDER_VERSION = 1
STRIP_HEIGHT = 280
FONT_PATH = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class MediaError(RuntimeError):
    pass


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
        return float(result.stdout.strip())
    except ValueError as error:
        raise MediaError(f"Thời lượng không hợp lệ: {path.name}") from error


def probe_channels(path: Path) -> int:
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
        return 2
    return channels if channels > 0 else 2


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


def make_cover(
    photo: Path,
    title: str,
    artist: str,
    cover_line: str,
    destination: Path,
    profile: RenderProfile | None = None,
) -> None:
    profile = profile or get_profile(None)
    base = fit_image(
        photo,
        (profile.width, profile.height),
        mode=profile.fit_mode,
    ).convert("RGBA")
    darkened = ImageEnhance.Brightness(base).enhance(0.42)
    veil = Image.new("RGBA", base.size, (0, 0, 0, 70))
    darkened.alpha_composite(veil)
    draw = ImageDraw.Draw(darkened)
    title_font = load_font(profile.cover_title_size)
    artist_font = load_font(profile.cover_artist_size)
    line_font = load_font(profile.cover_line_size)
    blocks: list[tuple[str, ImageFont.FreeTypeFont, tuple[int, int, int, int]]] = []
    for text, font, fill in (
        (title, title_font, (255, 244, 220, 255)),
        (artist, artist_font, (231, 177, 90, 255)),
        (cover_line, line_font, (232, 224, 210, 255)),
    ):
        for line in _wrap(draw, text.strip(), font, profile.cover_max_width):
            blocks.append((line, font, fill))
    heights = []
    for line, font, _fill in blocks:
        box = draw.textbbox((0, 0), line, font=font)
        heights.append(box[3] - box[1])
    gap = 18
    total = sum(heights) + gap * (len(blocks) - 1 if blocks else 0)
    y = (profile.height - total) // 2
    for (line, font, fill), height in zip(blocks, heights):
        box = draw.textbbox((0, 0), line, font=font)
        width = box[2] - box[0]
        x = (profile.width - width) // 2
        draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0, 160))
        draw.text((x, y), line, font=font, fill=fill)
        y += height + gap
    destination.parent.mkdir(parents=True, exist_ok=True)
    darkened.convert("RGB").save(destination, quality=95)


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

from __future__ import annotations

import math
import subprocess
import threading
from pathlib import Path

from PIL import Image, ImageDraw

from karaoke.media import MediaError, load_font, probe_channels
from karaoke.presentation import interlude_ranges, normalize_presentation, normalize_typography
from karaoke.profiles import RenderProfile, get_profile

TEXT_FPS = 12
# Keep these in sync with web/src/lib/cue.ts (LONG_GAP_SECONDS, COUNTDOWN_SECONDS).
LONG_GAP = 8.0
COUNTDOWN = 5.0
# One shared font size; long lines wrap to as many rows as the strip fits.
LYRIC_FONT_SIZE = 56
MAX_LYRIC_WIDTH = 1700
LYRIC_ROW_GAP = 10
TEXT_PADDING_X = 4
BAR_PADDING_Y = 36


def _run(command: list[str], log) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    tail = ""
    try:
        for line in process.stdout:
            tail = line.strip() or tail
    finally:
        process.stdout.close()
    code = process.wait()
    if code != 0:
        raise MediaError(tail or "FFmpeg dừng với lỗi.")


def _row_width(widths: list[float], space: float) -> float:
    if not widths:
        return 0.0
    return sum(widths) + space * max(0, len(widths) - 1)


def _wrap_word_rows(widths: list[float], space: float, max_width: float) -> list[list[int]]:
    """Greedily pack words into as many rows as the width needs.

    Older code hard-capped at two rows, which clipped long lines on portrait
    profiles. The caller caps the row count against the strip height and
    shrinks the shared font size when even that does not fit.
    """
    count = len(widths)
    if count == 0:
        return [[]]
    rows: list[list[int]] = []
    current: list[int] = []
    current_width = 0.0
    for index, width in enumerate(widths):
        if not current:
            current = [index]
            current_width = width
            continue
        candidate = current_width + space + width
        if candidate <= max_width:
            current.append(index)
            current_width = candidate
        else:
            rows.append(current)
            current = [index]
            current_width = width
    if current:
        rows.append(current)
    return rows


def _available_lyric_width(profile: RenderProfile) -> int:
    """Usable text width between safe margins, minus the text bitmap padding."""
    usable = profile.width - max(0, profile.safe_left) - max(0, profile.safe_right)
    return max(1, min(profile.max_lyric_width, usable) - TEXT_PADDING_X)


def _rgba(value: str, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    from karaoke.media import _hex_rgba

    return _hex_rgba(value, 255) if value else default


def _fit_lyric_font_size(
    lines: list[dict],
    profile: RenderProfile,
    *,
    base_size: int | None = None,
    row_gap: int | None = None,
) -> int:
    """Largest shared font size that keeps every line inside the strip.

    One size for the whole song keeps the typography stable; long lines shrink
    the shared size instead of stretching past the frame.
    """
    max_width = _available_lyric_width(profile)
    strip_height = profile.strip_rect[3]
    gap = profile.lyric_row_gap if row_gap is None else max(0, int(row_gap))
    size = int(base_size or profile.lyric_font_size)
    for _ in range(16):
        font = load_font(size)
        space = float(font.getlength(" "))
        ascent, descent = font.getmetrics()
        row_height = ascent + descent + 8
        # The bar adds BAR_PADDING_Y; leave a small margin so it is never
        # clipped by the strip bitmap.
        max_rows = max(
            1,
            int(
                (strip_height - BAR_PADDING_Y - 8)
                // max(1, row_height + gap)
            ),
        )
        fits = True
        for line in lines:
            words = list(line.get("words") or [])
            if not words:
                continue
            widths = [float(font.getlength(word["text"])) for word in words]
            if max(widths) > max_width:
                fits = False
                break
            rows = _wrap_word_rows(widths, space, max_width)
            if len(rows) > max_rows:
                fits = False
                break
        if fits or size <= 12:
            return size
        size = max(12, int(size * 0.9))
    return size


def _render_text_pair(
    line: dict,
    profile: RenderProfile | None = None,
    font_size: int | None = None,
    *,
    lyric_color: tuple[int, int, int, int] = (255, 248, 236, 255),
    sung_color: tuple[int, int, int, int] = (255, 196, 72, 255),
    row_gap: int | None = None,
    shadow: bool = True,
    stroke: int = 0,
):
    profile = profile or get_profile(None)
    gap = profile.lyric_row_gap if row_gap is None else max(0, int(row_gap))
    words = list(line.get("words") or [])
    font = load_font(font_size or profile.lyric_font_size)
    space = float(font.getlength(" "))
    widths = [float(font.getlength(word["text"])) for word in words]
    if not words:
        empty = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        return {
            "white": empty,
            "gold": empty,
            "boxes": [],
            "widths": [],
            "space": space,
            "start": line.get("start", 0),
            "end": line.get("end", 0),
            "words": [],
        }

    rows = _wrap_word_rows(widths, space, _available_lyric_width(profile))
    ascent, descent = font.getmetrics()
    row_height = ascent + descent + 8
    row_widths = []
    for row in rows:
        row_widths.append(_row_width([widths[index] for index in row], space))
    image_width = max(1, int(max(row_widths) + TEXT_PADDING_X + stroke * 2))
    image_height = max(
        1,
        int(row_height * len(rows) + gap * max(0, len(rows) - 1) + stroke * 2),
    )
    white = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    gold = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    white_draw = ImageDraw.Draw(white)
    gold_draw = ImageDraw.Draw(gold)
    boxes: list[dict] = [{"x": 0.0, "y": 0.0, "width": 0.0, "height": float(row_height)} for _ in words]

    for row_index, row in enumerate(rows):
        y = stroke + row_index * (row_height + gap)
        x = max(float(stroke), (image_width - row_widths[row_index]) / 2)
        for word_index in row:
            word = words[word_index]
            width = widths[word_index]
            text_y = y + 4
            if shadow:
                white_draw.text((x + 2, text_y + 3), word["text"], font=font, fill=(0, 0, 0, 190))
                gold_draw.text((x + 2, text_y + 3), word["text"], font=font, fill=(0, 0, 0, 190))
            white_draw.text(
                (x, text_y),
                word["text"],
                font=font,
                fill=lyric_color,
                stroke_width=stroke,
                stroke_fill=(0, 0, 0, 170) if stroke else None,
            )
            gold_draw.text(
                (x, text_y),
                word["text"],
                font=font,
                fill=sung_color,
                stroke_width=stroke,
                stroke_fill=(0, 0, 0, 170) if stroke else None,
            )
            boxes[word_index] = {
                "x": x,
                "y": float(y),
                "width": width,
                "height": float(row_height),
            }
            x += width + space

    return {
        "white": white,
        "gold": gold,
        "boxes": boxes,
        "widths": widths,
        "space": space,
        "start": line["start"],
        "end": line["end"],
        "words": words,
    }


def _display_start(layouts: list[dict], index: int) -> float:
    """Show the next line the moment the previous note finishes. Long gaps stay blank."""
    layout = layouts[index]
    previous_end = layouts[index - 1]["end"] if index else 0.0
    if index == 0 or layout["start"] - previous_end >= LONG_GAP:
        return layout["start"]
    return previous_end


def _display_end(layouts: list[dict], index: int) -> float:
    layout = layouts[index]
    if index + 1 >= len(layouts):
        return layout["end"] + 0.7
    if layouts[index + 1]["start"] - layout["end"] >= LONG_GAP:
        return layout["end"] + 0.2
    return min(layout["end"], _display_start(layouts, index + 1))


def cue_at(layouts: list[dict], song_time: float) -> tuple[str, dict | None, int]:
    """Return sing, countdown, or blank for one moment in the song."""
    for index, layout in enumerate(layouts):
        previous_end = layouts[index - 1]["end"] if index else 0.0
        if layout["start"] - previous_end < LONG_GAP:
            continue
        if layout["start"] - COUNTDOWN <= song_time < layout["start"]:
            remaining = layout["start"] - song_time
            number = max(1, min(int(COUNTDOWN), math.ceil(remaining - 1e-9)))
            return "countdown", layout, number
    for index, layout in enumerate(layouts):
        if _display_start(layouts, index) <= song_time < _display_end(layouts, index):
            return "sing", layout, 0
    return "blank", None, 0


def _word_fill(word: dict, song_time: float) -> float:
    start = float(word["start"])
    end = max(float(word["end"]), start + 0.05)
    if song_time >= end:
        return 1.0
    if song_time <= start:
        return 0.0
    return (song_time - start) / (end - start)


def _draw_strip(
    layout: dict,
    song_time: float,
    profile: RenderProfile | None = None,
    *,
    bar_color: tuple[int, int, int, int] = (8, 6, 4, 196),
) -> Image.Image:
    profile = profile or get_profile(None)
    width = profile.width
    strip_height = profile.strip_rect[3]
    safe_left = max(0, profile.safe_left)
    safe_right = max(0, profile.safe_right)
    usable = max(1, width - safe_left - safe_right)
    strip = Image.new("RGBA", (width, strip_height), (0, 0, 0, 0))
    text = layout["white"]
    bar_width = min(usable, text.width + 56)
    bar_height = text.height + BAR_PADDING_Y
    bar = Image.new("RGBA", (bar_width, bar_height), bar_color)
    bar_x = safe_left + max(0, (usable - bar_width) // 2)
    bar_y = (strip_height - bar_height) // 2
    strip.alpha_composite(bar, (bar_x, bar_y))
    text_x = safe_left + max(0, (usable - text.width) // 2)
    text_y = (strip_height - text.height) // 2
    strip.alpha_composite(layout["white"], (text_x, text_y))
    boxes = layout.get("boxes") or []
    for index, word in enumerate(layout["words"]):
        fill = _word_fill(word, song_time)
        if fill <= 0 or index >= len(boxes):
            continue
        box = boxes[index]
        left = int(box["x"])
        top = int(box["y"])
        width = max(1, int(math.ceil(box["width"] * fill)))
        height = max(1, int(math.ceil(box["height"])))
        right = min(layout["gold"].width, left + width)
        bottom = min(layout["gold"].height, top + height)
        if right <= left or bottom <= top:
            continue
        crop = layout["gold"].crop((left, top, right, bottom))
        strip.alpha_composite(crop, (text_x + left, text_y + top))
    return strip


def _draw_countdown(
    layout: dict,
    number: int,
    profile: RenderProfile | None = None,
    *,
    sung_color: tuple[int, int, int, int] = (255, 196, 72, 255),
    bar_color: tuple[int, int, int, int] = (8, 6, 4, 196),
) -> Image.Image:
    profile = profile or get_profile(None)
    strip = _draw_strip(layout, layout["start"] - 1, profile, bar_color=bar_color)
    font = load_font(72)
    digit = str(number)
    draw = ImageDraw.Draw(strip)
    box = draw.textbbox((0, 0), digit, font=font)
    x = max(0, profile.safe_left) + 56
    y = (profile.strip_rect[3] - (box[3] - box[1])) // 2 - box[1]
    draw.text((x + 2, y + 3), digit, font=font, fill=(0, 0, 0, 190))
    draw.text((x, y), digit, font=font, fill=sung_color)
    return strip


def _draw_interlude(
    text: str,
    next_line: str,
    show_next: bool,
    profile: RenderProfile,
    *,
    lyric_color: tuple[int, int, int, int],
    bar_color: tuple[int, int, int, int],
) -> Image.Image:
    width = profile.width
    strip_height = profile.strip_rect[3]
    strip = Image.new("RGBA", (width, strip_height), (0, 0, 0, 0))
    font = load_font(max(28, int(profile.lyric_font_size * 0.82)))
    small = load_font(max(18, int(profile.lyric_font_size * 0.5)))
    draw = ImageDraw.Draw(strip)
    title = str(text or "Nhạc dạo")
    subtitle = str(next_line or "") if show_next else ""
    box = draw.textbbox((0, 0), title, font=font)
    sub_box = draw.textbbox((0, 0), subtitle, font=small) if subtitle else (0, 0, 0, 0)
    bar_width = min(width - max(0, profile.safe_left) - max(0, profile.safe_right), max(box[2] - box[0], sub_box[2] - sub_box[0]) + 120)
    bar_height = (box[3] - box[1]) + (sub_box[3] - sub_box[1]) + (70 if subtitle else 54)
    bar_x = (width - bar_width) // 2
    bar_y = (strip_height - bar_height) // 2
    strip.alpha_composite(Image.new("RGBA", (bar_width, bar_height), bar_color), (bar_x, bar_y))
    title_x = (width - (box[2] - box[0])) // 2
    title_y = bar_y + 24
    draw.text((title_x + 2, title_y + 3), title, font=font, fill=(0, 0, 0, 180))
    draw.text((title_x, title_y), title, font=font, fill=lyric_color)
    if subtitle:
        sub_x = (width - (sub_box[2] - sub_box[0])) // 2
        sub_y = title_y + (box[3] - box[1]) + 10
        draw.text((sub_x, sub_y), subtitle, font=small, fill=(220, 214, 202, 255))
    return strip


def _frame_at(
    layouts: list[dict],
    song_time: float,
    blank: Image.Image,
    profile: RenderProfile | None = None,
    *,
    interludes: list[dict] | None = None,
    typography: dict | None = None,
    lyric_color: tuple[int, int, int, int] = (255, 248, 236, 255),
    sung_color: tuple[int, int, int, int] = (255, 196, 72, 255),
    bar_color: tuple[int, int, int, int] = (8, 6, 4, 196),
) -> Image.Image:
    profile = profile or get_profile(None)
    if song_time < 0:
        return blank
    mode, layout, number = cue_at(layouts, song_time)
    if mode == "sing" and layout is not None:
        return _draw_strip(layout, song_time, profile, bar_color=bar_color)
    if mode == "countdown" and layout is not None:
        return _draw_countdown(
            layout,
            number,
            profile,
            sung_color=sung_color,
            bar_color=bar_color,
        )
    for item in interludes or []:
        if item["start"] <= song_time <= item["end"]:
            return _draw_interlude(
                item.get("text") or "Nhạc dạo",
                item.get("next_line") or "",
                bool(item.get("show_next_line")),
                profile,
                lyric_color=lyric_color,
                bar_color=bar_color,
            )
    return blank


def _waveform_rect(plan: dict, profile: RenderProfile) -> tuple[int, int, int, int] | None:
    if not plan.get("enabled"):
        return None
    width = max(8, int(round(float(plan.get("width") or 0.34) * profile.width)))
    height = max(8, int(round(float(plan.get("height") or 0.07) * profile.height)))
    cx = float(plan.get("x") or 0.5) * profile.width
    cy = float(plan.get("y") or 0.84) * profile.height
    x = int(round(cx - width / 2))
    y = int(round(cy - height / 2))
    x = max(0, min(profile.width - width, x))
    y = max(0, min(profile.height - height, y))
    strip_top, strip_bottom = profile.strip_rect[1], profile.strip_rect[1] + profile.strip_rect[3]
    if y + height > strip_top and y < strip_bottom:
        y = max(0, strip_top - height - 8)
    return x, y, width, height


def _overlay_text(
    lines: list[dict],
    total_duration: float,
    cover_duration: float,
    slideshow: Path,
    destination: Path,
    log,
    profile: RenderProfile | None = None,
    *,
    presentation: dict | None = None,
    typography: dict | None = None,
    waveform: dict | None = None,
    waveform_path: Path | None = None,
    total_frames: int | None = None,
    song_duration: float | None = None,
    progress=None,
) -> None:
    """Stream lyric frames into FFmpeg. Nothing is written as a PNG sequence."""
    profile = profile or get_profile(None)
    pres = normalize_presentation(presentation or {})
    typo = normalize_typography(typography or {})
    wave = dict(waveform or {})
    lyric_color = _rgba(typo["lyric_color"], (255, 248, 236, 255))
    sung_color = _rgba(typo["sung_color"], (255, 196, 72, 255))
    bar_color = _rgba(typo["bar_color"], (8, 6, 4, 255))
    bar_color = (bar_color[0], bar_color[1], bar_color[2], int(typo["bar_opacity"]))
    strip_height = profile.strip_rect[3]
    strip_top = profile.strip_rect[1]
    font_size = _fit_lyric_font_size(
        lines,
        profile,
        base_size=typo.get("lyric_size"),
        row_gap=typo.get("row_gap"),
    )
    layouts = [
        _render_text_pair(
            line,
            profile,
            font_size,
            lyric_color=lyric_color,
            sung_color=sung_color,
            row_gap=typo.get("row_gap"),
            shadow=bool(typo.get("shadow", True)),
            stroke=int(typo.get("stroke") or 0),
        )
        for line in lines
    ]
    interludes = interlude_ranges(lines, pres, countdown=COUNTDOWN, long_gap=LONG_GAP)
    if total_frames is not None and total_frames > 0:
        frame_count = max(1, int(math.ceil(total_frames * TEXT_FPS / max(1, profile.fps))))
    else:
        frame_count = max(1, int(round(total_duration * TEXT_FPS)))
    blank = Image.new("RGBA", (profile.width, strip_height), (0, 0, 0, 0))
    command = ["ffmpeg", "-y", "-i", str(slideshow)]
    if waveform_path is not None and Path(waveform_path).is_file():
        command += ["-i", str(waveform_path)]
    command += [
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgba",
        "-video_size",
        f"{profile.width}x{strip_height}",
        "-framerate",
        str(TEXT_FPS),
        "-i",
        "pipe:0",
    ]
    text_index = 2 if waveform_path is not None and Path(waveform_path).is_file() else 1
    if text_index == 2:
        rect = _waveform_rect(wave, profile)
        wave_end = cover_duration + (
            float(song_duration) if song_duration is not None else max(0.0, total_duration - cover_duration)
        )
        if rect is None:
            filter_graph = (
                f"[{text_index}:v]fps={profile.fps},format=rgba[text];"
                f"[0:v][text]overlay=0:{strip_top}:format=auto:shortest=1,format=yuv420p"
            )
        else:
            x, y, _w, _h = rect
            filter_graph = (
                f"[1:v]format=rgba[wave];"
                f"[0:v][wave]overlay=x={x}:y={y}:format=auto:eof_action=pass:"
                f"enable='between(t,{cover_duration:.3f},{wave_end:.3f})'[base];"
                f"[{text_index}:v]fps={profile.fps},format=rgba[text];"
                f"[base][text]overlay=0:{strip_top}:format=auto:shortest=1,format=yuv420p"
            )
    else:
        filter_graph = (
            f"[1:v]fps={profile.fps},format=rgba[text];"
            f"[0:v][text]overlay=0:{strip_top}:format=auto:shortest=1,format=yuv420p"
        )
    command += [
        "-filter_complex",
        filter_graph,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-an",
    ]
    if total_frames is not None and total_frames > 0:
        command += ["-frames:v", str(total_frames)]
    command += ["-movflags", "+faststart", str(destination)]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    tail: list[str] = []

    def read_output() -> None:
        for line in process.stdout:
            text = line.decode("utf-8", "replace").strip()
            if text:
                tail.append(text)

    reader = threading.Thread(target=read_output)
    reader.start()
    try:
        for index in range(frame_count):
            song_time = index / TEXT_FPS - cover_duration
            frame = _frame_at(
                layouts,
                song_time,
                blank,
                profile,
                interludes=interludes,
                typography=typo,
                lyric_color=lyric_color,
                sung_color=sung_color,
                bar_color=bar_color,
            )
            process.stdin.write(frame.tobytes())
            if index % 96 == 0:
                log(f"Vẽ chữ karaoke {index + 1}/{frame_count}.")
            if progress is not None and (index % 8 == 0 or index + 1 == frame_count):
                try:
                    progress(index + 1, frame_count)
                except Exception:
                    pass
    except BrokenPipeError:
        pass
    finally:
        process.stdin.close()
    reader.join()
    process.stdout.close()
    code = process.wait()
    if code != 0:
        raise MediaError(tail[-1] if tail else "FFmpeg dừng với lỗi.")


def _mux(
    video: Path,
    audio: Path,
    destination: Path,
    cover_duration: float,
    log,
    label: str,
    ambience: Path | None = None,
    loop_ambience: bool = False,
) -> None:
    log(f"Đang ghép tiếng cho bản {label}.")
    delay = int(round(cover_duration * 1000))
    channels = probe_channels(audio)
    delay_filter = "adelay=" + "|".join([str(delay)] * channels)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-i",
        str(audio),
    ]
    if ambience is not None:
        if loop_ambience:
            # The bed is capped at MAX_AMBIENT_DURATION seconds; loop it for
            # songs longer than that instead of dropping ambience mid-song.
            command += ["-stream_loop", "-1", "-i", str(ambience)]
        else:
            command += ["-i", str(ambience)]
        filter_graph = (
            f"[1:a]{delay_filter},apad[music];"
            "[2:a]volume=-32dB,highpass=f=180[amb];"
            "[music][amb]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
    else:
        filter_graph = f"[1:a]{delay_filter},apad[a]"
    command += [
        "-filter_complex",
        filter_graph,
        "-map",
        "0:v:0",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
    ]
    if destination.suffix.lower() == ".mp4":
        command += ["-movflags", "+faststart"]
    command.append(str(destination))
    _run(command, log)


def _image_key(path: Path) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(path)


def _effect_plan(effect: dict | None) -> dict | None:
    if not isinstance(effect, dict):
        return None
    plan = effect.get("effect_plan")
    return plan if isinstance(plan, dict) else None


# Ambience is opt-in per effect item via ``params.audio = true``. When several
# kinds ask for it, rain wins over water, and water over wind.
AMBIENT_EFFECTS = ("rain", "water", "wind")


def _audio_requested(value) -> bool:
    """Accept the plan's boolean plus the common JSON/string spellings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "on"}
    return False


def _ambient_kind(
    background_effects: list[dict] | None,
    background_images: list[Path] | None = None,
) -> str | None:
    """Pick the ambience bed requested by the plans, honouring the priority.

    Effects belonging to a removed background image are ignored, so deleting
    an image cannot leave its ambient audio in the export.
    """
    used = {_image_key(path) for path in background_images or []}
    plans: list[dict] = []
    for effect in background_effects or []:
        raw = str(effect.get("image_path") or "").strip() if isinstance(effect, dict) else ""
        if used and raw and _image_key(Path(raw)) not in used:
            continue
        plan = _effect_plan(effect)
        if plan is not None:
            plans.append(plan)
    for kind in AMBIENT_EFFECTS:
        for plan in plans:
            for item in plan.get("items") or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("effect") or "").strip().casefold() != kind:
                    continue
                params = item.get("params")
                if isinstance(params, dict) and _audio_requested(params.get("audio")):
                    return kind
    return None

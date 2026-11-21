from __future__ import annotations

import math
import shutil
import subprocess
import threading
from pathlib import Path

from PIL import Image, ImageDraw

from karaoke.ambient import MAX_DURATION as MAX_AMBIENT_DURATION
from karaoke.ambient import synthesize as synthesize_ambience
from karaoke.living import plan_has_motion, render_motion_loop
from karaoke.media import (
    MediaError,
    load_font,
    make_cover,
    prepare_slide,
    probe_channels,
    probe_duration,
)
from karaoke.profiles import RenderProfile, get_profile

TEXT_FPS = 12
# Keep these in sync with web/src/lib/cue.ts (LONG_GAP_SECONDS, COUNTDOWN_SECONDS).
LONG_GAP = 8.0
COUNTDOWN = 5.0
# One size for every lyric line; long lines wrap to at most two rows.
LYRIC_FONT_SIZE = 56
MAX_LYRIC_WIDTH = 1700
LYRIC_ROW_GAP = 10


def _run(command: list[str], log) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    tail = ""
    for line in process.stdout:
        tail = line.strip() or tail
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
    """Usable text width between the profile's left/right safe margins."""
    usable = profile.width - max(0, profile.safe_left) - max(0, profile.safe_right)
    return max(1, min(profile.max_lyric_width, usable))


def _fit_lyric_font_size(lines: list[dict], profile: RenderProfile) -> int:
    """Largest shared font size that keeps every line inside the strip.

    One size for the whole song keeps the typography stable; long lines shrink
    the shared size instead of stretching past the frame.
    """
    max_width = _available_lyric_width(profile)
    strip_height = profile.strip_rect[3]
    size = profile.lyric_font_size
    for _ in range(16):
        font = load_font(size)
        space = float(font.getlength(" "))
        ascent, descent = font.getmetrics()
        row_height = ascent + descent + 8
        max_rows = max(
            1,
            int(
                (strip_height - 16)
                // max(1, row_height + profile.lyric_row_gap)
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
):
    profile = profile or get_profile(None)
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
    image_width = max(1, int(max(row_widths) + 4))
    image_height = max(
        1,
        int(row_height * len(rows) + profile.lyric_row_gap * max(0, len(rows) - 1)),
    )
    white = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    gold = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    white_draw = ImageDraw.Draw(white)
    gold_draw = ImageDraw.Draw(gold)
    boxes: list[dict] = [{"x": 0.0, "y": 0.0, "width": 0.0, "height": float(row_height)} for _ in words]

    for row_index, row in enumerate(rows):
        y = row_index * (row_height + profile.lyric_row_gap)
        x = max(0.0, (image_width - row_widths[row_index]) / 2)
        for word_index in row:
            word = words[word_index]
            width = widths[word_index]
            text_y = y + 4
            white_draw.text((x + 2, text_y + 3), word["text"], font=font, fill=(0, 0, 0, 190))
            gold_draw.text((x + 2, text_y + 3), word["text"], font=font, fill=(0, 0, 0, 190))
            white_draw.text((x, text_y), word["text"], font=font, fill=(255, 248, 236, 255))
            gold_draw.text((x, text_y), word["text"], font=font, fill=(255, 196, 72, 255))
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


def _draw_strip(layout: dict, song_time: float, profile: RenderProfile | None = None) -> Image.Image:
    profile = profile or get_profile(None)
    width = profile.width
    strip_height = profile.strip_rect[3]
    safe_left = max(0, profile.safe_left)
    safe_right = max(0, profile.safe_right)
    usable = max(1, width - safe_left - safe_right)
    strip = Image.new("RGBA", (width, strip_height), (0, 0, 0, 0))
    text = layout["white"]
    bar_width = min(usable, text.width + 56)
    bar_height = text.height + 36
    bar = Image.new("RGBA", (bar_width, bar_height), (8, 6, 4, 168))
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


def _draw_countdown(layout: dict, number: int, profile: RenderProfile | None = None) -> Image.Image:
    profile = profile or get_profile(None)
    strip = _draw_strip(layout, layout["start"] - 1, profile)
    font = load_font(72)
    digit = str(number)
    draw = ImageDraw.Draw(strip)
    box = draw.textbbox((0, 0), digit, font=font)
    x = max(0, profile.safe_left) + 56
    y = (profile.strip_rect[3] - (box[3] - box[1])) // 2 - box[1]
    draw.text((x + 2, y + 3), digit, font=font, fill=(0, 0, 0, 190))
    draw.text((x, y), digit, font=font, fill=(255, 196, 72, 255))
    return strip


def _frame_at(
    layouts: list[dict],
    song_time: float,
    blank: Image.Image,
    profile: RenderProfile | None = None,
) -> Image.Image:
    if song_time < 0:
        return blank
    mode, layout, number = cue_at(layouts, song_time)
    if mode == "sing" and layout is not None:
        return _draw_strip(layout, song_time, profile)
    if mode == "countdown" and layout is not None:
        return _draw_countdown(layout, number, profile)
    return blank


def _overlay_text(
    lines: list[dict],
    total_duration: float,
    cover_duration: float,
    slideshow: Path,
    destination: Path,
    log,
    profile: RenderProfile | None = None,
) -> None:
    """Stream lyric frames into FFmpeg. Nothing is written as a PNG sequence."""
    profile = profile or get_profile(None)
    strip_height = profile.strip_rect[3]
    strip_top = profile.strip_rect[1]
    font_size = _fit_lyric_font_size(lines, profile)
    layouts = [_render_text_pair(line, profile, font_size) for line in lines]
    frame_count = max(1, int(round(total_duration * TEXT_FPS)))
    blank = Image.new("RGBA", (profile.width, strip_height), (0, 0, 0, 0))
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(slideshow),
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
        "-filter_complex",
        (
            f"[1:v]fps={profile.fps},format=rgba[text];"
            f"[0:v][text]overlay=0:{strip_top}:format=auto:shortest=1,format=yuv420p"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-an",
        "-movflags",
        "+faststart",
        str(destination),
    ]
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
            frame = _frame_at(layouts, song_time, blank, profile)
            process.stdin.write(frame.tobytes())
            if index % 96 == 0:
                log(f"Vẽ chữ karaoke {index + 1}/{frame_count}.")
    except BrokenPipeError:
        pass
    finally:
        process.stdin.close()
    reader.join()
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


def render_videos(
    *,
    lines: list[dict],
    title: str,
    artist: str,
    cover_line: str,
    cover_photo: Path,
    background_images: list[Path],
    original_audio: Path,
    instrumental_audio: Path,
    output_dir: Path,
    vocal_name: str,
    instrumental_name: str,
    cover_duration: float,
    image_duration: float,
    background_effects: list[dict] | None = None,
    output_profile: str = "landscape_1080p",
    log,
) -> tuple[Path, Path]:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise MediaError("Chưa có ffmpeg hoặc ffprobe trong PATH.")

    profile = get_profile(output_profile)
    work = output_dir / ".karaoke-work"
    if work.exists():
        shutil.rmtree(work)
    slides = work / "slides"
    slides.mkdir(parents=True)

    try:
        log("Đang dựng trang bìa.")
        cover_card = slides / "cover.jpg"
        make_cover(cover_photo, title, artist, cover_line, cover_card, profile=profile)

        effect_by_path: dict[str, dict] = {}
        for effect in background_effects or []:
            if not isinstance(effect, dict):
                continue
            raw = str(effect.get("image_path") or "").strip()
            if not raw:
                continue
            effect_by_path[_image_key(Path(raw))] = effect

        prepared: list[tuple[Path, bool]] = []
        for index, image in enumerate(background_images):
            plan = _effect_plan(effect_by_path.get(_image_key(image)))
            clip: Path | None = None
            if plan is not None and plan_has_motion(plan):
                try:
                    clip = render_motion_loop(
                        image,
                        plan,
                        slides / f"bg-{index:02d}.mp4",
                        duration=image_duration,
                        width=profile.width,
                        height=profile.height,
                        fps=profile.fps,
                        fit_mode=profile.fit_mode,
                        # Background effects render before the lyric overlay.
                        # A zero-height strip explicitly means "do not mask the
                        # bottom"; the opaque text/bar drawn later keeps lyrics
                        # readable. Passing None would enable the legacy
                        # bottom-280px mask instead.
                        strip_rect=(0, 0, 0, 0),
                        log=log,
                    )
                except Exception as error:  # keep export usable if the effect fails
                    log(f"Không tạo được hiệu ứng cho {image.name}: {error}")
                    clip = None
            if clip is not None and Path(clip).is_file():
                prepared.append((Path(clip), True))
            else:
                destination = slides / f"bg-{index:02d}.jpg"
                prepare_slide(image, destination, profile=profile)
                prepared.append((destination, False))
        if not prepared:
            raise MediaError("Chưa có ảnh nền để dựng slideshow.")

        song_length = max(probe_duration(original_audio), probe_duration(instrumental_audio))
        # FFmpeg -t 0 does not produce a zero-length input; skip the cover clip.
        cover_duration = max(0.0, float(cover_duration))
        if cover_duration < 0.05:
            cover_duration = 0.0
        total = cover_duration + song_length

        ambience: Path | None = None
        loop_ambience = total > MAX_AMBIENT_DURATION
        ambient_kind = _ambient_kind(background_effects, background_images)
        if ambient_kind is not None:
            try:
                log(f"Đang tạo tiếng nền {ambient_kind} rất nhỏ.")
                ambience = synthesize_ambience(ambient_kind, total, work / "ambience.wav")
            except Exception as error:  # ambience is optional; keep the export usable
                log(f"Không tạo được tiếng nền: {error}")
                ambience = None

        clips: list[tuple[Path, float, bool]] = []
        if cover_duration > 0:
            clips.append((cover_card, cover_duration, False))
        remaining = song_length
        index = 0
        while remaining > 0.05:
            duration = min(image_duration, remaining)
            media, is_video = prepared[index % len(prepared)]
            clips.append((media, duration, is_video))
            remaining -= duration
            index += 1
        log("Đang dựng hình nền.")
        slideshow = work / "slideshow.mp4"
        slide_command = ["ffmpeg", "-y"]
        for media, duration, is_video in clips:
            if is_video:
                slide_command += ["-stream_loop", "-1", "-t", f"{duration:.3f}", "-i", str(media)]
            else:
                slide_command += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(media)]
        filters = [
            f"[{index}:v]fps={profile.fps},scale={profile.width}:{profile.height},setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v{index}]"
            for index in range(len(clips))
        ]
        filters.append("".join(f"[v{index}]" for index in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[v]")
        slide_command += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-an",
            str(slideshow),
        ]
        _run(slide_command, log)

        silent = work / "picture.mp4"
        log("Đang vẽ chữ và ghép lên hình.")
        _overlay_text(lines, total, cover_duration, slideshow, silent, log, profile=profile)

        vocal_out = output_dir / vocal_name
        instrumental_out = output_dir / instrumental_name
        _mux(
            silent,
            original_audio,
            vocal_out,
            cover_duration,
            log,
            "có giọng",
            ambience=ambience,
            loop_ambience=loop_ambience,
        )
        _mux(
            silent,
            instrumental_audio,
            instrumental_out,
            cover_duration,
            log,
            "không lời",
            ambience=ambience,
            loop_ambience=loop_ambience,
        )
        return vocal_out, instrumental_out
    finally:
        shutil.rmtree(work, ignore_errors=True)

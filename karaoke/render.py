from __future__ import annotations

import math
import shutil
import subprocess
import threading
from pathlib import Path

from PIL import Image, ImageDraw

from karaoke.media import (
    HEIGHT,
    STRIP_HEIGHT,
    WIDTH,
    MediaError,
    load_font,
    make_cover,
    prepare_slide,
    probe_channels,
    probe_duration,
)

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
    """Pack words into one row, or two balanced rows when the line is too long."""
    count = len(widths)
    if count == 0:
        return [[]]
    if _row_width(widths, space) <= max_width or count == 1:
        return [list(range(count))]

    best_split = 1
    best_score: float | None = None
    for split in range(1, count):
        first = _row_width(widths[:split], space)
        second = _row_width(widths[split:], space)
        overflow = 0.0
        if first > max_width:
            overflow += first - max_width
        if second > max_width:
            overflow += second - max_width
        score = overflow * 10_000 + abs(first - second)
        if best_score is None or score < best_score:
            best_score = score
            best_split = split
    return [list(range(best_split)), list(range(best_split, count))]


def _render_text_pair(line: dict):
    words = list(line.get("words") or [])
    font = load_font(LYRIC_FONT_SIZE)
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

    rows = _wrap_word_rows(widths, space, MAX_LYRIC_WIDTH)
    ascent, descent = font.getmetrics()
    row_height = ascent + descent + 8
    row_widths = []
    for row in rows:
        row_widths.append(_row_width([widths[index] for index in row], space))
    image_width = max(1, int(max(row_widths) + 4))
    image_height = max(1, int(row_height * len(rows) + LYRIC_ROW_GAP * max(0, len(rows) - 1)))
    white = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    gold = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    white_draw = ImageDraw.Draw(white)
    gold_draw = ImageDraw.Draw(gold)
    boxes: list[dict] = [{"x": 0.0, "y": 0.0, "width": 0.0, "height": float(row_height)} for _ in words]

    for row_index, row in enumerate(rows):
        y = row_index * (row_height + LYRIC_ROW_GAP)
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


def _draw_strip(layout: dict, song_time: float) -> Image.Image:
    strip = Image.new("RGBA", (WIDTH, STRIP_HEIGHT), (0, 0, 0, 0))
    text = layout["white"]
    bar_width = text.width + 56
    bar_height = text.height + 36
    bar = Image.new("RGBA", (bar_width, bar_height), (8, 6, 4, 168))
    bar_x = (WIDTH - bar_width) // 2
    bar_y = (STRIP_HEIGHT - bar_height) // 2
    strip.alpha_composite(bar, (bar_x, bar_y))
    text_x = (WIDTH - text.width) // 2
    text_y = (STRIP_HEIGHT - text.height) // 2
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


def _draw_countdown(layout: dict, number: int) -> Image.Image:
    strip = _draw_strip(layout, layout["start"] - 1)
    font = load_font(72)
    digit = str(number)
    draw = ImageDraw.Draw(strip)
    box = draw.textbbox((0, 0), digit, font=font)
    x = 56
    y = (STRIP_HEIGHT - (box[3] - box[1])) // 2 - box[1]
    draw.text((x + 2, y + 3), digit, font=font, fill=(0, 0, 0, 190))
    draw.text((x, y), digit, font=font, fill=(255, 196, 72, 255))
    return strip


def _frame_at(layouts: list[dict], song_time: float, blank: Image.Image) -> Image.Image:
    if song_time < 0:
        return blank
    mode, layout, number = cue_at(layouts, song_time)
    if mode == "sing" and layout is not None:
        return _draw_strip(layout, song_time)
    if mode == "countdown" and layout is not None:
        return _draw_countdown(layout, number)
    return blank


def _overlay_text(lines: list[dict], total_duration: float, cover_duration: float, slideshow: Path, destination: Path, log) -> None:
    """Stream lyric frames into FFmpeg. Nothing is written as a PNG sequence."""
    layouts = [_render_text_pair(line) for line in lines]
    frame_count = max(1, int(round(total_duration * TEXT_FPS)))
    blank = Image.new("RGBA", (WIDTH, STRIP_HEIGHT), (0, 0, 0, 0))
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
        f"{WIDTH}x{STRIP_HEIGHT}",
        "-framerate",
        str(TEXT_FPS),
        "-i",
        "pipe:0",
        "-filter_complex",
        "[1:v]fps=30,format=rgba[text];[0:v][text]overlay=0:main_h-overlay_h:format=auto:shortest=1,format=yuv420p",
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
            frame = _frame_at(layouts, song_time, blank)
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


def _mux(video: Path, audio: Path, destination: Path, cover_duration: float, log, label: str) -> None:
    log(f"Đang ghép tiếng cho bản {label}.")
    delay = int(round(cover_duration * 1000))
    channels = probe_channels(audio)
    delay_filter = "adelay=" + "|".join([str(delay)] * channels)
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-filter_complex",
            f"[1:a]{delay_filter},apad[a]",
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
            "-movflags",
            "+faststart",
            str(destination),
        ],
        log,
    )


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
    log,
) -> tuple[Path, Path]:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise MediaError("Chưa có ffmpeg hoặc ffprobe trong PATH.")

    work = output_dir / ".karaoke-work"
    if work.exists():
        shutil.rmtree(work)
    slides = work / "slides"
    slides.mkdir(parents=True)

    try:
        log("Đang dựng trang bìa.")
        cover_card = slides / "cover.jpg"
        make_cover(cover_photo, title, artist, cover_line, cover_card)
        prepared = []
        for index, image in enumerate(background_images):
            destination = slides / f"bg-{index:02d}.jpg"
            prepare_slide(image, destination)
            prepared.append(destination)

        song_length = max(probe_duration(original_audio), probe_duration(instrumental_audio))
        total = cover_duration + song_length
        clips: list[tuple[Path, float]] = [(cover_card, cover_duration)]
        remaining = song_length
        index = 0
        while remaining > 0.05:
            duration = min(image_duration, remaining)
            clips.append((prepared[index % len(prepared)], duration))
            remaining -= duration
            index += 1
        log("Đang dựng hình nền.")
        slideshow = work / "slideshow.mp4"
        slide_command = ["ffmpeg", "-y"]
        for image, duration in clips:
            slide_command += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(image)]
        filters = [
            f"[{index}:v]fps=30,format=yuv420p,setpts=PTS-STARTPTS[v{index}]"
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
        _overlay_text(lines, total, cover_duration, slideshow, silent, log)

        vocal_out = output_dir / vocal_name
        instrumental_out = output_dir / instrumental_name
        _mux(silent, original_audio, vocal_out, cover_duration, log, "có giọng")
        _mux(silent, instrumental_audio, instrumental_out, cover_duration, log, "không lời")
        return vocal_out, instrumental_out
    finally:
        shutil.rmtree(work, ignore_errors=True)

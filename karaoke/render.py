from __future__ import annotations

import shutil
import subprocess
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


def _layout(line: dict, size: int):
    font = load_font(size)
    space = font.getlength(" ")
    widths = [font.getlength(word["text"]) for word in line["words"]]
    total = sum(widths) + space * max(0, len(widths) - 1)
    return font, space, widths, total


def _render_text_pair(line: dict):
    font = None
    space = 0.0
    widths: list[float] = []
    total = 0.0
    for size in (72, 60, 50, 42, 34):
        font, space, widths, total = _layout(line, size)
        if total <= 1700:
            break
    assert font is not None
    height = max(font.getbbox(word["text"])[3] for word in line["words"]) + 12
    image_width = max(1, int(total) + 4)
    white = Image.new("RGBA", (image_width, height), (0, 0, 0, 0))
    gold = Image.new("RGBA", (image_width, height), (0, 0, 0, 0))
    white_draw = ImageDraw.Draw(white)
    gold_draw = ImageDraw.Draw(gold)
    x = 0.0
    for word, width in zip(line["words"], widths):
        white_draw.text((x + 2, 3), word["text"], font=font, fill=(0, 0, 0, 190))
        gold_draw.text((x + 2, 3), word["text"], font=font, fill=(0, 0, 0, 190))
        white_draw.text((x, 0), word["text"], font=font, fill=(255, 248, 236, 255))
        gold_draw.text((x, 0), word["text"], font=font, fill=(255, 196, 72, 255))
        x += width + space
    return {
        "white": white,
        "gold": gold,
        "widths": widths,
        "space": space,
        "total": total,
        "start": line["start"],
        "end": line["end"],
        "words": line["words"],
    }


def _active_layout(layouts: list[dict], song_time: float) -> dict | None:
    for index, layout in enumerate(layouts):
        end = layouts[index + 1]["start"] if index + 1 < len(layouts) else layout["end"] + 0.7
        if layout["start"] <= song_time < end:
            return layout
    return None


def _fill_width(layout: dict, song_time: float) -> float:
    if song_time >= layout["end"]:
        return layout["total"]
    filled = 0.0
    for index, word in enumerate(layout["words"]):
        width = layout["widths"][index]
        start = word["start"]
        end = max(word["end"], start + 0.05)
        if song_time >= end:
            filled += width
            if index < len(layout["words"]) - 1:
                filled += layout["space"]
            continue
        if song_time > start:
            filled += width * ((song_time - start) / (end - start))
        break
    return filled


def _draw_strip(layout: dict, song_time: float) -> Image.Image:
    strip = Image.new("RGBA", (WIDTH, STRIP_HEIGHT), (0, 0, 0, 0))
    filled = _fill_width(layout, song_time)
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
    if filled > 1:
        crop = layout["gold"].crop((0, 0, min(text.width, int(filled)), text.height))
        strip.alpha_composite(crop, (text_x, text_y))
    return strip


def _write_text_frames(lines: list[dict], total_duration: float, cover_duration: float, folder: Path, log) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    layouts = [_render_text_pair(line) for line in lines]
    frame_count = max(1, int(round(total_duration * TEXT_FPS)))
    for index in range(frame_count):
        timestamp = index / TEXT_FPS
        song_time = timestamp - cover_duration
        if song_time < 0:
            frame = Image.new("RGBA", (WIDTH, STRIP_HEIGHT), (0, 0, 0, 0))
        else:
            layout = _active_layout(layouts, song_time)
            frame = _draw_strip(layout, song_time) if layout else Image.new("RGBA", (WIDTH, STRIP_HEIGHT), (0, 0, 0, 0))
        frame.save(folder / f"{index:05d}.png", compress_level=1)
        if index % 96 == 0:
            log(f"Vẽ chữ karaoke {index + 1}/{frame_count}.")
    return frame_count


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
    frames = work / "frames"
    slides.mkdir(parents=True)
    frames.mkdir(parents=True)

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

        log("Đang vẽ chữ chạy theo nhạc.")
        _write_text_frames(lines, total, cover_duration, frames, log)
        silent = work / "picture.mp4"
        log("Đang ghép chữ lên hình.")
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(slideshow),
                "-framerate",
                str(TEXT_FPS),
                "-i",
                str(frames / "%05d.png"),
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
                str(silent),
            ],
            log,
        )

        vocal_out = output_dir / vocal_name
        instrumental_out = output_dir / instrumental_name
        _mux(silent, original_audio, vocal_out, cover_duration, log, "có giọng")
        _mux(silent, instrumental_audio, instrumental_out, cover_duration, log, "không lời")
        return vocal_out, instrumental_out
    finally:
        shutil.rmtree(work, ignore_errors=True)

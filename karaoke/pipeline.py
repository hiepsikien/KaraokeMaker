from __future__ import annotations

from pathlib import Path

from karaoke.align import AlignError, align_lyrics
from karaoke.lyrics import extract_sung_lines
from karaoke.media import (
    AUDIO_SUFFIXES,
    IMAGE_SUFFIXES,
    MediaError,
    probe_duration,
    safe_title,
    write_timing,
)
from karaoke.render import render_videos


class JobError(RuntimeError):
    pass


def _existing_file(path_text: str, label: str, suffixes: set[str]) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_file():
        raise JobError(f"Không thấy {label}: {path}")
    if path.suffix.lower() not in suffixes:
        raise JobError(f"{label} không phải định dạng hỗ trợ: {path.name}")
    return path


def run_job(payload: dict, log) -> dict:
    title = " ".join(str(payload.get("title", "")).split())
    artist = " ".join(str(payload.get("artist", "")).split())
    cover_line = " ".join(str(payload.get("cover_line", "")).split())
    if not title:
        raise JobError("Hãy nhập tên bài hát.")
    if not artist:
        raise JobError("Hãy nhập tác giả.")

    lyrics = str(payload.get("lyrics", ""))
    sung = extract_sung_lines(lyrics)
    if not sung:
        raise JobError("Hãy nhập lời. Các dòng [ghi chú] sẽ được bỏ qua.")

    original = _existing_file(payload.get("original_audio", ""), "audio gốc", AUDIO_SUFFIXES)
    vocal = _existing_file(payload.get("vocal_audio", ""), "file vocal", AUDIO_SUFFIXES)
    instrumental = _existing_file(payload.get("instrumental_audio", ""), "file instrumental", AUDIO_SUFFIXES)

    backgrounds = [_existing_file(item, "ảnh nền", IMAGE_SUFFIXES) for item in payload.get("background_images") or []]
    cover_text = str(payload.get("cover_image") or "").strip()
    cover = _existing_file(cover_text, "ảnh bìa", IMAGE_SUFFIXES) if cover_text else None
    if cover is None and not backgrounds:
        raise JobError("Hãy chọn ít nhất một ảnh bìa hoặc ảnh nền.")
    if cover is None:
        cover = backgrounds[0]
    if not backgrounds:
        backgrounds = [cover]

    output_dir = Path(str(payload.get("output_dir", "")).strip()).expanduser()
    if not str(output_dir):
        raise JobError("Hãy chọn thư mục xuất.")
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        cover_duration = float(payload.get("cover_duration", 5))
        image_duration = float(payload.get("image_duration", 8))
    except (TypeError, ValueError) as error:
        raise JobError("Thời lượng phải là số giây.") from error
    if cover_duration < 0 or image_duration <= 0:
        raise JobError("Thời lượng ảnh phải lớn hơn 0. Thời lượng bìa có thể bằng 0.")

    language = str(payload.get("language") or "vi").strip() or "vi"
    model_name = str(payload.get("model_name") or "small").strip() or "small"
    device = str(payload.get("device") or "cpu").strip() or "cpu"
    if device not in {"cpu", "mps"}:
        device = "cpu"
    if model_name not in {"tiny", "base", "small", "medium"}:
        raise JobError("Model căn lời không hợp lệ.")

    try:
        original_length = probe_duration(original)
        vocal_length = probe_duration(vocal)
        instrumental_length = probe_duration(instrumental)
    except MediaError as error:
        raise JobError(str(error)) from error
    log(
        "Thời lượng: "
        f"gốc {original_length:.1f}s, vocal {vocal_length:.1f}s, instrumental {instrumental_length:.1f}s."
    )
    if abs(vocal_length - instrumental_length) > 1.5:
        log("Vocal và instrumental lệch nhau hơn 1,5 giây. Vẫn tiếp tục với file bạn đã chọn.")

    try:
        lines = align_lyrics(vocal, lyrics, language, model_name, device, log)
        timing_path = output_dir / f"{safe_title(title)} timing.json"
        write_timing(timing_path, lines)
        log(f"Đã lưu thời gian từng chữ vào {timing_path.name}.")
        stem = safe_title(title)
        vocal_out, instrumental_out = render_videos(
            lines=lines,
            title=title,
            artist=artist,
            cover_line=cover_line,
            cover_photo=cover,
            background_images=backgrounds,
            original_audio=original,
            instrumental_audio=instrumental,
            output_dir=output_dir,
            vocal_name=f"{stem} (có giọng).mp4",
            instrumental_name=f"{stem} (không lời).mp4",
            cover_duration=cover_duration,
            image_duration=image_duration,
            log=log,
        )
    except (AlignError, MediaError, JobError):
        raise
    except Exception as error:
        raise JobError(f"Dựng video thất bại: {error}") from error

    log("Xong hai video.")
    return {
        "directory": str(output_dir),
        "vocal": str(vocal_out),
        "instrumental": str(instrumental_out),
        "timing": str(timing_path),
    }

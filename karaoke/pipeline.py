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


def normalize_edited_lines(lines: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for line in lines:
        words: list[dict] = []
        for word in line.get("words") or []:
            text = " ".join(str(word.get("text", "")).split())
            if not text:
                continue
            try:
                start = float(word.get("start", 0))
                end = float(word.get("end", 0))
            except (TypeError, ValueError) as error:
                raise JobError("Thời gian từng chữ phải là số giây.") from error
            if start < 0:
                start = 0.0
            if end <= start:
                end = start + 0.08
            words.append({"text": text, "start": start, "end": end})
        if not words:
            continue
        words.sort(key=lambda item: item["start"])
        cleaned.append(
            {
                "text": " ".join(word["text"] for word in words),
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "words": words,
            }
        )
    if not cleaned:
        raise JobError("Không còn chữ nào để xuất.")
    return cleaned


def prepare_job(payload: dict, log) -> dict:
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

    return {
        "title": title,
        "artist": artist,
        "cover_line": cover_line,
        "lyrics": lyrics,
        "original_audio": str(original),
        "vocal_audio": str(vocal),
        "instrumental_audio": str(instrumental),
        "background_images": [str(path) for path in backgrounds],
        "cover_image": str(cover),
        "output_dir": str(output_dir),
        "cover_duration": cover_duration,
        "image_duration": image_duration,
        "language": language,
        "model_name": model_name,
        "device": device,
        "vocal_duration": vocal_length,
    }


def align_job(prepared: dict, log) -> list[dict]:
    try:
        return align_lyrics(
            Path(prepared["vocal_audio"]),
            prepared["lyrics"],
            prepared["language"],
            prepared["model_name"],
            prepared["device"],
            log,
        )
    except AlignError:
        raise
    except Exception as error:
        raise JobError(f"Căn lời thất bại: {error}") from error


def render_job(prepared: dict, lines: list[dict], log) -> dict:
    lines = normalize_edited_lines(lines)
    output_dir = Path(prepared["output_dir"])
    title = prepared["title"]
    stem = safe_title(title)
    timing_path = output_dir / f"{stem} timing.json"
    try:
        write_timing(timing_path, lines)
        log(f"Đã lưu thời gian từng chữ vào {timing_path.name}.")
        vocal_out, instrumental_out = render_videos(
            lines=lines,
            title=title,
            artist=prepared["artist"],
            cover_line=prepared["cover_line"],
            cover_photo=Path(prepared["cover_image"]),
            background_images=[Path(path) for path in prepared["background_images"]],
            original_audio=Path(prepared["original_audio"]),
            instrumental_audio=Path(prepared["instrumental_audio"]),
            output_dir=output_dir,
            vocal_name=f"{stem} (có giọng).mp4",
            instrumental_name=f"{stem} (không lời).mp4",
            cover_duration=float(prepared["cover_duration"]),
            image_duration=float(prepared["image_duration"]),
            log=log,
        )
    except (MediaError, JobError):
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


def run_job(payload: dict, log) -> dict:
    prepared = prepare_job(payload, log)
    lines = align_job(prepared, log)
    return render_job(prepared, lines, log)

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from karaoke.access import AccessError, ensure_allowed, grant, grant_project_paths
from karaoke.align import AlignError, align_lyrics
from karaoke.errors import JobError
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

EXPORT_FORMATS = frozenset({"mp4", "mov", "mkv"})


def _existing_file(path_text: str, label: str, suffixes: set[str]) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_file():
        raise JobError(f"Không thấy {label}: {path}")
    if path.suffix.lower() not in suffixes:
        raise JobError(f"{label} không phải định dạng hỗ trợ: {path.name}")
    try:
        return ensure_allowed(path)
    except AccessError as error:
        raise JobError(str(error)) from error


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
        cleaned_line = {
            "text": " ".join(word["text"] for word in words),
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "words": words,
        }
        if line.get("uncertain"):
            cleaned_line["uncertain"] = True
        cleaned.append(cleaned_line)
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
    try:
        output_dir = ensure_allowed(output_dir)
    except AccessError as error:
        raise JobError(str(error)) from error
    output_dir.mkdir(parents=True, exist_ok=True)
    grant(output_dir)

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


def load_timing_file(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise JobError(f"Không đọc được file thời gian: {error}") from error
    if not isinstance(payload, list):
        raise JobError("File thời gian phải là danh sách các câu.")
    return normalize_edited_lines(payload)


def save_timing_file(path: Path, lines: list[dict]) -> Path:
    cleaned = normalize_edited_lines(lines)
    try:
        path = ensure_allowed(path)
    except AccessError as error:
        raise JobError(str(error)) from error
    path.parent.mkdir(parents=True, exist_ok=True)
    grant(path.parent)
    write_timing(path, cleaned)
    return path


def timing_path_for(prepared: dict) -> Path:
    stored = str(prepared.get("timing_path") or "").strip()
    if stored:
        return Path(stored)
    project_path = str(prepared.get("project_path") or "").strip()
    if project_path:
        path = Path(project_path)
        # Keep a sibling legacy timing.json next to the project for compatibility.
        stem = path.name
        if stem.endswith(".karaoke.json"):
            stem = stem[: -len(".karaoke.json")]
        elif stem.endswith(".karaoke"):
            stem = stem[: -len(".karaoke")]
        else:
            stem = path.stem
        return path.parent / f"{stem} timing.json"
    return Path(prepared["output_dir"]) / f"{safe_title(prepared['title'])} timing.json"


def _optional_existing(raw: str, label: str, suffixes: set[str]) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return str(_existing_file(text, label, suffixes))


def open_saved(payload: dict) -> tuple[dict, list[dict]]:
    """Rebuild an editing session from a saved timing file. Whisper is not used."""
    timing = _existing_file(payload.get("timing_path", ""), "file thời gian", {".json"})
    vocal = _existing_file(payload.get("vocal_audio", ""), "file vocal", AUDIO_SUFFIXES)
    lines = load_timing_file(timing)
    title = " ".join(str(payload.get("title", "")).split())
    if not title:
        title = timing.stem[: -len(" timing")] if timing.stem.endswith(" timing") else timing.stem
    artist = " ".join(str(payload.get("artist", "")).split())
    output_dir = Path(str(payload.get("output_dir") or timing.parent).strip()).expanduser()
    try:
        output_dir = ensure_allowed(output_dir)
    except AccessError as error:
        raise JobError(str(error)) from error
    output_dir.mkdir(parents=True, exist_ok=True)
    grant(output_dir)

    try:
        vocal_length = probe_duration(vocal)
    except MediaError as error:
        raise JobError(str(error)) from error
    prepared = {
        "title": title,
        "artist": artist,
        "cover_line": " ".join(str(payload.get("cover_line", "")).split()),
        "lyrics": "\n".join(line["text"] for line in lines),
        "original_audio": _optional_existing(payload.get("original_audio", ""), "audio gốc", AUDIO_SUFFIXES),
        "vocal_audio": str(vocal),
        "instrumental_audio": _optional_existing(
            payload.get("instrumental_audio", ""), "file instrumental", AUDIO_SUFFIXES
        ),
        "background_images": [
            _optional_existing(item, "ảnh nền", IMAGE_SUFFIXES)
            for item in payload.get("background_images") or []
            if str(item).strip()
        ],
        "cover_image": _optional_existing(payload.get("cover_image", ""), "ảnh bìa", IMAGE_SUFFIXES),
        "output_dir": str(output_dir),
        "cover_duration": float(payload.get("cover_duration") or 0),
        "image_duration": float(payload.get("image_duration") or 8),
        "language": "vi",
        "model_name": "small",
        "device": "cpu",
        "vocal_duration": vocal_length,
        "timing_path": str(timing),
    }
    return prepared, lines


def open_project_file(path_text: str) -> tuple[dict, list[dict], dict]:
    """Open a .karaoke.json project (or migrate legacy timing.json). Returns prepared, lines, project."""
    from karaoke.project import load_project, prepared_from_project, project_path_for, save_project

    path = Path(str(path_text or "").strip()).expanduser()
    if not path.is_file():
        raise JobError(f"Không thấy file dự án: {path}")
    try:
        path = ensure_allowed(path)
    except AccessError as error:
        raise JobError(str(error)) from error

    project, source_path = load_project(path)
    grant(source_path.parent)
    # Opening a project file is an explicit user choice — trust its stored media paths.
    grant_project_paths(project)

    # Legacy timing.json list → write a real project file beside it on first open.
    from karaoke.project import is_project_path

    if not is_project_path(source_path) and project.get("lines"):
        target = project_path_for(project["output_dir"] or source_path.parent, project["title"] or "karaoke")
        project["path"] = str(target)
        save_project(target, project)
        source_path = target
        project["path"] = str(target)
        grant_project_paths(project)

    prepared = prepared_from_project(project)
    prepared["project_path"] = str(source_path)
    if str(source_path).endswith(" timing.json"):
        prepared["timing_path"] = str(source_path)

    vocal_text = prepared.get("vocal_audio") or ""
    if vocal_text:
        vocal = _existing_file(vocal_text, "file vocal", AUDIO_SUFFIXES)
        prepared["vocal_audio"] = str(vocal)
        try:
            prepared["vocal_duration"] = probe_duration(vocal)
        except MediaError as error:
            raise JobError(str(error)) from error
    else:
        prepared["vocal_duration"] = 0.0

    for key, label, suffixes in (
        ("original_audio", "audio gốc", AUDIO_SUFFIXES),
        ("instrumental_audio", "file instrumental", AUDIO_SUFFIXES),
        ("cover_image", "ảnh bìa", IMAGE_SUFFIXES),
    ):
        if prepared.get(key):
            try:
                prepared[key] = _optional_existing(prepared[key], label, suffixes)
            except JobError:
                prepared[key] = ""

    backgrounds = []
    for item in prepared.get("background_images") or []:
        if not str(item).strip():
            continue
        try:
            backgrounds.append(_optional_existing(item, "ảnh nền", IMAGE_SUFFIXES))
        except JobError:
            continue
    prepared["background_images"] = backgrounds

    output_dir = Path(str(prepared.get("output_dir") or source_path.parent).strip()).expanduser()
    try:
        output_dir = ensure_allowed(output_dir)
    except AccessError as error:
        raise JobError(str(error)) from error
    output_dir.mkdir(parents=True, exist_ok=True)
    grant(output_dir)
    prepared["output_dir"] = str(output_dir)

    lines = list(project.get("lines") or [])
    if lines:
        lines = normalize_edited_lines(lines)
    project["path"] = str(source_path)
    # Keep the project payload aligned with paths the session can actually use.
    for key in ("original_audio", "vocal_audio", "instrumental_audio", "cover_image", "output_dir"):
        project[key] = prepared.get(key) or ""
    project["background_images"] = list(prepared.get("background_images") or [])
    return prepared, lines, project


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
    except AlignError as error:
        raise JobError(str(error)) from error
    except Exception as error:
        raise JobError(f"Căn lời thất bại: {error}") from error


def _present_file(raw) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_file() else None


def export_gaps(prepared: dict) -> list[str]:
    """Files still required before a video can be built. Editing can proceed without them."""
    gaps: list[str] = []
    if _present_file(prepared.get("original_audio")) is None:
        gaps.append("audio gốc")
    if _present_file(prepared.get("instrumental_audio")) is None:
        gaps.append("file instrumental")
    cover = _present_file(prepared.get("cover_image"))
    images = [path for path in (prepared.get("background_images") or []) if _present_file(path)]
    if cover is None and not images:
        gaps.append("ảnh bìa hoặc ảnh nền")
    return gaps


def _images_for_render(prepared: dict) -> tuple[Path, list[Path]]:
    cover = _present_file(prepared.get("cover_image"))
    images = [path for path in (prepared.get("background_images") or []) if _present_file(path)]
    if cover is None and images:
        cover = images[0]
    if cover is not None and not images:
        images = [cover]
    if cover is None:
        raise JobError("Chưa xuất được vì còn thiếu ảnh bìa hoặc ảnh nền. Hãy bổ sung file trong phần khai báo.")
    return cover, images


def normalize_export_format(raw: str | None) -> str:
    fmt = str(raw or "mp4").strip().lower().lstrip(".")
    if fmt not in EXPORT_FORMATS:
        raise JobError("Định dạng xuất chỉ hỗ trợ mp4, mov hoặc mkv.")
    return fmt


def unique_export_names(output_dir: Path, stem: str, fmt: str) -> tuple[str, str]:
    """Build video filenames with a timestamp suffix so re-exports never overwrite."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ext = f".{fmt}"
    for index in range(0, 50):
        suffix = stamp if index == 0 else f"{stamp}-{index + 1}"
        vocal_name = f"{stem} (có giọng) {suffix}{ext}"
        instrumental_name = f"{stem} (không lời) {suffix}{ext}"
        if not (output_dir / vocal_name).exists() and not (output_dir / instrumental_name).exists():
            return vocal_name, instrumental_name
    raise JobError("Không tạo được tên file xuất mới. Thử lại sau một giây.")


def render_job(prepared: dict, lines: list[dict], log, *, export_options: dict | None = None) -> dict:
    from karaoke.project import sync_project_file

    options = export_options or {}
    lines = normalize_edited_lines(lines)
    gaps = export_gaps(prepared)
    if gaps:
        raise JobError(
            "Chưa xuất được vì còn thiếu " + ", ".join(gaps) + ". Hãy bổ sung file trong phần khai báo."
        )

    fmt = normalize_export_format(options.get("format"))
    output_text = str(options.get("output_dir") or prepared.get("output_dir") or "").strip()
    if not output_text:
        raise JobError("Hãy chọn thư mục xuất.")
    output_dir = Path(output_text).expanduser()
    try:
        output_dir = ensure_allowed(output_dir)
    except AccessError as error:
        raise JobError(str(error)) from error
    output_dir.mkdir(parents=True, exist_ok=True)
    grant(output_dir)
    prepared["output_dir"] = str(output_dir)

    title = prepared["title"]
    stem = safe_title(title)
    vocal_name, instrumental_name = unique_export_names(output_dir, stem, fmt)
    cover, images = _images_for_render(prepared)
    try:
        timing_path = save_timing_file(timing_path_for(prepared), lines)
        log(f"Đã lưu thời gian từng chữ vào {timing_path}.")
        prepared["timing_path"] = str(timing_path)
        log(f"Xuất {fmt.upper()} vào {output_dir} — {vocal_name}")
        vocal_out, instrumental_out = render_videos(
            lines=lines,
            title=title,
            artist=prepared["artist"],
            cover_line=prepared["cover_line"],
            cover_photo=cover,
            background_images=images,
            original_audio=Path(prepared["original_audio"]),
            instrumental_audio=Path(prepared["instrumental_audio"]),
            output_dir=output_dir,
            vocal_name=vocal_name,
            instrumental_name=instrumental_name,
            cover_duration=float(prepared["cover_duration"]),
            image_duration=float(prepared["image_duration"]),
            log=log,
        )
    except (MediaError, JobError):
        raise
    except Exception as error:
        raise JobError(f"Dựng video thất bại: {error}") from error
    log("Xong hai video.")
    outputs = {
        "directory": str(output_dir),
        "vocal": str(vocal_out),
        "instrumental": str(instrumental_out),
        "timing": str(timing_path),
        "format": fmt,
    }
    try:
        project_path = sync_project_file(prepared, lines=lines, status="exported", outputs=outputs)
        outputs["project"] = str(project_path)
        log(f"Đã lưu dự án {project_path.name}.")
    except JobError as error:
        log(f"Video xong nhưng chưa ghi được file dự án: {error}")
    return outputs


def run_job(payload: dict, log) -> dict:
    prepared = prepare_job(payload, log)
    lines = align_job(prepared, log)
    return render_job(prepared, lines, log)

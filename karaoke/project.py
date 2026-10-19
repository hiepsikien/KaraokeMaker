from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from karaoke.access import AccessError, ensure_allowed, grant
from karaoke.errors import JobError
from karaoke.media import safe_title


def _normalize_lines(lines: list[dict]) -> list[dict]:
    from karaoke.pipeline import normalize_edited_lines

    return normalize_edited_lines(lines)

PROJECT_VERSION = 1
PROJECT_SUFFIX = ".karaoke.json"
LEGACY_TIMING_SUFFIX = " timing.json"
STATUSES = frozenset({"draft", "aligned", "exported"})


def project_filename(title: str) -> str:
    return f"{safe_title(title)}{PROJECT_SUFFIX}"


def project_path_for(output_dir: str | Path, title: str) -> Path:
    return Path(output_dir) / project_filename(title)


def is_project_path(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".karaoke.json") or name.endswith(".karaoke")


def _optional_str(value: Any) -> str:
    return " ".join(str(value or "").split())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def empty_project() -> dict:
    return {
        "version": PROJECT_VERSION,
        "status": "draft",
        "title": "",
        "artist": "",
        "cover_line": "",
        "lyrics": "",
        "original_audio": "",
        "vocal_audio": "",
        "instrumental_audio": "",
        "cover_image": "",
        "background_images": [],
        "output_dir": "",
        "cover_duration": 5.0,
        "image_duration": 8.0,
        "language": "vi",
        "model_name": "small",
        "device": "cpu",
        "lines": [],
        "outputs": None,
    }


def project_from_fields(payload: dict, *, lines: list[dict] | None = None, status: str = "draft") -> dict:
    project = empty_project()
    project["title"] = _optional_str(payload.get("title"))
    project["artist"] = _optional_str(payload.get("artist"))
    project["cover_line"] = _optional_str(payload.get("cover_line"))
    project["lyrics"] = str(payload.get("lyrics") or "")
    project["original_audio"] = str(payload.get("original_audio") or "").strip()
    project["vocal_audio"] = str(payload.get("vocal_audio") or "").strip()
    project["instrumental_audio"] = str(payload.get("instrumental_audio") or "").strip()
    project["cover_image"] = str(payload.get("cover_image") or "").strip()
    project["background_images"] = _string_list(payload.get("background_images"))
    project["output_dir"] = str(payload.get("output_dir") or "").strip()
    try:
        project["cover_duration"] = float(payload.get("cover_duration", 5))
        project["image_duration"] = float(payload.get("image_duration", 8))
    except (TypeError, ValueError) as error:
        raise JobError("Thời lượng phải là số giây.") from error
    project["language"] = str(payload.get("language") or "vi").strip() or "vi"
    project["model_name"] = str(payload.get("model_name") or "small").strip() or "small"
    project["device"] = str(payload.get("device") or "cpu").strip() or "cpu"
    if status not in STATUSES:
        raise JobError("Trạng thái dự án không hợp lệ.")
    project["status"] = status
    if lines is not None:
        project["lines"] = _normalize_lines(lines) if lines else []
        if project["lines"] and not project["lyrics"].strip():
            project["lyrics"] = "\n".join(line["text"] for line in project["lines"])
    elif isinstance(payload.get("lines"), list) and payload["lines"]:
        project["lines"] = _normalize_lines(payload["lines"])
    if isinstance(payload.get("outputs"), dict):
        project["outputs"] = payload["outputs"]
    return project


def public_project(project: dict, path: Path | None = None) -> dict:
    data = {
        "version": int(project.get("version") or PROJECT_VERSION),
        "status": project.get("status") or "draft",
        "title": project.get("title") or "",
        "artist": project.get("artist") or "",
        "cover_line": project.get("cover_line") or "",
        "lyrics": project.get("lyrics") or "",
        "original_audio": project.get("original_audio") or "",
        "vocal_audio": project.get("vocal_audio") or "",
        "instrumental_audio": project.get("instrumental_audio") or "",
        "cover_image": project.get("cover_image") or "",
        "background_images": list(project.get("background_images") or []),
        "output_dir": project.get("output_dir") or "",
        "cover_duration": float(project.get("cover_duration") or 0),
        "image_duration": float(project.get("image_duration") or 8),
        "language": project.get("language") or "vi",
        "model_name": project.get("model_name") or "small",
        "device": project.get("device") or "cpu",
        "lines": list(project.get("lines") or []),
        "outputs": project.get("outputs"),
        "path": str(path) if path else str(project.get("path") or ""),
    }
    return data


def save_project(path: Path, project: dict) -> Path:
    try:
        path = ensure_allowed(path)
    except AccessError as error:
        raise JobError(str(error)) from error
    path.parent.mkdir(parents=True, exist_ok=True)
    grant(path.parent)
    payload = public_project(project, path)
    payload.pop("path", None)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _migrate_timing_list(lines: list, path: Path) -> dict:
    project = empty_project()
    project["status"] = "aligned"
    project["lines"] = _normalize_lines(lines)
    project["lyrics"] = "\n".join(line["text"] for line in project["lines"])
    stem = path.stem
    if stem.endswith(" timing"):
        project["title"] = stem[: -len(" timing")]
    else:
        project["title"] = stem
    project["output_dir"] = str(path.parent)
    return project


def load_project(path: Path) -> tuple[dict, Path]:
    """Load a .karaoke.json project, or migrate a legacy timing.json list."""
    try:
        path = ensure_allowed(path)
    except AccessError as error:
        raise JobError(str(error)) from error
    if not path.is_file():
        raise JobError(f"Không thấy file dự án: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise JobError(f"Không đọc được file dự án: {error}") from error

    if isinstance(payload, list):
        project = _migrate_timing_list(payload, path)
        project["path"] = str(path)
        return project, path

    if not isinstance(payload, dict):
        raise JobError("File dự án không đúng định dạng.")

    if "lines" in payload and isinstance(payload.get("lines"), list) and "version" not in payload:
        # Object that is not a project but somehow wraps lines — treat fields if present.
        pass

    lines = payload.get("lines")
    if lines is None and isinstance(payload.get("timing"), list):
        lines = payload["timing"]

    status = str(payload.get("status") or "").strip()
    if status not in STATUSES:
        status = "aligned" if lines else "draft"

    project = project_from_fields(payload, lines=lines if isinstance(lines, list) else None, status=status)
    if not project["output_dir"]:
        project["output_dir"] = str(path.parent)
    if not project["title"]:
        stem = path.stem
        if stem.endswith(".karaoke"):
            project["title"] = stem[: -len(".karaoke")]
        elif stem.endswith(" timing"):
            project["title"] = stem[: -len(" timing")]
        else:
            project["title"] = stem
    project["path"] = str(path)
    return project, path


def resolve_project_path(payload: dict, prepared: dict | None = None) -> Path:
    stored = str((prepared or {}).get("project_path") or payload.get("project_path") or "").strip()
    if stored:
        return Path(stored)
    output_dir = str((prepared or payload).get("output_dir") or "").strip()
    title = str((prepared or payload).get("title") or "").strip()
    if not output_dir or not title:
        raise JobError("Chưa biết nơi lưu file dự án.")
    return project_path_for(output_dir, title)


def prepared_from_project(project: dict) -> dict:
    """Build a job prepared dict from a project. Missing export files stay empty strings."""
    lines = list(project.get("lines") or [])
    lyrics = str(project.get("lyrics") or "")
    if not lyrics.strip() and lines:
        lyrics = "\n".join(line.get("text", "") for line in lines)
    return {
        "title": project.get("title") or "",
        "artist": project.get("artist") or "",
        "cover_line": project.get("cover_line") or "",
        "lyrics": lyrics,
        "original_audio": str(project.get("original_audio") or "").strip(),
        "vocal_audio": str(project.get("vocal_audio") or "").strip(),
        "instrumental_audio": str(project.get("instrumental_audio") or "").strip(),
        "background_images": list(project.get("background_images") or []),
        "cover_image": str(project.get("cover_image") or "").strip(),
        "output_dir": str(project.get("output_dir") or "").strip(),
        "cover_duration": float(project.get("cover_duration") or 0),
        "image_duration": float(project.get("image_duration") or 8),
        "language": project.get("language") or "vi",
        "model_name": project.get("model_name") or "small",
        "device": project.get("device") or "cpu",
        "project_path": str(project.get("path") or ""),
        "timing_path": str(project.get("timing_path") or ""),
    }


def sync_project_file(
    prepared: dict,
    *,
    lines: list[dict] | None = None,
    status: str | None = None,
    outputs: dict | None = None,
) -> Path:
    """Write/update the project file from the current prepared session."""
    path = resolve_project_path({}, prepared)
    existing: dict = {}
    if path.is_file():
        try:
            existing, _ = load_project(path)
        except JobError:
            existing = {}
    payload = {**existing, **prepared}
    if lines is not None:
        payload["lines"] = lines
    elif existing.get("lines"):
        payload["lines"] = existing["lines"]
    next_status = status or existing.get("status") or ("aligned" if payload.get("lines") else "draft")
    project = project_from_fields(payload, lines=payload.get("lines"), status=next_status)
    if outputs is not None:
        project["outputs"] = outputs
    elif existing.get("outputs"):
        project["outputs"] = existing["outputs"]
    saved = save_project(path, project)
    prepared["project_path"] = str(saved)
    return saved

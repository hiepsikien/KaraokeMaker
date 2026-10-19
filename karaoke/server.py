from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from karaoke.access import AccessError, audio_root, ensure_allowed, grant
from karaoke.lyrics import display_name_from_stem, song_key_from_stem
from karaoke.media import IMAGE_SUFFIXES, find_original, find_stems, inspect_image, safe_title, sibling_stem
from karaoke.mix import normalize_mix_format, parse_mix_settings, render_mix
from karaoke.separate_runner import separate_kim2
from karaoke.align import fit_lines_to_voice
from karaoke.pipeline import (
    JobError,
    align_job,
    export_gaps,
    open_project_file,
    open_saved,
    prepare_job,
    render_job,
    save_timing_file,
    timing_path_for,
)
from karaoke.project import (
    assert_project_path_available,
    load_project,
    project_from_fields,
    project_path_for,
    public_project,
    restore_from_payload,
    save_project,
    sync_project_file,
)

app = FastAPI(title="KaraokeMaker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_JOB_LIMIT = 12
_JOB_TTL_SECONDS = 6 * 60 * 60


class BrowseRequest(BaseModel):
    kind: str
    initial_dir: str = ""


class MatchRequest(BaseModel):
    path: str


class ProjectFieldsRequest(BaseModel):
    title: str = ""
    artist: str = ""
    cover_line: str = ""
    original_audio: str = ""
    vocal_audio: str = ""
    instrumental_audio: str = ""
    lyrics: str = ""
    background_images: list[str] = Field(default_factory=list)
    cover_image: str = ""
    output_dir: str = ""
    cover_duration: float = 5
    image_duration: float = 8
    language: str = "vi"
    model_name: str = "small"
    device: str = "cpu"
    project_path: str = ""


CreateJobRequest = ProjectFieldsRequest


class OpenProjectRequest(BaseModel):
    path: str = ""


class OpenSavedRequest(BaseModel):
    timing_path: str = ""
    title: str = ""
    artist: str = ""
    cover_line: str = ""
    original_audio: str = ""
    vocal_audio: str = ""
    instrumental_audio: str = ""
    background_images: list[str] = Field(default_factory=list)
    cover_image: str = ""
    output_dir: str = ""
    cover_duration: float = 5
    image_duration: float = 8


class RevealRequest(BaseModel):
    path: str


class ImageInspectRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)


class WordIn(BaseModel):
    text: str = ""
    start: float = 0
    end: float = 0


class LineIn(BaseModel):
    text: str = ""
    start: float = 0
    end: float = 0
    uncertain: bool = False
    words: list[WordIn] = Field(default_factory=list)


class MixRequest(BaseModel):
    high_hz: float = 8000
    low_hz: float = 120
    high_volume: float = 50
    low_volume: float = 50
    format: str = "wav"


class RenderRequest(BaseModel):
    lines: list[LineIn] = Field(default_factory=list)
    output_dir: str = ""
    format: str = "mp4"


_AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}


def _apple_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _run_osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _default_dir(kind: str, requested: str) -> str:
    if requested and Path(requested).expanduser().is_dir():
        return str(Path(requested).expanduser())
    root = audio_root()
    songs = root / "Songs"
    separated = root / "Separated" / "Kim2"
    if kind == "original" and songs.is_dir():
        return str(songs)
    if kind in {"vocal", "instrumental"} and separated.is_dir():
        return str(separated)
    if kind in {"output", "timing", "project"}:
        karaoke = root / "Karaoke"
        if root.is_dir():
            return str(karaoke if karaoke.is_dir() else root)
    if root.is_dir():
        return str(root)
    return str(Path.home())


def _prune_jobs() -> None:
    now = time.time()
    stale = [
        job_id
        for job_id, record in _jobs.items()
        if record["status"] in {"done", "error"} and now - record["created"] > _JOB_TTL_SECONDS
    ]
    for job_id in stale:
        _jobs.pop(job_id, None)
    if len(_jobs) <= _JOB_LIMIT:
        return
    finished = sorted(
        (record["created"], job_id)
        for job_id, record in _jobs.items()
        if record["status"] in {"done", "error"}
    )
    for _created, job_id in finished:
        if len(_jobs) <= _JOB_LIMIT:
            break
        _jobs.pop(job_id, None)


def _browse_script(kind: str, directory: str) -> str:
    location = _apple_string(directory)
    prompt = {
        "original": "Chọn audio gốc",
        "vocal": "Chọn file vocal",
        "instrumental": "Chọn file instrumental",
        "cover": "Chọn ảnh bìa",
        "images": "Chọn ảnh nền",
        "output": "Chọn thư mục xuất",
        "timing": "Chọn file thời gian đã lưu",
        "project": "Chọn file dự án KaraokeMaker",
    }.get(kind, "Chọn file")
    if kind == "output":
        return f'''
set theFolder to choose folder with prompt "{prompt}" default location POSIX file "{location}"
return POSIX path of theFolder
'''
    if kind == "images":
        return f'''
set picked to choose file with prompt "{prompt}" default location POSIX file "{location}" with multiple selections allowed
set output to ""
repeat with itemRef in picked
    set output to output & POSIX path of itemRef & linefeed
end repeat
return output
'''
    if kind == "project":
        return f'''
set picked to choose file with prompt "{prompt}" of type {{"public.json", "JSON"}} default location POSIX file "{location}"
return POSIX path of picked
'''
    return f'''
set picked to choose file with prompt "{prompt}" default location POSIX file "{location}"
return POSIX path of picked
'''


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/defaults")
def defaults():
    root = audio_root()
    songs = root / "Songs"
    separated = root / "Separated" / "Kim2"
    output = root / "Karaoke" if root.is_dir() else Path.home() / "Karaoke"
    try:
        output.mkdir(parents=True, exist_ok=True)
        grant(output)
    except OSError:
        pass
    return {
        "songs_dir": str(songs) if songs.is_dir() else "",
        "separated_dir": str(separated) if separated.is_dir() else "",
        "output_dir": str(output),
    }


def _is_openable_project(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".karaoke.json") or name.endswith(" timing.json")


@app.post("/api/browse")
def browse(body: BrowseRequest):
    directory = _default_dir(body.kind, body.initial_dir)
    raw = _run_osascript(_browse_script(body.kind, directory))
    paths = [line.strip() for line in raw.splitlines() if line.strip()]
    if body.kind != "images" and len(paths) > 1:
        paths = paths[:1]
    if body.kind == "project" and paths:
        path = Path(paths[0])
        if not _is_openable_project(path):
            raise HTTPException(
                status_code=400,
                detail="Hãy chọn file .karaoke.json hoặc file … timing.json.",
            )
    for item in paths:
        grant(Path(item))
    return {"paths": paths}


def _reject_closed(path: Path) -> Path:
    try:
        return ensure_allowed(path)
    except AccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@app.post("/api/match")
def match_audio(body: MatchRequest):
    path = _reject_closed(Path(body.path))
    if not path.is_file():
        raise HTTPException(status_code=400, detail="Không thấy file vừa chọn.")
    vocal = ""
    instrumental = ""
    name = path.name
    if "_(Vocals)" in name:
        vocal = str(path)
        sibling = sibling_stem(path)
        instrumental = str(sibling) if sibling else ""
    elif "_(Instrumental)" in name:
        instrumental = str(path)
        sibling = sibling_stem(path)
        vocal = str(sibling) if sibling else ""
    else:
        found_vocal, found_instrumental = find_stems(path)
        vocal = str(found_vocal) if found_vocal else ""
        instrumental = str(found_instrumental) if found_instrumental else ""
    original = find_original(path)
    if original is None and "_(Vocals)" not in name and "_(Instrumental)" not in name:
        original = path
    title = display_name_from_stem(path.name)
    if original is not None:
        title = display_name_from_stem(original.name)
    return {
        "vocal": vocal,
        "instrumental": instrumental,
        "original": str(original) if original else "",
        "title": title if song_key_from_stem(path.name) else "",
    }


@app.post("/api/images/inspect")
def inspect_images(body: ImageInspectRequest):
    facts = []
    for raw in body.paths[:24]:
        try:
            path = ensure_allowed(Path(raw))
        except AccessError:
            facts.append(
                {
                    "path": raw,
                    "width": 0,
                    "height": 0,
                    "ratio": "",
                    "fits_frame": False,
                    "note": "Ảnh nằm ngoài thư mục được phép. Hãy chọn lại bằng hộp thoại.",
                    "thumbnail": "",
                }
            )
            continue
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            facts.append(
                {
                    "path": str(path),
                    "width": 0,
                    "height": 0,
                    "ratio": "",
                    "fits_frame": False,
                    "note": "Không đọc được ảnh này.",
                    "thumbnail": "",
                }
            )
            continue
        try:
            facts.append(inspect_image(path))
        except Exception:
            facts.append(
                {
                    "path": str(path),
                    "width": 0,
                    "height": 0,
                    "ratio": "",
                    "fits_frame": False,
                    "note": "Không đọc được ảnh này.",
                    "thumbnail": "",
                }
            )
    return {"images": facts}


def _public_job(record: dict) -> dict:
    prepared = record.get("prepared") or {}
    return {
        "id": record["id"],
        "status": record["status"],
        "log": list(record["log"]),
        "error": record["error"],
        "outputs": record["outputs"],
        "lines": record["lines"],
        "duration": record["duration"],
        "export_gaps": export_gaps(prepared) if prepared else [],
        "project_path": prepared.get("project_path") or "",
        "project": record.get("project"),
        "restore": record.get("restore"),
    }


def _store_job(record: dict) -> dict:
    with _lock:
        _prune_jobs()
        _jobs[record["id"]] = record
    return _public_job(record)


@app.post("/api/projects")
def create_project(body: ProjectFieldsRequest):
    """Save a draft project file without aligning. Path follows title + output_dir."""
    payload = body.model_dump()
    try:
        project = project_from_fields(payload, status="draft")
        if not project["title"]:
            raise JobError("Hãy nhập tên bài hát.")
        if not project["output_dir"]:
            raise JobError("Hãy chọn thư mục xuất.")
        output = Path(project["output_dir"]).expanduser()
        try:
            output = ensure_allowed(output)
        except AccessError as error:
            raise JobError(str(error)) from error
        output.mkdir(parents=True, exist_ok=True)
        grant(output)
        project["output_dir"] = str(output)
        target = project_path_for(output, project["title"])
        previous = Path(payload["project_path"]).expanduser() if payload.get("project_path") else None
        assert_project_path_available(target, previous)
        saved = save_project(target, project)
        if previous is not None:
            try:
                if previous.resolve() != saved.resolve() and previous.is_file():
                    previous.unlink()
            except OSError:
                pass
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except AccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return public_project(project, saved)


@app.post("/api/projects/session")
def project_session(body: ProjectFieldsRequest):
    """Update project fields from setup; relocate the .karaoke.json when title/output_dir change."""
    payload = body.model_dump()
    path_text = str(payload.get("project_path") or "").strip()
    if not path_text:
        raise HTTPException(status_code=400, detail="Thiếu đường dẫn dự án.")
    try:
        prepared, lines, existing = open_project_file(path_text)
        for key in (
            "title",
            "artist",
            "cover_line",
            "lyrics",
            "original_audio",
            "vocal_audio",
            "instrumental_audio",
            "cover_image",
            "output_dir",
            "cover_duration",
            "image_duration",
            "language",
            "model_name",
            "device",
        ):
            if key in payload:
                prepared[key] = payload[key]
        prepared["background_images"] = list(payload.get("background_images") or [])
        prepared["project_path"] = path_text
        # status=None keeps exported (same rule as sync_project_file for timing saves).
        saved = sync_project_file(
            prepared,
            lines=lines or None,
            status=None,
            outputs=existing.get("outputs"),
        )
        prepared, lines, project = open_project_file(str(saved))
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    project_public = public_project(project, Path(prepared["project_path"]))
    if not lines or not prepared.get("vocal_audio"):
        return {"phase": "setup", "project": project_public, "job": None}

    moved = str(Path(path_text).expanduser().resolve()) != str(Path(prepared["project_path"]).resolve())
    log_line = (
        f"Đã cập nhật khai báo và chuyển dự án sang {Path(prepared['project_path']).name}."
        if moved
        else "Đã cập nhật khai báo dự án."
    )
    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "status": "editing",
        "created": time.time(),
        "log": [log_line],
        "error": "",
        "outputs": project.get("outputs"),
        "lines": lines,
        "duration": prepared.get("vocal_duration") or 0,
        "prepared": prepared,
        "project": project_public,
    }
    return {"phase": "edit", "project": project_public, "job": _store_job(record)}


@app.post("/api/projects/open")
def open_project(body: OpenProjectRequest):
    """Open a project file (or migrate legacy timing.json) into a job when lines exist."""
    try:
        prepared, lines, project = open_project_file(body.path)
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    project_public = public_project(project, Path(prepared["project_path"]))
    if not lines:
        return {
            "phase": "setup",
            "project": project_public,
            "job": None,
        }

    if not prepared.get("vocal_audio"):
        return {
            "phase": "setup",
            "project": project_public,
            "job": None,
            "notice": "Dự án đã có lời căn sẵn nhưng thiếu file vocal. Hãy chọn lại vocal rồi vào biên tập.",
        }

    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "status": "editing",
        "created": time.time(),
        "log": [f"Đã mở dự án {Path(prepared['project_path']).name}. Không căn lại."],
        "error": "",
        "outputs": project.get("outputs"),
        "lines": lines,
        "duration": prepared.get("vocal_duration") or 0,
        "prepared": prepared,
        "project": project_public,
    }
    return {
        "phase": "edit",
        "project": project_public,
        "job": _store_job(record),
    }


@app.post("/api/jobs")
def create_job(body: CreateJobRequest):
    job_id = uuid.uuid4().hex
    payload = body.model_dump()
    record = {
        "id": job_id,
        "status": "running",
        "created": time.time(),
        "log": ["Bắt đầu căn lời."],
        "error": "",
        "outputs": None,
        "lines": None,
        "duration": 0,
        "prepared": None,
        "project": None,
    }
    with _lock:
        _prune_jobs()
        _jobs[job_id] = record

    def log(message: str) -> None:
        with _lock:
            record["log"].append(message)

    def worker() -> None:
        try:
            prepared = prepare_job(payload, log)
            if payload.get("project_path"):
                prepared["project_path"] = payload["project_path"]
            project_path = sync_project_file(prepared, status="draft")
            log(f"Đã lưu dự án {Path(project_path).name}.")
            lines = align_job(prepared, log)
            project_path = sync_project_file(prepared, lines=lines, status="aligned")
            with _lock:
                record["prepared"] = prepared
                record["lines"] = lines
                record["duration"] = prepared["vocal_duration"]
                record["project"] = public_project(
                    project_from_fields({**prepared, "lines": lines}, lines=lines, status="aligned"),
                    Path(project_path),
                )
                record["log"].append("Căn xong. Hãy sửa chữ và thời gian, rồi xuất video.")
                record["status"] = "editing"
        except JobError as error:
            with _lock:
                record["status"] = "error"
                record["error"] = str(error)
        except Exception as error:
            with _lock:
                record["status"] = "error"
                record["error"] = str(error)

    threading.Thread(target=worker, daemon=True).start()
    return {"id": job_id}


@app.post("/api/jobs/open")
def open_job(body: OpenSavedRequest):
    """Legacy open for timing.json + form fields. Prefer /api/projects/open."""
    job_id = uuid.uuid4().hex
    try:
        prepared, lines = open_saved(body.model_dump())
        project_path = sync_project_file(prepared, lines=lines, status="aligned")
        prepared["project_path"] = str(project_path)
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    record = {
        "id": job_id,
        "status": "editing",
        "created": time.time(),
        "log": [f"Đã mở {Path(prepared.get('timing_path') or project_path).name}. Không căn lại."],
        "error": "",
        "outputs": None,
        "lines": lines,
        "duration": prepared["vocal_duration"],
        "prepared": prepared,
        "project": public_project(
            project_from_fields({**prepared, "lines": lines}, lines=lines, status="aligned"),
            Path(project_path),
        ),
    }
    return _store_job(record)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Không thấy việc này.")
        return _public_job(record)


@app.get("/api/jobs/{job_id}/audio")
def job_audio(job_id: str):
    with _lock:
        record = _jobs.get(job_id)
        if record is None or not record.get("prepared"):
            raise HTTPException(status_code=404, detail="Chưa có audio để nghe thử.")
        path = _reject_closed(Path(record["prepared"]["vocal_audio"]))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Không thấy file vocal.")
    media_type = _AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.post("/api/jobs/{job_id}/fit")
def fit_job(job_id: str):
    """Manual re-fit. Alignment already fitted once; the editor does not call this on open."""
    with _lock:
        record = _jobs.get(job_id)
        if record is None or not record.get("lines") or not record.get("prepared"):
            raise HTTPException(status_code=404, detail="Chưa có lời để chỉnh.")
        vocal = Path(record["prepared"]["vocal_audio"])
        lines = record["lines"]

    def log(message: str) -> None:
        with _lock:
            record["log"].append(message)

    try:
        fitted = fit_lines_to_voice(lines, vocal, log)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Không chỉnh được theo tiếng hát: {error}") from error
    with _lock:
        if _jobs.get(job_id) is record:
            record["lines"] = fitted
    return {"lines": fitted}


@app.post("/api/jobs/{job_id}/timing")
def save_job_timing(job_id: str, body: RenderRequest):
    with _lock:
        record = _jobs.get(job_id)
        if record is None or not record.get("prepared"):
            raise HTTPException(status_code=404, detail="Chưa có việc để lưu.")
        prepared = record["prepared"]
        lines = [line.model_dump() for line in body.lines]
    try:
        path = save_timing_file(timing_path_for(prepared), lines)
        prepared["timing_path"] = str(path)
        project_path = sync_project_file(prepared, lines=lines, status="aligned")
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    with _lock:
        if _jobs.get(job_id) is record:
            record["prepared"]["timing_path"] = str(path)
            record["prepared"]["project_path"] = str(project_path)
            record["lines"] = lines
            record["project"] = public_project(
                project_from_fields({**prepared, "lines": lines}, lines=lines, status="aligned"),
                Path(project_path),
            )
            record["log"].append(f"Đã lưu dự án {Path(project_path).name}.")
    return {"path": str(project_path), "timing": str(path)}


@app.post("/api/jobs/{job_id}/render")
def start_render(job_id: str, body: RenderRequest):
    with _lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Không thấy việc này.")
        if record.get("prepared") is None:
            raise HTTPException(status_code=409, detail="Hãy căn lời trước khi xuất video.")
        if record["status"] in {"running", "rendering"}:
            raise HTTPException(status_code=409, detail="Việc này đang chạy.")
        record["status"] = "rendering"
        record["error"] = ""
        prepared = dict(record["prepared"])
        lines = [line.model_dump() for line in body.lines]
        export_options = {"output_dir": body.output_dir, "format": body.format}

    def log(message: str) -> None:
        with _lock:
            record["log"].append(message)

    def worker() -> None:
        try:
            outputs = render_job(prepared, lines, log, export_options=export_options)
            with _lock:
                record["status"] = "done"
                record["outputs"] = outputs
                record["lines"] = lines
                record["prepared"]["output_dir"] = prepared.get("output_dir") or record["prepared"].get("output_dir")
        except JobError as error:
            with _lock:
                record["status"] = "error"
                record["error"] = str(error)
        except Exception as error:
            with _lock:
                record["status"] = "error"
                record["error"] = str(error)

    threading.Thread(target=worker, daemon=True).start()
    return {"id": job_id}


def _save_project_fields(payload: dict) -> tuple[dict, Path]:
    previous_text = str(payload.get("project_path") or "").strip()
    existing: dict = {}
    if previous_text:
        try:
            existing, _source = load_project(Path(previous_text))
        except JobError:
            existing = {}
    status = str(existing.get("status") or "draft")
    if status not in {"draft", "aligned", "exported"}:
        status = "draft"
    project = project_from_fields({**existing, **payload}, status=status)
    if not project["title"]:
        raise JobError("Hãy nhập tên bài hát.")
    if not project["output_dir"]:
        raise JobError("Hãy chọn thư mục xuất.")
    original = Path(project["original_audio"]).expanduser()
    if not original.is_file():
        raise JobError(f"Không thấy audio gốc: {original}")
    try:
        original = ensure_allowed(original)
        output = ensure_allowed(Path(project["output_dir"]).expanduser())
    except AccessError as error:
        raise JobError(str(error)) from error
    output.mkdir(parents=True, exist_ok=True)
    grant(output)
    grant(original)
    project["original_audio"] = str(original)
    project["output_dir"] = str(output)
    target = project_path_for(output, project["title"])
    previous = Path(previous_text).expanduser() if previous_text else None
    assert_project_path_available(target, previous)
    saved = save_project(target, project)
    if previous is not None:
        try:
            if previous.resolve() != saved.resolve() and previous.is_file():
                previous.unlink()
        except OSError:
            pass
    project["path"] = str(saved)
    return project, saved


def _restore_record(project: dict, path: Path) -> dict:
    restore = restore_from_payload(project)
    vocal = Path(str(project.get("vocal_audio") or ""))
    stem = Path(restore["instrumental_stem"]) if restore["instrumental_stem"] else None
    ready = vocal.is_file() and stem is not None and stem.is_file()
    prepared = {
        "title": project.get("title") or "",
        "artist": project.get("artist") or "",
        "cover_line": project.get("cover_line") or "",
        "lyrics": project.get("lyrics") or "",
        "original_audio": project.get("original_audio") or "",
        "vocal_audio": project.get("vocal_audio") or "",
        "instrumental_audio": project.get("instrumental_audio") or "",
        "background_images": list(project.get("background_images") or []),
        "cover_image": project.get("cover_image") or "",
        "output_dir": project.get("output_dir") or "",
        "cover_duration": project.get("cover_duration") or 5,
        "image_duration": project.get("image_duration") or 8,
        "language": project.get("language") or "vi",
        "model_name": project.get("model_name") or "small",
        "device": project.get("device") or "cpu",
        "project_path": str(path),
        "restore": restore,
    }
    job_id = uuid.uuid4().hex
    return {
        "id": job_id,
        "status": "ready" if ready else "needs_split",
        "created": time.time(),
        "log": ["Đã mở bước tách và trộn." if ready else "Chưa tách giọng. Bấm Tách để chạy Kim Vocal 2."],
        "error": "",
        "outputs": project.get("outputs"),
        "lines": project.get("lines") or None,
        "duration": 0,
        "prepared": prepared,
        "project": public_project(project, path),
        "restore": restore,
        "preview_path": "",
    }


def _require_restore_job(job_id: str) -> dict:
    with _lock:
        record = _jobs.get(job_id)
    if record is None or record.get("prepared") is None:
        raise HTTPException(status_code=404, detail="Không thấy bước tách và trộn.")
    if record["status"] == "separating":
        raise HTTPException(status_code=409, detail="Đang tách giọng. Hãy đợi xong rồi nghe thử.")
    return record


@app.post("/api/restore/session")
def restore_session(body: ProjectFieldsRequest):
    try:
        project, path = _save_project_fields(body.model_dump())
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "project": public_project(project, path),
        "job": _store_job(_restore_record(project, path)),
    }


@app.post("/api/restore/{job_id}/separate")
def restore_separate(job_id: str):
    with _lock:
        record = _jobs.get(job_id)
        if record is None or record.get("prepared") is None:
            raise HTTPException(status_code=404, detail="Không thấy bước tách và trộn.")
        if record["status"] == "separating":
            raise HTTPException(status_code=409, detail="Đang tách giọng.")
        record["status"] = "separating"
        record["error"] = ""
        prepared = record["prepared"]
        audio = Path(prepared["original_audio"])
        output_dir = Path(prepared["output_dir"])
        device = str(prepared.get("device") or "cpu")

    def log(message: str) -> None:
        with _lock:
            record["log"].append(message)

    def worker() -> None:
        try:
            last_step = {"value": -1}

            def on_progress(fraction: float) -> None:
                step = int(max(0.0, min(1.0, fraction)) * 10)
                if step == last_step["value"]:
                    return
                last_step["value"] = step
                log(f"Đang tách Kim Vocal 2… {step * 10}%")

            log("Kim Vocal 2, overlap 25%, segment 512.")
            paths = separate_kim2(audio, output_dir, device=device, on_progress=on_progress)
            restore = dict(prepared.get("restore") or {})
            restore["instrumental_stem"] = paths["instrumental"]
            prepared["vocal_audio"] = paths["vocal"]
            prepared["restore"] = restore
            project_path = sync_project_file(prepared, status=None)
            prepared["project_path"] = str(project_path)
            saved_project, saved_path = load_project(Path(project_path))
            with _lock:
                record["restore"] = restore
                record["prepared"] = prepared
                record["project"] = public_project(saved_project, saved_path)
                record["log"].append("Đã tách vocal và instrumental. Chỉnh dải tần rồi nghe thử.")
                record["status"] = "ready"
        except JobError as error:
            with _lock:
                record["status"] = "error"
                record["error"] = str(error)
        except Exception as error:
            with _lock:
                record["status"] = "error"
                record["error"] = str(error)

    threading.Thread(target=worker, daemon=True).start()
    return {"id": job_id}


@app.post("/api/restore/{job_id}/preview")
def restore_preview(job_id: str, body: MixRequest):
    record = _require_restore_job(job_id)
    prepared = record["prepared"]
    try:
        settings = parse_mix_settings(body.model_dump())
        stem = Path(str((prepared.get("restore") or {}).get("instrumental_stem") or ""))
        original = _reject_closed(Path(prepared["original_audio"]))
        if not stem.is_file():
            raise JobError("Hãy tách giọng trước khi nghe thử.")
        stem = _reject_closed(stem)
        output = _reject_closed(Path(prepared["output_dir"]))
        destination = output / f".{safe_title(prepared['title'])} nghe thử.wav"
        render_mix(stem, original, destination, settings)
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    restore = {**(prepared.get("restore") or {}), **settings, "instrumental_stem": str(stem)}
    prepared["restore"] = restore
    try:
        project_path = sync_project_file(prepared, status=None)
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    with _lock:
        if _jobs.get(job_id) is record:
            record["preview_path"] = str(destination)
            record["restore"] = restore
            record["prepared"]["restore"] = restore
            record["prepared"]["project_path"] = str(project_path)
            record["log"].append("Đã dựng bản nghe thử.")
    return {"ok": True}


@app.get("/api/restore/{job_id}/preview")
def restore_preview_audio(job_id: str):
    with _lock:
        record = _jobs.get(job_id)
        preview = str(record.get("preview_path") or "") if record else ""
    if not preview:
        raise HTTPException(status_code=404, detail="Chưa có bản nghe thử. Bấm Nghe thử trước.")
    path = _reject_closed(Path(preview))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Không thấy file nghe thử.")
    return FileResponse(path, media_type="audio/wav", filename=path.name.lstrip("."))


@app.post("/api/restore/{job_id}/export")
def restore_export(job_id: str, body: MixRequest):
    record = _require_restore_job(job_id)
    prepared = record["prepared"]
    try:
        settings = parse_mix_settings(body.model_dump())
        fmt = normalize_mix_format(body.format)
        stem = Path(str((prepared.get("restore") or {}).get("instrumental_stem") or ""))
        if not stem.is_file():
            raise JobError("Hãy tách giọng trước khi xuất.")
        stem = _reject_closed(stem)
        original = _reject_closed(Path(prepared["original_audio"]))
        output = _reject_closed(Path(prepared["output_dir"]))
        destination = output / f"{safe_title(prepared['title'])} (nhạc nền).{fmt}"
        render_mix(stem, original, destination, settings)
        restore = {**(prepared.get("restore") or {}), **settings, "instrumental_stem": str(stem)}
        prepared["restore"] = restore
        prepared["instrumental_audio"] = str(destination)
        vocal_text = str(prepared.get("vocal_audio") or "").strip()
        if vocal_text:
            prepared["vocal_audio"] = str(_reject_closed(Path(vocal_text)))
        project_path = sync_project_file(prepared, status=None)
        saved_project, saved_path = load_project(Path(project_path))
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    project = public_project(saved_project, saved_path)
    with _lock:
        if _jobs.get(job_id) is record:
            record["restore"] = restore
            record["prepared"] = prepared
            record["project"] = project
            record["status"] = "ready"
            record["log"].append(f"Đã xuất {destination.name}.")
    return {"project": project, "instrumental": str(destination), "vocal": prepared["vocal_audio"]}


@app.post("/api/reveal")
def reveal(body: RevealRequest):
    path = _reject_closed(Path(body.path))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Không thấy file để mở.")
    subprocess.run(["open", "-R", str(path)], check=False)
    return {"ok": True}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from karaoke.access import AccessError, audio_root, ensure_allowed, grant
from karaoke.assets import (
    AssetError,
    delete_asset,
    list_assets,
    save_asset_from_preview,
)
from karaoke.background import (
    BackgroundRef,
    background_status,
    build_background,
    cleanup_backgrounds,
)
from karaoke.director import DirectorError, analyze_scene
from karaoke.lyrics import display_name_from_stem, song_key_from_stem
from karaoke.media import IMAGE_SUFFIXES, find_original, find_stems, inspect_image, safe_title, sibling_stem
from karaoke.mix import normalize_mix_format, parse_mix_settings, render_mix
from karaoke.separate_runner import separate_kim2
from karaoke.align import fit_lines_to_voice
from karaoke.studio import StudioError, cache_path, is_preview_id, render_preview
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
    whisper_options,
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

ROOT = Path(__file__).resolve().parent.parent


def _load_env(env_path: Path | None = None) -> None:
    """Load `.env` without overriding real environment variables.

    A non-empty exported variable always wins (README documents that path).
    `.env` only fills variables that are missing or blank, so a copied
    `.env.example` with ``GEMINI_API_KEY=`` cannot erase a shell key. The
    default absolute path makes the server independent of the current directory.
    """
    env_path = Path(env_path) if env_path is not None else ROOT / ".env"
    if not env_path.is_file():
        return
    for key, value in dotenv_values(env_path).items():
        if value is None:
            continue
        if not os.environ.get(key, "").strip():
            os.environ[key] = value


_load_env()

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
# Full-resolution Studio renders run in the background so the Next proxy never
# has to hold a request open for a 16 s 1080p multi-effect encode.
_studio_jobs: dict[str, dict] = {}
_STUDIO_JOB_LIMIT = 20
_STUDIO_JOB_TTL_SECONDS = 30 * 60
_STUDIO_JOB_PATTERN = re.compile(r"^[0-9a-f]{32}$")


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
    background_effects: list[dict] = Field(default_factory=list)
    output_profile: str = "landscape_1080p"
    cover_image: str = ""
    output_dir: str = ""
    cover_duration: float = 5
    image_duration: float = 8
    language: str = "vi"
    model_name: str = "small"
    device: str = "cpu"
    no_speech_threshold: float | None = None
    whisper_retry: bool = True
    project_path: str = ""


class AlignRequest(BaseModel):
    model_name: str = "small"
    device: str = "cpu"
    no_speech_threshold: float | None = None
    whisper_retry: bool = True


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
    background_effects: list[dict] = Field(default_factory=list)
    output_profile: str = "landscape_1080p"
    cover_image: str = ""
    output_dir: str = ""
    cover_duration: float = 5
    image_duration: float = 8


class RevealRequest(BaseModel):
    path: str


class ImageInspectRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    output_profile: str = "landscape_1080p"
    with_preview: bool = False


class DirectorRequest(BaseModel):
    image_path: str = ""
    prompt: str = Field(default="", max_length=500)
    consent: bool = False
    model: str = ""
    output_profile: str = "landscape_1080p"


class StudioRenderRequest(BaseModel):
    image_path: str = ""
    effect_plan: dict = Field(default_factory=dict)
    duration: float = 8
    output_profile: str = "landscape_1080p"
    full_resolution: bool = False


class StudioAssetRequest(BaseModel):
    preview_id: str = ""
    name: str = ""
    image_path: str = ""
    image_hash: str = ""
    effect_plan: dict = Field(default_factory=dict)
    duration: float = 16
    output_profile: str = "landscape_1080p"


class BackgroundRequest(BaseModel):
    rebuild: bool = False


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
    rebuild_background: bool = False


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
                    "preview": "",
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
                    "preview": "",
                }
            )
            continue
        try:
            facts.append(
                inspect_image(
                    path,
                    output_profile=body.output_profile,
                    with_preview=body.with_preview,
                )
            )
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
                    "preview": "",
                }
            )
    return {"images": facts}


@app.post("/api/director/analyze")
def director_analyze(body: DirectorRequest):
    if not body.consent:
        raise HTTPException(
            status_code=400,
            detail="Cần đồng ý gửi ảnh lên Google trước khi phân tích.",
        )
    text = str(body.image_path or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Hãy chọn ảnh để phân tích.")
    try:
        path = ensure_allowed(Path(text))
    except AccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Không đọc được ảnh này.")
    try:
        return analyze_scene(
            path,
            body.prompt,
            consent=body.consent,
            # The model comes from the client; ignore anything outside the
            # server allowlist instead of interpolating it into the Gemini URL.
            model=(body.model or "").strip() or None,
            output_profile=body.output_profile,
        )
    except DirectorError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _preview_payload(result: dict) -> dict:
    preview_id = result["id"]
    return {
        "id": preview_id,
        "url": f"/api/studio/preview/{preview_id}",
        "width": result["width"],
        "height": result["height"],
        "duration": result["duration"],
    }


def _prune_studio_jobs() -> None:
    """Drop finished Studio jobs older than the TTL, then cap the list."""
    now = time.time()
    stale = [
        job_id
        for job_id, record in _studio_jobs.items()
        if record["status"] in {"done", "error"}
        and now - record["created"] > _STUDIO_JOB_TTL_SECONDS
    ]
    for job_id in stale:
        _studio_jobs.pop(job_id, None)
    if len(_studio_jobs) <= _STUDIO_JOB_LIMIT:
        return
    finished = sorted(
        (record["created"], job_id)
        for job_id, record in _studio_jobs.items()
        if record["status"] in {"done", "error"}
    )
    for _created, job_id in finished:
        if len(_studio_jobs) <= _STUDIO_JOB_LIMIT:
            break
        _studio_jobs.pop(job_id, None)


def _run_studio_job(
    job_id: str,
    path: Path,
    body: StudioRenderRequest,
) -> None:
    def progress(done: int, total: int) -> None:
        with _lock:
            record = _studio_jobs.get(job_id)
            if record is not None:
                record["done"] = done
                record["total"] = total
                record["percent"] = round(100.0 * done / max(1, total))

    try:
        result = render_preview(
            path,
            body.effect_plan,
            duration=body.duration,
            output_profile=body.output_profile,
            full_resolution=True,
            progress=progress,
        )
        payload = _preview_payload(result)
        with _lock:
            record = _studio_jobs.get(job_id)
            if record is not None:
                record["status"] = "done"
                record["result"] = payload
                record["percent"] = 100
    except Exception:
        traceback.print_exc()
        with _lock:
            record = _studio_jobs.get(job_id)
            if record is not None:
                record["status"] = "error"
                record["error"] = (
                    "Không render được bản chuẩn. Xem terminal Python để xem chi tiết."
                )


@app.post("/api/studio/render")
def studio_render(body: StudioRenderRequest):
    """Render an independent Studio preview loop (no Gemini call, no consent).

    ``full_resolution=true`` starts a background job and returns HTTP 202 with
    ``job_id``; poll ``/api/studio/render-job/{job_id}`` for the result.
    """
    text = str(body.image_path or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Hãy chọn ảnh để render preview.")
    try:
        path = ensure_allowed(Path(text))
    except AccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Không đọc được ảnh này.")
    if body.full_resolution:
        job_id = uuid.uuid4().hex
        with _lock:
            _prune_studio_jobs()
            _studio_jobs[job_id] = {
                "id": job_id,
                "status": "rendering",
                "created": time.time(),
                "error": "",
                "result": None,
                "done": 0,
                "total": 0,
                "percent": 0,
            }
        thread = threading.Thread(
            target=_run_studio_job,
            args=(job_id, path, body),
            daemon=True,
        )
        thread.start()
        return JSONResponse(
            status_code=202,
            content={"job_id": job_id, "status": "rendering"},
        )
    try:
        result = render_preview(
            path,
            body.effect_plan,
            duration=body.duration,
            output_profile=body.output_profile,
        )
    except StudioError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail="Không render được preview. Xem terminal Python để xem chi tiết.",
        )
    return _preview_payload(result)


@app.get("/api/studio/render-job/{job_id}")
def studio_render_job(job_id: str):
    """Return the status of a background full-resolution Studio render."""
    if not _STUDIO_JOB_PATTERN.fullmatch(str(job_id or "")):
        raise HTTPException(status_code=404, detail="Không thấy tác vụ render.")
    with _lock:
        _prune_studio_jobs()
        record = _studio_jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Không thấy tác vụ render.")
        return {
            "id": record["id"],
            "status": record["status"],
            "created": record["created"],
            "error": record["error"],
            "result": record["result"],
            "done": record.get("done", 0),
            "total": record.get("total", 0),
            "percent": record.get("percent", 0),
        }


@app.get("/api/studio/preview/{preview_id}")
def studio_preview(preview_id: str):
    """Serve a rendered Studio preview from the cache."""
    if not is_preview_id(preview_id):
        raise HTTPException(status_code=404, detail="Không thấy preview.")
    path = cache_path(preview_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Không thấy preview. Hãy render lại.")
    return FileResponse(path, media_type="video/mp4", filename=f"{preview_id}.mp4")


def _image_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


@app.post("/api/studio/assets")
def studio_asset_create(body: StudioAssetRequest):
    text = str(body.image_path or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Hãy chọn ảnh nguồn.")
    try:
        image = ensure_allowed(Path(text))
    except AccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    if not image.is_file():
        raise HTTPException(status_code=400, detail="Không thấy ảnh nguồn.")
    try:
        meta = save_asset_from_preview(
            preview_id=body.preview_id,
            name=body.name,
            image_path=image,
            image_hash=_image_hash(image),
            effect_plan=body.effect_plan,
            duration=body.duration,
            output_profile=body.output_profile,
        )
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Không lưu được asset. Xem terminal Python.")
    return {"asset": meta}


@app.get("/api/studio/assets")
def studio_assets():
    return {"assets": list_assets()}


@app.get("/api/studio/assets/{asset_id}/clip")
def studio_asset_clip(asset_id: str):
    from karaoke.assets import clip_path, load_asset

    meta = load_asset(asset_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Không thấy asset.")
    try:
        path = clip_path(meta)
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Không thấy clip asset.")
    return FileResponse(path, media_type="video/mp4", filename=f"{asset_id}.mp4")


@app.delete("/api/studio/assets/{asset_id}")
def studio_asset_delete(asset_id: str):
    try:
        deleted = delete_asset(asset_id)
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="Không thấy asset.")
    return {"deleted": True}


def _public_job(record: dict) -> dict:
    prepared = record.get("prepared") or {}
    background = record.get("background")
    if background is None and prepared:
        try:
            background = background_status(prepared, prepared.get("output_dir"))
        except Exception:
            background = None
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
        "background": background,
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
            "no_speech_threshold",
            "whisper_retry",
            "output_profile",
            "background_effects",
        ):
            # Only fields the client actually sent may change. Pydantic defaults
            # (notably output_profile="landscape_1080p") must not overwrite a
            # saved 9:16 project when an older client omits the field.
            if key not in body.model_fields_set:
                continue
            if key == "background_effects":
                prepared[key] = [dict(item) for item in payload[key] or [] if isinstance(item, dict)]
            else:
                prepared[key] = payload[key]
        if "background_images" in body.model_fields_set:
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


@app.get("/api/jobs/{job_id}/background")
def get_job_background(job_id: str):
    with _lock:
        record = _jobs.get(job_id)
        if record is None or not record.get("prepared"):
            raise HTTPException(status_code=404, detail="Chưa có dự án để dựng nền.")
        prepared = record["prepared"]
        current = record.get("background")
    if current is not None and str(current.get("state")) in {"building", "error"}:
        return current
    return background_status(prepared, prepared.get("output_dir"))


@app.post("/api/jobs/{job_id}/background")
def build_job_background(job_id: str, body: BackgroundRequest):
    with _lock:
        record = _jobs.get(job_id)
        if record is None or not record.get("prepared"):
            raise HTTPException(status_code=404, detail="Chưa có dự án để dựng nền.")
        prepared = record["prepared"]
        if record.get("status") in {"running", "rendering", "separating"}:
            raise HTTPException(status_code=409, detail="Việc này đang chạy.")
        current = record.get("background") or {}
        if str(current.get("state")) == "building":
            raise HTTPException(status_code=409, detail="Nền đang được dựng.")
        output_dir = str(prepared.get("output_dir") or "").strip()
        if not output_dir:
            raise HTTPException(status_code=400, detail="Hãy chọn thư mục xuất.")
        try:
            output = ensure_allowed(Path(output_dir))
        except AccessError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        grant(output)
        prepared["output_dir"] = str(output)
        record["background"] = {"state": "building", "percent": 0, "reason": ""}
        record["log"].append("Bắt đầu dựng nền có hiệu ứng.")

    def log(message: str) -> None:
        with _lock:
            record["log"].append(message)

    def progress(done, total) -> None:
        with _lock:
            current = record.get("background") or {}
            if str(current.get("state")) == "building":
                current["percent"] = round(100.0 * float(done) / max(1.0, float(total)))
                record["background"] = current

    def worker() -> None:
        try:
            background_ref = BackgroundRef.from_project(prepared.get("background_render"), output)
            build = build_background(
                prepared,
                output_dir=output,
                background=background_ref,
                force=body.rebuild,
                progress=progress,
                log=log,
            )
            prepared["background_render"] = build.to_project()
            lines = record.get("lines")
            project_path = sync_project_file(prepared, lines=lines, status=None)
            saved_project, saved_path = load_project(Path(project_path))
            status = background_status(prepared, output)
            with _lock:
                record["prepared"] = prepared
                record["project"] = public_project(saved_project, saved_path)
                record["background"] = status
                record["log"].append(
                    "Nền đã sẵn sàng." if status.get("state") == "ready" else "Nền đã dựng nhưng cần kiểm tra lại."
                )
        except Exception as error:
            traceback.print_exc()
            with _lock:
                record["background"] = {"state": "error", "percent": 0, "reason": str(error)}
                record["log"].append(f"Không dựng được nền: {error}")

    threading.Thread(target=worker, daemon=True).start()
    return {"id": job_id, "background": {"state": "building", "percent": 0}}


@app.post("/api/jobs/{job_id}/background/cleanup")
def cleanup_job_backgrounds(job_id: str):
    with _lock:
        record = _jobs.get(job_id)
        if record is None or not record.get("prepared"):
            raise HTTPException(status_code=404, detail="Chưa có dự án để dọn nền.")
        prepared = record["prepared"]
        output_text = str(prepared.get("output_dir") or "").strip()
        project_path = str(prepared.get("project_path") or "").strip()
        meta = prepared.get("background_render")
    if not output_text:
        raise HTTPException(status_code=400, detail="Hãy chọn thư mục xuất.")
    try:
        output = ensure_allowed(Path(output_text))
    except AccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    keep = [str(meta.get("hash"))] if isinstance(meta, dict) and meta.get("hash") else []
    deleted = cleanup_backgrounds(
        output,
        keep_hashes=keep,
        project_paths=[Path(project_path)] if project_path else [],
    )
    return {"deleted": deleted}


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


@app.post("/api/jobs/{job_id}/realign")
def realign_job(job_id: str, body: AlignRequest):
    """Run Whisper alignment again and replace the timing currently being edited."""
    try:
        options = whisper_options(body.model_dump())
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    with _lock:
        record = _jobs.get(job_id)
        if record is None or not record.get("prepared"):
            raise HTTPException(status_code=404, detail="Không thấy việc này.")
        if record["status"] in {"running", "rendering", "separating"}:
            raise HTTPException(status_code=409, detail="Việc này đang chạy.")
        prepared = record["prepared"]
        if not str(prepared.get("vocal_audio") or "").strip():
            raise HTTPException(status_code=400, detail="Cần file vocal để ghép chữ lại.")
        prepared.update(options)
        record["status"] = "running"
        record["error"] = ""
        record["log"].append(
            f"Ghép chữ lại bằng {options['model_name']} trên {options['device']}. Timing đang sửa sẽ được thay khi nghe xong."
        )

    def log(message: str) -> None:
        with _lock:
            record["log"].append(message)

    def worker() -> None:
        try:
            lines = align_job(prepared, log)
            project_path = sync_project_file(prepared, lines=lines, status="aligned")
            prepared["project_path"] = str(project_path)
            saved_project, saved_path = load_project(Path(project_path))
            with _lock:
                record["prepared"] = prepared
                record["lines"] = lines
                record["duration"] = prepared.get("vocal_duration") or record.get("duration") or 0
                record["project"] = public_project(saved_project, saved_path)
                record["log"].append("Ghép lại xong. Timing mới đã thay bản đang sửa.")
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
        if str((record.get("background") or {}).get("state")) == "building":
            raise HTTPException(status_code=409, detail="Nền đang được dựng. Hãy đợi xong.")
        record["status"] = "rendering"
        record["error"] = ""
        prepared = dict(record["prepared"])
        lines = [line.model_dump() for line in body.lines]
        export_options = {
            "output_dir": body.output_dir,
            "format": body.format,
            "rebuild_background": body.rebuild_background,
        }

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
        "background_effects": [
            dict(item) for item in project.get("background_effects") or [] if isinstance(item, dict)
        ],
        "output_profile": project.get("output_profile") or "landscape_1080p",
        "cover_image": project.get("cover_image") or "",
        "output_dir": project.get("output_dir") or "",
        "cover_duration": project.get("cover_duration") or 5,
        "image_duration": project.get("image_duration") or 8,
        "language": project.get("language") or "vi",
        "model_name": project.get("model_name") or "small",
        "device": project.get("device") or "cpu",
        "no_speech_threshold": project.get("no_speech_threshold"),
        "whisper_retry": bool(project.get("whisper_retry", True)),
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
    payload = body.model_dump()
    # Older clients do not send these fields; keep plans already stored in the project.
    for key in ("background_effects", "output_profile"):
        if key not in body.model_fields_set:
            payload.pop(key, None)
    try:
        project, path = _save_project_fields(payload)
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

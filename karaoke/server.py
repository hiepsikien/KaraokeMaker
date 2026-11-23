from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from contextlib import asynccontextmanager
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from pydantic import BaseModel, Field

from karaoke.access import AccessError, audio_root, ensure_allowed, grant
from karaoke.cache import data_root
from karaoke.assets import (
    ASSET_MAX_BYTES,
    ASSET_MAX_FILES,
    AssetError,
    asset_dir,
    assets_usage,
    cleanup_assets,
    clip_path,
    delete_asset,
    ensure_asset_thumbnail,
    forget_project_paths,
    known_project_paths,
    list_assets,
    load_asset,
    missing_project_entries,
    missing_project_paths,
    referenced_asset_projects,
    register_project_path,
    rename_asset,
    save_asset_from_preview,
)
from karaoke.background import (
    BackgroundRef,
    background_status,
    build_background,
    cleanup_backgrounds,
    cleanup_pictures,
)
from karaoke.director import DirectorError, analyze_scene, build_fit_transform
from karaoke.lyrics import display_name_from_stem, song_key_from_stem
from karaoke.media import COVER_RENDER_VERSION, IMAGE_SUFFIXES, find_original, find_stems, inspect_image, safe_title, sibling_stem
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
from karaoke.profiles import get_profile, normalize_profile_id
from karaoke.project import (
    assert_project_path_available,
    load_project,
    project_from_fields,
    project_path_for,
    public_project,
    recent_projects,
    restore_from_payload,
    save_project,
    sync_project_file,
)
from karaoke.settings import load_settings, public_settings, save_settings

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

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        from karaoke.publish.service import start_publish_worker

        start_publish_worker()
    except Exception as error:  # publishing is optional
        print(f"Không khôi phục được hàng đợi đăng bài: {error}")
    yield


app = FastAPI(title="KaraokeMaker", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
try:
    from karaoke.publish.router import router as publish_router

    app.include_router(publish_router)
except Exception as error:  # publishing is optional; never break the karaoke API
    print(f"Không nạp được module đăng bài: {error}")

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
# Full-resolution renders are CPU-heavy. Auto-render after Apply can enqueue
# several in a row, so cap how many FFmpeg encodes run at once.
_STUDIO_RENDER_SEMAPHORE = threading.Semaphore(2)


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
    overlay_effects: list[dict] = Field(default_factory=list)
    waveform: dict = Field(default_factory=dict)
    presentation: dict = Field(default_factory=dict)
    typography: dict = Field(default_factory=dict)
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
    overlay_effects: list[dict] = Field(default_factory=list)
    waveform: dict = Field(default_factory=dict)
    presentation: dict = Field(default_factory=dict)
    typography: dict = Field(default_factory=dict)
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
    reuse: bool = True


class StudioAssetRenameRequest(BaseModel):
    name: str = ""


class SettingsRequest(BaseModel):
    gemini_consent: bool | None = None
    gemini_api_key: str | None = None
    clear_gemini_api_key: bool = False
    device: str | None = None
    model_name: str | None = None
    output_dir: str | None = None


class ForgetProjectsRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)
    missing_only: bool = False
    parent_exists: bool | None = None


class BackgroundRequest(BaseModel):
    rebuild: bool = False


class PresentationRequest(BaseModel):
    project_path: str = ""
    presentation: dict | None = None
    waveform: dict | None = None
    typography: dict | None = None


class ManualDirectorRequest(BaseModel):
    image_path: str = ""
    output_profile: str = "landscape_1080p"


class CoverPreviewRequest(BaseModel):
    image_path: str = ""
    title: str = ""
    artist: str = ""
    cover_line: str = ""
    output_profile: str = "landscape_1080p"
    presentation: dict = Field(default_factory=dict)


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
    waveform: dict = Field(default_factory=dict)
    presentation: dict = Field(default_factory=dict)
    typography: dict = Field(default_factory=dict)


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
        "outro": "Chọn ảnh trang kết",
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
    if kind in {"cover", "outro"}:
        return f'''
set picked to choose file with prompt "{prompt}" of type {{"public.image"}} default location POSIX file "{location}"
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
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Phân tích ảnh lỗi: {error}") from error


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
        if record["status"] in {"done", "error", "cancelled"}
        and now - record["created"] > _STUDIO_JOB_TTL_SECONDS
    ]
    for job_id in stale:
        _studio_jobs.pop(job_id, None)
    if len(_studio_jobs) <= _STUDIO_JOB_LIMIT:
        return
    finished = sorted(
        (record["created"], job_id)
        for job_id, record in _studio_jobs.items()
        if record["status"] in {"done", "error", "cancelled"}
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
                if record.get("status") == "cancelled":
                    raise StudioError("Đã huỷ render.")
                record["done"] = done
                record["total"] = total
                record["percent"] = round(100.0 * done / max(1, total))

    try:
        with _STUDIO_RENDER_SEMAPHORE:
            with _lock:
                record = _studio_jobs.get(job_id)
                if record is None or record.get("status") == "cancelled":
                    raise StudioError("Đã huỷ render.")
            result = render_preview(
                path,
                body.effect_plan,
                duration=body.duration,
                output_profile=body.output_profile,
                full_resolution=True,
                progress=progress,
            )
            with _lock:
                record = _studio_jobs.get(job_id)
                if record is None or record.get("status") == "cancelled":
                    raise StudioError("Đã huỷ render.")
        payload = _preview_payload(result)
        with _lock:
            record = _studio_jobs.get(job_id)
            if record is not None and record.get("status") != "cancelled":
                record["status"] = "done"
                record["result"] = payload
                record["percent"] = 100
    except Exception as error:
        with _lock:
            record = _studio_jobs.get(job_id)
            cancelled = record is not None and record.get("status") == "cancelled"
            if record is not None and not cancelled:
                record["status"] = "error"
                record["error"] = f"Không render được bản chuẩn: {error}"
        if not cancelled:
            traceback.print_exc()


def _studio_job_public(record: dict) -> dict:
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
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Không render được preview: {error}",
        ) from error
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
        return _studio_job_public(record)


@app.post("/api/studio/render-job/{job_id}/cancel")
def studio_render_job_cancel(job_id: str):
    """Request cancellation of a running background render.

    FFmpeg itself cannot be interrupted mid-encode, so the worker checks the
    record between progress callbacks and discards the result.
    """
    if not _STUDIO_JOB_PATTERN.fullmatch(str(job_id or "")):
        raise HTTPException(status_code=404, detail="Không thấy tác vụ render.")
    with _lock:
        _prune_studio_jobs()
        record = _studio_jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Không thấy tác vụ render.")
        if record["status"] == "rendering":
            record["status"] = "cancelled"
            record["error"] = ""
        return _studio_job_public(record)


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


def _remember_project(path: Path) -> None:
    try:
        register_project_path(Path(path))
    except Exception:
        # The project file is the source of truth; the registry is only a GC hint.
        pass


def _referenced_asset_ids() -> set[str] | None:
    """Collect asset ids referenced by open jobs and project files.

    ``None`` means the scan was incomplete (an unreadable project file); the
    caller must skip cleanup rather than risk deleting a referenced asset.
    """
    ids: set[str] = set()
    with _lock:
        records = list(_jobs.values())
    project_paths: set[Path] = set()
    for record in records:
        prepared = record.get("prepared") or {}
        for effect in prepared.get("background_effects") or []:
            if not isinstance(effect, dict):
                continue
            asset_id = str(effect.get("asset_id") or "").strip()
            if asset_id:
                ids.add(asset_id)
        project = record.get("project")
        if isinstance(project, dict):
            for effect in project.get("background_effects") or []:
                if not isinstance(effect, dict):
                    continue
                asset_id = str(effect.get("asset_id") or "").strip()
                if asset_id:
                    ids.add(asset_id)
        output_text = str(prepared.get("output_dir") or "").strip()
        if output_text:
            try:
                output = Path(output_text).expanduser()
                if output.is_dir():
                    for project_file in output.glob("*.karaoke.json"):
                        project_paths.add(project_file)
            except OSError:
                return None
    try:
        project_paths.update(known_project_paths())
    except Exception:
        return None
    # Also scan the default project root so assets referenced by projects that
    # are not currently open are not garbage-collected.
    try:
        default_root = audio_root() / "Karaoke"
        if default_root.is_dir():
            for project_file in default_root.rglob("*.karaoke.json"):
                project_paths.add(project_file)
    except OSError:
        return None
    for project_path in project_paths:
        try:
            data = json.loads(project_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        for effect in (data.get("background_effects") or []) if isinstance(data, dict) else []:
            if not isinstance(effect, dict):
                continue
            asset_id = str(effect.get("asset_id") or "").strip()
            if asset_id:
                ids.add(asset_id)
    return ids


def _referenced_asset_projects() -> dict[str, list[str]] | None:
    """Map asset id -> project paths, including currently open jobs."""
    mapping = referenced_asset_projects()
    if mapping is None:
        return None
    with _lock:
        records = list(_jobs.values())
    for record in records:
        prepared = record.get("prepared") or {}
        label = str(prepared.get("project_path") or "").strip() or "dự án đang mở"
        effects = list(prepared.get("background_effects") or [])
        project = record.get("project")
        if isinstance(project, dict):
            effects.extend(project.get("background_effects") or [])
        for effect in effects:
            if not isinstance(effect, dict):
                continue
            asset_id = str(effect.get("asset_id") or "").strip()
            if not asset_id:
                continue
            paths = mapping.setdefault(asset_id, [])
            if label not in paths:
                paths.append(label)
    return mapping


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
            reuse=body.reuse,
        )
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Không lưu được asset. Xem terminal Python.")
    try:
        usage = assets_usage()
        if usage["count"] > ASSET_MAX_FILES or usage["bytes"] > ASSET_MAX_BYTES:
            referenced = _referenced_asset_ids()
            if referenced is not None:
                cleanup_assets(
                    keep_ids=referenced | {str(meta.get("id") or "")},
                    min_age_days=30.0,
                )
    except Exception:
        traceback.print_exc()
    return {"asset": meta}


@app.get("/api/studio/assets")
def studio_assets(summary: int = 0, usage: int = 0):
    assets = list_assets(summary=bool(summary))
    if usage:
        refs = _referenced_asset_projects()
        known = refs is not None
        for asset in assets:
            asset_id = str(asset.get("id") or "")
            asset["used_projects"] = list(refs.get(asset_id, [])) if known else []
            asset["usage_known"] = known
    return {"assets": assets}


@app.get("/api/studio/assets/usage")
def studio_asset_usage():
    return assets_usage()


@app.post("/api/studio/assets/cleanup")
def studio_asset_cleanup(dry_run: int = 0):
    try:
        referenced = _referenced_asset_ids()
        if referenced is None:
            raise HTTPException(
                status_code=409,
                detail="Không quét được toàn bộ dự án; giữ nguyên asset để an toàn.",
            )
        result = cleanup_assets(keep_ids=referenced, dry_run=bool(dry_run))
        result["dry_run"] = bool(dry_run)
        return result
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/studio/assets/{asset_id}/open")
def studio_asset_open(asset_id: str):
    """Grant an asset's source image and return its metadata for editing."""
    meta = load_asset(asset_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Không thấy asset.")
    text = str(meta.get("source_image") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Asset không có ảnh nguồn.")
    image = Path(text).expanduser()
    if not image.is_file() or image.suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Không thấy ảnh nguồn của asset.")
    grant(image)
    return {"asset": meta, "image": str(image)}


@app.post("/api/studio/assets/{asset_id}/edit")
def studio_asset_edit(asset_id: str):
    """Alias of ``/open`` while clients migrate to the "edit" wording."""
    return studio_asset_open(asset_id)


@app.post("/api/studio/assets/{asset_id}/reveal")
def studio_asset_reveal(asset_id: str):
    meta = load_asset(asset_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Không thấy asset.")
    try:
        clip = clip_path(meta)
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not clip.is_file():
        raise HTTPException(status_code=404, detail="Không thấy clip asset.")
    subprocess.run(["open", "-R", str(clip)], check=False)
    return {"ok": True}


@app.get("/api/studio/assets/{asset_id}/thumb")
def studio_asset_thumb(asset_id: str):
    meta = load_asset(asset_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Không thấy asset.")
    try:
        clip = clip_path(meta)
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not clip.is_file():
        raise HTTPException(status_code=404, detail="Không thấy clip asset.")
    try:
        thumb = ensure_asset_thumbnail(meta)
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not thumb.is_file():
        raise HTTPException(status_code=404, detail="Không thấy thumbnail asset.")
    return FileResponse(thumb, media_type="image/jpeg", filename=f"{asset_id}.jpg")


@app.patch("/api/studio/assets/{asset_id}")
def studio_asset_rename(asset_id: str, body: StudioAssetRenameRequest):
    if load_asset(asset_id) is None:
        raise HTTPException(status_code=404, detail="Không thấy asset.")
    try:
        meta = rename_asset(asset_id, body.name)
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"asset": meta}


@app.get("/api/studio/assets/{asset_id}/clip")
def studio_asset_clip(asset_id: str):
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


def _asset_reference_labels(asset_id: str) -> list[str]:
    labels: list[str] = []

    def add(value: str) -> None:
        text = str(value or "").strip() or "dự án đang mở"
        if text == "dự án đang mở":
            label = text
        else:
            label = Path(text).name or text
        if label not in labels:
            labels.append(label)

    mapping = referenced_asset_projects()
    for path in (mapping or {}).get(asset_id, []):
        add(path)
    with _lock:
        records = list(_jobs.values())
    for record in records:
        prepared = record.get("prepared") or {}
        project = record.get("project") if isinstance(record.get("project"), dict) else {}
        effects = list(prepared.get("background_effects") or [])
        effects.extend(project.get("background_effects") or [])
        for effect in effects:
            if not isinstance(effect, dict):
                continue
            if str(effect.get("asset_id") or "").strip() != asset_id:
                continue
            add(str(prepared.get("project_path") or ""))
            break
    return labels


@app.delete("/api/studio/assets/{asset_id}")
def studio_asset_delete(asset_id: str, force: bool = False):
    try:
        folder = asset_dir(asset_id)
    except AssetError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Không thấy asset.")
    referenced = _referenced_asset_ids()
    if referenced is None:
        raise HTTPException(
            status_code=409,
            detail="Không quét được toàn bộ dự án; giữ nguyên asset để an toàn.",
        )
    if asset_id in referenced and not force:
        labels = _asset_reference_labels(asset_id)[:3]
        used_in = ", ".join(labels) or "dự án đang mở"
        raise HTTPException(
            status_code=409,
            detail=(
                f"Clip đang được dùng trong {used_in}. "
                "Bỏ clip khỏi các dự án đó trước khi xoá."
            ),
        )
    try:
        deleted = delete_asset(asset_id)
    except AssetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="Không thấy asset.")
    return {"deleted": True}


def _invalidate_background(record: dict) -> None:
    record["background"] = None
    record["_background_cache"] = None


def _background_for_record(record: dict) -> dict | None:
    prepared = record.get("prepared") or {}
    if not prepared:
        return None
    cached = record.get("_background_cache")
    now = time.time()
    if cached is not None and now - float(cached[0]) < 2.0:
        return cached[1]
    try:
        background = background_status(prepared, prepared.get("output_dir"))
    except Exception:
        background = None
    record["_background_cache"] = (now, background)
    return background


def _public_job(record: dict) -> dict:
    prepared = record.get("prepared") or {}
    background = record.get("background")
    if background is None and prepared:
        background = _background_for_record(record)
    return {
        "id": record["id"],
        "status": record["status"],
        "created": float(record.get("created") or 0),
        "started": float(record.get("started") or record.get("created") or 0),
        "log": list(record["log"]),
        "error": record["error"],
        "outputs": record["outputs"],
        "lines": record["lines"],
        "duration": record["duration"],
        "export_gaps": export_gaps(prepared) if prepared else [],
        "render_percent": record.get("render_percent", 0),
        "project_path": prepared.get("project_path") or "",
        "project": record.get("project"),
        "restore": record.get("restore"),
        "background": background,
    }


def _finish_render_record(record: dict, prepared: dict, lines: list[dict], outputs: dict) -> None:
    try:
        background = background_status(prepared, prepared.get("output_dir"))
    except Exception:
        background = None
    with _lock:
        record["status"] = "done"
        record["outputs"] = outputs
        record["lines"] = lines
        record["prepared"] = prepared
        if background is not None:
            record["background"] = background


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
        if prepared.get("project_path"):
            _remember_project(Path(prepared["project_path"]))
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
            "overlay_effects",
            "waveform",
            "presentation",
            "typography",
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
        _remember_project(saved)
        prepared, lines, project = open_project_file(str(saved))
        if prepared.get("project_path"):
            _remember_project(Path(prepared["project_path"]))
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


@app.post("/api/projects/presentation")
def save_project_presentation(body: PresentationRequest):
    """Persist presentation/waveform/typography changes without touching media."""
    text = str(body.project_path or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Thiếu đường dẫn dự án.")
    try:
        prepared, lines, _project = open_project_file(text)
        for key in ("presentation", "waveform", "typography"):
            if key in body.model_fields_set:
                value = getattr(body, key)
                if isinstance(value, dict):
                    if key == "presentation":
                        merged = dict(prepared.get("presentation") or {})
                        for subsection, subvalue in value.items():
                            if isinstance(subvalue, dict) and isinstance(merged.get(subsection), dict):
                                merged[subsection] = {**merged[subsection], **subvalue}
                            else:
                                merged[subsection] = subvalue
                        prepared[key] = merged
                    else:
                        prepared[key] = value
        saved = sync_project_file(prepared, lines=lines or None, status=None)
        project, path = load_project(Path(saved))
        _remember_project(path)
        with _lock:
            for record in _jobs.values():
                current = record.get("prepared") or {}
                if str(current.get("project_path") or "") == str(path):
                    record["prepared"] = dict(prepared)
                    record["project"] = public_project(project, path)
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"project": public_project(project, path)}


@app.post("/api/director/manual")
def director_manual(body: ManualDirectorRequest):
    """Build a minimal, Gemini-free plan so the user can draw boxes manually."""
    text = str(body.image_path or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Hãy chọn ảnh để làm hiệu ứng thủ công.")
    try:
        path = ensure_allowed(Path(text))
    except AccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Không đọc được ảnh này.")
    try:
        with Image.open(path) as opened:
            width, height = opened.size
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        profile_id = normalize_profile_id(body.output_profile)
        transform = build_fit_transform(width, height)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    plan = {
        "version": 1,
        "image_hash": digest,
        "profile_id": profile_id,
        "fit_transform": transform,
        "objects": [],
        "regions": [],
        "protect": [],
        "items": [],
    }
    return {
        "image": str(path),
        "image_hash": digest,
        "width": width,
        "height": height,
        "model": "manual",
        "scene": "",
        "objects": [],
        "regions": [],
        "protect": [],
        "intent": None,
        "effect_plan": plan,
        "usage": None,
        "cached": False,
    }


@app.post("/api/presentation/cover-preview")
def presentation_cover_preview(body: CoverPreviewRequest):
    """Render a single cover card as PNG for the setup preview."""
    from karaoke.media import make_cover
    from karaoke.presentation import normalize_presentation

    text = str(body.image_path or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Hãy chọn ảnh bìa.")
    try:
        photo = ensure_allowed(Path(text))
    except AccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    if not photo.is_file() or photo.suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Không đọc được ảnh bìa.")
    profile = get_profile(body.output_profile)
    cache_dir = data_root() / "presentation"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(
        (
            str(photo.resolve())
            + str(photo.stat().st_mtime_ns)
            + json.dumps(normalize_presentation(body.presentation), sort_keys=True, ensure_ascii=False)
            + body.title
            + body.artist
            + body.cover_line
            + profile.id
            + f"|v{COVER_RENDER_VERSION}"
        ).encode("utf-8")
    ).hexdigest()[:24]
    destination = cache_dir / f"cover-{key}.png"
    wrote_new = False
    if not destination.is_file():
        temporary = cache_dir / f".cover-{key}.{os.getpid()}.{uuid.uuid4().hex}.tmp.png"
        try:
            make_cover(
                photo,
                body.title,
                body.artist,
                body.cover_line,
                temporary,
                profile=profile,
                style=normalize_presentation(body.presentation).get("cover"),
            )
            os.replace(temporary, destination)
            wrote_new = True
        finally:
            temporary.unlink(missing_ok=True)
    if wrote_new:
        try:
            cutoff = time.time() - 7 * 86400
            for old in cache_dir.glob("cover-*.png"):
                if old.is_file() and old.stat().st_mtime < cutoff:
                    old.unlink(missing_ok=True)
        except OSError:
            pass
    return FileResponse(destination, media_type="image/png", filename="cover.png")


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


@app.get("/api/projects/recent")
def projects_recent(limit: int = 12):
    """Compact cards for the dashboard, newest project file first."""
    try:
        cap = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        cap = 12
    try:
        paths = known_project_paths()
    except Exception:
        paths = []
    return {"projects": recent_projects(paths, limit=cap)}


@app.get("/api/projects/registry/stale")
def projects_registry_stale():
    entries = missing_project_entries()
    return {
        "paths": [str(entry["path"]) for entry in entries],
        "entries": [
            {"path": str(entry["path"]), "parent_exists": entry["parent_exists"]}
            for entry in entries
        ],
    }


@app.post("/api/projects/registry/forget")
def projects_registry_forget(body: ForgetProjectsRequest):
    removed = forget_project_paths(
        paths=body.paths,
        missing_only=body.missing_only,
        parent_exists=body.parent_exists,
    )
    return {"removed": removed}


@app.get("/api/settings")
def get_settings():
    return {"settings": public_settings(load_settings())}


@app.put("/api/settings")
def update_settings(body: SettingsRequest):
    current = load_settings()
    updates = body.model_dump(exclude_unset=True)
    clear_key = bool(updates.pop("clear_gemini_api_key", False))
    new_key = updates.pop("gemini_api_key", None)
    merged = {**current}
    for key, value in updates.items():
        if value is not None:
            merged[key] = value
    if clear_key:
        merged["gemini_api_key"] = ""
    elif isinstance(new_key, str) and new_key.strip():
        merged["gemini_api_key"] = new_key.strip()
    return {"settings": public_settings(save_settings(merged))}


@app.post("/api/jobs")
def create_job(body: CreateJobRequest):
    job_id = uuid.uuid4().hex
    payload = body.model_dump()
    record = {
        "id": job_id,
        "status": "running",
        "created": time.time(),
        "started": time.time(),
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
            _remember_project(project_path)
            log(f"Đã lưu dự án {Path(project_path).name}.")
            lines = align_job(prepared, log)
            project_path = sync_project_file(prepared, lines=lines, status="aligned")
            _remember_project(project_path)
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
        _remember_project(project_path)
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
            _remember_project(project_path)
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
    try:
        pictures_deleted = cleanup_pictures(output)
    except Exception:
        pictures_deleted = []
    return {"deleted": deleted, "pictures_deleted": pictures_deleted}


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
        record["started"] = time.time()
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
            _remember_project(project_path)
            prepared["project_path"] = str(project_path)
            saved_project, saved_path = load_project(Path(project_path))
            with _lock:
                record["prepared"] = prepared
                _invalidate_background(record)
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
        _remember_project(project_path)
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
        record["render_percent"] = 0
        prepared = dict(record["prepared"])
        for key in ("waveform", "presentation", "typography"):
            if key in body.model_fields_set:
                prepared[key] = dict(getattr(body, key) or {})
        lines = [line.model_dump() for line in body.lines]
        export_options = {
            "output_dir": body.output_dir,
            "format": body.format,
            "rebuild_background": body.rebuild_background,
        }

    def log(message: str) -> None:
        with _lock:
            record["log"].append(message)

    def progress(done, total) -> None:
        with _lock:
            record["render_percent"] = round(100.0 * float(done) / max(1.0, float(total)))

    def worker() -> None:
        try:
            outputs = render_job(
                prepared,
                lines,
                log,
                export_options=export_options,
                progress=progress,
            )
            with _lock:
                record["render_percent"] = 100
            _finish_render_record(record, prepared, lines, outputs)
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
        "overlay_effects": [
            dict(item) for item in project.get("overlay_effects") or [] if isinstance(item, dict)
        ],
        "waveform": dict(project.get("waveform") or {}),
        "presentation": dict(project.get("presentation") or {}),
        "typography": dict(project.get("typography") or {}),
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
            _remember_project(project_path)
            prepared["project_path"] = str(project_path)
            saved_project, saved_path = load_project(Path(project_path))
            with _lock:
                record["restore"] = restore
                record["prepared"] = prepared
                _invalidate_background(record)
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
        _remember_project(project_path)
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
        _remember_project(project_path)
        saved_project, saved_path = load_project(Path(project_path))
    except JobError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    project = public_project(saved_project, saved_path)
    with _lock:
        if _jobs.get(job_id) is record:
            record["restore"] = restore
            record["prepared"] = prepared
            _invalidate_background(record)
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

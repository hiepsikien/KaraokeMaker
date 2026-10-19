from __future__ import annotations

import subprocess
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from karaoke.lyrics import display_name_from_stem, song_key_from_stem
from karaoke.media import AUDIO_ROOT, IMAGE_SUFFIXES, find_original, find_stems, inspect_image, sibling_stem
from karaoke.pipeline import JobError, align_job, prepare_job, render_job

app = FastAPI(title="KaraokeMaker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class BrowseRequest(BaseModel):
    kind: str
    initial_dir: str = ""


class MatchRequest(BaseModel):
    path: str


class CreateJobRequest(BaseModel):
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
    words: list[WordIn] = Field(default_factory=list)


class RenderRequest(BaseModel):
    lines: list[LineIn] = Field(default_factory=list)


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
    songs = AUDIO_ROOT / "Songs"
    separated = AUDIO_ROOT / "Separated" / "Kim2"
    if kind == "original" and songs.is_dir():
        return str(songs)
    if kind in {"vocal", "instrumental"} and separated.is_dir():
        return str(separated)
    if kind == "output":
        karaoke = AUDIO_ROOT / "Karaoke"
        if AUDIO_ROOT.is_dir():
            return str(karaoke if karaoke.is_dir() else AUDIO_ROOT)
    if AUDIO_ROOT.is_dir():
        return str(AUDIO_ROOT)
    return str(Path.home())


def _browse_script(kind: str, directory: str) -> str:
    location = _apple_string(directory)
    prompt = {
        "original": "Chọn audio gốc",
        "vocal": "Chọn file vocal",
        "instrumental": "Chọn file instrumental",
        "cover": "Chọn ảnh bìa",
        "images": "Chọn ảnh nền",
        "output": "Chọn thư mục xuất",
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
    return f'''
set picked to choose file with prompt "{prompt}" default location POSIX file "{location}"
return POSIX path of picked
'''


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/defaults")
def defaults():
    songs = AUDIO_ROOT / "Songs"
    separated = AUDIO_ROOT / "Separated" / "Kim2"
    output = AUDIO_ROOT / "Karaoke" if AUDIO_ROOT.is_dir() else Path.home() / "Karaoke"
    return {
        "songs_dir": str(songs) if songs.is_dir() else "",
        "separated_dir": str(separated) if separated.is_dir() else "",
        "output_dir": str(output),
    }


@app.post("/api/browse")
def browse(body: BrowseRequest):
    directory = _default_dir(body.kind, body.initial_dir)
    raw = _run_osascript(_browse_script(body.kind, directory))
    paths = [line.strip() for line in raw.splitlines() if line.strip()]
    if body.kind != "images" and len(paths) > 1:
        paths = paths[:1]
    return {"paths": paths}


@app.post("/api/match")
def match_audio(body: MatchRequest):
    path = Path(body.path).expanduser()
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
        path = Path(raw).expanduser()
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
    return {
        "id": record["id"],
        "status": record["status"],
        "log": list(record["log"]),
        "error": record["error"],
        "outputs": record["outputs"],
        "lines": record["lines"],
        "duration": record["duration"],
    }


@app.post("/api/jobs")
def create_job(body: CreateJobRequest):
    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "status": "running",
        "log": ["Bắt đầu căn lời."],
        "error": "",
        "outputs": None,
        "lines": None,
        "duration": 0,
        "prepared": None,
    }
    with _lock:
        _jobs[job_id] = record

    def log(message: str) -> None:
        with _lock:
            record["log"].append(message)

    def worker() -> None:
        try:
            prepared = prepare_job(body.model_dump(), log)
            lines = align_job(prepared, log)
            with _lock:
                record["prepared"] = prepared
                record["lines"] = lines
                record["duration"] = prepared["vocal_duration"]
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
        path = Path(record["prepared"]["vocal_audio"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Không thấy file vocal.")
    media_type = _AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name)


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
        prepared = record["prepared"]
        lines = [line.model_dump() for line in body.lines]

    def log(message: str) -> None:
        with _lock:
            record["log"].append(message)

    def worker() -> None:
        try:
            outputs = render_job(prepared, lines, log)
            with _lock:
                record["status"] = "done"
                record["outputs"] = outputs
                record["lines"] = lines
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


@app.post("/api/reveal")
def reveal(body: RevealRequest):
    path = Path(body.path).expanduser()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Không thấy file để mở.")
    subprocess.run(["open", "-R", str(path)], check=False)
    return {"ok": True}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")

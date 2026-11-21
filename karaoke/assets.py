"""Stable Studio background assets.

Studio previews live in a pruned cache. Saving an asset copies the rendered
clip into the platform app-data directory and stores enough metadata to decide
whether the clip can be reused by the karaoke export.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from karaoke.cache import data_root
from karaoke.living import MOTION_CACHE_VERSION
from karaoke.profiles import get_profile, normalize_profile_id
from karaoke.studio import cache_path, is_preview_id, preview_id_for

ASSET_VERSION = 1
ASSET_ID_PATTERN = re.compile(r"^bg_[0-9a-f]{32}$")
# Studio renders effects with a zero-height strip. Keep this in the asset
# fingerprint so a later profile change does not silently reuse the clip.
ASSET_STRIP_RECT = (0, 0, 0, 0)
CLIP_FILE = "loop.mp4"
META_FILE = "meta.json"
_LOCK = threading.Lock()
_SHA_CACHE: dict[tuple[str, int, int], str] = {}


class AssetError(Exception):
    """Raised when an asset cannot be saved or read."""


def assets_root() -> Path:
    return data_root("assets")


def _sha256(path: Path) -> str:
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _LOCK:
        cached = _SHA_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    value = "sha256:" + digest.hexdigest()
    with _LOCK:
        _SHA_CACHE[key] = value
    return value


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _probe_clip(path: Path) -> dict:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height,r_frame_rate,avg_frame_rate,nb_frames,pix_fmt,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise AssetError(process.stderr.strip() or "ffprobe không đọc được clip.")
    try:
        data = json.loads(process.stdout or "{}")
    except json.JSONDecodeError as error:
        raise AssetError("ffprobe trả dữ liệu không hợp lệ.") from error
    streams = data.get("streams") or []
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not videos or audios:
        raise AssetError("Clip asset phải có video và không có audio.")
    video = videos[0]
    fps_text = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
    try:
        left, right = fps_text.split("/", 1)
        fps = float(left) / max(1.0, float(right))
    except ValueError:
        fps = 0.0
    frames_text = str(video.get("nb_frames") or "").strip()
    frames = int(frames_text) if frames_text.isdigit() else 0
    duration = video.get("duration") or (data.get("format") or {}).get("duration")
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        seconds = 0.0
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "frames": frames,
        "duration": seconds,
        "pix_fmt": str(video.get("pix_fmt") or ""),
    }


def _profile_payload(profile) -> str:
    return json.dumps(
        {
            "width": profile.width,
            "height": profile.height,
            "fps": profile.fps,
            "fit_mode": profile.fit_mode,
            "strip_rect": list(ASSET_STRIP_RECT),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def asset_fingerprint(meta: dict) -> str:
    payload = json.dumps(
        {
            "asset_version": meta.get("asset_version"),
            "clip_sha256": meta.get("clip_sha256"),
            "renderer_version": meta.get("renderer_version"),
            "asset_frames": meta.get("asset_frames"),
            "image_hash": meta.get("image_hash"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _hash_text(payload)


def asset_dir(asset_id: str) -> Path:
    if not ASSET_ID_PATTERN.fullmatch(str(asset_id or "")):
        raise AssetError("Mã asset không hợp lệ.")
    return assets_root() / asset_id


def clip_path(meta: dict) -> Path:
    root = asset_dir(str(meta.get("id") or ""))
    name = str(meta.get("clip_file") or CLIP_FILE)
    if Path(name).name != name:
        raise AssetError("Tên clip asset không hợp lệ.")
    return root / name


def save_asset_from_preview(
    *,
    preview_id: str,
    name: str,
    image_path: Path,
    image_hash: str,
    effect_plan: dict,
    duration: float,
    output_profile: str,
) -> dict:
    """Copy a rendered Studio preview into the stable asset store."""
    if not is_preview_id(preview_id):
        raise AssetError("Preview không hợp lệ.")
    profile = get_profile(normalize_profile_id(output_profile))
    try:
        expected_id = preview_id_for(
            image_path,
            effect_plan,
            duration=duration,
            output_profile=profile.id,
            full_resolution=True,
        )
    except Exception as error:
        raise AssetError(f"Không kiểm tra được preview: {error}") from error
    if expected_id != preview_id:
        raise AssetError("Preview không khớp ảnh, effect plan, thời lượng hoặc profile.")
    source = cache_path(preview_id)
    if not source.is_file() or source.stat().st_size <= 0:
        raise AssetError("Không thấy clip preview. Hãy render bản chuẩn trước.")
    probe = _probe_clip(source)
    expected_frames = max(1, int(round(float(duration) * profile.fps)))
    if probe["width"] != profile.width or probe["height"] != profile.height:
        raise AssetError("Clip preview không đúng kích thước profile.")
    if abs(probe["fps"] - profile.fps) > 0.05:
        raise AssetError("Clip preview không đúng fps profile.")
    if probe["pix_fmt"] != "yuv420p":
        raise AssetError("Clip preview không phải yuv420p.")
    frames = probe["frames"] or expected_frames
    if abs(frames - expected_frames) > 1:
        raise AssetError("Clip preview không đúng thời lượng đã khai.")
    if probe["frames"] and abs(probe["duration"] - frames / profile.fps) > 1.5 / profile.fps:
        raise AssetError("Clip preview có duration không khớp số frame.")

    asset_id = "bg_" + uuid.uuid4().hex
    folder = asset_dir(asset_id)
    folder.mkdir(parents=True, exist_ok=False)
    destination = folder / CLIP_FILE
    temporary = folder / f".{CLIP_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with _LOCK:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        size = destination.stat().st_size
        mtime = destination.stat().st_mtime
        meta = {
            "id": asset_id,
            "name": str(name or "").strip() or f"Nền {asset_id[-6:]}",
            "source_image": str(Path(image_path).expanduser()),
            "image_hash": image_hash,
            "preview_id": preview_id,
            "effect_plan": effect_plan if isinstance(effect_plan, dict) else {},
            "asset_version": ASSET_VERSION,
            "profile_id": profile.id,
            "profile_fingerprint": _hash_text(_profile_payload(profile)),
            "width": profile.width,
            "height": profile.height,
            "fps": profile.fps,
            "fit_mode": profile.fit_mode,
            "strip_rect": list(ASSET_STRIP_RECT),
            "renderer_version": MOTION_CACHE_VERSION,
            "loop_duration": frames / profile.fps,
            "asset_frames": frames,
            "clip_file": CLIP_FILE,
            "clip_size": size,
            "clip_mtime": mtime,
            "clip_sha256": _sha256(destination),
            "created": datetime.now(timezone.utc).isoformat(),
        }
        _write_meta(folder, meta)
        return meta
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _write_meta(folder: Path, meta: dict) -> None:
    temporary = folder / f".{META_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, folder / META_FILE)


def load_asset(asset_id: str) -> dict | None:
    try:
        folder = asset_dir(asset_id)
    except AssetError:
        return None
    path = folder / META_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_assets() -> list[dict]:
    root = assets_root()
    if not root.is_dir():
        return []
    assets: list[dict] = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        meta = load_asset(folder.name)
        if meta is not None:
            assets.append(meta)
    assets.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
    return assets


def validate_asset(
    meta: dict | None,
    *,
    image_hash: str,
    image_frames: int,
    profile,
) -> tuple[bool, str]:
    if not isinstance(meta, dict):
        return False, "không thấy asset"
    if int(meta.get("asset_version") or 0) != ASSET_VERSION:
        return False, "asset_version không khớp"
    if int(meta.get("renderer_version") or 0) != MOTION_CACHE_VERSION:
        return False, "renderer_version không khớp"
    if str(meta.get("image_hash") or "") != image_hash:
        return False, "ảnh nguồn đã đổi"
    if str(meta.get("profile_fingerprint") or "") != _hash_text(_profile_payload(profile)):
        return False, "profile đã đổi"
    if str(meta.get("fit_mode") or "") != profile.fit_mode:
        return False, "fit_mode đã đổi"
    if int(meta.get("fps") or 0) != profile.fps:
        return False, "fps đã đổi"
    if list(meta.get("strip_rect") or []) != list(ASSET_STRIP_RECT):
        return False, "strip_rect đã đổi"
    try:
        frames = int(meta.get("asset_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    if frames < image_frames or frames - image_frames > 1:
        return False, "thời lượng clip không khớp"
    try:
        path = clip_path(meta)
        if not path.is_file() or path.stat().st_size <= 0:
            return False, "không thấy clip"
        if int(meta.get("clip_size") or -1) != path.stat().st_size:
            return False, "clip_size đã đổi"
        expected_sha = str(meta.get("clip_sha256") or "")
        if expected_sha and _sha256(path) != expected_sha:
            return False, "clip_sha256 đã đổi"
    except (OSError, AssetError) as error:
        return False, str(error)
    return True, ""


def delete_asset(asset_id: str) -> bool:
    folder = asset_dir(asset_id)
    if not folder.is_dir():
        return False
    if folder.is_symlink():
        raise AssetError("Không xoá asset qua symlink.")
    shutil.rmtree(folder)
    return True

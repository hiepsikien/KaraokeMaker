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
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from stat import S_ISREG

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - macOS/Linux
    msvcrt = None

from karaoke.cache import data_root
from karaoke.living import MOTION_CACHE_VERSION, visual_plan_hash
from karaoke.profiles import get_profile, normalize_profile_id
from karaoke.studio import cache_path, is_preview_id, preview_id_for

ASSET_VERSION = 2
ASSET_ID_PATTERN = re.compile(r"^bg_[0-9a-f]{32}$")
# Studio renders effects with a zero-height strip. Keep this in the asset
# fingerprint so a later profile change does not silently reuse the clip.
ASSET_STRIP_RECT = (0, 0, 0, 0)
CLIP_FILE = "loop.mp4"
META_FILE = "meta.json"
# Auto-save must stay bounded: without a quota every Studio save would grow the
# stable store forever. These defaults keep roughly 100 clips / ~2 GB.
ASSET_MAX_FILES = 100
ASSET_MAX_BYTES = 2_000_000_000
ASSET_MIN_AGE_DAYS = 7.0
PROJECTS_FILE = ".projects.json"
_LOCK = threading.Lock()
_SHA_CACHE: dict[tuple[str, int, int], str] = {}


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            handle.seek(0)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


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


def _parse_fps(value) -> float:
    text = str(value or "").strip()
    if not text or text in {"N/A", "0/0"}:
        return 0.0
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            return float(left) / max(1.0, float(right))
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_duration(value) -> float:
    text = str(value or "").strip()
    if not text or text == "N/A":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


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
    fps = _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    frames_text = str(video.get("nb_frames") or "").strip()
    frames = int(frames_text) if frames_text.isdigit() else 0
    duration = _parse_duration(video.get("duration"))
    if duration <= 0:
        duration = _parse_duration((data.get("format") or {}).get("duration"))
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": fps,
        "frames": frames,
        "duration": duration,
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
            "plan_hash": meta.get("plan_hash"),
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


def _save_asset_from_preview_impl(
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
    plan_hash = visual_plan_hash(effect_plan) if isinstance(effect_plan, dict) else ""
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
    if frames < expected_frames:
        raise AssetError("Clip preview ngắn hơn thời lượng đã khai.")
    if frames - expected_frames > 1:
        raise AssetError("Clip preview dài hơn thời lượng đã khai.")
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
            "plan_hash": plan_hash,
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


def save_asset_from_preview(*args, **kwargs) -> dict:
    root = assets_root()
    root.mkdir(parents=True, exist_ok=True)
    with _file_lock(root / ".assets.lock"):
        return _save_asset_from_preview_impl(*args, **kwargs)


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


def _folder_size(folder: Path) -> int | None:
    """Sum regular files below ``folder`` without following symlinks."""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if not os.path.islink(os.path.join(dirpath, name))
            ]
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    if path.is_symlink():
                        continue
                    info = path.stat()
                except OSError:
                    return None
                if S_ISREG(info.st_mode):
                    total += info.st_size
    except OSError:
        return None
    return total


def _asset_usage_entry(folder: Path) -> dict | None:
    """Return ``{id, folder, bytes, mtime}`` or ``None`` when unsafe to inspect."""
    try:
        if folder.is_symlink() or not folder.is_dir():
            return None
        meta_path = folder / META_FILE
        if meta_path.is_symlink():
            return None
        bytes_used: int | None = None
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = None
            if isinstance(meta, dict):
                name = str(meta.get("clip_file") or CLIP_FILE)
                if Path(name).name != name:
                    return None
                clip = folder / name
                if clip.is_symlink():
                    return None
                try:
                    info = clip.stat()
                except OSError:
                    return None
                if not S_ISREG(info.st_mode):
                    return None
                bytes_used = info.st_size
        if bytes_used is None:
            # Missing/corrupt metadata: fall back to the whole directory.
            bytes_used = _folder_size(folder)
            if bytes_used is None:
                return None
        info = meta_path.stat() if meta_path.is_file() else folder.stat()
        return {
            "id": folder.name,
            "folder": folder,
            "bytes": int(bytes_used),
            "mtime": info.st_mtime,
        }
    except OSError:
        return None


def _iter_asset_entries(root: Path) -> list[dict]:
    try:
        folders = list(root.iterdir())
    except OSError:
        return []
    entries: list[dict] = []
    for folder in folders:
        if not ASSET_ID_PATTERN.fullmatch(folder.name):
            continue
        entry = _asset_usage_entry(folder)
        if entry is not None:
            entries.append(entry)
    return entries


def assets_usage() -> dict:
    """Return ``{"count": int, "bytes": int}`` for readable asset folders."""
    root = assets_root()
    if not root.is_dir():
        return {"count": 0, "bytes": 0}
    entries = _iter_asset_entries(root)
    return {
        "count": len(entries),
        "bytes": sum(entry["bytes"] for entry in entries),
    }


def register_project_path(path: Path) -> None:
    """Remember a project file so asset GC can scan it even when closed."""
    root = assets_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / PROJECTS_FILE
    entry = str(Path(path).expanduser().resolve())
    with _file_lock(root / ".assets.lock"):
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = []
        if not isinstance(existing, list):
            existing = []
        if entry in existing:
            return
        existing.append(entry)
        temporary = target.with_name(
            f".{PROJECTS_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def known_project_paths() -> list[Path]:
    """Return existing project files recorded by :func:`register_project_path`."""
    target = assets_root() / PROJECTS_FILE
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    paths: list[Path] = []
    for item in data:
        try:
            path = Path(str(item)).expanduser()
        except (TypeError, ValueError):
            continue
        if path.is_file():
            paths.append(path)
    return paths


def cleanup_assets(
    *,
    keep_ids: set[str] | None = None,
    max_files: int = ASSET_MAX_FILES,
    max_bytes: int = ASSET_MAX_BYTES,
    min_age_days: float = ASSET_MIN_AGE_DAYS,
) -> dict:
    """Delete the oldest unreferenced assets until the quota is satisfied.

    Assets in ``keep_ids``, anything newer than ``min_age_days`` and anything
    that cannot be inspected safely are never removed.
    """
    root = assets_root()
    if not root.is_dir():
        return {"deleted": [], "freed_bytes": 0, "count": 0, "bytes": 0}
    keep = {str(value) for value in (keep_ids or set())}
    cutoff = time.time() - max(0.0, float(min_age_days)) * 86400
    with _file_lock(root / ".assets.lock"):
        entries = _iter_asset_entries(root)
        count = len(entries)
        total_bytes = sum(entry["bytes"] for entry in entries)
        candidates = [
            entry
            for entry in entries
            if entry["id"] not in keep and entry["mtime"] <= cutoff
        ]
        candidates.sort(key=lambda entry: (entry["mtime"], entry["id"]))
        deleted: list[str] = []
        freed_bytes = 0
        for entry in candidates:
            if count <= max_files and total_bytes <= max_bytes:
                break
            folder = entry["folder"]
            try:
                if folder.is_symlink() or not folder.is_dir():
                    continue
                shutil.rmtree(folder)
            except OSError:
                continue
            deleted.append(entry["id"])
            freed_bytes += entry["bytes"]
            count -= 1
            total_bytes -= entry["bytes"]
        return {
            "deleted": deleted,
            "freed_bytes": freed_bytes,
            "count": count,
            "bytes": total_bytes,
        }


def validate_asset(
    meta: dict | None,
    *,
    image_hash: str,
    image_frames: int,
    profile,
    plan: dict | None = None,
) -> tuple[bool, str]:
    if not isinstance(meta, dict):
        return False, "không thấy asset"
    if int(meta.get("asset_version") or 0) != ASSET_VERSION:
        return False, "asset_version không khớp"
    if plan is not None and str(meta.get("plan_hash") or "") != visual_plan_hash(plan):
        return False, "effect plan đã đổi"
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
    root = assets_root()
    root.mkdir(parents=True, exist_ok=True)
    with _file_lock(root / ".assets.lock"):
        folder = asset_dir(asset_id)
        if not folder.is_dir():
            return False
        if folder.is_symlink():
            raise AssetError("Không xoá asset qua symlink.")
        shutil.rmtree(folder)
        return True

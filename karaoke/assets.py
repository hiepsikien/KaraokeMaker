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
import unicodedata
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
ASSET_NAME_MAX = 120
THUMB_VERSION = "thumb-v1"
PROJECTS_FILE = ".projects.json"
_LOCK = threading.Lock()
_SHA_CACHE: dict[tuple[str, int, int], str] = {}
_PROJECT_REFS_LOCK = threading.Lock()
_PROJECT_REFS_FILES: dict[tuple[str, int, int], tuple[str, ...]] = {}
_PROJECT_REFS_SNAPSHOT: tuple[
    tuple[tuple[str, int, int], ...], dict[str, list[str]]
] | None = None
_THUMB_LOCKS: dict[str, threading.Lock] = {}
# Thumbnail generation is cheap individually but expensive when the library
# requests 100 posters at once. Keep it behind the same kind of bound as render.
try:
    _THUMB_WORKERS = max(1, int(os.environ.get("KARAOKE_THUMB_WORKERS", "2") or "2"))
except ValueError:
    _THUMB_WORKERS = 2
_THUMB_SEMAPHORE = threading.Semaphore(_THUMB_WORKERS)


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
        if len(_SHA_CACHE) >= 512:
            _SHA_CACHE.clear()
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
    plan_hash = meta.get("plan_hash")
    if not plan_hash and isinstance(meta.get("effect_plan"), dict):
        plan_hash = visual_plan_hash(meta["effect_plan"])
    payload = json.dumps(
        {
            "asset_version": meta.get("asset_version"),
            "clip_sha256": meta.get("clip_sha256"),
            "renderer_version": meta.get("renderer_version"),
            "asset_frames": meta.get("asset_frames"),
            "image_hash": meta.get("image_hash"),
            "plan_hash": plan_hash,
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


def _find_reusable_asset(
    *,
    image_hash: str,
    plan_hash: str,
    profile,
    frames: int,
) -> dict | None:
    """Return an existing asset rendered from the same inputs.

    The Studio renders the same effect plan repeatedly while the user tweaks
    nearby settings. Reusing the stored clip keeps auto-save from turning the
    library into a pile of identical ``bg_*`` folders.
    """
    root = assets_root()
    if not root.is_dir():
        return None
    for folder in root.iterdir():
        if not ASSET_ID_PATTERN.fullmatch(folder.name):
            continue
        meta = load_asset(folder.name)
        if not isinstance(meta, dict):
            continue
        if int(meta.get("asset_version") or 0) != ASSET_VERSION:
            continue
        if int(meta.get("renderer_version") or 0) != MOTION_CACHE_VERSION:
            continue
        if str(meta.get("image_hash") or "") != str(image_hash):
            continue
        if str(meta.get("plan_hash") or "") != str(plan_hash):
            continue
        if str(meta.get("profile_id") or "") != str(profile.id):
            continue
        try:
            if int(meta.get("asset_frames") or 0) != int(frames):
                continue
            path = clip_path(meta)
            if not path.is_file() or path.stat().st_size <= 0:
                continue
            expected_sha = str(meta.get("clip_sha256") or "")
            if expected_sha and _sha256(path) != expected_sha:
                continue
        except (OSError, AssetError, TypeError, ValueError):
            continue
        return meta
    return None


def _thumb_lock(asset_id: str) -> threading.Lock:
    with _LOCK:
        lock = _THUMB_LOCKS.get(asset_id)
        if lock is None:
            if len(_THUMB_LOCKS) >= 256:
                for key, value in list(_THUMB_LOCKS.items()):
                    if not value.locked():
                        del _THUMB_LOCKS[key]
            lock = threading.Lock()
            _THUMB_LOCKS[asset_id] = lock
        return lock


def _run_thumbnail_ffmpeg(clip: Path, tmp: Path, seek: float) -> None:
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                f"{seek:g}",
                "-i",
                str(clip),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2",
                "-q:v",
                "3",
                "-y",
                str(tmp),
            ],
            timeout=30,
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as error:
        raise AssetError("Không có ffmpeg để tạo thumbnail.") from error
    except subprocess.TimeoutExpired as error:
        raise AssetError("Tạo thumbnail quá lâu.") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr or b""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise AssetError(
            str(detail).strip() or "ffmpeg không tạo được thumbnail."
        ) from error


def ensure_asset_thumbnail(meta: dict) -> Path:
    """Return a cached JPEG poster for an asset, generating it once with ffmpeg."""
    if not isinstance(meta, dict):
        raise AssetError("Asset không hợp lệ.")
    asset_id = str(meta.get("id") or "")
    folder = asset_dir(asset_id)
    try:
        clip = clip_path(meta)
    except AssetError:
        raise
    try:
        info = clip.stat()
    except OSError as error:
        raise AssetError("Không thấy clip asset.") from error
    if not S_ISREG(info.st_mode) or info.st_size <= 0:
        raise AssetError("Không thấy clip asset.")
    digest = str(meta.get("clip_sha256") or "").strip()
    if digest.startswith("sha256:"):
        digest = digest[len("sha256:"):]
    if not digest:
        try:
            digest = _sha256(clip).removeprefix("sha256:")
        except OSError as error:
            raise AssetError("Không đọc được clip asset.") from error
    thumb = folder / f"{THUMB_VERSION}-{digest[:12]}.jpg"
    try:
        if thumb.is_file() and thumb.stat().st_size > 0:
            return thumb
    except OSError:
        pass
    lock = _thumb_lock(asset_id)
    with lock:
        try:
            if thumb.is_file() and thumb.stat().st_size > 0:
                return thumb
        except OSError:
            pass
        tmp = folder / f".{thumb.stem}.{os.getpid()}.{uuid.uuid4().hex}.tmp.jpg"
        last_error: AssetError | None = None
        try:
            with _THUMB_SEMAPHORE:
                for seek in (0.5, 0.0):
                    try:
                        _run_thumbnail_ffmpeg(clip, tmp, seek)
                        break
                    except AssetError as error:
                        last_error = error
                        tmp.unlink(missing_ok=True)
                else:
                    raise AssetError(
                        str(last_error) if last_error else "Không tạo được thumbnail."
                    )
            try:
                if not tmp.is_file() or tmp.stat().st_size <= 0:
                    raise AssetError("ffmpeg không tạo được thumbnail.")
                os.replace(tmp, thumb)
            except OSError as error:
                raise AssetError("Không lưu được thumbnail.") from error
            for old in folder.glob(f"{THUMB_VERSION}-*.jpg"):
                if old != thumb:
                    try:
                        old.unlink()
                    except OSError:
                        pass
        finally:
            tmp.unlink(missing_ok=True)
    return thumb


def _save_asset_from_preview_impl(
    *,
    preview_id: str,
    name: str,
    image_path: Path,
    image_hash: str,
    effect_plan: dict,
    duration: float,
    output_profile: str,
    reuse: bool = True,
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

    if reuse:
        reusable = _find_reusable_asset(
            image_hash=image_hash,
            plan_hash=plan_hash,
            profile=profile,
            frames=frames,
        )
        if reusable is not None:
            return reusable

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


def rename_asset(asset_id: str, name: str) -> dict:
    """Rename one asset, writing metadata atomically under the asset lock."""
    folder = asset_dir(asset_id)
    value = str(name or "").strip()
    if not value:
        raise AssetError("Tên asset không được để trống.")
    if len(value) > ASSET_NAME_MAX:
        raise AssetError(f"Tên asset tối đa {ASSET_NAME_MAX} ký tự.")
    root = assets_root()
    root.mkdir(parents=True, exist_ok=True)
    with _file_lock(root / ".assets.lock"):
        meta = load_asset(asset_id)
        if meta is None:
            raise AssetError("Không thấy asset.")
        meta["name"] = value
        _write_meta(folder, meta)
    return meta


def list_assets(summary: bool = False) -> list[dict]:
    root = assets_root()
    if not root.is_dir():
        return []
    assets: list[dict] = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        meta = load_asset(folder.name)
        if meta is not None:
            assets.append(asset_summary(meta) if summary else meta)
    assets.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
    return assets


def _summary_objects(plan: dict) -> list[dict]:
    """Collect object entries from the places older plans copied them to."""
    raw: list[dict] = []
    for key in ("objects", "targets"):
        value = plan.get(key)
        if isinstance(value, list):
            raw.extend(entry for entry in value if isinstance(entry, dict))
    inventory = plan.get("inventory")
    if isinstance(inventory, dict) and isinstance(inventory.get("objects"), list):
        raw.extend(entry for entry in inventory["objects"] if isinstance(entry, dict))
    for item in plan.get("items") or []:
        if isinstance(item, dict) and isinstance(item.get("objects"), list):
            raw.extend(entry for entry in item["objects"] if isinstance(entry, dict))
    objects: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        identity = str(entry.get("id") or f"object-{index + 1}")
        if identity in seen:
            continue
        seen.add(identity)
        objects.append(entry)
    return objects


def asset_summary(meta: dict) -> dict:
    """Compact asset metadata for the library grid (no embedded effect_plan)."""
    if not isinstance(meta, dict):
        return {}
    plan = meta.get("effect_plan") if isinstance(meta.get("effect_plan"), dict) else {}
    effect_names: list[str] = []
    for item in plan.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("effect") or "").strip()
        if name and name not in effect_names:
            effect_names.append(name)
    objects = _summary_objects(plan)
    manual_objects = sum(
        1
        for entry in objects
        if str(entry.get("source") or "").strip().casefold() == "manual"
    )
    try:
        frames = int(meta.get("asset_frames") or 0)
    except (TypeError, ValueError):
        frames = 0
    try:
        fps = float(meta.get("fps") or 0)
    except (TypeError, ValueError):
        fps = 0.0
    if frames > 0 and fps > 0:
        duration = frames / fps
    else:
        try:
            duration = float(meta.get("loop_duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
    try:
        clip_size = int(meta.get("clip_size") or 0)
    except (TypeError, ValueError):
        clip_size = 0
    return {
        "id": str(meta.get("id") or ""),
        "name": str(meta.get("name") or ""),
        "source_image": str(meta.get("source_image") or ""),
        "image_hash": str(meta.get("image_hash") or ""),
        "plan_hash": str(meta.get("plan_hash") or ""),
        "profile_id": str(meta.get("profile_id") or ""),
        "width": int(meta.get("width") or 0),
        "height": int(meta.get("height") or 0),
        "fps": float(meta.get("fps") or 0),
        "asset_frames": frames,
        "duration_seconds": duration,
        "clip_size": clip_size,
        "created": str(meta.get("created") or ""),
        "effect_names": effect_names,
        "object_count": len(objects),
        "manual_object_count": manual_objects,
    }


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
                    if not S_ISREG(clip.stat().st_mode):
                        # A missing/non-regular clip still occupies a folder;
                        # count it so quota and cleanup can deal with it.
                        pass
                except OSError:
                    pass
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
    entry = unicodedata.normalize("NFC", str(Path(path).expanduser().resolve()))
    with _file_lock(root / ".assets.lock"):
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = []
        if not isinstance(existing, list):
            existing = []
        if any(
            isinstance(item, str)
            and unicodedata.normalize("NFC", item) == entry
            for item in existing
        ):
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


def _registry_entries() -> list[str]:
    target = assets_root() / PROJECTS_FILE
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data if isinstance(item, str) and item.strip()]


def known_project_paths() -> list[Path]:
    """Return project paths recorded by :func:`register_project_path`.

    Existing files and paths whose parent is missing (usually an unmounted
    volume) are returned. A missing file in an existing folder is treated as
    stale registry data and skipped.
    """
    paths: list[Path] = []
    seen: set[str] = set()
    for item in _registry_entries():
        try:
            path = Path(item).expanduser()
        except (TypeError, ValueError, RuntimeError, OSError):
            continue
        if not path.is_file() and path.parent.is_dir():
            continue
        key = unicodedata.normalize("NFC", str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def missing_project_paths() -> list[Path]:
    """Registry entries whose project file no longer exists."""
    paths: list[Path] = []
    seen: set[str] = set()
    for item in _registry_entries():
        try:
            path = Path(item).expanduser()
        except (TypeError, ValueError, RuntimeError, OSError):
            continue
        if path.is_file():
            continue
        key = unicodedata.normalize("NFC", str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def forget_project_paths(
    *,
    paths: list[str] | None = None,
    missing_only: bool = False,
) -> list[str]:
    """Remove stale entries from ``.projects.json``.

    This is explicit user action: removing an entry for an unmounted volume
    would make asset cleanup treat its clips as unreferenced.
    """
    root = assets_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / PROJECTS_FILE
    wanted: set[str] = set()
    for item in paths or []:
        if not str(item or "").strip():
            continue
        try:
            wanted.add(unicodedata.normalize("NFC", str(Path(item).expanduser())))
        except (TypeError, ValueError, RuntimeError, OSError):
            continue
    with _file_lock(root / ".assets.lock"):
        entries = _registry_entries()
        removed: list[str] = []
        kept: list[str] = []
        for item in entries:
            try:
                path = Path(item).expanduser()
                missing = not path.is_file()
            except (TypeError, ValueError, RuntimeError, OSError):
                path = None
                missing = True
            remove = missing_only and missing
            if wanted and path is not None:
                remove = remove or unicodedata.normalize("NFC", str(path)) in wanted
            if remove:
                removed.append(item)
            else:
                kept.append(item)
        if removed:
            temporary = target.with_name(
                f".{PROJECTS_FILE}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(kept, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return removed


def _project_asset_ids(path: Path, mtime_ns: int, size: int) -> tuple[str, ...]:
    """Asset ids referenced by one project, cached by path + mtime + size."""
    key = (str(path), int(mtime_ns), int(size))
    with _PROJECT_REFS_LOCK:
        cached = _PROJECT_REFS_FILES.get(key)
    if cached is not None:
        return cached
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("project JSON is not an object")
    ids: list[str] = []
    for effect in data.get("background_effects") or []:
        if not isinstance(effect, dict):
            continue
        asset_id = str(effect.get("asset_id") or "").strip()
        if asset_id and asset_id not in ids:
            ids.append(asset_id)
    value = tuple(ids)
    with _PROJECT_REFS_LOCK:
        for stale in [
            entry
            for entry in _PROJECT_REFS_FILES
            if entry[0] == key[0] and entry != key
        ]:
            _PROJECT_REFS_FILES.pop(stale, None)
        _PROJECT_REFS_FILES[key] = value
    return value


def referenced_asset_projects() -> dict[str, list[str]] | None:
    """Map ``asset_id -> [project path]`` for every known project file.

    Returns ``None`` when any project file cannot be read, so callers can show
    "không xác định" instead of incorrectly reporting an asset as unused.
    """
    global _PROJECT_REFS_SNAPSHOT
    try:
        known = known_project_paths()
    except Exception:
        return None
    entries: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for path in known:
        try:
            resolved = unicodedata.normalize(
                "NFC", str(path.expanduser().resolve())
            )
            info = path.stat()
        except OSError:
            return None
        if resolved in seen:
            continue
        seen.add(resolved)
        entries.append((resolved, info.st_mtime_ns, info.st_size))
    key = tuple(entries)
    with _PROJECT_REFS_LOCK:
        snapshot = _PROJECT_REFS_SNAPSHOT
        if snapshot is not None and snapshot[0] == key:
            return {asset_id: list(paths) for asset_id, paths in snapshot[1].items()}
    mapping: dict[str, set[str]] = {}
    for resolved, mtime_ns, size in entries:
        try:
            ids = _project_asset_ids(Path(resolved), mtime_ns, size)
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        for asset_id in ids:
            mapping.setdefault(asset_id, set()).add(resolved)
    result = {
        asset_id: sorted(paths)
        for asset_id, paths in sorted(mapping.items())
    }
    with _PROJECT_REFS_LOCK:
        _PROJECT_REFS_SNAPSHOT = (key, result)
    return {asset_id: list(paths) for asset_id, paths in result.items()}


def cleanup_assets(
    *,
    keep_ids: set[str] | None = None,
    max_files: int = ASSET_MAX_FILES,
    max_bytes: int = ASSET_MAX_BYTES,
    min_age_days: float = ASSET_MIN_AGE_DAYS,
    dry_run: bool = False,
) -> dict:
    """Delete the oldest unreferenced assets until the quota is satisfied.

    Assets in ``keep_ids``, anything newer than ``min_age_days`` and anything
    that cannot be inspected safely are never removed. With ``dry_run`` the
    same candidates are reported but nothing is deleted.
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
            if not dry_run:
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
    if int(meta.get("asset_version") or 0) not in {1, ASSET_VERSION}:
        return False, "asset_version không khớp"
    if plan is not None:
        expected_plan_hash = str(meta.get("plan_hash") or "")
        if not expected_plan_hash and isinstance(meta.get("effect_plan"), dict):
            # Assets saved before plan_hash was introduced can still be
            # validated from their embedded plan.
            expected_plan_hash = visual_plan_hash(meta["effect_plan"])
        if expected_plan_hash != visual_plan_hash(plan):
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

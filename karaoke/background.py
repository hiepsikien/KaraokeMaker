"""Two-stage karaoke rendering: background first, then lyrics and audio.

Stage 1 resolves every background image to a static slide or an effect clip and
assembles a full-length silent ``background.mp4`` in the export directory.
Stage 2 overlays lyric frames and muxes the two audio versions.

Background identity intentionally does not include lyrics or ambience. Editing
lyrics therefore reuses the expensive effect render, and toggling ambience does
not invalidate the visual clip.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - macOS/Linux
    msvcrt = None

from karaoke.ambient import MAX_DURATION as MAX_AMBIENT_DURATION
from karaoke.ambient import synthesize as synthesize_ambience
from karaoke.access import AccessError, ensure_allowed
from karaoke.assets import (
    asset_fingerprint,
    clip_path as asset_clip_path,
    load_asset,
    validate_asset,
)
from karaoke.cache import cache_root, prune
from karaoke.living import (
    ENCODER_VERSION,
    MOTION_CACHE_VERSION,
    motion_cache_path,
    pin_cache_file,
    plan_has_motion,
    render_motion_loop,
    visual_plan_hash,
)
from karaoke.media import (
    COVER_RENDER_VERSION,
    IMAGE_PREP_VERSION,
    MediaError,
    make_cover,
    make_outro,
    probe_duration,
)
from karaoke.profiles import RenderProfile, get_profile
from karaoke.render import TEXT_FPS, _ambient_kind, _mux, _overlay_text, _run
from karaoke.segment import (
    SEGMENT_ENCODER_VERSION,
    encode_still,
    encode_video,
)

BACKGROUND_SCHEMA_VERSION = 1
BACKGROUND_BUILDER_VERSION = 3
BACKGROUND_CRF = 18
OVERLAY_RENDER_VERSION = 1
BACKGROUND_PRESET = "veryfast"
BACKGROUND_DIR = Path(".karaoke-media") / "backgrounds"
PICTURE_DIR = Path(".karaoke-media") / "pictures"
PICTURE_BUILDER_VERSION = 2
SEGMENT_CACHE_ROOT = cache_root("segments")
SEGMENT_CACHE_LIMIT_FILES = 200
SEGMENT_CACHE_LIMIT_BYTES = 4_000_000_000

_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_HASH_LOCK = threading.Lock()
_PROBE_CACHE: dict[tuple[str, int, int], dict] = {}
_VALIDATE_CACHE: dict[tuple, tuple[bool, str]] = {}
_PROBE_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()


@contextmanager
def _file_lock(path: Path):
    """Best-effort cross-process lock for one output directory."""
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


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _file_digest(path: Path) -> str:
    """SHA-256 of file contents, cached by path + size + mtime."""
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _HASH_LOCK:
        cached = _HASH_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _HASH_LOCK:
        _HASH_CACHE[key] = value
    return value


def _image_key(path: Path) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(path)


def _hex_from_hash(value: str) -> str:
    text = str(value or "").strip()
    return text.split(":", 1)[1] if text.startswith("sha256:") else text


def _hash_with_prefix(hex_value: str) -> str:
    return f"sha256:{hex_value}"


def _background_path(output_dir: Path, hash_value: str) -> Path:
    hex_value = _hex_from_hash(hash_value)
    return Path(output_dir) / BACKGROUND_DIR / f"background-{hex_value}.mp4"


def profile_fingerprint(profile: RenderProfile) -> str:
    """Fingerprint the visual background profile, including cover typography."""
    payload = {
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "fit_mode": profile.fit_mode,
        "cover_title_size": profile.cover_title_size,
        "cover_artist_size": profile.cover_artist_size,
        "cover_line_size": profile.cover_line_size,
        "cover_max_width": profile.cover_max_width,
        "cover_render_version": COVER_RENDER_VERSION,
    }
    return _sha256_text(_canonical(payload))


def _frames(
    cover_duration: float,
    image_duration: float,
    song_length: float,
    fps: int,
    outro_duration: float = 0.0,
) -> tuple[int, int, int, int, int]:
    cover_frames = 0 if float(cover_duration) < 0.05 else max(0, int(round(float(cover_duration) * fps)))
    image_frames = max(1, int(round(float(image_duration) * fps)))
    song_frames = max(1, int(math.ceil(float(song_length) * fps)))
    outro_frames = max(0, int(round(max(0.0, float(outro_duration)) * fps)))
    return cover_frames, image_frames, song_frames, outro_frames, cover_frames + song_frames + outro_frames


def _effect_map(background_effects: list[dict] | None) -> dict[str, dict]:
    effects: dict[str, dict] = {}
    for effect in background_effects or []:
        if not isinstance(effect, dict):
            continue
        raw = str(effect.get("image_path") or "").strip()
        if raw:
            effects[_image_key(Path(raw))] = effect
    return effects


def _effect_plan(effect: dict | None) -> dict | None:
    if not isinstance(effect, dict):
        return None
    plan = effect.get("effect_plan")
    return plan if isinstance(plan, dict) else None


def _parse_created(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _asset_is_newer_than_effect(effect: dict | None, meta: dict) -> bool:
    """True when the selected asset was created after the stored effect plan."""
    if not isinstance(effect, dict) or not isinstance(meta, dict):
        return False
    asset_created = _parse_created(meta.get("created"))
    if asset_created is None:
        return False
    effect_created = _parse_created(effect.get("created"))
    if effect_created is None:
        return True
    return asset_created > effect_created


def _cover_identity(
    cover_photo: Path | None,
    title: str,
    artist: str,
    cover_line: str,
    cover_frames: int,
    profile: RenderProfile,
    style: dict | None = None,
) -> str:
    if cover_frames <= 0 or cover_photo is None:
        return "no-cover"
    from karaoke.presentation import cover_style

    payload = {
        "image": _file_digest(cover_photo),
        "title": " ".join(str(title or "").split()),
        "artist": " ".join(str(artist or "").split()),
        "cover_line": " ".join(str(cover_line or "").split()),
        "cover_frames": cover_frames,
        "profile": profile_fingerprint(profile),
        "style": cover_style(style or {}),
    }
    return _sha256_text(_canonical(payload))


def _motion_identity(
    plan: dict,
    image_hash: str,
    image_frames: int,
    profile: RenderProfile,
) -> str:
    payload = {
        "motion_renderer": MOTION_CACHE_VERSION,
        "encoder": ENCODER_VERSION,
        "image_hash": image_hash,
        "plan_hash": visual_plan_hash(plan),
        "image_frames": image_frames,
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "fit_mode": profile.fit_mode,
    }
    return _sha256_text(_canonical(payload))


def _static_identity(image_hash: str, profile: RenderProfile) -> str:
    payload = {
        "image_prep_version": IMAGE_PREP_VERSION,
        "image_hash": image_hash,
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "fit_mode": profile.fit_mode,
    }
    return _sha256_text(_canonical(payload))


@dataclass(frozen=True)
class BackgroundRef:
    """Cheap reuse hint. Identity is always recomputed from the project inputs."""

    hash: str
    path: Path
    source_output_dir: Path | None = None
    profile_fingerprint: str = ""
    total_frames: int = 0
    cover_frames: int = 0
    song_frames: int = 0
    image_frames: int = 0
    outro_frames: int = 0
    overlay_hash: str = ""
    overlay_degraded: bool = False
    width: int = 0
    height: int = 0
    fps: int = 0
    degraded: bool = False

    @classmethod
    def from_project(cls, data: dict | None, output_dir: Path) -> "BackgroundRef | None":
        if not isinstance(data, dict):
            return None
        hash_value = str(data.get("hash") or "").strip()
        if not hash_value:
            return None
        source = str(data.get("source_output_dir") or "").strip()
        return cls(
            hash=hash_value,
            path=_background_path(output_dir, hash_value),
            source_output_dir=Path(source).expanduser() if source else None,
            profile_fingerprint=str(data.get("profile_fingerprint") or ""),
            total_frames=int(data.get("total_frames") or 0),
            cover_frames=int(data.get("cover_frames") or 0),
            song_frames=int(data.get("song_frames") or 0),
            image_frames=int(data.get("image_frames") or 0),
            outro_frames=int(data.get("outro_frames") or 0),
            overlay_hash=str(data.get("overlay_hash") or ""),
            overlay_degraded=bool(data.get("overlay_degraded")),
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            fps=int(data.get("fps") or 0),
            degraded=bool(data.get("degraded")),
        )


@dataclass
class BackgroundBuild:
    path: Path
    hash: str
    profile_id: str
    profile_fingerprint: str
    width: int
    height: int
    fps: int
    total_frames: int
    cover_frames: int
    song_frames: int
    image_frames: int
    outro_frames: int = 0
    overlay_hash: str = ""
    overlay_degraded: bool = False
    segments: list[dict] = field(default_factory=list)
    degraded: bool = False
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_project(self) -> dict:
        source_dir = str(self.path.parent.parent.parent)
        segments = [
            {key: value for key, value in segment.items() if key != "asset_reason"}
            for segment in self.segments
        ]
        return {
            "schema_version": BACKGROUND_SCHEMA_VERSION,
            "builder_version": BACKGROUND_BUILDER_VERSION,
            "hash": self.hash,
            "profile_id": self.profile_id,
            "profile_fingerprint": self.profile_fingerprint,
            "source_output_dir": source_dir,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "total_frames": self.total_frames,
            "cover_frames": self.cover_frames,
            "song_frames": self.song_frames,
            "image_frames": self.image_frames,
            "outro_frames": self.outro_frames,
            "overlay_hash": self.overlay_hash,
            "overlay_degraded": self.overlay_degraded,
            "degraded": self.degraded,
            "created": self.created,
            "segments": segments,
        }


@dataclass
class BackgroundPlan:
    profile: RenderProfile
    cover_photo: Path | None
    background_images: list[Path]
    effects: dict[str, dict]
    cover_frames: int
    image_frames: int
    song_frames: int
    outro_frames: int
    total_frames: int
    unique: dict[str, dict]
    order: list[str]
    cover_identity: str
    profile_fingerprint: str
    base_hash: str
    overlay_hash: str
    presentation: dict
    overlay_effects: list[dict]
    identity_hash: str
    target: Path


@dataclass(frozen=True)
class RenderResult:
    vocal: Path
    instrumental: Path
    background: BackgroundBuild

    def __iter__(self):
        # Keep old tuple unpacking working for compatibility.
        yield self.vocal
        yield self.instrumental


def _fps_value(value) -> float:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
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


def _probe_key(path: Path) -> tuple[str, int, int]:
    stat = Path(path).stat()
    return (str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns)


def _probe_video(path: Path) -> dict:
    key = _probe_key(path)
    with _PROBE_LOCK:
        cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,profile,level,width,height,r_frame_rate,avg_frame_rate,nb_frames,pix_fmt,duration:format=duration",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        raise MediaError(process.stderr.strip() or "ffprobe không đọc được video.")
    try:
        meta = json.loads(process.stdout or "{}")
    except json.JSONDecodeError as error:
        raise MediaError("ffprobe trả dữ liệu không hợp lệ.") from error
    with _PROBE_LOCK:
        _PROBE_CACHE[key] = meta
    return meta


def _validate_background(
    path: Path,
    *,
    width: int,
    height: int,
    fps: int,
    total_frames: int,
) -> tuple[bool, str]:
    if not path.is_file() or path.stat().st_size <= 0:
        return False, "file không tồn tại"
    stat = path.stat()
    key = (
        str(path.resolve()),
        stat.st_size,
        stat.st_mtime_ns,
        int(width),
        int(height),
        int(fps),
        int(total_frames),
    )
    with _PROBE_LOCK:
        cached = _VALIDATE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        meta = _probe_video(path)
    except MediaError as error:
        result = (False, str(error))
        with _PROBE_LOCK:
            _VALIDATE_CACHE[key] = result
        return result
    streams = meta.get("streams") or []
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not videos:
        return False, "không có video stream"
    if audios:
        return False, "background không được có audio"
    video = videos[0]
    if int(video.get("width") or 0) != width or int(video.get("height") or 0) != height:
        return False, "width/height không khớp"
    actual_fps = _fps_value(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    if abs(actual_fps - fps) > 0.05:
        return False, "fps không khớp"
    if str(video.get("pix_fmt") or "") != "yuv420p":
        return False, "pix_fmt không phải yuv420p"
    raw_frames = str(video.get("nb_frames") or "").strip()
    if raw_frames.isdigit() and int(raw_frames) > 0:
        if int(raw_frames) != total_frames:
            return False, "nb_frames không khớp"
    else:
        duration = video.get("duration") or (meta.get("format") or {}).get("duration")
        try:
            seconds = float(duration)
        except (TypeError, ValueError):
            return False, "không đọc được duration"
        if abs(seconds - total_frames / fps) > 1.5 / fps:
            return False, "duration không khớp"
    result = (True, "")
    with _PROBE_LOCK:
        _VALIDATE_CACHE[key] = result
    return result


def _segment_cache_path(kind: str, identity: str, frames: int) -> Path:
    value = _hex_from_hash(identity)[:32]
    return SEGMENT_CACHE_ROOT / f"{kind}-{value}-{int(frames)}-v{SEGMENT_ENCODER_VERSION}.mp4"


def _cached_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _segment_compatible(
    path: Path,
    *,
    frames: int,
    width: int,
    height: int,
    fps: int,
    reference: tuple[str, int] | None = None,
) -> bool:
    try:
        meta = _probe_video(path)
    except MediaError:
        return False
    streams = meta.get("streams") or []
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not videos or audios:
        return False
    video = videos[0]
    if str(video.get("codec_name") or "") != "h264":
        return False
    if str(video.get("profile") or "") != "High":
        # Our encoder emits High profile. Normalise anything else rather than
        # risking concat-copy with mismatched SPS/PPS.
        return False
    if reference is not None:
        signature = (str(video.get("profile") or ""), int(video.get("level") or 0))
        if signature != reference:
            return False
    if int(video.get("width") or 0) != width or int(video.get("height") or 0) != height:
        return False
    if str(video.get("pix_fmt") or "") != "yuv420p":
        return False
    actual_fps = _fps_value(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    if abs(actual_fps - fps) > 0.05:
        return False
    raw_frames = str(video.get("nb_frames") or "").strip()
    if raw_frames.isdigit() and int(raw_frames) > 0:
        return int(raw_frames) == frames
    duration = video.get("duration") or (meta.get("format") or {}).get("duration")
    try:
        return abs(float(duration) - frames / fps) <= 1.5 / fps
    except (TypeError, ValueError):
        return False


def _static_segment(
    image: Path,
    identity: str,
    *,
    image_frames: int,
    profile: RenderProfile,
    log,
) -> tuple[Path, bool]:
    destination = _segment_cache_path("static", identity, image_frames)
    if _cached_file(destination):
        pin_cache_file(destination)
        return destination, True
    encode_still(
        image,
        destination,
        frames=image_frames,
        width=profile.width,
        height=profile.height,
        fps=profile.fps,
        log=log,
    )
    pin_cache_file(destination)
    return destination, False


def _cover_segment(
    cover_photo: Path,
    identity: str,
    *,
    title: str,
    artist: str,
    cover_line: str,
    cover_frames: int,
    profile: RenderProfile,
    work: Path,
    log,
    style: dict | None = None,
) -> tuple[Path, bool]:
    destination = _segment_cache_path("cover", identity, cover_frames)
    if _cached_file(destination):
        pin_cache_file(destination)
        return destination, True
    cover_card = work / "cover.jpg"
    make_cover(
        cover_photo,
        title,
        artist,
        cover_line,
        cover_card,
        profile=profile,
        style=style,
    )
    encode_still(
        cover_card,
        destination,
        frames=cover_frames,
        width=profile.width,
        height=profile.height,
        fps=profile.fps,
        log=log,
    )
    pin_cache_file(destination)
    return destination, False


def _outro_segment(
    photo: Path,
    *,
    title: str,
    artist: str,
    subtext: str,
    handle: str,
    frames: int,
    profile: RenderProfile,
    style: dict,
    work: Path,
    log,
) -> tuple[Path, bool]:
    identity = _sha256_text(
        _canonical(
            {
                "image": _file_digest(photo),
                "title": title,
                "artist": artist,
                "subtext": subtext,
                "handle": handle,
                "style": style,
                "frames": frames,
                "profile": profile_fingerprint(profile),
            }
        )
    )
    destination = _segment_cache_path("outro", identity, frames)
    if _cached_file(destination):
        pin_cache_file(destination)
        return destination, True
    card = work / "outro.jpg"
    make_outro(
        photo,
        title,
        artist,
        subtext,
        handle,
        card,
        profile=profile,
        style=style,
    )
    encode_still(
        card,
        destination,
        frames=frames,
        width=profile.width,
        height=profile.height,
        fps=profile.fps,
        log=log,
        fade_out=float(style.get("fade") or 0.0),
    )
    pin_cache_file(destination)
    return destination, False


def _asset_segment(
    meta: dict,
    *,
    image_frames: int,
    profile: RenderProfile,
    log,
) -> tuple[Path, bool]:
    source = asset_clip_path(meta)
    if not source.is_file():
        raise MediaError("Không thấy clip asset.")
    asset_frames = int(meta.get("asset_frames") or 0)
    if asset_frames == image_frames:
        return source, True
    destination = _segment_cache_path("asset", asset_fingerprint(meta), image_frames)
    if _cached_file(destination):
        pin_cache_file(destination)
        return destination, True
    encode_video(
        source,
        destination,
        frames=image_frames,
        width=profile.width,
        height=profile.height,
        fps=profile.fps,
        log=log,
    )
    pin_cache_file(destination)
    return destination, False


def _resolve_segment(
    segment: dict,
    plan: dict | None,
    *,
    image_frames: int,
    profile: RenderProfile,
    work: Path,
    progress,
    log,
) -> tuple[Path, str, bool, bool]:
    """Return (segment path, kind, degraded, cache_hit)."""
    image = Path(segment["image_path"])
    index = int(segment["index"])
    asset_id = str(segment.get("asset_id") or "").strip()
    if asset_id:
        meta = load_asset(asset_id)
        if meta is not None:
            try:
                path, hit = _asset_segment(
                    meta,
                    image_frames=image_frames,
                    profile=profile,
                    log=log,
                )
                segment["asset_reason"] = ""
                return path, "asset", False, hit
            except Exception as error:
                log(f"Không dùng được asset {asset_id}: {error}")
                segment["asset_reason"] = str(error)
        segment["asset_id"] = None
        log(f"Không dùng được asset {asset_id}: {segment.get('asset_reason') or 'không hợp lệ'}")
    if plan is not None and plan_has_motion(plan):
        image_hash = str(segment.get("image_hash") or "")
        try:
            expected = motion_cache_path(
                image_hash=image_hash,
                plan=plan,
                duration=image_frames / max(1, profile.fps),
                width=profile.width,
                height=profile.height,
                fps=profile.fps,
                fit_mode=profile.fit_mode,
                strip_rect=(0, 0, 0, 0),
            )
            if _cached_file(expected):
                pin_cache_file(expected)
                return expected, "motion", False, True
        except Exception:
            pass
        destination = work / f"clip-{index:02d}.mp4"

        def image_progress(done: int, total: int) -> None:
            if progress is not None:
                progress(index, done, total)

        try:
            clip = render_motion_loop(
                image,
                plan,
                destination,
                duration=image_frames / max(1, profile.fps),
                width=profile.width,
                height=profile.height,
                fps=profile.fps,
                fit_mode=profile.fit_mode,
                # Background effects render before the lyric overlay. A zero
                # strip explicitly disables the legacy bottom mask.
                strip_rect=(0, 0, 0, 0),
                log=log,
                progress=image_progress,
                pin=True,
            )
        except Exception as error:  # keep export usable if one effect fails
            log(f"Không tạo được hiệu ứng cho {image.name}: {error}")
            clip = None
        if clip is not None and Path(clip).is_file():
            pin_cache_file(Path(clip))
            return Path(clip), "motion", False, False
        log(f"Hiệu ứng cho {image.name} không resolve được; dùng ảnh tĩnh.")
    path, hit = _static_segment(
        image,
        _static_identity(str(segment.get("image_hash") or ""), profile),
        image_frames=image_frames,
        profile=profile,
        log=log,
    )
    degraded = plan is not None and plan_has_motion(plan)
    return path, "static", degraded, hit


def _assemble_background_filter(
    slots: list[tuple[Path, bool, int]],
    *,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    total_frames: int,
    log,
) -> None:
    command = ["ffmpeg", "-y"]
    for media, is_video, _frames_count in slots:
        if is_video:
            command += ["-i", str(media)]
        else:
            command += ["-loop", "1", "-framerate", str(fps), "-i", str(media)]
    filters: list[str] = []
    for index, (_media, _is_video, frames) in enumerate(slots):
        filters.append(
            f"[{index}:v]fps={fps},scale={width}:{height}:in_range=auto:out_range=tv,"
            f"setsar=1,format=yuv420p,settb=AVTB,trim=start_frame=0:end_frame={frames},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )
    filters.append(
        "".join(f"[v{index}]" for index in range(len(slots)))
        + f"concat=n={len(slots)}:v=1:a=0[v]"
    )
    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-frames:v",
        str(total_frames),
        "-c:v",
        "libx264",
        "-preset",
        BACKGROUND_PRESET,
        "-crf",
        str(BACKGROUND_CRF),
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-an",
        str(destination),
    ]
    _run(command, log)


def _assemble_background(
    slots: list[tuple[Path, int]],
    *,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    total_frames: int,
    log,
) -> None:
    """Concat already-encoded segments without re-encoding the whole song."""
    list_path = destination.with_name(f".{destination.name}.concat.txt")
    lines = []
    for path, _frames in slots:
        value = (
            str(Path(path).resolve())
            .replace("\\", "/")
            .replace("'", "'\\''")
        )
        lines.append(f"file '{value}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-an",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    try:
        _run(command, log)
    except Exception as error:
        log(f"Concat copy thất bại ({error}); fallback encode lại toàn bài.")
        _assemble_background_filter(
            [(path, True, frames) for path, frames in slots],
            destination=destination,
            width=width,
            height=height,
            fps=fps,
            total_frames=total_frames,
            log=log,
        )
    finally:
        list_path.unlink(missing_ok=True)


def _plan_background(
    prepared: dict,
    *,
    output_dir: Path,
    profile: RenderProfile,
) -> BackgroundPlan:
    """Resolve inputs, clip identities and the expected background path."""
    cover_text = str(prepared.get("cover_image") or "").strip()
    cover_path = Path(cover_text).expanduser() if cover_text else None
    cover_photo = cover_path if cover_path is not None and cover_path.is_file() else None
    background_images = [
        Path(str(item)).expanduser()
        for item in (prepared.get("background_images") or [])
        if str(item).strip() and Path(str(item)).expanduser().is_file()
    ]
    if cover_photo is None and background_images:
        # Same fallback as pipeline._images_for_render: the first background
        # doubles as the cover when no cover file is set/missing.
        cover_photo = background_images[0]
    if not background_images:
        if cover_photo is None:
            raise MediaError("Chưa có ảnh nền để dựng background.")
        background_images = [cover_photo]

    effects = _effect_map(prepared.get("background_effects") or [])
    from karaoke.presentation import normalize_overlay_effects, normalize_presentation

    presentation = normalize_presentation(prepared.get("presentation"))
    overlay_effects = normalize_overlay_effects(prepared.get("overlay_effects"))
    outro = presentation.get("outro") or {}
    outro_duration = float(outro.get("duration") or 0) if outro.get("enabled") else 0.0
    original = Path(str(prepared.get("original_audio") or "")).expanduser()
    instrumental = Path(str(prepared.get("instrumental_audio") or "")).expanduser()
    if not original.is_file():
        raise MediaError(f"Không thấy audio gốc: {original}")
    if not instrumental.is_file():
        raise MediaError(f"Không thấy file instrumental: {instrumental}")

    song_length = max(probe_duration(original), probe_duration(instrumental))
    cover_frames, image_frames, song_frames, outro_frames, total_frames = _frames(
        float(prepared.get("cover_duration") or 0),
        float(prepared.get("image_duration") or 8),
        song_length,
        profile.fps,
        outro_duration,
    )

    unique: dict[str, dict] = {}
    order: list[str] = []
    for image in background_images:
        key = _image_key(image)
        order.append(key)
        if key in unique:
            continue
        image_hash = _file_digest(image)
        image_hash_prefixed = _hash_with_prefix(image_hash)
        effect = effects.get(key)
        plan = _effect_plan(effect)
        asset_id = str(effect.get("asset_id") or "").strip() if isinstance(effect, dict) else ""
        kind = "static"
        identity = _static_identity(image_hash, profile)
        asset_reason = ""
        if asset_id:
            meta = load_asset(asset_id)
            if (
                meta is not None
                and _asset_is_newer_than_effect(effect, meta)
                and isinstance(meta.get("effect_plan"), dict)
            ):
                # The project still carries the old plan but the user selected
                # an asset created after it. Use the asset's plan as the source
                # of truth. If the asset clip itself is stale (e.g. old
                # renderer version), the inline render still gets the new plan.
                plan = meta["effect_plan"]
            valid, reason = validate_asset(
                meta,
                image_hash=image_hash_prefixed,
                image_frames=image_frames,
                profile=profile,
                plan=plan,
            )
            if valid and meta is not None:
                identity = asset_fingerprint(meta)
                kind = "asset"
            else:
                asset_reason = reason
                asset_id = ""
        if kind != "asset" and plan is not None and plan_has_motion(plan):
            identity = _motion_identity(plan, image_hash, image_frames, profile)
            kind = "motion"
        unique[key] = {
            "index": len(unique),
            "image_path": str(image),
            "image_hash": image_hash_prefixed,
            "kind": kind,
            "clip_identity": identity,
            "asset_id": asset_id or None,
            "asset_reason": asset_reason,
            "image_frames": image_frames,
            "degraded": False,
        }

    cover_id = _cover_identity(
        cover_photo,
        str(prepared.get("title") or ""),
        str(prepared.get("artist") or ""),
        str(prepared.get("cover_line") or ""),
        cover_frames,
        profile,
        presentation.get("cover") or {},
    )
    profile_id = profile_fingerprint(profile)
    outro_image_text = str(outro.get("image") or "").strip() if outro_frames > 0 else ""
    outro_photo = Path(outro_image_text).expanduser() if outro_image_text else cover_photo
    outro_digest = (
        _file_digest(outro_photo)
        if outro_frames > 0 and outro_photo is not None and outro_photo.is_file()
        else ""
    )
    base_payload = {
        "schema_version": BACKGROUND_SCHEMA_VERSION,
        "builder_version": BACKGROUND_BUILDER_VERSION,
        "crf": BACKGROUND_CRF,
        "preset": BACKGROUND_PRESET,
        "segment_encoder_version": SEGMENT_ENCODER_VERSION,
        "order": [unique[key]["clip_identity"] for key in order],
        "cover": cover_id,
        "outro": (outro if outro_frames > 0 else {}),
        "outro_image": outro_digest,
        "profile": profile_id,
        "total_frames": total_frames,
        "cover_frames": cover_frames,
        "song_frames": song_frames,
        "image_frames": image_frames,
        "outro_frames": outro_frames,
    }
    base_hex = _sha256_text(_canonical(base_payload))
    try:
        from karaoke.overlays import OVERLAY_ENCODER_VERSION
        from karaoke.particles import PARTICLE_VERSION
    except Exception:
        OVERLAY_ENCODER_VERSION = 1
        PARTICLE_VERSION = 1
    overlay_payload = {
        "base": base_hex,
        "overlay_effects": [spec for spec in overlay_effects if spec.get("enabled")],
        "overlay_version": OVERLAY_RENDER_VERSION,
        "overlay_encoder": OVERLAY_ENCODER_VERSION,
        "particle_version": PARTICLE_VERSION,
    }
    overlay_hex = _sha256_text(_canonical(overlay_payload))
    identity_hex = _sha256_text(_canonical({"base": base_hex, "overlay": overlay_hex}))
    return BackgroundPlan(
        profile=profile,
        cover_photo=cover_photo,
        background_images=background_images,
        effects=effects,
        cover_frames=cover_frames,
        image_frames=image_frames,
        song_frames=song_frames,
        outro_frames=outro_frames,
        total_frames=total_frames,
        unique=unique,
        order=order,
        cover_identity=cover_id,
        profile_fingerprint=profile_id,
        base_hash=base_hex,
        overlay_hash=overlay_hex,
        presentation=presentation,
        overlay_effects=overlay_effects,
        identity_hash=_hash_with_prefix(identity_hex),
        target=_background_path(output_dir, identity_hex),
    )


def _build_background_impl(
    prepared: dict,
    *,
    output_dir: Path,
    profile: RenderProfile | None = None,
    background: BackgroundRef | None = None,
    force: bool = False,
    progress=None,
    log,
) -> BackgroundBuild:
    """Build (or reuse) the full-length silent background video."""
    profile = profile or get_profile(prepared.get("output_profile"))
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    plan = _plan_background(prepared, output_dir=output_dir, profile=profile)
    cover_photo = plan.cover_photo
    background_images = plan.background_images
    effects = plan.effects
    cover_frames = plan.cover_frames
    image_frames = plan.image_frames
    song_frames = plan.song_frames
    outro_frames = plan.outro_frames
    total_frames = plan.total_frames
    presentation = plan.presentation
    overlay_effects = plan.overlay_effects
    base_hash = plan.base_hash
    overlay_hash = plan.overlay_hash
    unique = plan.unique
    order = plan.order
    identity_hash = plan.identity_hash
    identity_hex = _hex_from_hash(identity_hash)
    target = plan.target
    profile_id = plan.profile_fingerprint
    target.parent.mkdir(parents=True, exist_ok=True)

    background_path: Path | None = None
    degraded = False
    if not force:
        if background is not None and background.hash == identity_hash and background.degraded:
            log("Nền cũ đang ở trạng thái degraded; dựng lại.")
        elif _validate_background(
            target,
            width=profile.width,
            height=profile.height,
            fps=profile.fps,
            total_frames=total_frames,
        )[0]:
            background_path = target
            log("Dùng lại background đã có.")
        elif background is not None and background.source_output_dir is not None:
            candidate = None
            try:
                ensure_allowed(background.source_output_dir)
                candidate = _background_path(background.source_output_dir, identity_hex)
            except AccessError:
                log("Bỏ qua background cũ: thư mục nguồn nằm ngoài vùng được phép.")
            if candidate is not None and candidate.resolve() == target.resolve():
                candidate = None
            valid, reason = (
                _validate_background(
                    candidate,
                    width=profile.width,
                    height=profile.height,
                    fps=profile.fps,
                    total_frames=total_frames,
                )
                if candidate is not None
                else (False, "cùng thư mục xuất")
            )
            if candidate is not None and valid:
                temporary = target.parent / f".{identity_hex}.{os.getpid()}.{uuid.uuid4().hex}.copy.tmp.mp4"
                try:
                    shutil.copy2(candidate, temporary)
                    os.replace(temporary, target)
                    background_path = target
                    log("Đã copy background sang thư mục xuất mới.")
                finally:
                    temporary.unlink(missing_ok=True)
            elif candidate is not None and candidate.exists():
                log(f"Không copy được background cũ: {reason}. Sẽ dựng lại.")

    if background_path is None:
        rendered: dict[str, tuple[Path, int]] = {}
        work = Path(tempfile.mkdtemp(prefix=".karaoke-bg-", dir=str(output_dir)))
        cache_hits = 0
        clips_started = time.perf_counter()
        progress_lock = threading.Lock()
        progress_last = {"value": 0.0}

        def segment_progress(index: int, done: int, total: int) -> None:
            if progress is None:
                return
            try:
                fraction = (index + (done / total if total else 1.0)) / max(1, len(unique))
                fraction = max(0.0, min(0.85, fraction * 0.85))
                with progress_lock:
                    if fraction <= progress_last["value"]:
                        return
                    progress_last["value"] = fraction
                progress(fraction, 1.0)
            except Exception:
                pass

        def render_one(segment: dict, segment_plan: dict | None):
            key = _image_key(Path(segment["image_path"]))
            path, kind, is_degraded, hit = _resolve_segment(
                segment,
                segment_plan,
                image_frames=image_frames,
                profile=profile,
                work=work,
                progress=segment_progress,
                log=log,
            )
            return key, path, kind, is_degraded, hit

        try:
            cover_path: Path | None = None
            cover_hit = False
            if cover_frames > 0 and cover_photo is not None:
                cover_path, cover_hit = _cover_segment(
                    cover_photo,
                    plan.cover_identity,
                    title=str(prepared.get("title") or ""),
                    artist=str(prepared.get("artist") or ""),
                    cover_line=str(prepared.get("cover_line") or ""),
                    cover_frames=cover_frames,
                    profile=profile,
                    work=work,
                    log=log,
                    style=presentation.get("cover") or {},
                )
                cache_hits += int(cover_hit)

            jobs = []
            for segment in unique.values():
                segment_plan = _effect_plan(effects.get(_image_key(Path(segment["image_path"]))))
                jobs.append((segment, segment_plan))
            try:
                requested_workers = int(os.environ.get("KARAOKE_CLIP_WORKERS", "2") or 2)
            except (TypeError, ValueError):
                requested_workers = 2
                log("KARAOKE_CLIP_WORKERS không hợp lệ; dùng 2 worker.")
            workers = max(1, min(requested_workers, len(jobs) or 1))
            if len(jobs) <= 1 or workers == 1:
                results = [render_one(segment, segment_plan) for segment, segment_plan in jobs]
            else:
                results = []
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(render_one, segment, segment_plan): segment
                        for segment, segment_plan in jobs
                    }
                    for future in as_completed(futures):
                        results.append(future.result())
            for key, path, kind, is_degraded, hit in results:
                cache_hits += int(hit)
                segment = next(
                    item for item in unique.values() if _image_key(Path(item["image_path"])) == key
                )
                segment["kind"] = "static-fallback" if is_degraded else kind
                segment["degraded"] = is_degraded
                degraded = degraded or is_degraded
                rendered[key] = (path, image_frames)

            clip_seconds = time.perf_counter() - clips_started

            slots: list[tuple[Path, int]] = []
            if cover_path is not None and cover_frames > 0:
                slots.append((cover_path, cover_frames))
            reference: tuple[str, int] | None = None
            for key in order:
                candidate = rendered[key][0]
                try:
                    meta = _probe_video(candidate)
                    video = next(
                        (
                            stream
                            for stream in (meta.get("streams") or [])
                            if stream.get("codec_type") == "video"
                        ),
                        None,
                    )
                    if video is not None and str(video.get("profile") or "") == "High":
                        reference = (
                            str(video.get("profile") or ""),
                            int(video.get("level") or 0),
                        )
                        break
                except MediaError:
                    continue
            remaining = song_frames
            index = 0
            while remaining > 0:
                key = order[index % len(order)]
                media, segment_frames = rendered[key]
                frames = min(image_frames, remaining)
                if not _segment_compatible(
                    media,
                    frames=segment_frames,
                    width=profile.width,
                    height=profile.height,
                    fps=profile.fps,
                    reference=reference,
                ):
                    normalized = work / f"norm-{index:03d}.mp4"
                    encode_video(
                        media,
                        normalized,
                        frames=segment_frames,
                        width=profile.width,
                        height=profile.height,
                        fps=profile.fps,
                        log=log,
                    )
                    media = normalized
                if frames != segment_frames:
                    tail = work / f"tail-{index:03d}.mp4"
                    encode_video(
                        media,
                        tail,
                        frames=frames,
                        width=profile.width,
                        height=profile.height,
                        fps=profile.fps,
                        log=log,
                    )
                    media = tail
                    segment_frames = frames
                slots.append((media, segment_frames))
                remaining -= frames
                index += 1

            if outro_frames > 0:
                outro = presentation.get("outro") or {}
                outro_text = str(outro.get("image") or "").strip()
                outro_photo = Path(outro_text).expanduser() if outro_text else None
                if outro_photo is None or not outro_photo.is_file():
                    outro_photo = cover_photo
                if outro_photo is not None:
                    outro_path, outro_hit = _outro_segment(
                        outro_photo,
                        title=str(prepared.get("title") or ""),
                        artist=str(prepared.get("artist") or ""),
                        subtext=str(outro.get("subtext") or ""),
                        handle=str(outro.get("handle") or ""),
                        frames=outro_frames,
                        profile=profile,
                        style=outro,
                        work=work,
                        log=log,
                    )
                    cache_hits += int(outro_hit)
                    slots.append((outro_path, outro_frames))

            temporary = target.parent / f".{identity_hex}.{os.getpid()}.{uuid.uuid4().hex}.tmp.mp4"
            log(f"Đang ghép background {total_frames} frame cho {len(unique)} ảnh.")
            if progress is not None:
                try:
                    progress(0.85, 1.0)
                except Exception:
                    pass
            assemble_started = time.perf_counter()
            try:
                _assemble_background(
                    slots,
                    destination=temporary,
                    width=profile.width,
                    height=profile.height,
                    fps=profile.fps,
                    total_frames=total_frames,
                    log=log,
                )
                assemble_seconds = time.perf_counter() - assemble_started
                valid, reason = _validate_background(
                    temporary,
                    width=profile.width,
                    height=profile.height,
                    fps=profile.fps,
                    total_frames=total_frames,
                )
                if not valid:
                    raise MediaError(f"Background dựng xong nhưng không hợp lệ: {reason}")
                final_temp = temporary
                overlay_degraded = False
                if overlay_effects:
                    overlay_temp = target.parent / f".{identity_hex}.{os.getpid()}.{uuid.uuid4().hex}.overlay.tmp.mp4"
                    try:
                        from karaoke.overlays import apply_overlays

                        def overlay_progress(done, total) -> None:
                            if progress is None:
                                return
                            try:
                                fraction = 0.85 + 0.15 * float(done) / max(1.0, float(total))
                                progress(min(1.0, fraction), 1.0)
                            except Exception:
                                pass

                        overlay_path, _overlay_hash, overlay_degraded = apply_overlays(
                            temporary,
                            overlay_effects,
                            profile,
                            profile.strip_rect,
                            overlay_temp,
                            log,
                            progress=overlay_progress,
                        )
                        if overlay_path is not None and Path(overlay_path).is_file():
                            final_temp = Path(overlay_path)
                    except Exception as error:
                        overlay_degraded = True
                        log(f"Không áp được hiệu ứng thêm: {error}. Dùng nền gốc.")
                        final_temp = temporary
                    if overlay_degraded:
                        degraded = True
                    if final_temp != temporary:
                        valid_overlay, overlay_reason = _validate_background(
                            final_temp,
                            width=profile.width,
                            height=profile.height,
                            fps=profile.fps,
                            total_frames=total_frames,
                        )
                        if not valid_overlay:
                            overlay_degraded = True
                            degraded = True
                            log(
                                f"Hiệu ứng thêm không hợp lệ ({overlay_reason}); dùng nền gốc."
                            )
                            final_temp.unlink(missing_ok=True)
                            final_temp = temporary
                os.replace(final_temp, target)
                log(
                    f"Hiệu ứng: {clip_seconds:.1f}s (cache {cache_hits}/{len(unique) + (1 if cover_path else 0)}), "
                    f"ghép nền: {assemble_seconds:.1f}s."
                )
                if progress is not None:
                    try:
                        progress(1.0, 1.0)
                    except Exception:
                        pass
            finally:
                temporary.unlink(missing_ok=True)
            background_path = target
            try:
                prune(
                    SEGMENT_CACHE_ROOT,
                    max_files=SEGMENT_CACHE_LIMIT_FILES,
                    max_bytes=SEGMENT_CACHE_LIMIT_BYTES,
                    min_age_seconds=300,
                )
            except Exception:
                pass
        finally:
            shutil.rmtree(work, ignore_errors=True)

    if progress is not None:
        try:
            progress(1.0, 1.0)
        except Exception:
            pass

    return BackgroundBuild(
        path=background_path,
        hash=identity_hash,
        profile_id=profile.id,
        profile_fingerprint=profile_id,
        width=profile.width,
        height=profile.height,
        fps=profile.fps,
        total_frames=total_frames,
        cover_frames=cover_frames,
        song_frames=song_frames,
        image_frames=image_frames,
        outro_frames=outro_frames,
        overlay_hash=overlay_hash,
        overlay_degraded=degraded,
        segments=list(unique.values()),
        degraded=degraded,
    )


def build_background(*args, **kwargs) -> BackgroundBuild:
    """Serialize builds in this process; unique temp names protect cross-process."""
    output_dir = kwargs.get("output_dir")
    lock_path = None
    if output_dir is not None:
        lock_path = Path(output_dir).expanduser() / BACKGROUND_DIR / ".build.lock"
    with _BUILD_LOCK:
        if lock_path is None:
            return _build_background_impl(*args, **kwargs)
        with _file_lock(lock_path):
            return _build_background_impl(*args, **kwargs)


def background_status(prepared: dict, output_dir: Path | None = None) -> dict:
    """Return ready/stale/missing for the current inputs without rendering."""
    output_text = str(output_dir or prepared.get("output_dir") or "").strip()
    if not output_text:
        return {"state": "missing", "reason": "chưa chọn thư mục xuất"}
    output_dir = Path(output_text).expanduser()
    profile = get_profile(prepared.get("output_profile"))
    try:
        plan = _plan_background(prepared, output_dir=output_dir, profile=profile)
    except Exception as error:
        return {"state": "missing", "reason": str(error)}
    meta = prepared.get("background_render")
    meta = meta if isinstance(meta, dict) else None
    base = {
        "hash": plan.identity_hash,
        "path": str(plan.target),
        "total_frames": plan.total_frames,
        "cover_frames": plan.cover_frames,
        "song_frames": plan.song_frames,
        "image_frames": plan.image_frames,
        "outro_frames": plan.outro_frames,
        "overlay_hash": plan.overlay_hash,
        "profile_id": profile.id,
        "fps": profile.fps,
    }
    if meta is None:
        valid, reason = _validate_background(
            plan.target,
            width=profile.width,
            height=profile.height,
            fps=profile.fps,
            total_frames=plan.total_frames,
        )
        if valid:
            return {**base, "state": "ready", "reason": "thiếu metadata; lần dựng/xuất sau sẽ ghi lại"}
        return {**base, "state": "missing", "reason": "chưa dựng nền"}
    if str(meta.get("hash") or "") != plan.identity_hash:
        return {**base, "state": "stale", "reason": "ảnh, hiệu ứng, profile hoặc thời lượng đã đổi"}
    for key in ("total_frames", "cover_frames", "song_frames", "image_frames", "outro_frames"):
        if int(meta.get(key) or 0) != int(getattr(plan, key)):
            return {**base, "state": "stale", "reason": "số frame đã đổi"}
    if str(meta.get("overlay_hash") or "") != plan.overlay_hash:
        return {**base, "state": "stale", "reason": "hiệu ứng thêm đã đổi"}
    if str(meta.get("profile_fingerprint") or "") != plan.profile_fingerprint:
        return {**base, "state": "stale", "reason": "profile đã đổi"}
    valid, reason = _validate_background(
        plan.target,
        width=profile.width,
        height=profile.height,
        fps=profile.fps,
        total_frames=plan.total_frames,
    )
    if not valid:
        state = "stale" if plan.target.exists() else "missing"
        return {**base, "state": state, "reason": reason}
    if bool(meta.get("degraded")):
        return {**base, "state": "stale", "reason": "nền trước dùng ảnh tĩnh fallback"}
    if bool(meta.get("overlay_degraded")):
        return {**base, "state": "stale", "reason": "hiệu ứng thêm lần trước bị lỗi"}
    return {**base, "state": "ready", "reason": ""}


def cleanup_backgrounds(
    output_dir: Path,
    *,
    keep_hashes: list[str] | None = None,
    project_paths: list[Path] | None = None,
    min_age_days: float = 7.0,
) -> list[str]:
    """Delete unreferenced old backgrounds; keep current/unknown ones."""
    output_dir = Path(output_dir).expanduser()
    root = output_dir / BACKGROUND_DIR
    if not root.is_dir():
        return []
    referenced = {_hex_from_hash(value) for value in (keep_hashes or []) if str(value).strip()}
    project_files = list(output_dir.glob("*.karaoke.json"))
    for path in project_paths or []:
        try:
            if Path(path).is_file() and Path(path) not in project_files:
                project_files.append(Path(path))
        except OSError:
            continue
    for project_path in project_files:
        try:
            data = json.loads(Path(project_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # If a project cannot be read, keep every old background rather
            # than risking deletion of a file it still references.
            return []
        meta = data.get("background_render") if isinstance(data, dict) else None
        if isinstance(meta, dict) and str(meta.get("hash") or "").strip():
            referenced.add(_hex_from_hash(str(meta["hash"])))
    cutoff = time.time() - max(0.0, float(min_age_days)) * 86400
    deleted: list[str] = []
    for path in root.glob("background-*.mp4"):
        if path.is_symlink() or not path.is_file():
            continue
        hex_value = path.stem.removeprefix("background-")
        if hex_value in referenced:
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
            path.unlink()
            deleted.append(path.name)
        except OSError:
            continue
    return deleted


def _picture_identity(
    background: BackgroundBuild,
    lines: list[dict],
    presentation: dict | None = None,
    typography: dict | None = None,
    waveform: dict | None = None,
    waveform_hash: str = "",
) -> str:
    from karaoke.presentation import presentation_hash

    simplified = [
        {
            "text": line.get("text") or "",
            "start": float(line.get("start") or 0),
            "end": float(line.get("end") or 0),
            "words": [
                {
                    "text": word.get("text") or "",
                    "start": float(word.get("start") or 0),
                    "end": float(word.get("end") or 0),
                }
                for word in (line.get("words") or [])
                if isinstance(word, dict)
            ],
        }
        for line in lines
    ]
    payload = {
        "builder_version": PICTURE_BUILDER_VERSION,
        "background": background.hash,
        "profile": background.profile_fingerprint,
        "total_frames": background.total_frames,
        "cover_frames": background.cover_frames,
        "text_fps": TEXT_FPS,
        "lines": simplified,
        "presentation": presentation_hash(presentation or {}, typography or {}),
        "waveform": waveform_hash or _sha256_text(_canonical(waveform or {})),
    }
    return _hash_with_prefix(_sha256_text(_canonical(payload)))


def _picture_path(output_dir: Path, identity: str) -> Path:
    return Path(output_dir) / PICTURE_DIR / f"picture-{_hex_from_hash(identity)}.mp4"


def cleanup_pictures(
    output_dir: Path,
    *,
    keep: list[Path] | None = None,
    max_files: int = 5,
    min_age_days: float = 2.0,
) -> list[str]:
    """Delete old derived picture files, keeping the current export's picture."""
    root = Path(output_dir).expanduser() / PICTURE_DIR
    if not root.is_dir():
        return []
    keep_paths = {Path(path).resolve() for path in (keep or []) if Path(path).is_file()}
    cutoff = time.time() - max(0.0, float(min_age_days)) * 86400
    entries: list[tuple[float, Path]] = []
    for path in root.glob("picture-*.mp4"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            entries.append((path.stat().st_mtime, path))
        except OSError:
            continue
    entries.sort(key=lambda item: item[0], reverse=True)
    deleted: list[str] = []
    for index, (mtime, path) in enumerate(entries):
        if path.resolve() in keep_paths:
            continue
        if index < max_files or mtime > cutoff:
            continue
        try:
            path.unlink()
            deleted.append(path.name)
        except OSError:
            continue
    return deleted


def render_picture(
    background: BackgroundBuild,
    lines: list[dict],
    *,
    profile: RenderProfile,
    destination: Path,
    presentation: dict | None = None,
    typography: dict | None = None,
    waveform: dict | None = None,
    waveform_path: Path | None = None,
    progress=None,
    log,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _overlay_text(
        lines,
        background.total_frames / background.fps,
        background.cover_frames / background.fps,
        background.path,
        destination,
        log,
        profile=profile,
        presentation=presentation or {},
        typography=typography or {},
        waveform=waveform or {},
        waveform_path=waveform_path,
        total_frames=background.total_frames,
        song_duration=background.song_frames / background.fps,
        progress=progress,
    )
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise MediaError("Không tạo được picture có lời.")
    return destination


def mux_outputs(
    picture: Path,
    *,
    original_audio: Path,
    instrumental_audio: Path,
    cover_duration: float,
    vocal_name: str,
    instrumental_name: str,
    output_dir: Path,
    ambience: Path | None = None,
    loop_ambience: bool = False,
    log,
) -> tuple[Path, Path]:
    vocal_out = Path(output_dir) / vocal_name
    instrumental_out = Path(output_dir) / instrumental_name
    _mux(
        picture,
        original_audio,
        vocal_out,
        cover_duration,
        log,
        "có giọng",
        ambience=ambience,
        loop_ambience=loop_ambience,
    )
    _mux(
        picture,
        instrumental_audio,
        instrumental_out,
        cover_duration,
        log,
        "không lời",
        ambience=ambience,
        loop_ambience=loop_ambience,
    )
    return vocal_out, instrumental_out


def render_videos(
    *,
    lines: list[dict],
    title: str,
    artist: str,
    cover_line: str,
    cover_photo: Path,
    background_images: list[Path],
    original_audio: Path,
    instrumental_audio: Path,
    output_dir: Path,
    vocal_name: str,
    instrumental_name: str,
    cover_duration: float,
    image_duration: float,
    background_effects: list[dict] | None = None,
    overlay_effects: list[dict] | None = None,
    waveform: dict | None = None,
    presentation: dict | None = None,
    typography: dict | None = None,
    output_profile: str = "landscape_1080p",
    background: BackgroundRef | None = None,
    rebuild_background: bool = False,
    progress=None,
    log,
) -> RenderResult:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise MediaError("Chưa có ffmpeg hoặc ffprobe trong PATH.")

    profile = get_profile(output_profile)
    prepared = {
        "title": title,
        "artist": artist,
        "cover_line": cover_line,
        "cover_image": str(cover_photo),
        "background_images": [str(path) for path in background_images],
        "background_effects": background_effects or [],
        "overlay_effects": overlay_effects or [],
        "waveform": waveform or {},
        "presentation": presentation or {},
        "typography": typography or {},
        "original_audio": str(original_audio),
        "instrumental_audio": str(instrumental_audio),
        "output_dir": str(output_dir),
        "cover_duration": cover_duration,
        "image_duration": image_duration,
        "output_profile": profile.id,
    }
    log("1/2 Đang dựng nền có hiệu ứng.")

    def build_progress(done, total) -> None:
        if progress is not None:
            try:
                progress(0.7 * float(done) / max(1.0, float(total)), 1.0)
            except Exception:
                pass

    build = build_background(
        prepared,
        output_dir=output_dir,
        profile=profile,
        background=background,
        force=rebuild_background,
        progress=build_progress,
        log=log,
    )

    log("2/2 Đang ghép lời và tiếng.")
    work = Path(tempfile.mkdtemp(prefix=".karaoke-render-", dir=str(output_dir)))
    try:
        def picture_progress(done, total) -> None:
            if progress is not None:
                try:
                    progress(0.7 + 0.3 * float(done) / max(1.0, float(total)), 1.0)
                except Exception:
                    pass

        waveform_path: Path | None = None
        waveform_hash = ""
        waveform_plan = dict(waveform or {})
        if waveform_plan.get("enabled"):
            try:
                from karaoke.waveform import waveform_identity

                source_audio = instrumental_audio if waveform_plan.get("source") != "original" else original_audio
                waveform_hash = waveform_identity(
                    waveform_plan,
                    source_audio,
                    profile,
                    build.cover_frames,
                    build.song_frames,
                    build.outro_frames,
                )
            except Exception as error:
                log(f"Không tính được waveform: {error}")
                waveform_hash = ""

        picture_identity = _picture_identity(
            build,
            lines,
            presentation=presentation or {},
            typography=typography or {},
            waveform=waveform_plan,
            waveform_hash=waveform_hash,
        )
        picture_path = _picture_path(output_dir, picture_identity)
        picture_path.parent.mkdir(parents=True, exist_ok=True)
        picture_started = time.perf_counter()
        valid, _reason = _validate_background(
            picture_path,
            width=build.width,
            height=build.height,
            fps=build.fps,
            total_frames=build.total_frames,
        )
        if valid:
            picture = picture_path
            log("Dùng lại picture đã có.")
        else:
            if waveform_plan.get("enabled") and waveform_hash:
                try:
                    from karaoke.waveform import render_waveform_clip

                    source_audio = (
                        instrumental_audio
                        if waveform_plan.get("source") != "original"
                        else original_audio
                    )
                    waveform_path = render_waveform_clip(
                        waveform_plan,
                        source_audio,
                        profile,
                        build.cover_frames / build.fps,
                        build.song_frames / build.fps,
                        build.outro_frames / build.fps,
                        work / "waveform.mp4",
                        log,
                    )
                except Exception as error:
                    log(f"CẢNH BÁO: không tạo được waveform ({error}); video sẽ xuất không có waveform.")
                    waveform_path = None
                    waveform_hash = ""
                    # Do not cache a picture as "with waveform" when the clip
                    # failed; recompute the identity without waveform_hash so
                    # the next export retries.
                    picture_identity = _picture_identity(
                        build,
                        lines,
                        presentation=presentation or {},
                        typography=typography or {},
                        waveform=waveform_plan,
                        waveform_hash="",
                    )
                    picture_path = _picture_path(output_dir, picture_identity)
                    picture_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = picture_path.with_name(
                f".{picture_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp.mp4"
            )
            try:
                render_picture(
                    build,
                    lines,
                    profile=profile,
                    destination=temporary,
                    presentation=presentation or {},
                    typography=typography or {},
                    waveform=waveform_plan,
                    waveform_path=waveform_path,
                    progress=picture_progress,
                    log=log,
                )
                os.replace(temporary, picture_path)
                picture = picture_path
            finally:
                temporary.unlink(missing_ok=True)
        log(f"Picture: {time.perf_counter() - picture_started:.1f}s.")
        total = build.total_frames / build.fps
        ambience = None
        ambient_kind = _ambient_kind(background_effects, background_images)
        if ambient_kind is not None:
            try:
                log(f"Đang tạo tiếng nền {ambient_kind} rất nhỏ.")
                ambience = synthesize_ambience(ambient_kind, total, work / "ambience.wav")
            except Exception as error:  # ambience is optional
                log(f"Không tạo được tiếng nền: {error}")
                ambience = None
        if progress is not None:
            try:
                progress(0.95, 1.0)
            except Exception:
                pass
        mux_started = time.perf_counter()
        vocal_out, instrumental_out = mux_outputs(
            picture,
            original_audio=original_audio,
            instrumental_audio=instrumental_audio,
            cover_duration=build.cover_frames / build.fps,
            vocal_name=vocal_name,
            instrumental_name=instrumental_name,
            output_dir=output_dir,
            ambience=ambience,
            loop_ambience=total > MAX_AMBIENT_DURATION,
            log=log,
        )
        log(f"Mux: {time.perf_counter() - mux_started:.1f}s.")
        try:
            cleanup_pictures(output_dir, keep=[picture])
        except Exception:
            pass
        if progress is not None:
            try:
                progress(1.0, 1.0)
            except Exception:
                pass
        return RenderResult(vocal=vocal_out, instrumental=instrumental_out, background=build)
    finally:
        shutil.rmtree(work, ignore_errors=True)

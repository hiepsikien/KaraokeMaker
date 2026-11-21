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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from karaoke.ambient import MAX_DURATION as MAX_AMBIENT_DURATION
from karaoke.ambient import synthesize as synthesize_ambience
from karaoke.living import (
    MOTION_CACHE_VERSION,
    plan_has_motion,
    render_motion_loop,
    visual_plan_hash,
)
from karaoke.media import (
    COVER_RENDER_VERSION,
    IMAGE_PREP_VERSION,
    MediaError,
    make_cover,
    prepare_slide,
    probe_duration,
)
from karaoke.profiles import RenderProfile, get_profile
from karaoke.render import _ambient_kind, _mux, _overlay_text, _run

BACKGROUND_SCHEMA_VERSION = 1
BACKGROUND_BUILDER_VERSION = 1
BACKGROUND_CRF = 18
BACKGROUND_PRESET = "veryfast"
BACKGROUND_DIR = Path(".karaoke-media") / "backgrounds"

_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_HASH_LOCK = threading.Lock()


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
        "strip_rect": list(profile.strip_rect),
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
) -> tuple[int, int, int, int]:
    cover_frames = 0 if float(cover_duration) < 0.05 else max(0, int(round(float(cover_duration) * fps)))
    image_frames = max(1, int(round(float(image_duration) * fps)))
    song_frames = max(1, int(math.ceil(float(song_length) * fps)))
    return cover_frames, image_frames, song_frames, cover_frames + song_frames


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


def _cover_identity(
    cover_photo: Path | None,
    title: str,
    artist: str,
    cover_line: str,
    cover_frames: int,
    profile: RenderProfile,
) -> str:
    if cover_frames <= 0 or cover_photo is None:
        return "no-cover"
    payload = {
        "image": _file_digest(cover_photo),
        "title": " ".join(str(title or "").split()),
        "artist": " ".join(str(artist or "").split()),
        "cover_line": " ".join(str(cover_line or "").split()),
        "cover_frames": cover_frames,
        "profile": profile_fingerprint(profile),
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
        "image_hash": image_hash,
        "plan_hash": visual_plan_hash(plan),
        "image_frames": image_frames,
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "fit_mode": profile.fit_mode,
        "strip_rect": list(profile.strip_rect),
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
    segments: list[dict] = field(default_factory=list)
    degraded: bool = False
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_project(self) -> dict:
        source_dir = str(self.path.parent.parent.parent)
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
            "degraded": self.degraded,
            "created": self.created,
            "segments": self.segments,
        }


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


def _probe_video(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,width,height,r_frame_rate,avg_frame_rate,nb_frames,pix_fmt,duration:format=duration",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        raise MediaError(process.stderr.strip() or "ffprobe không đọc được video.")
    try:
        return json.loads(process.stdout or "{}")
    except json.JSONDecodeError as error:
        raise MediaError("ffprobe trả dữ liệu không hợp lệ.") from error


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
    try:
        meta = _probe_video(path)
    except MediaError as error:
        return False, str(error)
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
        if abs(int(raw_frames) - total_frames) > 1:
            return False, "nb_frames không khớp"
    else:
        duration = video.get("duration") or (meta.get("format") or {}).get("duration")
        try:
            seconds = float(duration)
        except (TypeError, ValueError):
            return False, "không đọc được duration"
        if abs(seconds - total_frames / fps) > 1.5 / fps:
            return False, "duration không khớp"
    return True, ""


def _render_segment(
    image: Path,
    plan: dict | None,
    *,
    image_frames: int,
    profile: RenderProfile,
    work: Path,
    index: int,
    progress,
    log,
) -> tuple[Path, bool, bool]:
    """Return (media path, is_video, degraded)."""
    if plan is not None and plan_has_motion(plan):
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
            )
        except Exception as error:  # keep export usable if one effect fails
            log(f"Không tạo được hiệu ứng cho {image.name}: {error}")
            clip = None
        if clip is not None and Path(clip).is_file():
            return Path(clip), True, False
        log(f"Hiệu ứng cho {image.name} không resolve được; dùng ảnh tĩnh.")
    slide = work / f"slide-{index:02d}.jpg"
    prepare_slide(image, slide, profile=profile)
    return slide, False, plan is not None and plan_has_motion(plan)


def _assemble_background(
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
            command += ["-stream_loop", "-1", "-i", str(media)]
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
        "-movflags",
        "+faststart",
        str(destination),
    ]
    _run(command, log)


def build_background(
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

    cover_text = str(prepared.get("cover_image") or "").strip()
    cover_photo = Path(cover_text).expanduser() if cover_text else None
    background_images = [
        Path(str(item)).expanduser()
        for item in (prepared.get("background_images") or [])
        if str(item).strip()
    ]
    if not background_images:
        if cover_photo is None:
            raise MediaError("Chưa có ảnh nền để dựng background.")
        background_images = [cover_photo]

    effects = _effect_map(prepared.get("background_effects") or [])
    original = Path(str(prepared.get("original_audio") or "")).expanduser()
    instrumental = Path(str(prepared.get("instrumental_audio") or "")).expanduser()
    if not original.is_file():
        raise MediaError(f"Không thấy audio gốc: {original}")
    if not instrumental.is_file():
        raise MediaError(f"Không thấy file instrumental: {instrumental}")

    song_length = max(probe_duration(original), probe_duration(instrumental))
    cover_frames, image_frames, song_frames, total_frames = _frames(
        float(prepared.get("cover_duration") or 0),
        float(prepared.get("image_duration") or 8),
        song_length,
        profile.fps,
    )

    unique: dict[str, dict] = {}
    order: list[str] = []
    for image in background_images:
        key = _image_key(image)
        order.append(key)
        if key in unique:
            continue
        image_hash = _file_digest(image)
        plan = _effect_plan(effects.get(key))
        if plan is not None and plan_has_motion(plan):
            identity = _motion_identity(plan, image_hash, image_frames, profile)
            kind = "motion"
        else:
            identity = _static_identity(image_hash, profile)
            kind = "static"
        unique[key] = {
            "index": len(unique),
            "image_path": str(image),
            "image_hash": _hash_with_prefix(image_hash),
            "kind": kind,
            "clip_identity": identity,
            "asset_id": None,
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
    )
    profile_id = profile_fingerprint(profile)
    identity_payload = {
        "schema_version": BACKGROUND_SCHEMA_VERSION,
        "builder_version": BACKGROUND_BUILDER_VERSION,
        "crf": BACKGROUND_CRF,
        "preset": BACKGROUND_PRESET,
        "order": [unique[key]["clip_identity"] for key in order],
        "cover": cover_id,
        "profile": profile_id,
        "total_frames": total_frames,
        "cover_frames": cover_frames,
        "song_frames": song_frames,
        "image_frames": image_frames,
    }
    identity_hex = _sha256_text(_canonical(identity_payload))
    identity_hash = _hash_with_prefix(identity_hex)
    target = _background_path(output_dir, identity_hex)
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
            candidate = _background_path(background.source_output_dir, identity_hex)
            if candidate.resolve() == target.resolve():
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
                temporary = target.parent / f".{identity_hex}.{os.getpid()}.copy.tmp.mp4"
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
        rendered: list[tuple[Path, bool]] = []
        work = Path(tempfile.mkdtemp(prefix=".karaoke-bg-", dir=str(output_dir)))

        def segment_progress(index: int, done: int, total: int) -> None:
            if progress is None:
                return
            try:
                fraction = index + (done / total if total else 1.0)
                progress(fraction, len(unique))
            except Exception:
                pass

        try:
            if cover_frames > 0 and cover_photo is not None:
                cover_card = work / "cover.jpg"
                make_cover(
                    cover_photo,
                    str(prepared.get("title") or ""),
                    str(prepared.get("artist") or ""),
                    str(prepared.get("cover_line") or ""),
                    cover_card,
                    profile=profile,
                )
            for segment in unique.values():
                media, is_video, is_degraded = _render_segment(
                    Path(segment["image_path"]),
                    _effect_plan(effects.get(_image_key(Path(segment["image_path"])))),
                    image_frames=image_frames,
                    profile=profile,
                    work=work,
                    index=int(segment["index"]),
                    progress=segment_progress,
                    log=log,
                )
                segment["kind"] = "static-fallback" if is_degraded else segment["kind"]
                segment["degraded"] = is_degraded
                degraded = degraded or is_degraded
                rendered.append((media, is_video))

            slots: list[tuple[Path, bool, int]] = []
            if cover_frames > 0 and cover_photo is not None:
                slots.append((work / "cover.jpg", False, cover_frames))
            remaining = song_frames
            index = 0
            while remaining > 0:
                media, is_video = rendered[index % len(rendered)]
                frames = min(image_frames, remaining)
                slots.append((media, is_video, frames))
                remaining -= frames
                index += 1

            temporary = target.parent / f".{identity_hex}.{os.getpid()}.tmp.mp4"
            log(f"Đang dựng background {total_frames} frame cho {len(unique)} ảnh.")
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
                valid, reason = _validate_background(
                    temporary,
                    width=profile.width,
                    height=profile.height,
                    fps=profile.fps,
                    total_frames=total_frames,
                )
                if not valid:
                    raise MediaError(f"Background dựng xong nhưng không hợp lệ: {reason}")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            background_path = target
        finally:
            shutil.rmtree(work, ignore_errors=True)

    if progress is not None:
        try:
            progress(len(unique), len(unique))
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
        segments=list(unique.values()),
        degraded=degraded,
    )


def render_picture(
    background: BackgroundBuild,
    lines: list[dict],
    *,
    profile: RenderProfile,
    destination: Path,
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
        total_frames=background.total_frames,
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
    output_profile: str = "landscape_1080p",
    background: BackgroundRef | None = None,
    rebuild_background: bool = False,
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
        "original_audio": str(original_audio),
        "instrumental_audio": str(instrumental_audio),
        "output_dir": str(output_dir),
        "cover_duration": cover_duration,
        "image_duration": image_duration,
        "output_profile": profile.id,
    }
    build = build_background(
        prepared,
        output_dir=output_dir,
        profile=profile,
        background=background,
        force=rebuild_background,
        log=log,
    )

    work = Path(tempfile.mkdtemp(prefix=".karaoke-render-", dir=str(output_dir)))
    try:
        picture = render_picture(
            build,
            lines,
            profile=profile,
            destination=work / "picture.mp4",
            log=log,
        )
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
        return RenderResult(vocal=vocal_out, instrumental=instrumental_out, background=build)
    finally:
        shutil.rmtree(work, ignore_errors=True)

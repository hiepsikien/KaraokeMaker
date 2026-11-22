"""Short waveform clips for karaoke exports (plan section 7).

The module is deliberately self-contained: it decodes audio with ffmpeg into
raw mono PCM (no network, no extra dependencies), buckets it with numpy, and
renders a small overlay clip with Pillow. ``karaoke.render`` composites the
clip during the song window; this module only produces the clip and its
stable identity.

The default clip is an opaque H.264 rectangle with a dark "chip" background,
so the overlay does not need an alpha channel. Setting the plan's
``background`` flag to ``False`` switches to QuickTime RLE (``qtrle``/argb) in
a ``.mov`` container, which keeps real transparency at the cost of size.
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
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from karaoke.cache import cache_root
from karaoke.presentation import normalize_waveform
from karaoke.profiles import RenderProfile, get_profile

# Bump when peaking, drawing, or the encoded format changes so callers that
# cache clips by ``waveform_identity`` regenerate them.
WAVEFORM_MODULE_VERSION = 1
PEAKS_FORMAT_VERSION = 1
# Bump only when the smoothing algorithm changes: it is part of the cache key.
PEAKS_SMOOTHING_VERSION = 1
PEAKS_RATE = 50
DECODE_RATE = 8000
# Below this raw amplitude a bucket is treated as silence so a quiet recording
# does not get amplified into a full-scale noise waveform.
SILENCE_FLOOR = 1e-4
CHIP_RGB = (8, 6, 4)
CHIP_ALPHA = 196


class WaveformError(RuntimeError):
    """Raised when audio cannot be decoded or the clip cannot be encoded."""


_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}
_DIGEST_LOCK = threading.Lock()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _file_digest(path: Path) -> str:
    """SHA-256 of the audio content, memoised by path + size + mtime."""
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _DIGEST_LOCK:
        cached = _DIGEST_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _DIGEST_LOCK:
        _DIGEST_CACHE[key] = value
    return value


def _cache_dir() -> Path:
    # Resolved on every call so tests and users can override KARAOKE_CACHE_DIR.
    return cache_root("waveform")


def _peaks_cache_path(audio: Path, rate: int) -> tuple[Path, str]:
    stat = audio.stat()
    content_hash = _file_digest(audio)
    material = "|".join(
        [
            content_hash,
            str(stat.st_mtime_ns),
            str(stat.st_size),
            str(rate),
            str(PEAKS_SMOOTHING_VERSION),
            str(PEAKS_FORMAT_VERSION),
        ]
    )
    key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return _cache_dir() / f"peaks-{key}.json", content_hash


def _read_peaks_cache(path: Path, rate: int) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("version") != PEAKS_FORMAT_VERSION:
        return None
    if data.get("smoothing_version") != PEAKS_SMOOTHING_VERSION:
        return None
    try:
        if int(data.get("rate")) != rate:
            return None
        rms = data.get("rms")
        peak = data.get("peak")
        if not isinstance(rms, list) or not isinstance(peak, list):
            return None
        if int(data.get("count")) != len(rms) or len(peak) != len(rms):
            return None
        # Validate the payload so a hand-edited cache cannot crash rendering.
        for value in rms:
            float(value)
        for value in peak:
            float(value)
    except (TypeError, ValueError):
        return None
    return data


def _write_peaks_cache(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".peaks-", suffix=".json", dir=str(path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_name, path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
    except OSError:
        # A read-only cache must not break an export.
        return


def _decode_mono(audio: Path) -> np.ndarray:
    if shutil.which("ffmpeg") is None:
        raise WaveformError("Chưa có ffmpeg trong PATH để đọc waveform.")
    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio),
        "-vn",
        "-f",
        "s16le",
        "-ac",
        "1",
        "-ar",
        str(DECODE_RATE),
        "pipe:1",
    ]
    try:
        process = subprocess.run(command, capture_output=True, check=False)
    except FileNotFoundError as error:  # pragma: no cover - guard for broken PATH
        raise WaveformError("Chưa có ffmpeg trong PATH để đọc waveform.") from error
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", "replace").strip()
        last = detail.splitlines()[-1] if detail else ""
        raise WaveformError(
            f"ffmpeg không đọc được audio waveform {audio.name}: {last or 'lỗi không rõ'}"
        )
    raw = process.stdout or b""
    if not raw:
        return np.zeros(0, dtype=np.float32)
    # s16le is little-endian on every platform ffmpeg supports here.
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _smooth(values: np.ndarray) -> np.ndarray:
    """Three-tap smoothing keeps the waveform from vibrating bucket to bucket."""
    if values.size < 3:
        return values
    padded = np.pad(values, (1, 1), mode="edge")
    return 0.25 * padded[:-2] + 0.5 * padded[1:-1] + 0.25 * padded[2:]


def _bucket(samples: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    if samples.size == 0:
        empty = np.zeros(0, dtype=np.float32)
        return empty, empty.copy()
    per_bucket = max(1, int(round(DECODE_RATE / rate)))
    count = max(1, int(math.ceil(samples.size / per_bucket)))
    padded = np.zeros(count * per_bucket, dtype=np.float32)
    padded[: samples.size] = samples
    frames = padded.reshape(count, per_bucket).astype(np.float64)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    peak = np.max(np.abs(frames), axis=1)
    return _smooth(rms), _smooth(peak)


def _normalize(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    top = float(values.max())
    if top <= SILENCE_FLOOR:
        return np.zeros(values.size, dtype=np.float64)
    return np.clip(values / top, 0.0, 1.0)


def load_peaks(audio: Path, rate: int = 50) -> dict:
    """Return normalised ``rms``/``peak`` buckets for ``audio``.

    Audio is decoded at 8 kHz mono with ffmpeg and bucketed at ``rate`` Hz.
    The JSON cache lives under the app's waveform cache directory and is keyed
    by content hash + mtime + size + rate + smoothing version.
    """
    path = Path(audio).expanduser()
    if not path.is_file():
        raise WaveformError(f"Không thấy file audio: {path}")
    try:
        bucket_rate = int(rate)
    except (TypeError, ValueError) as error:
        raise WaveformError(f"Tần số peaks không hợp lệ: {rate!r}") from error
    if bucket_rate <= 0:
        raise WaveformError(f"Tần số peaks phải lớn hơn 0: {bucket_rate}")
    bucket_rate = min(bucket_rate, DECODE_RATE)

    try:
        cache_path, content_hash = _peaks_cache_path(path, bucket_rate)
    except OSError as error:
        raise WaveformError(f"Không đọc được file audio: {path.name}") from error
    cached = _read_peaks_cache(cache_path, bucket_rate)
    if cached is not None:
        return cached

    samples = _decode_mono(path)
    rms, peak = _bucket(samples, bucket_rate)
    rms = _normalize(rms)
    peak = _normalize(peak)
    duration = samples.size / float(DECODE_RATE)
    payload = {
        "version": PEAKS_FORMAT_VERSION,
        "smoothing_version": PEAKS_SMOOTHING_VERSION,
        "rate": bucket_rate,
        "sample_rate": DECODE_RATE,
        "source_hash": f"sha256:{content_hash}",
        "duration": round(duration, 6),
        "count": int(rms.size),
        "rms": [round(float(value), 5) for value in rms],
        "peak": [round(float(value), 5) for value in peak],
    }
    _write_peaks_cache(cache_path, payload)
    return payload


def peaks_digest(peaks: dict) -> str:
    """Stable digest of the visual peak data (not the cache metadata)."""
    payload = {
        "rate": int(peaks.get("rate") or PEAKS_RATE),
        "count": int(peaks.get("count") or 0),
        "duration": round(float(peaks.get("duration") or 0.0), 6),
        "rms": [float(value) for value in (peaks.get("rms") or [])],
        "peak": [float(value) for value in (peaks.get("peak") or [])],
    }
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _as_profile(profile: Any) -> RenderProfile:
    if isinstance(profile, RenderProfile):
        return profile
    if isinstance(profile, dict):
        return RenderProfile(
            id=str(profile.get("id") or "landscape_1080p"),
            width=int(profile["width"]),
            height=int(profile["height"]),
            fps=int(profile["fps"]),
            fit_mode=str(profile.get("fit_mode") or "contain"),
            strip_rect=tuple(int(value) for value in profile["strip_rect"]),
            safe_top=int(profile.get("safe_top") or 0),
            safe_bottom=int(profile.get("safe_bottom") or 0),
            safe_left=int(profile.get("safe_left") or 0),
            safe_right=int(profile.get("safe_right") or 0),
            lyric_font_size=int(profile.get("lyric_font_size") or 56),
            max_lyric_width=int(profile.get("max_lyric_width") or profile["width"]),
            lyric_row_gap=int(profile.get("lyric_row_gap") or 10),
            cover_title_size=int(profile.get("cover_title_size") or 78),
            cover_artist_size=int(profile.get("cover_artist_size") or 40),
            cover_line_size=int(profile.get("cover_line_size") or 30),
            cover_max_width=int(profile.get("cover_max_width") or profile["width"]),
        )
    return get_profile(str(profile) if profile else None)


def _frame_int(value: Any) -> int:
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return 0


def _profile_payload(profile: RenderProfile) -> dict:
    return {
        "id": profile.id,
        "width": profile.width,
        "height": profile.height,
        "fps": profile.fps,
        "fit_mode": profile.fit_mode,
        "strip_rect": list(profile.strip_rect),
        "safe_left": profile.safe_left,
        "safe_right": profile.safe_right,
    }


def waveform_identity(
    plan: Any,
    audio: Path,
    profile: Any,
    cover_frames: int,
    song_frames: int,
    outro_frames: int,
) -> str:
    """Stable hash of everything that changes the rendered waveform clip.

    The hash includes every visual plan field, the actual peaks data, the
    profile geometry, all three timeline frame counts and the module version.
    """
    profile_obj = _as_profile(profile)
    config = normalize_waveform(plan)
    peaks = load_peaks(Path(audio), rate=PEAKS_RATE)
    payload = {
        "module_version": WAVEFORM_MODULE_VERSION,
        "peaks_format_version": PEAKS_FORMAT_VERSION,
        "smoothing_version": PEAKS_SMOOTHING_VERSION,
        "peaks_hash": peaks_digest(peaks),
        "peaks_rate": int(peaks.get("rate") or PEAKS_RATE),
        "plan": config,
        "profile": _profile_payload(profile_obj),
        "cover_frames": _frame_int(cover_frames),
        "song_frames": _frame_int(song_frames),
        "outro_frames": _frame_int(outro_frames),
    }
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _clip_size(config: dict, profile: RenderProfile) -> tuple[int, int]:
    width = max(2, int(round(float(profile.width) * float(config["width"]))))
    height = max(2, int(round(float(profile.height) * float(config["height"]))))
    return width, height


def _parse_color(text: Any, opacity: float) -> tuple[int, int, int, int]:
    raw = str(text or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(character * 2 for character in raw)
    if len(raw) == 4:
        raw = "".join(character * 2 for character in raw)
    try:
        if len(raw) == 6:
            red, green, blue = (int(raw[index : index + 2], 16) for index in (0, 2, 4))
            alpha = 255
        elif len(raw) == 8:
            red, green, blue, alpha = (
                int(raw[index : index + 2], 16) for index in (0, 2, 4, 6)
            )
        else:
            red, green, blue, alpha = 255, 255, 255, 255
    except ValueError:
        red, green, blue, alpha = 255, 255, 255, 255
    scale = max(0.0, min(1.0, float(opacity)))
    return red, green, blue, int(round(alpha * scale))


def _sample_values(peaks: dict, count: int) -> list[float]:
    """Aggregate peaks into ``count`` visual samples in the 0..1 range."""
    if count <= 0:
        return []
    rms = [float(value) for value in (peaks.get("rms") or [])]
    peak = [float(value) for value in (peaks.get("peak") or [])]
    size = min(len(rms), len(peak))
    if size <= 0:
        return [0.04] * count
    values: list[float] = []
    for index in range(count):
        start = index * size // count
        end = max(start + 1, (index + 1) * size // count)
        rms_value = max(rms[start:end])
        peak_value = max(peak[start:end])
        combined = 0.7 * rms_value + 0.3 * peak_value
        values.append(min(1.0, max(0.04, combined)))
    return values


def _draw_bars(draw, values, width, height, progress, base, accent) -> None:
    usable_height = max(1.0, height * 0.8)
    center = height / 2.0
    bar_width = max(2, int(round(height * 0.12)))
    gap = max(1, int(round(bar_width * 0.35)))
    count = len(values)
    if count <= 0:
        return
    span = count * (bar_width + gap) - gap
    left = max(0, (width - span) // 2)
    progress_count = int(math.floor(max(0.0, min(1.0, progress)) * count))
    for index, value in enumerate(values):
        x = left + index * (bar_width + gap)
        bar_height = max(2, int(round(value * usable_height)))
        top = center - bar_height / 2.0
        bottom = center + bar_height / 2.0
        color = accent if index < progress_count else base
        draw.rectangle([x, top, x + bar_width - 1, bottom], fill=color)


def _draw_line(draw, values, width, height, progress, base, accent) -> None:
    count = len(values)
    if count <= 0:
        return
    padding = max(2, int(round(width * 0.02)))
    usable_width = max(1, width - 2 * padding)
    center = height / 2.0
    amplitude = max(1.0, height * 0.4)
    upper: list[tuple[float, float]] = []
    lower: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        x = padding + (index * (usable_width - 1) / max(1, count - 1))
        offset = value * amplitude
        upper.append((x, center - offset))
        lower.append((x, center + offset))
    line_width = max(1, int(round(height * 0.045)))
    if len(upper) >= 2:
        draw.line(upper, fill=base, width=line_width, joint="curve")
        draw.line(lower, fill=base, width=line_width, joint="curve")
    progress_count = int(math.ceil(max(0.0, min(1.0, progress)) * count))
    if progress_count >= 2:
        draw.line(
            upper[:progress_count], fill=accent, width=line_width, joint="curve"
        )
        draw.line(
            lower[:progress_count], fill=accent, width=line_width, joint="curve"
        )
    elif progress_count == 1:
        radius = max(1, line_width)
        x, y = upper[0]
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=accent)
        x, y = lower[0]
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=accent)


def _draw_dots(draw, values, width, height, progress, base, accent) -> None:
    count = len(values)
    if count <= 0:
        return
    spacing = max(3, int(round(height * 0.14)))
    radius = max(1, int(round(spacing * 0.28)))
    span = count * spacing
    left = max(0, (width - span) // 2) + spacing // 2
    center = height / 2.0
    amplitude = max(1.0, height * 0.4)
    progress_count = int(math.floor(max(0.0, min(1.0, progress)) * count))
    for index, value in enumerate(values):
        x = left + index * spacing
        if x + radius >= width:
            break
        offset = value * amplitude
        color = accent if index < progress_count else base
        for y in (center - offset, center + offset):
            draw.ellipse(
                [x - radius, y - radius, x + radius, y + radius], fill=color
            )


def _draw_waveform(
    config: dict,
    peaks: dict,
    width: int,
    height: int,
    progress: float,
) -> Image.Image:
    opacity = float(config["opacity"])
    base_color = _parse_color(config["color"], opacity)
    accent_color = _parse_color(config["accent"], opacity)
    style = config["style"]

    if style == "line":
        sample_count = max(2, width)
    elif style == "dots":
        spacing = max(3, int(round(height * 0.14)))
        sample_count = max(1, (width - 4) // spacing)
    else:
        bar_width = max(2, int(round(height * 0.12)))
        gap = max(1, int(round(bar_width * 0.35)))
        sample_count = max(1, width // (bar_width + gap))
    values = _sample_values(peaks, sample_count)

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if style == "line":
        _draw_line(draw, values, width, height, progress, base_color, accent_color)
    elif style == "dots":
        _draw_dots(draw, values, width, height, progress, base_color, accent_color)
    else:
        _draw_bars(draw, values, width, height, progress, base_color, accent_color)
    return layer


def _chip_image(width: int, height: int, opaque: bool) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if opaque:
        # Alpha cannot survive H.264; fill the whole clip with the chip colour.
        image.paste(Image.new("RGBA", (width, height), CHIP_RGB + (255,)), (0, 0))
        return image
    draw = ImageDraw.Draw(image)
    radius = max(2, min(width, height) // 5)
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=radius,
        fill=CHIP_RGB + (CHIP_ALPHA,),
    )
    return image


def _frame(
    config: dict,
    peaks: dict,
    width: int,
    height: int,
    progress: float,
    *,
    background_chip: Image.Image | None,
    opaque: bool,
) -> Image.Image:
    base = (
        background_chip.copy()
        if background_chip is not None
        else Image.new("RGBA", (width, height), (0, 0, 0, 0))
    )
    layer = _draw_waveform(config, peaks, width, height, progress)
    combined = Image.alpha_composite(base, layer)
    return combined.convert("RGB") if opaque else combined


def _blank_frame(width: int, height: int, opaque: bool) -> Image.Image:
    if opaque:
        return Image.new("RGB", (width, height), (0, 0, 0))
    return Image.new("RGBA", (width, height), (0, 0, 0, 0))


def _log(log, message: str) -> None:
    if log is None:
        return
    try:
        log(message)
    except Exception:
        pass


def render_waveform_clip(
    plan: Any,
    audio: Path,
    profile: Any,
    cover_duration: float,
    song_duration: float,
    outro_duration: float,
    destination: Path,
    log,
) -> Path:
    """Render the waveform overlay clip for the whole cover/song/outro timeline.

    The clip is ``plan["width"] × profile.width`` wide and
    ``plan["height"] × profile.height`` tall. Frames are drawn only inside the
    song window; cover and outro frames are blank. FFmpeg's ``enable`` filter
    in ``karaoke.render`` still gates visibility, so a blank rectangle can
    never cover the cover or outro pages.
    """
    profile_obj = _as_profile(profile)
    config = normalize_waveform(plan)
    if shutil.which("ffmpeg") is None:
        raise WaveformError("Chưa có ffmpeg trong PATH để dựng waveform.")

    try:
        cover = max(0.0, float(cover_duration))
        song = max(0.0, float(song_duration))
        outro = max(0.0, float(outro_duration))
    except (TypeError, ValueError) as error:
        raise WaveformError("Thời lượng waveform không hợp lệ.") from error
    total = cover + song + outro
    if total <= 0.0:
        raise WaveformError("Thời lượng clip waveform phải lớn hơn 0.")

    peaks = load_peaks(Path(audio), rate=PEAKS_RATE)
    width, height = _clip_size(config, profile_obj)
    background = bool(config["background"])
    opaque = background
    if opaque:
        # H.264 with yuv420p wants even dimensions.
        width += width % 2
        height += height % 2

    destination = Path(destination).expanduser()
    if not opaque and destination.suffix.lower() not in {".mov", ".mkv"}:
        # qtrle cannot be muxed into MP4; transparent clips are QuickTime.
        destination = destination.with_suffix(".mov")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fps = max(1, int(profile_obj.fps))
    total_frames = max(1, int(round(total * fps)))
    song_start = cover
    song_end = cover + song

    _log(log, f"Đang giải mã peaks waveform ({peaks['count']} mốc).")
    if opaque:
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-frames:v",
            str(total_frames),
            "-movflags",
            "+faststart",
            str(destination),
        ]
    else:
        command = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgba",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "qtrle",
            "-pix_fmt",
            "argb",
            "-frames:v",
            str(total_frames),
            str(destination),
        ]
    chip = _chip_image(width, height, opaque=True) if background else None
    blank = _blank_frame(width, height, opaque)

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stderr is not None
    tail: list[str] = []

    def read_output() -> None:
        for line in process.stderr:
            text = line.decode("utf-8", "replace").strip()
            if text:
                tail.append(text)

    reader = threading.Thread(target=read_output)
    reader.start()
    try:
        for index in range(total_frames):
            timestamp = index / float(fps)
            if song > 0.0 and song_start - 1e-9 <= timestamp < song_end - 1e-9:
                song_time = min(song, max(0.0, timestamp - song_start))
                progress = song_time / song
                frame = _frame(
                    config,
                    peaks,
                    width,
                    height,
                    progress,
                    background_chip=chip if background else None,
                    opaque=opaque,
                )
            else:
                frame = blank
            process.stdin.write(frame.tobytes())
            if index % 96 == 0:
                _log(log, f"Vẽ waveform {index + 1}/{total_frames}.")
    except BrokenPipeError:
        pass
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
    reader.join()
    process.stderr.close()
    code = process.wait()
    if code != 0:
        message = tail[-1] if tail else "ffmpeg dừng với lỗi."
        raise WaveformError(f"Không dựng được clip waveform: {message}")
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise WaveformError("Không tạo được clip waveform.")
    _log(log, f"Đã dựng waveform: {destination.name}.")
    return destination

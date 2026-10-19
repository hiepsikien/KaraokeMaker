"""Mix the instrumental stem with high and low bands from the original."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from karaoke.errors import JobError

MIX_FORMATS = frozenset({"wav", "flac", "mp3"})
DEFAULT_HIGH_HZ = 8000.0
DEFAULT_LOW_HZ = 120.0
DEFAULT_HIGH_VOLUME = 50.0
DEFAULT_LOW_VOLUME = 50.0


def default_mix_settings() -> dict:
    return {
        "high_hz": DEFAULT_HIGH_HZ,
        "low_hz": DEFAULT_LOW_HZ,
        "high_volume": DEFAULT_HIGH_VOLUME,
        "low_volume": DEFAULT_LOW_VOLUME,
    }


def parse_mix_settings(payload: dict | None) -> dict:
    data = payload or {}
    settings = default_mix_settings()
    try:
        if "high_hz" in data and data["high_hz"] not in (None, ""):
            settings["high_hz"] = float(data["high_hz"])
        if "low_hz" in data and data["low_hz"] not in (None, ""):
            settings["low_hz"] = float(data["low_hz"])
        if "high_volume" in data and data["high_volume"] not in (None, ""):
            settings["high_volume"] = float(data["high_volume"])
        if "low_volume" in data and data["low_volume"] not in (None, ""):
            settings["low_volume"] = float(data["low_volume"])
    except (TypeError, ValueError) as error:
        raise JobError("Cutoff và volume phải là số.") from error

    high = settings["high_hz"]
    low = settings["low_hz"]
    high_volume = settings["high_volume"]
    low_volume = settings["low_volume"]
    if not 20 <= low <= 500:
        raise JobError("Cutoff thấp phải từ 20 đến 500 Hz.")
    if not 2000 <= high <= 16000:
        raise JobError("Cutoff cao phải từ 2000 đến 16000 Hz.")
    if high <= low:
        raise JobError("Cutoff cao phải lớn hơn cutoff thấp.")
    if not 0 <= high_volume <= 100 or not 0 <= low_volume <= 100:
        raise JobError("Volume phải từ 0 đến 100%.")
    return settings


def mix_filtergraph(settings: dict) -> str:
    """Three-track sum. amix normalize stays off so the instrumental is not attenuated."""
    parsed = parse_mix_settings(settings)
    high_gain = parsed["high_volume"] / 100
    low_gain = parsed["low_volume"] / 100
    high = parsed["high_hz"]
    low = parsed["low_hz"]
    return (
        "[0:a]aformat=channel_layouts=stereo,volume=1[inst];"
        f"[1:a]aformat=channel_layouts=stereo,highpass=f={high},volume={high_gain}[hi];"
        f"[2:a]aformat=channel_layouts=stereo,lowpass=f={low},volume={low_gain}[lo];"
        "[inst][hi][lo]amix=inputs=3:normalize=0:duration=longest[out]"
    )


def normalize_mix_format(value: str) -> str:
    fmt = str(value or "wav").strip().lower().lstrip(".")
    if fmt not in MIX_FORMATS:
        raise JobError("Định dạng audio phải là wav, flac hoặc mp3.")
    return fmt


def render_mix(
    instrumental: Path,
    original: Path,
    destination: Path,
    settings: dict,
) -> Path:
    if shutil.which("ffmpeg") is None:
        raise JobError("Chưa có ffmpeg trong PATH.")
    if not instrumental.is_file():
        raise JobError(f"Không thấy instrumental đã tách: {instrumental.name}")
    if not original.is_file():
        raise JobError(f"Không thấy audio gốc: {original.name}")
    graph = mix_filtergraph(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix.lower()
    codec: list[str]
    if suffix == ".wav":
        codec = ["-c:a", "pcm_s16le"]
    elif suffix == ".flac":
        codec = ["-c:a", "flac"]
    elif suffix == ".mp3":
        codec = ["-c:a", "libmp3lame", "-q:a", "2"]
    else:
        raise JobError("Định dạng audio phải là wav, flac hoặc mp3.")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(instrumental),
        "-i",
        str(original),
        "-i",
        str(original),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        *codec,
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not destination.is_file():
        detail = result.stderr.strip().splitlines()
        message = detail[-1] if detail else "ffmpeg không trộn được audio."
        raise JobError(message)
    return destination

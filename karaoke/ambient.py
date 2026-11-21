"""Optional procedural ambience under a karaoke song (P1d).

The MVP keeps ambience **off** unless an effect plan explicitly sets
``params.audio = true`` on a rain/water/wind item. When it is on, the renderer
mixes this bed very low (-32 dB) under the vocal/instrumental so it can never
mask the music.

Everything here is stdlib ``wave`` + numpy: no scipy, no ffmpeg, no external
samples. Output is deterministic for a given ``(kind, duration, sample_rate,
seed)``, so renders are reproducible and cache-friendly.

Recipes:

* ``rain``  - band-passed noise with occasional short droplet transients.
* ``water`` - softer low-band noise with a slow, loop-safe swell.
* ``wind``  - heavily low-passed noise with slow gusting LFOs.

The bed is mono-compatible stereo 16-bit PCM. Audio is synthesised in chunks so
even a 10-minute bed stays well under a few hundred MB of working memory.

Beds are capped at ``MAX_DURATION`` (600 s). The renderer loops the file for
longer songs, so ambience never stops mid-song while disk use stays bounded.
"""

from __future__ import annotations

import hashlib
import math
import wave
from pathlib import Path

import numpy as np

KINDS = ("rain", "water", "wind")
MIN_DURATION = 0.5
MAX_DURATION = 600.0
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_SEED = 0

# Roughly -17 dBFS RMS before the renderer's -32 dB mix. Peaks stay well below
# full scale, so the bed can never clip the shared mix bus.
TARGET_RMS = 0.14
# Synthesise 5 s at a time; low memory and no audible level pumping on noise.
CHUNK_SECONDS = 5.0
FADE_SECONDS = 0.04


def _normalize_kind(kind: str) -> str:
    value = str(kind or "").strip().casefold()
    if value not in KINDS:
        raise ValueError(f"kind phải là một trong {', '.join(KINDS)}.")
    return value


def _clamp_duration(duration) -> float:
    try:
        seconds = float(duration)
    except (TypeError, ValueError) as error:
        raise ValueError("duration phải là số giây.") from error
    if not math.isfinite(seconds):
        raise ValueError("duration phải là số giây hữu hạn.")
    return min(MAX_DURATION, max(MIN_DURATION, seconds))


def _sample_rate(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("sample_rate phải là số nguyên dương.")
    rate = int(value)
    if rate <= 0 or rate != float(value):
        raise ValueError("sample_rate phải là số nguyên dương.")
    return rate


def _rng(kind: str, seed) -> np.random.Generator:
    try:
        value = int(seed)
    except (TypeError, ValueError) as error:
        raise ValueError("seed phải là số nguyên.") from error
    material = f"karaoke-ambient|{kind}|{value}".encode("utf-8")
    derived = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    return np.random.default_rng(derived)


def _highpass(freqs: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    """Butterworth-style high-pass magnitude response."""
    ratio = freqs / max(1e-6, cutoff)
    return ratio**order / np.sqrt(1.0 + ratio ** (2 * order))


def _lowpass(freqs: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    """Butterworth-style low-pass magnitude response."""
    ratio = freqs / max(1e-6, cutoff)
    return 1.0 / np.sqrt(1.0 + ratio ** (2 * order))


def _response(kind: str, freqs: np.ndarray) -> np.ndarray:
    if kind == "rain":
        # Broad hiss with a little more body than pure white noise.
        return _highpass(freqs, 320.0, 2) * _lowpass(freqs, 9000.0, 2)
    if kind == "water":
        # Softer, rounder bed: most energy under ~1.5 kHz.
        return _highpass(freqs, 110.0, 2) * _lowpass(freqs, 1600.0, 2)
    # wind: deep rumble, very little high end.
    return _highpass(freqs, 28.0, 2) * _lowpass(freqs, 420.0, 3)


def _filter(noise: np.ndarray, kind: str, sample_rate: int) -> np.ndarray:
    """Zero-phase-ish band shaping per chunk. Chunks are noise, so per-chunk
    circular filtering is perceptually seamless and keeps memory flat."""
    frames = noise.shape[0]
    freqs = np.fft.rfftfreq(frames, 1.0 / sample_rate)
    spectrum = np.fft.rfft(noise, axis=0)
    spectrum *= _response(kind, freqs)[:, None]
    filtered = np.fft.irfft(spectrum, n=frames, axis=0)
    return np.asarray(filtered, dtype=np.float32)


def _cycle_frequency(seconds: float, target_hz: float) -> float:
    """Quantize an LFO to whole cycles over the bed so its start/end match."""
    cycles = max(1, int(round(seconds * target_hz)))
    return cycles / seconds


def _slow_lfo(
    kind: str,
    seconds: float,
    chunk_start: float,
    frames: int,
    sample_rate: int,
) -> np.ndarray:
    """Global-time LFO. Whole cycles over ``seconds`` keep every chunk in phase
    and make the bed's start and end line up."""
    t = chunk_start + np.arange(frames, dtype=np.float32) / sample_rate
    if kind == "water":
        frequency = _cycle_frequency(seconds, 0.08)
        swell = 0.5 + 0.5 * np.sin(2.0 * math.pi * frequency * t)
        # Softer noise + slow swell: 0.55 gain at the trough, 1.0 at the crest.
        return (0.55 + 0.45 * swell).astype(np.float32)
    if kind == "wind":
        gust = _cycle_frequency(seconds, 0.07)
        ripple = _cycle_frequency(seconds, 0.19)
        slow = 0.5 + 0.5 * np.sin(2.0 * math.pi * gust * t)
        fast = 0.5 + 0.5 * np.sin(2.0 * math.pi * ripple * t + math.pi / 2.0)
        return (0.35 + 0.45 * slow + 0.20 * fast).astype(np.float32)
    # Rain is fairly steady; a shallow swell keeps it from sounding synthetic.
    frequency = _cycle_frequency(seconds, 0.04)
    swell = 0.5 + 0.5 * np.sin(2.0 * math.pi * frequency * t)
    return (0.88 + 0.12 * swell).astype(np.float32)


def _add_droplets(
    data: np.ndarray,
    rng: np.random.Generator,
    chunk_seconds: float,
    sample_rate: int,
) -> None:
    """Add sparse short decaying blips. They read as droplets, not melody."""
    count = int(rng.poisson(max(0.0, chunk_seconds) * 3.0))
    if count <= 0:
        return
    frames = data.shape[0]
    for _ in range(count):
        start = float(rng.uniform(0.0, max(0.0, chunk_seconds - 0.12)))
        length = int(sample_rate * float(rng.uniform(0.045, 0.11)))
        begin = int(start * sample_rate)
        end = min(frames, begin + max(1, length))
        if end <= begin:
            continue
        t = np.arange(end - begin, dtype=np.float32) / sample_rate
        frequency = float(rng.uniform(1300.0, 3600.0))
        phase = float(rng.uniform(0.0, 2.0 * math.pi))
        decay = float(rng.uniform(0.010, 0.032))
        envelope = np.exp(-t / decay).astype(np.float32)
        tone = np.sin(2.0 * math.pi * frequency * t + phase).astype(np.float32)
        # A hint of noise keeps the drop from sounding like a sine beep.
        texture = rng.standard_normal(t.shape).astype(np.float32) * 0.25
        drop = (tone + texture) * envelope * float(rng.uniform(0.5, 1.1))
        pan = float(rng.uniform(0.25, 0.75))
        data[begin:end, 0] += drop * (1.0 - pan) * 2.0
        data[begin:end, 1] += drop * pan * 2.0


def _normalize(data: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64))))
    if rms > 1e-8:
        data = data * (TARGET_RMS / rms)
    return np.clip(data, -0.95, 0.95).astype(np.float32)


def _chunk(
    kind: str,
    seconds: float,
    chunk_start: float,
    frames: int,
    sample_rate: int,
    rng: np.random.Generator,
) -> np.ndarray:
    noise = rng.standard_normal((frames, 2)).astype(np.float32)
    if kind == "rain":
        _add_droplets(noise, rng, frames / sample_rate, sample_rate)
    shaped = _filter(noise, kind, sample_rate)
    shaped *= _slow_lfo(kind, seconds, chunk_start, frames, sample_rate)[:, None]
    shaped = _normalize(shaped)

    # De-click the very start/end of the bed without audible pumping.
    fade = int(round(FADE_SECONDS * sample_rate))
    fade = max(0, min(fade, frames // 2))
    if fade:
        if chunk_start <= 0.0:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
            shaped[:fade] *= ramp
        if chunk_start + frames / sample_rate >= seconds - 1e-6:
            ramp = np.linspace(1.0, 0.0, fade, dtype=np.float32)[:, None]
            shaped[-fade:] *= ramp
    return shaped


def synthesize(
    kind: str,
    duration: float,
    destination: Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    seed: int = DEFAULT_SEED,
) -> Path:
    """Write a stereo 16-bit PCM WAV ambience bed and return ``destination``.

    ``duration`` is clamped to ``[0.5, 600]`` seconds. ``kind`` must be one of
    ``rain``, ``water`` or ``wind``. Output is deterministic per
    ``(kind, duration, sample_rate, seed)``.
    """
    normalized_kind = _normalize_kind(kind)
    seconds = _clamp_duration(duration)
    rate = _sample_rate(sample_rate)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)

    rng = _rng(normalized_kind, seed)
    total_frames = max(1, int(round(seconds * rate)))
    chunk_frames = max(1, int(round(CHUNK_SECONDS * rate)))

    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        written = 0
        while written < total_frames:
            frames = min(chunk_frames, total_frames - written)
            chunk_start = written / rate
            audio = _chunk(normalized_kind, seconds, chunk_start, frames, rate, rng)
            samples = np.rint(np.clip(audio, -1.0, 1.0) * 32767.0)
            handle.writeframes(samples.astype("<i2").tobytes())
            written += frames
    return target


__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "DEFAULT_SEED",
    "KINDS",
    "MAX_DURATION",
    "MIN_DURATION",
    "synthesize",
]

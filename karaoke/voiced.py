"""RMS-based voiced-span detection for Whisper clip timestamps.

``voiced_spans`` turns the per-hop RMS levels produced by ``_vocal_rms`` into
time spans that contain singing. ``flat_clip`` flattens those spans into the
``[start1, end1, start2, end2, ...]`` form expected by ``clip_timestamps`` on
both ``stable_whisper`` and ``mlx-whisper``.
"""

from __future__ import annotations

import math


def _merge_spans(
    spans: list[tuple[float, float]], min_gap: float
) -> list[tuple[float, float]]:
    """Merge spans whose gap is smaller than ``min_gap`` (negative = overlap)."""
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if start >= end:
            continue
        if merged and start - merged[-1][1] < min_gap:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _lowest_rms_cut(
    start: float,
    end: float,
    levels: list[float],
    hop: float,
    min_span: float,
) -> float | None:
    """Return a split time near the middle at the lowest RMS level."""
    if not levels or hop <= 0 or end - start <= 2.0 * min_span:
        return None

    middle = (start + end) / 2.0
    half_window = (end - start) * 0.25
    first_index = max(0, int(math.ceil((start + min_span) / hop)))
    last_index = min(len(levels) - 1, int((end - min_span) / hop))
    if first_index > last_index:
        return None

    # Prefer the central half of the span so the cut stays "near the middle".
    near_first = max(first_index, int(math.ceil((middle - half_window) / hop)))
    near_last = min(last_index, int(math.floor((middle + half_window) / hop)))

    def collect(low: int, high: int) -> list[tuple[float, float, int, float]]:
        found: list[tuple[float, float, int, float]] = []
        for index in range(low, high + 1):
            moment = index * hop
            if not (start + min_span <= moment <= end - min_span):
                continue
            found.append((abs(moment - middle), levels[index], index, moment))
        return found

    candidates = collect(near_first, near_last) if near_first <= near_last else []
    if not candidates:
        # Fall back to the whole safe range before giving up on the split.
        candidates = collect(first_index, last_index)
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[1], item[0], item[2]))[3]


def _split_long_spans(
    spans: list[tuple[float, float]],
    levels: list[float],
    hop: float,
    min_span: float,
    max_span: float,
) -> list[tuple[float, float]]:
    """Split spans longer than ``max_span`` without going below ``min_span``."""
    result: list[tuple[float, float]] = []
    pending = list(spans)
    while pending:
        start, end = pending.pop(0)
        if end - start <= max_span:
            result.append((start, end))
            continue
        cut = _lowest_rms_cut(start, end, levels, hop, min_span)
        if cut is None:
            # No safe cut point: keep the long span rather than losing audio.
            result.append((start, end))
            continue
        pending.insert(0, (cut, end))
        pending.insert(0, (start, cut))
    return result


def voiced_spans(
    levels: list[float],
    hop: float,
    *,
    threshold_ratio: float = 0.10,
    min_level: float = 80.0,
    min_gap: float = 0.35,
    pad: float = 0.30,
    min_span: float = 0.25,
    max_span: float = 120.0,
) -> list[tuple[float, float]]:
    """Return the time spans of a vocal stem that actually contain singing."""
    if not levels or hop <= 0:
        return []

    duration = len(levels) * hop
    if duration <= 0:
        return []

    peak = max(levels) if levels else 0.0
    threshold = max(min_level, peak * threshold_ratio)

    runs: list[tuple[float, float]] = []
    run_start: int | None = None
    for index, level in enumerate(levels):
        if level >= threshold:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            runs.append((run_start * hop, index * hop))
            run_start = None
    if run_start is not None:
        runs.append((run_start * hop, duration))

    runs = _merge_spans(runs, min_gap)

    padded = [
        (max(0.0, start - pad), min(duration, end + pad)) for start, end in runs
    ]
    # Padding can make two neighbouring spans overlap.
    spans = _merge_spans(padded, 0.0)
    spans = [(start, end) for start, end in spans if end - start >= min_span]

    voiced_total = sum(end - start for start, end in spans)
    if voiced_total < 0.05 * duration or voiced_total > 0.98 * duration:
        return [(0.0, duration)]

    return _split_long_spans(spans, levels, hop, min_span, max_span)


def flat_clip(spans: list[tuple[float, float]], duration: float) -> list[float]:
    """Flatten spans into ``[start, end, ...]`` for ``clip_timestamps``."""
    duration = max(0.0, float(duration))
    flat: list[float] = []
    for start, end in spans:
        start = max(0.0, min(float(start), duration))
        end = max(0.0, min(float(end), duration))
        if start >= end:
            continue
        flat.extend([start, end])

    if flat:
        # mlx-whisper #1256 hangs when the final end equals the exact duration.
        flat[-1] = min(flat[-1], max(0.0, duration - 0.1))
        if flat[-1] <= flat[-2]:
            del flat[-2:]
    return flat


def clip_for_whisper(
    spans: list[tuple[float, float]], duration: float
) -> list[float] | None:
    """Use clip_timestamps only when it is likely to save work.

    Many short spans make mlx-whisper slower (upstream #1285), so fall back to
    the full file when the voiced spans are fragmented or cover most of the song.
    """
    if not spans or duration <= 0:
        return None
    total = sum(max(0.0, end - start) for start, end in spans)
    if len(spans) > 12 or total < 0.05 * duration or total > 0.65 * duration:
        return None
    return flat_clip(spans, duration)

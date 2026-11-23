"""Approach B: Gemini line-level + lyrics matching + Whisper word-level.

Whisper runs once before Gemini. Gemini supplies coarse line windows; matching
is deterministic; words are fitted inside each matched line. Missing or
uncertain lines are left unsung instead of being forced.
"""

from __future__ import annotations

import math
from pathlib import Path

from karaoke.align import (
    AlignError,
    _align_tokens,
    _fold_token,
    _line_from_words,
    _vocal_rms,
    _word_dict,
    reconcile_lyrics_with_transcript,
    transcribe_whisper,
)
from karaoke.gemini_lines import GeminiLineError, transcribe_lines
from karaoke.line_match import match_lines
from karaoke.lyrics import extract_sung_lines

PAD = 0.8
SNAP_SHIFT = 0.20
MIN_WORD_COVERAGE = 0.5


def _words(segments: list[dict]) -> list[dict]:
    result: list[dict] = []
    for segment in segments or []:
        for word in segment.get("words") or []:
            text = " ".join(str(word.get("text") or "").split())
            if not text:
                continue
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                end = start + 0.08
            result.append({"text": text, "start": start, "end": end})
    result.sort(key=lambda item: item["start"])
    return result


def _mid(word: dict) -> float:
    return (float(word["start"]) + float(word["end"])) / 2.0


def _snap_time(moment: float, levels: list[float], hop: float, threshold: float) -> float:
    if not levels:
        return moment
    index = max(0, min(len(levels) - 1, int(round(moment / hop))))
    limit = max(1, int(SNAP_SHIFT / hop))
    best = None
    best_distance = None
    for distance in range(0, limit + 1):
        for candidate in (index - distance, index + distance):
            if 0 <= candidate < len(levels) and levels[candidate] >= threshold:
                best = candidate
                best_distance = distance
                break
        if best is not None:
            break
    if best is None:
        return moment
    return best * hop


def _spread(tokens: list[str], start: float, end: float) -> list[dict]:
    if not tokens:
        return []
    if end <= start:
        end = start + 0.08 * len(tokens)
    step = (end - start) / len(tokens)
    return [
        _word_dict(token, start + step * index, start + step * (index + 1))
        for index, token in enumerate(tokens)
    ]


def _words_for_line(
    text: str,
    start: float,
    end: float,
    heard: list[dict],
    levels: list[float],
    hop: float,
    threshold: float,
) -> list[dict]:
    tokens = text.split()
    if not tokens:
        return []
    if not heard:
        return _spread(tokens, start, end)
    pairs = _align_tokens(tokens, [word["text"] for word in heard])
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    for position, (heard_index, _score) in enumerate(pairs):
        if heard_index is None:
            continue
        word = heard[heard_index]
        times[position] = (word["start"], word["end"])

    first = next((index for index, value in enumerate(times) if value is not None), None)
    if first is None:
        return _spread(tokens, start, end)
    if first > 0:
        for offset, item in enumerate(_spread(tokens[:first], start, times[first][0])):
            times[offset] = (item["start"], item["end"])
    last = max(index for index, value in enumerate(times) if value is not None)
    if last < len(tokens) - 1:
        for offset, item in enumerate(_spread(tokens[last + 1 :], times[last][1], end)):
            times[last + 1 + offset] = (item["start"], item["end"])

    index = first
    while index < last:
        if times[index + 1] is not None:
            index += 1
            continue
        gap_end = index + 1
        while times[gap_end] is None:
            gap_end += 1
        for offset, item in enumerate(
            _spread(tokens[index + 1 : gap_end], times[index][1], times[gap_end][0])
        ):
            times[index + 1 + offset] = (item["start"], item["end"])
        index = gap_end

    result: list[dict] = []
    previous_end = start
    for token, time in zip(tokens, times):
        if time is None:
            continue
        word_start = max(start, min(time[0], end))
        word_end = max(word_start + 0.08, min(time[1], end))
        if word_start < previous_end:
            word_start = previous_end
        if word_end <= word_start:
            word_end = min(end, word_start + 0.08)
        if word_end <= word_start:
            continue
        word_start = _snap_time(word_start, levels, hop, threshold)
        word_end = _snap_time(word_end, levels, hop, threshold)
        if word_end <= word_start:
            word_end = word_start + 0.08
        result.append(_word_dict(token, word_start, word_end))
        previous_end = word_end
    return result or _spread(tokens, start, end)


def _line_confidence(line: dict, words: list[dict], ref: str) -> float:
    base = float(line.get("confidence") or 0.0)
    coverage = min(1.0, len(words) / max(1, len(ref.split())))
    return max(0.0, min(1.0, 0.7 * base + 0.3 * coverage))


def _cut_overlaps(lines: list[dict]) -> None:
    lines.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    for index in range(1, len(lines)):
        previous = lines[index - 1]
        current = lines[index]
        if float(current["start"]) >= float(previous["end"]):
            continue
        midpoint = (float(previous["end"]) + float(current["start"])) / 2.0
        previous["end"] = max(float(previous["start"]) + 0.08, midpoint)
        current["start"] = min(float(current["end"]) - 0.08, midpoint)
        for word in previous.get("words") or []:
            word["end"] = min(float(word["end"]), float(previous["end"]))
        for word in current.get("words") or []:
            word["start"] = max(float(word["start"]), float(current["start"]))


def _build_from_match(
    references: list[str],
    match: dict,
    whisper_words: list[dict],
    vocal_path: Path,
    log=None,
) -> list[dict]:
    def note(message: str) -> None:
        if log is not None:
            log(message)

    levels, hop = _vocal_rms(vocal_path)
    peak = max(levels) if levels else 0.0
    threshold = max(80.0, peak * 0.12)
    used: set[int] = set()
    lines: list[dict] = []

    for item in match.get("matched", []) + match.get("repeat", []):
        ref_index = item.get("reference_index")
        if ref_index is None or not 0 <= int(ref_index) < len(references):
            continue
        ref = references[int(ref_index)]
        start = float(item["start"])
        end = float(item["end"])
        core = [
            index
            for index, word in enumerate(whisper_words)
            if index not in used and start <= _mid(word) <= end
        ]
        for index in core:
            used.add(index)
        if len(core) < max(1, int(len(ref.split()) * MIN_WORD_COVERAGE)):
            for index, word in enumerate(whisper_words):
                if index in used:
                    continue
                if start - PAD <= _mid(word) <= end + PAD:
                    core.append(index)
                    used.add(index)
        heard = [whisper_words[index] for index in sorted(core)]
        words = _words_for_line(ref, start, end, heard, levels, hop, threshold)
        line_start = words[0]["start"] if words else start
        line_end = words[-1]["end"] if words else end
        confidence = _line_confidence(item, words, ref)
        line = _line_from_words(ref, words or _spread(ref.split(), start, end), uncertain=confidence < 0.7)
        line.update(
            {
                "reference_index": int(ref_index),
                "heard_text": item.get("heard_text", ""),
                "confidence": confidence,
                "text_similarity": item.get("text_similarity", 0.0),
                "status": item.get("status", "matched"),
            }
        )
        if item.get("status") == "repeat":
            line["repeated"] = True
            line["occurrence"] = item.get("occurrence", 1)
        if line_start < line_end:
            line["start"] = line_start
            line["end"] = line_end
        lines.append(line)

    for item in match.get("extra", []):
        start = float(item["start"])
        end = float(item["end"])
        text = " ".join(str(item.get("text") or "").split())
        core = [
            index
            for index, word in enumerate(whisper_words)
            if index not in used and start <= _mid(word) <= end
        ]
        for index in core:
            used.add(index)
        heard = [whisper_words[index] for index in sorted(core)]
        words = heard or _spread(text.split(), start, end)
        kind = item.get("kind", "extra")
        line = _line_from_words(text or "...", words, uncertain=kind != "nlv")
        line["status"] = "extra"
        if kind == "nlv":
            line["kind"] = "nlv"
            line["label"] = "chant" if len(text.split()) >= 2 else "adlib"
        lines.append(line)

    _cut_overlaps(lines)
    unsung = match.get("unsung") or []
    if unsung:
        note(f"{len(unsung)} câu không đủ tin cậy nên bỏ trống.")
    return lines


def align_with_gemini_lines(
    vocal_path: Path,
    lyrics: str,
    language: str,
    model_name: str,
    device: str,
    log,
    *,
    no_speech_threshold: float | None = None,
    whisper_retry: bool = True,
    gemini_model: str = "gemini-3.8-flash",
    gemini_start: float | None = None,
    gemini_end: float | None = None,
    use_cache: bool = True,
) -> list[dict]:
    """Run Approach B. Falls back to reconcile if Gemini/matching is unusable."""
    references = extract_sung_lines(lyrics)
    if not references:
        raise AlignError("Chưa có lời đã nhập để ghép chữ.")

    # Whisper first: its words calibrate Gemini chunks, provide a second opinion
    # for matching, and make the A fallback free if Gemini fails.
    segments = transcribe_whisper(
        vocal_path,
        language,
        model_name,
        device,
        log,
        no_speech_threshold=no_speech_threshold,
        whisper_retry=whisper_retry,
    )
    whisper_words = _words(segments)

    try:
        gemini_lines = transcribe_lines(
            vocal_path,
            lyrics,
            language=language,
            model=gemini_model,
            start=gemini_start,
            end=gemini_end,
            log=log,
            use_cache=use_cache,
        )
    except GeminiLineError as error:
        log(f"Gemini line pass lỗi ({error}). Dùng Whisper fallback.")
        return reconcile_lyrics_with_transcript(references, segments)

    match = match_lines(references, gemini_lines, whisper_words)
    if not match.get("matched") and not match.get("repeat"):
        log("Gemini không khớp được câu nào; dùng Whisper fallback.")
        return reconcile_lyrics_with_transcript(references, segments)

    lines = _build_from_match(references, match, whisper_words, vocal_path, log)
    if not lines:
        log("Không dựng được dòng nào từ kết quả Gemini; dùng Whisper fallback.")
        return reconcile_lyrics_with_transcript(references, segments)
    log(
        f"Approach B xong: {len(match.get('matched', []))} câu khớp, "
        f"{len(match.get('repeat', []))} lặp, {len(match.get('unsung', []))} bỏ trống."
    )
    return lines

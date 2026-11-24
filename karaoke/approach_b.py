"""Approach B: Gemini line-level + lyrics matching + Whisper word-level.

Whisper runs once before Gemini. Gemini supplies coarse line windows; matching
is deterministic; words are fitted inside each matched line. Missing or
uncertain lines are left unsung instead of being forced.
"""

from __future__ import annotations

import math
import unicodedata
from pathlib import Path

from karaoke.align import (
    AlignError,
    _align_tokens,
    _fold_token,
    _line_from_words,
    _plausible_heard,
    _vocal_rms,
    _word_dict,
    reconcile_lyrics_with_transcript,
    transcribe_whisper,
)
from karaoke.gemini_lines import GeminiLineError, transcribe_lines, transcribe_lines_chunked
from karaoke.gemini_models import DEFAULT_GEMINI_MODEL
from karaoke.line_match import match_lines
from karaoke.lyrics import extract_sung_lines
from karaoke.media import probe_duration

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
        # Snap first, then enforce order. Snapping a start can move it back by up
        # to SNAP_SHIFT; clamping before the snap could leave overlapping words.
        word_start = _snap_time(min(max(time[0], start), end), levels, hop, threshold)
        word_end = _snap_time(min(max(time[1], start), end), levels, hop, threshold)
        word_start = max(start, previous_end, word_start)
        if word_start > end - 0.08:
            word_start = max(start, end - 0.08)
        word_end = max(word_end, word_start + 0.08)
        if word_end > end:
            word_end = end if end > word_start else word_start + 0.08
        result.append(_word_dict(token, word_start, word_end))
        previous_end = word_end
    return result or _spread(tokens, start, end)


def _line_confidence(line: dict, words: list[dict], ref: str) -> float:
    base = float(line.get("confidence") or 0.0)
    coverage = min(1.0, len(words) / max(1, len(ref.split())))
    return max(0.0, min(1.0, 0.7 * base + 0.3 * coverage))


def _apply_window_boundaries(lines: list[dict]) -> None:
    """Blend Gemini line boundaries with word boundaries without moving lines.

    Ends are adjusted first. A Gemini end may extend a held note, but only if it
    stays before the next word start. Then starts may use the Gemini start when
    it fills a real gap after the previous adjusted end. This fixes the easy-song
    case where the first Whisper word starts about 0.2s late.
    """
    lines.sort(key=lambda item: (float(item["start"]), float(item["end"])))
    word_starts = [float(item["start"]) for item in lines]
    for index, line in enumerate(lines):
        word_end = float(line["end"])
        gemini_end = line.pop("window_end", None)
        next_word_start = word_starts[index + 1] if index + 1 < len(lines) else None
        if (
            gemini_end is not None
            and float(gemini_end) > word_end + 0.10
            and (next_word_start is None or float(gemini_end) <= next_word_start - 0.02)
        ):
            line["end"] = float(gemini_end)

    previous_end = 0.0
    for line in lines:
        word_start = float(line["start"])
        gemini_start = line.pop("window_start", None)
        start = word_start
        early = word_start - float(gemini_start) if gemini_start is not None else 0.0
        if (
            gemini_start is not None
            and 0.10 < early <= 0.35
            and float(gemini_start) >= previous_end + 0.02
        ):
            start = float(gemini_start)
        line["start"] = max(start, previous_end)
        previous_end = float(line["end"])


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


def _plausible_extra(text: str) -> bool:
    """Reject ASR noise that is not a pronounceable Latin/Vietnamese word."""
    if not _plausible_heard(text):
        return False
    folded = "".join(
        char
        for char in unicodedata.normalize("NFD", text.casefold())
        if char.isalpha() and not unicodedata.combining(char)
    )
    return len(folded) >= 2 and any(char in "aeiouy" for char in folded)


def _voiced_ratio(
    levels: list[float],
    hop: float,
    start: float,
    end: float,
    threshold: float,
) -> float:
    if not levels or hop <= 0:
        return 1.0
    first = max(0, int(start / hop))
    last = min(len(levels), int(end / hop) + 1)
    if last <= first:
        return 0.0
    voiced = sum(1 for level in levels[first:last] if level >= threshold)
    return voiced / (last - first)


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
    dropped_extras = 0

    planned: list[tuple[float, dict, str, int]] = []
    for item in match.get("matched", []) + match.get("repeat", []):
        ref_index = item.get("reference_index")
        if ref_index is None or not 0 <= int(ref_index) < len(references):
            continue
        planned.append(
            (float(item["start"]), item, references[int(ref_index)], int(ref_index))
        )
    planned.sort(key=lambda entry: (entry[0], float(entry[1]["end"])))

    # Pass 1: every matched line claims the words inside its Gemini window.
    # Overlapping windows must not let an earlier line steal a later line's core
    # words through the padded fallback below.
    claims: list[tuple[float, float, dict, str, int, list[int]]] = []
    for start, item, ref, ref_index in planned:
        end = float(item["end"])
        core = [
            index
            for index, word in enumerate(whisper_words)
            if index not in used and start <= _mid(word) <= end
        ]
        for index in core:
            used.add(index)
        claims.append((start, end, item, ref, ref_index, core))

    # Pass 2: after all cores are claimed, pad only lines that are still short.
    for start, end, item, ref, ref_index, core in claims:
        if len(core) < max(1, int(len(ref.split()) * MIN_WORD_COVERAGE)):
            for index, word in enumerate(whisper_words):
                if index in used:
                    continue
                if start - PAD <= _mid(word) <= end + PAD:
                    core.append(index)
                    used.add(index)
            core.sort()

    for start, end, item, ref, ref_index, core in claims:
        heard = [whisper_words[index] for index in core]
        words = _words_for_line(ref, start, end, heard, levels, hop, threshold)
        line_start = words[0]["start"] if words else start
        line_end = words[-1]["end"] if words else end
        confidence = _line_confidence(item, words, ref)
        line = _line_from_words(ref, words or _spread(ref.split(), start, end), uncertain=confidence < 0.7)
        line.update(
            {
                "reference_index": ref_index,
                "heard_text": item.get("heard_text", ""),
                "confidence": confidence,
                "text_similarity": item.get("text_similarity", 0.0),
                "status": item.get("status", "matched"),
                "window_start": start,
                "window_end": end,
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
        # Keep the saved text identical to the word list, otherwise
        # normalize_edited_lines rebuilds it from words and the line changes.
        spoken = " ".join(str(word["text"]) for word in words).strip()
        kind = item.get("kind", "extra")
        if kind != "nlv":
            dropped_extras += 1
            continue
        voiced = _voiced_ratio(levels, hop, start, end, threshold)
        if not spoken or voiced < 0.5 or not _plausible_extra(spoken):
            dropped_extras += 1
            continue
        line = _line_from_words(spoken, words, uncertain=False)
        line["status"] = "extra"
        line["kind"] = "nlv"
        line["label"] = "chant" if (end - start) >= 2.5 and voiced >= 0.6 else "adlib"
        lines.append(line)

    _apply_window_boundaries(lines)
    _cut_overlaps(lines)
    unsung = match.get("unsung") or []
    if unsung:
        note(f"{len(unsung)} câu không đủ tin cậy nên bỏ trống.")
    if dropped_extras:
        note(f"Bỏ {dropped_extras} đoạn hát nghi vấn (không đủ rõ để thành câu).")
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
    gemini_model: str = DEFAULT_GEMINI_MODEL,
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
        if gemini_start is None and gemini_end is None and gemini_lines:
            total = probe_duration(vocal_path)
            covered = (
                max(float(item["end"]) for item in gemini_lines)
                - min(float(item["start"]) for item in gemini_lines)
            ) / max(1.0, total)
            if covered < 0.55 or len(gemini_lines) < max(3, int(len(references) * 0.7)):
                log(
                    f"Gemini chỉ phủ {covered * 100:.0f}% bài; chạy lại theo chunk."
                )
                gemini_lines = transcribe_lines_chunked(
                    vocal_path,
                    lyrics,
                    language=language,
                    model=gemini_model,
                    log=log,
                    use_cache=use_cache,
                )

        match = match_lines(references, gemini_lines, whisper_words)
        if not match.get("matched") and not match.get("repeat"):
            log("Gemini không khớp được câu nào; dùng Whisper fallback.")
            return reconcile_lyrics_with_transcript(references, segments)
        matched_count = len(match.get("matched", [])) + len(match.get("repeat", []))
        if matched_count < len(references) * 0.6:
            log(
                f"Chỉ ghép được {matched_count}/{len(references)} câu. "
                "Kiểm tra lời nhập và audio; các câu còn lại sẽ để trống."
            )

        lines = _build_from_match(references, match, whisper_words, vocal_path, log)
        if not lines:
            log("Không dựng được dòng nào từ kết quả Gemini; dùng Whisper fallback.")
            return reconcile_lyrics_with_transcript(references, segments)
    except GeminiLineError as error:
        log(f"Gemini line pass lỗi ({error}). Dùng Whisper fallback.")
        return reconcile_lyrics_with_transcript(references, segments)
    except Exception as error:
        # Matching/word-fit errors must not bubble up to align_job: that would
        # run Whisper a second time. Reuse the transcript already in memory.
        log(f"Approach B lỗi ({error}); dùng Whisper fallback.")
        return reconcile_lyrics_with_transcript(references, segments)

    log(
        f"Approach B xong: {len(match.get('matched', []))} câu khớp, "
        f"{len(match.get('repeat', []))} lặp, {len(match.get('unsung', []))} bỏ trống."
    )
    return lines

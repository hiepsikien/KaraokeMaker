from __future__ import annotations

import math
import unicodedata
import wave
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from karaoke.lyrics import extract_sung_lines, normalize_token
from karaoke.media import MediaError, is_pcm16_wav, pcm16_wav


class AlignError(RuntimeError):
    pass


def _word_dict(text: str, start: float, end: float) -> dict:
    if end <= start:
        end = start + 0.08
    return {"text": text, "start": float(start), "end": float(end)}


def _words_from_segment(segment) -> list[dict]:
    words = []
    for word in segment.words or []:
        text = str(word.word).strip()
        if text:
            words.append(_word_dict(text, word.start, word.end))
    if words:
        return words
    text = str(getattr(segment, "text", "") or "").strip()
    if not text:
        return []
    return [_word_dict(text, segment.start, segment.end)]


def _line_from_words(
    text: str,
    words: list[dict],
    uncertain: bool = False,
    supplemented: bool = False,
) -> dict:
    line = {
        "text": text,
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": words,
    }
    if uncertain:
        line["uncertain"] = True
    if supplemented:
        line["supplemented"] = True
    return line


def _carry_line(source: dict, words: list[dict]) -> dict:
    return _line_from_words(
        source["text"],
        words,
        uncertain=bool(source.get("uncertain")),
        supplemented=bool(source.get("supplemented")),
    )


_GAP_LIMIT = 1.5
_SHORT_WORD = 0.28
_LONG_WORD = 2.4


def _typical_duration(words: list[dict]) -> float:
    durations = [word["end"] - word["start"] for word in words]
    healthy = [item for item in durations if _SHORT_WORD <= item <= _LONG_WORD]
    if not healthy:
        return 0.4
    healthy.sort()
    return healthy[len(healthy) // 2]


def repair_stretched_words(words: list[dict]) -> tuple[list[dict], int]:
    """Pull words that were parked across a long instrumental gap back onto the sung phrase."""
    if not words:
        return [], 0
    repaired: list[dict] = [dict(words[0])]
    moved = 0
    for source in words[1:]:
        word = dict(source)
        previous = repaired[-1]
        gap = word["start"] - previous["end"]
        duration = word["end"] - word["start"]
        if gap > _GAP_LIMIT:
            typical = _typical_duration(repaired)
            if duration < _SHORT_WORD or duration > _LONG_WORD:
                duration = max(typical, _SHORT_WORD)
            word["start"] = previous["end"] + 0.06
            word["end"] = word["start"] + duration
            moved += 1
        repaired.append(_word_dict(word["text"], word["start"], word["end"]))
    return repaired, moved


def _loosen_packed(words: list[dict], limit: float | None = None) -> list[dict]:
    """Give packed words enough time for the gold sweep to follow the voice."""
    if len(words) < 2:
        return words
    floor = 0.42
    start = words[0]["start"]
    durations = [max(word["end"] - word["start"], floor) for word in words]
    if limit is not None:
        available = limit - start
        if available < 0.12 * len(words):
            return words
        total = sum(durations)
        if total > available:
            scale = available / total
            durations = [item * scale for item in durations]
    cursor = start
    loosened: list[dict] = []
    for word, duration in zip(words, durations):
        loosened.append(_word_dict(word["text"], cursor, cursor + duration))
        cursor += duration
    return loosened


def _vocal_rms(vocal_path: Path, hop: float = 0.03) -> tuple[list[float], float]:
    try:
        with pcm16_wav(vocal_path) as wav_path:
            with wave.open(str(wav_path), "rb") as handle:
                channels = handle.getnchannels()
                width = handle.getsampwidth()
                rate = handle.getframerate()
                frames = handle.readframes(handle.getnframes())
    except MediaError as error:
        raise AlignError(str(error)) from error
    if width != 2 or rate <= 0 or channels <= 0:
        raise AlignError("File vocal không phải WAV 16-bit.")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float64)
    if channels > 1:
        usable = (samples.size // channels) * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    hop_count = max(1, int(hop * rate))
    window_count = max(0, samples.size // hop_count - 1)
    if window_count == 0:
        return [], hop
    windowed = samples[: window_count * hop_count].reshape(window_count, hop_count)
    levels = np.sqrt(np.mean(windowed * windowed, axis=1))
    return levels.tolist(), hop


def _voice_bounds(levels: list[float], hop: float, origin: float, limit: float) -> tuple[float, float, float] | None:
    """Return the sung phrase that contains this line, stopping at a real silence."""
    if not levels:
        return None
    start_index = max(0, int((origin - 0.25) / hop))
    end_index = min(len(levels) - 1, int(limit / hop))
    if end_index <= start_index:
        return None
    window = levels[start_index : end_index + 1]
    peak = max(window)
    if peak < 80:
        return None
    threshold = peak * 0.12
    active = [index for index, level in enumerate(window) if level >= threshold]
    if not active:
        return None
    runs: list[tuple[int, int]] = []
    run_start = previous = active[0]
    for index in active[1:]:
        if (index - previous) * hop > 0.55:
            runs.append((run_start, previous))
            run_start = index
        previous = index
    runs.append((run_start, previous))
    origin_index = int(origin / hop) - start_index
    chosen = runs[0]
    for run in runs:
        if run[0] - 4 <= origin_index <= run[1] + 8:
            chosen = run
            break
    finish = chosen[1]
    for run in runs:
        if run[0] > finish and (run[0] - finish) * hop < 0.7:
            finish = run[1]
    return (start_index + chosen[0]) * hop, (start_index + finish + 1) * hop, threshold


def _place_into_span(words: list[dict], start: float, end: float) -> list[dict]:
    """Put every word of the line inside the time the voice is actually singing."""
    span = max(0.12 * len(words), end - start)
    weights: list[float] = []
    for word in words:
        duration = word["end"] - word["start"]
        inside = start - 0.2 <= word["start"] <= end + 0.2 and 0.08 <= duration <= 1.6
        weights.append(duration if inside else -1.0)
    known = [item for item in weights if item > 0]
    typical = sorted(known)[len(known) // 2] if known else span / len(words)
    weights = [item if item > 0 else typical for item in weights]
    scale = span / sum(weights)
    cursor = start
    placed: list[dict] = []
    for word, weight in zip(words, weights):
        duration = max(0.08, weight * scale)
        placed.append(_word_dict(word["text"], cursor, cursor + duration))
        cursor += duration
    if placed:
        placed[-1]["end"] = start + span
    return placed


def _absorb_voiced_gaps(words: list[dict], levels: list[float], hop: float, threshold: float) -> list[dict]:
    """A gap that is still loud is the singer holding the previous word."""
    adjusted = [dict(word) for word in words]
    for index in range(1, len(adjusted)):
        gap_start = adjusted[index - 1]["end"]
        gap_end = adjusted[index]["start"]
        if gap_end - gap_start < 0.45:
            continue
        first = max(0, int(gap_start / hop))
        last = min(len(levels), int(gap_end / hop))
        if last <= first:
            continue
        voiced = sum(1 for level in levels[first:last] if level >= threshold)
        if voiced / (last - first) >= 0.55:
            adjusted[index - 1]["end"] = gap_end
    return [_word_dict(word["text"], word["start"], word["end"]) for word in adjusted]


def fit_lines_to_voice(lines: list[dict], vocal_path: Path, log=None) -> list[dict]:
    """Keep each lyric on screen for the note that is actually being sung."""

    def note(message: str) -> None:
        if log is not None:
            log(message)

    if not is_pcm16_wav(vocal_path):
        note(f"{vocal_path.name} không phải WAV 16-bit. Đang chuyển tạm để đo tiếng hát.")
    levels, hop = _vocal_rms(vocal_path)
    fitted: list[dict] = []
    quiet = 0
    short = 0
    for index, line in enumerate(lines):
        words = line.get("words") or []
        if not words:
            continue
        next_start = lines[index + 1]["start"] if index + 1 < len(lines) else words[0]["start"] + 12
        bounds = _voice_bounds(levels, hop, words[0]["start"], min(next_start - 0.04, words[0]["start"] + 12))
        if bounds is None:
            quiet += 1
            fitted.append(line)
            continue
        active_start, active_end, threshold = bounds
        if fitted:
            active_start = max(active_start, fitted[-1]["end"])
        if active_end - active_start < 0.18 * len(words):
            short += 1
            fitted.append(line)
            continue
        outside = any(word["start"] > active_end + 0.25 for word in words)
        word_span = words[-1]["end"] - words[0]["start"]
        active_span = active_end - active_start
        if outside or word_span > active_span * 1.45:
            words = _place_into_span(words, active_start, active_end)
        else:
            words = _absorb_voiced_gaps(words, levels, hop, threshold)
            if active_end > words[-1]["end"] + 0.22:
                words[-1] = _word_dict(words[-1]["text"], words[-1]["start"], min(active_end, words[-1]["end"] + 2.5))
        fitted.append(_carry_line(line, words))
    parts: list[str] = []
    if quiet:
        parts.append(f"{quiet} câu vì không thấy đoạn hát đủ lớn")
    if short:
        parts.append(f"{short} câu vì đoạn hát ngắn hơn số chữ")
    if parts:
        note("Giữ nguyên " + ", ".join(parts) + ".")
    return fitted


def loosen_against_next_line(lines: list[dict]) -> list[dict]:
    loosened: list[dict] = []
    for index, line in enumerate(lines):
        words = line["words"]
        if line.get("repaired") and index + 1 < len(lines):
            words = _loosen_packed(words, lines[index + 1]["start"] - 0.08)
        elif line.get("repaired"):
            words = _loosen_packed(words)
        if words:
            loosened.append(_carry_line(line, words))
    return loosened


def _match_lines(lines: list[str], timed_words: list[dict]) -> list[dict]:
    cursor = 0
    matched: list[dict] = []
    for line in lines:
        tokens = line.split()
        words: list[dict] = []
        for token in tokens:
            if cursor < len(timed_words):
                source = timed_words[cursor]
                cursor += 1
                words.append(_word_dict(token, source["start"], source["end"]))
            elif words:
                tail = words[-1]
                words.append(_word_dict(token, tail["end"], tail["end"] + 0.12))
            else:
                words.append(_word_dict(token, 0.0, 0.12))
        if words:
            matched.append(_line_from_words(line, words, uncertain=True))
    return matched


def _spread_tokens(tokens: list[str], start: float, end: float) -> list[dict]:
    """Place the user's words evenly across the time Whisper gave the line."""
    if not tokens:
        return []
    if end <= start:
        end = start + 0.12 * len(tokens)
    span = (end - start) / len(tokens)
    return [
        _word_dict(token, start + span * index, start + span * (index + 1))
        for index, token in enumerate(tokens)
    ]


def _segments_match_lines(lines: list[str], segments) -> list[dict] | None:
    if len(segments) != len(lines):
        return None
    matched: list[dict] = []
    for line, segment in zip(lines, segments):
        words = _words_from_segment(segment)
        tokens = line.split()
        uncertain = False
        if len(words) == len(tokens) and all(
            normalize_token(word["text"]) == normalize_token(token)
            for word, token in zip(words, tokens)
        ):
            words = [
                _word_dict(token, word["start"], word["end"])
                for token, word in zip(tokens, words)
            ]
        elif len(words) == len(tokens):
            words = [
                _word_dict(token, word["start"], word["end"])
                for token, word in zip(tokens, words)
            ]
            uncertain = True
        else:
            if not words:
                return None
            words = _spread_tokens(tokens, words[0]["start"], words[-1]["end"])
            uncertain = True
        if not words:
            return None
        matched.append(_line_from_words(line, words, uncertain=uncertain))
    return matched


def _fold_token(text: str) -> str:
    folded = unicodedata.normalize("NFD", normalize_token(text))
    return "".join(char for char in folded if unicodedata.category(char) != "Mn")


def _token_score(lyric: str, heard: str) -> float:
    left = _fold_token(lyric)
    right = _fold_token(heard)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if len(left) >= 2 and len(right) >= 2 and (left in right or right in left):
        shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
        if len(shorter) / len(longer) >= 0.75:
            return 0.85
    ratio = SequenceMatcher(None, left, right).ratio()
    return ratio if ratio >= 0.72 else 0.0


def _window_score(tokens: list[str], words: list[dict]) -> float:
    """How well this lyric line matches a heard phrase, in order."""
    if not tokens or not words or abs(len(words) - len(tokens)) > 2:
        return 0.0
    if _token_score(tokens[0], words[0]["text"]) < 0.72:
        return 0.0
    if _token_score(tokens[-1], words[-1]["text"]) < 0.72:
        return 0.0
    cursor = 0
    scores: list[float] = []
    hits = 0
    for token in tokens:
        best = 0.0
        best_at = cursor
        for index in range(cursor, len(words)):
            score = _token_score(token, words[index]["text"])
            if score > best:
                best = score
                best_at = index
                if score == 1.0:
                    break
        scores.append(best)
        if best >= 0.72:
            hits += 1
            cursor = best_at + 1
    if hits < max(1, (len(tokens) + 1) // 2):
        return 0.0
    return sum(scores) / len(tokens)


def _find_heard_window(tokens: list[str], words: list[dict], cursor: int, threshold: float) -> tuple[int, int, float] | None:
    """Earliest heard phrase that matches this lyric line. Later repeats stay for later lines."""
    for start in range(cursor, len(words)):
        best_score = 0.0
        best_end = start + 1
        for length in range(max(1, len(tokens) - 1), len(tokens) + 3):
            end = start + length
            if end > len(words):
                break
            score = _window_score(tokens, words[start:end])
            if score > best_score:
                best_score = score
                best_end = end
        if best_score >= threshold:
            return start, best_end, best_score
    return None


def _place_unheard(lines: list[str], placed: list[dict | None]) -> list[dict]:
    """Keep lyric lines Whisper did not match, just before the next line that did match."""
    index = 0
    while index < len(lines):
        if placed[index] is not None:
            index += 1
            continue
        run_end = index
        while run_end < len(lines) and placed[run_end] is None:
            run_end += 1
        previous_end = placed[index - 1]["end"] if index and placed[index - 1] is not None else 0.0
        next_start = placed[run_end]["start"] if run_end < len(lines) and placed[run_end] is not None else None
        durations = [max(0.35 * max(1, len(lines[item].split())), 0.8) for item in range(index, run_end)]
        total = sum(durations) + 0.15 * max(0, len(durations) - 1)
        cursor = previous_end + 0.4 if next_start is None else max(previous_end + 0.15, next_start - total - 0.2)
        for offset, duration in enumerate(durations):
            tokens = lines[index + offset].split()
            placed[index + offset] = _line_from_words(
                lines[index + offset],
                _spread_tokens(tokens, cursor, cursor + duration),
                uncertain=True,
            )
            cursor += duration + 0.15
        index = run_end
    return [line for line in placed if line is not None]


def _supplement_unmatched(segments: list[dict], used: set[int]) -> list[dict]:
    """Heard phrases that the typed lyrics never claimed: backing vocals, tags, endings."""
    extras: list[dict] = []
    for segment in segments:
        words = segment["words"]
        unused = [word for word in words if word["id"] not in used]
        if not unused:
            continue
        duration = unused[-1]["end"] - unused[0]["start"]
        if len(unused) < 2 and duration < 1.2:
            continue
        if len(unused) / len(words) < 0.6:
            continue
        text = " ".join(word["text"] for word in unused)
        extras.append(_line_from_words(text, [_word_dict(word["text"], word["start"], word["end"]) for word in unused], uncertain=True, supplemented=True))
    return extras


def reconcile_lyrics_with_transcript(lines: list[str], segments: list[dict]) -> list[dict]:
    """Time the typed lyrics from what Whisper heard, and keep heard lines the lyrics omitted.

    Typed lyrics win on wording wherever a phrase matches. A long silence before the first
    real match is left empty instead of pulling the verse back to the start of the file.
    """
    flat: list[dict] = []
    heard_segments: list[dict] = []
    for segment in segments:
        words = []
        for word in segment.get("words") or []:
            text = " ".join(str(word.get("text", "")).split())
            if not text:
                continue
            heard = _word_dict(text, float(word["start"]), float(word["end"]))
            heard["id"] = len(flat)
            flat.append(heard)
            words.append(heard)
        if words:
            heard_segments.append({"words": words})
    if not flat:
        raise AlignError("Whisper không nghe thấy lời nào trong file vocal.")

    placed: list[dict | None] = [None] * len(lines)
    used: set[int] = set()
    cursor = 0
    for index, line in enumerate(lines):
        tokens = line.split()
        if not tokens:
            continue
        found = _find_heard_window(tokens, flat, cursor, 0.55)
        if found is None:
            continue
        start, end, score = found
        window = flat[start:end]
        if len(tokens) == len(window):
            words = [_word_dict(token, word["start"], word["end"]) for token, word in zip(tokens, window)]
        else:
            words = _spread_tokens(tokens, window[0]["start"], window[-1]["end"])
        placed[index] = _line_from_words(line, words, uncertain=score < 0.75)
        used.update(word["id"] for word in window)
        cursor = end

    if not any(item is not None for item in placed):
        raise AlignError(
            "Whisper có nghe thấy tiếng hát, nhưng không khớp câu nào với lời đã nhập. Hãy kiểm tra lời và file vocal."
        )

    lyric_lines = _place_unheard(lines, placed)
    extras = _supplement_unmatched(heard_segments, used)
    combined = lyric_lines + extras
    combined.sort(key=lambda item: (item["start"], 1 if item.get("supplemented") else 0))
    return combined


def _allow_mps_alignment() -> None:
    """Whisper's DTW casts the matrix to float64. MPS cannot do that in place."""
    import whisper.timing as timing

    if getattr(timing.dtw, "_karaoke_mps_safe", False):
        return

    original = timing.dtw

    def dtw(x):
        device_type = getattr(getattr(x, "device", None), "type", None)
        if device_type == "mps":
            x = x.detach().float().cpu()
        return original(x)

    dtw._karaoke_mps_safe = True
    timing.dtw = dtw


def align_lyrics(
    vocal_path: Path,
    lyrics: str,
    language: str,
    model_name: str,
    device: str,
    log,
) -> list[dict]:
    lines = extract_sung_lines(lyrics)
    if not lines:
        raise AlignError("Không còn câu hát nào sau khi bỏ các dòng ghi chú trong [ngoặc].")

    log(f"Có {len(lines)} câu hát. Đang tải model {model_name} ({device}).")
    log("Lần đầu máy sẽ tải model về, có thể mất vài phút.")

    import stable_whisper

    try:
        model = stable_whisper.load_model(model_name, device=device)
    except Exception as error:
        if device != "cpu":
            log(f"Không dùng được {device} ({error}). Chuyển sang CPU.")
            model = stable_whisper.load_model(model_name, device="cpu")
        else:
            raise AlignError(f"Không tải được model căn lời: {error}") from error

    _allow_mps_alignment()
    prompt = " ".join(lines)[:220]
    log("Đang nghe vocal bằng Whisper, rồi đối chiếu với lời đã nhập.")
    try:
        result = model.transcribe(
            str(vocal_path),
            language=language or None,
            verbose=None,
            word_timestamps=True,
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            regroup=True,
        )
    except Exception as error:
        if device == "cpu":
            raise AlignError(f"Nghe vocal thất bại: {error}") from error
        log(f"GPU Apple không nghe được ({error}). Đang nghe lại trên CPU.")
        model = stable_whisper.load_model(model_name, device="cpu")
        try:
            result = model.transcribe(
                str(vocal_path),
                language=language or None,
                verbose=None,
                word_timestamps=True,
                temperature=0.0,
                condition_on_previous_text=False,
                initial_prompt=prompt or None,
                regroup=True,
            )
        except Exception as cpu_error:
            raise AlignError(f"Nghe vocal thất bại: {cpu_error}") from cpu_error

    if result is None:
        raise AlignError("Whisper không trả về lời nghe được.")

    heard_segments = []
    for segment in result.segments or []:
        words = _words_from_segment(segment)
        if words:
            heard_segments.append({"words": words})
    try:
        matched = reconcile_lyrics_with_transcript(lines, heard_segments)
    except AlignError:
        raise
    except Exception as error:
        raise AlignError(f"Không đối chiếu được lời với bản nghe: {error}") from error

    try:
        matched = fit_lines_to_voice(matched, vocal_path, log)
    except (AlignError, wave.Error, EOFError) as error:
        log(f"Không chỉnh được câu theo tiếng hát ({error}). Giữ thời gian đã đối chiếu.")
    added = [line["text"] for line in matched if line.get("supplemented")]
    if added:
        log(f"Đã thêm {len(added)} câu Whisper nghe được mà lời nhập chưa có, để khít hát bè hoặc đoạn kết.")
    unsure = [line["text"] for line in matched if line.get("uncertain") and not line.get("supplemented")]
    if unsure:
        log(f"{len(unsure)} câu khớp chưa chắc. Trên trang sửa, những câu đó được đánh dấu để nghe lại.")

    log(
        f"Căn xong. Câu đầu lúc {matched[0]['start']:.1f}s, "
        f"câu cuối lúc {matched[-1]['end']:.1f}s."
    )
    return matched

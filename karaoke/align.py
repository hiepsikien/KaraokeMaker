from __future__ import annotations

import math
import wave
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


def _line_from_words(text: str, words: list[dict], uncertain: bool = False) -> dict:
    line = {
        "text": text,
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": words,
    }
    if uncertain:
        line["uncertain"] = True
    return line


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
    for index, line in enumerate(lines):
        words = line.get("words") or []
        if not words:
            continue
        next_start = lines[index + 1]["start"] if index + 1 < len(lines) else words[0]["start"] + 12
        bounds = _voice_bounds(levels, hop, words[0]["start"], min(next_start - 0.04, words[0]["start"] + 12))
        if bounds is None:
            note(
                f"Câu {index + 1}: giữ nguyên thời gian vì không thấy đoạn hát đủ lớn quanh {words[0]['start']:.1f}s "
                "(ngưỡng peak 80, tỉ lệ 0,12)."
            )
            fitted.append(line)
            continue
        active_start, active_end, threshold = bounds
        if fitted:
            active_start = max(active_start, fitted[-1]["end"])
        if active_end - active_start < 0.18 * len(words):
            note(f"Câu {index + 1}: giữ nguyên thời gian vì đoạn hát ngắn hơn số chữ.")
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
        fitted.append(_line_from_words(line["text"], words, uncertain=bool(line.get("uncertain"))))
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
            loosened.append(_line_from_words(line["text"], words, uncertain=bool(line.get("uncertain"))))
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
    log("Đang căn lời vào file vocal.")
    try:
        result = model.align(
            str(vocal_path),
            "\n".join(lines),
            language=language,
            original_split=True,
            verbose=None,
        )
    except Exception as error:
        if device == "cpu":
            raise AlignError(f"Căn lời thất bại: {error}") from error
        log(f"GPU Apple không căn được ({error}). Đang căn lại trên CPU.")
        model = stable_whisper.load_model(model_name, device="cpu")
        try:
            result = model.align(
                str(vocal_path),
                "\n".join(lines),
                language=language,
                original_split=True,
                verbose=None,
            )
        except Exception as cpu_error:
            raise AlignError(f"Căn lời thất bại: {cpu_error}") from cpu_error

    if result is None:
        raise AlignError("Bộ căn lời không trả về thời gian cho bài này.")

    segments = list(result.segments or [])
    matched = _segments_match_lines(lines, segments)
    if matched is None:
        log("Số đoạn căn được khác số câu hát. Đang ghép theo từng chữ, nên kiểm tra kỹ các câu được đánh dấu.")
        flat: list[dict] = []
        for segment in segments:
            flat.extend(_words_from_segment(segment))
        matched = _match_lines(lines, flat)

    if not matched:
        raise AlignError("Không ghép được thời gian với lời bài hát.")

    repaired: list[dict] = []
    moved = 0
    for line in matched:
        words, count = repair_stretched_words(line["words"])
        moved += count
        if words:
            repaired_line = _line_from_words(line["text"], words, uncertain=bool(line.get("uncertain")))
            if count:
                repaired_line["repaired"] = True
            repaired.append(repaired_line)
    matched = loosen_against_next_line(repaired)
    try:
        matched = fit_lines_to_voice(matched, vocal_path, log)
    except (AlignError, wave.Error, EOFError) as error:
        log(f"Không chỉnh được câu theo tiếng hát ({error}). Giữ thời gian căn được.")
    if moved:
        log(f"Đã kéo {moved} chữ bị đặt vào khoảng nhạc về sát câu hát.")
    unsure = [line["text"] for line in matched if line.get("uncertain")]
    if unsure:
        log(f"{len(unsure)} câu được ghép ước lượng. Trên trang sửa, những câu đó được đánh dấu để nghe lại.")

    log(
        f"Căn xong. Câu đầu lúc {matched[0]['start']:.1f}s, "
        f"câu cuối lúc {matched[-1]['end']:.1f}s."
    )
    return matched

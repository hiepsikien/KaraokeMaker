from __future__ import annotations

from pathlib import Path

from karaoke.lyrics import extract_sung_lines, normalize_token


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


def _line_from_words(text: str, words: list[dict]) -> dict:
    return {
        "text": text,
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "words": words,
    }


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
            matched.append(_line_from_words(line, words))
    return matched


def _segments_match_lines(lines: list[str], segments) -> list[dict] | None:
    if len(segments) != len(lines):
        return None
    matched: list[dict] = []
    for line, segment in zip(lines, segments):
        words = _words_from_segment(segment)
        tokens = line.split()
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
        if not words:
            return None
        matched.append(_line_from_words(line, words))
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
        log("Số đoạn căn được khác số câu hát, đang ghép theo từng chữ.")
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
            repaired.append(_line_from_words(line["text"], words))
    matched = repaired
    if moved:
        log(f"Đã kéo {moved} chữ bị đặt vào khoảng nhạc về sát câu hát.")

    log(
        f"Căn xong. Câu đầu lúc {matched[0]['start']:.1f}s, "
        f"câu cuối lúc {matched[-1]['end']:.1f}s."
    )
    return matched

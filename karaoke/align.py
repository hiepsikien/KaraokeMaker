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

    log(
        f"Căn xong. Câu đầu lúc {matched[0]['start']:.1f}s, "
        f"câu cuối lúc {matched[-1]['end']:.1f}s."
    )
    return matched

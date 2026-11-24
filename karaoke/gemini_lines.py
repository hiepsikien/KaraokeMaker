"""Gemini line-level transcript for Approach B.

Gemini is used only for line text + coarse start/end. Word timing is handled by
Whisper + local DP later. The existing director client is reused so there is
only one Gemini HTTP implementation in the app.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from karaoke.align import _vocal_rms
from karaoke.cache import cache_root
from karaoke.director import (
    DEFAULT_MODEL,
    DirectorError,
    _api_key,
    _extract_payload,
    _post_generate,
)
from karaoke.lyrics import extract_sung_lines
from karaoke.media import MediaError, pcm16_wav, probe_duration

ALLOWED_AUDIO_MODELS = frozenset({DEFAULT_MODEL})
PROMPT_VERSION = "align_lines_v1"

_LINE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "reference_index": {"type": "INTEGER"},
                    "heard_text": {"type": "STRING"},
                    "start": {"type": "NUMBER"},
                    "end": {"type": "NUMBER"},
                    "status": {
                        "type": "STRING",
                        "enum": ["matched", "repeat", "extra", "nlv", "unsung"],
                    },
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["heard_text", "start", "end"],
            },
        }
    },
    "required": ["lines"],
}


class GeminiLineError(RuntimeError):
    pass


def _prompt(lyrics: str, start: float, end: float) -> str:
    context = ""
    sung = extract_sung_lines(lyrics)
    if sung:
        numbered = "\n".join(
            f"[{index}] {line}"
            for index, line in enumerate(sung)
        )
        context = (
            "\n\nLời tham chiếu (có thể sai hoặc thiếu; audio là chuẩn):\n"
            f"{numbered}\n"
        )
    return f"""Bạn là bộ căn lời cho karaoke. Hãy nghe đoạn audio và trả JSON.

Audio là chuẩn. Lời tham chiếu chỉ để tham khảo vocabulary và thứ tự câu.
Không được bịa câu không có trong audio. Không copy lời tham chiếu nếu audio hát khác.
Chỉ trả những câu thực sự được hát trong đoạn {start:.2f}s–{end:.2f}s.
Thời gian tính bằng giây, tương đối so với đầu đoạn audio.
Mỗi câu cần: reference_index nếu xác định được, heard_text, start, end,
status (matched|repeat|extra|nlv|unsung), confidence 0..1.
Nếu không chắc, confidence thấp; không đoán bừa.{context}"""


def _audio_clip(vocal_path: Path, start: float | None, end: float | None) -> tuple[bytes, str, float]:
    """Return compressed clip bytes, mime type, and clip offset.

    Always encode to 16 kHz mono MP3 when ffmpeg exists. Sending the original
    WAV (often ~50 MB for a full song) makes Gemini skip large sections.
    """
    with pcm16_wav(vocal_path) as wav_path:
        offset = 0.0
        has_clip = start is not None and end is not None and end > start
        if has_clip:
            offset = max(0.0, float(start))
        if shutil.which("ffmpeg"):
            handle = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            handle.close()
            target = Path(handle.name)
            try:
                command = ["ffmpeg", "-y"]
                if has_clip:
                    command += ["-ss", f"{offset:.3f}", "-t", f"{max(0.1, float(end) - offset):.3f}"]
                command += [
                    "-i",
                    str(wav_path),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-b:a",
                    "48k",
                    str(target),
                ]
                subprocess.run(command, check=True, capture_output=True)
                return target.read_bytes(), "audio/mpeg", offset
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                target.unlink(missing_ok=True)
        if has_clip:
            with wave.open(str(wav_path), "rb") as handle:
                rate = handle.getframerate()
                channels = handle.getnchannels()
                width = handle.getsampwidth()
                first = max(0, min(handle.getnframes(), int(round(start * rate))))
                last = max(first, min(handle.getnframes(), int(round(end * rate))))
                handle.setpos(first)
                frames = handle.readframes(last - first)
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as out:
                out.setnchannels(channels)
                out.setsampwidth(width)
                out.setframerate(rate)
                out.writeframes(frames)
            return buffer.getvalue(), "audio/wav", float(start)
        data = Path(wav_path).read_bytes()
        return data, "audio/wav", 0.0


def _cache_path(audio: bytes, model: str, prompt: str) -> Path:
    digest = hashlib.sha256()
    digest.update(audio)
    digest.update(b"\0")
    digest.update(model.encode("utf-8"))
    digest.update(b"\0")
    digest.update(PROMPT_VERSION.encode("utf-8"))
    digest.update(b"\0")
    digest.update(prompt.encode("utf-8"))
    return cache_root("align") / f"lines-{digest.hexdigest()}.json"


def _normalize_lines(raw: dict, offset: float, clip_end: float) -> list[dict]:
    lines = raw.get("lines")
    if not isinstance(lines, list):
        raise GeminiLineError("Gemini không trả danh sách câu.")
    result: list[dict] = []
    for item in lines:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("heard_text") or "").split())
        if not text:
            continue
        try:
            start = float(item.get("start", 0)) + offset
            end = float(item.get("end", 0)) + offset
        except (TypeError, ValueError):
            continue
        if end <= start:
            end = start + 0.3
        start = max(offset, start)
        end = min(offset + clip_end, end)
        if end <= start:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        status = str(item.get("status") or "matched")
        if status == "omitted":
            status = "unsung"
        if status not in {"matched", "repeat", "extra", "nlv", "unsung"}:
            status = "matched"
        reference_index = item.get("reference_index")
        try:
            reference_index = int(reference_index)
        except (TypeError, ValueError):
            reference_index = None
        result.append(
            {
                "text": text,
                "start": start,
                "end": end,
                "status": status,
                "confidence": max(0.0, min(1.0, confidence)),
                "reference_index": reference_index,
            }
        )
    return result


def _dedupe_lines(lines: list[dict]) -> list[dict]:
    result: list[dict] = []
    for line in sorted(lines, key=lambda item: (float(item["start"]), float(item["end"]))):
        text = " ".join(str(line.get("text") or "").casefold().split())
        duplicate = None
        for index, existing in enumerate(result):
            existing_text = " ".join(str(existing.get("text") or "").casefold().split())
            overlap = min(float(existing["end"]), float(line["end"])) - max(
                float(existing["start"]), float(line["start"])
            )
            if overlap <= 0.05:
                continue
            if text and existing_text and (text in existing_text or existing_text in text):
                duplicate = index
                break
            if abs(float(existing["start"]) - float(line["start"])) < 1.0:
                duplicate = index
                break
        if duplicate is None:
            result.append(line)
            continue
        existing = result[duplicate]
        current_score = (len(str(line.get("text") or "")), float(line.get("confidence") or 0.0))
        existing_score = (
            len(str(existing.get("text") or "")),
            float(existing.get("confidence") or 0.0),
        )
        if current_score > existing_score:
            result[duplicate] = line
    return result


def plan_chunks(
    vocal_path: Path,
    *,
    target_seconds: float = 90.0,
    max_seconds: float = 120.0,
    min_seconds: float = 30.0,
    min_silence: float = 0.8,
) -> list[tuple[float, float]]:
    """Split at long vocal rests; fall back to fixed max-length chunks.

    Cutting in a rest avoids slicing a lyric line at a chunk boundary. A small
    overlap is kept around the cut so a breath at the edge is not lost.
    """
    try:
        total = probe_duration(vocal_path)
    except MediaError:
        return []
    if total <= max_seconds:
        return [(0.0, total)]
    try:
        levels, hop = _vocal_rms(vocal_path)
    except Exception:
        levels, hop = [], 0.03
    if not levels:
        chunks: list[tuple[float, float]] = []
        start = 0.0
        while start < total:
            end = min(total, start + max_seconds)
            chunks.append((start, end))
            if end >= total:
                break
            start = max(start + 1.0, end - 2.0)
        return chunks

    peak = max(levels) if levels else 0.0
    threshold = max(80.0, peak * 0.08)
    silent: list[tuple[float, float]] = []
    run_start = None
    for index, level in enumerate(levels):
        if level < threshold and run_start is None:
            run_start = index
        elif level >= threshold and run_start is not None:
            start_time = run_start * hop
            end_time = index * hop
            if end_time - start_time >= min_silence:
                silent.append((start_time, end_time))
            run_start = None
    if run_start is not None:
        start_time = run_start * hop
        end_time = total
        if end_time - start_time >= min_silence:
            silent.append((start_time, end_time))

    chunks = []
    start = 0.0
    while start < total:
        remaining = total - start
        if remaining <= max_seconds:
            chunks.append((start, total))
            break
        target = min(start + target_seconds, start + max_seconds)
        candidates = [
            (abs((left + right) / 2.0 - target) - min(4.0, right - left) * 0.15,
             (left + right) / 2.0)
            for left, right in silent
            if start + min_seconds <= (left + right) / 2.0 <= start + max_seconds
        ]
        if candidates:
            cut = min(candidates)[1]
            chunks.append((start, min(total, cut + 0.25)))
            start = max(start + 1.0, cut - 0.25)
        else:
            cut = min(total, start + max_seconds)
            chunks.append((start, cut))
            start = max(start + 1.0, cut - 2.0)
    return chunks


def transcribe_lines_chunked(
    vocal_path: Path,
    lyrics: str,
    *,
    language: str = "vi",
    model: str = DEFAULT_MODEL,
    chunk_seconds: float = 90.0,
    overlap: float = 4.0,
    log=None,
    use_cache: bool = True,
) -> list[dict]:
    chunks = plan_chunks(
        Path(vocal_path),
        target_seconds=chunk_seconds,
        max_seconds=max(chunk_seconds * 1.35, chunk_seconds + 20),
    )
    if not chunks:
        try:
            probe_duration(Path(vocal_path))
        except MediaError as error:
            raise GeminiLineError(str(error)) from error
        chunks = [(0.0, probe_duration(Path(vocal_path)))]
    lines: list[dict] = []
    for index, (start, end) in enumerate(chunks, start=1):
        if log is not None:
            log(f"Gemini chunk {index}/{len(chunks)}: {start:.1f}s–{end:.1f}s.")
        try:
            chunk = transcribe_lines(
                vocal_path,
                lyrics,
                language=language,
                model=model,
                start=start,
                end=end,
                use_cache=use_cache,
                log=None,
            )
        except GeminiLineError as error:
            if log is not None:
                log(f"Gemini chunk {index} lỗi: {error}")
            chunk = []
        lines.extend(chunk)
    return _dedupe_lines(lines)


def transcribe_lines(
    vocal_path: Path,
    lyrics: str,
    *,
    language: str = "vi",
    model: str = DEFAULT_MODEL,
    start: float | None = None,
    end: float | None = None,
    log=None,
    use_cache: bool = True,
    generate=None,
) -> list[dict]:
    """Return Gemini line-level transcript, with absolute timestamps."""
    if model not in ALLOWED_AUDIO_MODELS:
        raise GeminiLineError(f"Model Gemini không được phép dùng cho audio: {model}")
    try:
        audio, mime, offset = _audio_clip(Path(vocal_path), start, end)
        total_duration = probe_duration(Path(vocal_path))
    except MediaError as error:
        raise GeminiLineError(str(error)) from error
    clip_end = (end - offset) if end is not None else max(0.1, total_duration - offset)
    prompt = _prompt(lyrics, offset, offset + clip_end)
    cache_path = _cache_path(audio, model, prompt)
    if use_cache:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                return _normalize_lines(cached, offset, clip_end)
        except (OSError, json.JSONDecodeError):
            pass

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode("ascii")}},
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _LINE_SCHEMA,
            "temperature": 0.0,
            "maxOutputTokens": 32768,
        },
    }
    if log is not None:
        log(f"Gemini line pass: {offset:.1f}s–{offset + clip_end:.1f}s.")
    try:
        if generate is None:
            raw_response = _post_generate(model, payload, _api_key(), timeout=180)
        else:
            raw_response = generate(model, payload, _api_key())
        parsed = _extract_payload(raw_response, "align_lines")
    except DirectorError as error:
        raise GeminiLineError(str(error)) from error
    if use_cache:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return _normalize_lines(parsed, offset, clip_end)

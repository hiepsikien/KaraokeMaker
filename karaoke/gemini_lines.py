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
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from karaoke.align import _vocal_rms
from karaoke.cache import cache_root
from karaoke.director import (
    DirectorError,
    GeminiHTTPError,
    _api_key,
    _extract_payload,
    _post_generate,
)
from karaoke.gemini_models import AUDIO_MODELS, DEFAULT_GEMINI_MODEL
from karaoke.lyrics import extract_sung_lines
from karaoke.media import MediaError, pcm16_wav, probe_duration
from karaoke.line_match import sim_text

ALLOWED_AUDIO_MODELS = AUDIO_MODELS
DEFAULT_MODEL = DEFAULT_GEMINI_MODEL
PROMPT_VERSION = "align_lines_v2"

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
    def __init__(self, message: str, *, transient: bool = False, config: bool = False):
        super().__init__(message)
        self.transient = transient
        self.config = config


class GeminiBudget:
    """Shared call budget for one Gemini line request (or one chunk)."""

    def __init__(self, calls: int = 4):
        self.left = max(0, int(calls))

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _retry_after_seconds(attempt: int) -> float:
    base = 2.0 * (2 ** max(0, attempt - 1))
    return min(8.0, base)


def _post_generate_retry(
    model: str,
    payload: dict,
    api_key: str,
    *,
    budget: GeminiBudget,
    log=None,
    timeout: int = 180,
) -> dict:
    """Call Gemini with 429/5xx retries, sharing the caller's budget."""
    attempt = 0
    while budget.take():
        attempt += 1
        try:
            return _post_generate(model, payload, api_key, timeout=timeout)
        except GeminiHTTPError as error:
            if not error.transient or budget.left <= 0:
                raise
            raw_wait = float(
                getattr(error, "retry_after", None) or _retry_after_seconds(attempt)
            )
            wait = min(30.0, max(0.0, raw_wait))
            if log is not None:
                log(f"Gemini tạm lỗi ({error}); thử lại sau {wait:.0f}s.")
            time.sleep(wait)
    raise GeminiLineError("Gemini đã hết lượt thử cho đoạn này.")


def _prompt(lyrics: str, start: float, end: float) -> str:
    context = ""
    duration = max(0.1, end - start)
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
Đoạn audio chỉ dài {duration:.2f} giây. Mọi start/end phải nằm trong 0–{duration:.2f};
start phải nhỏ hơn end. Không trả mốc vượt quá độ dài đoạn.
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
    # Gemini may return lines out of chronological order. The matcher walks
    # heard columns in order, so unsorted input would swap text and timing.
    result.sort(key=lambda item: (item["start"], item["end"]))
    return result


def _timestamps_out_of_clip(raw: dict, clip_end: float) -> bool:
    """True when the response mostly used absolute song time instead of clip time.

    Gemini occasionally answers a 90s chunk with timestamps from the whole song
    (e.g. 102s inside a 94s clip). Those lines would be clamped away, silently
    losing the verse, so the caller must retry or fall back instead of caching.
    """
    lines = raw.get("lines")
    if not isinstance(lines, list) or not lines:
        return False
    bad = 0
    for item in lines:
        if not isinstance(item, dict):
            bad += 1
            continue
        try:
            start = float(item.get("start", 0))
            end = float(item.get("end", 0))
        except (TypeError, ValueError):
            bad += 1
            continue
        if (
            start < -0.5
            or start > clip_end + 0.5
            or end > clip_end + 0.5
            # Do not reject end <= start: _normalize_lines repairs zero-length
            # lines, and treating them as out-of-clip can force a needless fallback.
        ):
            bad += 1
    return bad * 2 >= len(lines)


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
            # Two different short lines can start within a second of each other.
            # Only merge when the text really is the same; never merge on time alone.
            if text and existing_text and sim_text(existing_text, text) >= 0.6:
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
    target_seconds: float = 60.0,
    max_seconds: float = 90.0,
    min_seconds: float = 30.0,
    min_silence: float = 0.8,
    levels_hop: tuple[list[float], float] | None = None,
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
    if levels_hop is not None:
        levels, hop = levels_hop
    else:
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
            chunks.append((start, min(total, cut + 0.75)))
            start = max(start + 1.0, cut - 0.75)
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
    chunk_seconds: float = 60.0,
    log=None,
    use_cache: bool = True,
    workers: int = 3,
    chunks: list[tuple[float, float]] | None = None,
) -> tuple[list[dict], list[tuple[int, str]]]:
    """Transcribe a long song in vocal-rest chunks, up to `workers` at once.

    Returns ``(lines, failures)``. A failed chunk does not discard the good
    chunks; the caller checks regional coverage and decides on fallback A.
    """
    try:
        with pcm16_wav(Path(vocal_path)) as wav_path:
            if chunks is None:
                chunks = plan_chunks(
                    wav_path,
                    target_seconds=chunk_seconds,
                    max_seconds=max(chunk_seconds * 1.35, chunk_seconds + 20),
                )
            if not chunks:
                chunks = [(0.0, probe_duration(wav_path))]
            results: list[list[dict] | None] = [None] * len(chunks)
            failures: list[tuple[int, str]] = []

            def run(index: int, start: float, end: float) -> None:
                budget = GeminiBudget(4)
                try:
                    results[index] = transcribe_lines(
                        wav_path,
                        lyrics,
                        language=language,
                        model=model,
                        start=start,
                        end=end,
                        use_cache=use_cache,
                        log=None,
                        budget=budget,
                    )
                    return
                except GeminiLineError as error:
                    first_error = str(error)
                # One fresh rerun of the same chunk, still within the budget.
                try:
                    if budget.left > 0:
                        results[index] = transcribe_lines(
                            wav_path,
                            lyrics,
                            language=language,
                            model=model,
                            start=start,
                            end=end,
                            use_cache=False,
                            log=None,
                            budget=budget,
                        )
                        return
                except GeminiLineError as error:
                    first_error = str(error)
                failures.append((index, first_error))

            worker_count = max(1, min(int(workers or 1), len(chunks)))
            if log is not None and worker_count > 1:
                log(
                    f"Gemini chunk song song: {worker_count} worker, "
                    f"{len(chunks)} đoạn."
                )
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = {
                    pool.submit(run, index, start, end): index
                    for index, (start, end) in enumerate(chunks)
                }
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        future.result()
                    except Exception as error:  # defensive: never orphan a worker
                        failures.append((index, str(error)))
                    if log is not None:
                        start, end = chunks[index]
                        status = "lỗi" if any(i == index for i, _ in failures) else "xong"
                        log(f"Gemini chunk {index + 1}/{len(chunks)} {status} "
                            f"({start:.1f}s–{end:.1f}s).")
    except MediaError as error:
        raise GeminiLineError(str(error)) from error
    lines = [line for part in results if part for line in part]
    return _dedupe_lines(lines), failures


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
    budget: GeminiBudget | None = None,
) -> list[dict]:
    """Return Gemini line-level transcript, with absolute timestamps."""
    budget = budget or GeminiBudget(4)
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
            if isinstance(cached, dict) and not _timestamps_out_of_clip(cached, clip_end):
                return _normalize_lines(cached, offset, clip_end)
            if isinstance(cached, dict):
                # Do not keep feeding the same invalid response back.
                cache_path.unlink(missing_ok=True)
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

    def request(body: dict) -> dict:
        if generate is None:
            raw_response = _post_generate_retry(
                model,
                body,
                _api_key(),
                budget=budget,
                log=log,
                timeout=180,
            )
        else:
            if not budget.take():
                raise GeminiLineError("Gemini đã hết lượt thử cho đoạn này.")
            raw_response = generate(model, body, _api_key())
        return _extract_payload(raw_response, "align_lines")

    try:
        parsed = request(payload)
        if _timestamps_out_of_clip(parsed, clip_end):
            if log is not None:
                log("Gemini trả mốc ngoài độ dài đoạn; thử lại một lần.")
            parts = list(payload["contents"][0]["parts"])
            parts[0] = {
                "text": (
                    f"{parts[0]['text']}\n\nLần trước bạn trả mốc ngoài 0–{clip_end:.2f}. "
                    "Chỉ dùng thời gian trong đoạn này."
                )
            }
            parsed = request({**payload, "contents": [{"role": "user", "parts": parts}]})
    except DirectorError as error:
        transient = bool(getattr(error, "transient", False))
        # A non-HTTP DirectorError (missing key, safety block, no candidate)
        # is not fixed by retrying chunks; fallback A instead.
        config = not isinstance(error, GeminiHTTPError) or not transient
        raise GeminiLineError(
            str(error), transient=transient, config=config
        ) from error
    if _timestamps_out_of_clip(parsed, clip_end):
        raise GeminiLineError("Gemini trả mốc ngoài độ dài đoạn audio.")
    if use_cache:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return _normalize_lines(parsed, offset, clip_end)

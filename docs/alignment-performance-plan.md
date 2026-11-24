# Kế hoạch tăng tốc ghép lời (B + D + E + F)

Trạng thái: **nghiên cứu / chưa implement**
Phạm vi: chỉ B, D, E, F. Không làm A (cache Whisper) và C (bỏ full-song).
Bối cảnh: Approach B đã là mặc định; mục tiêu là giảm thời gian "Ghép chữ lại"
mà không đổi chất lượng alignment.

---

## 0. Baseline hiện tại

Đo trên Apple M5, 24 GB, `stable_whisper` 2.19.1, Whisper `medium` CPU:

| Thành phần | Thời gian |
|---|---:|
| Whisper medium (4–5 phút audio) | 55–81 s |
| Gemini full-song | 8–30 s |
| Gemini chunked (5 đoạn) | ~57 s |
| Matching + word-fit + RMS | vài giây |
| **Tổng Tàn Hồng** | ~163 s |
| **Tổng Tên Người Ở Lại** | ~140 s |

Kết luận: nút thắt là **Whisper** và **Gemini**, không phải matching.

---

## 1. Mục tiêu

- Giảm thời gian ghép lần đầu từ ~140–163 s xuống **≤60–70 s**.
- Không tăng số câu sai, không tăng hallucination.
- Fallback A vẫn sẵn sàng; D chỉ đổi **điều kiện kích hoạt** (regional coverage),
  không bỏ fallback.
- Vẫn giữ được: matching DP, word ownership, RMS snap, metadata, repeat/NLV.
- Có thể bật/tắt từng phần bằng config để dễ rollback.

Không làm:

- Không cache transcript Whisper (A).
- Không bỏ full-song Gemini (C); vẫn giữ full-song trước, chunk khi coverage thấp.
- Không đổi sang Gemini-only cho word timing.

---

## 2. Kiến trúc mục tiêu

```text
                 ┌─────────────────────────────┐
                 │ 1. Chuẩn bị (nhanh, local)  │
                 │ duration, RMS, voiced spans  │
                 └──────────────┬──────────────┘
                                │
              ┌─────────────────┴──────────────────┐
              │                                    │
   ┌──────────▼──────────┐              ┌──────────▼──────────┐
   │ 2. Whisper (E+F)    │              │ 3. Gemini (D)       │
   │ mlx/stable + clips  │              │ full → chunks 1..N  │
   │ 1 worker            │              │ tối đa 3 concurrent │
   └──────────┬──────────┘              └──────────┬──────────┘
              │                                    │
              └─────────────────┬──────────────────┘
                                │
                 ┌──────────────▼──────────────┐
                 │ 4. Matching + word-fit       │
                 │ DP, ownership, RMS snap      │
                 └─────────────────────────────┘
```

Whisper và Gemini chạy **song song** (B). Gemini chunk chạy **tối đa 3 request
đồng thời** (D). Whisper chỉ nghe các đoạn có hát (F), ưu tiên backend MLX (E).

---

## 3. B — Chạy Whisper và Gemini song song

### 3.1. Vì sao

- Whisper dùng CPU/GPU local; Gemini dùng network. Hai bên gần như không tranh tài nguyên.
- Hiện tại thời gian = Whisper + Gemini. Chạy song song → `max(Whisper, Gemini)`.

### 3.2. Thiết kế

Chia làm 2 phase để không phá mục tiêu `max(Whisper, Gemini)`:

**Phase 1 — chạy song song full-song Gemini + Whisper**

```python
duration = probe_duration(vocal_path)
levels, hop = _vocal_rms(vocal_path)

# F chỉ bật khi whisper_vad != "off"
clip = None
spans = []
if whisper_vad == "rms":
    spans = voiced_spans(levels, hop)
    clip = _flat_clip(spans, duration)     # flat list + clamp, xem §6.3

with ThreadPoolExecutor(max_workers=2) as pool:
    whisper_future = pool.submit(
        transcribe_whisper,
        vocal_path, language, model_name, device, log,
        no_speech_threshold=no_speech_threshold,
        whisper_retry=whisper_retry,
        clip_timestamps=clip,              # None khi F off
    )
    gemini_future = pool.submit(
        transcribe_lines, ...              # chỉ full-song
    )

    segments = whisper_future.result()
    try:
        gemini_lines = gemini_future.result()
    except GeminiLineError as error:
        if is_config_error(error):         # key/sai model/blocked
            log("Gemini full-song lỗi cấu hình; fallback A.")
            return reconcile_lyrics_with_transcript(references, segments)
        log(f"Gemini full-song lỗi tạm thời ({error}); thử chunk.")
        gemini_lines = None                # đi tiếp Phase 2
```

**Phase 2 — coverage rồi mới chunk**

Coverage cần `whisper_words`, nên chỉ chạy sau Phase 1. Phase 2 chạy khi:

- `gemini_lines is None` (full-song lỗi tạm thời/content), **hoặc**
- `needs_chunk(...)` do coverage thấp.

```python
if gemini_lines is None or needs_chunk(gemini_lines, whisper_words, references, duration):
    gemini_lines, failures = transcribe_lines_chunked(..., workers=3)
    if failures and chunk_region_has_missing_lyrics(failures, chunks, whisper_words, gemini_lines):
        return reconcile_lyrics_with_transcript(references, segments)
```

- Full-song lỗi **cấu hình/key/blocked** → fallback A ngay.
- Full-song lỗi **content** (mốc ngoài clip) hoặc **5xx/timeout** → thử chunk trước.
- Nếu chunk cũng fail/coverage thiếu → fallback A.

Trade-off:

- Với bài full-song đủ tốt: tổng ≈ `max(Whisper, full-song) + fit` → đạt mục tiêu.
- Với bài phải chunk: tổng ≈ `max(Whisper, full-song) + chunked`, **không phải**
  `max(Whisper, chunked)`, vì coverage cần Whisper xong.
- Muốn giữ `max()` cả khi phải chunk thì phải quyết định chunk bằng tín hiệu
  không cần Whisper (duration, RMS, span), tức là đã là thay đổi kiểu C — không làm.
- Vì vậy bảng §7 phải tách rõ phase, không chỉ ghi một con số `max()`.

### 3.3. Xử lý lỗi

| Tình huống | Hành vi thực tế |
|---|---|
| Whisper lỗi | Raise `AlignError`; Gemini future **không huỷ được** nếu đang bay, nhưng kết quả bị bỏ. `ThreadPoolExecutor` khi thoát sẽ `wait=True`; nếu muốn thoát ngay dùng `shutdown(wait=False, cancel_futures=True)` và chấp nhận request đang chạy |
| Gemini lỗi cấu hình/key/blocked | Fallback A; **không chạy lại Whisper** |
| Gemini lỗi content/5xx/timeout | Thử chunk (D) trước; chunk cũng fail/coverage thiếu → fallback A |
| Gemini chậm hơn | Chờ; Whisper đã xong |
| Người dùng huỷ | Cần cờ cancel chung; dừng nhận kết quả, không ghi project |

**Quan trọng:** nếu Whisper lỗi, `align_job` hiện bắt `Exception` và gọi
`align_lyrics` → Whisper chạy lần 2. Cần đánh dấu lỗi "Whisper đã fail" (ví dụ
`WhisperFailed(AlignError)`) để `align_job` không fallback A, hoặc kiểm tra
`record` đã có transcript.

Phân loại lỗi Gemini: hiện `_post_generate` chỉ raise `DirectorError`. Cần thêm
`GeminiAuthError` / `GeminiTransientError` / `GeminiContentError` (hoặc parse
status code) để quyết định "fallback A" vs "thử chunk". Không dựa vào string
message.

### 3.4. Log

- Prefix `[Whisper]` / `[Gemini]` để không lẫn thứ tự.
- Tổng kết cuối: thời gian từng nhánh, số câu, số chunk, số retry.
- Với MLX, không dùng `_log_model_cache` (nó đọc cache PyTorch); log riêng
  "MLX model từ Hugging Face cache".

### 3.5. Rủi ro

- MLX/PyTorch trong thread: cần test trên server (job đã chạy trong worker thread).
- Log callback phải thread-safe (server đã dùng `_lock`; CLI print an toàn).
- **Model MLX được chia sẻ**: `load_mlx_model` có `@lru_cache(maxsize=4)`, không phải
  "mỗi job một lần load". Hai job align đồng thời có thể dùng chung model → cần
  `_WHISPER_LOCK` toàn cục hoặc serialize align job.
- MLX + `word_timestamps` có thể tăng RAM theo chunk (mlx-examples #1254); cần
  `mx.metal.set_cache_limit` và đo peak RAM trên 24 GB.

---

## 4. D — Chạy song song các chunk Gemini (tối đa 3)

### 4.1. Vì sao

5 chunk × ~12 s tuần tự = ~57 s. Với 3 worker: ~2 wave ≈ 20–25 s.

### 4.2. Thiết kế

```python
def transcribe_lines_chunked(..., max_workers: int = 3):
    chunks = plan_chunks(...)
    results: list[list[dict] | None] = [None] * len(chunks)
    failures: list[tuple[int, Exception]] = []

    def run(index: int, start: float, end: float) -> None:
        try:
            results[index] = transcribe_lines(..., start=start, end=end)
        except GeminiLineError as error:
            failures.append((index, error))

    with ThreadPoolExecutor(max_workers=min(max_workers, len(chunks))) as pool:
        futures = [pool.submit(run, i, s, e) for i, (s, e) in enumerate(chunks)]
        for future in as_completed(futures):
            future.result()

    lines = [line for part in results if part for line in part]
    return _dedupe_lines(lines), failures
```

- Giữ **thứ tự chunk** khi ghép; dedupe sau khi có đủ.
- `max_workers=3` mặc định; cho phép `KARAOKE_GEMINI_WORKERS`.
- Không chạy full-song song song với chunk; full-song trước, chunk sau (theo C không đổi).

### 4.3. Retry / rate limit

Chính sách: **một budget tổng cho mỗi chunk**, không nhân tầng.

```text
MAX_GEMINI_CALLS_PER_CHUNK = 4   # tổng số lần gọi HTTP, không phải mỗi lớp
```

Các lớp dùng chung budget:

1. **HTTP retry** (wrapper trong `gemini_lines`, không sửa `_post_generate`
   vì hàm này dùng chung cho director/ảnh/video):
   - 429 / 500 / 502 / 503 / 504 / timeout: thử lại với backoff 2/4/8 s + jitter.
   - Đọc `Retry-After` nếu có.
   - Không retry 400/401/403.
2. **Content retry** (đã có trong `transcribe_lines`): response có mốc ngoài clip
   → retry 1 lần với prompt hiệu chỉnh.
3. **Chunk rerun**: nếu vẫn lỗi, chạy lại chunk 1 lần với `use_cache=False`.

Mỗi lần gọi HTTP ở bất kỳ lớp nào đều trừ budget. Hết 4 lần → chunk fail,
không retry thêm. Bình thường mỗi chunk chỉ 1 lần.

Để tránh nhân tầng khi implement, dùng một object truyền xuyên suốt:

```python
class GeminiBudget:
    def __init__(self, calls: int = 4):
        self.left = calls
    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True
```

- `transcribe_lines(..., budget=...)` gọi `budget.take()` trước mỗi HTTP call,
  gồm content retry.
- HTTP wrapper cũng dùng cùng budget, không tự retry khi `take()` trả False.
- Full-song có budget riêng (ví dụ 3 calls), không dùng chung với chunk.

Ví dụ xấu nhất: 1 gốc + 1 HTTP retry + 1 content retry + 1 rerun = 4 calls.
Không còn trường hợp 2 lớp nhân nhau thành 8–16 calls.

### 4.3.1. Coverage theo **mọi chunk window**, không chỉ chunk lỗi

Coverage toàn bài không đủ. Nhưng cũng không đủ nếu chỉ kiểm tra chunk `raise`:
một chunk có thể "thành công" mà trả `[]` hoặc 1–2 câu → `failures` rỗng →
partial vẫn thiếu verse. Phải kiểm tra **tất cả chunk window** trên kết quả cuối:

```python
MIN_WORDS_IN_REGION = 3
MIN_VOICED_RATIO = 0.2

def region_has_missing_lyrics(start, end, whisper_words, levels, hop,
                              gemini_lines):
    region = [w for w in whisper_words if start <= _mid(w) <= end]
    covered = sum(
        1 for w in region
        if any(l["start"] - 1.0 <= _mid(w) <= l["end"] + 1.0 for l in gemini_lines)
    )
    if len(region) >= MIN_WORDS_IN_REGION:
        return covered / len(region) < 0.6
    # Whisper có thể bỏ sót verse khẽ; dùng RMS đã có làm tín hiệu phụ.
    voiced = _voiced_ratio(levels, hop, start, end)
    return voiced >= MIN_VOICED_RATIO
```

Quy tắc:

- Chạy cho **mọi chunk**, kể cả chunk trả rỗng/ít câu.
- Nếu bất kỳ chunk nào có `region_has_missing_lyrics == True`
  → **fallback A** cho cả bài.
- Nếu tất cả chunk đều im lặng/instrumental → giữ partial, log cảnh báo.
- Không bao giờ trả partial có vùng lời bị thiếu.
- Test phải có case **chunk trả `[]`**, không chỉ case chunk raise.
- Chỉ chạy regional check khi có `failures` hoặc có `unsung` **và** word coverage
  toàn bài <0.8; tránh false positive do Whisper hallucinate ở đoạn instrumental
  (Tàn Hồng: unsung=0, chunk outro có từ rác nhưng không fallback).

Whisper đã chạy song song nên fallback A không tốn thêm thời gian Whisper.

### 4.3.2. Contract mới

`transcribe_lines_chunked` trả `(lines, failures)` thay vì raise:

- Cập nhật caller `approach_b`.
- Cập nhật test `test_failed_chunk_is_reported...` (hiện expect raise) thành
  expect `(lines, failures)` và kiểm tra regional fallback.
- Snippet §4.2 phải gọi `_dedupe_lines(lines)` trước khi trả.

### 4.4. Cache

- Mỗi chunk vẫn cache theo audio + prompt như hiện tại.
- Ghi cache trong thread: mỗi chunk một file, không tranh nhau.
- Cache invalid bị xoá như hiện tại.

### 4.5. Rủi ro

- 429 khi 3 request đồng thời → cần retry/backoff và giới hạn 3.
- Tăng chi phí? Cùng số request; chỉ tăng concurrency.
- Thứ tự log lộn xộn → prefix `[Gemini chunk i/N]`.

---

## 5. E — mlx-whisper trên Apple Silicon

### 5.1. Tin tốt: stable-whisper đã hỗ trợ MLX

`stable_whisper` 2.19.1 có sẵn:

```python
from stable_whisper import load_mlx_whisper
model = load_mlx_whisper("medium")
```

`load_mlx_whisper` map tên model sang repo MLX:

| Tên | Repo |
|---|---|
| `small` | `mlx-community/whisper-small-mlx` |
| `medium` | `mlx-community/whisper-medium-mlx` |
| `large-v3` | `mlx-community/whisper-large-v3-mlx` |
| `large-v3-turbo` | `mlx-community/whisper-large-v3-turbo` |

Model wrapper giữ được `transcribe`, `align`, `refine`, `regroup`,
`suppress_silence`, `vad` của stable-ts. Vì vậy **không cần adapter word
timestamp riêng**: `_words_from_segment` hiện tại vẫn dùng được nếu
`model.transcribe()` trả `WhisperResult` như bản PyTorch.

Cần cài:

```bash
pip install mlx-whisper
```

(`mlx-whisper` 0.4.3 hỗ trợ `word_timestamps`, `clip_timestamps`, các decode option
như `temperature`, `condition_on_previous_text`, `no_speech_threshold`.)

### 5.2. Thiết kế backend

Thêm `karaoke/whisper_backend.py` (hoặc helper trong `align.py`):

```python
def load_whisper_model(model_name: str, device: str, engine: str, log):
    use_mlx = engine == "mlx" or (
        engine == "auto" and _mlx_available()
    )
    if use_mlx:
        try:
            log("Dùng mlx-whisper (Apple GPU).")
            # load_mlx_whisper mặc định float32; chỉ đổi fp16 sau khi pass parity.
            return load_mlx_whisper(model_name)
        except Exception as error:
            if engine == "mlx":
                log(f"mlx-whisper lỗi ({error}); fallback stable_whisper.")
            else:
                log(f"mlx-whisper không dùng được ({error}); fallback stable_whisper.")
    return stable_whisper.load_model(model_name, device=device)
```

**Precedence rõ ràng:**

- `whisper_engine=auto`:
  - Nếu macOS arm64 + import `mlx_whisper` OK → **MLX, bất kể `device=cpu`**.
  - Ngược lại → stable_whisper với `device` hiện có.
- `whisper_engine=mlx`: cố dùng MLX; lỗi thì fallback stable (không làm hỏng job).
- `whisper_engine=stable`: giữ nguyên hành vi cũ (`cpu`/`mps`).
- `device` chỉ còn ý nghĩa cho backend stable; UI hiện có CPU/GPU Apple vẫn dùng
  cho stable. Nếu chọn "GPU Apple" + `auto` trên Apple Silicon, MLX được ưu tiên.
- Không thêm `"auto"` vào `DEVICES`; `auto` là engine, không phải device.
- Cần setting/UI mới cho engine (mặc định `auto`), nếu không người dùng
  `device=cpu` sẽ không bao giờ nhận MLX.

**Dtype:**

- `load_mlx_whisper` mặc định `mx.float32` (`mlx_whisper.py:316-318`), không phải fp16.
- Plan không mặc định fp16. Nếu muốn fp16 phải truyền `dtype=mx.float16` và đo lại
  quality; chỉ bật sau khi pass gate.
- RAM/quality: float32 an toàn hơn trên 24 GB; fp16 nhanh hơn nhưng cần parity test.

- Không đổi output format: vẫn `[{"words": [{"text", "start", "end"}]}]`.

### 5.2.1. Optional hay bắt buộc?

| | Bắt buộc | Optional + auto-detect |
|---|---|---|
| Cài đặt | Mọi máy phải cài `mlx-whisper`/`mlx` | Chỉ cài trên Apple Silicon; máy khác không cần |
| Máy Intel / non-macOS | Có thể fail cài hoặc không chạy | Fallback `stable_whisper` |
| Code | Một đường duy nhất | Hai nhánh, nhưng nhánh MLX chỉ vài dòng |
| Rollback | Phải gỡ dependency | Tắt `whisper_engine=stable` hoặc gỡ package |
| Tải model | Vẫn tải riêng lần đầu | Như nhau |

**Đề xuất: optional ở mức package, nhưng auto-cài trên Apple Silicon.**

- Installer/script trên macOS arm64 cài `mlx-whisper` mặc định → người dùng M5
  không phải làm gì.
- Runtime vẫn `try/except import`; thiếu package → stable_whisper.
- Thêm setting `whisper_engine=auto|mlx|stable`; `auto` = MLX nếu có.
- CI/non-Apple không cần cài, test fallback.
- Nếu sau này chỉ hỗ trợ Apple Silicon, có thể chuyển thành bắt buộc.

Lý do không bắt buộc ngay: `mlx` chỉ chạy Apple Silicon; app vẫn có thể mở project
trên máy Intel/CI, và lỗi import không nên làm hỏng toàn bộ tính năng ghép lời.

### 5.3. Chất lượng

Cùng weights Whisper, nhưng khác implementation/decode (MLX float32 mặc định,
stable-ts postprocess). Phải đo:

- So với `stable_whisper medium` hiện tại trên 5 bài dev.
- Nếu lệch quá ngưỡng, thử `large-v3` hoặc `large-v3-turbo`.
- Không đổi default sang turbo chỉ vì nhanh; phải pass quality gate.

### 5.4. Kỳ vọng tốc độ

Tham khảo benchmark M5 / MLX:

| Model | Tốc độ ước tính | 4 phút audio |
|---|---:|---:|
| medium MLX | 10–15× real-time | 16–24 s |
| large-v3-turbo MLX | 20–30× real-time | 8–12 s |
| large-v3 MLX | 10–18× real-time | 13–24 s |

So với stable_whisper medium CPU (~3× real-time, 55–81 s), MLX medium nhanh
hơn khoảng **3–5×**. Turbo nhanh hơn nữa nhưng cần kiểm tra tiếng Việt.

### 5.5. Rủi ro / lưu ý

- Model MLX tải lần đầu (~1–3 GB) từ Hugging Face; cần cache và thông báo.
- Chỉ macOS Apple Silicon; máy khác fallback stable_whisper.
- `mlx-whisper` + `mlx` là optional extra; auto-cài trên Apple Silicon.
- MLX dùng GPU; nếu chạy song song với tác vụ GPU khác (Studio render) có thể tranh
  tài nguyên. Cần đo.
- `load_mlx_model` có `@lru_cache(maxsize=4)` → model được chia sẻ giữa các job,
  không phải load riêng mỗi job. Cần `_WHISPER_LOCK` hoặc serialize align để tránh
  race/thread-safety với MLX.
- `word_timestamps=True` + MLX có thể tăng RAM theo chunk (mlx-examples #1254);
  đặt `mx.metal.set_cache_limit` trong `try/except` (API/phiên bản MLX có thể khác),
  không hạ limit ảnh hưởng job khác, và đo peak RAM.

---

## 6. F — Chỉ Whisper những đoạn có hát

### 6.1. Vì sao

Nhiều bài có intro/instrumental/outro dài. Whisper hiện decode cả file, kể cả
im lặng. Chỉ nghe đoạn voiced giảm 20–50% thời gian với bài nhiều khoảng nghỉ
(Tàn Hồng, Tên Người), ít hơn với bài hát liên tục.

### 6.2. Phát hiện đoạn voiced

Ưu tiên RMS có sẵn (`_vocal_rms`), vì:

- vocal stem đã tách nên RMS phản ánh giọng hát khá tốt;
- không thêm dependency/model;
- cùng tín hiệu đang dùng cho snap word.

```python
def voiced_spans(levels, hop, *, threshold_ratio=0.10, min_level=80,
                 min_gap=0.35, pad=0.30, min_span=0.25, max_span=120.0):
    # threshold_ratio=0.10 chỉ dùng cho F (phát hiện span),
    # không đổi 0.08 của plan_chunks hay 0.12 của _build_from_match.
    peak = max(levels) if levels else 0.0
    threshold = max(min_level, peak * threshold_ratio)
    runs = ...  # các đoạn level >= threshold
    runs = merge(runs, gap < min_gap)       # gộp breath/ngắt ngắn
    runs = [pad(span, pad) for span in runs]
    runs = merge(runs, gap=0.0)             # pad có thể làm 2 span chồng nhau
    runs = [span for span in runs if (span[1] - span[0]) >= min_span]
    # nếu tổng voiced < 5% hoặc > 98% → fallback full file
    # nếu span > max_span → cắt tại điểm RMS thấp nhất
    return runs
```

- `peak` phải xử lý `levels` rỗng: `max(levels) if levels else 0.0`.
- `duration` phải định nghĩa `end - start`, không dùng biến ẩn.
- Pad **trước**, rồi merge/clamp lại để tránh 2 span chồng nhau.
- `pad` 0.25–0.40 s để không cắt onset/offset.
- `min_gap` 0.3–0.5 s để không cắt giữa câu.
- Nếu RMS không đáng tin (quá ít/quá nhiều voiced) → dùng full file.
- Throat growling/backing vocals sẽ được coi là voiced; F không lọc nội dung,
  chỉ bỏ khoảng lặng. Đây là giới hạn chấp nhận.

**Threshold:** không đổi trong phạm vi B/D/E/F. Giữ nguyên hiện tại:
`plan_chunks` dùng `peak*0.08`, `_build_from_match` dùng `peak*0.12`. Nếu muốn
thống nhất (ví dụ 0.10), làm experiment riêng + quality gate, không gộp vào PR
performance. `_vocal_rms` chỉ tính **một lần** và truyền `(levels, hop)` vào:
`plan_chunks`, `voiced_spans`, `_build_from_match` (không đổi công thức).

### 6.3. Cách đưa spans vào Whisper

**Cách 1 — `clip_timestamps` (ưu tiên)**

Cả `mlx-whisper` và `stable_whisper` đều nhận **flat list**
`[start1, end1, start2, end2, ...]` (hoặc string comma-separated), **không phải**
`list[(start, end)]`. Timestamp trả về là **tuyệt đối** theo file gốc.

```python
def _flat_clip(spans, duration: float) -> list[float]:
    flat: list[float] = []
    for start, end in spans:
        start = max(0.0, min(start, duration - 0.05))
        end = max(start + 0.05, min(end, duration))
        if end - start < 0.05:
            continue
        flat.extend([start, end])
    if flat:
        # clamp một chiều: pad 0.30 có thể đẩy end vượt duration
        flat[-1] = min(flat[-1], max(0.0, duration - 0.1))
    return flat

flat = _flat_clip(spans, duration)
model.transcribe(
    str(vocal_path),
    language=language,
    word_timestamps=True,
    condition_on_previous_text=False,
    regroup=True,
    clip_timestamps=flat,
)
```

- `stable_whisper` tự cắt `clip_timestamps[i:i+2]`; truyền tuple lồng sẽ sai.
- Mọi cặp phải có `start < end` sau pad/merge; nếu không, bỏ cặp đó.
- mlx-whisper #1256: treo khi `end` cuối **bằng đúng** duration. Đã fix ở 0.4.2
  nhưng vẫn clamp một chiều để an toàn.
- mlx-whisper #1285: thời gian xử lý bất thường với clip ngắn; benchmark riêng,
  không giả định F luôn nhanh hơn.
- mlx-whisper #1254: RAM tăng khi `word_timestamps`; đặt cache limit và đo.

Ưu điểm: không tạo file tạm, không lệch offset.
Cần test: `stable_whisper` + `clip_timestamps` + `regroup` trả đúng timestamp
tuyệt đối và không làm hỏng word ordering.

**Mặc định F = off** cho đến khi pass quality gate; sau đó mới flip `whisper_vad=rms`.

**Cách 2 — cắt file tạm (fallback)**

Nếu backend không hỗ trợ tốt `clip_timestamps`:

- ffmpeg cắt từng span (16 kHz mono WAV).
- Transcribe từng span.
- Cộng `span.start` vào mọi word timestamp.
- Ghép segments, sort, dedupe.

Cách này chắc chắn nhưng tốn I/O và cần xử lý offset cẩn thận.

### 6.4. Tương tác với Gemini

- Whisper spans và Gemini chunks độc lập; không cần trùng boundary.
- Gemini vẫn dùng `plan_chunks` hiện tại (target 60 s, cắt tại nghỉ).
- `_vocal_rms` chỉ tính **một lần**, dùng cho cả F và `_build_from_match`.

### 6.5. Rủi ro

- Cắt mất chữ ở biên nếu pad quá nhỏ → cần test với các bài có onset mạnh.
- Câu hát rất khẽ hoặc growl có thể bị loại nếu threshold cao.
- `clip_timestamps` + `condition_on_previous_text=False` có thể làm giảm ngữ cảnh
  giữa các đoạn; chấp nhận vì đã cắt tại nghỉ.
- VAD không phân biệt hát và tiếng động; chỉ dùng RMS là đủ cho vocal stem.

### 6.6. Tùy chọn Silero VAD

Nếu RMS không ổn định trên một số bài:

- `stable-ts` hỗ trợ `vad=True, vad_threshold=0.35, vad_onnx=True`.
- Có thể dùng làm chế độ `whisper_vad = silero`.
- Silero chạy CPU, thêm model nhỏ; cần đo lại vì VAD cho speech có thể bỏ sót
  đoạn hát/chant.
- Khuyến nghị: khi bật F, dùng **RMS** trước; Silero VAD là fallback thử nghiệm.
  Mặc định F vẫn **off** đến khi pass quality gate.

---

## 7. Tổng hợp hiệu năng kỳ vọng

Giả định bài 4–5 phút, có 30–50% khoảng nghỉ, và **phải fallback chunk**
(trường hợp phổ biến với bài dài). Công thức: `max(Whisper, full-song) + chunked + fit`.

| Cấu hình | Whisper | Gemini chunk (sau full-song) | Tổng (ước tính) |
|---|---:|---:|---:|
| Hiện tại (serial, stable CPU) | 55–81 s | 30–57 s | 140–163 s |
| B + D (stable CPU) | 55–81 s | 20–25 s | 80–110 s |
| B + D + F (stable CPU) | 35–55 s | 20–25 s | 60–85 s |
| B + D + F + E medium MLX | 12–20 s | 20–25 s | **35–50 s** |
| B + D + F + E turbo MLX | 6–12 s | 20–25 s | **30–42 s** |

Cột “Gemini chunk” **không** gồm full-song: full-song nằm trong `max(Whisper, full-song)`
ở Phase 1. Nếu full-song lỗi và phải chunk, full-song vẫn có thể tốn 8–30 s
(budget/retry riêng).

Nếu full-song đủ tốt (không cần chunk), tổng = `max(Whisper, full-song) + fit`:

| Cấu hình | Tổng (ước tính) |
|---|---:|
| B + D + F + E medium MLX | 20–30 s |
| B + D + F + E turbo MLX | 12–22 s |

Overhead chưa tính: load model MLX lần đầu (~2–5 s), tải model (~1–3 GB lần đầu),
và `fit`/matching (~2–5 s). Bảng §7 chỉ là ước tính; phải benchmark thật.

Sau khi Whisper nhanh, **Gemini là nút thắt**; D rất quan trọng.

---

## 8. Quality gate

### 8.1. Bộ test

- Dev: Quả Cam, Bài Ca, Văn Lang, Tàn Hồng, Tên Người.
- Hold-out: Buồm Đỏ, Hà Nội Sáu Mươi Ngày Đêm, Đại Việt.
- Gold: line timing đã export/curated; so theo `reference_index`.
- Đo bằng `scripts/eval_b_alignment.py` (mở rộng thêm chế độ so sánh backend).

### 8.2. Ngưỡng

| Hạng mục | Ngưỡng |
|---|---|
| Line start MAE so baseline | tăng ≤0.05 s |
| Line start P90 | tăng ≤0.10 s và vẫn ≤0.5 s |
| Line end MAE/P90 | tăng ≤0.10 s |
| Word onset P90 (matched words) | lệch ≤0.10 s so stable medium |
| Text similarity transcript | ≥0.95 |
| Số câu mất / unsung | không tăng |
| Hallucination/repeat | không tăng |
| Tổng thời gian/bài | giảm ≥35% so baseline |
| Peak RAM (MLX medium, 24 GB) | ≤10 GB; large-v3 ≤14 GB |
| Số request Gemini | không tăng trong trường hợp bình thường; chỉ tăng khi retry lỗi |
| Lỗi 429 | không tăng sau retry |
| Độ ổn định | chạy 3 lần, lấy median; temperature tuple không gây lệch lớn |

Nếu MLX medium fail, thử `large-v3` hoặc `large-v3-turbo` trước khi bỏ E.

---

## 9. Cấu hình / feature flag

| Config | Mặc định | Ý nghĩa |
|---|---|---|
| `whisper_engine` | `auto` | `auto` = MLX nếu Apple Silicon + import được, **bất kể device=cpu**; ngược lại stable |
| `whisper_vad` | `off` (bật sau gate) | `off` / `rms` / `silero` |
| `whisper_clip_pad` | `0.30` | pad quanh voiced span |
| `gemini_chunk_workers` | `3` | số chunk Gemini đồng thời |
| `align_parallel` | `true` | chạy Whisper/Gemini song song |
| `whisper_mlx_dtype` | `float32` | chỉ đổi `float16` sau parity test |

Có thể đọc từ env để benchmark mà không đổi UI. `device` chỉ dùng cho stable backend.

Pin dependency: `mlx-whisper>=0.4.3,<0.5` để tránh behavior drift của `clip_timestamps`;
`mlx` theo phiên bản đi kèm.

---

## 10. Test cần thêm

1. `voiced_spans`:
   - gộp gap ngắn, pad, min/max span;
   - fallback full file khi voiced <5% hoặc >98%;
   - span cắt tại điểm RMS thấp nhất;
   - pad không tạo span chồng nhau.
2. `clip_timestamps`:
   - **flat list** `[s1,e1,s2,e2]`, không phải tuple lồng;
   - timestamp trả về tuyệt đối, không lệch offset;
   - word order tăng dần;
   - câu ở biên span không bị mất;
   - clamp `end` cuối < duration (mlx #1256).
3. D:
   - 3 chunk chạy song song vẫn giữ đúng thứ tự;
   - dedupe overlap;
   - 1 chunk lỗi/`[]` ở **vùng có lời** → fallback A;
   - 1 chunk lỗi/`[]` ở **vùng im lặng** → giữ partial;
   - regional check chạy cho **mọi chunk**, không chỉ chunk raise;
   - retry 429 không làm treo job; budget 4 calls/chunk dùng chung object.
4. B:
   - Whisper lỗi → raise, không treo Gemini; không chạy lại Whisper lần 2;
   - Gemini full-song lỗi content/5xx → thử chunk trước, không fallback A ngay;
   - Gemini lỗi key/blocked → fallback A;
   - log không lẫn.
5. E:
   - `whisper_engine=auto` chọn MLX dù `device=cpu` trên Apple Silicon;
   - fallback stable khi thiếu package;
   - output `WhisperResult` tương thích `_words_from_segment`;
   - `_WHISPER_LOCK` bao trọn **load + transcribe**, process-wide;
   - engine được truyền qua cả `align_lyrics` (fallback A);
   - float32 mặc định; fp16 chỉ khi pass parity;
   - so sánh stable vs mlx trên file ngắn.
6. Eval script:
   - thêm `--engine`, `--vad`, `--compare`;
   - truyền device/engine đúng, không hardcode `"cpu"`;
   - dùng chung Gemini cache giữa các lần đo để chỉ so Whisper.
7. Regression: working tree hiện tại **355 test**; PR19 gốc **352**; test mới ở trên sẽ cộng thêm.
   `tsc` nếu có đổi UI.

---

## 11. Thứ tự triển khai đề xuất

Có thể làm B/D/E/F song song về mặt nghiên cứu, nhưng nên merge theo thứ tự
an toàn:

1. **B — song song Whisper/Gemini** (không đổi chất lượng; dễ rollback).
2. **D — chunk Gemini song song + retry** (đo 429, partial failure).
3. **F — voiced spans + `clip_timestamps`** (đo quality gate).
4. **E — mlx-whisper backend** (parity test, fallback).
5. **Kết hợp + benchmark + chốt default** (medium hay turbo).

Mỗi bước có feature flag; benchmark trước/sau trên cùng 3 bài.

---

## 12. Điểm chạm code dự kiến

| File / hàm | Thay đổi |
|---|---|
| `karaoke/approach_b.py::align_with_gemini_lines` | Phase 1 song song; Phase 2 coverage→chunk; truyền voiced spans; nhận partial chunk |
| `karaoke/approach_b.py::_window_coverage` | Giữ cho coverage toàn bài; thêm `region_has_missing_lyrics` chạy cho **mọi chunk** |
| `karaoke/align.py::transcribe_whisper` | Thêm `clip_timestamps` (flat list), chọn backend MLX/stable, lock load+transcribe, log thời gian |
| `karaoke/align.py::align_lyrics` | Nhận `whisper_engine`/config để fallback A cũng dùng MLX |
| `karaoke/align.py::_vocal_rms` | Tính một lần; truyền `(levels, hop)` cho `plan_chunks`, `voiced_spans`, `_build_from_match` |
| `karaoke/gemini_lines.py::transcribe_lines_chunked` | ThreadPool 3 worker, giữ thứ tự, trả `(lines, failures)`, dedupe |
| `karaoke/gemini_lines.py::plan_chunks` | Nhận `levels/hop` (bỏ gọi lại `_vocal_rms`); giữ target 60 / max 81 |
| `karaoke/gemini_lines.py` (retry wrapper) | Retry 429/5xx + `GeminiBudget` dùng chung; **không sửa `_post_generate`** |
| `karaoke/pipeline.py::align_job` | Truyền engine/VAD/workers; không fallback A nếu Whisper đã fail |
| `karaoke/settings.py`, `web/src/components/align-options.tsx` | Thêm engine (`auto/mlx/stable`) và model turbo nếu pass gate |
| `scripts/eval_b_alignment.py` | Thêm `--engine`, `--vad`, `--compare`; bỏ hardcode `"cpu"` |
| `tests/test_approach_b.py`, `test_gemini_lines.py`, `test_karaoke.py` | Test mới ở mục 10 |

---

## 13. Tiêu chí Done

- [ ] B: Whisper/Gemini chạy song song Phase 1, coverage→chunk Phase 2; không đổi kết quả alignment.
- [ ] D: 3 chunk Gemini đồng thời; budget 4 calls/chunk; regional check **mọi chunk** (kể cả `[]`); vùng có lời → fallback A.
- [ ] F: voiced spans + `clip_timestamps` flat, clamp một chiều; F **off** mặc định; không mất chữ ở biên.
- [ ] E: `whisper_engine=auto` chọn MLX dù device=cpu; lock load+transcribe; engine đi qua cả fallback A; pass quality gate.
- [ ] Full-song lỗi content/5xx → thử chunk trước; lỗi key/blocked → fallback A.
- [ ] Benchmark trước/sau có bảng thời gian từng thành phần, peak RAM.
- [ ] Không tăng unsung/hallucination; 355+ test pass; `tsc` sạch.
- [ ] Feature flag để tắt từng phần nếu có regression.

---

## 14. Quyết định đã chốt

1. `mlx-whisper`: **optional + auto-cài trên Apple Silicon**, runtime auto-detect; thiếu thì stable_whisper.
2. Model MLX mặc định: **`medium`**, dtype **float32**; fp16 chỉ sau parity test.
3. F: dùng **RMS** trước; Silero VAD là fallback; **mặc định off** đến khi pass gate.
4. Chunk Gemini:
   - Budget **4 lần gọi HTTP/chunk** dùng chung object `GeminiBudget` (gốc + HTTP retry + content retry + rerun).
   - Chạy **regional coverage cho mọi chunk window**, kể cả chunk trả `[]`.
   - Vùng có lời → fallback A; vùng im lặng → giữ partial.
   - Nếu Whisper nghe <3 từ trong vùng nhưng RMS voiced ≥0.2 → vẫn fallback A.
5. Full-song lỗi content/5xx/timeout → thử chunk trước; lỗi key/blocked → fallback A.
6. `gemini_chunk_workers`: mặc định **3**, override bằng env.
7. `whisper_engine=auto` ưu tiên MLX trên Apple Silicon **bất kể `device=cpu`**;
   `device` chỉ dùng cho stable backend. Engine được truyền qua cả `align_lyrics`.
8. `_WHISPER_LOCK` là lock cấp module, bao trọn **load + transcribe**.
9. F mặc định off; khi bật mới dùng RMS, Silero là fallback.
10. Không đổi threshold RMS (0.08/0.12) trong phạm vi B/D/E/F; thống nhất threshold là experiment riêng.

## 15. Còn mở

1. Có bật `large-v3-turbo` trong `WHISPER_MODELS`/UI như opt-in không?
2. Chấp nhận tải model MLX lần đầu (~1–3 GB) và hiển thị tiến độ tải không?
3. Có cần giới hạn tổng số request Gemini/bài (budget toàn bài) không?
4. Cache transcript Whisper (A) và bỏ full-song (C) đã bị loại; có mở lại sau này không?

---

## 16. Changelog

- 2026-11-24: cập nhật theo review:
  - coverage theo vùng chunk lỗi thay vì toàn bài;
  - budget retry 4 calls/chunk, không nhân tầng;
  - `clip_timestamps` flat list + clamp end (mlx #1256/#1285/#1254);
  - precedence `whisper_engine=auto` thắng `device=cpu` trên Apple Silicon;
  - tách Phase 1 (song song full-song) và Phase 2 (coverage → chunk);
  - MLX float32 mặc định, model dùng chung cần lock;
  - F mặc định off đến khi pass gate;
  - bổ sung quality gate line end / peak RAM / thời gian, sửa lỗi pseudo-code.
- 2026-11-24 (lần 2): theo review:
  - regional coverage chạy cho **mọi chunk**, thêm case chunk trả `[]` + tín hiệu RMS;
  - F thực sự **off** mặc định (clip chỉ tính khi `whisper_vad=rms`);
  - clamp `end` một chiều, đảm bảo `start < end`;
  - full-song lỗi content/5xx → thử chunk trước, không fallback A ngay;
  - `GeminiBudget` dùng chung, full-song budget riêng;
  - `_WHISPER_LOCK` bao trọn load+transcribe; engine đi qua `align_lyrics`;
  - giữ nguyên threshold 0.08/0.12, không đổi ngoài phạm vi;
  - pin `mlx-whisper>=0.4.3,<0.5`; sửa nit test count/§7/§8.

---

## 17. Trạng thái triển khai (branch `feat/alignment-performance`)

Đã implement:

- **B**: Phase 1 chạy Whisper + full-song Gemini song song; Phase 2 coverage → chunk.
- **D**: chunk Gemini tối đa 3 worker, giữ thứ tự; `GeminiBudget` 4 calls/chunk;
  retry 429/5xx trong `gemini_lines`; regional check cho mọi chunk.
- **E**: `karaoke/whisper_backend.py` tự chọn `mlx-whisper` trên Apple Silicon;
  fallback stable; `load_mlx_whisper` giữ stable-ts word timing.
- **F**: `karaoke/voiced.py` + `clip_timestamps`; F mặc định **off**, bật bằng UI
  `VAD=RMS`; có guard bỏ F khi span quá nhiều/quá dài (mlx #1285).
  `VAD=Silero` được nối qua `vad=True` của stable-ts (thử nghiệm).
- Config: `whisper_engine`, `whisper_vad`, `gemini_chunk_workers`, `align_parallel`
  đi qua project/settings/UI; `requirements-karaoke.txt` có mlx-whisper optional.
- `WhisperFailed` để fallback A không chạy lại Whisper lần 2.

Đo trên Apple M5 24 GB, medium, bài 4–5 phút:

| Bài | Trước (stable serial) | B + D (stable) | B + D + E (MLX) | Ghi chú |
|---|---:|---:|---:|---|
| Tàn Hồng / Lời Thề | ~163 s | 56.0 s | **35.4 s** | F tự tắt do span 82% bài |
| Tên Người / Hà Nội 1882 | ~140 s | — | **33.2 s** | F bật RMS (span 51%, 11 đoạn) |

- 60 giây audio: stable medium ~25 s; MLX medium ~10.3 s (warm).
- F giúp Tên Người: Whisper 47.4 → 39.4 s; Tàn Hồng F làm chậm nên guard bỏ F.
- Kết quả alignment: Tàn Hồng 24/24; Tên Người 18/18 (Gò Công 123.8 s).

Còn lại:

- Chạy hold-out đầy đủ và quyết định bật F mặc định.
- Cân nhắc thêm UI cho `gemini_chunk_workers`/`align_parallel` (hiện env/default).
- Đo peak RAM MLX trên bài dài, chạy lặp 3 lần.
- 2026-11-24 (implement): B/D/E/F đã code; thêm Silero (`vad=True`) và regional
  check có điều kiện; 384 test pass; đo thật Tàn Hồng 47.1 s, Tên Người 33.2 s.

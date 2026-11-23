# Phương án ghép lời: Gemini line-level (B) trước, Whisper fallback (A)

Trạng thái: **Đã triển khai core Approach B; Gate 0 live chưa chạy được vì API key 401**  
Ngày: 2026-11-23  
Phạm vi: `karaoke/align.py`, `karaoke/pipeline.py`, chất lượng Whisper, editor  
Bối cảnh: Chirp/Gemini **word-level** đã bị loại và không có trong `main` stable.
Gemini **line-level** là Approach B, sẽ thử trước; Whisper-only (Approach A) là fallback.

> Ghi chú: local `main` đang behind `mine/main` 29 commit. Trước khi code nên
> fast-forward/pull `main` để tránh làm trên bản cũ.

---

## 0. Kết luận trước

A/B với Whisper `medium` cho thấy **aligner cũ (`reconcile_lyrics_with_transcript`)
thắng aligner word-span mới** ở timing câu lyric. Lưu ý: production đang mặc định
`small`; phải đo lại `small` trước khi chốt.

Vì vậy:

- **Approach B (Gemini line-level) được thử trước.**
- **Nếu B fail gate**, không viết lại aligner cũ; hướng đúng cho A là:
  1. Giữ aligner cũ làm xương sống.
  2. Tối ưu Whisper: model, decode ổn định, VAD, làm sạch audio.
  3. Sửa word timestamp bằng tín hiệu vocal local (RMS/onset).
  4. Sửa thuật toán đặt câu: bỏ kiểu dồn câu về sau, lọc supplement rác, xử lý hò.
  5. Thêm confidence + review UI thay vì cố tự động 100%.

Mục tiêu không phải "Whisper hoàn hảo", mà là **giảm số câu phải sửa tay** và
**không để một câu sai kéo lệch các câu sau**.

**Thứ tự thực hiện: B trước, A là fallback.** Approach B là pipeline mới; Approach A
chỉ quay lại nếu B fail gate. B luôn chạy sau **feature flag/opt-in** cho tới khi qua
gate + hold-out; A vẫn là đường offline mặc định trong thời gian thử B.

### Thứ tự triển khai (không cộng dồn 20–28 ngày)

```text
Phase 0 — hạ tầng dùng chung (2–4 ngày)
  harness + cache + gold từ lines đã export
  đo baseline small/medium + precision/recall của uncertain
  gán nhãn tay NLV/hò cho 1–2 bài hold-out (GT dùng chung cho cả A và B)

Phase 1 — B Gate 0 (2–4 giờ)
  Gemini line-level trên 3 clip 60s; thử 1 request toàn bài vs chunked
  → line P90 > 1,0s: dừng B, chuyển Phase 3A
  → 0,8–1,0s: chỉ tiếp tục nếu biên tính lại từ word/RMS
  → ≤ 0,8s: pass

Phase 2 — hạ tầng dùng chung cho B (2–3 ngày)
  Whisper một lần + word ownership
  snap RMS trong biên câu
  audio sạch dùng chung
  passthrough confidence (normalize/write_timing)

Phase 3B — build B (9–12 ngày)
Phase 3A — improve A (7–10 ngày)  ← chỉ làm nếu B fail
```

Ước lượng (không cộng dồn A+B):

- B: Phase 0 + Phase 1 + Phase 2 + Phase 3B ≈ **13–18 ngày**.
- A fallback: Phase 0 + Phase 2 + Phase 3A ≈ **11–16 ngày** (chỉ khi B fail).
- Nếu B fail, không làm lại Phase 0/2; chỉ làm Phase 3A.


---

## 1. Bằng chứng A/B với Whisper `medium`

Cùng một transcript Whisper `medium`, so sánh:

- **OLD**: `reconcile_lyrics_with_transcript` hiện tại.
- **NEW**: word-span aligner thử nghiệm.
- **HYB**: OLD cho câu tự tin, NEW cho câu OLD `uncertain`.

NEW/HYB đến từ branch thử nghiệm **đã bị xoá**; chỉ dùng làm bằng chứng lịch sử,
không có trong `main`.

Sai số **start câu lyric** (MAE / P90, giây):

| Bài | OLD | NEW | HYB |
|---|---:|---:|---:|
| Hào Khí Lam Sơn | **0,447 / 0,594** | 1,082 / 3,080 | 0,774 / 0,591 |
| Bài Ca Khải Hoàn | **1,715 / 5,310** | 3,560 / 11,547 | 3,419 / 11,547 |
| Quả Cam Cờ Nghĩa | **0,088 / 0,208** | 0,268 / 0,640 | 0,095 / 0,213 |
| Bình Minh Chiến Thắng | **0,948 / 2,429** | 2,583 / 8,284 | 2,754 / 11,833 |

Kết luận:

- OLD tốt hơn cho câu bình thường **khi dùng `medium`**.
- NEW/HYB không đủ tốt để làm mặc định.
- Nhưng OLD vẫn có lỗi thật:
  - câu bị đẩy lùi khi đoạn trước sai;
  - ad-lib cuối bài lệch;
  - câu `supplemented` bị rác (`"H creepy"`, `"iftsalay"`);
  - có câu sai nhưng OLD không đánh dấu `uncertain`.

> **Cảnh báo baseline:** production đang mặc định **`small`**
> (`pipeline.py:362`, `pipeline.py:266`, `settings.py:31`, UI `align-options.tsx`).
> Bảng trên chỉ chứng minh OLD > NEW với `medium`; **chưa phải config mặc định**.
> P0 phải đo thêm `small`. Chỉ khi `small` cũng cho kết luận tương tự mới dùng bảng
> này để chốt.

Vì vậy, **đây là kết luận cho hướng A (fallback)**: tối ưu OLD + tín hiệu audio
local, không thay bằng word-span. Điều này không làm thay đổi thứ tự B-trước.

---

## 2. Chẩn đoán lỗi

### 2.1. Lỗi từ Whisper

| Lỗi | Biểu hiện | Cách giảm |
|---|---|---|
| Hallucination trong nhạc dạo/im lặng | Thêm câu rác, từ rác | VAD/energy gate, lọc `supplemented` |
| Nghe sai tiếng Việt | Sai dấu, sai từ, mất từ | Model lớn hơn, decode ổn định, prompt có kiểm soát |
| Lặp câu | Cùng một câu xuất hiện nhiều lần | `condition_on_previous_text=False` **đã bật**; thêm lọc repeat |
| Word timestamp bị kéo | Từ giữ nốt dài, từ cuối câu | Snap RMS/onset, giới hạn max shift |
| Segment/regroup | Word timestamp lệch do stable-ts gộp lại | Đo `regroup`, `suppress_silence`; không chỉ chỉnh decode |
| Bỏ trắng câu | Không có word cho câu thật | Structural fallback, phrase slot |
| Không ổn định giữa các lần chạy | Cùng file ra kết quả khác | Temperature 0, bỏ retry ngẫu nhiên |

### 2.2. Lỗi từ thuật toán hiện tại

- `_place_unheard` đặt câu không khớp sát **trước câu khớp kế tiếp** → đẩy lùi về sau.
  Điểm neo là `_fit_unheard_spans` (`align.py:561`):
  `cursor = max(window_start, window_end - total)` (right-align).
- `_fit_unheard_spans` nén nhiều câu vào một khoảng trống.
- Nhánh cuối bài `next_start is None` → `cursor = previous_end + 0.4`
  (`align.py:534`) là nguồn lỗi ad-lib cuối bài.
- `_after_previous` tạo hiệu ứng domino và vẫn chạy trong `reconcile` hiện tại.
- `_supplement_unmatched` (`align.py:598`) chỉ có điều kiện:
  - `len(unused) < 2 and duration < 1.2` → **2 từ rác vẫn lọt**;
  - `len(unused) / len(words) < 0.6`.
  `_drop_covered_supplements` + `_echoes` có kiểm tra overlap/similarity, nhưng
  `_echoes` có thể **xoá nhầm backing vocal thật**.
- `_main_cluster` có thể bỏ anchor xa khi có đoạn hò dài.
- `_trusted` chỉ nhị phân, không có confidence mềm.
- Không có bước snap word timestamp về vùng có tiếng hát.
- `repair_stretched_words` **là dead code**, chỉ có unit test, không được gọi trong luồng.
- `fit_lines_to_voice` có endpoint `POST /api/jobs/{id}/fit` (`server.py:1641`) nhưng
  **web không gọi**; không phải "chỉ thủ công" theo nghĩa không có API.
- `fit_lines_to_voice` có cascade riêng: `active_start = max(active_start, fitted[-1]["end"])`
  (`align.py:255`). Nếu auto-fit sau align, câu sai vẫn kéo câu sau.

---

## 3. Mục tiêu và metric

### 3.1. Mục tiêu

1. Không regression trên các bài đã tốt.
2. Giảm mạnh hiện tượng một câu sai kéo lệch câu sau.
3. Giảm câu `supplemented` rác.
4. Cải thiện ad-lib/hò mà không phá câu lyric.
5. Giảm số dòng phải sửa tay mỗi bài.

### 3.2. Metric

Tách metric theo loại bài để không so sai hạng:

| Metric | Bài thường | Bài có hò/ad-lib |
|---|---|---|
| Line start MAE | Không tăng quá **+0,02s** so baseline; hoặc giảm ≥20% nếu baseline >0,3s | Không tăng quá +0,05s |
| Line start P90 | ≤ 0,5s (baseline tốt) / cải thiện ≥20% (baseline xấu) | ≤ 1,0s |
| Số câu bị đẩy lùi > 0,5s | Giảm ≥50%; mục tiêu cuối là 0 | Giảm ≥50% |
| `uncertain` precision/recall | Precision ≥70%, recall ≥80% cho câu lệch >0,5s | Như bài thường |
| Supplement rác | ≤ 1/bài | ≤ 1/bài |
| NLV precision/recall | — | ≥80% / ≥80% |
| Số dòng phải sửa tay | ≤5/bài | ≤8/bài |
| Thời gian transcribe | ≤2x baseline cho model mặc định | large là opt-in, không áp 2x |

Quy ước “một dòng phải sửa tay” (rubric):

- start lệch >0,3s; hoặc
- phải sửa chữ; hoặc
- phải tách/gộp câu;
- không tính thay đổi style.

### 3.3. Bộ test

Tập curated dùng test gồm **9 bài**:

`Hào Khí Lam Sơn`, `Sóng Cọc Bạch Đằng`, `Bài Ca Khải Hoàn`, `Quả Cam Cờ Nghĩa`,
`Buồm Đỏ Căng Gió Nam`, `Đại Việt Trường Tồn`, `Nữ Vương Mê Linh`,
`Hà Nội sáu mươi ngày đêm`, `Bình Minh Chiến Thắng`.

`Văn Lang Trường Tồn` không nằm trong curated → không dùng trong test set chính.

Chốt bản dùng:

- `Hào Khí Lam Sơn`: bản `Hào Khí Lam Sơn/Hào Khí Lam Sơn.karaoke.json`
  (bản `v5/` trùng 31 câu).
- `Sóng Cọc Bạch Đằng`: bản 42 câu `Sóng Cọc Bạch Đằng/Sóng Cọc Bạch Đằng.karaoke.json`
  (không dùng `SCBD/` 28 câu). Lưu ý status `aligned` = output của A, **không phải gold
  độc lập**; chỉ dùng coverage/line count cho tới khi gán nhãn tay lại.
- `Bài Ca Khải Hoàn`: dùng bản `BCKH/`.
- `Nữ Vương Mê Linh`: có đoạn hát sai so với lời → **không ưu tiên cho MAE/P90**;
  dùng làm stress test cho lời nhập lệch audio.
- `Bình Minh Chiến Thắng` (không phải “Bình Mình”).

Phân vai:

| Nhóm | Bài | Mục đích |
|---|---|---|
| Dev/tune | Quả Cam, Hào Khí, Bài Ca, Bình Minh | Chỉnh threshold, matching, word alignment |
| Hold-out accuracy | Buồm Đỏ, Hà Nội, Đại Việt | MAE/P90, không dùng tune |
| Stress | Nữ Vương (lời lệch), Sóng Cọc (A-origin) | Coverage, qualitative, lời lệch |
| Gate 0 | Quả Cam (dễ), Bài Ca/Bình Minh (khó), Hào Khí (trung bình) | 3 clip 60s + 1 bài full |

- Không dùng hold-out cho Gate 0/spike; giữ sạch để đánh giá cuối.
- Cache transcript theo:
  `(model, decode_config, audio_hash, stable-ts version, git SHA)`.
- Không dùng NEW/HYB trong harness mới; chỉ so các cấu hình OLD.
- Ghi tay NLV/hò cho ít nhất 2 bài (1–2 ngày công, nằm trong Phase 0). Chọn bài sau khi
  nghe; không giả định Nữ Vương có hò chỉ vì lời lệch.
- Chấp nhận gold có sai lệch nhỏ; metric chính là **so sánh tương đối giữa các phiên bản**.

---

## 4. Giải pháp theo thứ tự ưu tiên

### P0 — Dựng harness đo lường

Thêm script:

```text
scripts/eval_whisper_alignment.py
```

Chức năng:

- Load project `.karaoke.json`.
- Transcribe bằng model/config chỉ định.
- Cache transcript theo key ở §3.3.
- Chạy OLD với các model/decode config khác nhau (NEW/HYB chỉ là tham chiếu lịch sử,
  không có trong `main`).
- **Đo baseline `small` (production default) và `medium`** trước khi sửa thuật toán.
- **Đo precision/recall của `uncertain`** so với câu lệch >0,5s.
- **Freeze một bản aligner hiện tại** làm reference (commit SHA trong output).
- Xuất bảng MAE/P90/max, số câu lỗi, số câu `uncertain`.
- Có `--details` để xem từng câu.

Đây là **hạ tầng dùng chung cho cả B và A**. Không sửa thuật toán trước khi có harness.
Nếu `small` cho kết luận khác `medium`, phải chốt config trước.

### P1 — Tối ưu Whisper

#### 1. Model

Hiện trạng:

- Production default là **`small`** (`pipeline.py:362`, `pipeline.py:266`,
  `settings.py:31`, UI `align-options.tsx`).
- `large` trong whisper 20250625 **đã là `large-v3.pt`**; UI label “Large (bản v3)”
  là đúng.
- `large-v3-turbo` **có** trong `whisper._MODELS` nhưng bị chặn bởi
  `WHISPER_MODELS` (`pipeline.py:23`).

Việc cần làm:

1. Đo baseline `small` vs `medium` (P0).
2. Đo `large` / `large-v3-turbo` trên hold-out.
3. Nếu muốn `large-v3-turbo`, thêm vào:
   - `WHISPER_MODELS`;
   - `MODELS` trong `web/src/components/align-options.tsx`;
   - test `WhisperOptionTests`.
4. Thử `faster-whisper` / `WhisperX` sau cùng.

Chỉ đổi mặc định nếu thắng rõ trên bộ test; `large` là opt-in “chất lượng cao”.

#### 2. Decode ổn định

`condition_on_previous_text=False` **đã bật từ trước** (`align.py:816`), không tính
vào effort.

Thử cấu hình:

```python
temperature = 0.0
beam_size = 5
best_of = 5
condition_on_previous_text = False   # đã có
word_timestamps = True
no_speech_threshold = 0.6
logprob_threshold = -1.0
compression_ratio_threshold = 2.4
regroup = True                       # stable-ts 2.19.1
suppress_silence = ...
```

- `regroup` và `suppress_silence` của stable-ts **quyết định word timestamp**,
  không chỉ decode config; phải đo riêng.
- Temperature 0 mặc định.
- Chỉ retry fallback cho segment có `compression_ratio` cao hoặc lặp từ.
- Không dùng temperature tuple cho toàn bài nếu không cần.

#### 3. VAD / energy gate

- Dùng `_vocal_rms` để tìm đoạn thật sự có tiếng hát.
- Chỉ transcribe đoạn voiced, hoặc ít nhất lọc word rác trong im lặng.
- Có thể dùng Silero VAD nếu muốn.

#### 4. Làm sạch audio

- Chuẩn hoá loudness.
- High-pass ~80 Hz.
- Cắt silence thừa.
- Dereverb nhẹ nếu cần.
- Luôn dùng vocal stem.

**Quan trọng:** hiện Whisper đọc `vocal_path` gốc (`align.py:822`), còn `_vocal_rms`
đọc qua `pcm16_wav` (`align.py:129`). Nếu thêm loudnorm/high-pass, phải tạo **một file
tạm dùng chung cho cả Whisper và RMS**; không thì timing đo trên audio A, chữ nghe trên
audio B.

#### 5. `initial_prompt`

Chỉ thử sau cùng:

- Đưa 1–2 câu lời hoặc vài từ khoá.
- Rủi ro: Whisper bám prompt và bịa lời.
- Phải đo lại, không bật mặc định nếu không thắng.

### P2 — Snap word timestamp

Không tạo bộ snap RMS song song với `fit_lines_to_voice` / `_place_into_span` /
`_absorb_voiced_gaps`. Nên **mở rộng hàm cũ** thành một hàm dùng chung:

```python
snap_words_to_voice(words, levels, hop, threshold, max_shift=0.20)
```

Quy tắc:

- Snap onset/offset về vùng RMS vượt ngưỡng.
- Không dịch quá ±0,2s.
- Không cho từ nhảy sang câu khác.
- Không kéo từ vào NLV.
- Từ không tìm được vùng voiced → `uncertain`.
- Không làm thay đổi thứ tự từ.
- **Chỉ auto khi câu `uncertain` hoặc lệch lớn**, không chạy lại toàn bài.

`repair_stretched_words` hiện là **dead code** (chỉ có unit test, không được gọi).
Cần quyết định: nối vào luồng snap hay xoá; không mô tả nó như “đang chạy thủ công”.

Nếu bật auto `fit_lines_to_voice`, phải xử lý cascade riêng:
`active_start = max(active_start, fitted[-1]["end"])` (`align.py:255`) vẫn kéo câu sau.
Chỉ auto-fit cho câu `uncertain`, và test trường hợp câu trước bị đẩy lùi.

### P3 — Sửa thuật toán đặt câu

Giữ `reconcile_lyrics_with_transcript`, sửa các điểm sau:

#### 3.1. Bỏ `_place_unheard` kiểu dồn về sau

Điểm neo rẻ nhất là `align.py:561`:

```python
cursor = max(window_start, window_end - total)
```

Đây là right-align: câu không khớp bị đẩy sát `next_start`. Sửa đúng dòng này rẻ hơn
viết lại cả hàm.

Thay bằng local anchored placement:

- Tạo `vocal_phrases` từ RMS.
- Giữa hai anchor mạnh, nếu số reference line bằng số phrase → gán theo thứ tự.
- Nếu không bằng → `unsung`/`uncertain`, **không dời câu sau**.
- Mỗi câu lấy `start/end` từ phrase của nó.
- Không dùng `previous_end` làm con trỏ.

Nhánh cuối bài `next_start is None` (`align.py:534`) cũng phải sửa; đây là nguồn lỗi
ad-lib cuối bài.

**`_after_previous`:** hiện vẫn chạy sau `_place_unheard`. Nếu giữ nó, mục tiêu
“không dời câu sau” không đạt. Phải chọn:

- bỏ `_after_previous` và xử lý overlap bằng co biên trong phrase; hoặc
- giữ nó nhưng chỉ cho co biên, không đẩy câu.

Ghi rõ lựa chọn trong code + test.

#### 3.2. Lọc `_supplement_unmatched`

Lỗ hổng thật nằm ở:

```python
if len(unused) < 2 and duration < 1.2:
    continue
```

Với **2 từ rác**, điều kiện này không chặn, nên `"H creepy"` lọt. Ngoài ra
`_drop_covered_supplements` + `_echoes` có thể **xoá nhầm backing vocal thật**.

Sửa:

- duration ≥ 1,2s cho mọi trường hợp, kể cả 2 từ;
- từ phải có vowel/Latin hợp lệ, không phải ký tự rác;
- không nằm trong im lặng;
- không chồng lên câu lyric;
- similarity với mọi reference line thấp;
- nếu là backing vocal thật, đánh dấu `supplemented`/NLV thay vì xoá.

Câu như `"H creepy"`, `"iftsalay"` phải bị loại.

#### 3.3. NLV/hò

- Chỉ tạo NLV khi text similarity với mọi reference line thấp.
- Duration 1,2–20s.
- `voiced_ratio` cao.
- Không chồng lên lyric.
- Nếu NLV chồng lên câu `uncertain`, có thể thay; nhưng phải test.

#### 3.4. Block-shift correction

Lưu ý: `_align_tokens` là DP monotonic toàn bài. Dịch block sau DP là **sửa hai lần**,
có thể phá anchor. Điều kiện:

- Chỉ dịch khi **cả hai biên block** đều unmatched/`uncertain`.
- Không dịch block chứa anchor mạnh.
- So sánh **trung bình similarity trên mỗi token**, không dùng tổng thô (tổng thô thiên
  vị block dài).
- Chỉ nhận nếu trung bình tăng >0,15 và không làm giảm block kề.
- Đánh dấu `block_shift`; test riêng.

Nếu không đủ điều kiện, bỏ block-shift và để review UI xử lý.

#### 3.5. Confidence

Thêm:

- `alignment_confidence`: câu có neo đúng phrase không.
- `text_confidence`: word/text có đáng tin không.
- `cascade_risk`: câu có khả năng bị đẩy lùi.

**Phải sửa passthrough trước khi thêm field:**

- `normalize_edited_lines` (`pipeline.py:44`) chỉ giữ `uncertain`/`supplemented`;
- `write_timing` (`media.py:429`) chỉ ghi text/start/end/words.

Nếu không, badge mất sau save/reload.

Deliverable bắt buộc: **precision/recall của `uncertain`** so với câu lệch >0,5s.
Bảng A/B hiện tại cho thấy `uncertain` đang hỏng: HYB ở *Bài Ca Khải Hoàn* bằng đúng
NEW (3,419/11,547), nghĩa là các câu lệch 1,7s không được đánh dấu để can thiệp.

UI hiển thị badge tương ứng; panel “câu cần kiểm tra”.

### P4 — Model/forced alignment nâng cao

Chỉ làm nếu P1–P3 chưa đủ:

- **WhisperX**: faster-whisper + wav2vec2 forced alignment.
- **wav2vec2 tiếng Việt**: forced alignment từng từ, không phụ thuộc Whisper word timestamp.
- Chấp nhận thêm model/dependency; cần đo tốc độ và độ chính xác.

### P5 — Editor và UX

- Badge: `Lệch nhịp`, `Lệch chữ`, `Có thể bị đẩy lùi`, `Hò`, `Ad-lib`, `Không hát`.
- Nút **Khóa timing**.
- Nút nghe lại đoạn.
- **Nút “Chỉnh theo tiếng hát” gọi lại endpoint có sẵn**
  `POST /api/jobs/{id}/fit`, không cần viết endpoint mới.
- Ưu tiên sửa nhanh: sửa text không đổi timing; kéo timing không đổi text.
- Panel “câu cần kiểm tra” thay vì bắt người dùng dò toàn bài.

### P6 — Không cần dọn cloud

`main` stable **không chứa Chirp/Gemini** (`karaoke/chirp.py`, `karaoke/phrase_align.py`,
`stt_engine`, `google_audio_consent` đều không có). Branch thử nghiệm đã bị xoá, nên
không có gì để revert hay dọn.

Việc duy nhất nên giữ là **script A/B** nếu muốn dùng để đo các thay đổi Whisper.

---

## 5. Thay đổi code dự kiến

| File | Thay đổi |
|---|---|
| `karaoke/align.py` | decode options, VAD, snap word, local placement, filter supplements, confidence |
| `karaoke/media.py` | audio sạch dùng chung; `write_timing` passthrough field mới |
| `karaoke/pipeline.py` | options Whisper mới; `normalize_edited_lines` passthrough confidence; `align_strategy` |
| `karaoke/director.py` | tách/tái dùng `_api_key`, `_post_generate`, `ALLOWED_MODELS`, cache, responseSchema |
| `karaoke/server.py` | request model, log; tái dùng `/fit`; consent audio |
| `karaoke/settings.py` | default `audio_consent`, model Gemini, feature flag B |
| `karaoke/project.py` | lưu `align_strategy`, `gemini_model`, `prompt_version`, confidence, audio consent receipt |
| `web/src/lib/types.ts` | `align_strategy`, `audio_consent_receipt`, field confidence |
| `web/src/components/align-options.tsx` | chọn model/chế độ; thêm turbo nếu bật; chọn B/A |
| `web/src/components/settings-drawer.tsx` | consent audio riêng, mô tả cloud |
| `web/src/components/maker-form.tsx` | payload B, log, fallback |
| `web/src/components/timing-editor.tsx` | badge, khóa timing, panel review, nút gọi `/fit` |
| `tests/test_karaoke.py` | regression placement/supplement/confidence |
| `scripts/eval_whisper_alignment.py` | benchmark + cache key version/SHA |

Lưu ý: `project_from_fields` chỉ nhận key đã whitelist; `align_strategy`,
`gemini_model`, `prompt_version`, `audio_consent` phải được thêm vào schema/allowlist,
nếu không sẽ bị bỏ khi save/reload.

---

## 6. Kế hoạch A — chỉ dùng nếu B fail

Phase 0 và Phase 2 (snap/audio/passthrough) là hạ tầng dùng chung, đã tính ở §0.

| Giai đoạn | Nội dung | Thời gian |
|---|---|---|
| A1 | Model/decode/VAD/audio, thêm turbo whitelist | 2 ngày |
| A3 | Local placement, filter supplement, NLV, block-shift | 3–4 ngày |
| A5 | UI confidence/review, gọi lại `/fit` | 1–2 ngày |
| A4 | WhisperX/wav2vec2 nếu cần (optional) | 2–3 ngày |

A-only: **6–8 ngày**; cộng shared Phase 0+2 (4–7 ngày, gồm GT) → **11–16 ngày**.
Cộng A4 optional nếu cần.

---

## 7. Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| **B:** Gemini 429/quota/5xx | Retry budget, fallback A ngay; không treo job |
| **B:** chi phí/bài vượt ngân sách | 1 request toàn bài trước; cache; đo usageMetadata |
| **B:** upload vocal privacy | Consent audio riêng + receipt; fallback A khi tắt |
| **B:** chunk drift / câu cắt biên | Ưu tiên 1 request; calibrate bằng Whisper; policy cut rõ |
| **B:** Gemini đổi model/region | `ALLOWED_MODELS`, verify audio input, feature flag |
| **B:** merge trùng chorus | Dedupe theo `text+time+occurrence`; repeat pass 2 |
| **B:** Gemini ASR sai → unsung oan | Whisper second opinion; hạ confidence thay vì bỏ |
| `large`/`large-v3-turbo` quá chậm/OOM | Opt-in, benchmark trước |
| VAD cắt mất từ | Dùng overlap, kiểm tra word đầu/cuối |
| Snap làm sai word đúng | Giới hạn ±0,2s, không đổi thứ tự |
| Hai bộ snap RMS đá nhau | Mở rộng `fit_lines_to_voice`, không viết song song |
| Auto-fit lại cascade | Chỉ fit câu `uncertain`; test câu trước bị đẩy |
| Block-shift sửa hai lần | Chỉ khi hai biên unmatched; dùng trung bình similarity |
| Hai nguồn audio khác nhau | Một file tạm dùng chung cho Whisper + RMS + Gemini |
| Overfit bài dev | Hold-out 3 bài accuracy (Buồm Đỏ, Hà Nội, Đại Việt); Nữ Vương/Sóng Cọc chỉ stress/coverage |
| Whisper vẫn hallucinate | Lọc supplement, review UI |
| Local placement lại cascade | Test chèn câu sai/bỏ câu |
| Sửa quá nhiều gây regression | Mỗi thay đổi đo A/B, commit nhỏ |

---

## 8. Tiêu chí chấp nhận

### 8.1. Approach A (fallback)

- Bài thường: MAE không tăng quá +0,02s; P90 không tăng quá 20% nếu baseline <0,5s,
  hoặc giảm ≥20% nếu baseline xấu.
- Bài hò: P90 ≤1,0s; không tăng quá +0,05s MAE.
- Số câu bị đẩy lùi >0,5s giảm ≥50%.
- `uncertain` precision ≥70%, recall ≥80% cho câu lệch >0,5s.
- Supplement rác ≤1/bài.
- NLV precision/recall ≥80% trên bài có hò.
- Số dòng phải sửa tay ≤5/bài thường, ≤8/bài hò (theo rubric §3.2).

### 8.2. Approach B (đề xuất thay default)

- Line start MAE/P90 không tệ hơn A theo dung sai tuyệt đối (+0,02s/+0,05s).
- Matching precision ≥0,9; recall ≥0,85.
- Hallucination ≤5% số câu Gemini trả.
- Word onset P90 **matched** ≤0,2s; interpolated tách riêng.
- Số câu `unsung`/extra không tăng quá 10% so A.
- Chi phí/latency trong ngân sách đã chốt.
- Có feature flag + fallback A.

### 8.3. Chung

- **204 test hiện tại vẫn pass**:
  ```bash
  .venv-karaoke/bin/python -m unittest discover -s tests -q
  ```
  (`web/package.json` chưa có script test; chỉ chạy `npx tsc --noEmit`/`npm run build`.)

---

## 9. Câu hỏi cần chốt

### Đã chốt

1. **Whisper trước, Gemini sau.**
2. **Dùng Whisper words làm second opinion** khi matching.
3. **Sóng Cọc chỉ dùng coverage** (A-origin, chưa relabel); **Nữ Vương dùng stress test**
   (lời lệch audio), không ưu tiên MAE/P90. `Văn Lang` ngoài curated, không dùng.
4. **Gate 0 thử cả 1 request toàn bài và chunked**, ưu tiên 1 request.
5. **B được phép thành default** nếu pass gate + hold-out.
6. **SIM_MIN khởi điểm 0.30**, calibrate trên 4 bài dev.

### Còn chốt

1. Ngân sách Gemini/bài: số request, retry, chi phí tối đa?
2. Baseline production `small` hay đổi sang `medium`?
3. Có bật `large` / `large-v3-turbo` như opt-in chất lượng cao không?
4. Có thêm WhisperX/wav2vec2 local nếu word timestamp vẫn kém không?
5. Có chấp nhận thêm consent audio + dependency cloud trong app local không?
6. Sai số biên Gemini chấp nhận: P90 0,5s / 0,8s / 1,0s? (Gate 0 đang dùng 1,0s
   làm ngưỡng dừng, 0,8s làm mục tiêu).

---

## 10. Approach B: đề xuất thay default nếu pass gate — Gemini line-level + Lyrics matching + Whisper word-level

Đây là **pipeline mới, đề xuất thay default nếu pass gate**, không phải bản improve nhỏ.
Có thể tái dùng một số phần của A (matching, DP token, snap RMS), nhưng luồng chính là:

- **Whisper chạy trước**, một lần toàn bài, local;
- Gemini lo **cấp câu** (sau Whisper, để calibrate chunk và fallback rẻ);
- matching algorithm lo **lời nhập ↔ lời thực tế**, dùng cả Gemini line và Whisper
  words làm second opinion;
- Whisper lo **word timing trong từng câu**.

Approach A vẫn là baseline/fallback để so sánh và để dùng khi không có cloud.

### 10.1. Ý tưởng

```text
vocal
  → một file audio sạch dùng chung
  → Whisper MỘT LẦN toàn bài (local, word timestamps)
  → Gemini line pass:
       [{text, start, end}, ...]          ← chỉ là cửa sổ tìm kiếm
  → merge chunk + calibrate drift bằng Whisper words
  → matching lyrics ↔ Gemini lines
       + Whisper words làm second opinion
       → matched | repeat | extra | nlv | unsung
  → với mỗi câu khớp:
       lấy word trong [start-pad, end+pad]
       → DP từ reference ↔ heard word
       → tính lại biên câu từ word đầu/cuối
       → snap RMS
  → lines có confidence
  → câu không đủ confidence: bỏ trống/unsung, người dùng thêm
```

Điểm cốt lõi:

> **Gemini chỉ cần đúng cấp câu, không cần đúng từng từ.**
> **Whisper + audio local chịu trách nhiệm timing từng từ.**

Cách này né đúng điểm yếu lớn nhất của Gemini (word timestamp), trong khi vẫn dùng
được thế mạnh của nó (hiểu tiếng Việt, ngữ cảnh, phân câu).

### 10.2. Gemini line pass

Whisper đã chạy xong trước bước này, nên có sẵn word timestamps để calibrate chunk và
fallback A nếu Gemini lỗi/429/tắt consent.

- Gate 0 thử **1 request toàn bài** trước; chỉ chunk 45–90s nếu model/giới hạn bắt buộc
  hoặc 1 request fail.
- Prompt yêu cầu JSON:
  ```json
  {
    "lines": [
      {"text": "...", "start": 12.34, "end": 15.67, "confidence": 0.8}
    ]
  }
  ```
- Có thể đưa lyrics làm context, nhưng phải nói rõ:
  - audio là chuẩn;
  - lời tham chiếu có thể sai;
  - không được bịa câu không có trong audio;
  - nếu câu bị cắt ở mép chunk: xem Chunk policy bên dưới (không tự động bỏ).
- Merge chunk: cộng offset, dedupe overlap, xử lý câu bị cắt.
- Lưu `heard_lines` thô; **không tin text tuyệt đối**.

#### Biên câu: Gemini chỉ là cửa sổ, không phải biên cuối

Đây là lỗi kiến trúc quan trọng nhất của B:

- `start/end` của Gemini chỉ dùng làm **cửa sổ tìm kiếm**.
- `pad = ±0,6–1,0s`, không phải ±0,25s.
- Whisper word DP chạy trong cửa sổ đó.
- **Biên cuối = onset word Whisper đầu tiên / offset word cuối**, rồi snap RMS.
- Cắt biên với câu kề tại **midpoint**, không dời câu.
- Nếu không cho phép tính lại biên, B không thể vượt sai số Gemini và snap ±0,2s
  không sửa được lệch 0,8s.

#### Chunk policy

- Ưu tiên chunk mà câu nằm trọn trong đó.
- Nếu câu bị cắt ở cả hai chunk → đánh `uncertain` + request lại chunk dịch 20–30s
  (chỉ 1 lần).
- Timestamp từng chunk có thể drift; calibrate bằng anchor tin cậy
  (Whisper word hoặc RMS activity) trước khi cộng offset.
- Dedupe overlap phải so cả `text` + `start/end`; không dedupe hai chorus giống text
  nhưng khác occurrence.

**Rủi ro:** Gemini vẫn có thể trả timestamp lệch. Gate 0 đo line start/end trên clip
sạch; nếu P90 > 1,0s thì dừng B. Nếu 0,8–1,0s, chỉ tiếp tục khi biên được tính lại
từ word/RMS.

### 10.2.1. Có nên cung cấp lyrics cho Gemini?

**Nên, nhưng theo kiểu “reference context”, không phải “copy đúng lời này”.**

Lợi ích:

- Gemini biết vocabulary, tên riêng, từ cổ/địa danh;
- biết bài có bao nhiêu câu, câu nào có thể bị lặp;
- phân câu ổn định hơn, ít gộp/tách câu lung tung;
- giảm lỗi chính tả tiếng Việt.

Rủi ro:

- Gemini có thể **bịa theo lời tham chiếu** dù audio không hát câu đó;
- có thể tạo đủ số câu bằng cách “đọc” lời nhập;
- có thể gán timestamp theo thứ tự lời nhập thay vì audio;
- lời nhập sai 10% sẽ kéo Gemini theo chỗ sai.

Vì vậy prompt phải nói rõ:

```text
Audio là chuẩn. Lời tham chiếu có thể sai hoặc thiếu.
Chỉ trả những câu thực sự nghe được trong audio.
Không được copy lời tham chiếu nếu audio hát khác.
Với mỗi câu, trả:
- reference_index nếu xác định được câu tương ứng,
- heard_text là chữ thực tế nghe được,
- start/end,
- status: matched | repeat | extra | nlv | unsung,
  (map omitted → unsung),
- confidence.
```

Dạng JSON gợi ý:

```json
{
  "lines": [
    {
      "reference_index": 12,
      "heard_text": "anh thương em suốt đời",
      "start": 54.2,
      "end": 58.9,
      "status": "matched | repeat | extra | nlv | unsung",
      "confidence": 0.82
    }
  ]
}
```

Nguyên tắc:

- **Nhãn của Gemini chỉ là gợi ý**; nhãn cuối do matching deterministic quyết định.
- **Không tin `reference_index` tuyệt đối**; matching algorithm vẫn phải kiểm tra order
  và similarity.
- **Không dùng `heard_text` của Gemini làm chữ hiển thị** nếu đã có lyrics đúng; dùng
  lyrics cho câu matched.
- Nếu Gemini trả `heard_text` giống hệt reference nhưng audio không có vocal/không đủ
  duration → nghi hallucination, hạ confidence.
- Nên chạy **A/B: Gemini có lyrics context vs không context** trên 3 bài. Nếu có context
  làm tăng hallucination mà không tăng matching, bỏ context.

Khuyến nghị thực dụng:

1. Pass 1: Gemini line pass **có lyrics context** để phân câu + vocabulary.
2. Pass 2: matching deterministic với lyrics.
3. Chỉ những block ambiguous mới gọi Gemini retouch.

### 10.3. Matching lyrics ↔ heard lines

Đây là phần "thuật toán thông minh" quan trọng nhất. Không nên giao toàn bộ cho Gemini
retouch, vì Gemini có thể đổi chữ hoặc bịa. Matching deterministic an toàn hơn.

#### Chuẩn hoá

- NFC, casefold.
- Bỏ dấu câu.
- Tạo 2 bản:
  - bản hiển thị: giữ dấu, chữ hoa;
  - bản so khớp: bỏ dấu, lowercase.

#### Similarity

```text
lcs_norm = weighted_lcs_tokens(ref, heard)
         / max(1, len(ref_tokens), len(heard_tokens))     # [0,1]

char_norm = SequenceMatcher(fold(ref), fold(heard)).ratio() # [0,1]

sim_text = 0.8 * lcs_norm + 0.2 * char_norm
```

- Token LCS là tín hiệu chính; char ratio chỉ phụ. Không dùng `max()` vì char ratio
  trên tiếng Việt fold dấu thường cao giả tạo.
- `weighted_lcs_tokens`: mỗi cặp token đóng góp `token_similarity ∈ [0,1]`; chia cho
  `max(len(ref_tokens), len(heard_tokens))` để ra `[0,1]`.
- Nếu có Whisper words trong cửa sổ:
  ```text
  sim = max(sim_text(ref, gemini_line), sim_text(ref, whisper_words))
  ```
  Dùng làm second opinion; không thay thế hoàn toàn Gemini line.
- Không dùng `max()` trên cả bài; chỉ so từng cặp câu.

#### Calibrate ngưỡng similarity

Không chọn 0,70 bằng cảm tính. Trong spike:

1. Chạy trên 4 bài dev, với gold đã export.
2. Vẽ phân bố `sim` cho cặp đúng và cặp sai.
3. Chọn ngưỡng theo precision/recall.
4. Ưu tiên token-LCS; nếu char-ratio làm nhiễu, giảm trọng số hoặc bỏ `max()`.
5. Ghi ngưỡng + phân bố vào doc/harness.

#### Line-level DP

Toàn bài, monotonic:

```text
score(i, j) = max(
    score(i-1, j-1) + match_score(ref_i, heard_j),
    score(i-1, j)   + GAP_REF,
    score(i, j-1)   + GAP_HEARD
)
```

`match_score`:

```text
raw_score = W_TEXT * sim
          + W_DURATION * duration_fit
          + W_ORDER * order_bonus

match_score = raw_score - MATCH_BASE
```

**Invariant đúng:** phải kiểm tra trường hợp xấu nhất mà `fit` và `order` là điểm cộng:

```text
match_score(sim=0, fit=1, order=1) < GAP_REF + GAP_HEARD
0.35 + 0.10 - MATCH_BASE < -0.60
→ MATCH_BASE > 1.05
```

Với `MATCH_BASE=0.95`, trường hợp `sim=0, fit=1, order=1` vẫn cho `-0.50 > -0.60`
và DP sẽ ghép. Vì vậy **không thể chỉ dựa vào MATCH_BASE**; phải chặn bằng `sim`:

```python
SIM_MIN = 0.30            # khởi điểm, calibrate trên 4 bài dev
if sim < SIM_MIN:
    return float("-inf")
```

Gate này chặn mọi cặp `sim` thấp, kể cả khi duration/order hoàn hảo. `SIM_MIN` phải
calibrate; 0,30 chỉ là điểm bắt đầu.

Gợi ý (duration_fit và order_bonus trong `[0,1]`):

- `W_TEXT=0.55`, `W_DURATION=0.35`, `W_ORDER=0.10`.
- `GAP_REF=-0.45`, `GAP_HEARD=-0.15`.
- `MATCH_BASE=0.95` (hoặc thấp hơn nếu dùng `SIM_MIN`).
- `duration_fit = 1 - min(1, |actual - expected| / max(actual, expected, 1e-3))`.
- **Bootstrap `syllable_seconds`:** ước lượng từ heard lines trước khi có anchor:
  `median(duration_heard / syllable_count_gemini)`, rồi tinh chỉnh bằng anchor mạnh.
- **Bootstrap `order_bonus`:** dùng vị trí chuẩn hoá
  `1 - |i / max(1, L_ref-1) - j / max(1, L_heard-1)|`, không cần anchor.
- **Tie-break:** nếu `match_score` bằng gap trong sai số `1e-9`, **ưu tiên gap**
  (thà thiếu còn hơn đặt sai). Code phải có quy tắc cố định, không phụ thuộc thứ tự
  so sánh.

Kiểm tra nhanh:

```text
2 gap = -0.45 + -0.15 = -0.60
sim=0, fit=1, order=1 → 0.35 + 0.10 - 0.95 = -0.50  → bị SIM_MIN chặn
sim=0.30, fit=0.5, order=0.5 → 0.165+0.175+0.05-0.95 = -0.56  → ghép
sim=0.90, fit=0.8, order=1 → 0.495+0.28+0.10-0.95 = -0.075 → ghép
```

`raw_score` cũng là `line_confidence` cơ sở, để DP và confidence dùng cùng một hệ
score, không cần tune hai bộ trọng số.

#### Anchor và fallback

- Anchor mạnh: `sim >= 0.70` và duration hợp lý.
- Anchor yếu: `0.35 <= sim < 0.70`.
- Giữa hai anchor mạnh:
  - nếu số reference line = số heard line → gán theo thứ tự;
  - nếu lệch → đánh dấu `unsung`/`extra`, không ép.
- Không dời câu sau chỉ vì một câu không khớp.

#### Lặp câu / một câu nhiều lần

- DP chính là monotonic one-to-one, nên repeat phải làm **pass 2**:
  - heard line chưa dùng;
  - `sim >= ngưỡng repeat` (calibrate, ví dụ 0.70);
  - gắn `occurrence` (0,1,2...).
- Đặt `MAX_REPEATS = 4` (hoặc theo số chorus thực tế) và test bài có chorus 3 lần.
- Bộ nhãn phải có **`repeat`**, không chỉ `matched | extra | nlv | unsung`.
- **Repeat không được bị luật NLV xoá**: chorus lặp có similarity cao với reference;
  nếu rơi vào nhánh "heard line không match" thì phải thành `repeat`, không thành NLV.
- `repeat` giữ text reference, timing của heard line.

#### Một heard line chứa nhiều reference line

- Nếu Gemini gộp 2 câu thành 1:
  - split theo word timestamps/Whisper words;
  - hoặc split theo tỉ lệ số âm tiết;
  - đánh dấu `uncertain`.

#### Confidence

Dùng cùng `raw_score` của DP:

```text
line_confidence = raw_score
                = 0.55 * sim
                + 0.35 * duration_fit
                + 0.10 * order_bonus
```

- `< 0.50`: không dùng làm câu chuẩn; đánh dấu `unsung`/`review`.
- `0.50–0.70`: dùng nhưng `uncertain`.
- `>= 0.70`: câu chuẩn.

Có thể trừ nhẹ confidence nếu margin giữa best và second-best <0.10 (ambiguous),
nhưng không tạo hệ trọng số thứ hai. Ngưỡng phải calibrate trên 4 bài dev.

Không cần khớp 100% số câu. **Thà thiếu còn hơn đặt sai**; người dùng thêm câu thiếu.

#### Gemini retouch

Chỉ dùng cho block ambiguous:

- `sim` trong khoảng 0.30–0.60;
- hoặc số câu hai bên lệch nhau;
- hoặc có nghi ngờ repeat/omission.

Prompt chỉ để phân loại/sửa nhẹ, không lấy timestamp:

```text
Audio là chuẩn. Lời tham chiếu có thể sai.
Hãy cho biết câu nào thực sự được hát, câu nào là hò/ad-lib,
và câu reference nào tương ứng. Không bịa câu mới.
```

Nếu Gemini trả text khác reference, vẫn giữ reference text cho câu khớp; chỉ dùng
Gemini để quyết định `match / unsung / nlv`.

### 10.4. Whisper word-level trong từng câu

Có 2 cách:

#### Cách 1 — Whisper toàn bài, cắt theo cửa sổ câu (khuyến nghị)

- Chạy Whisper một lần on full vocal, `word_timestamps=True`.
- Với mỗi câu khớp:
  - lấy các heard word có midpoint trong `[start - pad, end + pad]`;
  - `pad = 0.6–1.0s`, không phải 0.25s;
  - DP reference token ↔ heard word (dùng `_align_tokens`);
  - từ khớp giữ timestamp Whisper;
  - từ không khớp nội suy trong biên câu;
  - **tính lại biên câu** từ word đầu/cuối;
  - snap RMS trong biên mới;
  - cắt với câu kề tại midpoint.
- Nhanh, ít API, tận dụng Whisper một lần.

#### Word ownership khi cửa sổ chồng nhau

Pad ±0,6–1,0s làm hai cửa sổ kề có thể chứa cùng một word. Phải có luật:

- Mỗi **heard word chỉ thuộc một câu**.
- Gán word theo midpoint vào **cửa sổ Gemini gốc, không pad**.
- Chỉ mở pad cho câu có coverage <50%; khi đó ưu tiên word chưa thuộc câu khác.
- Cắt biên tại midpoint giữa hai câu.
- Chỉ **co biên**, không dời câu.

Nếu không, cùng một word có thể bị dùng hai lần và bóp méo timing cả hai câu.

#### Cách 2 — Whisper trên từng clip câu

- Cắt audio theo `[start - pad, end + pad]`.
- Transcribe từng clip với `initial_prompt = reference line`.
- Ghép word timestamp + offset.
- Chính xác hơn nếu câu ngắn, nhưng chậm hơn nhiều và dễ lỗi ở biên.
- Chỉ dùng khi Cách 1 cho coverage thấp.

#### Word alignment

- Dùng DP token-level hiện có (`_align_tokens`) trong phạm vi câu.
- Không dùng DP toàn bài cho word-level; mỗi câu độc lập.
- Từ reference không khớp: chia đều giữa hai từ khớp gần nhất.
- Từ heard không có trong reference: bỏ hoặc đánh dấu ad-lib.
- Snap onset/offset về RMS, giới hạn ±0.2s.
- Nếu coverage < 50% hoặc word confidence thấp:
  - đánh dấu `uncertain`;
  - hoặc chạy Cách 2 cho riêng câu đó (tùy chi phí).

### 10.4.1. Whisper thiếu từ trong câu — có phá pipeline không?

Không, nếu word alignment được làm đúng:

- `start/end` Gemini + matching là **cửa sổ**, không phải biên cuối; **không cần
  Whisper khớp đủ từ** để giữ câu đúng chỗ.
- DP token-level cho phép:
  - từ reference không có heard word → nội suy giữa hai từ khớp gần nhất;
  - heard word không có trong reference → bỏ/ad-lib;
  - coverage thấp → chia đều trong biên câu + snap RMS.
- Vì câu đã bị giới hạn trong `[start, end]`, thiếu từ không làm câu nhảy sang câu khác.
- Nếu coverage <50% hoặc word confidence thấp:
  - đánh dấu `uncertain`;
  - hoặc chạy Whisper trên clip câu với `initial_prompt = reference line`;
  - hoặc chỉ snap theo RMS.

Điều kiện để không phá:

- **Không dùng DP toàn bài cho word-level**; mỗi câu độc lập.
- Không để word từ câu này tràn sang câu kia; clamp trong biên câu + pad.
- Không yêu cầu Whisper phải “nghe đúng chữ”; chỉ cần đủ anchor để nội suy.

### 10.5. Câu thiếu / câu không chắc

Chấp nhận thiếu:

- Reference line không match → `unsung`.
- Heard line không match → NLV/ad-lib hoặc bỏ.
- Không tạo câu bằng cách nội suy toàn bài.
- UI có panel “câu cần thêm” để người dùng bổ sung.

Đây là điểm khác biệt so với aligner cũ: **không cố đặt đủ 100% câu**, chỉ đặt câu
đủ confidence.

### 10.6. Ưu / nhược

**Ưu:**

- Gemini mạnh ở phân câu/ngữ cảnh tiếng Việt.
- Không phụ thuộc word timestamp của Gemini.
- Whisper word alignment trong câu tránh cascade toàn bài.
- Câu thiếu được đánh dấu thay vì đặt sai.
- Có thể xử lý lời nhập lệch bằng matching.
- Là pipeline mới, có thể tái dùng matching/DP/snap của A; không bị ràng buộc bởi
  aligner cũ.
- Nếu spike thắng, có thể thay thế hẳn A thay vì duy trì hai pipeline.

**Nhược:**

- Reintroduce cloud/Gemini: consent, chi phí, latency, offline.
- Gemini line timestamp vẫn là model-generated, cần đo.
- Gemini có thể bị confirmation bias khi đưa lyrics context.
- Hai model (Gemini + Whisper) → chậm hơn.
- Matching algorithm phức tạp; lặp câu/omission dễ sai.
- Nếu Gemini bỏ sót câu, matching không thể tạo lại timing.
- Nếu thay thế A, cần một giai đoạn song song/feature flag để rollback.

### 10.6.1. Hạ tầng Gemini nên tái dùng

Repo đã có client Gemini trong `karaoke/director.py`:

- `_api_key()` (`director.py:224`) **chỉ đọc `os.environ`** (`GEMINI_API_KEY` /
  `GOOGLE_API_KEY`). `.env` được `server.py:95` (`_load_env`) nạp vào env nếu biến
  chưa có; settings.json không chứa key Gemini;
- `_post_generate()` (`director.py:407`) xử lý `generateContent`, retry khi
  `responseSchema` không hỗ trợ, 429/5xx;
- `DEFAULT_MODEL = "gemini-3.8-flash"`, `INTENT_MODEL`, `ALLOWED_MODELS`
  (`director.py:33`);
- cache digest trong `cache_root("director")`;
- `DirectorError` cho lỗi người dùng.

B nên **extract/tái dùng** các hàm này, không viết client thứ hai. Cần bổ sung:

- Model được phép dùng cho **audio input** (verify model nhận audio, không chỉ ảnh).
- `responseSchema` cho output JSON thay vì chỉ prompt.
- Cache key `(audio_hash, prompt_version, model, git SHA, stable-ts version)`.
- Audio gửi lên: **không gửi WAV**; 16kHz mono Opus/MP3 cho 45–90s (~200–350KB),
  dùng inline base64 dưới hạn request hoặc Files API cho file dài.
- **Upload đúng file đã clean** dùng chung cho Whisper + RMS, không upload bản khác.
- Retry budget + hành vi 429: không được treo job; fallback A.

### 10.6.2. Consent riêng cho audio

Không dùng lại `gemini_consent` (đang có nghĩa "gửi **ảnh** lên Gemini"). Cần:

- `audio_consent` riêng;
- receipt riêng: audio hash, model, prompt version, thời điểm;
- copy pattern có sẵn: `effect_consent_receipt` (`web/src/lib/types.ts:107`,
  `tests/test_project_effects.py:21`) → đổi thành `audio_consent_receipt`;
- copy rõ "gửi vocal stem lên cloud";
- consent tắt → tự fallback A, không chặn job.

Đây là thay đổi sản phẩm, cần đưa vào câu hỏi chốt.

### 10.7. Gate 0 và Spike

#### Gate 0 — 2–4 giờ, trước khi viết matching DP

Mục tiêu: trả lời câu hỏi sống còn "Gemini line timestamp có đủ tốt để làm cửa sổ
không?".

- Lấy 3 clip 60s từ bài dev: **Quả Cam** (dễ), **Bình Minh** hoặc **Bài Ca** (hò),
  **Hào Khí** (trung bình). Không dùng hold-out.
- Gửi 1 request có `responseSchema`, so start/end với gold bằng tay.
- Thử **1 request toàn bài** trên ít nhất 1 bài 4 phút; nếu ổn thì bỏ chunking.
- Thử cả có/không lyrics context.
- Ngưỡng duy nhất:
  - `P90 > 1,0s` → dừng B, chuyển A;
  - `0,8–1,0s` → chỉ tiếp tục nếu biên được tính lại từ word/RMS;
  - `≤0,8s` → pass.
- Chỉ khi pass mới làm matching DP.

#### Spike đầy đủ

1. Gemini line pass + matching.
2. Chạy 2 biến thể: có lyrics context / không context.
3. Đo:
   - line count;
   - line start/end MAE/P90 so gold;
   - matching precision/recall;
   - hallucination rate;
   - số câu `unsung`/extra;
   - word timing P90 sau Whisper.
4. So với A trên cùng bài.

Định nghĩa metric:

- **Matching precision/recall** chỉ tính trên câu `matched` (bỏ `repeat`, `unsung`,
  `extra`); ghi rõ mẫu số.
- **Hallucination** = Gemini trả câu mà:
  - `voiced_ratio` trong `[start,end]` dưới ngưỡng; hoặc
  - duration <0,8s; hoặc
  - không có word Whisper nào trong window.
- **Word onset P90** tách:
  - matched words;
  - interpolated words (không thể đạt 0,2s, không tính vào gate matched).
- **Không tệ hơn A** dùng dung sai tuyệt đối §3.2 (+0,02/+0,05), không dùng "bằng
  hoặc tốt hơn" vì A đã 0,088s ở Quả Cam.

Gate:

- Gate 0: P90 ≤1,0s; nếu 0,8–1,0s chỉ tiếp tục khi biên được tính lại từ word/RMS.
- Line start P90 ≤0,5s (tốt) hoặc ≤0,8s (chấp nhận).
- Matching precision ≥0,9, recall ≥0,85.
- Hallucination ≤5% số câu Gemini trả.
- Word onset P90 matched ≤0,2s; interpolated tách riêng.
- Không tệ hơn A theo dung sai tuyệt đối.
- Chi phí/latency chấp nhận.

Nếu Gate 0 fail: dừng B, làm A. Nếu spike fail: B chỉ opt-in cho bài khó.

### 10.8. Effort

| Việc | Thời gian |
|---|---|
| Gate 0 (3 clip, 1 request vs chunked) | 0,5 ngày |
| Matching DP + repeat/omission + tests | 2–3 ngày |
| Whisper word alignment trong câu | 2 ngày |
| Tích hợp pipeline mới + consent/cache/fallback | 2–3 ngày |
| Benchmark A/B + tune + hold-out | 2–3 ngày |
| **B-only** | **9–12 ngày** |
| Shared Phase 0 + Phase 2 (gồm GT) | 4–7 ngày |
| **Tổng B** | **13–18 ngày** |

Chưa tính chi phí Gemini. Nếu B fail, không cộng dồn với A; chỉ làm Phase 3A
(7–10 ngày).

### 10.9. Điều kiện thay thế Approach A

Trong **phát triển**, B làm trước. Trong **sản phẩm**, B luôn opt-in sau feature flag
cho tới khi pass gate + hold-out; A vẫn là đường offline mặc định/fallback trong giai
đoạn thử.

Chỉ thay thế default khi spike chứng minh:

- line start/end không tệ hơn A theo dung sai tuyệt đối §3.2 (+0,02s bài thường,
  +0,05s bài hò); không dùng "bằng hoặc tốt hơn" vì A đã 0,088s ở Quả Cam;
- matching precision/recall đạt gate;
- hallucination thấp hơn ngưỡng;
- word timing trong câu không tệ hơn A;
- chi phí/latency chấp nhận;
- có feature flag + fallback A.

Nếu không đạt, B chỉ dùng cho bài có hò/ad-lib nhiều hoặc bài A bị cascade nặng.
Mặc định sản phẩm vẫn là A.

---

## 11. Trạng thái triển khai (branch `feat/align-approach-b-gemini-lines`)

Đã làm:

- `karaoke/gemini_lines.py`: Gemini line pass, responseSchema, cache, audio clip,
  offset/clamp, `omitted → unsung`.
- `karaoke/line_match.py`: similarity 0.8 LCS + 0.2 char, SIM_MIN=0.30, DP với gap
  ưu tiên khi tie, repeat pass 2, `MAX_REPEATS=4`.
- `karaoke/approach_b.py`: Whisper chạy trước, Gemini line pass, matching, word-fit
  trong cửa sổ, tính lại biên, snap RMS, fallback A.
- `karaoke/align.py`: tách `transcribe_whisper` để B tái dùng.
- `karaoke/pipeline.py`, `project.py`, `settings.py`, `server.py`: `align_strategy`,
  `gemini_model`, `google_audio_consent`, dispatch + fallback.
- Web: chọn chiến lược B/A, model Gemini, consent audio.
- Tests: `test_line_match.py`, `test_gemini_lines.py`, `test_approach_b.py`.
- `scripts/gate0_gemini_lines.py`: đo line P90 trên clip.

Kết quả test:

- `218` Python tests pass.
- `npx tsc --noEmit` pass.
- Gate 0 live chưa chạy: key trong `.env` trả `401 API key not valid`.

Chưa làm:

- Chunk Gemini (hiện 1 request toàn bài).
- Receipt `audio_consent_receipt`.
- UI badge confidence/NLV trong editor.
- Gate 0/hold-out thật khi có key hợp lệ.


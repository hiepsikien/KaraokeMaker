# AI Scene Director — tự động hiểu ảnh + prompt và dựng hiệu ứng

Tài liệu này trả lời câu hỏi: có thể đưa **một ảnh bất kỳ** + **một prompt tự nhiên**
(ví dụ "mưa rơi, có tiếng mưa, gió làm đèn lồng đung đưa, khói bốc từ bát mỳ và nồi
nước dùng nhẹ nhẹ") rồi hệ thống tự nhận biết object, tự đặt hiệu ứng và render không?

> **Bản 3 — đã chốt theo review kỹ thuật ngày 2026-11-21.**
> Kế hoạch code duy nhất: **`docs/living-still-mvp.md`** — hướng **A: UI-first
> 10 ngày**, 16:9, một effect (mưa), nhân vật C0, review tất cả.
> Các mục 12.4/12.5 là **P1/optional**, không phải spike. 9:16 và C1 là P1/P2.
> Ba ưu tiên sửa: (1) contract toạ độ, (2) tách inventory khỏi planning,
> (3) điểm nối `render.py` + loop theo ảnh + protect strip lời.

## 0. Định vị sản phẩm — "Living Still"

Công cụ này là **lớp living-still** nằm giữa ảnh 2D tĩnh và video motion:

- **Input:** một ảnh tĩnh (có thể từ Midjourney).
- **Output:** một loop nền có **chuyển động vi mô có tính vật lý** + **ambient audio
  tuỳ chọn**.
- **Không phải:** video generator, character animation, music-video editor, hay công
  cụ thay thế phần nhạc.
- **Với karaoke:** nhạc là trung tâm. Nền chỉ làm ảnh "có hồn", không tranh mắt/tai.

Tiêu chí thành công:

- Người xem cảm thấy ảnh sống, nhưng vẫn tập trung vào bài hát.
- Xem 4–5 phút không mỏi mắt, không thấy loop seam.
- Ambient (nếu bật) không lấn vocal/instrumental.
- Mặt/chữ không warp; effect không đi qua vùng lời.
- Có thể tắt hoàn toàn để quay về ảnh tĩnh.

Đây là lý do các ưu tiên là **parallax, light breathing, steam, sway, rain nhẹ,
water shimmer, dust** — không phải surf/spray/3D camera hay hiệu ứng dày đặc.

## Kết luận ngắn

**Có, làm được — nhưng không phải bằng một model, và "universal" cần được định nghĩa
đúng.**

- Depth model chỉ cho hình học. Nó không biết bát mỳ, đèn lồng, rèm là gì.
- Cần một **pipeline nhiều lớp**: hiểu prompt → hiểu ảnh → scene graph → planner →
  renderer procedural → critic.
- Không nên để AI sinh video trực tiếp. Nên để AI **hiểu và lập kế hoạch**, còn
  renderer là thư viện hiệu ứng xác định (deterministic), có mask, depth, loop.
- "Universal" thực tế nghĩa là:
  1. chạy được với nhiều phong cách ảnh (photo, anime, illustration, 3D);
  2. hiểu nhiều cách gọi object (mở rộng bằng open-vocabulary);
  3. degrade gracefully khi không chắc;
  4. người dùng sửa được bằng một cú click.
- "Một click hoàn hảo cho mọi ảnh và mọi prompt" thì hiện chưa ai làm được, kể cả
  showcase — showcase được làm thủ công cho đúng một ảnh.

## 1. Vì sao không thể chỉ thêm "handle" cho depth model

Depth Anything V2 trả về `depth[y, x]`. Nó không có:

- nhãn object ("đèn lồng", "bát mỳ", "rèm");
- mask tách object khỏi nền;
- thuộc tính vật liệu ("vải", "kim loại", "nước");
- quan hệ ("bát nằm trên quầy", "đèn treo dưới mái");
- affordance ("vật này có thể đung đưa", "chỗ này có thể bốc khói");
- vùng nào được phép mưa, vùng nào phải khô.

Vì vậy cần thêm các model \*hiểu\* và một lớp \*biên dịch\*. Depth vẫn là một đầu vào
quan trọng, nhưng chỉ là một trong nhiều đầu vào.

## 2. Kiến trúc đề xuất: AI director + renderer xác định

```text
Người dùng: ảnh + prompt (tiếng Việt/Anh)
        │
        ▼
┌─────────────────────┐
│ 1. Intent Parser    │  LLM / VLM nhỏ, trả JSON theo schema
│    prompt → spec    │  "mưa", "gió", "khói", "tiếng mưa", mức nhẹ
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 2. Perception       │  depth + open-vocab detection + segmentation
│    ảnh → scene      │  + OCR + phân vùng trời/đất/nước + màu/ánh sáng
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 3. Scene Graph      │  object + mask + depth + vật liệu + quan hệ
│                     │  + affordance + confidence
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 4. Planner/Compiler │  spec + scene → effect plan
│                     │  chọn recipe, anchor, tham số, audio, loop
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 5. Renderer         │  particle, cloth, pendulum, bloom, rain, steam…
│    procedural       │  depth-aware compositing, 30 fps, loop kín
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ 6. Critic / QA      │  kiểm tra tự động + VLM review
│                     │  retry hoặc hỏi người dùng
└─────────┬───────────┘
          ▼
   MP4 + project JSON (anchors, spec, confidence)
```

### Nguyên tắc quan trọng

- **AI không render pixel trực tiếp.** AI chỉ ra quyết định: object nào, hiệu ứng
  gì, mức bao nhiêu, ở đâu. Renderer là code xác định, có seed, có loop.
- **Không để LLM sinh code/shader mỗi request.** Rất khó kiểm soát, khó test, có rủi
  ro bảo mật. Dùng một **DSL hiệu ứng cố định** (JSON schema).
- **LLM không được tin tuyệt đối.** Mọi object nó nói phải được perception xác nhận.
  Nếu không tìm thấy, hỏi lại hoặc bỏ qua, không bịa.

## 3. Ví dụ đi hết pipeline với prompt của bạn

> Ví dụ minh hoạ; **schema chốt ở `living-still-mvp.md` §2**. `loop_duration` là độ
> dài loop, không phải timeline. `mask_id` là cache key; `box`/`point` theo contract mới.

Prompt:

> "Tôi muốn có mưa rơi, có tiếng mưa, có gió làm đèn lồng sway, khói bốc lên từ bát
> mỳ và nồi nước dùng nhẹ nhẹ."

### 3.1 Intent Parser → JSON

```json
{
  "effects": [
    {
      "type": "rain",
      "intensity": 0.55,
      "audio": { "enabled": true, "gain_db": -32 },
      "wet_ground": true
    },
    {
      "type": "wind",
      "intensity": 0.30,
      "targets": [
        { "label": "đèn lồng", "motion": "pendulum" },
        { "label": "rèm", "motion": "cloth" }
      ]
    },
    {
      "type": "steam",
      "intensity": 0.25,
      "targets": [
        { "label": "bát mỳ", "part": "opening" },
        { "label": "nồi nước dùng", "part": "opening" }
      ]
    }
  ],
  "protect": ["face", "text", "screen"],
  "loop_duration": 8
}
```

LLM chỉ cần làm phần này. Nó không cần biết toạ độ pixel.

### 3.2 Perception → scene graph

```json
{
  "objects": [
    { "id": "lantern-1", "label": "lantern",
      "box": { "x1": 0.53, "y1": 0.28, "x2": 0.59, "y2": 0.48 },
      "mask_id": "m1", "depth": 0.31,
      "affordances": ["hanging", "swayable"], "confidence": 0.94 },
    { "id": "lantern-2", "label": "lantern",
      "box": { "x1": 0.60, "y1": 0.33, "x2": 0.65, "y2": 0.49 },
      "mask_id": "m2", "depth": 0.28,
      "affordances": ["hanging", "swayable"], "confidence": 0.91 },
    { "id": "bowl-1", "label": "noodle bowl",
      "box": { "x1": 0.26, "y1": 0.72, "x2": 0.35, "y2": 0.79 },
      "mask_id": "m3", "depth": 0.66,
      "affordances": ["container", "steam_source"], "confidence": 0.82 }
  ],
  "regions": [
    { "id": "outdoor", "polygon": [{"x": 0.5, "y": 0}, {"x": 1, "y": 0},
                                    {"x": 1, "y": 1}, {"x": 0.5, "y": 1}] },
    { "id": "dry", "polygon": [{"x": 0, "y": 0}, {"x": 0.5, "y": 0},
                                {"x": 0.5, "y": 1}, {"x": 0, "y": 1}] }
  ],
  "protect": [
    { "label": "face", "box": { "x1": 0.37, "y1": 0.43, "x2": 0.44, "y2": 0.57 } }
  ]
}
```

### 3.3 Planner/Compiler → effect plan

Planner làm những việc mà LLM không nên làm:

- "đèn lồng" → 2 object, chọn cả hai; pivot = **điểm treo/bản lề** derive từ mask +
  support point, cho phép offset lên theo dây; biên độ theo `intensity`.
- "bát mỳ" + "nồi nước dùng" → emitter = tâm mép trên của mask (phần hở), không phải
  tâm object.
- "mưa" → 3 lớp far/mid/near; `dry` là vùng cấm cho mid/near với tolerance <0,5%,
  không đòi 0 tuyệt đối; thêm wet mask cho `ground`.
- "tiếng mưa" → synth audio, mix -32 dB, ducking dưới nhạc.
- "nhẹ nhẹ" → map sang preset `subtle`: biên độ 0.3, mật độ 0.5, alpha thấp.
- Kiểm tra xung đột: nếu object nằm trong `dry` mà prompt muốn mưa lên nó, hỏi lại.

### 3.4 Renderer

Các recipe độc lập, có tham số:

- `rain`: 3 lớp particle + splash + wet reflection.
- `steam`: curl-noise particle tại emitter.
- `pendulum`: local rotate quanh pivot, hoặc cutout + inpaint.
- `cloth`: mesh warp, mép trên pinned, phase theo panel.
- `bloom`: mask emissive + blur + screen.
- `audio_rain`: filtered noise + droplet impulses.

Mỗi recipe nhận `mask`, `depth`, `intensity`, `seed`, `loop_duration`. Renderer
không cần biết prompt.

### 3.5 Critic/QA

Kiểm tra tự động:

- mưa có rơi trong vùng `dry` không? (phải bằng 0)
- hiệu ứng có lọt ra ngoài mask không?
- loop có mối nối không?
- mặt/chữ có bị warp không?
- object có bị nhân đôi / lộ inpaint không?
- màu/ánh sáng có nhất quán không?

Critic bằng VLM (tuỳ chọn):

- "Trong frame này có mưa ngoài trời không? Khói có bốc từ bát không? Đèn lồng có
  đung đưa không?" → tín hiệu QA, không phải phán quyết cuối.

Nếu fail:

- retry với tham số khác;
- hoặc hỏi người dùng click đúng object;
- hoặc fallback về hiệu ứng generic.

## 4. Model stack khả thi

| Vai trò | Lựa chọn | Chạy local trên M5 24GB? | Ghi chú license |
|---|---|---|---|
| Depth | Depth-Anything-V2-Small | Có, ~0,2 s | Apache-2.0; Large là CC-BY-NC |
| Open-vocab detection | GroundingDINO, YOLO-World, Florence-2 | Có, 1–5 s | GroundingDINO Apache-2.0; Florence-2 MIT; kiểm tra từng weight |
| Segmentation | SAM 2, CLIPSeg, anime-seg | Có, 0,5–3 s | SAM 2 Apache-2.0; CLIPSeg MIT; anime-seg cần kiểm tra |
| Scene understanding / affordance | Qwen2.5-VL, InternVL, Florence-2; hoặc Gemini/GPT/Claude | 3B–7B 4-bit được, 5–20 s | Qwen/InternVL theo license từng size; cloud theo điều khoản |
| Intent parsing | Qwen2.5/3, Llama 3.x, Gemma; hoặc cloud | Có, 1–5 s | Theo license từng model |
| OCR bảo vệ chữ | PaddleOCR, Tesseract | Có | Apache-2.0 / Apache-2.0 |
| Inpaint nền bị che | LaMa; hoặc OpenCV Telea | LaMa ~1–2 s | LaMa Apache-2.0 (kiểm tra weight) |
| Audio | DSP procedural (numpy/ffmpeg) | Có, realtime | Không cần model |
| Renderer | OpenCV/NumPy + ffmpeg; optional WebGL | Có | — |
| Runtime | `transformers` + torch MPS; MLX cho VLM local | Có | — |

Đã kiểm tra **trong môi trường demo `/tmp/depthdeps`** (transformers 5.23, torch
2.14): các class `GroundingDinoForObjectDetection`, `Sam2Model`,
`Florence2ForConditionalGeneration`, `Qwen2_5_VLForConditionalGeneration`,
`Owlv2ForObjectDetection` đều có sẵn. Đây **chưa phải** runtime `.venv-karaoke`;
xem 14.6 để biết dependency cần thêm và cách tái lập.

### 4.1 Kết quả test Gemini trên ảnh poster

Trong repo **chưa có code Gemini**, nhưng biến môi trường `GEMINI_API_KEY` đang có và
key hoạt động (đã test, không in key). Gửi ảnh poster 1024×576 + prompt tiếng Việt,
yêu cầu trả JSON scene graph:

| Model | Thời gian | Kết quả |
|---|---|---|
| `gemini-3.1-flash-lite` | ~3,8 s | Box tốt, nhưng ít object và toạ độ point có thể bị đảo trục |
| `gemini-3.8-flash` | ~16,4 s | Box, pivot, opening, vùng outdoor/dry/ground, face protect đều dùng được |
| `gemini-4-neon` | ~93,4 s | Rất chi tiết: 6 panel noren, từng biển neon, bracket, awning… nhưng quá chậm cho MVP |

**Lỗi contract cần sửa ngay.** Hai model trả hai hệ toạ độ khác nhau trong cùng một
schema lỏng:

```text
gemini-3.8-flash:     box [285, 530, 484, 592]  → (ymin, xmin, ymax, xmax)
                      pivot [560, 285]          → (x, y)

gemini-3.1-flash-lite: box [287, 528, 484, 590]
                       pivot [287, 559]         → (y, x)  ← đảo trục
```

Vì vậy **không dùng mảng trần**. Contract chốt (mục 14.1):

```json
{
  "objects": [
    {
      "id": "lantern_front",
      "label": "red lantern",
      "box": {"x1": 0.530, "y1": 0.285, "x2": 0.591, "y2": 0.484},
      "anchors": {"pivot": {"x": 0.560, "y": 0.285}},
      "confidence": 0.98
    }
  ],
  "regions": [
    {"id": "outdoor", "polygon": [
      {"x": 0.5, "y": 0}, {"x": 1, "y": 0},
      {"x": 1, "y": 1}, {"x": 0.5, "y": 1}
    ]}
  ],
  "protect": [
    {"label": "face", "box": {"x1": 0.375, "y1": 0.426, "x2": 0.442, "y2": 0.571}}
  ]
}
```

Kết luận đã sửa:

- Gemini vision đủ tốt để làm **inventory analyst** cho MVP, nhưng **không dùng
  confidence tự báo** để auto (xem 14.3).
- `gemini-3.1-flash-lite`: **intent parser text-only**, không phải scene analyst.
- `gemini-3.8-flash`: **inventory ảnh, prompt-agnostic** (không nhận prompt hiệu ứng;
  xem 14.2). Nếu vẫn truyền prompt thì cache phải theo `(ảnh, prompt)`.
- `gemini-4-neon`: expert mode, không dùng mặc định.
- Luôn validate, transpose nếu point nằm ngoài box, rồi **derive pivot/opening từ SAM
  mask**; không tin point thô. Anchor nồi nước trong test bị thấp hơn miệng nồi thật.

### 4.2 Chi phí thực tế (giá 11/2026, Standard tier)

Giá niêm yết:

| Model | Input / 1M | Output / 1M (gồm thinking) |
|---|---|---|
| `gemini-3.1-flash-lite` | $0,25 | $1,50 |
| `gemini-3.8-flash` | $0,75 | $3,75 (hết 31/12/2026 tăng gấp đôi) |
| `gemini-4-neon` | $3,00 | $15,00 |

**A. Vision test** — ảnh 1024×576 (1.100 image token) + prompt 445 token:

| Model | Input | Output thực tế | Chi phí/round | Quy đổi ~26.000đ/USD |
|---|---|---|---|---|
| `gemini-3.1-flash-lite` | 1.545 | 852 | **~$0,00166** | **~43đ** |
| `gemini-3.8-flash` | 1.545 | 1.064 + 1.460 thinking = 2.524 | **~$0,0106** | **~276đ** |
| `gemini-4-neon` | 1.545 | nhiều thinking (ước tính 8k–15k) | **~$0,15–0,30** | **~4.000–8.000đ** |

Dòng `3.1-flash-lite` ở trên là **vision test**, không phải intent text-only. Đừng dùng
nó để tính chi phí intent.

**B. Intent text-only** — ước tính 600 input + 300 output (chưa chạy riêng):

| Model | Input | Output | Chi phí/round |
|---|---|---|---|
| `gemini-3.1-flash-lite` | ~600 | ~300 | **~$0,0006** |

MVP tách 2 call (intent + inventory):

- intent bằng `3.1-flash-lite`: ~$0,0006 (text-only)
- inventory bằng `3.8-flash`: ~$0,0106 (ảnh, prompt-agnostic)
- **tổng ~$0,0112/round (~290đ)**; nếu gộp 1 call vision+intent thì ~$0,0106–0,011.

Con số `10.000 round ≈ $106` vẫn đúng cho vision test; nếu tách intent, cộng thêm
~$6/10.000 round.

Quy mô:

| Số round/tháng | 3.8 Flash | 3.1 Flash-Lite |
|---|---|---|
| 100 | ~$1,1 (~29kđ) | ~$0,17 (~4,3kđ) |
| 1.000 | ~$10,6 (~276kđ) | ~$1,7 (~43kđ) |
| 10.000 | ~$106 (~2,8trđ) | ~$17 (~430kđ) |

Lưu ý:

- Giá trên là **Standard**; Batch/Flex giảm 50% nếu xử lý offline.
- Từ 01/01/2027, 3.8 Flash tăng gấp đôi → ~$0,019/round.
- Ảnh gốc 1920×1080 có thể tốn thêm vài nghìn image token, vẫn dưới ~2 cent/round.
- Nếu key đang ở **free tier**, round có thể miễn phí trong hạn mức, nhưng nội dung được
  dùng để cải thiện sản phẩm — không phù hợp ảnh riêng tư.
- Chi phí local (depth, SAM 2, render) chỉ là điện năng, không đáng kể.

### 4.3 Luồng gọi Gemini đề xuất (đã sửa)

```text
POST /api/director/analyze   (FastAPI, key chỉ ở server)
  ├─ intent:  prompt → gemini-3.1-flash-lite → Intent JSON
  │            cache theo hash(prompt) + model_version
  ├─ inventory: ảnh (KHÔNG có prompt hiệu ứng) → gemini-3.8-flash
  │            → Inventory JSON: object, box, region, protect, style
  │            cache theo hash(ảnh) + preprocessing + model_version
  ├─ validate: Pydantic + responseSchema; point nằm trong box; transpose nếu cần
  ├─ refine:  box → SAM 2 (local) → mask pixel
  ├─ anchors: derive pivot/opening từ mask (top-center, mép trên), không tin point thô
  ├─ depth:   Depth Anything (local)
  ├─ plan:    compiler local: Intent + Inventory → EffectPlan
  └─ trả về inventory + masks + effect plan + confidence đã hiệu chỉnh
```

- Dùng `response_mime_type: application/json` **và `responseSchema`** + Pydantic.
- Dùng toạ độ có tên (`{"x":..., "y":...}`, `{"x1":..., ...}`) thay vì mảng.
- **Không** gửi prompt hiệu ứng vào inventory call. Nếu vẫn gửi, cache phải theo
  `(ảnh, prompt)` và bỏ tuyên bố "đổi prompt không phân tích lại ảnh".
- Cache riêng: intent theo prompt; inventory theo ảnh; plan theo `(intent, inventory)`.
- Retry/backoff cho 429/5xx; fallback sang local heuristic hoặc manual anchors.
- Thêm consent "gửi ảnh lên Google" trước khi gọi; cho phép tắt và dùng local-only.
- Không log key, không log ảnh/prompt; chỉ log hash + request id.
- Xử lý safety-block/empty candidates/timeout; đặt `maxOutputTokens` và model snapshot
  rõ ràng (mục 14.14).

Gợi ý cho MVP:

- **Cloud VLM** (Gemini/GPT/Claude) cho intent + scene understanding → nhanh, hiểu
  tiếng Việt tốt, ít công tích hợp.
- **Local perception** (Depth + GroundingDINO/Florence-2 + SAM 2) cho mask chính xác.
- **Local renderer** procedural.
- Sau đó mới thay cloud bằng VLM local nếu cần privacy/offline.

Nếu muốn **hoàn toàn local** ngay từ đầu: Qwen2.5-VL-3B/7B 4-bit qua MLX hoặc
transformers, chấp nhận chậm hơn và chất lượng hiểu ảnh thấp hơn cloud một chút.

## 5. "Universal" — định nghĩa và cách đạt

Không nên hứa "mọi ảnh, mọi prompt". Nên định nghĩa universal theo 4 trục:

1. **Đa phong cách**: photo, anime, illustration, 3D render, tranh vẽ.
2. **Đa object**: nhận object bằng open-vocabulary thay vì danh sách cố định.
3. **Đa ngôn ngữ**: prompt Việt/Anh, từ đồng nghĩa ("đèn lồng", "lantern", "chochin").
4. **Đa mức độ chắc chắn**: có confidence, có fallback, có sửa tay.

Cách đạt:

- **Open-vocabulary** thay vì classifier cố định.
- **Scene graph + affordance** thay vì hard-code "nếu có đèn lồng thì sway".
- **LLM/VLM reasoning** để map ý định mơ hồ sang preset.
- **Confidence threshold**: dưới ngưỡng thì không tự làm, hiện gợi ý để người dùng click.
- **Human-in-the-loop**: một cú click sửa mask/pivot là cách thực tế nhất để đạt 95%+
  trường hợp đẹp.
- **Evaluation set** ngay từ đầu: 50–100 ảnh đa dạng + 20 prompt; đo recall, IoU,
  artifact, thời gian, điểm người chấm.

### 5.1 Các mức trưởng thành của "universal"

| Mức | Năng lực | Công sức | Đánh giá |
|---|---|---|---|
| L1 | Danh sách object cố định (đèn lồng, bát, nồi, rèm) + 3–4 hiệu ứng | 1–2 tuần | Chắc chắn làm được, nhưng không universal |
| L2 | Open-vocabulary object + 5–6 hiệu ứng + confidence + fallback | 3–4 tuần | MVP khả thi, phủ 70–80% ảnh phổ biến |
| L3 | Prompt ghép ("mưa nhẹ + gió vừa + khói"), affordance, critic, correction UI | 2–3 tháng | Sản phẩm tốt, phủ 90–95% với một lần sửa |
| L4 | Mọi ảnh, mọi prompt, vật lý/ánh sáng chính xác, inpaint hoàn hảo | Bài toán mở | Không nên hứa |

Mục tiêu thực tế:

- 70–80% ảnh phổ biến: tự động, không cần sửa.
- 90–95%: tự động + một lần sửa nhỏ.
- Phần còn lại: fallback generic hoặc từ chối mềm ("mình chưa chắc cái nào là đèn
  lồng, bạn click giúp").

## 6. Thư viện hiệu ứng và affordance

| Hiệu ứng | Perception cần | Anchor | Renderer |
|---|---|---|---|
| Mưa | vùng trời/ngoài trời, mặt đất, depth | polygon mưa/khô | particle + splash + wet |
| Tuyết | vùng trời, depth | polygon | particle chậm |
| Khói/hơi | object container, phần hở | điểm emitter | curl-noise particle |
| Gió/Sway | object treo/mềm, điểm gắn | mask + pivot | pendulum / local warp |
| Rèm/vải | cloth panel, mép trên | polygon + pinned edge | mesh wave |
| Neon/glow | vùng sáng, màu, vật liệu | mask emissive | bloom + flicker |
| Sương mù | depth, vùng thấp | mask | noise layer |
| Lửa | vùng lửa, vật liệu | mask + emitter | particle + bloom |
| Nước gợn | mặt nước, depth | mask | PDE / ripple |
| Sét | vùng trời | mask | flash + bloom |
| Bụi/tia sáng | vùng tối, cửa sổ | mask | particle + ray |
| Bảo vệ mặt/chữ | face detection, OCR | mask protect | không warp, không mưa |

Gió/Sway hiện nhận cả **khăn/headband, scarf tail, lọn tóc/ponytail, vạt áo/coat/
robe/cape/sleeve/hem**. Inventory prompt yêu cầu tách các phần này khỏi box `person`
và gắn pivot ở nút buộc/gốc tóc/vai/gấu áo. Nếu prompt nhắc một target gió mà lượt
inventory chung bỏ sót, director chạy thêm **một lượt inventory focus** cho đúng target
đó (có cache riêng), rồi merge object vào inventory gốc.

Object thủ công có nhãn cloth/hair (khăn, tóc, vải…) đi đường warp; nhãn khác
(hoa, cây, vật thể…) đi đường rigid translate/rotate để cả khối đung đưa. Mask
được mở rộng theo amplitude để silhouette không bị ghim ở viền. Preview 4s và bản
chuẩn 8/16s có số cycle khác nhau; render bản chuẩn khi cần kiểm tra đúng tốc độ.

Mỗi hiệu ứng là một **recipe** có schema tham số. Thêm hiệu ứng mới = thêm recipe +
perception cue + test, không cần sửa toàn pipeline.

### 6.1 Sóng biển (ocean waves)

**Làm được**, nhưng cần tách "mặt nước" khỏi ảnh và mô phỏng chuyển động, không chỉ
warp cả ảnh. Có 3 mức:

| Mức | Hiệu ứng | Cách làm | Công sức |
|---|---|---|---|
| W1 | Nước lấp lánh, gợn nhẹ | normal-map shimmer + specular, mask mặt nước | 2–3 ngày |
| W2 | Sóng lăn + bọt bờ + phản chiếu | normal map + foam theo shoreline + reflection | ~1 tuần |
| W3 | Sóng vỗ, spray, caustics, âm thanh | Gerstner/FFT ocean shader, headless WebGL | 2–3 tuần |

#### Perception cần gì

- **Water mask**: vùng nước (SAM 2 + prompt "sea/water", hoặc model segmentation
  chuyên nước).
- **Horizon line**: ranh giới trời/nước; không warp vùng trời.
- **Shoreline**: biên nước/đất để đặt bọt và wave run-up.
- **Depth**: để biết vùng gần/xa, scale biên độ sóng theo phối cảnh.
- **Style**: photo/anime/illustration để chọn preset (realistic vs toon).
- **View**: horizon view / aerial / close-up; mỗi loại dùng recipe khác.
- **Light direction**: hướng mặt trời để đặt specular/glitter.
- **Object masks**: thuyền, đá, người, cầu — không warp/refract sai.

Gemini vision có thể trả thêm `water_polygon`, `horizon_y`, `shore_polyline`; SAM 2
refine thành mask pixel.

#### Renderer: công thức cơ bản

1. **Normal map procedural** (không dùng normal tĩnh):
   ```text
   N(x, y, t) = normalize(
       Σ A_i * [ -k_ix * sin(k_i·p - ω_i t),
                 -k_iy * sin(k_i·p - ω_i t),
                 1 ]
   )
   ```
   Chọn `ω_i = 2π * n_i / T` để loop kín sau `T` giây.
2. **Screen-space distortion**: sample ảnh nước tại `uv + strength * N.xy *
   perspective(y)`. Gần horizon giảm biên độ; gần camera tăng.
3. **Specular / sun glitter**: `pow(max(dot(N, H), 0), shininess)` cộng vào vùng
   nước, mạnh ở hướng mặt trời.
4. **Reflection**: lấy vùng trời/phía trên horizon, flip dọc, blur, tint, trộn vào
   nước theo Fresnel; perturb bằng normal map. Với ảnh tĩnh, đây là approximation,
   không phải reflection thật.
5. **Foam**: distance transform từ shoreline; noise tileable + sin để tạo dải bọt
   chạy vào/bờ; chỉ vẽ trong water mask + một biên nhỏ.
6. **Spray** (W3): particle ở đỉnh sóng vỡ, chỉ ở preset `surf`.
7. **Color grade**: tăng blue/teal, giảm chi tiết thừa, thêm sun glitter.

#### Loop

- Sum-of-sines với tần số là bội số của `1/T` → loop chính xác.
- Noise dùng texture tileable; scroll theo hai hướng với chu kỳ chia hết `T`.
- Nếu dùng FFT ocean: generate spectrum tuần hoàn hoặc crossfade 0,5 s.
- Không nên loop bằng cách cắt clip 8 s bất kỳ; sẽ thấy "giật" ở mép.

#### Audio

- Sóng: pink/brown noise + low-pass + LFO biên độ chậm (0,08–0,2 Hz).
- Bọt: band-pass noise burst theo nhịp sóng.
- Mặc định **tắt** hoặc rất nhỏ (-32 dB) vì là karaoke; bật khi người dùng yêu cầu.

#### Hai cách render trong repo

**A. CPU/OpenCV offline (hợp pipeline hiện tại)**

- Normal map + `cv2.remap` + specular + foam + reflection.
- 1080p30, loop 8 s: ước tính 5–20 s tuỳ độ phức tạp.
- Không thêm dependency nặng; deterministic; dễ cache.
- Chất lượng đủ cho W1–W2, nhất là ảnh anime/illustration.

**B. GPU/WebGL offline (chất lượng cao hơn)**

- Three.js + shader Gerstner/FFT; render headless (Playwright) từng frame hoặc
  `readPixels` → ffmpeg.
- Chất lượng W3, có spray/caustics.
- Nhược điểm: thêm Chromium/Node, chậm hơn, phức tạp integration.

Khuyến nghị: **W1–W2 bằng CPU trước**, W3 bằng WebGL sau nếu cần.

Demo CPU đã dựng trên ảnh biển 1920×1080: `/tmp/demo25d/sea_waves.mp4`
(8 s, 1080p30, ~58 s render). Demo gồm nước gợn, swell bands chạy vào bờ,
sun glitter và foam theo crest; water mask và vùng bảo vệ đá được đánh tay.
Đây là mức **W1/W2**; sóng vỗ mạnh, spray và caustics cần W3 shader.

#### Prompt mapping

| Prompt | Recipe |
|---|---|
| "nước lấp lánh", "gợn nhẹ" | `water_shimmer` (W1) |
| "sóng biển nhẹ" | `ocean_waves` preset `gentle` (W2) |
| "sóng vỗ bờ", "bọt trắng" | `ocean_waves` preset `surf` (W2/W3) |
| "có tiếng sóng" | thêm `audio_ocean`, gain thấp |
| ảnh nhìn từ trên cao | `water_ripples` + caustics, không dùng horizon waves |

#### Rủi ro

- Water mask sai → sóng tràn lên bờ/trời. Critic phải check leak ngoài mask.
- Horizon rung → giữ đường horizon cố định, chỉ warp dưới horizon.
- Vật thể trong nước bị warp → object mask + layer compositing.
- Reflection giả có thể lộ; dùng blur + Fresnel + normal perturb để che.
- Ảnh nước đã có sóng vẽ sẵn; warp có thể thành "sliding". Giảm biên độ, thêm
  specular/foam mới thay vì chỉ dịch pixel.
- Loop seam → sum-of-sines/tileable noise, không cắt tuỳ tiện.

#### Chi phí

- Cloud VLM: ~1 cent/ảnh như các hiệu ứng khác.
- Render local: chỉ điện năng.
- Nếu dùng generative video (Gemini Omni): ~$0,07/s 720p, ~$0,70 cho 10 s; không
  loop được, dễ biến dạng — không khuyến nghị cho nền karaoke.

## 7. Planner: dùng compiler, không dùng agent tự do

Có hai cách:

### Cách A — Agent tự do

LLM gọi tool: `detect`, `segment`, `add_rain`, `render_preview`, `critique`… rồi tự
lặp. Linh hoạt nhưng khó test, khó tái lập, dễ lặp vô hạn, tốn token.

### Cách B — Compiler có kiểm soát (khuyến nghị)

```python
spec = llm.parse(prompt, Intent)          # JSON schema, validate
scene = perceive(image)                    # depth + objects + masks + regions
plan = compile(spec, scene)                # raise MissingTarget nếu không đủ
video = render(plan, seed=42)
report = critique(video, spec)             # rule-based + optional VLM
if not report.ok:
    plan = adjust(plan, report)            # retry có giới hạn
```

- LLM chỉ làm parser/planner, không tự chạy vòng lặp vô hạn.
- Nếu thiếu target: trả về câu hỏi cho UI, kèm ảnh + mask gợi ý.
- Có thể thêm agentic retry 1–2 lần, nhưng vẫn nằm trong compiler.

Đây chính là mô hình "AI layer interpret → layer phân tích → render" mà bạn mô tả.

## 8. UX đề xuất

- Một ô prompt: "Bạn muốn cảnh này sống động thế nào?"
- Hiện những gì hệ thống nhận ra: "Tìm thấy 2 đèn lồng, 1 bát, 1 nồi, 1 rèm" +
  confidence.
- Chế độ:
  - **Auto**: tự làm nếu confidence cao.
  - **Review**: hiện mask/anchor để người dùng xác nhận trước khi render.
  - **Manual**: tự vẽ mask/click anchor.
- Preview 3–4 s trước khi render đầy đủ.
- Nếu thiếu object: "Mình không thấy đèn lồng. Click vào đèn lồng hoặc bỏ hiệu ứng."
- Lưu spec + anchors vào `.karaoke.json` để lần sau tái dùng.

## 9. Lộ trình và công sức

### P0 — UI-first spike (10 ngày, sau review)

- Ngày 1: chốt contract toạ độ, responseSchema/Pydantic, test với 2 JSON thật;
  `.env` ignore + deps + `/api/images/preview`.
- Ngày 2: inventory **prompt-agnostic**, cache theo ảnh, usage; mock test.
- Ngày 3–4: UI "Hiệu ứng nền (AI)" trong Khai báo; overlay 16:9 full-res; chọn object;
  lưu `effect_prompt`/`effect_consent` + `effect_plan` + image hash.
- Ngày 5: 10 ảnh ground truth; precision/recall/time/cost.
- Ngày 6–7: SAM 2 + Depth; derive anchor/pivot từ mask; plan JSON.
- Ngày 8–9: render một effect (mưa) theo loop/ảnh; nối `render.py`; protect strip;
  preview 480p.
- Ngày 10: go/no-go + report.
- WebGL W3: nhánh song song, không phải cổng chặn.

### P1 — AI Director MVP (4–5 tuần)

- Intent schema + validation.
- Perception: depth, detection, segmentation, OCR, region masks.
- Scene graph + confidence.
- 5–6 hiệu ứng ưu tiên: rain, steam, sway, glow, fog, ocean.
- Planner/compiler + fallback + câu hỏi làm rõ.
- Auto + review UI hoàn chỉnh; lưu plan/anchors vào dự án.
- Preview video + correction.
- Evaluation set 50 ảnh.

### P2 — Mở rộng + WebGL W3 (4–6 tuần)

- Thêm hiệu ứng: fire, snow, water ripples, light rays.
- WebGL W3 hoàn chỉnh: sóng vỗ, spray, caustics.
- Critic tự động + VLM review.
- Cache, xử lý song song, ước lượng thời gian.
- Anime-specific segmentation / fine-tune.
- Audio ambience + mix tự động.
- Bộ eval 100+ ảnh, regression test.
- (Tuỳ chọn) local VLM thay cloud.

### P3 — Nâng cao

- Auto-inpaint (LaMa) cho cutout sạch.
- Physics tốt hơn (cloth, pendulum, fluid).
- WebGL live preview.
- Hiệu ứng theo nhịp nhạc.
- Học từ correction của người dùng (lưu anchors, few-shot).

Tổng: **10–13 tuần** cho bản có UI + bộ hiệu ứng mở rộng + WebGL W3, chưa kể thời
gian tinh chỉnh chất lượng nghệ thuật. "Universal hoàn hảo" là bài toán mở, nhưng
"universal đủ tốt cho 80–95% ảnh phổ biến" là mục tiêu khả thi.

## 10. Rủi ro và cách giảm

| Rủi ro | Cách giảm |
|---|---|
| LLM bịa object không có | Bắt buộc perception xác nhận; confidence + hỏi lại |
| Segmentation kém trên anime | Dùng anime-seg / fine-tune; cho sửa mask |
| Occlusion, lộ nền khi object động | Local warp cho biên độ nhỏ; LaMa inpaint cho biên độ lớn |
| Mưa rơi sai chỗ | Region masks + depth + critic "0 leak in dry" |
| Mặt/chữ bị warp | Protect masks từ face detect + OCR |
| Prompt mơ hồ ("gió nhẹ") | Map sang preset; hỏi nếu xung đột |
| Latency cao | Cache, model nhỏ, chạy song song, preview 480p |
| Tốn RAM/VRAM | Lazy load, giải phóng model, 4-bit VLM |
| License model | Chỉ dùng weight Apache/MIT cho sản phẩm thương mại |
| Cloud privacy | Cho chọn local-only; không upload nếu người dùng tắt |
| "Universal" bị overpromise | Đặt kỳ vọng theo confidence + correction; đo bằng eval set |

## 11. Khuyến nghị

1. **Làm, nhưng làm theo lớp.** Bắt đầu với compiler + 4–5 hiệu ứng, không cố làm
   mọi thứ ngay.
2. **Cloud VLM cho hiểu ảnh/prompt ở MVP**, local perception cho mask, local renderer
   cho video. Sau đó tối ưu local.
3. **Không dùng generative video** cho hướng này: không loop, không kiểm soát, dễ
   biến dạng mặt/chữ, tốn phí.
4. **Đầu tư vào eval set sớm.** "Universal" là bài toán đo lường, không phải cảm giác.
5. **Cho người dùng sửa.** Một click đúng object là cách rẻ nhất để đạt chất lượng
   showcase với ảnh bất kỳ.

## 12. Quyết định đã chốt (2026-11-21)

| Hạng mục | Chốt |
|---|---|
| Mốc đầu tiên | **UI-first spike 10 ngày**: contract + inventory + UI analyze/overlay; SAM 2 + một effect preview; WebGL song song |
| Bộ hiệu ứng | **Mở rộng** là library; mặc định chỉ micro-motion + ambient nhẹ: parallax, light, steam, sway, mưa nhẹ, water shimmer, dust |
| Mức tự động | **Auto + review khi confidence thấp**; lưu correction |
| Ảnh ưu tiên | **Hỗn hợp**: phong cảnh, anime, nội thất, có người |
| Render sóng | **WebGL/Three.js W3 là nhánh song song**, không chặn milestone 2 tuần; CPU W1/W2 là fallback |
| Âm thanh | **Tắt mặc định**, bật theo prompt, gain thấp (-30 dB) |
| Gửi ảnh Gemini | **Có, kèm consent**; mặc định tắt đến khi người dùng bật |
| Thương mại | **Không, cá nhân** → có thể dùng Depth Anything V2 Large (CC-BY-NC) |
| Model intent | `gemini-3.1-flash-lite`, `thinkingBudget` thấp |
| Model inventory | `gemini-3.8-flash`, **prompt-agnostic**, cache theo hash ảnh + preprocessing + model |
| Key | Chỉ ở server qua env/`.env`; thêm `.env` vào `.gitignore` |
| UI | **Làm ngay trong KaraokeMaker**: mục "Hiệu ứng nền (AI)" ở màn Khai báo + overlay object; màn AI Director riêng sau |
| Phạm vi UI đầu tiên | Prompt, consent, chọn ảnh, preview ~1280, overlay box/anchor, chọn object, lưu prompt/consent + effect_plan + image hash |

### 12.1 Hệ quả về thời gian

Lựa chọn **Mở rộng + WebGL W3 ngay + UI ngay** không còn là MVP 3–4 tuần. Mốc thực tế:

- **Tuần 1**: contract/schema + `.env`/deps + `/api/images/preview`; inventory
  **prompt-agnostic**; UI analyze/overlay trong Khai báo; ground truth 10 ảnh.
- **Tuần 2**: SAM 2 + Depth; derive anchor/pivot; render **một** effect (mưa) theo
  loop/ảnh; preview 480p; go/no-go. WebGL W3 chạy **song song**, không chặn.
- **Tuần 3–6**: AI Director MVP: Auto/Review hoàn chỉnh, 5–6 hiệu ứng
  (mưa/khói/gió/neon/sương/sóng), lưu effect plan vào dự án.
- **Tuần 7–10**: mở rộng hiệu ứng: lửa, tuyết, gợn nước, tia sáng; critic; audio.
- **Tuần 11–13**: WebGL W3 hoàn chỉnh (nếu nhánh song song đạt), eval 100+ ảnh,
  hardening, tài liệu.

Nếu muốn có bản dùng được trong 3–4 tuần, cần cắt scope: bộ cơ bản + CPU W1/W2,
rồi làm W3 và hiệu ứng mở rộng sau.

### 12.2 UI tích hợp trong KaraokeMaker

**Vị trí MVP:** thêm mục **"Hiệu ứng nền (AI)"** ngay trong màn **Khai báo**, sau
phần **Hình**. Không thêm phase mới ở bản đầu để tránh đổi luồng 5 bước hiện tại.

```text
Khai báo
  ├─ thông tin, audio, lời
  ├─ hình (bìa, ảnh nền)
  └─ Hiệu ứng nền (AI)
       ├─ chọn ảnh nền
       ├─ prompt + quick chips ("mưa nhẹ", "khói bát mỳ", "gió đèn lồng", "sóng biển")
       ├─ consent: "Gửi ảnh lên Google Gemini để phân tích"
       ├─ [Phân tích ảnh]
       ├─ overlay box/anchor + danh sách object (checkbox)
       ├─ regions/usage/cost + cảnh báo
       └─ lưu prompt/consent vào .karaoke.json
```

**Component đề xuất:** `web/src/components/ai-effects.tsx`

- Chọn ảnh từ `background_images` (fallback `cover_image`).
- Lấy thumbnail + kích thước qua `/api/images/inspect` (đã có).
- Gọi `/api/director/analyze` với `{image_path, prompt, consent}`.
- Overlay SVG trên ảnh: box object, polygon vùng (outdoor/dry/ground/water),
  điểm pivot/opening; object được chọn tô vàng.
- Danh sách object: checkbox, label, confidence, affordance chips.
- Hiển thị `usage` (token vào/ra/thinking) và `cached`.
- Nút "Phân tích lại" / "Xóa kết quả".
- Lưu `effect_prompt` + `effect_consent` + **`effect_plan` nhỏ** + `effect_image_hash`
  vào `.karaoke.json`; mask/depth để cache vì regenerate được từ box. Cache-only là
  không đủ: máy khác/xoá cache sẽ mất plan và render không reproducible.
- **Mỗi ảnh nền có plan riêng** (list `background_effects` keyed by image path/hash).
  Nếu ảnh đổi, hash lệch → cảnh báo và yêu cầu phân tích lại.
- Overlay dùng `/api/images/preview` (~1280, auto-orient, sRGB), **không dùng thumbnail
  360×202**. Khung overlay mô phỏng đúng `fit_image` 16:9, có letterbox; test ảnh
  dọc/3:2/EXIF xoay.
- Consent theo **hành động phân tích**, không chỉ một boolean trong project; "Xóa kết
  quả" phải xoá cache tương ứng.
- Cover: MVP **không áp effect lên cover**; nếu sau này áp, phải protect vùng title/chữ.
- Khi chưa có ảnh, chưa consent, thiếu key hoặc Gemini lỗi: hiện thông báo rõ,
  không làm hỏng form Khai báo.

**Backend đề xuất:**

- `POST /api/director/analyze` (FastAPI, key chỉ ở server). Endpoint nhận prompt để
  chạy **intent**; bên trong gọi **inventory Gemini không kèm prompt** (xem 14.2).
- `karaoke/director.py`: schema, gọi Gemini, chuẩn hoá box/anchor/polygon,
  cache `~/Library/Caches/KaraokeMaker/director/<hash>.json`.
- Thêm `effect_prompt`, `effect_consent` vào project schema và `fieldsPayload`.
- `.env` + `GEMINI_API_KEY`; thêm `.env` vào `.gitignore`.

**Phase sau:** thêm phase **"Hiệu ứng"** giữa **Biên tập** và **Xuất** để xem video
preview, sửa mask/anchor chi tiết, chọn preset và render thử. Bản đầu chỉ cần
overlay + lưu kế hoạch.

### 12.3 Rủi ro lớn nhất của lựa chọn này

1. **Headless WebGL capture**: Chromium + Playwright trên macOS, đọc frame
   deterministic, tốc độ và độ ổn định. Theo dõi trong tuần 2; **không phải điều kiện
   go/no-go** (xem 14.11).
2. **Water mask/horizon** trên ảnh hỗn hợp: sai mask → sóng tràn lên bờ/trời.
3. **Anime segmentation**: model off-the-shelf có thể kém; cần anime-seg/fine-tune.
4. **Chi phí Gemini** tăng khi chạy eval nhiều ảnh/prompt; cache và batch.
5. **Privacy**: ảnh gửi Google; consent rõ ràng, mặc định tắt.

### 12.4 AI Studio tab — P1 (không phải spike)

**Kết luận: nên tách tab, nhưng làm ở P1 sau spike A.** Spike 10 ngày làm component
`AiEffects` inline trong Khai báo; P1 mount lại **cùng component** vào tab AI Studio.
Không tách app/repo riêng, không viết editor thứ hai.

Lý do:

- **Phát triển độc lập**: tab AI Studio có thể tiến hoá UI, preset, preview, WebGL,
  eval mà không đụng luồng 5 bước của KaraokeMaker.
- **Không duplicate**: dùng chung `karaoke/director.py`, `karaoke/living.py`,
  `karaoke/ocean.py`, cache, model downloads, job runner.
- **Tích hợp chéo sạch**: Studio tạo ra **asset nền động** (loop MP4 + scene graph +
  effect plan). Karaoke chỉ cần chọn asset đó, không cần biết chi tiết renderer.
- **UX rõ**: người dùng phổ thông bấm "Tạo nền động" trong Karaoke; người muốn chỉnh
  sâu mở tab AI Studio.
- **Sau này mở rộng**: có thể thêm "Tạo ảnh" (Gemini image), "Tạo cover", "Tạo video
  loop" mà không làm nặng màn Khai báo.

#### Kiến trúc đề xuất

```text
Cùng một Next.js app
  ├─ Tab "Karaoke"        (luồng 5 bước hiện tại)
  └─ Tab "AI Studio"      (Xưởng nền AI)
        ├─ ảnh nguồn
        ├─ prompt + consent
        ├─ phân tích Gemini + overlay
        ├─ chọn object / preset hiệu ứng
        ├─ preview loop
        └─ lưu asset (loop.mp4 + plan.json)

Chung một FastAPI backend
  ├─ /api/director/analyze
  ├─ /api/studio/assets
  ├─ /api/studio/render
  └─ job runner + cache + model downloads
```

#### Data model

Studio làm việc với **asset**, không phụ thuộc project karaoke:

```json
{
  "id": "bg_01H...",
  "name": "Quán mỳ đêm mưa",
  "source_image": "/path/bg.jpg",
  "scene_graph": { "objects": [], "regions": {} },
  "effect_plan": { "effects": [] },
  "settings": { "preset": "gentle", "duration": 8, "fps": 30 },
  "preview_path": ".../preview.mp4",
  "loop_path": ".../loop.mp4",
  "created": "2026-11-21T..."
}
```

Karaoke project chỉ tham chiếu:

```json
{
  "background_effects": [
    {
      "image_path": "/path/bg.jpg",
      "asset_id": "bg_01H...",
      "mode": "linked"
    }
  ]
}
```

- `mode: linked` → export dùng `loop.mp4` của Studio.
- `mode: inline` → render tại chỗ từ prompt/plan (quick path).
- `asset_id` giúp tái dùng, không render lại.

#### Thứ tự làm

1. **Spike A (10 ngày):** `AiEffects` inline trong Khai báo — 16:9, một effect, C0.
2. **P1 — Studio tab:** mount lại `AiEffects` + asset store + picker; thêm 9:16,
   steam/sway/light/water, ambient.
3. **P2 — inline quick-create:** modal nhỏ trong Khai báo dùng lại component Studio;
   không viết editor thứ hai.

#### Ưu/nhược

| Ưu | Nhược |
|---|---|
| Phát triển độc lập, ít rủi ro cho luồng karaoke | Thêm 1 tab, điều hướng phức tạp hơn |
| Asset tái dùng cho nhiều bài | Cần schema asset rõ từ đầu |
| Dễ eval/preview/WebGL riêng | Thêm ~1 tuần cho integration |
| Không làm nặng màn Khai báo | Nguy cơ có 2 UI nếu không kỷ luật |

#### Lưu ý

- **Không tạo app/repo riêng** ở giai đoạn này: chi phí đồng bộ schema, auth, model,
  cache, export cao hơn lợi ích.
- Nếu sau này muốn tách repo, chỉ cần tách khi API/schema đã ổn định.
- Phân biệt hai tool trong cùng tab:
  - **Tạo ảnh**: text-to-image, output ảnh tĩnh. Midjourney là nguồn chính; xem 12.5.
  - **Làm ảnh động**: input ảnh tĩnh + prompt, output loop MP4. Đây là pipeline chính
    của AI Studio.
- Không trộn hai tool vào cùng một form; dùng hai mục con rõ ràng.

### 12.5 Tạo ảnh với Midjourney

**Trạng thái 11/2026: Midjourney không có official public API.** Không có API key,
không có bảng giá per-call, không có developer console. Các "Midjourney API" hiện có
(PiAPI, APIFrame, ImaginePro, useapi.net, mjapi.io, Apify…) là **wrapper unofficial**:

- vi phạm Terms of Service của Midjourney (cấm automated access và resale);
- có thể bị suspend/ban account, mất gallery và GPU time;
- phụ thuộc account pool, dễ đứt dịch vụ, không phù hợp đưa vào sản phẩm.

Vì vậy **không nên đưa unofficial API vào KaraokeMaker làm mặc định**.

#### Phương án đề xuất: Midjourney Bridge

Giữ chất lượng Midjourney, không API, không rủi ro ban:

```text
AI Studio → "Tạo ảnh"
  ├─ nhập ý tưởng
  ├─ tạo prompt Midjourney (--ar 16:9, style, negative, version)
  ├─ [Copy prompt & mở Midjourney]
  ├─ người dùng tạo 4 ảnh, chọn/upscale ảnh ưng ý
  ├─ [Import ảnh] hoặc [Theo dõi thư mục tải về]
  └─ ảnh vào AI Studio để phân tích + làm nền động
```

- **Prompt bridge**: AI Studio viết prompt tối ưu cho Midjourney, có `--ar 16:9`,
  mô tả ánh sáng/style, tránh chữ trong ảnh. User copy/paste, giữ creative loop
  4 ảnh + upscale/vary.
- **Import**: file picker hoặc watch folder (`~/Downloads/Midjourney`), tự nhận ảnh
  mới, tránh import trùng.
- **Không cần thêm chi phí**: user đã có subscription Midjourney; import = $0.
- **Lưu metadata**: provider `midjourney`, prompt gốc, file nguồn, ngày tạo.

#### Nếu vẫn muốn API unofficial (chỉ personal)

Có thể làm **adapter tách biệt**, mặc định tắt, có cảnh báo rõ:

- `ImageProvider` interface: `midjourney_import` (default), `midjourney_unofficial`
  (opt-in), `gemini_image`, `flux`, `openai_image`.
- Không lưu credential Midjourney; chỉ lưu key của provider nếu user tự nhập.
- Hiển thị banner: "Không chính thức, có thể vi phạm ToS và bị ban. Dùng cho cá nhân,
  tự chịu rủi ro."
- Fallback về import nếu provider lỗi.

#### Alternative official API (nếu cần automation)

| Provider | Model | Giá tham khảo | Ghi chú |
|---|---|---|---|
| Google | Gemini image / Nano Banana 2.1 | ~$0,0336/ảnh 1K; ~$0,113/ảnh 4K | Official, đã có key trong repo |
| OpenAI | GPT Image 2 | Theo bảng giá OpenAI | Official |
| Black Forest Labs | FLUX qua fal/Replicate | Theo provider | Official, chất lượng cao |
| Ideogram / Recraft | — | Theo provider | Official, tốt cho chữ/typography |

Midjourney vẫn có thể cho chất lượng đẹp hơn ở một số style; vì vậy **Bridge là mặc
định**, API official khác là lựa chọn bổ sung.

#### UI đề xuất cho tool "Tạo ảnh"

- Tab con: **Midjourney (import)** [mặc định] | **Gemini** | **FLUX/OpenAI**.
- Nút "Copy prompt & mở Midjourney".
- Khu vực drop ảnh + nút "Theo dõi thư mục".
- Preview 4 ảnh gần nhất đã import; chọn 1 để đưa sang "Làm ảnh động".
- Ghi rõ: ảnh Midjourney do người dùng tạo và tải về; app không gọi API Midjourney.

#### License / thương mại

- Dự án hiện là **cá nhân**, nhưng vẫn nên giữ mặc định Bridge.
- Nếu sau này thương mại: kiểm tra điều khoản Midjourney theo subscription tier; không
  resell quyền truy cập Midjourney.
- Ảnh import vào app là ảnh user đã tạo; app chỉ xử lý local.

**Studio-first không còn là spike.** Spike chốt **A — UI-first 10 ngày**; Studio tab là
P1, mount lại cùng component `AiEffects` + asset picker. Kế hoạch code duy nhất:
`docs/living-still-mvp.md`.

## 13. Kế hoạch 10 ngày sau review

Mục tiêu: có **UI dùng được trong KaraokeMaker** + chứng minh end-to-end trên
**10 ảnh ground truth**. WebGL W3 chạy **song song**, không phải cổng chặn.

| Ngày | Việc | Điều kiện qua |
|---|---|---|
| 1 | Chốt schema (`box`, `point`, `region`, `plan`), test với 2 JSON thật; `.env` ignore; deps; `/api/images/preview` | Contract + env xong |
| 2 | `director.py`: inventory **prompt-agnostic**, `responseSchema`, cache, usage; mock test | Gọi được Gemini + test không cần mạng |
| 3–4 | UI trong Khai báo: consent, preview ~1280, overlay 16:9, chọn object, lưu plan/prompt + image hash | Analyze end-to-end 1 ảnh |
| 5 | 10 ảnh ground truth; đo precision/recall, thời gian, cost | Có báo cáo, chưa cần render |
| 6–7 | SAM 2 + Depth; derive anchor/pivot từ mask; plan JSON | Mask + plan ổn định trên 5 ảnh |
| 8–9 | Render **một** effect (mưa) theo loop/ảnh, ghép vào `render.py`, protect strip lời, preview 480p; nếu còn thời gian test ambience mưa -32 dB | Video preview trong UI, đo thời gian |
| 10 | Go/no-go + viết report | Quyết định P1 |

**Định nghĩa go/no-go (đo được):**

- **Ground truth** 10 ảnh lập ngày 1: danh sách object chính + box/mask tay.
- Object chính = object có trong ground truth và liên quan hiệu ứng; IoU ≥ 0,5;
  precision/recall ≥ 0,8.
- **Frame timing**: shader dùng virtual clock; frame index `n` phải khớp timestamp
  `n/fps`; lệch ≤ 1 frame.
- **Loop seam**: so frame cuối/đầu với baseline; không vượt ngưỡng perceptual.
- **Rain leak**: đo % pixel mưa trong vùng `dry` với tolerance (ví dụ <0,5%), không
  đòi bằng 0 tuyệt đối.
- **Time/ảnh**: ghi riêng model download, analyze, mask, preview 480p; mục tiêu
  <60 s/ảnh sau khi model đã cache.

Không nằm trong 10 ngày: correction UI đầy đủ (sửa mask/pivot), audio, local VLM,
production hardening, phase "Hiệu ứng" riêng.

## 14. Chốt kỹ thuật sau review

### 14.1 Contract toạ độ và schema

**Schema chốt nằm ở `docs/living-still-mvp.md` §2.** Dưới đây là tóm tắt; nếu khác,
bản MVP thắng.

Một contract duy nhất, normalized 0..1, có tên trường:

```text
Point   {x: float, y: float}
Box     {x1, y1, x2, y2}
Object  {id, label, box, anchors: {pivot?: Point, opening?: Point},
         affordances: [str], confidence: float, source: str}
Region  {id, polygon: [Point]}
Protect {label, box}
Inventory {version, image_hash, width, height, style,
           objects: [Object], regions: [Region], protect: [Protect],
           model, usage}
Intent  {version, effects: [{type, target_labels, intensity, audio, ...}]}
EffectPlan {version, image_hash, profile_id, fit_transform,
            items: [{effect, target_ids, params}]}
```

Quy tắc:

- Gemini trả `box_2d` theo `[ymin,xmin,ymax,xmax]` 0..1000 → server đổi sang
  `Box` 0..1. Không đưa mảng trần ra UI.
- Dùng `responseSchema` + Pydantic; không chỉ `response_mime_type`.
- Validate point nằm trong box; nếu không, thử transpose rồi kiểm tra lại.
- **Derive pivot/opening từ SAM mask** (top-center, mép trên, support point), không
  tin point thô của model.
- Unit test với đúng 2 JSON thật của `3.8-flash` và `3.1-flash-lite` (đã đảo trục).

### 14.2 Tách inventory khỏi planning

```text
Inventory: ảnh → Gemini (KHÔNG prompt) → object/region/protect/style
Intent:    prompt → LLM text → effect intent
Plan:      compiler local (Intent + Inventory) → EffectPlan
```

- Cache inventory theo `hash(ảnh) + preprocessing + model_version`.
- Cache intent theo `hash(prompt) + model_version`.
- Cache plan theo `(intent, inventory)`.
- Chỉ khi giữ scene call có prompt thì cache mới phải gồm prompt, và bỏ tuyên bố
  "đổi prompt không phân tích lại ảnh".
- Compiler local map `target_labels` → object ids; không để Gemini tự quyết định
  hiệu ứng.

### 14.3 Confidence và review

- Confidence Gemini tự báo **không calibrated**; trong test mọi object đều 0,94–0,99.
- Dùng tín hiệu perception: detector score + độ ổn định mask của SAM (IoU khi perturb
  box/point) + consensus giữa 2 lần gọi/nhiều prompt.
- MVP: **review tất cả**, hoặc auto chỉ với object có mask ổn định.
- Không hiển thị confidence VLM như "độ chính xác".

### 14.4 Lưu plan và nhiều ảnh nền

- `.karaoke.json` lưu `background_effects[]`, mỗi item (schema chốt ở MVP §2):
  `image_path`, `image_hash`, `effect_prompt`, `effect_consent_receipt`,
  `effect_plan` (gồm `profile_id`, `fit_transform`, `items[]`), `model`, `created`,
  `asset_id?`.
- Không lưu `effect_*` top-level; mask/depth để cache vì regenerate được từ box.
- Ảnh đổi → hash lệch → cảnh báo, không dùng plan cũ.
- MVP: effect **chỉ áp cho ảnh nền**, không áp cover; nếu sau này áp cover, protect
  vùng title/chữ.
- Nếu nhiều ảnh: mỗi ảnh một plan; không dùng một plan chung mơ hồ.

### 14.5 Checklist thêm field vào project

- `karaoke/project.py`: `empty_project`, `project_from_fields`, `public_project`,
  `prepared_from_project`.
- `karaoke/server.py`: `ProjectFieldsRequest`, `OpenSavedRequest`, session keys.
- `karaoke/pipeline.py`: `prepare_job` / `open_project_file` nếu cần.
- `web/src/lib/types.ts`: `Project`, `ProjectFields`, `emptyFields`,
  `fieldsFromProject`, `fieldsPayload`.
- Test round-trip: save → load → field còn nguyên; mở project cũ thiếu field không lỗi.
- Nhớ `public_project` là **whitelist**: quên field sẽ bị xoá âm thầm khi lưu.

### 14.6 Runtime và dependency

- Máy kiểm chứng: **Apple M5, 24 GB**, `ffmpeg 9.0.2`.
- Demo hiện chạy ở `/tmp/depthdeps` (transformers 5.23, opencv 5.1), **không phải**
  `.venv-karaoke`. Runtime app `.venv-karaoke` hiện chỉ có fastapi, numpy, torch,
  Pillow…; chưa có transformers/opencv.
- Global có transformers 5.22.2 nhưng không có torch → **không tái lập được**.
- Chốt runtime `.venv-karaoke`, thêm pin:
  `requests`, `python-dotenv`, `opencv-python-headless`, `numpy`.
  Khi làm SAM/Depth: thêm `transformers`, `safetensors`, `accelerate`, `timm`
  (kiểm tra `torchvision` nếu SAM 2 cần).
- `.env` và `.env.*` phải vào `.gitignore` **trước khi tạo file**; thêm `.env.example`.
- `GEMINI_API_KEY` hiện chỉ ở shell session; ghi rõ cách set.
- Ghi version thật vào doc; không ghi 5.23 cho app env.

### 14.7 Overlay, preview, letterbox, EXIF

- Thêm `/api/images/preview` trả ảnh ~1280, auto-orient, sRGB, kèm transform metadata.
- Không dùng thumbnail 360×202 để đặt anchor.
- Overlay trong khung 16:9 mô phỏng đúng `fit_image`, có letterbox; test ảnh dọc/3:2.
- `inspect_image` thumbnail chỉ để xem nhanh; preview riêng cho editor.
- Gemini analysis phải `exif_transpose` giống app; nếu không ảnh xoay sẽ lệch.

### 14.8 Điểm nối `render.py` và budget

- Render **một loop cho mỗi ảnh nền duy nhất**, dài đúng `image_duration` (mặc định
  8 s), cache theo `image_hash + plan_hash`.
- Đưa loop vào **stage slideshow** (trước text overlay), giữ nguyên "một picture,
  hai mux" — không render 2 lần.
- Strip lời 280 px: effect render trước lyric overlay nên có thể đi qua vùng chữ;
  text/bar vẽ sau đảm bảo đọc được. Protect box cho mặt/logo vẫn giữ.
- Budget thực tế: demo 8 s ≈ 58 s ≈ **7× realtime**. Bài 4 phút:
  - 30 ảnh unique ≈ 29 phút;
  - 5 ảnh lặp ≈ 5 phút.
- Preview 480/720p; chỉ render full 1080p ở export.
- Loop duration = `image_duration`; nếu `image_duration` đổi, cache key đổi.

### 14.9 Mưa: 3 lớp và metric leak

- **Far**: bị depth che; **mid**: chỉ trong `outdoor`; **near**: screen-space, thưa,
  trừ face/text/strip lời.
- Không mask toàn bộ mưa bằng `dry` (tạo lỗ đa giác giả).
- Critic đo **% leak có tolerance**, không đòi 0 tuyệt đối.
- Loop seam đo bằng so sánh frame cuối/đầu với baseline.

### 14.10 Audio chain

- `_mux` hiện chỉ nhận một audio input; thêm ambience cần `amix`/sidechain.
- Chốt loudness target (LUFS), ducking theo music/vocal, ambience có trong cả bản có
  giọng và không lời không.
- Mặc định tắt; nếu bật, gain thấp và có thể chỉ ở intro/outro.

### 14.11 WebGL W3

- Là **nhánh song song**, không phải điều kiện go/no-go của milestone 10 ngày.
- Drive shader bằng **virtual clock**, capture `readPixels` → ffmpeg.
- Test frame index vs timestamp, không chỉ "≥10 fps capture".
- Nếu headless capture lỗi/chậm, fallback CPU W1/W2 và tiếp tục.

### 14.12 Noren và pivot

- Gemini thường trả **một box** cho noren; recipe panel cần 6 panel. Split theo seam
  dọc hoặc point-prompt SAM từng panel.
- Pivot là **điểm treo/bản lề**, không phải mép trên mask. Với đèn lồng, cho phép
  offset pivot lên theo chiều dài dây; với rèm, pinned edge là một **đường**, không
  phải một điểm.

### 14.13 Bằng chứng thực nghiệm và bộ ảnh eval

- Bộ ảnh test: `docs/eval-manifest.json` (10 core + 2 extra), nguồn local
  `~/Projects/vanxuan/client/Assets/_Project/Resources/Art`.
  Không commit ảnh; chỉ lưu path/metadata.
- Chuyển `/tmp/demo25d` (scripts + JSON) vào `docs/experiments/` hoặc
  `scripts/experiments/` kèm README, model version, `usageMetadata`, license/nguồn ảnh.
- Không commit ảnh poster showcase (bản quyền) và video lớn; dùng `.gitignore` hoặc
  lưu ngoài repo.
- Lưu `usageMetadata` thật:
  - `3.8-flash`: 1.545 in (1.100 image + 445 text), 1.064 out, 1.460 thoughts,
    tổng 4.069.
  - `3.1-flash-lite`: 1.545 in, 852 out, tổng 2.397.
  - `4-neon`: chưa capture usage; cần chạy lại nếu dùng số liệu.
- `sea_waves.mp4`: 1920×1080, 30 fps, 8 s.

### 14.14 Chi tiết P2

- Face/OCR: thêm MediaPipe/InsightFace + PaddleOCR/Tesseract, hoặc ít nhất validate
  face/text từ Gemini; chưa chứng minh "protect text" chỉ bằng Gemini.
- Pin model snapshot; xử lý 404/429/safety-block/timeout/`maxOutputTokens`;
  `gemini-4-neon` chỉ expert mode.
- Prompt/image là untrusted: chỉ dùng output như data, validate schema, không cho LLM
  gọi tool.
- `duration: 8` phải ghi rõ là loop duration hay timeline duration.
- Consent theo hành động phân tích, không chỉ boolean trong project; "Xóa kết quả"
  xoá cache.
- Không log ảnh/prompt; chỉ log hash + request id.
- Tách bảng chi phí intent text-only và vision test (đã sửa ở 4.2).

### 14.15 Ba việc phải sửa trước khi code

1. **Chốt một contract toạ độ** + validate/derive từ SAM mask.
2. **Tách inventory (prompt-agnostic) khỏi plan**, lưu plan + image hash vào project.
3. **Viết rõ điểm nối render + loop theo ảnh + budget + protect strip lời**.

### 14.16 Chính sách ảnh đầu vào: linh hoạt

**Không có quy định cứng về kích thước.** Ảnh đầu vào có thể là bất kỳ kích thước/tỉ lệ
nào đọc được; app tự fit vào khung video 1920×1080.

| Stage | Kích thước | Ghi chú |
|---|---|---|
| Input | bất kỳ, khuyến nghị ≥ 1280 px cạnh dài | JPG/PNG/WebP; tự EXIF-orient; chuyển sRGB |
| Gemini inventory | downscale cạnh dài ~1024–1280 | Toạ độ normalized 0..1 nên không phụ thuộc resolution |
| UI overlay | preview ~1280, cùng transform với `fit_image` | Có letterbox 16:9; click/anchor chính xác hơn thumbnail 360 |
| Render | 1920×1080, 30 fps | Source lớn hơn được downscale; source nhỏ được upscale |
| Output | 1920×1080 (16:9) | Muốn 9:16/1:1 là output profile khác, chưa làm ở MVP |

#### Quy tắc đề xuất

- **Không reject** ảnh chỉ vì kích thước/tỉ lệ; chỉ reject file hỏng/định dạng lạ.
- Auto-orient EXIF, convert sRGB, composite alpha PNG lên nền letterbox (hoặc nền user
  chọn) thay vì để đen bất ngờ.
- Ảnh < 720p: vẫn nhận, nhưng cảnh báo "có thể mờ khi lên 1080p".
- Ảnh > 4096 px: downscale trước khi phân tích/render để tránh chậm/RAM; vẫn giữ
  metadata/hash của file gốc.
- Tỉ lệ rất lệch (dọc, panorama >3:1): vẫn nhận; mặc định **contain + letterbox**.
  Thêm tuỳ chọn **cover (crop)** và **blur-fill** cho người dùng.
- Ảnh trong suốt: cho chọn nền composite (letterbox color / blur / màu).
- GIF/HEIC: chưa hỗ trợ; có thể thêm `pillow-heif` hoặc lấy frame đầu của GIF sau.
- Ảnh quá lớn **không** làm Gemini chính xác hơn vì bị downscale; object nhỏ ở xa có
  thể mất. Nếu cần, crop/tile vùng quan tâm thay vì tăng resolution toàn ảnh.
- Midjourney: khuyến nghị `--ar 16:9`, upscale ≥ 1920×1080 để đầy khung; nhưng import
  ảnh tỉ lệ khác vẫn dùng được với letterbox.

#### Fit vs Fill

- **Fit / contain (mặc định hiện tại):** giữ toàn bộ ảnh, viền tối hai bên. An toàn,
  không mất chi tiết.
- **Fill / cover:** phủ kín 1920×1080, crop mép. Đẹp hơn cho ảnh gần 16:9, nhưng mất
  chi tiết rìa.
- **Blur-fill:** ảnh gốc contain + nền blur cover. Hợp cho ảnh dọc/3:2, tránh viền đen.

MVP giữ **Fit**; thêm Fit/Fill/Blur-fill như tuỳ chọn UI ở P2.

### 14.17 Output profile và mobile

**Phải hỗ trợ output profile ngay trong thiết kế**, vì ảnh nền/lyric/effect phụ thuộc
khung đích. Input vẫn linh hoạt; output chọn profile.

| Profile | Kích thước | Tỉ lệ | Dùng cho |
|---|---|---|---|
| `landscape_1080p` | 1920×1080 | 16:9 | YouTube, TV, mặc định cũ |
| `portrait_1080x1920` | 1080×1920 | 9:16 | TikTok, Reels, Shorts |
| `square_1080` | 1080×1080 | 1:1 | Feed vuông |
| `portrait_4x5` | 1080×1350 | 4:5 | Instagram feed |

MVP mobile: **9:16 trước**; 1:1 và 4:5 ở P2.

#### Safe area cho mobile

- 9:16: chừa **top ~8–10%**, **bottom ~15–18%**, **right ~10–12%** cho UI của app.
- Lyric strip phải nằm **trên vùng bottom safe area**, không sát mép.
- Text/khói/đèn lồng quan trọng không đặt trong vùng UI che.
- Preview trong AI Studio phải hiện overlay safe area để người dùng thấy trước.

#### Refactor render profile

Hiện `media.py`/`render.py` hard-code `WIDTH=1920`, `HEIGHT=1080`,
`STRIP_HEIGHT=280`, `LYRIC_FONT_SIZE=56`, `MAX_LYRIC_WIDTH=1700`… Cần tách:

```python
@dataclass(frozen=True)
class RenderProfile:
    id: str
    width: int
    height: int
    strip_height: int
    lyric_font_size: int
    max_lyric_width: int
    lyric_row_gap: int
    bottom_safe: int
```

- `fit_image(size=profile.size)`, `make_cover(profile)`, `inspect_image` trả note theo
  profile.
- `render.py` dùng profile cho text layout, strip, overlay position; giữ “một picture,
  hai mux” **cho mỗi profile**.
- Project JSON thêm `output_profile` (default `landscape_1080p`) và danh sách profile
  nếu xuất nhiều bản.
- File naming thêm profile: `(có giọng, dọc)`, `(không lời, dọc)`…

#### AI Studio / effect plan

- Profile selector ở đầu Studio; preview canvas đổi aspect theo profile.
- Toạ độ vẫn normalized 0..1; nhưng phải lưu **fit transform** (`contain` / `cover` /
  `blur-fill`) để map anchor từ ảnh gốc sang khung đích.
- Với `cover`, object ngoài vùng crop phải bị loại khỏi plan hoặc cảnh báo.
- Midjourney prompt bridge thêm `--ar` theo profile: `16:9`, `9:16`, `1:1`, `4:5`.
- Có thể dùng cùng một ảnh cho nhiều profile (fit/blur-fill), hoặc tạo ảnh riêng cho
  từng profile (`--ar 9:16`) để đầy khung hơn.

#### Export

- Với N profile: render N picture, mux 2 audio bản → **2×N video**.
- 9:16 1080×1920 có **cùng số pixel** với 1920×1080 (2.073.600); so với preview
  720×1280 (921.600) thì full nặng ~2,25×.
- Nếu chỉ xuất mobile, bỏ 16:9 để tiết kiệm thời gian.

#### Test

- Ảnh ngang 16:9 → output 9:16: fit, fill, blur-fill; anchor map đúng; lyric không bị
  UI che; không cắt chữ.
- Ảnh dọc 9:16 → output 16:9.
- 1:1 và 4:5: lyric wrap, font size, safe area.
- Ảnh EXIF xoay, PNG trong suốt.

#### Thời gian

- Refactor profile-aware + 9:16 safe area: **+3–5 ngày**.
- UI chọn profile + preview mobile + test: **+3–5 ngày**.
- Nằm trong P1; 1:1/4:5 sang P2.

### 14.18 Motion budget, ambient audio và tiêu chí "có hồn"

#### Motion budget mặc định

| Hiệu ứng | Mặc định | Ghi chú |
|---|---|---|
| Parallax / camera drift | ±1–1,5% khung | Không fly-through, không zoom mạnh |
| Sway đèn lồng/rèm | ±2–3°, chu kỳ 4–8 s | Pivot đúng điểm treo |
| Light breathing | ±1–2% | Rất nhẹ, không nhấp nháy liên tục |
| Steam | mật độ thấp, alpha 10–20% | Bát/nồi, không che mặt |
| Mưa | 3 lớp, mật độ nhẹ | Không storm mặc định |
| Nước | W1/W2, biên độ nhỏ | W3 chỉ khi được yêu cầu |
| Dust/light rays | thưa | Không hạt dày |
| Lyric strip | Text/bar trên cùng | Effect có thể chạy phía sau; text/bar vẽ sau |

Preset:

- `subtle` — mặc định cho karaoke.
- `medium` — rõ hơn, vẫn không tranh nhạc.
- `showcase` — cho demo/thumbnail, không dùng mặc định.

#### Ambient audio

- Mặc định **tắt**; chỉ bật khi prompt yêu cầu.
- Gain **-30 đến -35 dB** so với nhạc; sidechain duck dưới vocal.
- High-pass 150–250 Hz để không lấn bass/trống.
- Loop crossfade; có thể chỉ phát ở intro/outro.
- Procedural: mưa, gió, sóng, room tone. Không one-shot SFX (sấm, chim) mặc định.
- Có trong cả bản có giọng và không lời, cùng một mức.

#### Tiêu chí "có hồn" (acceptance)

- **Alive but not busy**: xem 4 phút vẫn thấy ảnh sống, không mỏi mắt.
- **Music-first**: bật/tắt ambience không làm giảm độ rõ của vocal/instrumental.
- **Loop invisible**: không thấy mối nối ở 8 s hoặc `image_duration`.
- **No warp**: mặt, chữ, logo không biến dạng.
- **No leak**: mưa/khói/nước không lọt vào vùng cấm quá tolerance.
- **Tắt được**: một toggle trả về ảnh tĩnh gốc.

#### Không làm mặc định

- Không billboard/3D camera fly-through.
- Không full character rig/lip-sync mặc định; chỉ micro-motion C1/C2 nếu mask/landmark
  đủ tốt (xem 14.19).
- Không mưa bão, sấm sét, lửa dày đặc.
- Không ambient to, không one-shot SFX.
- Không generative video.

Các hiệu ứng "showcase" (W3 surf/spray/caustics, lightning, snowstorm) vẫn có trong
thư viện nhưng **không phải điều kiện của bản karaoke**. Với karaoke, bộ micro-motion
+ ambient nhẹ là đủ.

### 14.19 Nhân vật: chỉ micro-motion, không full rig

**MVP dùng C0.** C1/C2 là **P2 opt-in**; không lip-sync, không walking, không gesture
tự động.

| Mức | Làm được | Cần gì | Mặc định |
|---|---|---|---|
| **C0** | Nhân vật tĩnh, scene động quanh họ | person/face mask | **MVP** |
| **C1** | Thở, tóc/áo bay, chớp mắt thưa | person/face/hair/clothes mask + landmarks | **P2 opt-in** |
| **C2** | Head tilt/gaze/weight shift rất nhẹ | landmarks + rigid card/mesh | Optional |
| **C3** | Lip-sync, gesture, walking | model/rig riêng (LivePortrait/Wav2Lip/Live2D) | **Không làm** |

#### Recipe

- **Breathing:** torso mask, scale/translate ±0,3–0,8%, pinned ở hông/chân. Tần số
  phải là `n/T`; với T=8 s dùng 0,125 Hz (n=1) hoặc 0,25 Hz (n=2), **không dùng 0,2 Hz**.
- **Hair/clothes:** mesh/local warp, pinned roots, wind field ±2–4 px; cùng hướng gió
  với rèm/đèn lồng.
- **Blink:** eye landmarks + eyelid patch; 100–150 ms, **phase cố định trong loop**
  (ví dụ T=8 s: blink ở 2 s và 6 s), không random qua biên. Nếu landmark/mask không
  chắc, **bỏ blink** thay vì làm mắt méo.
- **Head:** rigid card xoay ±0,5–1° quanh neck pivot; face được protect, không warp
  từng pixel.
- **Gaze:** dịch pupil rất nhỏ, optional; ảnh photo dễ uncanny nên cân nhắc.
- **Không** dùng per-pixel depth warp trên mặt; không lip-sync mặc định.

#### Perception cần gì

- Person/face/hair/clothes mask.
- Landmarks: vai, cổ, hông, mắt (MediaPipe/InsightFace; anime cần model anime-specific).
- Pose: portrait / half-body / full-body.
- `is_main_subject`, style (photo/anime), confidence.
- Nếu confidence thấp hoặc mask lỗi → tự động về **C0** (nhân vật tĩnh).

#### Guardrail

- Face identity check: không đổi cấu trúc mặt; không warp mắt/miệng.
- Không seam ở cổ/tóc/vai; feather mask đủ rộng.
- Blink artifact check; nếu fail, tắt blink.
- Nhân vật không phải chủ thể chính → C0.
- Với karaoke, nhân vật nền **không nên "diễn"**; thở + tóc bay nhẹ là đủ.
- MVP C0; C1 opt-in ở P2. Nếu bật C1, dùng preset `subtle`.

#### Chi phí / hiệu năng

- Segmentation + landmarks local: ~1–3 s/ảnh, không thêm phí cloud.
- Rigid card/mesh rẻ hơn per-pixel warp; phù hợp loop 8 s.
- C3 (lip-sync) là pipeline khác, nặng và dễ uncanny; không nằm trong scope karaoke.

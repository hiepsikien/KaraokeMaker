# Living Still MVP — bản chốt để code

Tài liệu này là **single source of truth** cho spike 10 ngày. Các tài liệu khác là
nghiên cứu/kiến trúc; khi mâu thuẫn, bản này thắng.

## 0. Quyết định chốt

| Hạng mục | Chốt |
|---|---|
| Hướng spike | **A — UI-first 10 ngày**, UI nằm trong màn Khai báo |
| Output MVP | **16:9, 1920×1080, 30 fps** |
| Effect trong spike | **Một effect: mưa** (các effect khác P1) |
| Nhân vật | **C0 — tĩnh**; C1 (thở/tóc/blink) là P2 opt-in |
| Review | **Review tất cả** object; auto chỉ khi có mask ổn định (P2) |
| WebGL W3 | Nhánh song song, **không phải cổng go/no-go** |
| 9:16 / mobile | **P1**, không nằm trong spike 10 ngày |
| Studio tab | **P1**; component `AiEffects` viết để có thể mount lại |
| Midjourney | **Bridge/import**, không dùng unofficial API |
| Ambient audio | Mặc định tắt; test -32 dB nếu còn thời gian, không thì P1 |
| Consent | Theo **hành động**, lưu receipt; không dùng boolean project |

## 1. Scope

### Trong spike 10 ngày

- Contract toạ độ + schema + `responseSchema`/Pydantic.
- Inventory prompt-agnostic + intent text-only + compiler skeleton.
- UI Khai báo: prompt, consent, preview ~1280, overlay, chọn object, lưu plan.
- SAM 2 mask + Depth; derive pivot/opening từ mask.
- Một effect mưa: 3 lớp, loop quantization, strip_rect, preview 480p.
- 10 ảnh ground truth; đo precision/recall/time/cost.
- Nối `render.py`: loop theo ảnh, một picture, hai mux.

### P1

- Studio tab (mount lại `AiEffects` + picker asset).
- 9:16 / `RenderProfile` đầy đủ; `outputs.profiles`.
- Steam, sway, light breathing, water shimmer, dust.
- Ambient audio + mix chain.
- Auto + review theo confidence đã hiệu chỉnh.

### P2

- Character C1 (thở, tóc/áo, blink) opt-in; C2 head/gaze.
- 1:1 / 4:5.
- Fire, snow, lightning, W3 surf/spray/caustics.
- Local VLM, anime-specific segmentation, correction mask/pivot đầy đủ.

### Không làm

- C3 lip-sync/gesture/walking.
- Generative video.
- Unofficial Midjourney API mặc định.
- Full 3D camera fly-through.

## 2. Schema duy nhất

### 2.1 Project

Bỏ `effect_*` top-level. Chỉ dùng một list:

```json
{
  "output_profile": "landscape_1080p",
  "background_effects": [
    {
      "image_path": "/path/bg.jpg",
      "image_hash": "sha256:...",
      "effect_prompt": "mưa nhẹ, khói bát mỳ",
      "effect_consent_receipt": {
        "timestamp": "2026-11-21T10:00:00Z",
        "image_hash": "sha256:...",
        "provider": "gemini",
        "model": "gemini-3.8-flash"
      },
      "effect_plan": {
        "version": 1,
        "image_hash": "sha256:...",
        "profile_id": "landscape_1080p",
        "fit_transform": {
          "mode": "contain",
          "source_width": 1920,
          "source_height": 1080,
          "frame_width": 1920,
          "frame_height": 1080,
          "scale": 1.0,
          "offset_x": 0.0,
          "offset_y": 0.0
        },
        "items": []
      },
      "model": "gemini-3.8-flash",
      "created": "2026-11-21T10:00:05Z",
      "asset_id": null
    }
  ],
  "outputs": {
    "directory": "/path/out",
    "vocal": "...",
    "instrumental": "..."
  }
}
```

- MVP: một profile, `outputs` flat như cũ.
- P1: đổi `outputs` sang `{ "profiles": { "landscape_1080p": {...}, "portrait_1080x1920": {...} } }`
  + migration.
- `asset_id` chỉ dùng khi có Studio (P1).

### 2.2 Contract toạ độ

```text
Point   {x: float, y: float}          0..1
Box     {x1, y1, x2, y2}              0..1
Object  {id, label, box, anchors: {pivot?: Point, opening?: Point},
         affordances: [str], confidence: float, source: str}
Region  {id, polygon: [Point]}        >= 3 điểm
Protect {label, box}
Inventory {version, image_hash, width, height, style,
           objects: [Object], regions: [Region], protect: [Protect],
           model, usage}
Intent  {version, effects: [{type, target_labels, intensity, audio, ...}]}
EffectPlan {version, image_hash, profile_id, fit_transform,
            items: [{effect, target_ids, params}]}
```

**Hệ quy chiếu:** tất cả box/point/polygon do model trả về là normalized theo **ảnh
gốc sau `exif_transpose`, trước letterbox**. Renderer/overlay áp dụng `fit_transform`
để map sang output frame.

- `fit_transform` + `profile_id` + preprocessing nằm trong plan và cache key.
- Cache key: `image_hash + preprocessing + profile_id + fit_transform + plan_hash`.
- Validate point nằm trong box; nếu không, thử transpose.
- Pivot/opening **derive từ SAM mask**; không tin point thô.
- Region phải >= 3 điểm; ví dụ trong doc cũ 2 điểm là sai.

### 2.3 Field checklist

Thêm/sửa đồng bộ 7 chỗ:

- `karaoke/project.py`: `empty_project`, `project_from_fields`, `public_project`,
  `prepared_from_project`.
- `karaoke/server.py`: `ProjectFieldsRequest`, `OpenSavedRequest`, session keys.
- `karaoke/pipeline.py`: `prepare_job`, `open_project_file`.
- `web/src/lib/types.ts`: `Project`, `ProjectFields`, `emptyFields`,
  `fieldsFromProject`, `fieldsPayload`.
- Test round-trip save → load; mở project cũ thiếu field.
- `public_project` là whitelist: quên field = mất âm thầm.

## 3. Data flow

```text
Intent:    prompt → gemini-3.1-flash-lite (text-only) → Intent
           cache: hash(prompt) + model
Inventory: ảnh (KHÔNG prompt) → gemini-3.8-flash → Inventory
           cache: hash(ảnh) + preprocessing + model
Plan:      compiler local (Intent + Inventory) → EffectPlan
           cache: (intent, inventory, profile, fit_transform)
```

- Inventory không nhận prompt hiệu ứng. Test cũ có prompt nên **phải chạy lại không
  prompt** để chứng minh prompt-agnostic.
- Nếu vẫn muốn scene call có prompt: cache theo `(ảnh, prompt)` và bỏ tuyên bố tái dùng.
- Consent theo hành động: receipt gồm timestamp, image_hash, provider, model; hash/model
  đổi thì hỏi lại.

## 4. Render integration

### 4.1 `RenderProfile`

```python
@dataclass(frozen=True)
class RenderProfile:
    id: str
    width: int
    height: int
    fps: int
    strip_rect: tuple[int, int, int, int]  # x, y, w, h
    safe_top: int
    safe_bottom: int
    safe_left: int
    safe_right: int
    lyric_font_size: int
    max_lyric_width: int
    lyric_row_gap: int
    cover_title_size: int
    cover_artist_size: int
    cover_line_size: int
    cover_max_width: int
    fit_mode: str  # contain | cover | blur-fill
```

MVP chỉ dùng `landscape_1080p`:

```text
1920x1080, strip_rect=(0, 800, 1920, 280),
lyric_font_size=56, max_lyric_width=1700, lyric_row_gap=10
```

P1 9:16:

```text
1080x1920, safe_top=173, safe_bottom=307, safe_right=118,
strip_rect=(0, 1333, 1080, 280)  # portrait_1080x1920
lyric_font_size=48, max_lyric_width=960
```

- `max_lyric_width = width - 2*margin`, không hard-code 1700.
- Cover font/width theo profile.
- `inspect_image` trả note theo profile; UI không hard-code "1920×1080".

### 4.2 Loop quantization

Mọi motion tuần hoàn phải có **số nguyên chu kỳ trên loop T**:

```text
ω = 2π * n / T
period = T / n
speed * T = k * span
```

- Sway phải chọn `period = T/n`, không dùng 4–8 s tùy ý.
- Breathing `0.15–0.25 Hz` phải chọn `n/T`; với T=8, dùng `n=2` (0.25 Hz) hoặc `n=1`
  (0.125 Hz), không dùng 0.2 Hz.
- Blink/splash là event có **phase cố định** trong loop, không random qua biên.
- Test frame cuối/đầu; test riêng splash/blink.
- Demo cũ có splash period 1,1–2,8 s không chia hết 8 s → không dùng làm bằng chứng
  seam sạch.

### 4.3 Lyric strip

- `strip_rect` là vị trí lyric strip, không phải vùng cấm effect.
- Effect render **trước** lyric overlay nên được phép đi qua `strip_rect`; text/bar vẽ sau đảm bảo đọc được và nằm trong `safe_left`/`safe_right`.
- `_draw_strip` tạo RGBA trong suốt; bar tối phía sau chữ giữ độ tương phản trên nền động.

### 4.4 Budget

- Demo 8 s ≈ 58 s ≈ 7× realtime.
- Bài 4 phút, 30 ảnh unique ≈ 29 phút; 5 ảnh lặp ≈ 5 phút.
- Preview 480/720p; full 1080p chỉ ở export.
- 9:16 1080×1920 có **cùng số pixel** với 1920×1080; so với preview 720×1280 thì full
  nặng ~2,25×. (Sửa lỗi "2×" trong doc cũ.)

### 4.5 Outputs

- MVP: một profile, `outputs` flat, `unique_export_names` 2 tên.
- P1: N profile → `outputs.profiles` map; cập nhật `types.ts`, `render_videos`,
  `unique_export_names`, UI download, project JSON.
- Không để profile thứ 2 ghi đè profile thứ nhất.

## 5. Kế hoạch 10 ngày

| Ngày | Việc | Điều kiện qua |
|---|---|---|
| 1 | Schema + contract; test 2 JSON thật; `.env`/deps; `/api/images/preview`; **ground truth 10 ảnh** | Contract + env + GT xong |
| 2 | `director.py`: inventory prompt-agnostic, `responseSchema`, cache, usage; mock test | Gọi được Gemini + test offline |
| 3–4 | UI Khai báo: consent receipt, preview 1280, overlay 16:9, chọn object, lưu plan/hash | Analyze end-to-end 1 ảnh |
| 5 | Đo 10 ảnh GT: precision/recall/time/cost | Báo cáo |
| 6–7 | SAM 2 + Depth; derive anchor/pivot; plan JSON | Mask + plan ổn định 5 ảnh |
| 8–9 | Render mưa: 3 lớp, loop quantization, `strip_rect`, nối `render.py`, preview 480p | Preview trong UI + đo thời gian |
| 10 | Go/no-go + report; quyết định P1 | Quyết định |

- WebGL W3: nhánh song song từ ngày 1, **không chặn**.
- Ambient: test -32 dB nếu còn thời gian; không thì P1.
- C1/9:16: không làm trong 10 ngày.

## 6. Go/no-go

- Ground truth lập **ngày 1**, đo **ngày 5**.
- Object chính = object trong GT, liên quan hiệu ứng; IoU ≥ 0,5; precision/recall ≥ 0,8.
- Review all: UI hiển thị object/mask để xác nhận; chưa auto.
- Frame timing: virtual clock; frame `n` khớp `n/fps`; lệch ≤ 1 frame.
- Loop seam: frame cuối/đầu so baseline; pass nếu dưới ngưỡng perceptual.
- Rain leak: % pixel trong `dry` <0,5% (tolerance), không đòi 0.
- Time/ảnh: tách download, analyze, mask, preview 480p; mục tiêu <60 s sau cache.
- Lyric: text nằm trong safe area; effect có thể chạy phía sau strip.

## 7. Những gì đã sửa theo review

1. Chốt **A — UI-first 10 ngày**; Studio/9:16/C1 là P1/P2.
2. Một schema project duy nhất; bỏ `effect_*` top-level; `regions[]`; `items[]`.
3. C1 → P2 opt-in; MVP C0. Không ghi "khuyến nghị/mặc định" cho C1.
4. 9:16 → P1; MVP 16:9 only.
5. Normalized theo **ảnh gốc sau EXIF, trước letterbox**; có `fit_transform` + cache key.
6. `strip_rect` là vị trí lyric; test text nằm trong safe area và không tràn khung.
7. Loop quantization bắt buộc; sửa splash/blink.
8. `RenderProfile` đủ field; sửa pixel math 9:16; max width theo profile.
9. `outputs` MVP flat; P1 `profiles` map.
10. Viết lại contract; region >= 3 điểm; duration là loop duration.
11. Review all ở MVP; auto P2.
12. WebGL theo dõi, không gate.
13. Consent receipt theo hành động; cache key có profile/fit.
14. Cost: tách intent/vision; 2027 ~$0,0213 nếu cả input/output tăng gấp đôi.
15. GT ngày 1, đo ngày 5.
16. Alpha PNG: `fit_image` phải composite RGBA trước khi convert RGB.
17. Test cũ có prompt → chạy lại không prompt để chứng minh prompt-agnostic.

## 8. Câu hỏi đã trả lời

- **A hay B?** A — UI-first 10 ngày. Studio tab P1; component viết để mount lại.
- **9:16?** P1.
- **C1?** P2 opt-in; MVP C0.
- **WebGL?** Song song, không gate.
- **Review?** Tất cả ở MVP.
- **Midjourney?** Bridge/import.

## 9. Kết quả P0 (2026-11-21)

- Unit tests: **88/88 pass** (`tests.test_karaoke`, `test_director`,
  `test_project_effects`, `test_living`).
- TypeScript: `npx tsc --noEmit` pass; `npm run build` pass.
- API thật: consent=false → 400; cached analyze → 200.
- Gemini thật: inventory + intent chạy; indoor trả `dry`, outdoor trả `outdoor`.
- Render E2E: 2 MP4 1920×1080/30fps, mưa + lyric + mux; lyric nằm trong safe area, effect phía sau strip.
- Eval 10 ảnh core:
  - schema **10/10**, effect type **10/10**.
  - strict label recall: micro 0.692 / macro 0.717.
  - semantic label recall: micro **0.962** / macro **0.967**.
  - cached 10/10; cost lần đầu ~$0.074; thời gian ~197 s.
- Giới hạn P0: chỉ render mưa; 9:16/Studio/C1/SAM/Depth/ambient chưa làm.

## 10. P1 kế tiếp

1. **P1a — xong**: steam, wind/sway, light trong `karaoke/living.py` +
   `render.py`; smoke test all-effects 640×360 1 s = 0.325 s; 1080p 1 s ≈ 8.4 s;
   80 tests pass. Steam plan thật từ nồi đã hiển thị đúng.
2. **P1b — xong**: `RenderProfile` 16:9/9:16, UI selector, safe area, tên file
   `(dọc)`, cover=0 đã sửa. Smoke: portrait 1080×1920/30fps, lyric trên safe bottom,
   16:9 vẫn 1920×1080; 80 tests pass.
3. **P1c — xong**: tab **AI Studio** (`/studio`), preview endpoint
   `/api/studio/render`, video preview; chưa lưu asset vào dự án Karaoke.
4. **P1d — xong**: ambient audio procedural (rain/water/wind), default off,
   mix -32 dB với `normalize=0`; chỉ bật khi `params.audio=true`.
5. **Profile bổ sung — xong**: 16:9, 9:16, 1:1, 4:5; tên file có `(dọc)`,
   `(vuông)`, `(4x5)`.
6. **P2a — xong**: snow (3 lớp, mask outdoor/dry) + water W1 shimmer (region
   liquid/water). Smoke protect boxes respected; test suite pass; 1080p 8 s: snow 39.3 s,
   water 14.9 s; plan thật snow/water render đúng.
7. **P2b — xong**: fire (glow/flicker theo object/region) + fog (noise drift),
   expose trong intent (`lửa/cháy`, `sương mù/fog`); 80 tests pass; smoke
   protect boxes respected; fire thật trên ảnh wildfire đã render.
8. **P3 còn lại**:
   - SAM 2 + Depth masks (thay box/ellipse).
   - C1 character micro-motion (thở/tóc/blink).
   - W3/WebGL surf/spray/caustics.
   - Studio asset persistence + picker trong Karaoke.
   - Xuất nhiều profile cùng lúc (2×N).
   - `blur-fill` fit mode, ducking/sidechain audio.

## 10.1 Sửa theo feedback Studio (ảnh hải chiến)

- **Effect không còn mask strip lời**: background motion render trước lyric overlay với
  `strip_rect=None`, nên sóng/nước/khói hiện đầy đủ; text/bar vẽ sau vẫn đảm bảo đọc được.
- **Inventory v3**: thêm ships/boats, sails, masts, flags, ropes, fire/flames, smoke,
  embers; affordances `wind_movable`, `fire/burn`, `container/steam_source`; region
  `liquid` phủ toàn bộ mặt nước, thêm `fire`/`smoke`. Cache inventory bump v3.
- **Target synonyms**: `biển` → water/liquid/sea, `buồm` → sail, `lửa` → fire,
  `thuyền` → boat/ship, `nồi` → pot, `bát` → bowl.
- **Parser chịu lỗi**: Gemini đôi khi trả số nguyên khổng lồ; parser dùng
  `parse_int=float` và loại toạ độ không hữu hạn thay vì crash.
- **UI**: nút **Áp dụng** thành sticky, màu vàng, kèm hint “bấm Áp dụng rồi Render
  preview”; tránh bị bỏ qua khi object list dài.
- **Studio preview 640 px**: 4 s/5 effect trước mất 38,9 s ở 1080p (proxy timeout →
  “Internal Server Error”); nay render 640×360 trong ~2,6 s. Export Karaoke vẫn full
  profile. Temp file có UUID + lock tuần tự cho preview; traceback in ra terminal.
- **Smoke bốc lên, không warp thuyền**: tắt warp dao động của khói; chỉ dùng particle
  rising (màu sáng hơn nền khói tối) để tạo hướng bốc lên. Cache motion bump để không
  dùng loop cũ.
- **Nước không warp người/wooden stakes**: inventory v6 thêm người đứng/wading dưới
  nước và stakes; water mask cắt object + edge-protect heuristic cho cấu trúc đứng
  mà inventory có thể bỏ sót. Preview/export dùng mask mới.
- **Smoke sparse, không khung chữ nhật**: nguyên nhân khung vuông là mask khói bị cắt
  bằng bounding box của object (ship/fire) và ellipse lớn; sprite to (bán kính tới
  ~100 px) cộng dồn thành mảng mờ hình chữ nhật. Nay plume là các wisp rời, không
  clip theo box/occluder; bán kính 3,6–14 px ở 640×360, ~11–22 hạt/source, vị trí và
  pha ngẫu nhiên. Chỉ `prevent` (vùng lyric) được áp một lần lúc composite. Không
  còn cạnh thẳng nào từ mask khói.
- **Tàn lửa (`embers`)**: particle nhỏ màu cam vàng bốc lên từ fire object/region,
  loop-quantized, có protect/strip. Prompt `tàn lửa`, `tia lửa`, `embers`, `sparks`
  được map sang `embers`; đã có test riêng.
- **Đàn chim (`birds`)**: inventory v7 nhận `bird flock` (box cả đàn); renderer dịch
  nhẹ cả patch có mask ellipse feather (drift ngang 1–3 px + bob dọc 1–2 px ở 640×360),
  chu kỳ nguyên để loop khít. **Chỉ bật khi prompt yêu cầu** (`chim`, `đàn chim`,
  `birds`, `flock`); ảnh có chim không tự động thêm effect.
- **Tia nắng (`sunrays`)**: inventory v7 nhận `sun`/`light source`; fallback là vùng
  sáng ấm nhất phía trên khung. God rays mềm + glow breathing quanh mặt trời, alpha
  thấp, chu kỳ 16 s. **Chỉ bật khi prompt yêu cầu** (`nắng`, `tia nắng`,
  `ánh sáng mặt trời`, `sunrays`, `god rays`); không tự bật vì ảnh có nắng.
- 104 tests pass.

Kết quả trên ảnh `song_coc_bach_dang.png` + prompt:
`mưa nhẹ, ánh lửa cháy, sóng biển dập dềnh, gió làm rung nhẹ buồm`:

- Intent: rain, fire, water, wind.
- Inventory: 2 ships, 2 sails, fire, smoke, flag, boats.
- Wind target_ids: `sail_right`, `sail_left_mid`.
- Preview: mưa, lửa, nước sát foreground, buồm rung; 88 tests pass.

## 10.2 Sửa vòng 2 theo feedback ảnh hải chiến

- **Nước**: mask không còn bị mất khi `items.water.target_ids` trỏ vào object
  (`sea_surface`); vùng `liquid` luôn được giữ và ưu tiên hơn box object. Thêm
  dao động dọc theo cột + glint theo hàng, nên nước foreground tối cũng chuyển
  động, không chỉ dải giữa. Box của mọi object không phải nước (tàu, thuyền, cọc,
  người) bị trừ khỏi mask nước để sóng không làm biến dạng vật thể.
- **Gió/cờ**: thêm synonym `cờ`/`lá cờ` → flag/banner/pennant/ensign; `sail` và
  `buồm` được xem là cloth. Cloth warp được ghim tại `pivot` (cột buồm) và biên
  độ theo kích thước object, thay vì xoay 0,7° gần như không thấy.
- **Khói**: smoke box được dùng làm emitter (không cần `opening`), lấy màu từ
  vùng khói trong ảnh. Painted smoke **không còn warp**; hướng bốc lên do các
  wisp rời, sparse đảm nhiệm (xem mục smoke sparse bên dưới). Inventory v7 yêu
  cầu liệt kê từng cột khói/đám lửa riêng và nhận thêm chim/mặt trời.
- **Mưa**: sửa hướng streak khớp với drift, thêm gió giật loop-quantized, độ dài
  và alpha ngẫu nhiên từng hạt, màu trung tính lạnh hơn, giảm độ dài để mưa nhẹ
  không giống vết xước.
- **Cache**: `MOTION_CACHE_VERSION=17`, `PREVIEW_VERSION=preview_v14`; inventory
  `v7`, intent `v6`.
- **Thứ tự composite**: nước được áp trước mưa/tuyết để hạt mưa không bị warp
  theo sóng.

## 10.3 Sửa theo review PR #5

- **`.env`**: nạp bằng path tuyệt đối, chỉ điền biến thiếu/rỗng; biến môi trường
  thật (khác rỗng) luôn thắng. Không còn `override=True`.
- **`/api/projects/session`**: chỉ ghi các field client thực sự gửi
  (`model_fields_set`), nên request cũ thiếu `output_profile` không kéo project
  9:16 về 16:9.
- **Lyric + safe area**: text dùng `safe_left`/`safe_right`; một font size chung
  cho cả bài, tự thu nhỏ nếu dòng quá dài hoặc vượt số hàng của strip. Thêm wrap
  nhiều hàng thay vì tối đa 2.
- **Effect trước lời**: background motion render với `strip_rect=None`; effect
  được phép chạy phía sau dải lời, text/bar vẽ sau vẫn đảm bảo đọc được.
- **Protect object**: ember sprite bị clip theo toàn bộ `prevent` (không chỉ tâm);
  wind rigid vẫn áp `prevent` lên patch để mặt/chữ được bảo vệ.
- **Hash ảnh**: renderer so `plan.image_hash` với file thật; ảnh bị thay nội dung
  sẽ bỏ qua effect và yêu cầu phân tích lại thay vì áp box cũ.
- **Inspect ảnh**: `fits_frame`/note tính theo `output_profile`; thêm 4:5. Preview
  lớn chỉ trả khi `with_preview=true`, danh sách ảnh chỉ nhận thumbnail.
- **Cache**: thư mục theo platform + `KARAOKE_CACHE_DIR`; có eviction theo số file
  và dung lượng cho living/studio/director.
- **An toàn**: prompt cap 500 ký tự; model phải nằm trong allowlist; lỗi render
  preview không trả raw exception ra client.
- **Intent fallback**: kết quả có `intent_fallback`; UI cảnh báo khi chỉ dùng
  keyword matching.
- **Dọn dẹp**: xoá dead code smoke warp; ambient >600 s được loop; xoá ảnh nền
  cũng xoá effect tương ứng và ambient chỉ lấy từ ảnh còn dùng.

## 11. Bộ ảnh eval

Nguồn (local, user-owned):

```text
~/Projects/vanxuan/client/Assets/_Project/Resources/Art
```

Manifest: **`docs/eval-manifest.json`** — 10 ảnh core + 2 extra. Không commit ảnh vào
repo; chỉ lưu path/metadata.

| # | File | Kích thước | Aspect | Test chính |
|---|---|---|---|---|
| 1 | `still-era02-le-cuoi.png` | 1280×720 | 16:9 | steam/smoke, light, people protect |
| 2 | `still-era02-qua-doi.png` | 1280×720 | 16:9 | smoke/steam, people protect |
| 3 | `disaster_flash_flood.png` | 1280×720 | 16:9 | rain, water, storm |
| 4 | `disaster_great_blizzard.png` | 1280×720 | 16:9 | snow, storm, depth |
| 5 | `disaster_wildfire_lightning.png` | 1280×720 | 16:9 | fire, lightning, emissive |
| 6 | `still-era06-song-cuon-bach-dang.png` | 720×1280 | 9:16 | water, rain, boats |
| 7 | `still-era03-dem-suong-nui-nua.png` | 720×1280 | 9:16 | steam/smoke, fire, people |
| 8 | `still-era06-hoi-nghi-dien-hong.png` | 720×1280 | 9:16 | light rays, dust, interior |
| 9 | `still-era07-bat-song-moc-thanh.png` | 1024×1024 | 1:1 | rain/storm, square output |
| 10 | `hero-nguyen-trai.png` | 896×1344 | 2:3 | character C0, face protect |
| 11 | `relic-coc-tre-lam-son.png` | 928×1232 | 3:4 | fire/emissive, 4:5 |
| 12 | `still-era05-tap-kich-dem-nhu-nguyet.png` | 720×1280 | 9:16 | night, fire, rain, boats |

### External bổ sung (CC0/CC-BY, local-only)

Bộ art gốc không có food close-up, neon street, text-heavy đúng nghĩa. Bổ sung 5 ảnh
từ Wikimedia Commons, tải về `/tmp/eval_external` (không commit):

| File | License | Focus |
|---|---|---|
| `food_ramen_cc0.jpg` | CC0 1.0 | food close-up, steam, bowl, 3:4 |
| `food_pho_cc0.jpg` | CC0 1.0 | food close-up, steam, phở, 4:3 |
| `neon_dotonbori.jpg` | CC BY-SA 4.0 | neon street, water reflection, 9:16 |
| `neon_night1.jpg` | CC BY 2.0 | neon/text sign, bloom, 16:9 |
| `text_ramen_shop.jpg` | CC BY-SA 2.0 | text-heavy, OCR/protect text |

- Source/author/download URL: `docs/eval-manifest.json` mục `external`.
- CC BY/SA cần giữ attribution khi dùng ngoài test local.
- Nếu `/tmp` bị xoá, tải lại từ `download_url` trong manifest.

Coverage:

- Output: 16:9, 9:16, 1:1, 2:3, 3:4.
- Effects: rain, snow, fire, steam/smoke, water, light rays, character.
- Protect: face/people (1, 2, 7, 10), text/logo (external text-heavy).
- Depth: foreground/background rõ (3, 4, 6, 9).
- Food/steam: external food close-up.
- Neon/reflection: external neon street.
- Text-heavy: external menu/sign.

Dùng cho:

- Ground truth ngày 1 (10 ảnh core): label object/box/mask tay.
- Đo precision/recall/IoU ngày 5.
- Test fit/cover/blur-fill khi lên 9:16/1:1/4:5.
- Test C0/face protect; C1 chỉ ở P2.

Lưu ý:

- Ảnh gốc là game art 720–1280 px; khi lên 1080p có thể hơi mềm, nhưng đủ để test
  pipeline. Không dùng làm final quality benchmark.
- Không commit ảnh; nếu cần chia sẻ, dùng contact sheet/thumbnail hoặc đường dẫn.
- Contact sheet tạm:
  - `/tmp/eval_sheet.jpg` — 12 ảnh core/extra.
  - `/tmp/eval_sheet2.jpg` — candidate food/neon/text từ art gốc.
  - `/tmp/eval_external_sheet.jpg` — 5 ảnh external.

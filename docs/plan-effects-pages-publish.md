# Kế hoạch triển khai: hiệu ứng nền, lớp sáng, waveform, trang video và đăng bài

- **Trạng thái:** đã triển khai trên nhánh `feat/effects-pages-publish` (trừ TikTok).
- **Ngày:** 2026-11-22
- **Phạm vi:** KaraokeMaker (FastAPI + Next.js)
- **Thứ tự đã chốt:** 1 → 2 → 3 → 4 → 5

---

## 0. Tóm tắt

| # | Hạng mục | Cách làm | Ước lượng thô |
|---|---|---|---|
| 1 | Mây, tuyết, lá, cành cây, tàn lửa | Mây = warp vùng ảnh do người dùng khoanh (AI chỉ gợi ý). Tuyết/lá = hạt cộng thêm. Cành cây = nhóm object + gió. Tàn lửa = tro + glow ấm | 7–10 ngày |
| 2 | Bokeh + light leak | Lớp phủ toàn video, palette lấy từ ảnh hoặc preset | 1,5–2 ngày |
| 3 | Waveform ngắn | Dải sóng nhỏ, chạy theo nhạc, kéo vị trí | 2,5–3 ngày |
| 4 | Cover / interlude / kết + chữ | Mở rộng `make_cover`, thêm trang nghỉ và trang kết, tuỳ chọn typography | 4–5 ngày |
| 5 | Đăng YouTube (TikTok để backlog) | Trang Đăng bài + hàng đợi + OAuth YouTube; TikTok giữ trong plan nhưng chưa làm | 4–5 ngày (YouTube) |
| 0 | Nền tảng + wiring | Schema v2, normalizer, migration, allowlist | 2 ngày |

Tổng cho phạm vi hiện tại (chưa gồm TikTok): khoảng **21–27 ngày làm việc**. Nếu làm thêm TikTok 5B: **25–32 ngày**. Chưa gồm thời gian chờ YouTube verification.

### 0.1 Đã sửa theo review

| Điểm review | Cách xử lý trong tài liệu |
|---|---|
| P0-1 Interlude sai tầng | Chuyển sang `render_picture`; background chỉ cover + ảnh + outro; `_picture_identity` nhận interlude; MVP banner trong strip |
| P0-2 Interlude default bật, `min_gap` | Default `false`; `LONG_GAP = 8` giữ nguyên; `min_gap >= max(8, COUNTDOWN + 0.5)` |
| P0-3 Thiếu wiring | Thêm bảng wiring Phase 0: `prepare_job`, `open_saved`, `project_session`, `_restore_record`, `grant_project_paths`, `RenderRequest`, types |
| P0-4 Cover style ngoài cache | `_cover_identity` hash `presentation.cover`; định nghĩa `bg_darken = 58`, `veil = 70`, `line_color` |
| P0-5 Mây thủ công không tạo motion | UI thêm item `cloud`; `SUPPORTED_EFFECTS` + nhánh render; không thêm vào `EFFECT_TYPES`; manual endpoint trả `fit_transform` |
| P0-6 Waveform thiếu persist/filter | `POST /api/projects/presentation` (body path), `ExportOptions`/`RenderRequest`; toạ độ tâm; `enable=between`; `outro_frames`; identity đầy đủ |
| P0-7 Overlay stage mơ hồ | Chốt overlay **trong `build_background`**; thêm `overlay_hash` vào `BackgroundBuild`/`Ref`/status |
| Outro frames thiếu chuỗi | Thêm `outro_frames` vào toàn bộ `BackgroundBuild`/`Ref`/status/identity; bump builder version |
| Object trùng tên | Thêm `display_label` (`flag_1`, `flag_2`), giữ `label` ngữ nghĩa; `_plan_objects` mang theo `display_label`/`group_id` |
| Cành cây bị lọc | `_wind_specs` dùng `selected_only=False` khi mở rộng nhóm; chỉ branch/foliage làm target |
| Waveform phủ cover/outro | `enable='between(t,cover,cover+song)'`; clip có `outro_duration`; identity gồm outro |
| YouTube thiếu category | Adapter gửi `snippet.categoryId` default 10/22, không thêm field UI |
| Presentation thiếu waveform | `POST /api/projects/presentation` nhận partial `presentation?` + `waveform?`, trả project đã merge |
| Hash vòng lặp | Tách `base_hash` → `overlay_hash` → `background_hash` |
| Branch target cây | Thêm `_branch_candidate`; target `tree`/trunk bị bỏ, có migration note |
| Publish OAuth/scope/TikTok | Chốt callback server hiện tại; làm 5A YouTube (title/body/kids/privacy/playlist, emoji); TikTok để backlog 5B; không thumbnail/end screen |

**Bổ sung ngoài review:** nhận diện **cành cây theo nhóm** (`group_id`), tự đưa vào `wind` khi prompt yêu cầu; thêm vai trò thủ công "Cành cây (gió)". Xem 5.7. Nâng cấp **tàn lửa/tro bay** để có ánh lửa ấm thay vì bong bóng trắng; xem 5.8. **Lưu nội dung đăng bài** để đăng lại sau khi sửa/xuất lại video; xem 9.5.

---

## 1. Hiện trạng

### 1.1 Pipeline render hiện tại

```
prepare_job()
  └─ render_job()                         karaoke/pipeline.py
       ├─ build_background()              karaoke/background.py
       │    ├─ cover segment              media.make_cover()
       │    ├─ image segments             living.render_motion_loop() (AI effects)
       │    └─ concat → background.mp4
       ├─ render_picture()                karaoke/background.py
       │    └─ _overlay_text()            karaoke/render.py (TEXT_FPS = 12)
       └─ mux_outputs()                   karaoke/background.py
            └─ _mux()                     video + audio → 2 file
```

### 1.2 Điểm mở rộng

| Thành phần | Vị trí | Ghi chú |
|---|---|---|
| Hiệu ứng AI theo ảnh | `karaoke/living.py` | `SUPPORTED_EFFECTS`, plan do Gemini tạo |
| UI hiệu ứng AI | `web/src/components/ai-effects.tsx` | Đã có vẽ box thủ công, chọn object |
| Cover | `karaoke/media.py::make_cover` | 1 layout giữa khung, chữ cố định |
| Profile | `karaoke/profiles.py` | Kích thước, strip lời, cỡ chữ bìa |
| Lời + đếm ngược | `karaoke/render.py` | `LONG_GAP = 8`, `COUNTDOWN = 5` |
| Project schema | `karaoke/project.py` | `PROJECT_VERSION = 1` |
| Export UI | `web/src/components/timing-editor.tsx` | Drawer "Xuất video" |
| Setup UI | `web/src/components/project-setup.tsx` | "Tuỳ chọn xuất" đang rất đơn giản |
| Đăng bài | — | Chưa có |

---

## 2. Nguyên tắc thiết kế

1. **Tương thích ngược:** mọi field mới là optional; project `version: 1` vẫn mở và render như cũ. Không đổi tên field cũ (`cover_duration`, `image_duration`, `background_effects`).
2. **Không bắt buộc AI:** tuyết, lá, bokeh, light leak và mây thủ công phải dùng được khi chưa bật Gemini.
3. **Loop khép kín:** mọi hiệu ứng động dùng thời gian chu kỳ (`u = t / duration`) với số chu kỳ nguyên, để clip 8–16 giây lặp không giật.
4. **Không che lời:** manual overlay (tuyết/lá/bokeh/leak) trừ vùng `strip_rect` khi bật `avoid_lyrics`. Hiệu ứng AI theo ảnh (rain/snow/cloud…) nằm trong background và **không** trừ strip, vì background được dựng trước khi có lời; bar lời che vùng chữ. Ghi rõ điều này trong docs.
5. **Cache có phiên bản:** mỗi thay đổi làm tăng version riêng (`MOTION_CACHE_VERSION`, `COVER_RENDER_VERSION`, `PICTURE_BUILDER_VERSION`, `OVERLAY_RENDER_VERSION`).
6. **Không lưu secret trong project:** token đăng bài nằm ngoài `.karaoke.json`, không gửi xuống browser.
7. **Không chặn export:** lỗi hiệu ứng/đăng bài chỉ ghi log, không làm hỏng video.

---

## 3. Kiến trúc đích

```
prepare_job()
  └─ render_job()
       ├─ build_background()
       │    ├─ cover segment (presentation.cover)
       │    ├─ image segments:
       │    │    ├─ AI effects hiện có
       │    │    ├─ wind cho cành cây (nhóm branch)
       │    │    └─ cloud warp (AI hoặc box thủ công)
       │    ├─ outro segment (presentation.outro)
       │    ├─ concat → base.mp4
       │    └─ apply_overlays()            [MỚI] karaoke/overlays.py
       │         └─ snow, leaf, bokeh, lightleak → background.mp4
       ├─ render_picture()
       │    ├─ interlude banner (strip)     [MỚI] phụ thuộc lines
       │    ├─ lyric strip (typography)
       │    └─ waveform clip               [MỚI] karaoke/waveform.py
       └─ mux_outputs()
```

**Phân chia quan trọng:**

- **Mây** thuộc nhóm **warp theo vùng ảnh**, chạy bên trong `build_background` (giống `wind`, `water`). Không phải hạt phủ toàn khung.
- **Tuyết, lá** thuộc nhóm **hạt cộng thêm toàn video**, chạy trong `apply_overlays`.
- **Bokeh, light leak** thuộc nhóm **lớp sáng toàn video**, chạy trong `apply_overlays`.
- **Overlay stage nằm trong `build_background`** (quyết định P0-7): nhờ vậy nút **"Dựng nền"** và `background_status` phản ánh đúng cả hiệu ứng thêm; preview không bị thiếu tuyết/lá/bokeh/leak. Đổi overlay sẽ làm background identity đổi, nhưng segment ảnh vẫn cache theo `image + plan`, nên chỉ re-concat + một lần encode overlay.
- **Interlude** thuộc tầng **picture** vì phụ thuộc `lines`; tuyệt đối không đưa vào background (P0-1). Background vẫn chỉ là cover + ảnh + outro, đúng contract `docs/karaoke-background-pipeline.md`.
- **Waveform** thuộc lớp phủ lúc ghép lời, chạy trong `render_picture`.


---

## 4. Mô hình dữ liệu v2

Giữ nguyên các field cũ, thêm:

```json
{
  "version": 2,

  "overlay_effects": [
    {
      "id": "ovl_snow_1",
      "kind": "snow",
      "enabled": true,
      "params": { "intensity": 55, "density": 100, "speed": 100, "size": 100 },
      "mask": "avoid_lyrics",
      "seed": 12345
    }
  ],

  "waveform": {
    "enabled": false,
    "source": "instrumental",
    "style": "bars",
    "anchor": "center",
    "x": 0.5,
    "y": 0.70,
    "width": 0.32,
    "height": 0.06,
    "color": "#ffc448",
    "accent": "#ff7a59",
    "opacity": 0.9,
    "background": true,
    "mode": "progress"
  },

  "presentation": {
    "cover": {
      "layout": "center",
      "label": "",
      "show_label": false,
      "title_size": null,
      "artist_size": null,
      "line_size": null,
      "uppercase": false,
      "text_color": "#fff4dc",
      "line_color": "#e8e0d2",
      "accent_color": "#e7b15a",
      "shadow": true,
      "stroke": 0,
      "bg_blur": 0,
      "bg_darken": 58,
      "veil": 70,
      "vignette": 0,
      "scrim": "none",
      "scrim_opacity": 0
    },
    "interlude": {
      "enabled": false,
      "min_gap": 8,
      "mode": "strip",
      "text": "Nhạc dạo",
      "show_next_line": false,
      "fade": 0.4
    },
    "outro": {
      "enabled": false,
      "duration": 4,
      "image": "",
      "title": "",
      "subtext": "Cảm ơn đã xem",
      "handle": "",
      "show_title": true,
      "show_artist": true,
      "fade": 0.6
    }
  },

  "typography": {
    "lyric_size": null,
    "lyric_color": "#fff8ec",
    "sung_color": "#ffc448",
    "bar_color": "#080604",
    "bar_opacity": 196,
    "shadow": true,
    "stroke": 0,
    "row_gap": null
  }
}
```

### 4.1 Quy ước

- `null` = dùng mặc định của profile. Không dùng truthiness cho `0`; ví dụ `row_gap: 0` vẫn là giá trị hợp lệ.
- Toạ độ `x`, `y` của waveform là **tâm** của dải, tỉ lệ 0–1 theo khung hình; `width`, `height` là tỉ lệ kích thước khung.
- `cover_duration` và `image_duration` cũ vẫn là nguồn sự thật; `presentation.cover` chỉ chứa style.
- `presentation.outro.duration` là field mới; `0` = tắt.
- `overlay_effects` là danh sách, mỗi `kind` xuất hiện tối đa một lần.
- `seed` được sinh **một lần** khi thêm effect và lưu vào project; nếu thiếu thì derive ổn định từ `kind + project_path`, không random mỗi lần lưu.
- `bg_darken = 58` và `veil = 70` tái tạo đúng pixel mặc định hiện tại của `make_cover` (`brightness = 1 - 58/100 = 0.42`, veil alpha 70).
- `label` là badge nhỏ phía trên title (ví dụ "KARAOKE"); `cover_line` là dòng phụ dưới artist. Hai field khác nhau, không gộp.
- `line_color` áp cho `cover_line`; `text_color` áp cho title; `accent_color` áp cho artist + label. Ba màu tách biệt, không chồng.
- `overlay_effects[].mask` nhận `none` | `avoid_lyrics`; `none` để tắt mask hoàn toàn.
- Object trong `effect_plan.objects` có thêm `display_label` (duy nhất, ví dụ `flag_1`) và `group_id` (optional, ví dụ `tree_1` cho nhóm cành). Cả hai đều optional để plan cũ vẫn đọc được; `display_label` là UI-only, không được ảnh hưởng `visual_plan_hash`.

### 4.2 Chuẩn hoá và migration

- `PROJECT_VERSION = 2`; mở project v1 rồi lưu sẽ thành v2 (migration một chiều, ghi rõ trong README).
- `empty_project()` thêm default cho các field mới.
- `project_from_fields()` đọc field mới, nếu thiếu thì dùng default (không lỗi).
- `public_project()` xuất đủ field mới.
- `prepared_from_project()` và `sync_project_file()` phải merge thêm `overlay_effects`, `waveform`, `presentation`, `typography`.
- **Deep copy** nested dict/list khi merge để tránh aliasing giữa `project`, `prepared` và payload.
- **Normalizer bắt buộc** (chống JSON sửa tay làm crash UI, giống `_effects_list` hiện có):
  - `_overlay_effects_list`: unique `kind`, clamp `params`, validate `palette`/`mask`, seed ổn định. Mỗi `kind` có schema params riêng:
    - `snow`: `intensity`, `density`, `speed`, `size`.
    - `leaf`: `intensity`, `density`, `speed`, `size`, `palette`.
    - `bokeh`: `intensity`, `density`, `size`, `speed`, `opacity`, `blend`, `palette`.
    - `lightleak`: `intensity`, `speed`, `angle`, `opacity`, `blend`, `palette`.
  - `_waveform_dict`: clamp `x/y/width/height/opacity`, validate màu, `style`, `mode`.
  - `_presentation_dict`: merge default từng nhánh, clamp size/darken/veil, validate layout/mode.
  - `_typography_dict`: phân biệt `None` với `0`, validate màu, size, `bar_color`/`bar_opacity`.
- **Điểm normalize duy nhất:** helper `normalize_plan_objects(plan: dict) -> dict` trong `karaoke/presentation.py` (pure, không import Gemini), gọi từ:
  - `build_effect_plan` (Gemini),
  - `project_from_fields`/`_effects_list` (load/save project),
  - endpoint lưu manual plan.
  Helper sort object theo `(box.y1, box.x1)`, gán `display_label`, dedupe `protect`, giữ `group_id`. UI chỉ hiển thị và có thể gợi ý, không phải nguồn sự thật.
- **Thứ tự đánh số `display_label`:** sort object theo `(box.y1, box.x1)` rồi đánh số, để ổn định giữa các lần load; không phụ thuộc thứ tự payload.
- **`display_label` UI-only:** `_visual_plan` phải loại `display_label` khỏi objects trước khi hash; giữ `group_id` vì nó ảnh hưởng chuyển động.
- **Interlude tương thích v1:** default `enabled = false`. Nếu payload không có key `presentation`, giữ nguyên hành vi cũ; chỉ bật khi người dùng chủ động bật.
- **`min_gap`:** giữ `LONG_GAP = 8` làm hằng số cue chung cho cả `render.py` và `cue.ts`; `presentation.interlude.min_gap` là ngưỡng riêng nhưng phải `>= max(LONG_GAP, COUNTDOWN + 0.5)` (tức >= 8). Không thay thế `LONG_GAP` toàn cục trong MVP.
- **Interlude mode:** chốt **banner trong dải lời** (`mode: "strip"`) cho MVP; không làm card toàn màn hình ở phase này. Banner nằm trong strip, không đè countdown. Có thể thêm `card` sau nếu cần.
- `load_project()` không cần nhánh riêng: `project_from_fields()` đã xử lý thiếu field.

---

## 5. Hạng mục 1 — Mây, tuyết, lá, cành cây, tàn lửa

### 5.0 Tên object duy nhất

Hiện `karaoke/director.py::normalize_inventory` đã tạo `id` duy nhất bằng `_unique_id`, nhưng **`label` vẫn trùng**: hai object cùng là "flag" sẽ có id `flag-1`, `flag-2` nhưng UI hiển thị cả hai là "flag". Trên ảnh và trong danh sách object không phân biệt được cái nào là cái nào.

**Cách sửa:** thêm `display_label` duy nhất, giữ `label` là nhãn ngữ nghĩa.

- Quy tắc:
  - Nếu một label chỉ xuất hiện một lần: `display_label = label`.
  - Nếu xuất hiện nhiều lần: đánh số 1-based **theo thứ tự đọc** `(box.y1, box.x1)`: `flag_1`, `flag_2`, …
  - Dùng `_` đúng như yêu cầu; không đổi `id` hiện có để không phá `target_ids`/asset cũ.
  - Nếu `flag_1` đã tồn tại như một label khác, tiếp tục dùng `used` set để tránh trùng (`flag_1_2` hoặc số tiếp theo).
- Nơi áp dụng:
  - `normalize_inventory()`: gọi `_finalize_display_labels(objects)` sau khi dựng xong danh sách.
  - `_merge_inventory()`: gọi lại sau khi merge base + focus, vì merge có thể thêm object trùng label.
  - `build_effect_plan()` và response `/api/director/analyze`: object trong `effect_plan.objects` cũng có `display_label`.
  - **Điểm normalize duy nhất:** helper `normalize_plan_objects(plan)` trong `presentation.py`, gọi từ `build_effect_plan`, `project_from_fields`/`_effects_list`, và endpoint lưu manual plan. UI có thể gợi ý nhưng server là nguồn sự thật.
  - Manual object trong `ai-effects.tsx`: khi thêm, tính `display_label` từ label + danh sách hiện có; server normalize lại khi lưu.
  - `protect` cũng nên dedupe label tương tự; `regions` đã có `id`, có thể thêm `display_label` nếu UI cần.
- **`_plan_objects` phải mang theo `display_label` và `group_id`**: hiện hàm chỉ trả `id/label/box/anchors/affordances/source`. Thiếu 2 field này thì `_target_ids` và mở rộng nhóm cành đều fail dù plan đúng.
- **`_visual_plan` phải scrub `display_label` đệ quy** ở mọi nơi có objects: `plan["objects"]`, `plan["inventory"]["objects"]`, `item["objects"]`, `item["targets"]`; chỉ giữ `group_id`. Hiện code chỉ copy `plan`/`items`, nên bản copy trong `inventory`/item vẫn làm hash đổi khi chỉ rename.
- `_target_ids()`: thêm `display_label` vào haystack để target `flag_2` chỉ khớp object thứ hai; target `flag` vẫn khớp tất cả như hành vi hiện tại.
- UI:
  - Danh sách object và text trên ảnh dùng `display_label || label`.
  - `title`/tooltip của anchor dùng `display_label`.
  - `DirectorObject` trong `web/src/lib/types.ts` thêm `display_label?: string` **và** `group_id?: string` (optional, tương thích project cũ). Thiếu `group_id` thì UI "Chọn cả nhóm"/badge nhóm không có dữ liệu.
- Tương thích ngược: plan cũ không có `display_label` thì fallback `label`; lần normalize/save tiếp theo sẽ điền.

### 5.1 Mây: warp vùng ảnh, không phải hạt

Mây là vùng pixel có sẵn trong ảnh. Cách làm đúng là **biến dạng nhẹ vùng đó** để nó bồng bềnh, không phải vẽ thêm mây giả.

#### Cách chọn vùng

**Đã chốt: box thủ công, tái dùng nút "Vẽ object".** Không tự bật hiệu ứng mây; chỉ chạy khi người dùng chọn.

1. **Người dùng khoanh tay (cách chính):**
   - Tái dùng nút **"Vẽ object"** đã có trong `ai-effects.tsx`.
   - Thêm lựa chọn vai trò khi vẽ: **"Vật thể (gió)"** hoặc **"Mây (bồng bềnh)"**.
   - Nếu chọn "Mây", object có `affordances: ["cloud_soft"]`; nếu chọn "Vật thể", giữ `["wind_movable"]` như hiện tại.
   - Hỗ trợ nhiều box mây trong một ảnh.
   - Box được lưu vào `effect_plan.objects` với `source: "manual"`, `label: "mây"`.
2. **Gợi ý vùng từ AI (chỉ để đỡ phải vẽ, không tự bật):**
   - Khi người dùng đã chọn hiệu ứng `cloud`, có thể gọi Gemini để gợi ý vùng `cloud`/`mây`, hoặc dùng vùng `sky`/`trời` nếu không tách được mây.
   - Gợi ý này chỉ điền sẵn box; người dùng vẫn phải xác nhận/chỉnh.
   - Tuyệt đối không tự thêm hiệu ứng mây chỉ vì prompt có chữ "mây" hoặc ảnh có mây.

#### Đường không cần Gemini

Hiện `ai-effects.tsx` chỉ cho vẽ khi đã có `analysis`. Thêm nút **"Làm hiệu ứng thủ công"**:

- Gọi endpoint mới `POST /api/director/manual` với `image_path`.
- Server trả về một `DirectorAnalysis` tối thiểu:
  - `image`, `image_hash`, `width`, `height`, `model: "manual"`.
  - `fit_transform`, `profile_id` (để `_plan_objects`/warp tính đúng toạ độ).
  - `objects: []`, `regions: []`, `protect: []`.
  - `effect_plan` rỗng nhưng có `image_hash`, `fit_transform`, `profile_id`.
  - `selected_object_ids: []` (hoặc bỏ hẳn key; nếu có thì phải khớp object).
- Không gọi Gemini, không cần consent.
- `effect_consent_receipt` của effect thủ công ghi `provider: "manual"`, `model: "manual"`.
- Sau đó người dùng vẽ box mây như bình thường.

#### Wiring bắt buộc cho mây thủ công

- Khi người dùng chọn vai trò **"Mây"**, UI phải thêm item vào `effect_plan.items`:
  ```json
  { "effect": "cloud", "target_ids": ["manual-..."], "params": { "intensity": 0.55, "speed": 1.0, "billow": 0.6, "feather": 48 } }
  ```
  giống cách `applyEffect` đang thêm item `wind` cho object thủ công. Chỉ gán `affordances: ["cloud_soft"]` là **không đủ**: `plan_has_motion` chỉ đọc `items`.
- Thêm `"cloud"` vào `SUPPORTED_EFFECTS` **và** thêm nhánh render `cloud` trong `_render_loop`; nếu chưa có nhánh thì không được thêm vào tuple.
- **Không thêm `"cloud"` vào `EFFECT_TYPES`** trong `karaoke/director.py`. `EFFECT_TYPES` là enum cho Gemini intent và nguồn của `keyword_intent`; thêm vào sẽ khiến prompt "mây" tự bật effect, trái quyết định "chỉ bật khi người dùng chọn". Chỉ thêm alias/region matcher riêng phục vụ gợi ý vùng.
- `_active_effects` sẽ tự nhận `cloud` sau khi thêm vào `SUPPORTED_EFFECTS`; đảm bảo `_render_loop` có `_cloud_specs` + `_cloud_frame` trước khi bật.
- Bump `MOTION_CACHE_VERSION` và kiểm tra asset cũ (Studio) không bị `degraded`.

#### Thuật toán warp mây

Thêm vào `karaoke/living.py` (cùng chỗ với `wind`, `water`):

```python
def _cloud_specs(plan, transform, width, height, seconds) -> list[dict]
def _cloud_frame(image, specs, u) -> Image.Image
```

Mỗi spec gồm:

- `box` pixel + `feather` (px).
- `mask`: rounded box hoặc polygon, làm mờ bằng `ImageFilter.GaussianBlur`.
- `protect`: trừ các polygon `protect` và object được đánh dấu foreground (nếu có).
- `amplitude`: 4–28 px theo kích thước vùng và `intensity`.
- `speed`: số chu kỳ nguyên trên toàn clip.
- `billow`: biên độ octave 2.
- `phase`, `seed`.

Công thức gợi ý:

```
dx = A * [ sin(2π(k·u + f1(x,y)))
         + 0.5·sin(2π(2k·u + f2(x,y)))
         + 0.25·sin(2π(3k·u + f3(x,y))) ]
dy = A * 0.6 * [ cos(2π(k·u + g1(x,y))) + 0.5·cos(2π(2k·u + g2(x,y))) ]
```

- `f1..g2` là các hàm sin không gian với bước sóng 120–400 px, lệch pha nhau.
- Mọi thành phần thời gian dùng `k` nguyên → loop khép kín.
- Lấy mẫu bilinear (tái dùng kiểu `_warp_crop` / `_warp_water`).
- Nhân sáng nhẹ: `warped *= 1 + 0.04·sin(2πu + phase)` để mây "thở".
- Blend lại bằng mask đã feather; ngoài mask giữ nguyên pixel gốc.

#### Tham số UI

| Tham số | Khoảng | Mặc định |
|---|---|---|
| Cường độ bồng bềnh | 0–100 | 55 |
| Tốc độ | 20–200% | 100 |
| Độ mềm viền | 0–120 px | 48 |
| Nhịp sáng | 0–100 | 30 |

#### Giới hạn cần biết

- Nếu mây nằm sau mái nhà/nhân vật, cần khoanh box tránh vùng đó hoặc đánh dấu `protect`. Warp không tự hiểu occlusion.
- Box thủ công là MVP. Lasso/brush có thể làm sau nếu box chưa đủ mịn.

### 5.2 Tuyết: nâng cấp thành hạt tuyết

Hiện `snow` là chấm tròn mờ, chỉ bật qua plan AI. Nâng cấp:

- 3 lớp độ sâu:
  - xa: hạt tròn nhỏ, mờ, chậm.
  - giữa: hạt tròn + bông 6 cánh nhỏ.
  - gần: bông 6 cánh rõ, xoay nhẹ, nhanh hơn.
- Pre-render sprite (8–12 kích thước, vài góc xoay) để không vẽ line mỗi frame.
- Giữ nguyên công thức rơi + trôi ngang + quantize chu kỳ của `_make_snow_layers`.
- Thêm tham số `size` (60–160%).
- `MOTION_CACHE_VERSION` tăng.

#### Tránh hai bộ render tuyết khác nhau

Hiện có hai đường:

- **AI snow** trong `living.py` (`_make_snow_layers`, `_draw_snow_layer`) có mask theo ảnh.
- **Manual snow** trong `overlays.py` không mask (trừ dải lời).

Để không lệch look và lệch cache, tách phần hạt/sprite sang `karaoke/particles.py`:

```python
def snow_sprites(...)
def leaf_sprites(...)
def build_snow_layers(width, height, duration, seed, params)
def draw_snow_layer(draw, layer, t, u, height)
```

- `living.py` gọi module này rồi áp mask ảnh.
- `overlays.py` gọi cùng module, không mask ảnh.
- **Tách `particles.py` là bắt buộc trong Phase 1.** `living.py` gọi module này rồi áp mask ảnh; `overlays.py` gọi cùng module, không mask ảnh. Nếu vì lý do kỹ thuật không tách được, phải ghi rõ trong UI đây là **hai hiệu ứng khác nhau** và không trộn nhãn "Tuyết".
- **`PARTICLE_VERSION`** dùng chung cho `particles.py`; cả `living.py` và `overlays.py` phải đưa version này vào cache key, để sửa sprite không làm một bên dùng cache cũ.
- **Precedence ở server:** nếu project sửa tay bật cả AI snow (`background_effects`) và manual snow (`overlay_effects`), **manual overlay thắng**; bỏ AI snow, ghi log. Không dựa vào UI disable. Test bằng project có cả hai.
- **Seed:** sinh một lần khi thêm effect, lưu vào project; nếu thiếu, derive từ `sha256(kind + project_path)` để lần sau vẫn ổn định.

### 5.3 Lá: hạt cộng thêm

- 5–7 hình lá procedurally (ellipse + gân + cuống), pre-render sprite.
- Palette thật: xanh `#4f8a3d`, vàng `#c9a227`, cam `#c96a2b`, đỏ `#a83232`; mỗi lá chọn màu + biến thể.
- 3 lớp; lá rơi chậm hơn tuyết, có:
  - xoay quanh trục dọc (lật mặt),
  - đảo ngang theo gió,
  - dao động nhẹ theo hình sin.
- Mật độ mặc định thấp (khoảng 35–60 lá toàn khung) để không rối.
- Tham số: cường độ, mật độ, tốc độ, kích thước, palette (`spring` / `autumn` / `mixed`).

### 5.4 Nơi render

**Tuyết và lá thủ công:** `karaoke/overlays.py` (mới), được gọi từ **bên trong `build_background`** ngay sau bước concat, trước khi trả `BackgroundBuild` (quyết định P0-7).

- Chạy trên `base.mp4` (cover + ảnh + outro) và ghi ra `background.mp4` cuối cùng.
- Vẽ trực tiếp lên frame RGB bằng numpy, chỉ trong bounding box của từng hạt.
- Không tạo ảnh RGBA full-frame mỗi frame.
- Mask tĩnh (nếu có) nhân vào alpha của hạt.
- Cache theo `overlay_identity`; lưu trong `.karaoke-media/backgrounds/` cạnh background hiện có, để cơ chế `source_output_dir` copy sang thư mục xuất mới hoạt động.
- Nếu không có hiệu ứng nào bật: trả nguyên `base.mp4`, không encode lại.
- **Chi phí:** bật overlay thêm **một lần encode full-length** (base → overlay → picture). Đưa vào estimate; dùng CRF riêng (gợi ý 16) cho overlay để tránh banding trên gradient bokeh/leak.
- **Cleanup:** `cleanup_backgrounds` phải coi file overlay là một phần của background hash; không để lại file mồ côi.
- `BackgroundBuild`/`BackgroundRef`/`to_project` thêm `overlay_hash` (và `overlay_frames`, `overlay_degraded` nếu cần) để `background_status` báo đúng.
- **Overlay lỗi:** nếu `apply_overlays` fail và fallback về base, set `overlay_degraded=True`/`degraded=True`; `background_status` báo stale kèm lý do, UI không hiện "sẵn sàng" khi tuyết/lá/bokeh/leak đã mất.
- **Progress:** chia lại progress của `build_background`: segment ảnh 0–0.70, concat 0.70–0.85, overlay 0.85–1.0. Nếu không, UI báo 100% trước khi overlay xong.

**Tuyết/lá qua AI:** chỉ thêm `leaf` vào `SUPPORTED_EFFECTS` khi `_render_loop` có nhánh `leaf`; nếu chưa có nhánh thì không thêm, tránh `plan_has_motion` báo có nhưng renderer trả `None` → `degraded`. Bản thủ công là mặc định.

### 5.5 UI

Thêm disclosure **"Hiệu ứng thêm (không cần AI)"** trong `project-setup.tsx`, dưới "Hiệu ứng nền":

- 3 thẻ: Mây, Tuyết, Lá.
- Mỗi thẻ có công tắc + thanh trượt tham số.
- Mây mặc định **tắt**; chỉ bật khi người dùng chủ động chọn.
- Mây có nút **"Chọn vùng mây"** mở editor ảnh (tái dùng `ai-effects` ở chế độ thủ công).
- Danh sách object và nhãn trên ảnh hiển thị `display_label` (ví dụ `flag_1`, `flag_2`) để không nhầm.
- Nút **"Xem thử 5 giây"** (tuỳ chọn, phase 1b) gọi endpoint preview.

### 5.6 Test và tiêu chí nghiệm thu

- [ ] Bật tuyết/lá không cần Gemini, không cần consent.
- [ ] Loop 8 giây không giật ở điểm nối.
- [ ] Hạt manual không rơi vào dải lời khi bật `avoid_lyrics`; AI snow/rain trong background không trừ strip (đã ghi rõ).
- [ ] Với 3 ảnh mẫu và mật độ mặc định, hạt không che mặt/landmark rõ ràng (đây là tiêu chí thị giác, không phải bảo đảm thuật toán; manual overlay không có mask `protect` theo ảnh).
- [ ] Mây warp đúng box, viền mềm, không kéo méo vùng ngoài.
- [ ] Mây thủ công chạy được khi chưa từng gọi Gemini.
- [ ] Mây không tự bật khi prompt có chữ "mây" hoặc ảnh có mây.
- [ ] Khi người dùng chọn Mây, gợi ý vùng `cloud`/`sky` (nếu có) điền đúng box.
- [ ] Cache hit lần xuất thứ hai; đổi tham số làm cache miss.
- [ ] Hai object cùng label "flag" hiển thị `flag_1`, `flag_2` trong danh sách và trên ảnh.
- [ ] Label duy nhất như "guitar" giữ nguyên, không bị thêm số.
- [ ] Manual thêm hai object cùng tên cũng được đánh số.
- [ ] Target `flag_2` chỉ khớp object thứ hai; target `flag` vẫn khớp cả hai.
- [ ] Có test đơn vị cho sprite/mask/warp và test tích hợp render 2 giây.

### 5.7 Cành cây / tán lá: nhận diện theo nhóm

Đây là trường hợp thường phải khoanh tay: một cây có nhiều cành, mỗi cành có hướng và điểm gắn khác nhau. Mục tiêu: **tự nhận diện cành cây thành nhóm**, và khi prompt yêu cầu gió thì đưa nhóm đó vào hiệu ứng `wind`.

**Nhận diện**

- Mở rộng `INVENTORY_PROMPT` của `director.py`:
  - Thêm "tree branches, boughs, foliage clusters, vines" vào danh sách object.
  - Mỗi cành lớn là một object riêng; các cành cùng một cây dùng chung `group_id` (ví dụ `tree_1`).
  - `pivot_2d` đặt tại **điểm gắn vào thân/cành mẹ**, không đặt giữa tán.
  - Nếu cả tán là một khối liền, trả một object duy nhất; khi đó `group_id` có thể bỏ trống.
  - Affordances: `wind_movable`, `branch`, `foliage`.
  - **Giới hạn:** tối đa 1–2 object cành/tán mỗi cây, chỉ khi lớn/nhìn rõ; không để cành chiếm hết 20 slot của mặt người/lửa/vật thể chính.
- Thêm `group_id` (optional string) vào `_INVENTORY_SCHEMA` và `normalize_inventory`; `display_label` vẫn duy nhất (`branch_1`, `branch_2`).
- Thêm hint vào `living.py`:
  - `WIND_LABELS`: chỉ `branch`, `bough`, `tree branch`, `foliage`, `canopy`, `cành`, `cành cây`, `tán lá`, `vòm lá`. **Bỏ `tree`/`cây` khỏi `WIND_LABELS`** để cả thân cây không bị xoay; `tree`/`cây` chỉ nằm trong `WIND_FOCUS_HINTS` để gợi ý tìm cành.
  - `WIND_AFFORDANCES`: `branch`, `foliage`, `wind_movable`.
  - `WIND_FOCUS_HINTS` (director): `cành`, `cành cây`, `tán lá`, `vòm lá`, `branch`, `bough`, `foliage`, `tree`, `cây`.
- **Prompt-triggered:** nếu intent `wind` có `target_labels` chứa "cành/cây/tán/lá/branch/tree" mà inventory chưa có object khớp, chạy focused inventory pass (cơ chế `_unresolved_wind_targets` + `_load_inventory(focus=...)` đã có) để tìm cành. Không tự bật `wind` nếu prompt không yêu cầu; người dùng vẫn có thể chọn nhóm và bật wind thủ công.

**Nhóm và target**

- Object cùng `group_id` tạo thành một nhóm.
- **P0-2 — selected filter:** `_wind_specs` hiện gọi `_plan_objects(plan)` với `selected_only=True`, nên cành không được tick bị loại trước khi mở rộng nhóm. Sửa:
  - Lấy `all_objects = _plan_objects(plan, selected_only=False)`.
  - Nếu có `targets`: chọn targeted từ `all_objects`, rồi mở rộng sang mọi object cùng `group_id`.
  - Nếu không có `targets`: chỉ lấy object đang được chọn (`selected_object_ids`) làm wind candidate. **Nếu `selected_object_ids` không tồn tại** (plan cũ), giữ hành vi cũ: dùng tất cả wind candidate.
  - Cách này không phụ thuộc UI và không làm object không chọn khác bị ảnh hưởng.
- **Chỉ branch/foliage làm target gió:** object label chỉ là `tree`/`cây` (không có `branch`/`foliage`) chỉ dùng làm mốc nhóm/trunk, không đưa vào `_wind_specs`.
  - Thêm `BRANCH_AFFORDANCES = ("branch", "foliage", "wind_movable")`, `BRANCH_LABELS = ("branch", "bough", "foliage", "canopy", "cành", "cành cây", "tán lá", "vòm lá")`.
  - `_branch_candidate(entry)` dùng `_matches_hints`.
  - Khi có `targets`, **chỉ nhận target nếu `_branch_candidate` hoặc được mở rộng từ `group_id` của một branch**; target chỉ là `tree`/trunk bị bỏ qua kèm log, không fallback sang toàn bộ cây.
  - **Thay đổi hành vi:** plan/asset cũ có `target_ids` trỏ vào object `tree` sẽ không còn xoay cả cây sau khi bump `MOTION_CACHE_VERSION`. Ghi migration note + test.
- Nếu không có `group_id`: fallback gom theo object `trunk`/`tree` gần nhất **trong bán kính hợp lý** (ví dụ box chồng lấn hoặc tâm cách < 1.5× chiều cao cành); nếu vẫn không rõ, để riêng từng object (không gom bừa hai cây cạnh nhau).
- UI hiển thị badge "nhóm tree_1" và nút **"Chọn cả nhóm"**; target vẫn lưu bằng `target_ids` để ổn định. Nếu chọn một object có `group_id`, UI cũng thêm toàn bộ id cùng nhóm vào `selected_object_ids` để nhất quán với renderer.

**Chuyển động**

- Cành cây dùng `wind` hiện có, nhưng thêm cờ `branch` trong spec:
  - `cloth = False`, `rigid = False`, mask ellipse + feather như plant hiện tại.
  - `pivot` lấy từ `anchors.pivot`; nếu thiếu, fallback **đáy box** `(x1+x2)/2, y2` hoặc điểm gần thân nhất, không dùng top-center như flag.
  - Biên độ khoảng **0.6×** biên độ vải/cờ; góc xoay nhỏ; thêm dao động phụ để lá rung nhẹ.
  - Trọng số `pin**0.7` giữ gốc cành đứng yên, ngọn cành động nhiều hơn.
- Không dùng `CLOTH_HINTS` cho cành, tránh hiệu ứng rung vải.

**UI thủ công**

- Khi vẽ object, thêm vai trò **"Cành cây (gió)"** bên cạnh "Vật thể (gió)" và "Mây".
- Vai trò này:
  - đặt pivot mặc định ở đáy box;
  - thêm item `wind` với `target_ids: [id]`;
  - có ô **"Nhóm"** tuỳ chọn (ví dụ `tree_1`); nếu để trống, mỗi cành là một nhóm riêng và ẩn nút "Chọn cả nhóm";
  - hiển thị trong nhóm object như object thường, có badge "cành".

**Tiêu chí nghiệm thu**

- [ ] Ảnh có 2-3 cành cùng cây: inventory trả các object riêng, cùng `group_id`, pivot ở gốc cành.
- [ ] Prompt "cành cây đung đưa theo gió" tạo intent `wind` target đúng nhóm; nếu inventory thiếu, focused pass bổ sung.
- [ ] Chọn một cành trong nhóm → cả nhóm cùng chuyển động, không phải chỉ một box.
- [ ] Cành không rung như vải; chuyển động quanh gốc, ngọn cành động nhiều hơn.
- [ ] Manual "Cành cây" chạy được khi không có Gemini.
- [ ] Ảnh không có cành: không tự thêm object/effect.

### 5.8 Tàn lửa / tro bay: bớt "bong bóng trắng", thêm ánh lửa

Hiện `_embers_spec`/`_apply_embers` dùng một sprite tròn mềm (`_soft_sprite`) cộng additive với màu cam. Trên nền sáng hoặc khi nhiều hạt chồng nhau, phần lõi bị cộng bão hoà → nhìn như bong bóng trắng. Cần đổi sang hình ảnh tro/lửa thật hơn.

**Thiết kế**

- Tách 2 loại hạt:
  - `spark` (ít, phát sáng): lõi vàng nhỏ + quầng cam đỏ, có nhấp nháy.
  - `ash` (nhiều hơn): mảnh tro xám/than, không phát sáng mạnh, có viền cam rất nhẹ, xoay/lật khi bay.
- Sprite pre-render 3 biến thể:
  - `ember_core`: lõi sáng 1–2 px, halo 4–8 px.
  - `spark_tail`: hạt nhỏ kéo dài theo hướng bay.
  - `ash_flake`: đa giác bất quy tắc, bán trong suốt.
- Màu:
  - Lõi: `#fff2b0` → `#ffb347` → `#e0511f`.
  - Tro: `#4a4038` → `#6b5a4a`, viền `#c96a2b` alpha thấp.
- Blend:
  - `spark` dùng additive có **cap** (không cộng vượt 255) và giảm alpha khi nền sáng.
  - `ash` dùng alpha thường, không additive.
  - Tránh dùng một sprite tròn trắng cho tất cả.
- Chuyển động:
  - Bay lên có gia tốc nhẹ, trôi ngang + đảo gió.
  - `spark` nhấp nháy theo sin với chu kỳ nguyên; `ash` xoay chậm.
  - Kích thước giảm dần khi lên cao (spark) hoặc giữ nguyên (ash).
- Tham số mới:
  - `glow` (0–100): tỉ lệ/độ mạnh ánh lửa.
  - `ash_ratio` (0–100): tỉ lệ tro vs spark.
  - `flicker` (0–100): nhấp nháy.
  - Giữ `intensity`, `density`, `speed`.
- UI: đổi nhãn thành **"Tàn lửa / tro bay"**, thêm 3 thanh trượt trên; `EFFECT_PARAMS.embers` và `PARAM_META` cập nhật.
- `MOTION_CACHE_VERSION` tăng; test lại asset cũ.

**Tiêu chí nghiệm thu**

- [ ] Trên nền tối và nền sáng, không còn các bong bóng trắng tròn đều.
- [ ] Có lõi vàng-cam rõ, quầng ấm, không cháy trắng vùng xung quanh.
- [ ] Tro xám/than chiếm phần lớn; spark chỉ điểm xuyết.
- [ ] Loop 8 giây không giật; nhấp nháy không chớp mạnh.
- [ ] `glow = 0` cho tro gần như không phát sáng; `ash_ratio = 0` cho toàn spark.
- [ ] Không đè vùng `protect`/dải lời.

---

## 6. Hạng mục 2 — Bokeh và light leak

### 6.1 Bokeh

- 3 lớp đốm tròn:
  - xa: nhỏ, mờ, nhiều.
  - giữa: vừa, rõ viền.
  - gần: lớn, mờ mạnh, ít.
- Sprite tròn có gradient radial + viền sáng mảnh.
- Chuyển động: trôi chậm theo hướng gió + nhấp nháy nhẹ.
- Blend `screen` (mặc định) hoặc `soft_light`.
- Opacity 0,1–0,5; kích thước 10–120 px.

### 6.2 Light leak

- 2–3 dải sáng chéo, mờ lớn, xoay theo `angle`.
- Cường độ dao động theo sin, loop khép kín.
- Blend `screen`; màu ấm hoặc theo palette.
- Có thể cộng thêm gradient ấm/lạnh toàn khung ở mức rất nhẹ (`grade` tuỳ chọn).

### 6.3 Palette

- `auto`: lấy 4 màu chủ đạo từ ảnh bìa/nền bằng `PIL.Image.quantize`, lọc theo độ bão hoà/độ sáng; fallback palette ấm.
- Preset: `warm`, `cool`, `pastel`, `neon`.
- UI hiện swatch để người dùng thấy trước.

### 6.4 Render

- Chung `karaoke/overlays.py`, chạy trong `apply_overlays` (bên trong `build_background`).
- Thứ tự compositing: cloud (đã nằm trong background) → bokeh → light leak → tuyết → lá. Ghi comment trong code để lần sau không đảo.
- Tất cả dùng chung `overlay_identity`; `palette` đã resolve (kể cả `auto`) phải nằm trong identity, cùng `base_hash` (đã chứa digest ảnh bìa) — đổi ảnh bìa làm đổi palette thì overlay phải đổi. Không dùng `background.hash` cuối cùng để tránh vòng lặp.
- Bật overlay thêm một lần encode full-length; xem chi phí ở 5.4.

### 6.5 UI

- Cùng disclosure "Hiệu ứng thêm".
- 2 thẻ: Bokeh, Light leak.
- Chọn palette, cường độ, mật độ, kích thước, tốc độ, opacity, blend.

### 6.6 Tiêu chí nghiệm thu

- [ ] Bokeh trông giống đốm sáng phim, không giống hạt tròn đặc.
- [ ] Light leak không cháy trắng vùng mặt/chữ trên 3 ảnh mẫu (tiêu chí thị giác với opacity/blend mặc định; không có mask bảo vệ tự động).
- [ ] Palette auto lấy đúng tông ảnh (kiểm tra 3 ảnh mẫu) và có trong cache identity.
- [ ] Tắt hết thì không encode thêm.

---

## 7. Hạng mục 3 — Waveform ngắn

### 7.1 Dữ liệu peaks

Module mới `karaoke/waveform.py`:

```python
def load_peaks(audio: Path, rate: int = 50) -> dict
def waveform_identity(plan, audio, profile, cover_frames, song_frames, outro_frames) -> str
def render_waveform_clip(plan, audio, profile, cover_duration, song_duration, outro_duration, destination, log) -> Path
```

- Decode bằng ffmpeg: `-f s16le -ac 1 -ar 8000 pipe:1`.
- Bucket 50 Hz; lưu `rms` và `peak` đã chuẩn hoá.
- Cache JSON theo `hash(file) + mtime + size + rate + smoothing_version`, trong cache dir hiện có.
- Làm mượt nhẹ để waveform không rung giật; tăng `smoothing_version` khi đổi thuật toán.
- **Nguồn audio:** mặc định `instrumental`. Cả hai output dùng chung một `picture`, nên bản "có giọng" cũng hiển thị waveform không vocal. Ghi rõ trong UI; nếu muốn khớp từng bản phải render 2 picture (đắt gấp đôi, không làm MVP).

### 7.2 Vẽ

- Kích thước clip = `width × height` theo tỉ lệ khung (ví dụ 650×90 ở 1080p).
- 3 kiểu:
  - `bars`: cột dọc, khoảng cách 20–50%.
  - `line`: đường sóng đối xứng.
  - `dots`: chấm tròn theo biên độ.
- Nền chip bo góc bán trong suốt (mặc định bật) để không cần alpha.
- Nếu `background = false`: encode `qtrle`/`argb` có alpha; cảnh báo dung lượng.
- `mode = progress`: vẽ toàn bộ bài trong dải, tô màu đến vị trí hiện tại. Bài dài + dải hẹp sẽ ra cột rất mảnh; test ở 16:9 và 9:16.
- `mode = scroll` (phase 2): cửa sổ 6 giây cuộn.

### 7.3 Ghép vào video

Sửa `karaoke/render.py::_overlay_text`:

- Input order: `[0]` background, `[1]` text strip, `[2]` waveform.
- **Không dùng `-itsoffset`** cho waveform. Render clip đủ dài `cover + song + outro`, nhưng chỉ hiện trong đoạn bài bằng `enable`.
- **P0-3 — chip đục không được phủ cover/outro:** thêm `enable='between(t,{cover_duration},{cover_duration + song_duration})'` vào filter overlay. Không dựa vào “phần trước/sau để đen”; `yuv420p` không trong suốt.
- Filter gợi ý:
  ```
  [2:v]format=rgba[wave];
  [0:v][wave]overlay=x=X:y=Y:format=auto:eof_action=pass:
      enable='between(t,{cover},{cover + song})'[base];
  [1:v]fps=...,format=rgba[text];
  [base][text]overlay=0:{strip_top}:format=auto:shortest=1,format=yuv420p
  ```
- Nếu `cover = 0` hoặc `outro = 0`, filter vẫn đúng; test cả hai.
- Toạ độ là **tâm**:
  ```
  X = x * W - w / 2
  Y = y * H - h / 2
  ```
  với `w = width * W`, `h = height * H`. Clamp theo safe area.
- `picture_identity` phải nhận `waveform_hash`; `_picture_identity(background, lines, presentation, waveform)` hoặc một hash tổng.
- Nếu waveform chồng `strip_rect`: chốt hành vi **tự đẩy lên trên strip** (không chỉ cảnh báo), trừ khi người dùng bật "cho phép chồng".

### 7.4 Kéo vị trí và lưu

- Trong drawer Xuất video, tab **Waveform**.
- `TimingEditor` hiện chỉ nhận `jobId`, `lines`, `outputs`… nên phải thêm prop `presentation`/`waveform`/`outputProfile` (hoặc đọc từ project trong `MakerForm` và truyền xuống).
- Kéo bằng pointer events trên khung preview đúng tỉ lệ profile; lưu `x`, `y` tâm tỉ lệ.
- **Persist ngay khi kéo** (không đợi bấm Xuất):
  - `POST /api/projects/presentation` nhận **partial** `{ project_path, presentation?, waveform? }` và deep-merge từng phần; không dùng path param dài.
  - Response trả `project` đã merge để `MakerForm`/`TimingEditor` cập nhật state, tránh lần save sau ghi đè dữ liệu cũ.
  - Thêm callback `onPresentationSaved(project)` (hoặc dùng response) để `MakerForm` cập nhật `fields` và `job.project`.
  - Chỉ gửi phần thay đổi (chỉ `waveform` hoặc chỉ nhánh `presentation`), không gửi toàn bộ dict cũ.
- `ExportOptions` thêm `waveform?: WaveformPlan`; `RenderRequest` thêm `waveform` để lần xuất dùng đúng giá trị mới nhất kể cả khi POST thất bại.
- Nút snap: **9 điểm** (4 góc, 4 cạnh, giữa); hiển thị toạ độ và "Đặt lại".
- **Vị trí mặc định:** `x=0.5`, `y=0.70`, `width=0.32`, `height=0.06` cho 16:9;
  với 9:16 dùng `y≈0.63`, `width≈0.50`. Waveform nằm ngay trên dải lời, không đè chữ.

### 7.5 Tiêu chí nghiệm thu

- [ ] Waveform khớp nhạc (sai số < 1 frame).
- [ ] Kéo vị trí đúng; đổi profile vẫn đúng vị trí tương đối; lưu ngay khi kéo.
- [ ] 3 kiểu vẽ đều đẹp ở 16:9 và 9:16.
- [ ] Không che lời: tự đẩy lên trên strip.
- [ ] Cache peaks và clip; xuất lại nhanh.
- [ ] `waveform_identity` chứa đủ: peaks hash, `color`, `accent`, `opacity`, `background`, `mode`, `style`, `width/height`, `x/y`, `profile`, `cover_frames`, `song_frames`, `outro_frames`, module version.

---

## 8. Hạng mục 4 — Cover, interlude, trang kết, typography

### 8.1 Cover

Mở rộng `karaoke/media.py::make_cover(..., style: dict | None = None)`:

- **Layout:** `center`, `bottom_left`, `bottom_center`.
- **Nhãn:** `label` là badge nhỏ phía trên title (ví dụ "KARAOKE"); `cover_line` vẫn là dòng phụ dưới artist. Hai field khác nhau.
- **Cỡ chữ:** title / artist / line, ghi đè profile; `null` = mặc định.
- **Màu:** `text_color` = title; `accent_color` = artist + label; `line_color` = cover_line. Ba màu tách biệt, không chồng.
- **Kiểu:** uppercase, shadow, stroke.
- **Nền:** blur 0–40; `bg_darken` 0–80 với `brightness = 1 - bg_darken/100` (default 58 → 0.42 như hiện tại); `veil` 0–100 (default 70); vignette 0–100; scrim `none|bottom|full`.
- **Auto-fit:** thu nhỏ title nếu quá rộng; không cắt chữ.

**Cache bắt buộc:** `_cover_identity` phải hash thêm canonical JSON của `presentation.cover` (đã normalize). Chỉ bump `COVER_RENDER_VERSION` là chưa đủ: đổi layout/màu/cỡ chữ sau này sẽ dùng lại cover cũ nếu style không nằm trong identity.

Preview nhanh: `POST /api/presentation/cover-preview`:
- `ensure_allowed(image_path)`, trả PNG/JPEG.
- Cache theo `hash(style) + ảnh + title/artist + profile`.

### 8.2 Interlude

**Tầng:** picture, không phải background (P0-1). `_picture_identity` phải nhận `presentation.interlude` và danh sách range đã tính; bump `PICTURE_BUILDER_VERSION`.

- Phát hiện khoảng lặng giữa hai câu: `gap = next.start - current.end`.
- Chỉ chạy khi `presentation.interlude.enabled = true`; **default false** để project v1 không tự mọc banner (P0-2).
- `min_gap >= max(LONG_GAP, COUNTDOWN + 0.5)`; `LONG_GAP = 8` vẫn là hằng số cue chung trong `render.py` và `cue.ts`, không thay bằng `min_gap` toàn cục.
- Vùng hiển thị: `[current.end + 0.3, next.start - COUNTDOWN]`; nếu độ dài <= 0 thì bỏ qua.
- **MVP: `mode = "strip"`** (đã chốt): banner nằm trong dải lời, hiển thị "Nhạc dạo" + tuỳ chọn dòng tiếp theo, fade in/out; không đè countdown.
- Vẽ banner trong `_frame_at`/`_draw_interlude` ở `TEXT_FPS`; không cần filter graph full-page.
- Vẫn giữ đếm ngược 5 giây trước câu kế.
- Cache banner theo text/style.
- `cue.ts`: thêm marker interlude (tuỳ chọn) để preview không lệch; nếu chưa làm, ghi rõ export là nguồn sự thật.
- `mode = "card"` để dành phase sau; không đưa vào MVP.

### 8.3 Trang kết

Phải thêm `outro_frames` vào **toàn bộ chuỗi**:

- `_frames()`, `_plan_background()`, `BackgroundBuild`, `BackgroundBuild.to_project()`, `BackgroundRef.from_project()`, `background_status()` (key `outro_frames` + so sánh), `_validate_background` (qua `total_frames`), `web/src/lib/types.ts::BackgroundRender`.
- `total_frames = cover + song + outro`.
- Bump `BACKGROUND_BUILDER_VERSION` để background cũ không bị tái dùng sai.

Segment cuối dùng `make_outro()`:

- Ảnh nền: `presentation.outro.image` nếu có, ngược lại ảnh bìa.
- Chữ: title/artist, `subtext` ("Cảm ơn đã xem"), `handle`. Nếu `presentation.outro.title` rỗng thì fallback `project.title`; nếu `handle` rỗng thì ẩn dòng đó.
- Fade to black ở 0,6 giây cuối.
- `duration = 0` → tắt hoàn toàn.
- **Quyền truy cập:** thêm `presentation.outro.image` vào `grant_project_paths()` (`karaoke/access.py`) và validate bằng `_existing_file`; nếu ảnh ngoài allowed roots thì báo lỗi rõ, không render âm thầm.
- **Identity:** hash `presentation.outro` + digest ảnh outro vào identity của segment outro.
- **Audio:** `_mux` đã có `apad`; `-shortest` dừng theo video dài hơn nên outro còn tiếng silence. Test cả file có/không có ambience.

### 8.4 Typography

- `typography.lyric_size`: ghi đè `profile.lyric_font_size`.
- `_fit_lyric_font_size(lines, profile, base_size=..., row_gap=...)` phải **bắt đầu từ override** và chỉ shrink; không cap về profile. Như vậy `lyric_size` nhỏ hơn profile vẫn có tác dụng.
- `typography.lyric_color`, `sung_color`: màu chữ thường và chữ đã hát.
- `typography.shadow`, `stroke`: đổ bóng/viền.
- `typography.row_gap`: khoảng cách dòng; `0` là giá trị hợp lệ, không fallback.
- `_draw_countdown` dùng `sung_color` thay vì hardcode vàng.
- `_draw_strip` đang hardcode bar `(8,6,4,196)`; thêm `typography.bar_color` + `bar_opacity` (optional, default giữ nguyên).
- Font family: giữ Arial Unicode (đã hỗ trợ tiếng Việt) trong MVP. Danh sách font khác để phase 4b, tránh lỗi dấu.

### 8.5 UI

Trong `project-setup.tsx`, thay "Tuỳ chọn xuất" bằng 2 nhóm:

1. **Xuất:** định dạng, thư mục, bìa (giây), mỗi ảnh nền (giây).
2. **Trang & chữ:** cover layout, cỡ chữ, màu, nền; interlude; outro; typography.

Trong drawer Xuất video, thêm tab **"Trang"** hiển thị tóm tắt và nút "Sửa trong khai báo" (tránh nhân đôi form).

### 8.6 Tiêu chí nghiệm thu

- [ ] 3 layout bìa đều đúng ở 4 profile.
- [ ] Đổi cỡ chữ/màu/layout bìa làm đổi `cover_identity` và ảnh preview ngay.
- [ ] Project v1 không có `presentation` vẫn render như trước; interlude mặc định không xuất hiện.
- [ ] Interlude banner xuất hiện đúng khoảng lặng trong dải lời, không đè countdown; `min_gap < 8` bị từ chối.
- [ ] Trang kết dài đúng số giây, tiếng không bị cắt; `outro_frames` khớp `total_frames`.
- [ ] `lyric_size` nhỏ hơn profile vẫn có tác dụng.
- [ ] Cache bìa/picture invalidate khi đổi style/interlude/waveform.
- [ ] Ảnh outro ngoài allowed roots bị chặn với thông báo rõ.

---

## 9. Hạng mục 5 — Đăng YouTube (TikTok để backlog)

> **Phạm vi hiện tại:** chỉ làm YouTube (Phase 5A). TikTok vẫn giữ trong tài liệu làm backlog/phase 5B, **không triển khai** cho đến khi bạn yêu cầu.

### 9.1 Kiến trúc

```
karaoke/publish/
  __init__.py
  models.py       # Draft, PublishJob, Channel
  store.py        # đọc/ghi drafts.json, jobs.json, tokens.json (atomic + file lock 0600)
  oauth.py        # loopback flow, refresh token
  youtube.py      # resumable upload + playlistItems.insert
  tiktok.py       # content posting API (backlog 5B, không làm đợt này)
  worker.py       # hàng đợi 1 luồng, retry, progress
  service.py      # API-facing functions
```

Thư mục dữ liệu: `data_root() / "publish"`.

### 9.2 Kênh và token

> **Trạng thái credential (đã kiểm tra):** có OAuth client **Desktop** tại `/Users/andynguyen/Credential/KaraokeMaker`; `.env` đã có `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` và khớp với file JSON. Project `fabled-essence-515515-a2` đã bật `youtube.googleapis.com`. Còn phải xác nhận **OAuth consent screen** có scope `youtube.force-ssl` và email của bạn nằm trong **Test users**; nếu thiếu, bấm kết nối sẽ bị `Access blocked`. TikTok developer app có thể đã có nhưng phạm vi TikTok chưa chốt; TikTok ở 5B.

- `channels.json`: danh sách kênh đã kết nối (provider, channel id, tên kênh, avatar, scopes, expiry). YouTube MVP dùng **một kênh**; playlist được chọn trong kênh đó. Nếu sau này có nhiều brand channel, thêm selector kênh (không nằm trong 5 trường MVP).
- `tokens.json`: access/refresh token theo `channel_id`; quyền `0600`; không bao giờ trả về client.
- **Một flow OAuth duy nhất:** dùng callback của server hiện tại `http://127.0.0.1:8765/api/publish/oauth/callback`, không mở listener tạm. Đăng ký đúng URI này với Google/TikTok; Next chỉ rewrite `/api`, không tham gia OAuth.
- Google:
  - OAuth client loại **Desktop app**; loopback được hỗ trợ; dùng PKCE.
  - Authorization URL phải có `access_type=offline` và `prompt=consent` (ít nhất lần đầu/scope đổi) để nhận refresh token; thiếu thì token chỉ sống 1 giờ.
  - Cần scope **`youtube.force-ssl`** để vừa upload vừa đọc/ghi playlist; **không cần** `youtube.readonly` (force-ssl đã bao gồm quyền đọc). Xin cả hai chỉ làm tăng rủi ro verification.
  - Luôn xin `youtube.force-ssl` ngay khi kết nối nếu UI có playlist. Nếu token cũ thiếu scope, phát hiện `insufficientPermissions` và yêu cầu kết nối lại (không tự upload xong rồi mới báo).
  - Nếu bỏ playlist ở tương lai: quay lại `youtube.upload` + `youtube.readonly`.
- TikTok (backlog, không làm đợt này):
  - Login Kit for Desktop; redirect `http://127.0.0.1:8765/api/publish/oauth/callback` (port cố định, đăng ký trước).
  - PKCE bắt buộc; `code_challenge` là **hex SHA-256** của verifier (không phải base64url).
  - Scope: `user.info.basic`, `video.publish`, `video.upload`.
- `state` ngẫu nhiên, one-time, TTL 10 phút; lưu `code_verifier` cùng state.
- Refresh tự động trước khi hết hạn 60 giây.
- Nút "Ngắt kết nối" gọi **revoke** của Google (`https://oauth2.googleapis.com/revoke?token=...`) trước khi xoá token local; nếu revoke lỗi vẫn xóa local và log.
- **Rủi ro verification:** OAuth consent screen ở Testing thường làm refresh token hết hạn sau 7 ngày; `youtube.upload` là sensitive scope, muốn dùng lâu dài phải publish/verify app. Ghi rõ trong tài liệu cấu hình.

### 9.3 YouTube

- `videos.insert` resumable:
  - tạo session với `snippet` + `status`.
  - PUT từng chunk 8 MB, xử lý `308 Resume Incomplete`.
  - lưu `upload_url` + `bytes_uploaded` vào job để resume, không upload lại từ đầu.
- **Form tối giản theo yêu cầu** — chỉ 5 trường:
  1. `title` (bắt buộc, tối đa 100 ký tự).
  2. `description` (body, tối đa 5.000 ký tự).
  3. `madeForKids` (trẻ em có xem được không) → `status.selfDeclaredMadeForKids`.
  4. `privacyStatus`: `public` / `unlisted` / `private` — **default `public`**, người dùng luôn chọn được. Vẫn gửi tường minh: tài liệu Google nói mặc định là `public`, nhưng account settings có thể ép private; gửi rõ để tránh bất ngờ.
  5. `playlistId`: chọn **playlist** để thêm video sau khi upload (đã chốt "kênh phát" = playlist). Có thể để trống nếu không muốn thêm.
- **`snippet.categoryId` (bắt buộc trên thực tế):** không thêm thành trường thứ 6 cho người dùng. Adapter gửi mặc định **`10` (Music)** cho karaoke; nếu kênh/region không có Music thì fallback **`22` (People & Blogs)**. Tốt hơn: gọi `videoCategories.list?part=snippet&regionCode=VN` một lần khi kết nối, lưu danh sách, chọn Music nếu có.
- **Không làm** thumbnail/cover upload và end screen ở MVP; người dùng nói thường không chọn. Nếu sau này cần thì thêm `thumbnails.set`/`endScreen` ở phase sau.
- Playlist: sau `videos.insert` gọi `playlistItems.insert` với `playlistId`; nếu bước này lỗi, video vẫn đã đăng → retry **chỉ playlist**, không upload lại.
- `publishAt`/hẹn giờ: không đưa vào form MVP; nếu cần thì chỉ hợp lệ khi `privacyStatus = private`.
- **Emoji/icon:** title và description thường có icon. Phải:
  - giữ nguyên Unicode/emoji khi nhập/paste, không strip/escape sai;
  - lưu JSON UTF-8 (`ensure_ascii=False`);
  - gửi nguyên chuỗi lên YouTube;
  - đếm độ dài theo code point để validate, không cắt giữa emoji;
  - **không làm bảng chọn emoji** (đã chốt: chỉ nhập/paste).
- **Validation:** title không chứa `<`/`>`; title ≤100 ký tự; description ≤5.000 ký tự (đếm code point, không cắt giữa emoji). Có thể kiểm tra thêm giới hạn byte của YouTube nếu gặp lỗi.
- **Synthetic media:** video có hiệu ứng AI có thể cần `status.containsSyntheticMedia`; MVP không bật mặc định, ghi chú trong tài liệu legal/UI để người dùng tự chọn sau.
- **Idempotency:** lưu `platform_video_id` ngay sau `videos.insert` thành công. Nếu bước playlist lỗi, retry **chỉ bước đó**, không upload lại video.
- Quota: mặc định 10.000 units/ngày; một upload ~1.600 units → khoảng 6 video/ngày. Ngoài ra bucket "Video Uploads" 100 calls/ngày. Hiển thị cảnh báo khi gần hạn mức.
- **Made for kids lưu ý:** nếu kênh đã được đặt "made for kids" ở cấp kênh, API có thể không cho đổi; UI đọc trạng thái kênh và hiển thị khoá nếu vậy.

### 9.4 TikTok — backlog (không làm đợt này)

> Giữ lại để sau này làm Phase 5B. **Không code, không UI, không test** trong đợt hiện tại.

- **Trước khi init**, gọi `creator_info/query` để lấy:
  - `privacy_level_options`
  - `max_video_post_duration_sec`
  - mặc định `comment/duet/stitch`
- `POST /v2/post/publish/video/init/` với `post_info` + `source_info` (`FILE_UPLOAD`).
  - `brand_content_toggle` là field bắt buộc.
  - Chỉ init **khi đến giờ đăng**: `upload_url` chỉ sống khoảng 1 giờ.
- PUT chunk: `chunk_size` trong khoảng 5–64 MB (một chunk có quy định riêng); bắt buộc `Content-Range` + `Content-Length`.
- Poll `/v2/post/publish/status/fetch/` bằng `publish_id`.
- Rate limit khoảng 6 request/phút/user; gặp 429 thì backoff, worker tuần tự.
- **Giới hạn quan trọng:** app chưa qua audit chỉ đăng được `SELF_ONLY`/private. Muốn public phải được TikTok duyệt. UI phải khoá privacy và hiện lý do.
- Inbox upload dùng endpoint riêng `/v2/post/publish/inbox/video/init/`; **không làm ở MVP 5B** trừ khi người dùng xác nhận. Nếu cần, thêm state + UI riêng, không trộn với direct post.
- **Chỉ đăng ngay** (đã chốt): không hẹn giờ TikTok. Nếu người dùng muốn đăng sau, họ bấm đăng khi đến giờ. Không giữ job chờ và không hứa đăng khi app tắt.

### 9.5 Lưu và tái sử dụng nội dung đăng bài

Mục tiêu: nội dung đã điền (title/body/kids/privacy/playlist) **được lưu lại**, để sau khi sửa video và xuất lại, chỉ cần bấm "Đăng lại" mà không phải nhập lại.

- **Nơi lưu:** `data_root()/publish/drafts.json`, dùng file lock + atomic replace; **không** lưu token trong draft.
- **Khoá liên kết:** `project_path + platform + video_role`, không dùng tên file video (vì tên có timestamp và đổi sau mỗi lần xuất).
  - `video_role`: `vocal` hoặc `instrumental` — draft biết sẽ đăng bản nào.
  - Khi publish, resolve `project.outputs[video_role]` để lấy file mới nhất; nếu thiếu thì báo và cho chọn lại.
- **Schema gợi ý:**
  ```json
  {
    "id": "draft_...",
    "project_path": "/.../Bài hát.karaoke.json",
    "project_title": "Bài hát",
    "platform": "youtube",
    "video_role": "vocal",
    "title": "🎤 Bài hát",
    "description": "🎶 ...",
    "made_for_kids": false,
    "privacy": "public",
    "playlist_id": "PL...",
    "playlist_name": "Karaoke",
    "created": "...",
    "updated": "...",
    "revision": 3,
    "last_published_at": "...",
    "last_video_id": "...",
    "last_url": "..."
  }
  ```
- **Autosave:** lưu debounce ~500ms khi sửa; có nút "Lưu nháp" và trạng thái "Đã lưu / Đang lưu / Lỗi". `revision` tăng mỗi lần lưu để tránh ghi đè giữa 2 tab. API trả **409** kèm draft mới nhất khi `revision` lệch (hoặc dùng `If-Match`); client hỏi overwrite/reload.
- **Sau khi xuất lại video:** draft vẫn giữ nguyên nội dung; UI hiện badge **"Video mới nhất"** và nút **"Đăng lại"**. Không tạo draft mới, trừ khi người dùng bấm "Nhân bản".
- **Snapshot khi đăng:** copy toàn bộ metadata vào `publish_jobs` tại thời điểm bấm đăng; sửa draft sau đó không làm thay đổi job đang chạy/đã đăng.
- **Lịch sử:** mỗi lần đăng lưu `last_published_at`, `last_video_id`, `last_url`; danh sách job giữ đủ để xem lại.
- **Dự án bị move/rename:** nếu `project_path` không còn, thử khớp theo `project_title + output_dir`; nếu vẫn không, hiện "Dự án không còn" và cho **Chọn lại dự án** hoặc xoá draft.
- **Nhiều biến thể:** một draft / platform / video_role; "Nhân bản" để tạo bản khác (ví dụ mô tả dài/ngắn), không ảnh hưởng bản gốc.

### 9.6 Hàng đợi

Trạng thái có bước con:

```
draft → queued → initializing → uploading → finalizing → published
                                                        └→ failed
                                                        └→ cancel_requested
```

- Không có `scheduled` trong MVP: đăng ngay khi bấm.
- Một worker thread; job tuần tự để tránh đụng quota.
- Lưu `progress`, `attempts`, `last_error`, `platform_video_id`, `publish_id`, `upload_url`, `bytes_uploaded`, `step`.
- Retry backoff: 30s, 2m, 10m; tối đa 3 lần với lỗi mạng/5xx/429; không retry lỗi auth/quota.
- **Recovery khi khởi động:** job `uploading` phải resume `upload_url` nếu còn hiệu lực; nếu hết hạn thì chuyển `queued` kèm cảnh báo, không để kẹt mãi.
- **Progress throttle:** ghi `jobs.json` tối đa mỗi 1 giây hoặc mỗi 5%, không ghi mỗi frame.
- **Cancel:** nếu đang `finalizing`/`processing`, không thể thu hồi video đã gửi; đặt `cancel_requested`, không hứa "đã huỷ".
- Một channel/provider là MVP.

### 9.7 API

Mỗi draft có `platform: "youtube" | "tiktok"`, với YouTube thêm `playlist_id` (đã chốt: một draft = một nền tảng; "kênh phát" = playlist).

| Method | Path | Việc |
|---|---|---|
| GET | `/api/publish/channels` | Danh sách kênh + trạng thái |
| POST | `/api/publish/channels/{provider}/connect` | Bắt đầu OAuth, trả URL |
| GET | `/api/publish/oauth/callback` | Nhận code (server hiện tại) |
| DELETE | `/api/publish/channels/{provider}` | Ngắt kết nối |
| GET | `/api/publish/creator-info/{provider}` | TikTok privacy/duration/options (5B) |
| GET | `/api/publish/youtube/playlists` | Danh sách playlist của kênh để chọn |
| GET | `/api/publish/videos` | Danh sách video từ output dự án để chọn |
| GET | `/api/publish/drafts` | Danh sách draft, lọc theo `project`, `platform` |
| POST | `/api/publish/drafts` | Tạo draft |
| PATCH | `/api/publish/drafts/{id}` | Sửa draft (autosave) |
| POST | `/api/publish/drafts/{id}/duplicate` | Nhân bản draft |
| DELETE | `/api/publish/drafts/{id}` | Xoá draft |
| POST | `/api/publish/drafts/{id}/publish` | Đưa vào hàng đợi |
| GET | `/api/publish/jobs` | Danh sách job, lọc `?project=` |
| GET | `/api/publish/jobs/{id}` | Chi tiết + log |
| POST | `/api/publish/jobs/{id}/retry` | Thử lại bước lỗi |
| POST | `/api/publish/jobs/{id}/cancel` | Huỷ / cancel_requested |

### 9.8 UI

- Thêm mục **"Đăng bài"** vào `app-shell.tsx`, route `/publish`.
- Khu 1: **Kênh** — thẻ YouTube, trạng thái, nút Kết nối/Ngắt. TikTok ẩn ở đợt này.
- Khu 2: **Soạn nội dung** — mỗi draft chọn **một nền tảng**:
  - YouTube (MVP): đúng 5 trường — **title**, **description/body**, **made for kids**, **privacy** (public/unlisted/private, **default public**), **playlist** (playlist của kênh đã kết nối; có thể để trống). Không có thumbnail/end screen/publishAt.
  - Title/description hỗ trợ **emoji/icon**: chỉ nhập hoặc paste trực tiếp, giữ nguyên Unicode, đếm ký tự theo code point; **không làm bảng chọn emoji**.
  - Playlist dropdown tải từ `GET /api/publish/youtube/playlists`, có tìm kiếm nếu nhiều.
  - TikTok (backlog 5B, **ẩn khỏi UI đợt này**): caption, privacy từ `creator_info`, comment/duet/stitch, `brand_content_toggle`; không hẹn giờ.
- **Draft & đăng lại:** autosave, badge "Đã lưu", danh sách draft theo dự án, nút "Đăng lại" dùng video mới nhất; "Nhân bản" để tạo biến thể. Nếu video đã xuất lại, hiện "Video mới nhất" thay vì bắt nhập lại nội dung.
- Khu 3: **Hàng đợi** — tiến trình, bước, lỗi, retry, cancel.
- Khu 4: **Lịch sử** — video đã đăng, link, thời gian.
- Nút **"Đăng"** xuất hiện trong drawer Xuất video sau khi render xong, mở draft với video vừa tạo.
- **Định dạng:** export có `mp4/mov/mkv`; TikTok chỉ nhận mp4/mov, YouTube nhận rộng hơn. Trước khi publish phải validate; nếu cần thì convert sang MP4 H.264/AAC. Cảnh báo nếu video 16:9 khi đăng TikTok (nên 9:16).

### 9.9 Bảo mật

- Token không nằm trong `.karaoke.json`, không ghi log, không trả về client.
- `publish/store.py` phải dùng **file lock** (tái dùng `_file_lock` trong `settings.py`) quanh mọi read-modify-write; atomic replace chỉ tránh file hỏng, không tránh mất update giữa API thread và worker.
- OAuth `state` ngẫu nhiên, one-time, TTL; kiểm tra khi callback.
- File token `0600`; `channels.json` cũng `0600`; cân nhắc Keychain macOS ở phase 5b.
- Log redact: `Authorization`, `access_token`, `refresh_token`, `upload_url`, `upload_token`, `publish_id` (capability URL của TikTok chứa token trong query).

### 9.10 Test

- [ ] Store ghi atomic, không hỏng file khi tắt giữa chừng.
- [ ] State machine chuyển đúng; retry chỉ bước lỗi, không upload lại video.
- [ ] Recovery job `uploading` sau khi restart.
- [ ] Adapter giả (fake provider) chạy hết luồng không cần mạng.
- [ ] YouTube chunk math đúng với file > 8 MB; `publishAt` không dùng trong MVP.
- [ ] YouTube gửi đúng `selfDeclaredMadeForKids`, `privacyStatus` (default `public`), `playlist_id`, `snippet.categoryId`.
- [ ] Title/description có emoji/icon được giữ nguyên, không lỗi encoding, validate độ dài đúng; title không chứa `<`/`>`.
- [ ] Playlist lỗi không làm mất video đã upload; retry chỉ `playlistItems.insert`.
- [ ] (5B backlog) TikTok payload đúng schema, `brand_content_toggle`, privacy `SELF_ONLY` khi chưa audit, chunk 5–64 MB.
- [ ] Draft có đúng một `platform`; metadata đúng theo nền tảng.
- [ ] (5B backlog) TikTok không tạo job `scheduled`; bấm đăng là chạy ngay.
- [ ] Draft lưu/đọc qua restart; autosave không ghi đè giữa 2 tab (`revision`).
- [ ] Xuất lại video → draft vẫn còn; "Đăng lại" resolve đúng file mới nhất theo `video_role`.
- [ ] Job snapshot metadata tại lúc bấm đăng; sửa draft sau đó không đổi job.
- [ ] Dự án move/rename → fallback/re-link hoạt động.
- [ ] Không có token/upload_url trong response/log/project.
- [ ] Convert MP4 trước khi publish nếu định dạng không hỗ trợ.

---

## 10. Lộ trình

### Phase 0 — Nền tảng + wiring (2 ngày)

- [ ] `presentation.py` + default + normalizer + validate.
- [ ] `project.py` v2 + migration + deep copy + `prepared_from_project`.
- [ ] Pydantic model + `types.ts` + `fieldsPayload` + `ExportOptions`.
- [ ] Test default/migration.

**Bảng wiring bắt buộc** (nếu thiếu, field mới sẽ rơi âm thầm):

| File/hàm | Việc cần thêm |
|---|---|
| `pipeline.py::prepare_job` | copy `overlay_effects`, `waveform`, `presentation`, `typography` vào dict trả về |
| `pipeline.py::open_saved` | copy các field mới khi mở job legacy |
| `pipeline.py::render_job` → `render_videos` | truyền `prepared` hoặc kwarg mới; `render_videos` đang tự dựng dict nội bộ |
| `server.py::ProjectFieldsRequest` | thêm field Pydantic |
| `server.py::project_session` | thêm key vào allowlist `for key in (...)` + xử lý nested `model_fields_set` |
| `server.py::OpenSavedRequest` | thêm field nếu `/api/jobs/open` còn dùng |
| `server.py::RenderRequest` | thêm `waveform` (và `presentation` nếu cần) |
| `server.py::_restore_record` | copy field mới vào `prepared` |
| `project.py::prepared_from_project` | copy field mới |
| `access.py::grant_project_paths` | grant `presentation.outro.image` |
| `web/src/lib/types.ts` | `Project`, `ProjectFields`, `emptyFields`, `fieldsFromProject`, `fieldsPayload`, `ExportOptions`, `BackgroundRender` |
| `background.py::render_picture` | nhận `presentation`, `waveform`; truyền xuống `_overlay_text` |
| `render.py::_overlay_text` | nhận `presentation`, `waveform`, interlude ranges; thêm input `[2]` |
| `background.py::_picture_identity` | nhận `presentation`, `waveform` (hoặc hash tổng) |
| `background.py::_cover_segment` | truyền `presentation.cover` vào `make_cover` |
| `background.py::_build_background_impl` | gọi `apply_overlays`, set `overlay_hash`/`degraded` |
| `background.py::_plan_background` | đọc `overlay_effects`, `presentation`, `typography` |
| `media.py::make_outro` | thêm hàm và gọi từ `_build_background_impl` |
| `pipeline.py::render_videos` dict nội bộ | thêm `overlay_effects`, `waveform`, `presentation`, `typography` |
| `server.py::start_render` | merge `body.waveform` (và `presentation`) vào `prepared` trước `render_job` |
| `web/src/components/maker-form.tsx::renderVideos` | gửi `waveform` (và `presentation` nếu cần) |
| `web/src/components/timing-editor.tsx` | nhận `presentation`/`waveform`/`outputProfile` prop |

**Xong khi:** project cũ mở được, field mới round-trip qua save/open, chưa đổi hành vi render.

### Phase 1 — Mây, tuyết, lá, cành cây, tàn lửa (7–10 ngày)

Ước lượng chi tiết:

| Phần | Ngày |
|---|---:|
| Cloud warp + manual flow | 2–3 |
| Snow/leaf sprite + overlay + mask | 2–3 |
| Branches (AI + group + UI) | 3–5 |
| Embers redesign | 1–2 |
| Tích hợp + test/visual | 1–2 |

Đã chốt: **gộp tất cả vào Phase 1**, không tách 1B.

- [ ] `overlays.py` + gọi từ `build_background`; `BackgroundBuild`/`Ref` thêm `overlay_hash`.
- [ ] `particles.py` dùng chung cho snow/leaf (bắt buộc trong Phase 1).
- [ ] Tuyết sprite + lá sprite + mask.
- [ ] Cloud specs + warp + `_cloud_frame` + item `cloud` trong `SUPPORTED_EFFECTS`/`_render_loop`.
- [ ] `director.py`: alias/gợi ý region `cloud`/`sky` khi người dùng chọn; **không** thêm `cloud` vào `EFFECT_TYPES`.
- [ ] `display_label` duy nhất (`flag_1`, `flag_2`) trong `normalize_inventory` + `_merge_inventory` + UI.
- [ ] `_plan_objects` mang `display_label`/`group_id`; `_visual_plan` scrub `display_label`; test trực tiếp.
- [ ] Cành cây: `group_id`, pivot gốc cành, `WIND_LABELS`/`WIND_AFFORDANCES`, focused pass khi prompt yêu cầu, mở rộng target theo nhóm trong `_wind_specs` với `selected_only=False`.
- [ ] UI vai trò "Cành cây (gió)" + "Chọn cả nhóm".
- [ ] Tàn lửa: sprite spark/ash, màu lõi-cam, `glow`/`ash_ratio`/`flicker`, giảm bão hoà trắng.
- [ ] `POST /api/director/manual` trả `fit_transform`/`profile_id`/`model`.
- [ ] UI "Hiệu ứng thêm" + vai trò "Mây" khi vẽ box.
- [ ] Test + preview.

### Phase 2 — Bokeh, light leak (1,5–2 ngày)

- [ ] Palette auto + preset + đưa palette đã resolve vào identity.
- [ ] Bokeh sprite + blend.
- [ ] Light leak bands.
- [ ] UI thẻ + swatch.

### Phase 3 — Waveform (2,5–3 ngày)

- [ ] `waveform.py`: peaks + cache + version smoothing.
- [ ] Render clip 3 kiểu, clip đủ dài cover+song+outro.
- [ ] Ghép vào `_overlay_text` (input `[2]`, `eof_action=pass`).
- [ ] `POST /api/projects/presentation` (body path) hoặc mở rộng `project_session` để lưu ngay.
- [ ] UI kéo vị trí + snap + truyền prop từ `MakerForm`.
- [ ] Test đồng bộ + identity.

### Phase 4 — Trang và chữ (4–5 ngày)

- [ ] `make_cover` style + layout + `cover_style_hash` + preview endpoint.
- [ ] Interlude **banner trong dải lời** (strip) + `_picture_identity`.
- [ ] Outro segment + `outro_frames` toàn chuỗi + grant ảnh + mux test.
- [ ] Typography override + bar color + countdown color.
- [ ] UI "Trang & chữ".

### Phase 5A — Đăng YouTube (4–5 ngày)

- [ ] `publish/store.py` + models + atomic/file-lock 0600; draft theo project/platform/video_role.
- [ ] Autosave draft + "Đăng lại" resolve video mới nhất; snapshot metadata vào job.
- [ ] OAuth server callback + PKCE + state TTL; một kênh YouTube, nhiều playlist.
- [ ] YouTube adapter: title/description/kids/privacy/playlist + `snippet.categoryId`; emoji-safe; idempotent + resume.
- [ ] Playlist dropdown (`GET /api/publish/youtube/playlists`) + retry riêng bước playlist.
- [ ] Worker + recovery + progress throttle + API + UI `/publish`.
- [ ] Test fake provider + test kênh thật + tài liệu cấu hình app.

### Phase 5B — TikTok (backlog, chưa làm)

> Không nằm trong phạm vi triển khai hiện tại. Giữ checklist để sau.

- [ ] `creator_info`, `brand_content_toggle`, chunk 5–64 MB, rate limit.
- [ ] UI caption/privacy; không hẹn giờ.
- [ ] Test fake provider + tài khoản thật nếu app đã audit.
- [ ] Chỉ bật khi người dùng xác nhận; giữ adapter stub/ẩn nút.

---

## 11. Test plan

### Unit

- `test_presentation.py`: default, validate, migration v1, cover style, deep copy.
- `test_overlays.py`: sprite, mask, loop, identity, palette, no-op khi tắt.
- `test_waveform.py`: peaks, progress, identity, cache version.
- `test_publish_store.py`: atomic, state machine, recovery, redaction, revision conflict.
- `test_publish_youtube.py`: categoryId, privacy, playlist idempotency, emoji validation.
- `test_publish_oauth.py`: PKCE, state TTL, offline/consent, revoke.
- `test_render.py`: typography override, interlude strip, waveform enable.
- `test_living.py`: cloud/leaf/snow mới, `plan_has_motion` với `cloud`, ember sprite/màu/glow, wind group selection/tree target.
- `test_director.py`: `display_label` duy nhất, `_target_ids` với `flag_2`, `group_id` cành cây, focused pass, normalize_plan_objects.
- `test_background.py`: `cover_style_hash`, `outro_frames`, `background_status`, overlay degrade.

### Integration

- Render 2 giây với từng hiệu ứng ở 16:9 và 9:16.
- Xuất đủ 2 video, kiểm tra `ffprobe` frame count, duration, audio.
- Outro: video dài hơn audio, tiếng không bị cắt; test có/không ambience.
- Project cũ mở + xuất lại không lỗi; interlude không tự xuất hiện.
- Đổi cover style/overlay param → cache miss; giữ nguyên → cache hit.
- Publish: resume job `uploading`, retry bước lỗi không upload lại video.
- YouTube: emoji trong title/body, made-for-kids, privacy, playlist_id.
- (5B backlog) TikTok chunk 5–64 MB + `brand_content_toggle` + privacy `SELF_ONLY`.

### Manual/visual

- 3 ảnh mẫu: trời mây, cảnh thu, phố đêm.
- Kiểm tra mây không méo công trình, tuyết/lá không che mặt, bokeh không cháy.
- Cover 3 layout, interlude (không đè countdown), outro.
- Waveform ở 4 profile; kéo sát strip thì tự đẩy lên.
- Hai object cùng label hiển thị `flag_1`/`flag_2` khớp đúng box trên ảnh.
- Cành cây: chọn một cành → cả nhóm đung đưa; gốc cành đứng yên; prompt gió kích hoạt đúng.
- Tàn lửa: nền tối/sáng, không còn bong bóng trắng; lõi vàng-cam, tro xám, glow tắt được.

---

## 12. Hiệu năng và cache

- Overlay nằm trong `build_background`: nếu không bật hiệu ứng nào, không encode thêm; nếu bật, thêm **một lần encode full-length** (base → background cuối). Dùng CRF riêng (gợi ý 16) cho overlay để tránh banding.
- Segment ảnh vẫn cache theo `image + plan`; đổi overlay chỉ re-concat + encode overlay, không render lại từng ảnh.
- Sprite pre-render; numpy chỉ vẽ trong bounding box.
- Cache key tập trung, **không phụ thuộc vòng tròn**:
  1. `base_hash` = `profile_fingerprint + cover_identity(style_hash) + motion/asset identity + outro(style_hash + image) + total_frames`.
  2. `overlay_hash` = `base_hash + overlay_effects(đã resolve palette) + mask_hash + OVERLAY_RENDER_VERSION`.
  3. `background_hash` = `base_hash + overlay_hash + total_frames`.
  - `overlay_identity` **không** dùng `background.hash` cuối cùng; nó dùng `base_hash`.
  - `picture` key = `background_hash + lines + presentation.cover/interlude + typography + waveform_hash`.
- Ghi công thức này vào code comment để tránh tự chế lại.
- Tăng version khi đổi thuật toán:
  - `MOTION_CACHE_VERSION`
  - `COVER_RENDER_VERSION`
  - `PICTURE_BUILDER_VERSION`
  - `OVERLAY_RENDER_VERSION` (mới)
  - `BACKGROUND_BUILDER_VERSION`
- Dọn cache cũ theo cơ chế hiện có; không xoá asset. Overlay là một phần của background hash nên `cleanup_backgrounds` không để file mồ côi.

---

## 13. Bảo mật và pháp lý

- Gemini vẫn chỉ gọi khi người dùng đồng ý; hiệu ứng thủ công không gửi ảnh.
- Token đăng bài: file riêng, `0600`, không log.
- **Bản quyền:** video karaoke cover có thể bị YouTube Content ID claim hoặc TikTok mute. UI cần cảnh báo trước khi đăng; người dùng chịu trách nhiệm về quyền sử dụng bài hát.
- TikTok Content Posting API yêu cầu app được audit để đăng public; nếu chưa, chỉ private/inbox.

---

## 14. Rủi ro và giảm thiểu

| Rủi ro | Mức | Giảm thiểu |
|---|---|---|
| Warp mây méo vùng nền/foreground | Cao | Box thủ công + protect + feather; giảm biên độ mặc định |
| Overlay full-frame chậm | Trung bình | Chỉ vẽ bounding box, cache, progress; CRF riêng |
| Cache invalidation sai | Trung bình | Gom identity vào một chỗ, test đổi param |
| Waveform lệch nhạc | Trung bình | Cùng nguồn audio, clip đủ dài, test 3 bài |
| Interlude strip đè chữ | Thấp | Banner nằm trong strip, tắt khi countdown; test 4 profile |
| TikTok không cho đăng public (5B backlog) | Cao | Adapter + thông báo rõ; fallback tải file |
| TikTok không hẹn giờ (5B backlog) | Trung bình | Chỉ đăng ngay; app phải chạy lúc bấm; UI ghi rõ |
| YouTube quota/verification | Trung bình | Hiển thị hạn mức; cảnh báo Testing 7 ngày |
| Token rò rỉ | Cao | 0600, redaction cả upload_url/publish_id, không trả client |

---

## 15. Câu hỏi mở

1. **Category YouTube mặc định:** `10` (Music) hay `22` (People & Blogs)? (đề xuất: Music, fallback People & Blogs)
2. **Synthetic media:** có khai báo `status.containsSyntheticMedia` cho video có hiệu ứng AI không? (đề xuất: không bật mặc định ở MVP; ghi chú legal)
3. **TikTok 5B:** có làm ngay sau 5A không, hay để backlog đến khi bạn yêu cầu? (đề xuất: backlog)
4. **Manual branch group:** có cần ô "Nhóm" để gom nhiều cành thủ công không? (đề xuất: có, tuỳ chọn)

### Đã chốt

- **Mây:** box thủ công, tái dùng "Vẽ object"; không cần lasso/brush ở MVP.
- **Kích hoạt mây:** chỉ khi người dùng chọn; không tự bật theo prompt hoặc theo ảnh.
- **Overlay stage:** nằm trong `build_background`, để "Dựng nền" và `background_status` phản ánh đúng.
- **Interlude:** banner trong dải lời (`mode: "strip"`); default tắt; `LONG_GAP = 8` giữ nguyên, `min_gap >= 8`.
- **Cover label:** `label` là badge trên title, `cover_line` là dòng dưới artist; không gộp.
- **Tên object trùng:** thêm `display_label` (`flag_1`, `flag_2`), giữ `label` ngữ nghĩa.
- **Cành cây:** nhận diện nhiều cành riêng + `group_id`; chọn một cành mở rộng cả nhóm; prompt gió kích hoạt focused pass.
- **Tàn lửa:** tách spark/ash, thêm `glow`/`ash_ratio`/`flicker`, không còn bong bóng trắng.
- **Đăng bài:** một draft = một nền tảng, một playlist.
- **"Kênh phát":** là **playlist** YouTube, không phải brand channel.
- **YouTube MVP:** chỉ 5 trường title, body, made-for-kids, privacy (**default `public`**), playlist; hỗ trợ emoji nhập/paste; **không** thumbnail/end screen/publishAt.
- **Cover/cảnh kết:** bỏ thumbnail + end screen của YouTube; vẫn giữ trang bìa/trang kết trong video.
- **TikTok:** để **backlog 5B**, không làm đợt này; giữ trong plan, ẩn UI/nút TikTok.
- **Credentials:** đã có OAuth client Desktop + `.env` khớp; project đã bật YouTube Data API v3. Còn xác nhận consent screen scopes + Test users khi kết nối.
- **OAuth client:** Desktop app (đã tạo), không dùng Web application.
- **Trang kết:** ảnh riêng nếu có, fallback ảnh bìa.
- **Waveform:** nguồn mặc định instrumental.
- **Font:** chỉ cỡ chữ ở MVP; font family để sau.
- **Privacy YouTube:** default `public`, gửi tường minh.
- **Phase 1:** gộp branches + embers, không tách 1B.

---

## 16. Definition of Done

- Tất cả tiêu chí nghiệm thu từng hạng mục đạt.
- Project v1 mở và xuất lại không lỗi.
- Có test tự động cho phần lõi; test tay cho phần hình.
- Tài liệu cập nhật: README, `docs/karaoke-background-pipeline.md`, `docs/ai-scene-director.md`.
- Không có secret trong repo, project, log.
- Cache hoạt động; xuất lại không render lại từ đầu khi không đổi input.

---

## Phụ lục A — Bản đồ file

### Mới

```
karaoke/overlays.py          # tuyết, lá, bokeh, light leak
karaoke/particles.py         # sprite + layer dùng chung cho snow/leaf
karaoke/waveform.py          # peaks + clip
karaoke/presentation.py      # cover/interlude/outro/typography
karaoke/publish/__init__.py
karaoke/publish/models.py
karaoke/publish/store.py
karaoke/publish/oauth.py
karaoke/publish/youtube.py
karaoke/publish/tiktok.py   # backlog 5B, không code đợt này
karaoke/publish/worker.py
karaoke/publish/service.py
karaoke/publish/creator_info.py

web/src/components/overlay-effects.tsx
web/src/components/presentation-settings.tsx
web/src/components/waveform-settings.tsx
web/src/components/publish/publish-workspace.tsx
web/src/components/publish/channel-cards.tsx
web/src/components/publish/draft-form.tsx
web/src/components/publish/publish-queue.tsx
web/src/app/publish/page.tsx

tests/test_presentation.py
tests/test_overlays.py
tests/test_waveform.py
tests/test_publish_store.py
tests/test_publish_youtube.py
tests/test_publish_oauth.py
tests/test_render.py
tests/test_director.py        # bổ sung display_label/group_id
```

### Sửa

```
karaoke/project.py           # schema v2, default, normalizer, deep copy
karaoke/pipeline.py          # prepare_job/open_saved/render_job wiring
karaoke/background.py        # outro frames, overlay trong build, identity
karaoke/render.py            # typography, waveform, interlude
karaoke/media.py             # make_cover style, make_outro
karaoke/director.py          # display_label, group_id cành cây, alias/region cloud, manual endpoint
karaoke/living.py            # cloud warp, snow sprite, leaf AI, wind cành cây, embers glow
karaoke/access.py            # grant ảnh outro
karaoke/profiles.py          # default mới nếu cần
karaoke/server.py            # model + allowlist + endpoint + publish router
karaoke/settings.py          # (nếu cần) không lưu secret

web/src/lib/types.ts         # field mới + display_label
web/src/components/project-setup.tsx
web/src/components/timing-editor.tsx
web/src/components/ai-effects.tsx
web/src/components/app-shell.tsx
web/src/lib/cue.ts           # (tuỳ chọn) interlude preview
README.md
docs/karaoke-background-pipeline.md   # overlay stage, outro_frames
docs/ai-scene-director.md             # display_label, group_id, cloud/branch
docs/youtube-oauth-setup.md           # hướng dẫn đăng ký OAuth (mới)
```

---

## Phụ lục B — API mới

```
POST /api/director/manual
POST /api/presentation/cover-preview
POST /api/projects/presentation                    (body: project_path + presentation? + waveform?)
POST /api/overlays/preview                        (tuỳ chọn)

GET  /api/publish/channels
POST /api/publish/channels/{provider}/connect
GET  /api/publish/oauth/callback
DELETE /api/publish/channels/{provider}
GET  /api/publish/creator-info/{provider}
GET  /api/publish/youtube/playlists
GET  /api/publish/videos
GET  /api/publish/drafts
POST /api/publish/drafts
PATCH /api/publish/drafts/{id}
DELETE /api/publish/drafts/{id}
POST /api/publish/drafts/{id}/publish
GET  /api/publish/jobs?project=...
GET  /api/publish/jobs/{id}
POST /api/publish/jobs/{id}/retry
POST /api/publish/jobs/{id}/cancel
```

---

## Phụ lục C — Wireframe UI

### Setup: "Hiệu ứng thêm"

```
┌ Hiệu ứng thêm (không cần AI) ─────────────────────┐
│ [Mây ✓]  Cường độ ─────●──  Tốc độ ──●────        │
│          [Chọn vùng mây]                          │
│ [Tuyết ✓] Mật độ ───●────  Kích thước ──●──       │
│ [Lá ✓]    Mật độ ──●────   Palette [Thu ▾]        │
│ [Bokeh]   Palette [Auto ▾]  Opacity ──●──         │
│ [Leak]    Góc ──●──         Cường độ ──●──        │
└───────────────────────────────────────────────────┘
```

### Export drawer: tab Waveform

```
┌ Waveform ─────────────────────────────────────────┐
│ [✓] Bật    Kiểu [Bars ▾]   Nguồn [Instrumental ▾]│
│ ┌───────────────────────────────────────────────┐ │
│ │                                               │ │
│ │              ▂▅█▇▃▂  (kéo được)               │ │
│ │                                               │ │
│ └───────────────────────────────────────────────┘ │
│ Snap: [↖] [↑] [↗] [←] [•] [→] [↙] [↓] [↘]             │
└───────────────────────────────────────────────────┘
```

### Trang Đăng bài

```
┌ Kênh ─────────────────────────────────────────────┐
│ YouTube  ● Đã kết nối  @kenh        [Ngắt]        │
└───────────────────────────────────────────────────┘
┌ Soạn nội dung (YouTube) ──────────────────────────┐
│ Video [Tên bài (dọc) ▾]   Playlist [Chọn ▾]      │
│ Tiêu đề [🎤 Tên bài ................................]│
│ Nội dung [🎶 ......................................]│
│ Trẻ em có xem được?  ( ) Có   (•) Không            │
│ Chế độ đăng:  (•) Công khai  ( ) Không công khai  │
│               ( ) Riêng tư                         │
│ [Lưu nháp]                    [Đăng ngay]         │
└───────────────────────────────────────────────────┘
┌ Hàng đợi ─────────────────────────────────────────┐
│ Tên bài · YouTube · ██████░░ 60% · Đang tải      │
└───────────────────────────────────────────────────┘
```

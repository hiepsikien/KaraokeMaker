# Ghép nền có hiệu ứng trước, ghép lời sau

Trạng thái: **Phase 1–4 đã triển khai**
Ngày: 2026-11-21
Phạm vi: pipeline xuất video Karaoke (Living Still → nền → lời → tiếng)

**Quyết định đã chốt:**

1. Ưu tiên **asset đã render từ AI Studio**, fallback render từ `effect_plan`.
2. **Luôn ghi `background.mp4` ra thư mục xuất**, tại
   `<output_dir>/.karaoke-media/backgrounds/`.
3. **Một profile mỗi lần xuất**, nhưng key theo `profile_id` + thông số profile
   để sau mở rộng.
4. **Chưa nhận video nền ngoài** ở bản này; chừa schema `background_video` cho Phase sau.
5. **Ambience không nằm trong hash hình**: bật/tắt ambience không render lại clip/background.
6. **`loop_duration` = duration đã render**, không lấy từ `params.loop_duration` trong plan.
7. **Cho phép thay `background.mp4` thủ công**, nhưng phải validate bằng `ffprobe`.
8. **Đổi thư mục xuất thì copy background cũ** nếu còn tìm thấy; lưu
   `source_output_dir` làm hint (không tham gia hash). Nếu hint mất → rebuild.
9. **Dọn bản nền cũ chỉ xoá file không được project nào tham chiếu.**
10. **`living.py` dùng chung `visual_plan_hash`** cho cache key và seed; bump
    `MOTION_CACHE_VERSION` khi đổi.
11. **Picture không có cache bền ở Phase 1**; đổi audio cùng độ dài sẽ overlay lại.
12. **Frame-exact**: cắt segment bằng `trim=start_frame/end_frame` và ép
    `-frames:v total_frames` ở background/picture.

**Supersedes:** tài liệu này thay thế phần asset/loop trong
`docs/ai-scene-director.md §12.4` và các điểm liên quan trong
`docs/living-still-mvp.md` khi có xung đột. Contract chính thức nằm ở §2–§4;
phần pseudocode/tên hàm chỉ là implementation note.

**Phạm vi đã code:** tách pipeline + background identity + frame-exact + lưu
background ra output (Phase 1); API/UI dựng nền, trạng thái và cleanup (Phase 2);
asset store + picker Studio (Phase 3); regression/test/docs (Phase 4).

---

## 0. Tóm tắt

Thứ tự mong muốn — **hiệu ứng trước, lời sau** — hiện đã có một phần trong
`karaoke/render.py`, nhưng toàn bộ nằm trong một hàm lớn và không có “file nền
karaoke” trung gian tái sử dụng.

Đề xuất tách thành 4 tầng:

```text
Ảnh + effect_plan/asset
   │
   ├─ (1) Effect clip  (đúng image_duration, đúng profile)
   │
   ├─ (2) background.mp4 = cover + các clip, đúng tổng thời lượng, KHÔNG tiếng
   │
   ├─ (3) picture.mp4 = background + lời (overlay 12 fps)
   │
   └─ (4) mux ×2 = (có giọng) + (không lời), theo format xuất
```

Điểm cốt lõi:

- **Tầng 2 không phụ thuộc timing lời.** Sửa lời chỉ chạy lại tầng 3–4.
- **Tầng 1 cache theo clip identity**, gồm `image_hash`, `visual_plan_hash`,
  `duration`, `width×height`, `fps`, `fit_mode`, `strip_rect`, renderer version.
- **Tầng 2 lưu và tái sử dụng theo background identity**, không dựa vào byte hash
  của file clip.
- Giữ nguyên nguyên tắc **“một picture, hai mux”**.
- Clip đã render từ AI Studio được dùng trực tiếp qua `asset_id`; nếu thiếu hoặc
  không khớp thì fallback render từ `effect_plan`.
- `background.mp4` được ghi **cố định vào thư mục xuất**, có thể replace thủ công
  nếu `ffprobe` xác nhận đúng width/height/fps/duration và không có audio.

---

## 1. Hiện trạng code

### 1.1. Luồng hiện tại

> Ghi chú: mục này mô tả code **trước Phase 1**. Phase 1 đã chuyển orchestrator
> sang `karaoke/background.py`; `render.py` còn primitive text/overlay/mux.

`karaoke/render.py::render_videos()` (khoảng dòng 520) đang làm tuần tự:

1. Dựng cover card bằng `make_cover()` — **luôn gọi**, kể cả `cover_duration = 0`
   (dòng 550–552).
2. Với mỗi ảnh nền, gọi `render_motion_loop()` để tạo clip hiệu ứng dài đúng
   `image_duration` (dòng 563–594). Nếu không có plan/hiệu ứng thì chuẩn bị ảnh tĩnh.
3. Ghép cover + các clip/ảnh thành `slideshow.mp4` trong `.karaoke-work/`
   (dòng 628–654), encode `libx264`, CRF 20.
4. Gọi `_overlay_text()` để vẽ lời lên `slideshow.mp4`, ra `picture.mp4`
   (dòng 656–658), encode thêm một lần CRF 20.
5. Gọi `_mux()` hai lần cho audio gốc và instrumental (dòng 660–681). File cuối
   theo format người dùng chọn: `mp4`, `mov` hoặc `mkv`.
6. Xoá `.karaoke-work/` trong `finally`.

### 1.2. Điểm đã tốt

- Thứ tự **effect → slideshow → lời → tiếng** đã đúng.
- `karaoke/living.py::render_motion_loop()` đã cache clip theo:

  ```text
  v{MOTION_CACHE_VERSION}
  + image_hash
  + plan_hash
  + duration
  + width × height
  + fps
  + effects
  + fit_mode
  + strip_rect
  ```

  (xem `_render_loop`, khoảng dòng 2805; key ở dòng 2853–2858).
- Hiệu ứng được render trước lyric overlay nên có thể chạy phía sau dải lời;
  text/bar bán trong suốt vẽ sau đảm bảo đọc được.
- “Một picture, hai mux” đã được giữ.
- Clip hiệu ứng được encode CRF 18 (`living.py` khoảng 2696–2726), tốt hơn
  slideshow/picture CRF 20.

### 1.3. Điểm cần sửa

| Vấn đề | Hiện tại | Hệ quả |
|---|---|---|
| Không có background trung gian | `slideshow.mp4` nằm trong work dir và bị xoá | Sửa lời rồi xuất lại vẫn phải dựng lại slideshow |
| `plan_hash` gộp cả trường không thuộc hình | `_plan_hash` hash toàn bộ plan, gồm `params.audio` và `params.loop_duration` | Bật/tắt ambience hoặc đổi metadata loop làm render lại clip vô ích |
| Cache clip ngắn có thể bị prune | `living` cache giới hạn 40 file / 2 GB | Clip có thể mất, lần xuất sau phải render lại |
| Studio chưa nối vào dự án | `BackgroundEffect.asset_id` luôn `null` (`ai-effects.tsx:454`) | Không dùng lại đúng clip đã preview |
| `render.py` chưa đọc `asset_id` | Chỉ dùng `effect_plan` | Clip Studio chỉ để xem, không dùng để xuất |
| Cover luôn được dựng | `make_cover()` gọi cả khi `cover_duration = 0` | Tốn thời gian vô ích; hash dễ phụ thuộc title/artist không cần thiết |
| Pipeline một khối | `render_videos()` làm tất cả, chỉ trả `tuple[Path, Path]` | Không có chỗ trả metadata background; khó progress/retry/test tầng |
| `public_project` là whitelist | `project.py:180` | Field top-level mới mà quên khai báo sẽ mất khi lưu. Riêng `asset_id` nằm trong effect nên `_effects_list` đã giữ nguyên |
| Job state nằm trong bộ nhớ | `_jobs` giới hạn 12, TTL 6 giờ (`server.py:80–83`) | `GET /api/jobs/{id}` mất trạng thái sau restart |

### 1.4. Kết luận phân tích

Không cần viết lại renderer. Việc cần làm là:

1. **Tách** `render_videos()` thành các stage rõ ràng.
2. **Lưu** stage nền vào thư mục xuất, có identity/hash và metadata.
3. **Nối** `asset_id` của Studio vào bước chọn clip.
4. **Thêm** API/UI để dựng nền trước và xuất lại chỉ phần lời/tiếng.
5. **Chuẩn hoá thời lượng theo frame** trước khi hash và render.

---

## 2. Kiến trúc đích

### 2.1. Sơ đồ tầng

```text
┌─────────────────────────────────────────────────────────────┐
│ Tầng 1 — Effect clip                                        │
│  input : image_path, effect_plan, image_frames, profile     │
│  output: clip MP4 ngắn, silent, đúng image_frames           │
│  cache : living/<clip_identity>.mp4                         │
│  nguồn : asset Studio (nếu hợp lệ) hoặc render_motion_loop  │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ Tầng 2 — Background karaoke                                 │
│  input : cover card + clips/ảnh theo thứ tự + total_frames  │
│  output: background.mp4 full bài, silent, yuv420p           │
│  file  : <output_dir>/.karaoke-media/backgrounds/           │
│          background-<hex>.mp4                               │
│  hash  : KHÔNG chứa lyrics, KHÔNG chứa ambience             │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ Tầng 3 — Picture có lời                                     │
│  input : background.mp4 + lines + profile                   │
│  output: picture.mp4 silent, đã overlay lời                 │
│  cache : tuỳ chọn (picture/<hash>.mp4)                      │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ Tầng 4 — Mux tiếng                                          │
│  input : picture.mp4 + audio gốc/instrumental + ambience    │
│  output: 2 file theo format xuất (mp4/mov/mkv)              │
└─────────────────────────────────────────────────────────────┘
```

### 2.2. Cache/lưu trữ theo tầng

| Tầng | Key chính | Nơi lưu | Khi nào dùng lại | Khi nào dựng lại |
|---|---|---|---|---|
| 1. Effect clip | **clip identity**: `image_hash`, `visual_plan_hash`, `duration`, `width×height`, `fps`, `fit_mode`, `strip_rect`, `MOTION_CACHE_VERSION` | `cache/living/` | Identity không đổi | Ảnh đổi, plan hình đổi, `image_duration` đổi, profile đổi |
| 2. Background | **background identity**: tập clip identity + cover inputs + **profile fingerprint** + `total_frames` + builder version | `<output_dir>/.karaoke-media/backgrounds/` | Chỉ lời/timing đổi; ambience đổi; audio cùng độ dài đổi nội dung; format xuất đổi | Ảnh/plan/profile/thời lượng/cover đổi; audio đổi độ dài |
| 3. Picture | `background_hash` + `hash(lines)` + text profile + `total_frames` + `TEXT_FPS` + picture builder version | `work/` (Phase 1) | Không tái sử dụng ở Phase 1 | Lời/timing đổi; audio đổi cũng overlay lại |
| 4. Mux | `picture_hash` + audio hash + ambience | `output_dir` | Gần như luôn chạy lại khi xuất file mới | — |

**Điểm quan trọng:** đổi title/artist/cover chỉ làm hỏng background identity, nhưng
**không** làm hỏng clip identity. Vì vậy dựng lại background vẫn nhanh.

### 2.3. Identity và hash

Không dùng byte hash của file clip làm identity, vì:

- Cache bị prune → render lại → byte có thể khác do version ffmpeg/x264 dù nội dung
  tương đương → background bị coi là stale oan.
- Ngược lại, file asset bị thay lén mà metadata không đổi thì lại không phát hiện.

Quy ước:

```text
clip_identity = sha256(
  MOTION_CACHE_VERSION
  + image_hash
  + visual_plan_hash
  + image_frames
  + width + height + fps + fit_mode + strip_rect
)

static_identity = sha256(
  IMAGE_PREP_VERSION
  + image_hash
  + width + height + fps + fit_mode
)
```

- `IMAGE_PREP_VERSION` tăng khi `prepare_slide()`/fit ảnh đổi.
- `segments[].clip_identity` phải có giá trị cả với `kind = "static"` và
  `"static-fallback"`.

Khi dùng asset Studio, thay `visual_plan_hash + renderer` bằng **asset fingerprint**:

```text
asset_fingerprint = sha256(
  ASSET_VERSION
  + asset.clip_sha256 (hoặc clip_size + clip_mtime)
  + asset.renderer_version
  + asset.asset_frames (int)
  + asset.image_hash
)
```

Nếu file asset bị thay, `asset_fingerprint` đổi → background identity đổi → rebuild.
Không dùng byte hash của clip làm identity cho cache nội bộ, nhưng **có** dùng
`clip_sha256`/size/mtime để phát hiện asset bị thay **trước khi dùng**; chỉ tin
metadata là chưa đủ.

```text
background_identity = sha256(
  BACKGROUND_BUILDER_VERSION
  + BACKGROUND_CRF + BACKGROUND_PRESET
  + toàn bộ background_images theo thứ tự (kể cả trùng)
  + map image → clip_identity/static_identity
  + cover_identity (hoặc "no-cover")
  + profile_fingerprint
  + total_frames + cover_frames + song_frames + image_frames
)
```

- **Không dùng `segments` để tính identity**, vì `segments` đã dedup. Đổi thứ tự
  `[A, B, A]` → `[A, A, B]` phải đổi hash dù tập ảnh unique không đổi.
- `visual_plan_hash`: hash plan **sau khi bỏ các trường không thuộc hình**:
  `params.audio`, `params.loop_duration`. Giữ `intensity`, `density`, `speed`,
  `objects`, `regions`, `protect`, `selected_object_ids`, `fit_transform`.
  `living.py` dùng **cùng hàm** này cho cache key **và seed**, để clip inline khớp
  với Studio preview.
- `profile_fingerprint`: hash các thông số **ảnh hưởng hình nền**: `width`,
  `height`, `fps`, `fit_mode`, `strip_rect`, **và các thông số cover**:
  `cover_title_size`, `cover_artist_size`, `cover_line_size`, `cover_max_width`,
  `COVER_RENDER_VERSION`. Không đưa font size/safe area của lời vào đây; các thông
  số đó nằm trong `text_profile_fingerprint` dùng cho tầng 3.
- `cover_identity`: hash cover image + title/artist/cover_line + `cover_frames` +
  `profile_fingerprint` (phần cover). Nếu `cover_frames = 0` thì dùng `"no-cover"`
  và bỏ title/artist/cover khỏi hash.
- Asset lưu thêm `asset_version`, `renderer_version`, `clip_size`, `clip_mtime`,
  `clip_sha256` để phát hiện file bị thay. Verify size/sha trước khi dùng.
- Tên file dùng **hex thuần**, không dùng chuỗi `sha256:<hex>` vì dấu `:` không
  hợp lệ trên Windows. Metadata vẫn có thể lưu `sha256:<hex>`.

### 2.4. Code module đề xuất

Tạo module mới `karaoke/background.py`:

```python
@dataclass(frozen=True)
class BackgroundRef:
    hash: str              # "sha256:<hex>"
    path: Path             # <output_dir>/.karaoke-media/backgrounds/background-<hex>.mp4
    source_output_dir: Path | None
    profile_fingerprint: str
    total_frames: int
    cover_frames: int
    song_frames: int
    image_frames: int
    width: int
    height: int
    fps: int
```

`BackgroundRef` chỉ là **hint tái sử dụng**, không phải nguồn sự thật. Mỗi lần
build vẫn recompute identity từ `prepared`; chỉ early-return khi hash và toàn bộ
frame counts khớp.

```python
@dataclass(frozen=True)
class BackgroundBuild:
    path: Path
    hash: str
    schema_version: int
    builder_version: int
    profile_id: str
    profile_fingerprint: str
    width: int
    height: int
    fps: int
    total_frames: int
    cover_frames: int
    song_frames: int
    image_frames: int
    segments: list[dict]   # mỗi ảnh nền duy nhất, theo thứ tự hiển thị
    created: str

    def to_project(self) -> dict:
        """Serialize các field bền vững; không lưu path tuyệt đối."""
```

```python
@dataclass(frozen=True)
class RenderResult:
    vocal: Path
    instrumental: Path
    background: BackgroundBuild
```

```python
def build_background(
    prepared: dict,
    *,
    output_dir: Path,
    profile,
    background: BackgroundRef | None = None,
    force: bool = False,
    progress=None,
    log,
) -> BackgroundBuild:
    """Dựng cover + clip hiệu ứng/ảnh tĩnh thành background.mp4 full bài."""


def render_picture(
    background: BackgroundBuild,
    lines: list[dict],
    *,
    profile,
    destination: Path,
    progress=None,
    log,
) -> Path:
    """Overlay lời lên background, trả về picture.mp4 silent."""


def mux_outputs(
    picture: Path,
    *,
    original_audio: Path,
    instrumental_audio: Path,
    cover_duration: float,
    vocal_name: str,
    instrumental_name: str,
    output_dir: Path,
    ambience: Path | None = None,
    log,
) -> tuple[Path, Path]:
    """Ghép tiếng, trả về hai file cuối theo format xuất."""
```

**Chiều phụ thuộc module** (tránh import vòng):

```text
media.py       # ảnh, probe, make_cover, prepare_slide
living.py      # effect clip + visual_plan_hash
render.py      # text/layout/overlay, _mux primitives
background.py  # build_background + render_picture + mux_outputs + render_videos
pipeline.py    # gọi render_videos từ background.py
```

`render.py` **không** import `background.py`. Nếu cần tách thêm, chuyển
`_overlay_text`/`_mux` sang `karaoke/video.py` dùng chung.

`render_videos()` **không giữ nguyên signature**:

```python
def render_videos(
    *,
    ...,
    output_profile: str = "landscape_1080p",
    background: BackgroundRef | None = None,
    rebuild_background: bool = False,
    log,
) -> RenderResult:
    build = build_background(...)
    picture = render_picture(build, lines, ...)
    vocal, instrumental = mux_outputs(picture, ...)
    return RenderResult(vocal=vocal, instrumental=instrumental, background=build)
```

Lý do đổi: `background_render` cần được ghi vào `.karaoke.json` qua
`sync_project_file`. Chỉ trả `tuple[Path, Path]` thì không có đường đưa metadata
từ `build_background()` ra `render_job()`. `BackgroundRef` cũng cần hash để kiểm tra
tái sử dụng, không chỉ `Path`.

Trong `pipeline.render_job()`:

```python
result = render_videos(...)
prepared["background_render"] = result.background.to_project()
# ... rồi mới sync_project_file(prepared, lines=lines, ...)
```

`to_project()` chỉ giữ hash, profile fingerprint, frame counts và segments;
không lưu `path` tuyệt đối.

### 2.5. Ý nghĩa `segments`

`segments` là **danh sách ảnh nền duy nhất theo thứ tự hiển thị**, không phải danh
sách slot thực tế (bài 4 phút / 8 s = 30 slot). Mỗi entry:

```json
{
  "index": 0,
  "image_path": "/path/bg1.jpg",
  "image_hash": "sha256:...",
  "kind": "motion",
  "clip_identity": "<hex>",
  "asset_id": null,
  "image_frames": 240
}
```

- Thứ tự lặp lấy từ `background_images`; `segments` chỉ mô tả clip/ảnh của từng
  ảnh duy nhất.
- `segments[].index` là **vị trí đầu tiên** của ảnh trong `background_images`.
- Identity **không** dùng `segments`; nó dùng toàn bộ `background_images` theo thứ
  tự, kể cả ảnh trùng.
- Slot cuối ngắn hơn `image_frames` được xử lý bằng `total_frames`, không cần lưu
  từng slot.
- Nếu một ảnh xuất hiện nhiều lần trong `background_images`, dùng cùng một segment.

---

## 3. Data model

### 3.1. Trong `.karaoke.json`

Thêm metadata nền. Đây là **derived cache**, không phải nguồn sự thật:

```json
"background_render": {
  "schema_version": 1,
  "builder_version": 1,
  "hash": "sha256:<hex>",
  "profile_id": "landscape_1080p",
  "profile_fingerprint": "<hex>",
  "source_output_dir": "/path/output/cu",
  "width": 1920,
  "height": 1080,
  "fps": 30,
  "total_frames": 7350,
  "cover_frames": 150,
  "song_frames": 7200,
  "image_frames": 240,
  "inputs_fingerprint": "<hex>",
  "created": "2026-11-21T10:00:00Z",
  "segments": [
    {
      "index": 0,
      "image_path": "/path/bg1.jpg",
      "image_hash": "sha256:...",
      "kind": "motion",
      "clip_identity": "<hex>",
      "asset_id": null,
      "image_frames": 240,
      "degraded": false
    }
  ]
}
```

- `schema_version` = cấu trúc JSON; `builder_version` = thuật toán dựng. Đổi một
  trong hai phải có chủ đích; không dùng lẫn.
- `source_output_dir` chỉ là **hint** để tìm file cũ khi đổi thư mục xuất; không
  tham gia hash và có thể stale.
- `inputs_fingerprint` là hash rẻ từ `path + size + mtime + visual_plan_hash +
  frame counts` để status endpoint so sánh nhanh, không hash lại toàn bộ ảnh mỗi
  lần poll.

Quy tắc:

- **Không lưu `path` tuyệt đối** trong JSON. Đường dẫn suy ra từ
  `output_dir + hash`:
  `<output_dir>/.karaoke-media/backgrounds/background-<hex>.mp4`.
- Nếu file mất, hash không khớp, metadata thiếu, hoặc `ffprobe` báo sai
  width/height/fps/duration/audio → dựng lại.
- Nếu đổi thư mục xuất: thử file ở `source_output_dir` (hint) trước; nếu còn và
  hợp lệ → **copy** sang thư mục mới, không encode lại. Nếu hint stale/mất file →
  rebuild và ghi log.
- Không tự xoá bản nền cũ; cleanup chỉ xoá file không được project nào tham chiếu.
- Có thể thêm nút **“Mở thư mục nền”** để mở `.karaoke-media/backgrounds/`.

### 3.2. Asset Studio

```json
{
  "id": "bg_01H...",
  "name": "Quán mỳ đêm mưa",
  "source_image": "/path/bg.jpg",
  "image_hash": "sha256:...",
  "effect_plan": { "...": "..." },
  "asset_version": 1,
  "profile_id": "landscape_1080p",
  "profile_fingerprint": "<hex>",
  "width": 1920,
  "height": 1080,
  "fps": 30,
  "fit_mode": "contain",
  "strip_rect": [0, 0, 0, 0],
  "renderer_version": 31,
  "loop_duration": 16.0,
  "asset_frames": 480,
  "clip_file": "loop.mp4",
  "clip_size": 12345678,
  "clip_mtime": 1763700000.0,
  "clip_sha256": "sha256:...",
  "created": "2026-11-21T10:00:00Z"
}
```

- `loop_duration` ở đây là **duration đã render**, không phải
  `effect_plan.items[].params.loop_duration`.
- `asset_frames` là nguồn so sánh chính (int); `loop_duration` chỉ để hiển thị.
- `clip_file` là tên tương đối; đường dẫn resolve từ `asset_id`:
  `<app-data>/KaraokeMaker/assets/<id>/loop.mp4`. Không lưu `clip_path` tuyệt đối.
- `params.loop_duration` hiện do `director.py` hard-code 8 và **renderer không đọc**.
  Nên bỏ khỏi plan hoặc đánh dấu legacy/ignored; không dùng làm nguồn sự thật.
- So sánh với `image_duration` bằng `asset_frames` / `fps` và tolerance, không dùng
  `==` float.
- Trước khi dùng, verify `clip_size` và `clip_sha256` (hoặc ít nhất size + mtime);
  metadata một mình không đủ.

### 3.3. Thứ tự ưu tiên khi chọn clip

Với mỗi ảnh nền:

1. Nếu có `asset_id`:
   - Resolve `assets/<id>/loop.mp4`; file tồn tại.
   - Verify `clip_size` + `clip_sha256`/mtime.
   - `asset.asset_version` khớp `ASSET_VERSION`.
   - `asset.profile_fingerprint == profile_fingerprint`.
   - `asset.image_hash == hash(ảnh hiện tại)`.
   - `asset.fit_mode` khớp profile.
   - `asset.strip_rect` khớp `(0, 0, 0, 0)` của export (hoặc khớp cấu hình mới).
   - `asset.fps` khớp profile.
   - `asset.renderer_version == MOTION_CACHE_VERSION`.
   - `abs(asset.asset_frames / asset.fps - image_duration) <= 0.5 / fps`.
   → Dùng clip.
2. Nếu asset không hợp lệ → ghi log lý do, fallback bước 3.
3. Nếu có `effect_plan` + hiệu ứng motion → `render_motion_loop()`.
4. Nếu không → ảnh tĩnh.

Không làm fail export chỉ vì asset hỏng.

### 3.4. Asset store

Asset Studio **không được trỏ vào cache** (`studio/` prune 100 file/1.5 GB,
`living/` prune 40 file/2 GB), vì asset sẽ biến mất.

Đề xuất:

```text
<app-data>/KaraokeMaker/assets/
  bg_01H.../
    loop.mp4
    meta.json
```

- `<app-data>` là thư mục dữ liệu ổn định theo platform (macOS:
  `~/Library/Application Support/KaraokeMaker`, Windows: `%APPDATA%\KaraokeMaker`,
  Linux: `~/.local/share/karaokemaker`), không phải cache.
- `id`: `bg_` + UUID4 hex hoặc ULID; validate bằng regex.
- Khi “Lưu thành asset”: **copy** clip từ Studio cache sang `assets/<id>/loop.mp4`,
  ghi `meta.json`.
- Resolve clip từ `asset_id` (`assets/<id>/loop.mp4`), **không** lưu `clip_path`
  tuyệt đối và **không** gọi `ensure_allowed()` cho đường dẫn nội bộ này.
  `ensure_allowed()` chỉ dùng cho ảnh nguồn/clip do client gửi.
- Ghi atomic: `.tmp` + `os.replace`.
- Có lock cross-process khi tạo/xoá asset; dùng `fcntl.flock` hoặc lockfile có
  timeout/stale-PID.
- Lưu `clip_size`, `clip_mtime`, `clip_sha256` để phát hiện file bị thay; verify
  trước khi dùng.
- Có thể bỏ `index.json`, quét `meta.json` để tránh hai nguồn sự thật. Nếu giữ
  index, index chỉ là cache và phải rebuild được từ `meta.json`.
- `DELETE` chỉ xoá trong thư mục asset, không đi theo symlink.

---

## 4. Quy tắc kỹ thuật cần chốt

### 4.1. Loop và thời lượng

`render_motion_loop(duration=D)` tạo loop khép kín theo chu kỳ `D`
(`u = t / D`). Vì vậy:

- **Không cắt clip 16 s thành 8 s rồi lặp**, vì điểm nối 8 s sẽ lệch.
- Chỉ dùng asset khi `render_duration` khớp `image_duration` (có tolerance).
- Nếu người dùng muốn đổi `image_duration`, render lại clip theo thời lượng mới.
- Nếu clip ngắn hơn slot, có thể `-stream_loop -1`; nhưng chỉ an toàn khi clip
  được thiết kế loop đúng chu kỳ được dùng.
- `params.loop_duration` trong plan là legacy, không dùng để quyết định.
- `living.py` phải có `visual_plan_hash(plan)` dùng cho **cả cache key và seed**;
  bump `MOTION_CACHE_VERSION` (30 → 31). `studio.py` và `background.py` dùng chung
  hàm này, nhờ vậy clip inline và clip Studio có cùng seed/vị trí hạt.
- Không chỉ sanitize plan ở `background.py`: nếu không sửa `living.py`, cache và
  seed vẫn phụ thuộc `audio`/`loop_duration`.

### 4.2. Cover và lời

- Cover nằm trong background, không nằm trong tầng lời.
- Lời bắt đầu ở `t = cover_duration`.
- `_overlay_text()` giữ cách tính `song_time = index / TEXT_FPS - cover_duration`.
- Nếu `cover_frames = 0`: **bỏ qua `make_cover()`**, không đưa title/artist/
  cover_line/cover image vào background hash.
- Nếu `cover_duration < 0.05` (ngưỡng hiện tại của `render.py`), chuẩn hoá về 0.
- Dùng cùng `total_frames` cho background và lyric overlay để tránh lệch 1 frame.

### 4.3. Audio, ambience và hash

- `background.mp4` và `picture.mp4` **không có audio**.
- Audio gốc/instrumental chỉ ghép ở tầng 4.
- Ambience (nếu bật) tạo ở tầng 4, không nằm trong background.
- **`visual_plan_hash` bỏ `params.audio` và `params.loop_duration`**. Nhờ vậy:
  - Bật/tắt ambience không làm đổi clip identity/background identity.
  - Đổi `params.loop_duration` legacy không render lại.
- `_ambient_kind()` vẫn đọc `background_effects` để quyết định ambience; orchestrator
  tính `ambience: Path | None` rồi truyền vào `mux_outputs()`, không để
  `mux_outputs()` tự quét effects.
- Đổi audio **cùng độ dài** → dùng lại background; đổi audio **khác độ dài** →
  background rebuild vì `total_frames` đổi.

### 4.4. Chất lượng encode

Với ảnh có hiệu ứng, pipeline hiện tại encode **3 lần**:

1. Clip hiệu ứng: `libx264`, CRF 18 (`living.py:2696–2726`).
2. Slideshow/background: `libx264`, CRF 20 (`render.py:645–651`).
3. Picture có lời: `libx264`, CRF 20 (`render.py:355–362`).

Ảnh tĩnh chỉ encode 2 lần (slideshow → picture). Vì vậy:

- Giữ CRF 20 cho background sẽ **không làm giảm thêm** so với hiện tại; nó giữ
  nguyên mức chất lượng hiện có.
- Muốn giảm generation loss phải nâng chất lượng **clip** (CRF 16–18) hoặc dùng
  intermediate chất lượng cao/lossless; chỉ nâng background lên 16–18 là chưa đủ.
- Phase 1 chọn background CRF 18 để bù cho lần encode thêm; chấp nhận file lớn hơn.

### 4.5. Lưu trữ, validate và cleanup

- Background ghi vào `<output_dir>/.karaoke-media/backgrounds/background-<hex>.mp4`.
- Tên file chỉ dùng hex; metadata có thể giữ `sha256:<hex>`.
- Khi tái sử dụng, **luôn `ffprobe`**:
  - `width`, `height` khớp profile.
  - `fps` khớp profile (tolerance).
  - `nb_frames` (nếu container có) hoặc `duration` khớp `total_frames`
    (tolerance 1 frame).
  - **không có audio stream**.
  - `pix_fmt` là `yuv420p`.
  - `schema_version` và `builder_version` khớp hằng số hiện tại.
- Nếu file sai → báo log và dựng lại; không dùng im lặng.
- Đổi thư mục xuất: thử `source_output_dir` hint; nếu file cũ hợp lệ → copy
  atomic; nếu không → rebuild và ghi log.
- Ghi atomic: `.tmp` + `os.replace`.
- **Lock cross-process** theo background hash: dùng `fcntl.flock` trên macOS/Linux,
  `msvcrt` trên Windows. Nếu dùng lockfile `O_CREAT|O_EXCL`, phải có timeout và
  stale-PID cleanup; không để lock kẹt sau crash.
- Cleanup: quét `*.karaoke.json` trong `output_dir`, project hiện tại và các
  project đang mở; giữ mọi hash/path được tham chiếu; chỉ xoá file trong
  `.karaoke-media/backgrounds/` không được tham chiếu; không đi theo symlink; luôn
  giữ hash hiện tại. Nếu không xác định chắc một file có được tham chiếu hay không,
  **giữ lại**.
- Cleanup có **age guard**: chỉ xoá file cũ hơn N ngày (ví dụ 7) và không phải file
  đang được build/lock; tránh xoá file vừa tạo trước khi project kịp lưu.
- Không tự xoá file người dùng; cleanup là hành động chủ động.

### 4.6. Fallback

| Tình huống | Hành vi |
|---|---|
| Một effect lỗi | Dùng ảnh tĩnh cho ảnh đó, log lỗi, ghi `kind = "static-fallback"` + `degraded = true` |
| Asset thiếu/hỏng/không khớp | Render inline từ plan |
| Không có effect plan | Ảnh tĩnh như hiện tại (`kind = "static"`) |
| Background dựng lỗi | Báo lỗi rõ, không xuất video thiếu nền |
| Background trong output bị xoá/di chuyển | Tự dựng lại khi xuất |
| Background bị replace sai chuẩn | `ffprobe` phát hiện, báo lỗi, dựng lại |
| Audio thiếu | `export_gaps()` đã chặn từ trước |

- Nếu `degraded = true`, UI cảnh báo và **không tự động reuse** background đó cho
  lần xuất sau; “Dựng lại nền” phải retry effect lỗi.
- Chỉ reuse tự động khi `degraded = false` và file hợp lệ.

### 4.7. Tương thích cũ

Thêm field top-level `background_render` vào đủ các chỗ:

1. `karaoke/project.py`: `empty_project`, `project_from_fields`, `public_project`,
   `prepared_from_project`.
2. `karaoke/server.py`: `ProjectFieldsRequest`, `OpenSavedRequest`, session keys
   (nếu client gửi/đọc).
3. `karaoke/pipeline.py`: `prepare_job`, `open_project_file`, `sync_project_file`.
4. `web/src/lib/types.ts`: `Project`, `ProjectFields`, `emptyFields`,
   `fieldsFromProject`, `fieldsPayload` (nếu UI hiển thị trạng thái nền).
5. Test round-trip save → load; mở project cũ thiếu field.

Lưu ý:

- `public_project` là whitelist; quên field top-level = mất âm thầm.
- `asset_id` nằm trong `background_effects[]`; `_effects_list` dùng `dict(item)`
  nên đã bảo toàn `asset_id` và các field con.
- `render_videos()` đổi signature; cập nhật `pipeline.render_job` và test mock.

### 4.8. Chuẩn hoá thời lượng theo frame

Hash và render phải dùng cùng đơn vị frame:

```python
fps = profile.fps
cover_frames = 0 if cover_duration < 0.05 else round(cover_duration * fps)
image_frames = max(1, round(image_duration * fps))
song_frames = ceil(song_length * fps)
total_frames = cover_frames + song_frames
cover_duration = cover_frames / fps
image_duration = image_frames / fps
total_duration = total_frames / fps
```

- Hash trên `total_frames`, `cover_frames`, `image_frames`, không trên số thực thô.
- **Frame-exact khi concat:** mỗi segment dùng
  `trim=start_frame=0:end_frame=<frames>,setpts=PTS-STARTPTS`; output dùng
  `-frames:v total_frames`. Không chỉ dựa vào `-t` + `fps` + `concat`, vì mỗi
  segment có thể lệch ±1 frame.
- `_overlay_text()` nhận `total_duration = total_frames / fps` (hoặc `total_frames`)
  và thêm `-frames:v total_frames`; không chỉ dựa `shortest=1`.
- Test đọc `nb_frames` bằng `ffprobe -count_frames`; nếu container không có
  `nb_frames`, fallback tolerance 1 frame.
- `_mux()` dùng `cover_duration = cover_frames / fps` cho `adelay`, không dùng số
  thực thô ban đầu.
- `ceil` cho `song_frames` để `-shortest` không cắt đuôi audio.
- `cover_frames = 0` thì bỏ `make_cover()`.
- Slot cuối ngắn hơn `image_frames` được cắt theo `total_frames`.

---

## 5. API và UI

### 5.1. API

Job hiện tại là in-memory (12 job, TTL 6 giờ), nên trạng thái nền **không được**
chỉ dựa vào job record. API đã triển khai theo job, nhưng trạng thái bền vững vẫn
đọc từ project + `ffprobe`:

| Endpoint | Việc |
|---|---|
| `POST /api/jobs/{id}/background` | Dựng nền; body `{rebuild: bool}`; chạy thread, cập nhật `background.percent` |
| `GET /api/jobs/{id}/background` | Trả ready/stale/missing/building/error + lý do, hash, frame counts |
| `GET /api/jobs/{id}` | Bao gồm field `background` để UI poll chung |
| `POST /api/jobs/{id}/render` | Nếu nền thiếu/stale hoặc `rebuild_background=true` thì dựng trước, rồi overlay lời + mux |
| `POST /api/jobs/{id}/background/cleanup` | Dọn bản nền cũ, giữ file được tham chiếu + file mới |
| `POST /api/studio/assets` | Lưu clip full-res từ Studio thành asset |
| `GET /api/studio/assets` | Liệt kê asset để chọn |
| `GET /api/studio/assets/{id}/clip` | Serve clip asset để preview |
| `DELETE /api/studio/assets/{id}` | Xoá asset |

- Trạng thái bền vững đọc từ project + probe file; job record chỉ để progress tạm
  thời. Sau restart, UI vẫn biết nền có sẵn hay không.
- Trạng thái nền được recompute từ `background_render` + inputs hiện tại; không
  tin tuyệt đối vào job record.
- Mọi endpoint ghi ra `output_dir` phải qua `ensure_allowed()` và `grant()` giống
  `render_job` (`pipeline.py:483–490`). `project_path` client gửi cũng phải qua
  `ensure_allowed()`/`grant_project_paths`.
- `RenderRequest` đã có `rebuild_background: bool = false` để nút “Dựng lại nền”
  hoạt động.
- Cleanup kiểm tra `ensure_allowed()` trước khi quét/xoá; có age guard và giữ file
  không xác định được tham chiếu.

### 5.2. Progress

- `build_background(progress=...)` nhận callback `(done, total)`.
- Truyền progress xuống `render_motion_loop(progress=...)` (đã hỗ trợ sẵn,
  `living.py:2819`).
- Với concat FFmpeg, dùng `-progress pipe:1` hoặc progress thô theo số clip.
- `_overlay_text()` đã có callback progress theo frame.
- Background build cập nhật `background.percent`; UI poll `GET /api/jobs/{id}` để hiển thị.

### 5.3. UI

- **Biên tập / Nền có hiệu ứng**:
  - 3 trạng thái: **chưa có nền**, **có nền đúng hash**, **có nền nhưng stale**.
  - Stale phải kèm lý do: đổi ảnh, đổi profile, đổi độ dài audio, đổi cover…
  - Nút **Dựng nền**, **Dựng lại nền**.
  - Hiển thị thời lượng, profile, hash rút gọn, đường dẫn
    `<output_dir>/.karaoke-media/backgrounds/`.
  - Nút **Mở thư mục nền** và **Dọn bản nền cũ**.
- **Studio**:
  - Sau khi render bản chuẩn, thêm nút **Lưu thành asset**.
  - Cho đặt tên asset.
  - Có thể thêm **Gửi sang dự án** nếu đang mở dự án.
- **Karaoke / AiEffects**:
  - Dropdown “Dùng clip đã render” cho từng ảnh.
  - Chọn asset → ghi `asset_id` vào `BackgroundEffect`.
  - Cảnh báo nếu asset khác profile, khác `loop_duration`, hoặc renderer version.
- **Xuất video**:
  - Hiển thị 2 giai đoạn:
    - `1/2 Đang dựng nền có hiệu ứng…`
    - `2/2 Đang ghép lời và tiếng…`
  - Nếu chỉ sửa timing lời, ghi rõ “Dùng lại nền đã dựng”.
  - Nếu format là `mov`/`mkv`, vẫn ghi đúng “2 file theo format xuất”.

---

## 6. Kế hoạch triển khai

| Phase | Nội dung | Thời lượng |
|---|---|---|
| **1. Tách render + lưu nền ra output** | Tạo `karaoke/background.py`; tách `build_background()`, `render_picture()`, `mux_outputs()`; `RenderResult`/`BackgroundRef`; `living.visual_plan_hash` + bump cache version; static/cover identity; frame-exact trim; ffprobe validate; `source_output_dir` copy; progress; test | **2,5–3,5 ngày** |
| **2. API/UI trạng thái nền** | Endpoint theo job, status ready/stale/missing + lý do, progress 2 giai đoạn, nút Dựng nền/Dựng lại/Mở thư mục/Dọn bản cũ, `rebuild_background`, tự dựng khi xuất nếu thiếu | 1–1,5 ngày |
| **3a. Asset store + API** | Thư mục asset ổn định, copy khỏi cache, atomic + lock, metadata/fingerprint, verify khi dùng, API list/delete | 1–1,5 ngày |
| **3b. UI picker + validate** | Picker trong `AiEffects`, cảnh báo profile/loop/renderer, fallback inline | 1–1,5 ngày |
| **4. Regression + docs** | Test cũ xanh, test `tests.test_background`, cập nhật README/docs | 0,5–1 ngày |

**Phase 1–3 đã triển khai.** Phase 1 làm nền tảng render; Phase 2 thêm API/UI
dựng nền, trạng thái, `rebuild_background`, cleanup; Phase 3 thêm asset store và
picker Studio. Phase 4 đã chạy regression, test mới và cập nhật README/docs.

**Bản tối thiểu có giá trị ngay:** Phase 1 + 2 (khoảng **3,5–5 ngày**).

Khi đó đã có đúng luồng:

```text
dựng nền có hiệu ứng → ghép lời → ghép tiếng
```

và sửa lời không phải render lại hiệu ứng. Phase 3a + 3b đã nối **clip đã render
từ Studio** qua `asset_id`; asset không khớp profile/thời lượng sẽ fallback inline.
Tổng bản đầy đủ khoảng **6–9 ngày**.

---

## 7. Test và tiêu chí nghiệm thu

### 7.1. Unit test

- [ ] `static_identity` ổn định; bump `IMAGE_PREP_VERSION` làm đổi.
- [ ] Đổi `cover_title_size`/`cover_max_width` → background identity đổi.
- [ ] `background_hash` đổi khi đổi ảnh, plan hình, profile fingerprint,
      `image_frames`, `cover_frames`, cover text, hoặc `song_frames`.
- [ ] `background_hash` **không đổi** khi chỉ đổi `params.audio` hoặc
      `params.loop_duration` legacy; patch `karaoke.living._encode`/`_render_loop`
      để assert clip không render lại.
- [ ] `background_hash` **không đổi** khi chỉ đổi font size/safe area của lời;
      picture layout đổi.
- [ ] `background_hash` đổi khi clip identity đổi, **không** đổi khi chỉ byte file
      clip đổi do render lại cùng identity.
- [ ] Reorder ảnh trùng `[A,B,A]` → `[A,A,B]` → background identity đổi.
- [ ] Asset hợp lệ được chọn; asset sai profile/loop/fps/strip/renderer/hash bị fallback.
- [ ] Asset bị thay file (cùng size khác nội dung; khác mtime) → fingerprint/validate
      phát hiện.
- [ ] Project round-trip giữ `background_render` và `asset_id`; `/api/jobs/open`
      (legacy `open_saved`) không làm mất field.
- [ ] Project cũ thiếu field mới vẫn load/xuất bình thường.
- [ ] `cover_duration = 0` và `0.01` chuẩn hoá về cùng hash; `make_cover` không gọi.
- [ ] Tên file background không chứa `:`.
- [ ] `background_render.schema_version` và `builder_version` tách biệt; đổi schema
      không tự invalidate nếu builder không đổi (và ngược lại).

### 7.2. Integration test

- [ ] Xuất lần 1: dựng background + picture + 2 video.
- [ ] `background.mp4` nằm trong `<output_dir>/.karaoke-media/backgrounds/`,
      tên `background-<hex>.mp4`.
- [ ] Xuất lần 2 chỉ đổi timing lời: `karaoke.background.render_motion_loop`
      **không được gọi** (patch và assert `not called`).
- [ ] Đổi thư mục xuất rồi **reopen project**: copy được nếu `source_output_dir`
      còn file hợp lệ; nếu không → rebuild có log.
- [ ] Đổi audio cùng độ dài: dùng lại background; picture vẫn overlay lại (Phase 1
      không có picture cache); đổi audio khác độ dài: rebuild background.
- [ ] Đổi ambience (`params.audio`): không rebuild background, không render lại clip.
- [ ] Background bị thay bằng file sai width/height/fps/duration hoặc có audio:
      `ffprobe` phát hiện và dựng lại.
- [ ] Xoá background trong output: xuất lại tự dựng, không lỗi.
- [ ] Slot cuối ngắn hơn `image_frames` (`song_frames % image_frames != 0`):
      `ffprobe -count_frames` trả đúng `total_frames`.
- [ ] Export `mov` và `mkv`: hai file đúng format, background/picture vẫn mp4.
- [ ] Cleanup giữ file đang dùng, file được project khác tham chiếu, file mới hơn
      age threshold, và file đang build/lock.
- [ ] Hai thread/process cùng `build_background` một hash: chỉ một encode, không
      ghi đè hỏng.
- [ ] Background đúng `width × height`, đúng fps, đúng tổng thời lượng, không audio.
- [ ] `picture.mp4` có lời đúng vị trí strip; hai file cuối có audio và thời lượng khớp.
- [ ] Fallback motion → static được đánh dấu `kind=static-fallback`/`degraded`;
      “Dựng lại nền” retry effect lỗi.
- [ ] Status endpoint chỉ đọc metadata/probe, không hash lại toàn bộ ảnh.
- [ ] Test cũ `tests.test_karaoke`, `tests.test_living`,
      `tests.test_project_effects` vẫn xanh.
- [ ] `cd web && npx tsc --noEmit` pass.

### 7.3. Smoke test thủ công

- [ ] 16:9, 1 ảnh, có hiệu ứng, bài 10 s.
- [ ] 9:16, 2 ảnh, có hiệu ứng, bài 10 s; chữ nằm trong safe area.
- [ ] Ảnh không có effect plan → nền tĩnh.
- [ ] Asset Studio 16 s + `image_duration = 16` → dùng asset.
- [ ] Asset Studio 16 s + `image_duration = 8` → fallback inline/cảnh báo.
- [ ] Replace background đúng chuẩn → dùng; replace sai → báo lỗi/dựng lại.
- [ ] `cover_duration = 0` → không có cover card.

### 7.4. Lệnh test chuẩn

```bash
.venv-karaoke/bin/python -m unittest tests.test_director tests.test_project_effects tests.test_living tests.test_background tests.test_assets
.venv-karaoke/bin/python -m unittest tests.test_karaoke
cd web && npx tsc --noEmit
```

Thêm `tests.test_background` vào README / `docs/living-still-mvp.md`.

---

## 8. Rủi ro và cách xử lý

| Rủi ro | Cách xử lý |
|---|---|
| Background cũ/stale | Hash identity + `ffprobe` validate; thiếu/sai thì dựng lại |
| Loop bị “giật” khi tái dùng clip | Chỉ dùng asset khi `render_duration` khớp `image_duration` (tolerance) |
| Dung lượng thư mục xuất lớn | Cảnh báo dung lượng; nút “Dọn bản nền cũ”; không tự xoá file người dùng |
| Đổi thư mục xuất | Copy nếu `source_output_dir` còn file hợp lệ; hint stale → rebuild có log |
| Replace thủ công sai chuẩn | `ffprobe` kiểm tra width/height/fps/duration/audio; báo lỗi rõ |
| Xuất lâu lần đầu | Progress theo ảnh; log rõ đang ở giai đoạn nào |
| Quên field trong `public_project` | Thêm test round-trip save/load; checklist 7 chỗ; test legacy `open_saved` |
| Hai request/process cùng dựng một nền | Lock cross-process + atomic write; lockfile có timeout/stale-PID |
| Chất lượng giảm do encode 2–3 lần | Ghi rõ pipeline; background CRF 18; đổi chất lượng phải bump builder version |
| Asset khác profile nhưng vẫn bị dùng | Verify clip size/sha, profile fingerprint, fit_mode, fps, strip_rect, renderer version |
| Xoá ảnh nền nhưng effect còn trong project | Lọc theo `background_images` như `_ambient_kind()` hiện tại |
| Cleanup xoá nhầm file project khác | Quét `*.karaoke.json` + project đang mở; age guard; giữ file không chắc; không symlink |
| Fallback motion kẹt ảnh tĩnh | Ghi `degraded=true`, UI cảnh báo, không auto-reuse; “Dựng lại nền” retry |
| Status endpoint hash lại toàn bộ ảnh | Dùng `inputs_fingerprint` từ path/size/mtime + plan hash; chỉ hash đầy đủ khi build |
| Job restart mất trạng thái | Trạng thái bền vững đọc từ project + `ffprobe`, không chỉ job record |

---

## 9. Quyết định đã chốt

| Câu hỏi | Quyết định | Hệ quả |
|---|---|---|
| Nguồn clip hiệu ứng | **Asset Studio + fallback `effect_plan`** | Đã triển khai asset store + picker; export vẫn chạy nếu asset thiếu/hỏng. |
| Nơi lưu background | **`<output_dir>/.karaoke-media/backgrounds/`** | Không dùng cache làm nơi lưu chính. Cần cảnh báo dung lượng, nút dọn bản cũ. |
| Nhiều profile | **Một profile mỗi lần xuất**, key theo `profile_fingerprint` | UI chỉ chọn một; schema/API sẵn sàng cho nhiều profile sau này. |
| Video nền bên ngoài | **Chưa làm ở bản này**, để Phase sau | Schema có thể chừa `background_video`; chưa thêm UI/validate. |
| Ambience & hash hình | **Bỏ `audio`/`loop_duration` khỏi `visual_plan_hash`** | Bật/tắt ambience không render lại clip/background. |
| Tên thư mục nền | **`.karaoke-media/backgrounds/`** | Tách khỏi `.karaoke.json` và `.karaoke-work/`; dễ tìm/dọn. |
| Replace thủ công | **Có, validate bằng `ffprobe`** | Người dùng có thể thay file đúng chuẩn; sai thì báo lỗi/dựng lại. |
| Nguồn `loop_duration` | **Duration đã render / asset** | `params.loop_duration` là legacy, không dùng làm nguồn sự thật. |
| Dọn bản nền cũ | **Giữ file được project tham chiếu** | Quét `*.karaoke.json`; không xoá file đang dùng; không symlink. |
| Copy khi đổi output | **Dùng `source_output_dir` làm hint** | Hint không tham gia hash; stale thì rebuild có log. |
| Picture cache | **Không cache bền ở Phase 1** | Đổi audio cùng độ dài vẫn overlay lại; đơn giản, tránh file lớn. |
| Frame-exact | **Trim theo frame + `-frames:v`** | Test bằng `ffprobe -count_frames`; tránh lệch ±1 frame. |
| Version | **Tách `schema_version` và `builder_version`** | Đổi schema không tự invalidate thuật toán; đổi builder mới rebuild. |
| Hash/seed clip | **`living.visual_plan_hash` dùng chung** | Bump `MOTION_CACHE_VERSION` 30 → 31; Studio và inline cùng seed. |
| Cleanup an toàn | **Age guard + lock + giữ khi không chắc** | Tránh xoá project ngoài output hoặc file đang build. |

### 9.1. Hệ quả của việc lưu background ra output

- `background.mp4` là artifact người dùng thấy được, không phải cache tạm.
- Khi đổi ảnh/effect/profile/thời lượng, hash đổi → ghi file mới; file cũ không
  tự xoá.
- Khi đổi thư mục xuất, background được **copy** sang thư mục mới nếu
  `source_output_dir` còn file hợp lệ; hint stale/thiếu → dựng lại có log.
- Cần kiểm tra quyền ghi thư mục xuất trước khi dựng nền (`ensure_allowed` + `grant`).
- Có thể thêm lựa chọn “chỉ dùng cache” sau này nếu người dùng không muốn file lớn.

### 9.2. Hệ quả của việc ưu tiên asset Studio

- Asset store + picker đã triển khai; asset phải đúng profile/thời lượng/renderer.
- Asset phải lưu kèm `asset_version`, `profile_fingerprint`, `loop_duration`
  (duration đã render), `asset_frames`, `fit_mode`, `fps`, `strip_rect`,
  `renderer_version`, `image_hash`, `clip_size`, `clip_sha256`.
- Asset phải copy ra thư mục ổn định, không trỏ vào cache; verify size/sha trước
  khi dùng, không chỉ tin metadata.
- Nếu asset không khớp, export tự fallback render từ plan và ghi log; không chặn
  người dùng.
- Cần UI cảnh báo khi chọn asset sai profile/thời lượng/renderer.

### 9.3. Video nền ngoài (Phase sau)

Khi làm, thêm field:

```json
"background_video": {
  "path": "/path/background.mp4",
  "profile_fingerprint": "<hex>",
  "total_frames": 7350,
  "hash": "sha256:<hex>"
}
```

Nếu có `background_video` hợp lệ thì bỏ qua tầng 1–2, dùng trực tiếp làm nền.
Chưa làm UI/validate ở bản này.

---

## 10. Việc không làm trong bản này

- Không viết lại renderer hiệu ứng.
- Không đổi cách tính timing lời.
- Không đổi “một picture, hai mux”.
- Không render nhiều profile cùng lúc; mỗi lần xuất một profile.
- Không thêm UI/validate cho `background_video` ở bản này; chỉ chừa schema.
- Không lưu mask/depth vào project; chỉ lưu plan + asset reference.
- Không dùng unofficial API hay dịch vụ ngoài cho bước ghép.
- Không tự động xoá file trong thư mục xuất của người dùng.

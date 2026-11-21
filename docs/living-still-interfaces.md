# Living Still MVP — interface chung cho subagents

Bản này chốt contract để backend/frontend/renderer/test làm song song. Khi mâu thuẫn,
`docs/living-still-mvp.md` thắng.

## Phạm vi P0 (đang làm)

- UI-first, 16:9, một effect: **mưa**.
- Gemini inventory + intent + plan.
- Lưu plan vào project; render loop mưa; preview.
- Không làm trong P0: SAM 2/Depth, C1, 9:16, Studio tab, ambient audio, WebGL.

## File ownership

| Agent | Files |
|---|---|
| Backend | `karaoke/director.py`, `karaoke/server.py`, `karaoke/project.py`, `karaoke/pipeline.py`, `karaoke/media.py` (chỉ thêm preview), `requirements-karaoke.txt`, `.gitignore`, `.env.example` |
| Frontend | `web/src/components/ai-effects.tsx`, `web/src/lib/types.ts`, `web/src/components/project-setup.tsx` |
| Renderer | `karaoke/living.py`, `karaoke/render.py` |
| Test/eval | `tests/test_director.py`, `tests/test_project_effects.py`, `tests/test_living.py`, `scripts/eval_living_still.py`, `docs/eval-ground-truth.json` |

Không sửa file ngoài ownership. Không đụng `models/`, `gui_data/`, `.venv*`.

## Schema `background_effects[]`

Mỗi item:

```json
{
  "image_path": "/path/bg.jpg",
  "image_hash": "sha256:...",
  "effect_prompt": "mưa nhẹ",
  "effect_consent_receipt": {
    "timestamp": "2026-11-21T10:00:00Z",
    "image_hash": "sha256:...",
    "provider": "gemini",
    "model": "gemini-3.8-flash"
  },
  "selected_object_ids": ["lantern-1", "bowl-1"],
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
    "objects": [
      {"id": "lantern-1", "label": "lantern", "affordances": ["hanging"],
       "box": {"x1": 0.53, "y1": 0.28, "x2": 0.59, "y2": 0.48},
       "anchors": {"pivot": {"x": 0.56, "y": 0.285}, "opening": null}}
    ],
    "regions": [
      {"id": "outdoor", "polygon": [{"x": 0.5, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0.5, "y": 1}]}
    ],
    "protect": [
      {"label": "face", "box": {"x1": 0.37, "y1": 0.43, "x2": 0.44, "y2": 0.57}}
    ],
    "items": [
      {"effect": "rain", "target_ids": [], "params": {"intensity": 0.55, "loop_duration": 8, "audio": false}}
    ]
  },
  "model": "gemini-3.8-flash",
  "created": "2026-11-21T10:00:05Z",
  "asset_id": null
}
```

Quy ước:

- `box`/`point`/`polygon` normalized 0..1 theo **ảnh gốc sau EXIF, trước letterbox**.
- `fit_transform` map ảnh gốc → frame 16:9.
- `regions`/`protect` nằm trong `effect_plan` để renderer dùng.
- `items.effect` P0: `rain`. P1: `steam`, `wind`, `water`, `light`. P2: `snow`,
  `fire`, `embers`, `birds`, `sunrays`, `fog`. `birds`/`sunrays` chỉ xuất hiện khi
  prompt yêu cầu, dù inventory có phát hiện chim/mặt trời.
- `target_ids` là các object được chọn trong UI.
- `loop_duration` tính bằng giây.

## API `/api/director/analyze`

Request:

```json
{
  "image_path": "/path/bg.jpg",
  "prompt": "mưa nhẹ",
  "consent": true,
  "model": ""
}
```

Response:

```json
{
  "image": "/path/bg.jpg",
  "image_hash": "sha256:...",
  "width": 1920,
  "height": 1080,
  "prompt": "mưa nhẹ",
  "model": "gemini-3.8-flash",
  "scene": "short description",
  "objects": [
    {"id": "lantern-1", "label": "lantern",
     "box": {"x1": 0.53, "y1": 0.28, "x2": 0.59, "y2": 0.48},
     "anchors": {"pivot": {"x": 0.56, "y": 0.285}, "opening": null},
     "affordances": ["hanging"], "confidence": 0.9}
  ],
  "regions": [
    {"id": "outdoor", "polygon": [{"x": 0.5, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0.5, "y": 1}]}
  ],
  "protect": [{"label": "face", "box": {"x1": 0.37, "y1": 0.43, "x2": 0.44, "y2": 0.57}}],
  "intent": {"effects": [{"type": "rain", "intensity": 0.55, "audio": false}]},
  "effect_plan": {
    "version": 1, "image_hash": "sha256:...", "profile_id": "landscape_1080p",
    "fit_transform": {"mode": "contain", "source_width": 1920, "source_height": 1080, "frame_width": 1920, "frame_height": 1080, "scale": 1.0, "offset_x": 0, "offset_y": 0},
    "objects": [], "regions": [], "protect": [],
    "items": [{"effect": "rain", "target_ids": [], "params": {"intensity": 0.55, "loop_duration": 8, "audio": false}}]
  },
  "usage": {"prompt": 1545, "output": 1064, "thoughts": 1460, "total": 4069},
  "cached": false
}
```

- `consent=false` → 400.
- Không có `GEMINI_API_KEY` → 502 với message rõ.
- `image_hash` dạng `sha256:<hex>`.
- Nếu Gemini trả box/point lệch, server chuẩn hoá/transpose.
- Nếu prompt có "mưa/rain" → plan item rain; "khói/steam" → steam; "gió/wind" → wind; "nước/sóng/water" → water. P0 renderer chỉ dựng rain; các item khác vẫn lưu plan.

## `render_videos` signature

```python
def render_videos(
    *,
    ...,
    background_effects: list[dict] | None = None,
) -> tuple[Path, Path]:
```

- `background_effects` map theo `image_path`.
- Nếu item có plan rain: gọi `karaoke.living.render_rain_loop(...)` để tạo loop dài
  đúng `image_duration`; dùng loop trong slideshow (`-stream_loop -1 -t ... -i clip`).
- Không có plan/effect → ảnh tĩnh như cũ.
- Cover vẫn tĩnh.
- Giữ “một picture, hai mux”.

## `karaoke.living`

```python
def render_rain_loop(
    image_path: Path,
    plan: dict,
    destination: Path,
    *,
    duration: float,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    log=None,
) -> Path | None:
    ...
```

- Trả `None` nếu plan không có rain.
- Loop quantization: `speed * duration = k * span`.
- Mask: `dry` polygon + `protect` boxes. Lyric strip **không** mask effect vì effect
  render trước lyric overlay; text/bar được vẽ sau.
- Cache: `cache_root("living")/<sha256>.mp4` (platform cache, override bằng
  `KARAOKE_CACHE_DIR`); hash gồm image/plan/duration/size/mode/strip.
- Encode: libx264, yuv420p, faststart.

## UI `AiEffects`

Props:

```ts
{
  images: string[];
  effects: BackgroundEffect[];
  outputProfile: string;
  onEffectsChange: (updater: (current: BackgroundEffect[]) => BackgroundEffect[]) => void;
}
```

- Vị trí: sau section **Hình** trong `project-setup.tsx`.
- `prompt` và `consent` là **draft state bên trong `AiEffects`**, không lưu top-level
  trong project. Khi bấm "Áp dụng", chúng được ghi vào `BackgroundEffect` item
  (`effect_prompt` + `effect_consent_receipt`).
- Preview dùng `/api/images/inspect` field `preview` ~1280 (fallback thumbnail).
- Overlay SVG normalized 0..1.
- Nút "Phân tích ảnh"; trạng thái loading/error/cached/usage.
- "Áp dụng" tạo/cập nhật item trong `effects` với plan từ API.
- Consent mặc định false; nếu false, không gọi API.

## Test commands

```bash
.venv-karaoke/bin/python -m unittest tests.test_director tests.test_project_effects tests.test_living
.venv-karaoke/bin/python -m unittest tests.test_karaoke
cd web && npx tsc --noEmit
```

- Không gọi Gemini thật trong unit test; mock `requests.post`.
- Test render dùng ảnh synthetic 640×360, 1 s, 15 fps.

# Nền động 2.5D từ một ảnh tĩnh

Tài liệu này đề xuất cách đưa hiệu ứng "living still / 2.5D diorama" kiểu
[Shinya Ramen](https://ashutoshai.com/showcase/d/shinya-ramen) vào KaraokeMaker,
biến ảnh nền tĩnh thành một đoạn nền động ngắn rồi ghép vào video karaoke như hiện nay.

## Kết luận ngắn

- Showcase **không dùng AI video generation**. Nó là ảnh tĩnh + depth map + các lớp
  cắt + shader WebGL2 chạy realtime. Model AI duy nhất trong pipeline hình ảnh là
  **Depth Anything V2**, chạy local trên Apple Silicon.
- "Gemini 4 Neon" trên trang đó là model được dùng để **viết ra scene/code**, không
  phải model chạy lúc render. Không cần API Google, không cần key, không upload ảnh.
- Cách phù hợp với KaraokeMaker là **bake (nướng sẵn) một loop 2.5D ngắn** bằng
  Python + Depth Anything V2 Small + OpenCV, cache lại, rồi để pipeline ffmpeg hiện
  tại loop clip đó thay cho ảnh tĩnh.
- Trên M5 của bạn, prototype cho kết quả: depth ~0,21 s/ảnh, loop 8 s ở 1080p30
  render + encode ~2,6 s, file ~1,4 MB. Hoàn toàn khả thi để chạy trong job xuất video.
- Model nên dùng là **Depth-Anything-V2-Small** (Apache-2.0, dùng thương mại được).
  Bản Large mà showcase dùng là CC-BY-NC-4.0, **không dùng cho sản phẩm thương mại**.
- Mưa, khói, đèn lồng, rèm, neon **không phải là output của model depth**. Chúng là
  particle system + mask vùng + pivot/cloth kinematics. Demo v2 đã dựng được các hiệu
  ứng này cho một ảnh; để chạy với ảnh bất kỳ cần **scene anchors** (người dùng đánh
  dấu vùng/điểm) hoặc segmentation tự động.
- Tiếng mưa có thể tổng hợp được, nhưng với karaoke nên **tắt mặc định** vì sẽ lẫn
  với nhạc; chỉ nên là ambience rất nhỏ, tuỳ chọn.

## 1. Showcase thực sự làm gì

Theo mô tả kỹ thuật trên trang:

| Bước | Công nghệ | Ghi chú |
|---|---|---|
| Ảnh gốc | 1 JPEG 1376×768 | Không có mesh 3D, không video clip |
| Depth | Depth-Anything-V2-Large, local MPS | Model open source của HKU + TikTok |
| Cắt lớp | Python OpenCV GrabCut | Tách người, rèm, đèn lồng thành các card |
| Lấp nền | OpenCV inpainting | Vẽ lại tường/brick bị che để camera động không lộ lỗ |
| Mask hiệu ứng | OpenCV/NumPy HSV, Canny, Black-Hat | Mưa, vũng nước, neon, vùng khô |
| Render | Three.js r187, WebGL2, GLSL | 11 shader: thở, chớp mắt, tóc, hơi nước, mưa, nước, bloom, heat haze |
| Âm thanh | Web Audio API | Tổng hợp, không file audio |
| Tương tác | Chuột/bàn phím | Parallax, kéo rèm, chạm đèn, splash |

Điểm quan trọng: đây là **parallax theo depth + layer card + shader**, không phải
model sinh video. Vì vậy mặt người và chữ không bị "tan chảy" như video AI.

### 1.1 Vì sao depth model không tự tạo ra mưa, khói, đèn lồng

Depth Anything chỉ trả về **một bản đồ độ sâu cho mỗi ảnh**. Nó không biết cái gì là
bát mỳ, đèn lồng, rèm, hay chỗ nào được phép mưa. Các hiệu ứng trong showcase đến từ
bốn hệ thống riêng, không phải "handle" của model:

| Hiệu ứng | Công nghệ thật sự | Input cần có |
|---|---|---|
| Mưa | Particle system + shader + mask vùng mưa | Mask vùng ngoài trời / vùng khô |
| Nước, vũng gợn | Shallow-water PDE + reflection | Mask mặt đường ướt |
| Khói bát mỳ | Curl-noise particle / shader | Toạ độ miệng bát |
| Đèn lồng đung đưa | Pendulum kinematics + cutout | Mask đèn + điểm treo |
| Rèm bay | Cloth shader + spring | Mask từng panel + mép trên cố định |
| Neon nhấp nháy | Emissive shader + bloom + flicker | Mask vùng sáng |
| Nhân vật thở/chớp mắt | Live2D rig 128×72 + shader | Rig riêng cho nhân vật |
| Tiếng mưa | Web Audio tổng hợp | Không cần file, nhưng cần mix |

Vì vậy "dùng hết handle của model" không đủ. Cái còn thiếu là **scene anchors**
(vùng/điểm neo) và **các hệ mô phỏng** đi kèm.

## 2. Các hướng có thể tích hợp

### A. Bake loop 2.5D offline (đề xuất)

```
ảnh tĩnh
  → fit 1920×1080 + overscan
  → Depth Anything V2 Small (MPS/CPU) → depth map (cache)
  → camera path + depth parallax (+ layer card, inpaint, hiệu ứng)
  → 8 s @ 30 fps → ffmpeg → bg-XX.mp4 (cache)
  → slideshow loop clip → overlay lời → mux audio
```

Ưu điểm:

- Hợp với kiến trúc Python/ffmpeg hiện tại, không thêm Node/Chromium.
- Deterministic: cùng input cho cùng output, dễ kiểm tra, dễ cache.
- Chạy local, không tốn phí API, không upload ảnh.
- Chỉ cần render một loop ngắn, không render toàn bộ 4–5 phút.
- Có thể tắt/fallback về ảnh tĩnh nếu thiếu model hoặc lỗi.

Nhược điểm:

- Không có tương tác realtime như showcase.
- Hiệu ứng mưa/nước/neon phải tự viết bằng OpenCV/NumPy hoặc shader offline,
  không "miễn phí" như WebGL.

### B. Headless WebGL, clone gần đúng showcase

Dựng scene Three.js, chạy Chromium headless (Playwright), đặt thời gian cố định,
render từng frame rồi pipe sang ffmpeg.

Ưu điểm: đúng chất showcase, có đủ shader đẹp.

Nhược điểm:

- Thêm Chromium (~150–300 MB) và Node vào bộ cài, trong khi app đang chỉ cần Python.
- Capture từng frame chậm hơn nhiều so với OpenCV; khó deterministic nếu không
  can thiệp sâu vào vòng lặp render.
- Phải viết và bảo trì song song một scene WebGL nữa.
- Không cần thiết cho nền karaoke, vốn chỉ cần chuyển động nhẹ.

Đề xuất: để dành cho giai đoạn 3 nếu bạn muốn hiệu ứng "wow" thật sự.

### C. AI video generation (Veo / Kling / Runway / SVD)

Không đề xuất.

- Clip thường 5–10 s, không loop liền mạch cho bài 4–5 phút.
- Artifact: mặt, chữ, logo bị biến dạng; đúng thứ showcase cố tránh.
- Tốn phí mỗi lần xuất, phụ thuộc mạng, upload ảnh lên cloud.
- Không deterministic, khó cache và khó tái lập.
- Chỉ phù hợp nếu mục tiêu là "b-roll" riêng, không phải nền karaoke.

### D. Ken Burns thuần ffmpeg (fallback không cần AI)

Chỉ zoom/pan chậm trên ảnh. Không phải 2.5D nhưng zero dependency, zero model,
làm được ngay bằng `zoompan`. Nên giữ làm lựa chọn dự phòng khi model chưa tải
hoặc máy quá yếu.

### Giới hạn thực tế cần biết

Showcase được làm **thủ công cho đúng một ảnh**: 7 mask ngữ nghĩa, 6 panel rèm,
rig mặt 128×72, 11 shader riêng. Với ảnh nền bất kỳ trong karaoke, không có cách
tự động biết chỗ nào nên mưa, chỗ nào là vũng nước, chỗ nào là neon.

Vì vậy nên đặt kỳ vọng:

- **Làm được tự động, chất lượng tốt**: parallax theo depth, camera drift, nhịp sáng
  nhẹ, hạt bụi/ánh sáng, layer card + inpaint.
- **Cần mask thủ công hoặc segmentation**: mưa chỉ ngoài hiên, nước chỉ trên đường,
  neon chỉ trên biển hiệu, tách đúng nhân vật.
- **Cần rig riêng**: nhân vật chớp mắt, thở, tóc bay như showcase. Không nên làm
  generic cho mọi ảnh.
- **Khác bản chất**: showcase render WebGL realtime; ta bake ra MP4 để ghép vào video
  karaoke. Hiệu ứng hình ảnh tương đương về ý tưởng, không phải bản sao 1:1.

### So sánh nhanh

| Tiêu chí | A. Offline 2.5D | B. Headless WebGL | C. AI video | D. Ken Burns |
|---|---|---|---|---|
| Đúng chất showcase | Gần | Rất gần | Khác | Không |
| Chạy local | Có | Có | Không | Có |
| Thêm dependency | Vừa | Nặng | API | Không |
| Thời gian xuất | Thấp | Cao | Rất cao | Rất thấp |
| Loop liền mạch | Có | Có | Khó | Có |
| Rủi ro biến dạng mặt/chữ | Thấp | Thấp | Cao | Không |
| Chi phí | 0 | 0 | Theo request | 0 |

## 3. Kiến trúc đề xuất

### 3.1 Module mới `karaoke/living.py`

```python
# Không phải code cuối cùng, chỉ là chữ ký đề xuất.

def estimate_depth(image: Path, *, device: str = "auto") -> np.ndarray:
    """Depth map float32 0..1, cache theo SHA-256 của ảnh + tên model."""

def build_living_loop(
    image: Path,
    destination: Path,
    *,
    duration: float,
    preset: str = "living",
    strength: float = 0.5,
    fps: int = 30,
    size: tuple[int, int] = (1920, 1080),
    log,
) -> None:
    """Ghi một mp4 loop liền mạch, đúng duration, 1920×1080, yuv420p."""

def prepare_motion_clips(
    images: list[Path],
    *,
    image_duration: float,
    settings: dict,
    work: Path,
    log,
) -> list[Path]:
    """Trả về danh sách clip (mp4) hoặc ảnh jpg nếu tắt/lỗi."""
```

### 3.2 Pipeline

1. **Fit khung + overscan**
   - Dùng `fit_image()` hiện tại để giữ nguyên hành vi (ảnh không 16:9 nằm giữa,
     viền tối).
   - Scale thêm ~1.08× rồi crop giữa để có biên cho camera di chuyển, tránh lộ mép.
   - Với ảnh không 16:9, có thể thay viền đen bằng bản blur cover phía sau
     (tuỳ chọn ở giai đoạn 2).

2. **Depth map**
   - `Depth-Anything-V2-Small-hf` qua `transformers`.
   - Suy luận ở 518×518 (hoặc 644 nếu muốn chi tiết hơn), upscale về kích thước khung.
   - Làm mượt depth bằng Gaussian/bilateral (sigma lớn) để **không warp từng nét
     chữ, từng sợi tóc**. Chỉ giữ hình khối lớn.
   - Cache `depth.npy` theo hash ảnh, để đổi preset/strength không phải chạy lại model.

3. **Camera path + parallax**
   - Chuyển động tuần hoàn để loop kín:
     ```text
     pan_x = A  * sin(2πt/T)
     pan_y = A2 * sin(4πt/T + φ)
     depth_x = K  * (depth - 0.5)
     depth_y = K2 * (depth - 0.5)
     ```
   - `T` = đúng `image_duration` (hoặc bội số) để frame cuối nối frame đầu.
   - Biên độ mặc định nhỏ: global ±10–12 px, depth ±15–22 px ở 1080p.
   - Giảm biên độ ở 280 px cuối khung (vùng lời) để chữ dễ đọc.

4. **Layer card (giai đoạn 2)**
   - Chia depth thành 3–5 dải, feather mép, mỗi dải là một RGBA card.
   - Inpaint vùng bị che bằng `cv2.inpaint` (Telea/NS) để khi card trước dịch sang
     bên không lộ lỗ.
   - Card gần (người/vật chính) dịch **cứng** theo một offset, không warp từng pixel
     → mặt không bị méo. Đây là điểm quan trọng nhất để giống showcase.

5. **Hiệu ứng (tuỳ chọn, giai đoạn 2)**
   - `living`: parallax + nhịp sáng rất nhẹ (0,98–1,02) + hạt bụi mờ.
   - `neon`: bloom trên pixel sáng + flicker rất nhẹ.
   - `rain`: hạt mưa procedural + gợn nước ở vùng thấp.
   - `steam`: hơi nước cong nhẹ ở vùng sáng.
   - Không nên bật quá nhiều cho karaoke; mặc định chỉ `living`.

6. **Encode**
   - Pipe raw BGR/RGB vào ffmpeg `libx264`, `-crf 16..18`, `yuv420p`, 30 fps,
     `+faststart`.
   - Clip ngắn 8 s, cache lại; slideshow hiện tại loop bằng `-stream_loop -1`.

### 3.3 Cache

Đề xuất cache ngoài thư mục xuất, để không làm bẩn thư mục nhạc:

```text
~/Library/Caches/KaraokeMaker/
  depth/<sha256>_small.npy
  living/<sha256>_<preset>_<strength>_<duration>_v1.mp4
```

- Key gồm hash ảnh, preset, strength, duration, phiên bản pipeline.
- Có thể xoá cache an toàn; lần sau tự dựng lại.
- Nếu muốn dự án "mang đi" được, có thể thêm tuỳ chọn lưu cache cạnh file dự án;
  mặc định không nên.

### 3.4 Model

- Tải một lần từ Hugging Face: `depth-anything/Depth-Anything-V2-Small-hf` (~95 MB).
- Vì depth được làm mượt mạnh để tránh warp, Small cho kết quả gần như tương đương
  Large trong bài toán parallax; không cần đánh đổi license để dùng Large.
- Lưu vào `~/Library/Application Support/KaraokeMaker/models/` hoặc thư mục
  `models/` sẵn có; cho phép override bằng biến môi trường
  `KARAOKE_DEPTH_MODEL=/duong/dan/model`.
- Nếu chưa có model / không có mạng / thiếu `transformers`: log cảnh báo rõ ràng và
  **fallback về ảnh tĩnh**, không làm hỏng job xuất video.
- Load model một lần trong process, có `threading.Lock` vì job chạy trong thread.
- Mặc định `mps` nếu có, fallback `cpu`; có thể thêm lựa chọn trong UI sau.

### 3.5 Scene anchors — phần bắt buộc để có hiệu ứng như showcase

Để mưa rơi đúng chỗ, khói bốc đúng bát, đèn lồng đung đưa đúng điểm treo, cần lưu
**neo cảnh** theo toạ độ chuẩn hoá (0..1) trong file dự án:

```json
{
  "scene_anchors": {
    "rain_polygons": [[[0.49, 0], [1, 0], [1, 1], [0.49, 1]]],
    "dry_polygons": [[[0, 0], [0.49, 0], [0.49, 1], [0, 1]]],
    "steam_points": [{"x": 0.30, "y": 0.73, "scale": 1.0}],
    "sway_objects": [
      {"mask": "lantern-1", "pivot": [0.56, 0.29], "amplitude": 2.2}
    ],
    "cloth_panels": [
      {"polygon": [[0.15, 0], [0.52, 0], [0.52, 0.37], [0.15, 0.30]], "pinned": "top"}
    ],
    "glow_masks": ["neon-1", "neon-2"]
  }
}
```

Có ba cách tạo anchors:

1. **Thủ công (chất lượng cao, làm được ngay)**: người dùng vẽ polygon/mask và click
   điểm neo trong một editor nhỏ. Đây là cách showcase được làm.
2. **Bán tự động**: gợi ý mask bằng heuristic (màu đỏ cho đèn lồng, vùng tối trên cao
   cho rèm, vùng sáng ấm cho bát) rồi người dùng sửa.
3. **Tự động bằng segmentation**: SAM / CLIPSeg / GroundingDINO với prompt
   "lantern", "curtain", "bowl", "rain area". Nặng, thêm model, và với ảnh anime/ảnh
   vẽ độ chính xác không cao. Để giai đoạn 3.

Không có anchors thì chỉ nên bật các hiệu ứng **không cần ngữ nghĩa**:

- parallax toàn cảnh;
- nhịp sáng ấm toàn khung;
- bloom + flicker trên pixel sáng (neon);
- hạt bụi / tia sáng;
- mưa với mask suy ra từ depth (chỉ nơi xa) — chấp nhận có thể sai ở ảnh khác;
- rung nhẹ toàn khung.

## 4. Tích hợp vào code hiện tại

### 4.1 `requirements-karaoke.txt`

Thêm:

```text
transformers>=4.45
opencv-python-headless>=4.10
```

`safetensors`, `huggingface-hub`, `tokenizers` sẽ được kéo theo. Không cần
`torchvision` (đã kiểm tra với transformers 5.23 + torch 2.14). `numpy` đã có.

### 4.2 `karaoke/media.py`

Giữ `prepare_slide()` cho ảnh tĩnh. Thêm hàm tạo khung overscan và hàm
`prepare_slide_motion()` hoặc để toàn bộ trong `living.py`.

### 4.3 `karaoke/render.py`

Điểm sửa chính: slideshow hiện tại chỉ nhận ảnh.

```python
clips: list[tuple[Path, float]] = [(cover_card, cover_duration)]
...
slide_command = ["ffmpeg", "-y"]
for clip, duration in clips:
    if clip.suffix.lower() in {".mp4", ".mov"}:
        slide_command += [
            "-stream_loop", "-1",
            "-t", f"{duration:.3f}",
            "-i", str(clip),
        ]
    else:
        slide_command += [
            "-loop", "1",
            "-t", f"{duration:.3f}",
            "-i", str(clip),
        ]
```

Phần filter `fps=30,format=yuv420p,setpts=PTS-STARTPTS` và `concat` giữ nguyên,
vì clip động được render đúng 1920×1080, 30 fps, yuv420p.

Lưu ý:

- `-stream_loop -1` phải đứng **trước** `-i`.
- Nếu `image_duration` = độ dài loop thì mỗi lần chuyển ảnh là một vòng kín, không
  thấy điểm nối.
- Nếu một ảnh lỗi, dùng ảnh tĩnh cho ảnh đó và log cảnh báo.

### 4.4 `karaoke/pipeline.py` + `karaoke/project.py`

Thêm các trường (mặc định tắt để tương thích dự án cũ):

```json
{
  "motion_enabled": false,
  "motion_preset": "living",
  "motion_strength": 0.5
}
```

- `prepare_job()`: validate preset/strength.
- `render_job()`: truyền settings xuống `render_videos()`.
- `sync_project_file()` / `open_project_file()`: đọc/ghi, dự án cũ thiếu trường thì
  dùng mặc định.

### 4.5 `karaoke/server.py`

- Thêm trường vào `ProjectFieldsRequest` và `OpenSavedRequest`.
- Thêm vào danh sách key của `/api/projects/session`.
- (Giai đoạn 2) Endpoint preview:
  - `POST /api/images/motion-preview` body `{path, preset, strength}`.
  - Trả về mp4 3–4 s, 640×360, silent; cache theo hash.
  - UI hiện `<video autoplay loop muted playsInline>`.
  - Lần đầu có thể mất ~6–10 s vì load model; các lần sau <1 s.

### 4.6 Web

- `web/src/lib/types.ts`: thêm 3 trường vào `Project`, `ProjectFields`,
  `emptyFields()`, `fieldsFromProject()`, `toPayload()`.
- `web/src/components/project-setup.tsx`: thêm block "Nền động 2.5D":
  - bật/tắt;
  - preset (Living / Neon / Mưa / Tắt);
  - độ mạnh (Nhẹ / Vừa / Mạnh);
  - ghi chú "ảnh sẽ được xử lý local, không upload".
- `web/src/components/image-cards.tsx`: badge "2.5D" cho ảnh đã bật.
- (Giai đoạn 2) nút "Xem thử nền động" mở modal video.

### 4.7 `README.md`

Thêm mục ngắn: model tải lần đầu, dung lượng, cache, fallback, license, và cách tắt
nếu không muốn dùng AI local.

## 5. Thông số mặc định đề xuất

| Thông số | Giá trị |
|---|---|
| Model | Depth-Anything-V2-Small-hf |
| Input depth | 518×518, upscale về khung |
| Depth smoothing | Gaussian sigma ~20–28 px ở 1080p |
| Loop | đúng `image_duration`, 30 fps |
| Global pan | ±10–12 px |
| Depth parallax | ±15–22 px |
| Vùng lời (280 px cuối) | giảm 50% biên độ |
| Preset mặc định | `living` |
| Độ mạnh mặc định | `0.5` (Nhẹ) |
| Encode | libx264, CRF 17, yuv420p, faststart |
| Cache | ngoài thư mục xuất |

Tiêu chí chất lượng:

- Không thấy điểm nối khi loop.
- Không lộ mép/vùng trống khi camera dịch.
- Mặt người không bị kéo giãn rõ rệt.
- Chữ/sign trong ảnh không bị "thở" hoặc nhấp nháy.
- Nền chuyển động đủ nhận ra nhưng không tranh mắt với lời.

## 6. Kết quả prototype trên máy này

Môi trường: Apple M5, 24 GB RAM, macOS, `.venv-karaoke` với torch 2.14.1 (MPS),
transformers 5.23.0, OpenCV 5.1.0. Ảnh thử: poster 1920×1080 của chính showcase.

| Việc | Kết quả |
|---|---|
| Load model lần đầu (đã có cache) | ~5,7 s |
| Dung lượng model | ~95 MB |
| Depth inference, MPS, sau warm-up | ~0,21 s/ảnh |
| Depth inference, CPU | ~0,83 s/ảnh |
| Render + encode 240 frame (8 s, 1080p30) | ~2,6 s |
| File loop 8 s | ~1,4 MB |

Prototype v1 chỉ có per-pixel depth parallax + Gaussian smoothing + overscan, **cố ý
chưa có hiệu ứng** để đo riêng chi phí depth/parallax. Nó không phải là giới hạn của
model; mưa/khói/đèn lồng/rèm là các hệ procedural ở trên.

File thử v1: `/tmp/demo25d/living_demo.mp4` (tạm thời, có thể bị xoá).

### 6.1 Demo v2 — có mưa, khói, đèn lồng, rèm, neon

Mình dựng thêm một bản v2 cho đúng ảnh poster để chứng minh các hiệu ứng đó làm được
mà không cần thêm model AI nào:

- **Mưa**: 3 lớp particle (490 hạt) + splash trên mặt đường. Mask mưa suy ra từ
  depth (`depth < 0.43`) và vùng bên phải, nên mưa **không rơi vào quán và không rơi
  lên người**. Đây là ví dụ depth được dùng như một "handle" hữu ích, nhưng nó chỉ
  giới hạn vùng, không tạo ra hạt mưa.
- **Khói**: 2 nguồn neo (bát mỳ, nồi nước) + particle curl-noise, sprite mờ, cộng sáng.
- **Đèn lồng**: mask HSV đỏ/cam + pivot ở đầu đèn + xoay con lắc ±2–3° (local warp,
  không cần inpaint).
- **Rèm**: polygon mềm + mép trên cố định + sóng 9 px ở mép dưới.
- **Neon**: mask màu cyan/tím + bloom + flicker không đồng bộ.
- **Ánh sáng ấm**: pulse rất nhẹ (1,2%) để quán "thở".

File thử v2: `/tmp/demo25d/living_v2.mp4` (8 s, 1080p30, ~5,8 MB).
Thời gian render v2: ~51 s cho 240 frame (do particle Python loop); bản production sẽ
tối ưu bằng vector hoá/precomputed texture, mục tiêu 5–10 s.

**Quan trọng**: v2 dùng anchors **hard-code cho đúng ảnh poster này**. Với ảnh nền
bất kỳ, muốn đạt chất lượng tương tự phải có bước chọn anchors (thủ công hoặc
segmentation). Nếu không, chỉ nên bật các hiệu ứng không cần ngữ nghĩa.

## 7. Giấy phép

- **Depth-Anything-V2-Small**: Apache-2.0 → dùng thương mại được.
- **Depth-Anything-V2-Base/Large/Giant**: CC-BY-NC-4.0 → **không** dùng cho sản phẩm
  thương mại. Showcase dùng Large vì là demo cá nhân.
- **Three.js**: MIT (chỉ cần nếu đi hướng B).
- **OpenCV**: Apache-2.0.
- **Transformers**: Apache-2.0.
- Không dùng API Google, không upload ảnh, không phát sinh chi phí.

Nếu sau này muốn dùng Large cho chất lượng cao, cần thêm cảnh báo license trong UI
và chỉ cho phép ở chế độ phi thương mại.

## 8. Kế hoạch theo giai đoạn

### Giai đoạn 1 — MVP "Living Still" (khoảng 2–3 ngày)

- [ ] `karaoke/living.py`: depth + cache + loop parallax + encode.
- [ ] `requirements-karaoke.txt`: thêm transformers, opencv-headless.
- [ ] `render.py`: slideshow nhận clip động, fallback ảnh tĩnh.
- [ ] `pipeline.py` / `project.py` / `server.py`: 3 trường cấu hình.
- [ ] Web: toggle + preset + strength.
- [ ] README + license note.
- [ ] Test với ảnh 16:9, ảnh vuông, ảnh có người, ảnh chữ nhiều.

**Điều kiện nghiệm thu**: bật nền động, xuất 2 video; nền chuyển động nhẹ, loop
không thấy mối nối; tắt thì hành vi y như cũ; thiếu model thì vẫn xuất được bằng ảnh
tĩnh.

### Giai đoạn 2A — Hiệu ứng không cần anchors (khoảng 2–3 ngày)

- [ ] Bloom + flicker trên pixel sáng (neon) — tự động.
- [ ] Mưa với mask suy ra từ depth (vùng xa) — tự động, có thể sai ở ảnh khác.
- [ ] Hạt bụi / tia sáng / heat haze — tự động.
- [ ] Nhịp sáng ấm toàn khung — tự động.
- [ ] Preset `living`, `neon`, `rain` mức nhẹ.
- [ ] Preview endpoint + modal video.
- [ ] Crossfade 0,4–0,6 s giữa các ảnh nền (tuỳ chọn).
- [ ] Animate cover bằng zoom chậm (không parallax vì đã có chữ).

### Giai đoạn 2B — Scene anchors + hiệu ứng có ngữ nghĩa (khoảng 3–5 ngày)

- [ ] Editor nhỏ: vẽ polygon vùng mưa/vùng khô, click điểm khói, mask + pivot đèn
      lồng, polygon rèm.
- [ ] Lưu anchors vào `.karaoke.json`, có thể tái dùng khi mở lại dự án.
- [ ] Steam particle theo anchor.
- [ ] Đèn lồng xoay quanh pivot (local warp hoặc cutout + inpaint).
- [ ] Rèm sóng theo panel, mép trên cố định.
- [ ] Chia 3–5 lớp theo depth, feather, inpaint nền bị che.
- [ ] Card tiền cảnh dịch cứng để bảo vệ mặt.
- [ ] Preset `steam`, `lantern`, `curtain`.
- [ ] (Tuỳ chọn) ambience mưa tổng hợp, mix rất thấp, mặc định tắt.

### Giai đoạn 3 — Nâng cao (tuỳ chọn)

- [ ] Tự động gợi ý anchors bằng segmentation (SAM / CLIPSeg / GroundingDINO) từ
      prompt "lantern", "curtain", "bowl", "rain area"; người dùng vẫn sửa được.
- [ ] WebGL/Three.js live preview trong editor.
- [ ] Headless capture cho hiệu ứng shader đầy đủ.
- [ ] Nền phản ứng theo nhịp nhạc (onset detection từ instrumental).
- [ ] Ambience mưa/gió tổng hợp bằng Web Audio hoặc ffmpeg, mix dưới -30 dB, tắt mặc định.
- [ ] Cho phép chỉnh camera path / keyframe theo từng ảnh.
- [ ] Ước lượng depth bằng Core ML để tiết kiệm điện hơn.

## 9. Rủi ro và cách giảm

| Rủi ro | Cách giảm |
|---|---|
| Mặt người bị warp | Smooth depth mạnh, biên độ nhỏ, layer card cứng ở giai đoạn 2 |
| Chữ/sign bị nhấp nháy | Không dùng depth chi tiết; smooth; giảm biên độ vùng chữ |
| Lộ mép khi pan | Overscan 1.06–1.10× + border reflect |
| Lộ lỗ sau vật thể | Inpaint + layer chồng mép + biên độ nhỏ |
| Loop bị giật | Hàm tuần hoàn đúng chu kỳ; duration loop = thời lượng hiển thị |
| Model tải chậm/lỗi | Cache, log, fallback ảnh tĩnh, cho phép đường dẫn local |
| Xuất lâu hơn | Cache theo hash; chỉ render loop ngắn; render song song từng ảnh nếu cần |
| Tốn đĩa | Cache có thể xoá; dọn theo LRU nếu vượt hạn mức |
| Job chạy song song | Lock quanh model + cache ghi atomic (tmp rồi rename) |
| Ảnh không 16:9 | Giữ viền tối như hiện tại, hoặc blur-cover fill ở giai đoạn 2 |

## 10. Câu hỏi cần chốt trước khi code

1. Bạn muốn mặc định **bật** nền động cho dự án mới, hay để người dùng tự bật?
2. Có cần hiệu ứng mưa/neon/hơi nước không, hay chỉ cần parallax nhẹ?
3. Nếu cần mưa/khói/đèn lồng/rèm: bạn chấp nhận **đánh dấu anchors** trên ảnh một
   lần cho mỗi ảnh (chất lượng cao), hay muốn tự động hoàn toàn (chấp nhận sai)?
4. Có muốn **tiếng mưa/gió** không? Mình khuyên mặc định tắt vì sẽ lẫn với nhạc.
5. Có cần nút xem thử ngay trong màn hình khai báo không?
6. Video xuất của bạn chủ yếu là ảnh phong cảnh, hay có người/portrait?
7. Có ràng buộc thời gian xuất (ví dụ thêm tối đa 1 phút cho mỗi bài) không?
8. Dự án có dùng thương mại không? (ảnh hưởng việc có được phép chọn model Large)

# Đề xuất: tăng tốc xuất nền & làm gọn luồng Studio → Karaoke

Trạng thái: **Đã triển khai (P0.1–P0.5, P1, P2)**
Ngày: 2026-11-21
Phạm vi: pipeline xuất video Karaoke sau PR "background trước, lời sau"
Liên quan:

- `karaoke/background.py`
- `karaoke/living.py`
- `karaoke/render.py`
- `karaoke/server.py`
- `web/src/components/maker-form.tsx`
- `web/src/components/project-setup.tsx`
- `web/src/components/ai-effects.tsx`
- `web/src/components/studio-workspace.tsx`
- `web/src/components/phase-stepper.tsx`

---

## 0. Tóm tắt

Sau PR vừa rồi, pipeline đã đúng thứ tự **hiệu ứng → nền → lời → tiếng** và đã tái sử dụng
được `background.mp4`. Tuy nhiên còn hai vấn đề:

1. **Xuất nền còn chậm**, chủ yếu do sinh frame hiệu ứng trong Python, encode lại
   toàn bộ nền dù các clip đầu vào đã đúng chuẩn, và decode lại ảnh tĩnh mỗi frame.
2. **Luồng thao tác còn cồng kềnh**, đặc biệt là đoạn tạo hiệu ứng AI → render preview →
   lưu asset → chọn lại asset trong dự án Karaoke.

Đề xuất chia thành 2 nhóm, 3 mức ưu tiên:

- **P0 – Quick wins tốc độ**: không đổi hành vi, rủi ro thấp.
- **P1 – Tăng tốc thêm**: render song song, picture cache, xuất nháp.
- **P2 – Làm gọn luồng**: giảm số màn hình/bước thao tác, gộp Studio vào dự án.

---

## 1. Hiện trạng & nơi thời gian thực sự bị mất

### 1.1. Dựng clip hiệu ứng là khâu chậm nhất

`karaoke/living.py::_render_loop` sinh từng frame bằng NumPy/PIL rồi đẩy qua FFmpeg.

Đo nhanh trên máy phát triển (**Apple M5, 10 core, macOS arm64, FFmpeg 9.0.2**,
x264 `veryfast`) với clip mưa **8 giây @ 1920×1080, 30fps**:

| Hạng mục | Thời gian |
|---|---|
| Tổng thời gian render 1 clip | **~21s** |
| Sinh frame trong Python | **~19,8s** |
| Encode x264 | ~1s |
| Riêng `GaussianBlur` | **~77% thời gian sinh frame** |

Profile cho thấy 3 layer mưa đều blur full-frame RGB. Đo riêng blur 1080p radius 0.8:

| Kiểu ảnh | Thời gian blur |
|---|---|
| `RGB` | ~21,6ms/lần |
| `L` (grayscale) | ~8,5ms/lần |

Với 8 ảnh nền có hiệu ứng, riêng khâu này đã mất khoảng **2,5–3 phút**.

Lưu ý: blur nhanh 2,55 lần **không** đồng nghĩa clip nhanh 2,55 lần, vì còn phần sinh
frame khác và encode. Tốc độ tổng của clip chỉ khoảng **1,8x** (xem P0.3).

### 1.2. Ghép background đang decode + encode lại toàn bộ

`karaoke/background.py::_assemble_background` (khoảng dòng 488) dựng `filter_complex`
gồm `fps, scale, format, trim, concat` cho từng slot rồi encode lại CRF 18.

Với slot motion/asset, các clip đầu vào đã đúng width/height/fps/pix_fmt và đã frame-exact.

Đo thử bài **4 phút @ 1920×1080, 30fps**. Có hai loại slot cần tách riêng:

| Trường hợp | Hiện tại | Sau P0.2 |
|---|---|---|
| 30 slot motion/asset đã encode (filter + encode lại) | 17,1s | ~0,2s (concat copy) |
| 1 ảnh tĩnh, 1 slot 7200 frame | **~41s** | **~9s** (decode 1 lần + loop filter) |
| 1 ảnh tĩnh lặp 30 slot 8s | ~41s | ~0,6s (1 segment 8s + concat) |
| 8 ảnh tĩnh unique lặp 30 slot | ~41s | ~3–5s (8 segment 8s + concat) |

Lý do ảnh tĩnh đắt: `_assemble_background` dùng `-loop 1 -framerate fps -i slide.jpg`
(`background.py:503`), và image2 demuxer **decode lại JPEG mỗi frame**. Đo riêng decode-only
7200 frame đã ~36s trên máy này. Đây là chi phí ẩn của dự án toàn ảnh tĩnh, tách biệt với
concat copy.

Con số **17,1s → 0,2s** chỉ áp dụng khi các slot đã là clip encode sẵn (motion hoặc asset
Studio). Với ảnh tĩnh, win lớn nhất đến từ **decode ảnh một lần rồi clone frame** (xem P0.2),
sau đó mới concat. Cách copy giảm một thế hệ nén ở khâu nền (picture cuối vẫn encode CRF 20)
và không bị giới hạn số input khi `image_duration` ngắn.

### 1.3. Overlay lời + mux

`karaoke/background.py::render_picture` gọi `karaoke/render.py::_overlay_text`, vẫn phải
encode toàn bài một lần. Đo riêng encode x264 ~16s cho 4 phút; tổng stage picture (vẽ text
+ overlay + encode) thực tế thường ~30s (log mẫu ở P0.1 là ví dụ minh hoạ).

Hai lần `_mux` đang chạy tuần tự nhưng dùng `-c:v copy`, nên chỉ tốn vài giây. Chạy song
song chỉ tiết kiệm ~2–4s và thêm phức tạp xử lý lỗi ghi file, nên để P2 (không nằm trong P0).

Phần này khó tránh nếu vẫn burn chữ vào video.

### 1.4. Chi phí ẩn khi poll trạng thái

`karaoke/server.py::_public_job` (khoảng dòng 753) gọi `background_status` mỗi lần UI poll
1 giây. Hàm này chạy:

- `probe_duration` 2 lần (`background.py`, khoảng dòng 570)
- `_validate_background` 1 lần ffprobe

Tức **~3 subprocess/giây** trong lúc đang render. Không làm chậm encode nhiều nhưng làm
UI/CPU nặng và tạo cảm giác “ì”.

### 1.5. Cache clip có thể bị dọn giữa các lần xuất

Cache `living/` giới hạn 40 file / 2GB. Với dự án nhiều ảnh, clip có thể bị prune, lần sau
lại phải render ~20s/ảnh. Đây có thể là lý do “lúc nhanh lúc chậm” mà không rõ nguyên nhân.

### 1.6. Vì sao luồng cồng kềnh

- **5 phase + Home**: Khai báo → Tách/trộn → Ghép chữ → Biên tập → Xuất. Trong đó
  Tách/trộn là optional nhưng luôn hiện trong stepper.
- **Setup vừa là form vừa là nơi làm hiệu ứng AI**, lại có thêm trang `/studio` riêng dùng
  lại cùng component `AiEffects`.
- Để có một clip hiệu ứng dùng được, người dùng phải:
  1. Chọn ảnh
  2. Nhập prompt
  3. Bật consent
  4. Analyze
  5. Chọn object
  6. Chỉnh slider
  7. Apply
  8. Sang Studio
  9. Preview
  10. Render bản chuẩn 16s
  11. Đặt tên
  12. Lưu asset
  13. Quay lại Karaoke
  14. Chọn asset trong dropdown

  Khoảng **14 bước, 2 màn hình** ở hiện trạng (sau khi chuyển consent sang Settings còn 13).
- **“Dựng nền”** là tuỳ chọn, nhưng **“Xuất hai video”** cũng tự dựng nền. Người dùng phải
  hiểu nên bấm cái nào, và có 2 hệ progress khác nhau.
- Setup có 3 CTA khác nhau tuỳ trạng thái:
  - “Lưu khai báo và vào biên tập”
  - “Tiếp tục tách và trộn”
  - “Bỏ qua, căn lời với file đã có”
- Cảnh báo asset lệch duration/profile rồi fallback render inline — đúng về kỹ thuật nhưng
  tạo cảm giác “làm xong vẫn không dùng được”.

---

## 2. Phương án cải thiện tốc độ

### P0 — Quick wins, gần như không đổi hành vi (4,5–6,5 ngày)

#### P0.1. Đo trước khi tối ưu (0,5–1 ngày)

Thêm log có cấu trúc theo stage, ghi thẳng vào job log:

```text
clips 120.3s (cache 6/8) / assemble 17.1s / picture 31.2s / mux 3.8s
```

- Ghi cả **cache hit/miss** cho từng clip; đây là thứ giải thích “lúc nhanh lúc chậm”.
- Vẫn hỗ trợ `KARAOKE_PROFILE=1` để in chi tiết theo từng frame/layer khi cần.
- Log mẫu là ví dụ minh hoạ, không phải số đo cố định cho mọi project.
- Việc này giúp xác nhận trên máy/ảnh thật, tránh tối ưu cảm tính.

#### P0.2. Background: concat copy + decode-once + cache segment (2–2,5 ngày)

Mục tiêu: không encode lại slot đã có clip, chỉ encode mỗi ảnh **unique** một lần, và không
decode lại JPEG mỗi frame.

1. **Decode ảnh tĩnh một lần rồi clone frame**:
   - dùng `-framerate fps -i slide.jpg` + filter `loop=loop=-1:size=1:start=0`
     (hoặc `tpad=stop_mode=clone`);
   - **không dùng `-loop 1`** cho segment dài;
   - áp dụng cho cả cover (cover vẫn là một ảnh tĩnh đã bake chữ).
2. Tạo helper `encode_segment()` cho segment file-based (static/cover/tail/asset):
   - cùng `libx264`, `-preset veryfast`, `-crf 18`, `-pix_fmt yuv420p`, `-g 60`,
     `-an`, `-movflags +faststart`;
   - **`-bf 0`**: B-frame làm timestamp của concat demuxer lệch khi các segment có
     duration khác nhau (cover/tail). Đã đo: cùng `-bf 0`, concat copy cho đúng
     `nb_frames`, `avg_frame_rate` và duration;
   - frame-exact bằng `-frames:v N`;
   - `living._encode` vẫn dùng pipe rawvideo nhưng dùng cùng bộ tham số/version.
3. Thêm `SEGMENT_ENCODER_VERSION` (hoặc đưa `-g`, preset, CRF, pix_fmt, `-bf` vào key) cho
   `motion_cache_key`, static/cover key **và `background_identity`**; bump khi đổi tham số
   encoder. Đã bump `MOTION_CACHE_VERSION` 31 → 32 và `ENCODER_VERSION` 1 → 2 vì
   blur L-channel đổi pixel đầu ra.
4. Cache segment theo identity:
   - motion: `living.motion_cache_key(...)`;
   - static: `_static_identity(image_hash, profile)` + `image_frames`;
   - cover: `_cover_identity(...)` + `cover_frames`;
   - asset: dùng clip gốc nếu đã đúng frame count; nếu dư/thiếu frame thì re-encode một bản
     chuẩn hoá và cache theo `asset_fingerprint + image_frames`.
5. **Không trim bằng `-c copy`**. Cắt packet có B-frame sẽ hỏng. Mọi đoạn cần đổi số frame
   (asset dư 1 frame, slot cuối) đều phải re-encode đúng đoạn đó; slot cuối chỉ re-encode
   phần đuôi (≤ `image_frames`).
6. Validate từng segment trước khi concat: codec, profile, level, width/height, fps, pix_fmt,
   `nb_frames` hoặc duration. `nb_frames` có thể vắng trong MP4; runtime fallback sang
   duration, chỉ dùng `-count_frames` trong test. Segment lỗi được re-encode riêng; không
   fallback cả bài.
7. Sinh `concat.txt` với các `file '...'` lặp lại, chạy:

   ```bash
   ffmpeg -f concat -safe 0 -i concat.txt -c copy -an -movflags +faststart output.mp4
   ```

8. Giữ nguyên `_validate_background`; thêm `ffprobe -count_frames` trong test để assert
   `nb_frames == total_frames` (vì `_mux` dùng `-shortest`, thiếu 1 frame sẽ cắt đuôi audio).
9. Chỉ fallback toàn bộ về filter path cũ khi concat demuxer lỗi; log rõ lý do.

Lợi ích (đo/ước tính trên máy phát triển):

| Trường hợp (4 phút, 1080p30) | Trước | Sau |
|---|---|---|
| 30 slot motion/asset đã encode | 17,1s | ~0,2s |
| 1 ảnh tĩnh, 1 slot 7200 frame | **~41s** | **~9s** |
| 1 ảnh tĩnh lặp 30 slot (`image_duration = 8s`) | ~41s | ~0,6s |
| 8 ảnh tĩnh unique lặp 30 slot | ~41s | ~3–5s |

- Với ảnh tĩnh, win lớn nhất là **decode ảnh một lần** (36s decode-only → gần 0), sau đó mới
  đến việc chỉ encode ảnh unique và tái dùng segment cache giữa các lần rebuild.
- Không còn giới hạn số input khi `image_duration` ngắn.
- File có thể lớn hơn ~5–10% so với encode lại, nhưng giảm một thế hệ nén ở khâu nền.

#### P0.3. Giảm chi phí blur trong `living.py` (0,5–1 ngày)

Phạm vi: **mưa và tuyết** — hai effect dùng blur full-frame RGB. Lửa/khói/hơi nước/gió có
đường blur riêng, không hưởng lợi trực tiếp từ thay đổi này.

- Vẽ vào canvas `L` (grayscale), blur `L`, sau đó tint bằng màu hằng `RAIN_COLOR` /
  `SNOW_COLOR` (không phải màu riêng từng layer).
- Có thể bỏ blur khi radius < ~1 (hiện 0.4–0.8) hoặc dùng blur nhẹ hơn; cần so sánh ảnh
  trước/sau.
- Kỳ vọng đúng theo công thức: `82,5ms/frame × (0,23 + 0,77/2,55) ≈ 43ms/frame`
  → clip 8s ~10,4s + ~1s encode ≈ **11,5s**, tức **~1,8x** (không phải 2,5x).
- L-channel **không bit-exact**: cùng màu hằng nhưng thứ tự làm tròn uint8 khác, lệch
  ~1–2/255. Test nên so sánh bằng ngưỡng (PSNR hoặc lệch tối đa mỗi kênh), không so tuyệt đối.
- Áp dụng luôn cho Studio preview.

#### P0.4. Bỏ ffprobe lặp khi poll (0,5 ngày)

- Cache kết quả `probe_duration` / `_probe_video` theo `path + size + mtime_ns`.
- Cache luôn `_validate_background` theo
  `(path, size, mtime_ns, width, height, fps, total_frames)` — phải gồm tham số kỳ vọng, vì
  cùng một file có thể valid với profile này nhưng invalid với profile khác.
- Chỉ cần `stat` để kiểm tra cache; không gọi `ffprobe` nếu file và tham số kỳ vọng không đổi.
- Lưu `(identity_hash, target stat)` và `background` đã tính vào `record`; chỉ recompute khi
  có sự kiện (đổi ảnh/profile/audio, build xong, file mtime đổi).
- Khi đang build, trả trạng thái từ record thay vì probe lại.
- Test: mock `_probe_video`, assert **0 lần gọi** trong 1 poll khi metadata còn hợp lệ.

#### P0.5. Pin segment cache + thống nhất prune (1–1,5 ngày)

- Segment static/cover cũng nằm trong `cache/living/` và bị `prune(max_files=40)` dọn, nên phải
  pin **cả motion, static, cover**, không chỉ motion. Không pin → lần xuất sau mất segment tĩnh
  và mất mục tiêu “≤ 10s”.
- Tách `motion_cache_key(...)` từ `_render_loop` thành helper dùng chung; background gọi
  helper này để biết chính xác tên file cache cần pin. `clip_identity` hiện **không** chứa
  `seconds`, `selection`, `strip_key`, nên không map trực tiếp được.
- Static/cover dùng `_static_identity` / `_cover_identity` + frame count để pin.
- Cách khác: ghi sidecar `.pinned` cạnh file cache và cho `prune()` bỏ qua file có sidecar.
- **Đã kiểm tra**: `studio.py` dùng `cache_root("studio")`, `living.py` dùng
  `cache_root("living")`, nên **không** tranh chấp `max_files`. Tuy nhiên `render_preview()`
  tạo thêm một bản copy trong `cache/studio/` qua `_materialize`; có thể bỏ copy này nếu chỉ
  cần serve, nhưng đây không phải nguyên nhân prune.
- Asset Studio nằm ở `data_root` nên không bị prune; không cần pin.

**Kỳ vọng P0** (bài 4 phút, 8 ảnh mưa @1080p, máy phát triển):

- Khâu nền: ~3 phút → **~95–105s** (8 clip × ~11,5s + concat + cover).
- Tổng xuất: ~3,5–4 phút → **~130–140s**.
- Chỉ khi bật render clip song song (P1) mới kỳ vọng **~60–90s** cho khâu nền.

Không nên hứa 90–100s chỉ với P0.

### P1 — Tăng tốc thêm (2–3 ngày)

- **Render clip song song**: đã bật mặc định **2 worker** trong P0.2 (`KARAOKE_CLIP_WORKERS`).
  Đo nhanh 4 clip mưa @960×540: 1 worker 1,47s / 2 worker 1,01s / 4 worker 0,82s
  (~1,4–1,8x). Phần vẽ particle là Python-level nên không nên hứa 2,5–3x; cần đo lại @1080p.
- **Picture cache**: key gồm `background.hash + hash(nội dung lines) + profile_fingerprint +
  total_frames + cover_frames + TEXT_FPS + PICTURE_BUILDER_VERSION`; chỉ tái dùng khi tất cả
  khớp. Hữu ích khi chỉ đổi audio/format. Thiếu `background.hash` là dễ tái dùng picture cũ
  sau khi đổi ảnh/effect.
- **Cho phép tái dùng background `degraded`**: hiện một effect lỗi làm `degraded=true` →
  lần xuất sau rebuild mãi. Nên cho dùng lại kèm cảnh báo, hoặc cache lý do lỗi để không
  retry liên tục.
- **Chế độ “Xuất nháp”**: 720p hoặc CRF cao hơn để xem trước nhanh; bản cuối vẫn 1080p.

**Không khuyến nghị mặc định dùng `h264_videotoolbox`**: đo trên máy phát triển thì chậm
hơn x264 veryfast (26s vs 16s cho 4 phút), nên chỉ nên là tuỳ chọn.

---

## 3. Phương án làm gọn luồng (P2)

Mục tiêu: **giảm 5 phase xuống 3 nhóm bước** (Chuẩn bị → Biên tập → Xuất), gộp nhãn
restore/align vào “Chuẩn bị”; màn align vẫn tồn tại cho tới khi có editor chạy nền.

### 3.1. Luồng đích

```text
Home
 └─ Dự án (1 trang)
     ├─ Chọn audio gốc → tự match vocal/instrumental/title
     ├─ Nếu thiếu stem: card "Tách giọng" inline (chạy nền)
     ├─ Lời + ảnh + hiệu ứng (drawer)
     └─ CTA duy nhất: "Vào biên tập" (tự căn lời nếu chưa có)
          └─ Biên tập & Xuất (1 trang)
              ├─ Editor + preview
              ├─ Drawer "Nền & hiệu ứng"
              └─ CTA: "Xuất hai video" (tự dựng nền nếu cần)
```

- Bỏ phase “Ghép chữ” riêng: căn lời chạy nền, hiện progress/toast, xong tự mở editor.
- Bỏ phase “Tách và trộn” khỏi stepper: chỉ hiện card khi thật sự thiếu stem.
- Bỏ nút “Dựng nền” khỏi luồng chính: xuất video tự dựng; trạng thái nền chỉ là một chip nhỏ
  (`Nền sẵn sàng · 4:12`). Nút “Dựng lại” đưa vào mục Nâng cao.
- Match audio theo đúng logic hiện có (`/api/match`): `_(Vocals)` / `_(Instrumental)` cùng thư
  mục, hoặc `find_stems()` theo `song_key_from_stem` trong `Separated/`, chọn cặp mới nhất.
  Nếu không tìm thấy cặp chắc chắn, để trống và cho chọn tay; không đoán theo tên tiếng Việt.

### 3.2. Gộp AI Studio vào card hiệu ứng

- Không bắt người dùng sang `/studio` rồi quay lại. Dùng drawer/modal ngay trong card ảnh.
- Giữ route `/studio` cho power user, nhưng **không thêm tính năng mới**; tách editor hiệu
  ứng thành component dùng chung để tránh 2 bản UI lệch nhau.
- **Render bản chuẩn theo `image_duration` của dự án**, không hard-code 16s: truyền
  `imageDuration` từ `project-setup`/`ai-effects` xuống Studio. API đã nhận `duration`
  (`server.py:189`) và `save_asset_from_preview` dùng `preview.duration`. Nhờ vậy asset 16s
  không còn invalid với mặc định 8s, và cache key Studio trùng với background
  (`strip_rect=(0,0,0,0)`), tránh render 2 lần. Đồng thời thay điều kiện
  `preview.duration < STANDARD_DURATION` trong `saveAsset` bằng so với duration mục tiêu.
  Standalone `/studio` dùng mặc định 8s hoặc cho chọn duration.
- Sau khi **Apply**, tự chạy job render bản chuẩn ở nền; card hiện trạng thái:
  `Đang dựng 40%` → `Sẵn sàng` + video preview.
- **Single-flight theo cache key**: nếu job render Studio cùng `motion_cache_key` đang chạy,
  export chờ/attach vào job đó thay vì render inline lần thứ hai.
- **Tự lưu asset** sau khi có quota + GC (trước đó để opt-in), tên mặc định = tên file ảnh.
  Hiện chỉ có `delete_asset`, không có prune; bật auto-save trước khi có quota sẽ đầy đĩa
  âm thầm. Đây là **điều kiện tiên quyết**, không phải việc có thể làm sau.
- Nếu người dùng xuất khi clip chưa xong: export vẫn render inline từ `effect_plan` như hiện
  tại, không chặn (trừ khi có job cùng key — xem single-flight ở trên).
- Consent Gemini chuyển thành setting một lần trong phần cài đặt, nhưng **vẫn ghi
  `effect_consent_receipt` cho từng effect** khi Apply (image_hash, model, timestamp) để
  không phá schema/test hiện có.
- **Không tự set `image_duration` toàn cục theo asset**: setting này áp cho mọi ảnh và làm
  đổi `_motion_identity`/background hash của tất cả clip. Mặc định vẫn render inline từ
  `effect_plan`; chỉ gợi ý đổi duration trong mục Nâng cao kèm cảnh báo “sẽ dựng lại toàn bộ
  clip”, và chỉ tự đổi khi tất cả asset đang dùng có cùng duration.

### 3.3. Giảm tải nhận thức

- Ẩn bớt thông số kỹ thuật (hash, frame count, profile, đường dẫn nền) vào “Nâng cao”.
- Gom `notice/error/assetsError/inspectError/renderHint` về một chỗ hiển thị.
- Tự suy ra: title/artist từ tên file, profile từ tỉ lệ ảnh đầu tiên, `cover_line` mặc định rỗng.
- `cover_duration`, `image_duration`, `format`, `output_dir` cho vào “Tuỳ chọn xuất”, mặc định
  hợp lý.
- Với nhiều ảnh: hiển thị grid thumbnail, mỗi ảnh một chip trạng thái hiệu ứng + nút
  “Làm hiệu ứng”, thay vì panel dài chỉ cho ảnh đang chọn.

**Kỳ vọng:** từ ~14 bước / 2 màn hình cho một hiệu ứng xuống **2–3 bước / 1 drawer**;
stepper từ 5 phase xuống 3 nhóm bước (màn align vẫn còn).

---

## 4. Thứ tự đề xuất

| Ưu tiên | Việc | Công | Kết quả |
|---|---|---|---|
| 1 | Instrumentation theo stage + cache hit/miss trong job log | 0,5–1 ngày | Biết chính xác máy/ảnh thật chậm ở đâu |
| 2 | Concat copy + decode-once + `encode_segment` + cache static/cover | 2–2,5 ngày | Bỏ encode lại slot đã có; ảnh tĩnh 41s → ~9s |
| 3 | Blur L-channel cho mưa/tuyết | 0,5–1 ngày | Clip hiệu ứng nhanh ~1,8x |
| 4 | Cache probe + lưu background status | 0,5 ngày | UI mượt, bỏ 3 ffprobe/giây |
| 5 | Pin segment cache + thống nhất prune | 1–1,5 ngày | Hết cảnh “lần sau tự chậm lại” |
| 6 | Gọn luồng (P2): bỏ phase thừa, gộp Studio, auto asset | 5–8 ngày | Giảm mạnh số bước thao tác |
| 7 | Tăng tốc thêm (P1): render clip song song + picture cache | 2–3 ngày | Nhanh thêm, tốt cho re-export |

Thứ tự trên theo **giá trị**, không theo số hiệu P0/P1/P2. Tổng P0 (mục 1–5):
khoảng **4,5–6,5 ngày**.

---

## 5. Tiêu chí nghiệm thu

Đo trên Apple M5, FFmpeg 9.0.2, bài 4 phút @1080p30.

- **8 ảnh mưa, `image_duration = 8s`:**
  - Khâu nền lần đầu **≤ 110s** với P0.
  - Tổng xuất lần đầu **≤ 145s** với P0.
  - Khi bật render clip song song (P1): khâu nền **≤ 70s**.
  - Sửa lời rồi xuất lại **≤ 45–60s**, không rebuild nền, không gọi `render_motion_loop`.
- **Ảnh tĩnh:**
  - Nhiều ảnh unique lặp lại: khâu nền **≤ 10s** (decode-once + chỉ encode ảnh unique).
  - Một ảnh phủ toàn bài (`image_duration` ≥ độ dài bài): **≤ 15s** lần đầu (baseline hiện
    tại ~41s).
- **Poll trạng thái:** 0 ffprobe/lần poll; p95 < 50ms cho 200 request liên tiếp vào
  `/api/jobs/{id}` khi đang render.
- **Frame-exact:** `ffprobe -count_frames` trả đúng `total_frames` (7200); `-mux` với
  `-shortest` không cắt đuôi audio.
- **Tests:**
  `.venv-karaoke/bin/python -m unittest tests.test_background tests.test_living tests.test_assets tests.test_project_effects`
  xanh.
- **Test bổ sung:**
  - Fixture 4 segment (motion/static/asset/tail) → concat đúng tổng frame, decode sạch bằng
    `ffmpeg -v error -i out -f null -`.
  - Lyric-only re-export: mock `render_motion_loop` và assert **không gọi**.
  - Poll trạng thái: mock `_probe_video`, assert **0 lần gọi**.
  - L-channel: so sánh frame với ngưỡng lệch ≤ 2/255, không so tuyệt đối.
  - Static decode-once: assert số frame decode JPEG = 1, không phải 7200.
- **Luồng hiệu ứng:** sau khi có quota + GC, không cần mở `/studio`, không cần đặt tên/lưu
  asset thủ công.
- `cd web && npx tsc --noEmit` pass.

---

## 6. Rủi ro & cách xử lý

| Rủi ro | Cách xử lý |
|---|---|
| Concat copy lệch SPS/PPS do segment encode khác tham số | Dùng chung `encode_segment` (`-g 60`, yuv420p, `+faststart`); `SEGMENT_ENCODER_VERSION` trong cache key; validate từng segment; test decode `ffmpeg -v error -i out -f null -` |
| Trim asset dư frame bằng `-c copy` làm hỏng B-frame | Không packet-trim; re-encode đúng segment hoặc phần đuôi |
| Background thiếu frame làm `_mux -shortest` cắt audio | Assert `nb_frames == total_frames` bằng `ffprobe -count_frames` |
| Fallback filter path làm chậm gấp đôi | Validate từng segment trước concat; chỉ repair segment lỗi, fallback toàn bài là phương án cuối |
| `-loop 1` decode lại JPEG mỗi frame | Decode một lần + `loop=loop=-1:size=1:start=0` / `tpad=stop_mode=clone`; test đếm decode = 1 |
| Blur L lệch màu nhẹ | So sánh bằng ngưỡng (lệch ≤ 2/255 hoặc PSNR), không so tuyệt đối; chỉ áp dụng mưa/tuyết |
| Validate cache thiếu tham số kỳ vọng | Key gồm `(path, size, mtime_ns, width, height, fps, total_frames)` |
| Render song song tốn RAM/CPU | Giới hạn 2–3 worker; tắt prune trong worker; đo lại |
| Apply rồi Export ngay gây render trùng | Single-flight theo `motion_cache_key`; export chờ/attach job Studio cùng key |
| Tự đổi `image_duration` làm invalidate toàn bộ cache | Không tự đổi toàn cục; chỉ gợi ý trong Nâng cao kèm cảnh báo rebuild |
| Asset chuẩn 16s invalid với mặc định 8s | Render bản chuẩn theo `image_duration` của dự án; standalone Studio mặc định 8s |
| Picture cache tái dùng sai | Key gồm background hash + hash nội dung lines + profile + frames + `TEXT_FPS` + builder version |
| Refactor UX ảnh hưởng luồng cũ | Làm theo bước, giữ route `/studio` (không thêm tính năng), tách component dùng chung |
| Bỏ phase làm vỡ state machine | Map `Phase` (`types.ts`) với `draft/aligned/exported`; xử lý lỗi căn lời; test `maker-form.tsx` |
| Asset tự lưu gây đầy đĩa | **Việc mới**: quota + GC là điều kiện tiên quyết trước khi bật auto-save; cho xoá trong UI |
| Cache segment bị prune oan | Pin motion/static/cover bằng `motion_cache_key` dùng chung hoặc sidecar `.pinned`; `prune()` bỏ qua file pinned |

---

## 7. Kết quả triển khai (đo trên Apple M5, FFmpeg 9.0.2)

| Hạng mục | Trước | Sau |
|---|---|---|
| Clip mưa 8s @1080p30 | ~21,0s | **~11,6s** (~1,8x) |
| Background 1 ảnh tĩnh, 1 slot 240s | ~41s | **~6,7–12,5s** tuỳ texture ảnh (decode-once + concat) |
| Background 8 ảnh tĩnh unique + cover, bài 4 phút | ~41s | **~2,2s** |
| Ghép 30 slot motion/asset đã encode | ~17,1s | **~0,2s** |
| Picture cache (cùng lời/nền) | encode lại ~30s | **reuse, ~0s** |
| Poll trạng thái khi render | ~3 ffprobe/lần poll | **0 ffprobe/lần poll** (cache theo size/mtime + TTL 2s) |

Đã triển khai thêm:

- `karaoke/segment.py`: `encode_still` (decode-once + `loop` filter), `encode_video`,
  `encode_segment`, `SEGMENT_ENCODER_VERSION = 2`, `-bf 0`.
- `background.py`: concat copy + fallback filter path, cache segment static/cover/asset,
  render clip song song (`KARAOKE_CLIP_WORKERS`, mặc định 2), picture cache,
  instrumentation theo stage, probe/validate cache.
- `living.py`: `motion_cache_key`/`motion_cache_path`, single-flight theo key, pin/prune,
  blur L-channel, `MOTION_CACHE_VERSION = 32`, `ENCODER_VERSION = 2`.
- `media.py`: cache `probe_duration`/`probe_channels`.
- `server.py`: cache background status 2s, dọn asset sau khi lưu, endpoint
  `GET /api/studio/assets/usage` và `POST /api/studio/assets/cleanup`.
- `web`: stepper 3 bước, auto-skip tách/trộn, tự render + lưu asset sau Apply,
  Studio duration 8/16, panel nền gọn trong “Nâng cao”.

Lưu ý vận hành:

- `KARAOKE_CLIP_WORKERS=1` nếu máy yếu hoặc muốn giảm tranh chấp CPU.
- Cache `segments/` và `living/` dùng sidecar `.pin`; đừng xoá sidecar nếu muốn giữ segment.
- Background cũ (builder v1) sẽ rebuild một lần sau khi nâng cấp.

---

## 8. Việc chưa làm trong đề xuất này

- Không viết lại renderer hiệu ứng.
- Không đổi cách tính timing lời.
- Không đổi nguyên tắc “một picture, hai mux”.
- Không bỏ `effect_consent_receipt`; chỉ đổi UI consent thành setting một lần.
- Không tự đổi `image_duration` toàn cục theo asset.
- Không mặc định dùng hardware encoder.
- Không thêm `background_video` bên ngoài ở giai đoạn này.

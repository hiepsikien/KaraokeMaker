# KaraokeMaker

KaraokeMaker căn lời vào file vocal, cho sửa thời gian từng chữ, rồi xuất hai video karaoke dùng chung hình nền và trang bìa: một bản có giọng và một bản không lời.

Giao diện là ứng dụng web. Phần căn lời và dựng video chạy bằng Python. Ứng dụng viết cho macOS: hộp thoại chọn file dùng AppleScript, mở thư mục dùng Finder, chữ Việt dùng font Arial Unicode, và GPU căn lời là Apple MPS.

Mã tách giọng của [Ultimate Vocal Remover GUI](https://github.com/anjok07/ultimatevocalremovergui) vẫn nằm trong repo này. Bước **Tách và trộn** gọi Kim Vocal 2 bằng môi trường `.venv` của UVR. File model không nằm trong repo: tải [Kim_Vocal_2.onnx](https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx) vào `models/MDX_Net_Models/`. Thiếu file thì nút Tách báo không thấy model. Có thể bỏ qua bước đó nếu vocal và instrumental đã có sẵn.

## Chạy

Cần hai terminal, cùng thư mục gốc của repo. API chỉ nghe `127.0.0.1:8765`. Không đưa cổng 8765 hay 3000 ra mạng: API đọc file trong thư mục nhạc và các thư mục đã chọn trong phiên này.

```bash
.venv-karaoke/bin/python -m karaoke
```

```bash
cd web
npm run dev
```

Mở http://127.0.0.1:3000. Next.js chuyển `/api/*` sang API, kể cả file vocal dùng để nghe thử.

Nếu chưa có môi trường Python:

```bash
python3 -m venv .venv-karaoke
.venv-karaoke/bin/python -m pip install -r requirements-karaoke.txt
ffmpeg -version
ffprobe -version
cd web && npm install
```

`requirements-karaoke.txt` gồm `stable-ts`, `torch`, Pillow, FastAPI, Uvicorn, `requests`, `python-dotenv` và `numpy`. Lần cài đầu có thể mất vài phút vì `torch` lớn. Nếu `ffmpeg` hoặc `ffprobe` không có trong `PATH`, cài bằng `brew install ffmpeg`.

Thư mục nhạc mặc định là `~/Downloads/Audio` nếu thư mục đó tồn tại, kể cả tên có một dấu cách ở cuối. Đổi bằng:

```bash
export KARAOKE_AUDIO_ROOT="$HOME/Music/Karaoke"
```

`KARAOKE_ALLOWED_ROOTS` là các thư mục thêm, ngăn cách bằng dấu `:`. File nằm ngoài các thư mục này và ngoài thư mục vừa chọn trong hộp thoại sẽ bị từ chối.

Môi trường `.venv` của UVR và `.venv-karaoke` tách riêng. UVR khóa `numpy` 1.23.5, còn thư viện căn lời cần bản mới hơn.

Sau khi sửa code Python, tắt server bằng Ctrl+C rồi chạy lại `.venv-karaoke/bin/python -m karaoke`. Trang web tải lại là đủ khi chỉ sửa giao diện.

## Hiệu ứng nền AI (thử nghiệm)

Trong bước **Khai báo** có mục **Hiệu ứng nền (AI)**. Ứng dụng gửi ảnh nền đang chọn
lên Google Gemini để nhận diện object/vùng, rồi lưu một `effect_plan` vào dự án.
Hiện đã render **mưa, tuyết, nước (W1 shimmer), khói, gió/sway, lửa, tàn lửa, chim, tia nắng, sương mù và nhịp sáng**.
Chim và tia nắng chỉ được bật khi **prompt yêu cầu rõ**; ảnh có chim/nắng không tự động thêm effect.

Bật bằng cách tạo file `.env` ở thư mục gốc (đã có trong `.gitignore`):

```bash
cp .env.example .env
# rồi điền GEMINI_API_KEY=...
```

Hoặc đặt biến môi trường trước khi chạy server. Sau khi đổi `.env`, tắt và chạy lại
server Python. Key chỉ nằm ở server, không gửi xuống trình duyệt.

Thứ tự ưu tiên key Gemini: **Cài đặt trong app → `.env` → biến môi trường**. Có thể
nhập key trong **Cài đặt → Hiệu ứng AI**; key đó sẽ đè lên key hệ thống/`.env` và
được lưu với quyền `0600`.

- Ảnh chỉ được gửi khi bạn bật ô đồng ý trong mục Hiệu ứng nền.
- **Định dạng video**: Ngang 16:9 (1920×1080), Dọc 9:16 (1080×1920), Vuông 1:1
  (1080×1080), Dọc 4:5 (1080×1350). 9:16 có hậu tố `(dọc)` và dải lời nằm trên vùng
  an toàn đáy; 1:1/4:5 có hậu tố `(vuông)`/`(4x5)`.
- **Ambient audio**: mặc định tắt; nếu prompt có "tiếng mưa/tiếng sóng/âm thanh" thì
  mix rất nhỏ (-32 dB) dưới nhạc.
- Tab **AI Studio** (`/studio`) có hai tab: **Biên tập** (ảnh → sân khấu → thuộc tính)
  và **Thư viện clip** (tìm không dấu, lọc định dạng, xem lưới/danh sách, đổi tên, xoá).
  Preview nhanh render tối đa 640 px để phản hồi nhanh; video Karaoke xuất vẫn đúng profile
  1080p/9:16/1:1/4:5.
- Model: `gemini-3.8-flash` cho inventory ảnh, `gemini-3.1-flash-lite` cho intent.
- Chi phí tham khảo: ~1 cent/ảnh (xem `docs/ai-scene-director.md` §4.2).
- Cache: dùng thư mục cache theo platform (`~/Library/Caches/KaraokeMaker` trên macOS,
  `%LOCALAPPDATA%\KaraokeMaker\Cache` trên Windows, `~/.cache/karaokemaker` trên Linux);
  override bằng `KARAOKE_CACHE_DIR`.
- Asset Studio nằm ở thư mục dữ liệu ổn định (`~/Library/Application Support/KaraokeMaker/assets`
  trên macOS, `%APPDATA%\KaraokeMaker\assets` trên Windows, `~/.local/share/karaokemaker/assets`
  trên Linux), override bằng `KARAOKE_DATA_DIR`; không bị dọn cùng cache.
- Ảnh nằm ngoài thư mục nhạc/`KARAOKE_ALLOWED_ROOTS` phải được chọn qua hộp thoại
  của ứng dụng, giống các file khác.
- Trong **Biên tập**, mục **Nền có hiệu ứng** cho phép dựng nền trước khi ghép lời,
  xem trạng thái sẵn sàng/cần dựng lại, mở thư mục nền và dọn bản cũ. Nền lưu tại
  `<thư mục xuất>/.karaoke-media/backgrounds/`.
- Trong **AI Studio**, clip chuẩn được tự lưu vào **Thư viện clip** sau khi áp dụng; có thể
  đổi tên, tải về, mở thư mục hoặc xoá (clip đang được dự án dùng sẽ bị chặn xoá). Clip lưu
  ở thư mục dữ liệu ứng dụng, không bị dọn cùng cache. Nếu clip khác định dạng hoặc khác
  “Mỗi ảnh nền”, export sẽ render lại từ `effect_plan`.

## Hiệu ứng thêm, trang và waveform

Ba phần này nằm trong **Khai báo** (mục **Hiệu ứng thêm**, **Trang & chữ**) và drawer
**Xuất video**.

- **Hiệu ứng thêm (không cần AI):** tuyết, lá, bokeh, light leak. Chạy như một lớp phủ
  toàn video, không phụ thuộc Gemini; có thể bật/tắt, chỉnh cường độ/mật độ/tốc độ/palette.
  Tuyết và lá dùng sprite thật, loop khép kín; bokeh/light leak blend `screen` để không
  cháy trắng.
- **Mây:** khoanh box trong editor ảnh (nút **Vẽ object** → vai trò **Mây**). Vùng mây
  được warp nhẹ, viền mờ, giữ gốc; có thể dùng Gemini để gợi ý vùng nhưng không tự bật.
- **Cành cây:** nhận diện nhiều cành theo `group_id` và cho đung đưa bằng `wind`; prompt
  “cành cây đung đưa” sẽ kích hoạt focused inventory. Có thể vẽ tay và đặt tên nhóm.
- **Tàn lửa/tro bay:** tách `spark` (lõi vàng-cam, có glow) và `ash` (tro xám), thêm
  `glow`/`ash_ratio`/`flicker`, tránh hiện tượng bong bóng trắng.
- **Trang & chữ:** layout bìa (giữa/dưới-trái/dưới-giữa), cỡ chữ, màu, uppercase,
  bóng/viền, blur/darken/vignette/scrim; banner **Nhạc dạo** trong dải lời khi khoảng
  lặng ≥ 8 giây; trang kết có ảnh riêng (fallback ảnh bìa), thời lượng và fade.
- **Waveform:** dải sóng ngang, mặc định đặt ngay trên dải lời (`y = 0.70` cho 16:9,
  `0.63` cho 9:16), kiểu bars/line/dots, chạy theo tiến trình bài hát. Kéo vị trí trong
  tab **Waveform** của drawer Xuất; toạ độ được lưu ngay vào dự án.

## Đăng YouTube

Trang **Đăng bài** (`/publish`) cho phép kết nối một kênh YouTube, lưu nháp nội dung và
bấm đăng video mới nhất sau khi xuất. Mỗi nháp gắn với một dự án + vai trò video
(`vocal`/`instrumental`), nên sau khi xuất lại chỉ cần bấm **Đăng lại**.

- Cần tạo OAuth client và điền `.env`; xem hướng dẫn từng bước tại
  `docs/youtube-oauth-setup.md`.
- Form YouTube chỉ có 5 trường: tiêu đề, nội dung, trẻ em có xem được, chế độ đăng
  (mặc định **Công khai**), playlist. Không làm thumbnail/end screen.
- Tiêu đề/nội dung giữ nguyên emoji/icon khi nhập hoặc dán.
- TikTok chưa nằm trong phạm vi phiên bản này; nút TikTok được ẩn.

## Cách dùng

Giao diện dùng **Dự án** ở `/` (dashboard) và workspace ở `/project`; các bước bên trong
workspace là **Chuẩn bị** (khai báo, tách/trộn, ghép chữ) → **Biên tập** → **Xuất** (drawer).

1. Ở dashboard `/`, chọn **Tạo dự án mới**, mở một dự án gần đây, hoặc **Mở file dự án**
   (file `.karaoke.json`; bản `timing.json` cũ vẫn mở được và sẽ được chuyển sang file dự án).
2. Với dự án mới: nhập tên bài và tác giả, chọn audio gốc, dán lời, chọn ảnh, chọn thư mục xuất, rồi bấm **Tiếp tục tách và trộn**. Nếu đã có sẵn vocal và instrumental, có thể **Bỏ qua, căn lời với file đã có**.
3. **Tách** chạy Kim Vocal 2 (overlap 25%, segment 512) trong môi trường `.venv` của UVR và ghi vocal cùng instrumental. Cần file `models/MDX_Net_Models/Kim_Vocal_2.onnx`. Bản nhạc nền cộng instrumental với dải cao của file gốc (mặc định từ 8000 Hz, 50%) và dải thấp (mặc định dưới 120 Hz, 50%). Hai dải lấy từ bản gốc nên hơi giọng và tiếng trầm của vocal vẫn có thể lọt vào; hạ volume hoặc nới cutoff nếu muốn bản không lời sạch hơn. Cutoff và volume sửa được. **Nghe thử** và **Xuất nhạc nền** dựng file ngay trong request (WAV, FLAC hoặc MP3), nên bài dài sẽ đợi đến khi ffmpeg xong. File xuất trở thành instrumental của video không lời. Đổi cutoff hoặc volume không chạy lại model. Sau đó bấm **Căn lời và vào biên tập**. Thiết bị tách giọng và model mặc định đổi trong **Cài đặt** (biểu tượng bánh răng ở thanh trên).
4. Khi căn xong, sửa trên timeline: sóng âm ở trên, câu hát ở dưới. Kéo cả câu để dời. Kéo mép để đổi chỗ bắt đầu và kết thúc. Bấm một câu để sửa từng chữ. **Phát** và **Tạm dừng** nằm trên timeline.
5. **Thêm câu** chèn một câu mới sau câu đang chọn. **Chia câu** cắt câu đang chọn thành hai, tại chữ đang chọn hoặc tại vạch phát.
6. **Lưu dự án** ghi file `.karaoke.json` (và `timing.json` kèm theo), không cần đợi xuất video.
7. Lần sau: **Mở dự án đã có** — vào thẳng biên tập, Whisper không chạy lại. Nếu thiếu file để xuất, bấm **Bổ sung file trong khai báo**.
8. **Xuất hai video** cần audio gốc, instrumental, và ảnh bìa hoặc ảnh nền.

Chọn file vocal hoặc instrumental dạng `1_Tên bài_(Vocals).wav` thì ứng dụng tự điền file còn lại trong cùng thư mục, và tìm bài gốc cùng tên trong thư mục `Songs`. Chọn thẳng bài gốc thì ứng dụng lấy cặp vocal và instrumental mới nhất trong `Separated`.

Hộp thoại chọn file mở sẵn `Songs`, `Separated/Kim2` và `Karaoke` bên trong thư mục nhạc, nếu các thư mục đó còn tồn tại.

## Lời bài hát

Mỗi câu hát là một dòng. Dòng trống được bỏ. Dòng nằm trọn trong ngoặc vuông cũng được bỏ, vì đó là ghi chú chứ không phải lời hát:

```text
[Verse 1]
[Tiếng trống trận]
Mười tám con người dưới cờ Lũng Nhai
```

Chỉ dòng thứ ba được căn giờ và hiện trên video.

Căn lời dùng file vocal. Trước khi chạy, chọn chiến lược:

- **Whisper** — local, mặc định; chọn model (`tiny`, `base`, `small`, `medium`, `large-v2`, `large`), thiết bị CPU/GPU Apple.
- **Gemini line-level + Whisper word-level** — Gemini chia câu và lấy mốc câu; Whisper căn từng từ trong câu. Cần `GEMINI_API_KEY` và bật đồng ý gửi vocal. Nếu Gemini lỗi hoặc chưa đồng ý, app tự quay lại Whisper.

Chỗ khớp với lời đã nhập thì chữ trên video là lời đó. Đoạn hò/ad-lib không có trong lời được đánh dấu riêng; câu không đủ tin cậy có thể bỏ trống để thêm tay.

Việc này chạy một lần trong lúc căn. Mở dự án đã có không căn lại, trừ khi bấm **Ghép chữ lại**. Vocal không phải WAV 16-bit, kể cả mp3 và flac, được chuyển tạm trước khi đo tiếng hát.

## Video xuất ra

Với bài tên `Bình Ngô Đại Cáo`, thư mục xuất có:

- `Bình Ngô Đại Cáo.karaoke.json` là file dự án (metadata + timing)
- `Bình Ngô Đại Cáo (có giọng).mp4` dùng audio gốc
- `Bình Ngô Đại Cáo (không lời).mp4` dùng instrumental
- `Bình Ngô Đại Cáo timing.json` là bản timing kèm theo (tương thích cũ)

Lưu và xuất đều ghi lại vào cùng file dự án.

Trang bìa hiện trước, mặc định 5 giây, với ảnh bìa, tên bài và tác giả. Tiếng được lùi đúng bằng thời lượng bìa, nên chữ bắt đầu đổi màu khi bài hát bắt đầu. Ảnh nền đổi lần lượt, mặc định mỗi ảnh 8 giây. Nếu không chọn ảnh nền, ảnh bìa được dùng làm nền.

Câu hát nằm ở cuối khung hình. Chữ chưa hát màu trắng, chữ đang hát được tô vàng dần. Hết câu thì chuyển sang câu sau. Trước câu đầu và trước câu sau một khoảng trống từ 8 giây, 5 giây cuối hiện sẵn câu đó cùng số đếm.

## Mã nguồn

```text
karaoke/
  access.py      # thư mục được phép đọc
  server.py      # API, chỉ trên 127.0.0.1
  project.py     # đọc/ghi file .karaoke.json
  pipeline.py    # kiểm tra file, mở dự án, căn lời, xuất video
  mix.py         # trộn instrumental với hai dải tần của file gốc
  separate_runner.py  # gọi Kim Vocal 2 trong .venv của UVR
  align.py       # căn lời vào vocal và kéo chữ về đoạn đang hát
  lyrics.py      # bỏ dòng ghi chú, so khớp tên bài
  media.py       # ảnh, thời lượng, chuyển vocal sang WAV
  living.py      # hiệu ứng nền động (mưa, khói, nước…)
  assets.py      # lưu clip Studio thành asset ổn định
  background.py  # dựng nền có hiệu ứng trước, rồi overlay lời và ghép tiếng
  render.py      # dựng chữ, đếm ngược, overlay và mux
web/
  src/app        # trang Next.js
  src/components/maker-form.tsx      # điều phối các bước
  src/components/home-gate.tsx       # tạo mới / mở dự án
  src/components/project-setup.tsx   # khai báo
  src/components/timing-editor.tsx
  src/components/align-timeline.tsx
requirements-karaoke.txt
```

Việc đang chạy được giữ trong bộ nhớ. Tắt server là mất phiên đang mở. File dự án và video đã ghi vẫn còn trên đĩa.

## Tách giọng bằng UVR

Bước tách trong ứng dụng đã gọi `.venv`. Khi cần mở giao diện UVR đầy đủ, dùng cùng môi trường đó, không dùng `.venv-karaoke`:

```bash
source .venv/bin/activate
python UVR.py
```

Hướng dẫn cài và dùng UVR nằm ở repo gốc: https://github.com/anjok07/ultimatevocalremovergui

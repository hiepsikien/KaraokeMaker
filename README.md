# KaraokeMaker

KaraokeMaker căn lời vào file vocal, cho sửa thời gian từng chữ, rồi xuất hai video karaoke dùng chung hình nền và trang bìa: một bản có giọng và một bản không lời.

Giao diện là ứng dụng web. Phần căn lời và dựng video chạy bằng Python. Ứng dụng viết cho macOS: hộp thoại chọn file dùng AppleScript, mở thư mục dùng Finder, chữ Việt dùng font Arial Unicode, và GPU căn lời là Apple MPS.

Mã tách giọng của [Ultimate Vocal Remover GUI](https://github.com/anjok07/ultimatevocalremovergui) vẫn nằm trong repo này. Bước **Tách và trộn** gọi Kim Vocal 2 bằng môi trường `.venv` của UVR. Có thể bỏ qua bước đó nếu vocal và instrumental đã có sẵn.

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

`requirements-karaoke.txt` gồm `stable-ts`, `torch`, Pillow, FastAPI và Uvicorn. Lần cài đầu có thể mất vài phút vì `torch` lớn. Nếu `ffmpeg` hoặc `ffprobe` không có trong `PATH`, cài bằng `brew install ffmpeg`.

Thư mục nhạc mặc định là `~/Downloads/Audio` nếu thư mục đó tồn tại, kể cả tên có một dấu cách ở cuối. Đổi bằng:

```bash
export KARAOKE_AUDIO_ROOT="$HOME/Music/Karaoke"
```

`KARAOKE_ALLOWED_ROOTS` là các thư mục thêm, ngăn cách bằng dấu `:`. File nằm ngoài các thư mục này và ngoài thư mục vừa chọn trong hộp thoại sẽ bị từ chối.

Môi trường `.venv` của UVR và `.venv-karaoke` tách riêng. UVR khóa `numpy` 1.23.5, còn thư viện căn lời cần bản mới hơn.

Sau khi sửa code Python, tắt server bằng Ctrl+C rồi chạy lại `.venv-karaoke/bin/python -m karaoke`. Trang web tải lại là đủ khi chỉ sửa giao diện.

## Cách dùng

Luồng gồm năm bước: **Khai báo → Tách và trộn → Ghép chữ → Biên tập → Xuất**.

1. Trên màn hình đầu, chọn **Tạo dự án mới** hoặc **Mở dự án đã có** (file `.karaoke.json`; bản `timing.json` cũ vẫn mở được và sẽ được chuyển sang file dự án).
2. Với dự án mới: nhập tên bài và tác giả, chọn audio gốc, dán lời, chọn ảnh, chọn thư mục xuất, rồi bấm **Tiếp tục tách và trộn**. Nếu đã có sẵn vocal và instrumental, có thể **Bỏ qua, căn lời với file đã có**.
3. **Tách** chạy Kim Vocal 2 (overlap 25%, segment 512) trong môi trường `.venv` của UVR và ghi vocal cùng instrumental. Bản nhạc nền cộng instrumental với dải cao của file gốc (mặc định từ 8000 Hz, 50%) và dải thấp (mặc định dưới 120 Hz, 50%). Cutoff và volume sửa được. **Nghe thử** phát bản trộn trước khi **Xuất nhạc nền** (WAV, FLAC hoặc MP3). File xuất trở thành instrumental của video không lời. Đổi cutoff hoặc volume không chạy lại model. Sau đó bấm **Căn lời và vào biên tập**. Thiết bị tách giọng dùng lựa chọn CPU hoặc GPU Apple ở bước khai báo.
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

Căn lời dùng file vocal. Trước khi chạy, chọn model Whisper (`tiny`, `base`, `small`, `medium`, `large-v2`, `large` — bản v3), thiết bị CPU hoặc GPU Apple, ngưỡng không-lời, và có thử lại khi câu bị lặp hay không. Máy chưa có model thì tải về lúc bắt đầu. Whisper nghe và ghi lại đúng chữ cùng thời gian nó nghe được. Lời đã nhập không tham gia lần căn này; lời đó để sửa chính tả sau.

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
  render.py      # dựng hình, đếm ngược, chữ và hai file MP4
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

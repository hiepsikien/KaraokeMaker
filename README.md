# KaraokeMaker

KaraokeMaker căn lời vào file vocal, cho sửa thời gian từng chữ, rồi xuất hai video karaoke dùng chung hình nền và trang bìa: một bản có giọng và một bản không lời.

Giao diện là ứng dụng web. Phần căn lời và dựng video chạy bằng Python. Ứng dụng viết cho macOS: hộp thoại chọn file dùng AppleScript, mở thư mục dùng Finder, chữ Việt dùng font Arial Unicode, và GPU căn lời là Apple MPS.

Mã tách giọng của [Ultimate Vocal Remover GUI](https://github.com/anjok07/ultimatevocalremovergui) vẫn nằm trong repo này. KaraokeMaker dùng các file vocal và instrumental đã tách sẵn, và không tự gọi UVR.

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

1. Nhập tên bài hát và tác giả. Dòng phụ trên bìa là tùy chọn.
2. Chọn audio gốc, file vocal và file instrumental.
3. Dán lời bài hát.
4. Chọn ảnh bìa hoặc ảnh nền. Khung video là 1920×1080. Ảnh đúng tỉ lệ này đầy khung. Ảnh khác tỉ lệ nằm giữa và có viền tối hai bên.
5. Chọn thư mục xuất, rồi bấm **Căn lời**.
6. Khi căn xong, sửa trên timeline: sóng âm ở trên, câu hát ở dưới. Kéo cả câu để dời. Kéo mép để đổi chỗ bắt đầu và kết thúc. Bấm một câu để sửa từng chữ. **Phát** và **Tạm dừng** nằm trên timeline. Bấm một câu hoặc một chữ thì nghe từ chỗ đó.
7. **Thêm câu** chèn một câu mới sau câu đang chọn. **Chia câu** cắt câu đang chọn thành hai, tại chữ đang chọn hoặc tại vạch phát.
8. **Lưu bản sửa** ghi ngay file thời gian, không cần đợi xuất video.
9. Lần sau, chọn file vocal rồi bấm **Mở bản đã sửa** và chọn file `timing.json`. Trang sửa mở lại đúng thời gian đã lưu. Whisper không chạy lại.
10. **Xuất hai video** cần audio gốc, instrumental, và ảnh bìa hoặc ảnh nền. Nếu còn thiếu, nút xuất bị khóa. Hãy chọn đủ file rồi mở lại bản đã sửa.

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

Căn lời dùng file vocal. Model mặc định là `small`, chạy trên CPU. Có thể chọn `tiny`, `base`, `small` hoặc `medium`, và chuyển sang GPU Apple (MPS). Lần đầu máy tải model Whisper về, các lần sau dùng lại bản đã tải.

Sau khi căn, thời gian từng chữ được kéo về đoạn đang hát, kể cả tiếng ngân. Việc này chạy một lần trong lúc căn. Mở bản đã sửa không căn lại, để giữ các chỗ đã sửa tay. Vocal không phải WAV 16-bit, kể cả mp3 và flac, được chuyển tạm trước khi đo tiếng hát. Câu không đo được thì giữ nguyên thời gian, và tiến trình ghi một dòng tóm tắt.

## Video xuất ra

Với bài tên `Bình Ngô Đại Cáo`, thư mục xuất có:

- `Bình Ngô Đại Cáo (có giọng).mp4` dùng audio gốc
- `Bình Ngô Đại Cáo (không lời).mp4` dùng instrumental
- `Bình Ngô Đại Cáo timing.json` là thời điểm từng chữ

Nếu bản sửa được mở từ một file thời gian có sẵn, lần lưu và lần xuất đều ghi vào file đó.

Trang bìa hiện trước, mặc định 5 giây, với ảnh bìa, tên bài và tác giả. Tiếng được lùi đúng bằng thời lượng bìa, nên chữ bắt đầu đổi màu khi bài hát bắt đầu. Ảnh nền đổi lần lượt, mặc định mỗi ảnh 8 giây. Nếu không chọn ảnh nền, ảnh bìa được dùng làm nền.

Câu hát nằm ở cuối khung hình. Chữ chưa hát màu trắng, chữ đang hát được tô vàng dần. Hết câu thì chuyển sang câu sau. Trước câu đầu và trước câu sau một khoảng trống từ 8 giây, 5 giây cuối hiện sẵn câu đó cùng số đếm.

## Mã nguồn

```text
karaoke/
  access.py      # thư mục được phép đọc
  server.py      # API, chỉ trên 127.0.0.1
  pipeline.py    # kiểm tra file, mở bản đã sửa, căn lời, xuất video
  align.py       # căn lời vào vocal và kéo chữ về đoạn đang hát
  lyrics.py      # bỏ dòng ghi chú, so khớp tên bài
  media.py       # ảnh, thời lượng, chuyển vocal sang WAV
  render.py      # dựng hình, đếm ngược, chữ và hai file MP4
web/
  src/app        # trang Next.js
  src/components/maker-form.tsx
  src/components/timing-editor.tsx
  src/components/align-timeline.tsx
requirements-karaoke.txt
```

Việc đang chạy được giữ trong bộ nhớ. Tắt server là mất trang sửa đang mở. File thời gian và video đã ghi vẫn còn trên đĩa.

## Tách giọng bằng UVR

Khi cần tách vocal và instrumental, dùng môi trường `.venv` của UVR, không dùng `.venv-karaoke`:

```bash
source .venv/bin/activate
python UVR.py
```

Hướng dẫn cài và dùng UVR nằm ở repo gốc: https://github.com/anjok07/ultimatevocalremovergui

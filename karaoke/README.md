# KaraokeMaker

KaraokeMaker ghép một bài hát đã tách giọng thành hai video karaoke: một bản có giọng và một bản không lời. Hai video dùng chung hình nền, trang bìa và chỗ đổi màu từng chữ.

Giao diện là ứng dụng Next.js. Phần căn lời và dựng video chạy bằng Python, vì cần Whisper và FFmpeg.

## Chạy

Cần hai terminal, cùng thư mục gốc của repo.

```bash
.venv-karaoke/bin/python -m karaoke
```

```bash
cd web
npm run dev
```

Mở http://127.0.0.1:3000. API Python lắng nghe ở http://127.0.0.1:8765. Next.js chuyển các đường dẫn `/api/*` sang API đó.

Nếu chưa có môi trường Python:

```bash
~/.pyenv/versions/3.11.15/bin/python3 -m venv .venv-karaoke
.venv-karaoke/bin/python -m pip install -r requirements-karaoke.txt
cd web && npm install
```

Python này phải là bản pyenv đã được dựng với Tk không bắt buộc cho KaraokeMaker, nhưng phải là Python 3.11 đang dùng trong repo. FFmpeg và ffprobe cần có trong `PATH`.

Môi trường `.venv` của UVR và `.venv-karaoke` tách riêng. UVR khóa `numpy` 1.23.5, còn thư viện căn lời cần bản mới hơn.

## Cách dùng

1. Nhập tên bài hát và tác giả. Dòng phụ trên bìa là tùy chọn.
2. Chọn audio gốc, file vocal và file instrumental.
3. Dán lời bài hát.
4. Chọn ảnh bìa và các ảnh nền sẽ được lặp đến hết bài.
5. Chọn thư mục xuất, rồi bấm **Tạo hai video karaoke**.

Chọn file vocal hoặc instrumental dạng `1_Tên bài_(Vocals).wav` thì ứng dụng tự điền file còn lại trong cùng thư mục, và tìm bài gốc cùng tên trong thư mục `Songs`. Chọn thẳng bài gốc thì ứng dụng lấy cặp vocal và instrumental mới nhất trong `Separated`.

Hộp thoại chọn file mở sẵn các thư mục này nếu chúng còn tồn tại:

- `/Users/andynguyen/Downloads/Audio /Songs`
- `/Users/andynguyen/Downloads/Audio /Separated/Kim2`
- `/Users/andynguyen/Downloads/Audio /Karaoke`

## Lời bài hát

Mỗi câu hát là một dòng. Dòng trống được bỏ qua. Dòng nằm trọn trong ngoặc vuông cũng được bỏ, vì đó là ghi chú sản xuất chứ không phải lời hát:

```text
[Verse 1]
[Tiếng trống trận]
Mười tám con người dưới cờ Lũng Nhai
```

Chỉ dòng thứ ba được căn giờ và hiện trên video.

Căn lời dùng file vocal, không dùng bản instrumental. Model mặc định là `small`, chạy trên CPU. Có thể chọn `tiny`, `base`, `small` hoặc `medium`, và chuyển sang GPU Apple (MPS). Lần đầu máy tải model Whisper về, các lần sau dùng lại bản đã tải.

## Video xuất ra

Với bài tên `Bình Ngô Đại Cáo`, thư mục xuất có:

- `Bình Ngô Đại Cáo (có giọng).mp4` dùng audio gốc
- `Bình Ngô Đại Cáo (không lời).mp4` dùng instrumental
- `Bình Ngô Đại Cáo timing.json` là thời điểm từng chữ, tính từ đầu bài hát

Trang bìa hiện trước, mặc định 5 giây, với ảnh bìa, tên bài và tác giả. Trong lúc đó tiếng được lùi đúng bằng thời lượng bìa, nên chữ bắt đầu đổi màu khi bài hát bắt đầu. Ảnh nền đổi lần lượt, mặc định mỗi ảnh 8 giây. Nếu không chọn ảnh nền, ảnh bìa được lặp lại.

Câu hát nằm ở cuối khung hình. Chữ chưa hát màu trắng, chữ đang hát được tô vàng dần.

## Cấu trúc

```text
karaoke/
  server.py      # API cho giao diện
  pipeline.py    # kiểm tra file và chạy cả việc
  align.py       # căn lyrics lên vocal.wav
  lyrics.py      # bỏ dòng ghi chú, so khớp tên bài
  media.py       # ảnh bìa, thời lượng, tìm file UVR
  render.py      # dựng hình, chữ và hai file MP4
web/
  src/app        # trang Next.js
  src/components/maker-form.tsx
requirements-karaoke.txt
```

`server.py` giữ việc đang chạy trong bộ nhớ và trả nhật ký cho trang web hỏi mỗi giây. Một việc tại một thời điểm là đủ cho máy cá nhân. Tắt server là mất nhật ký của việc đang xem, file đã xuất vẫn còn trong thư mục.

## Bước tiếp theo

Bản này nhận ba file audio có sẵn. Bước sau là gọi code tách giọng của UVR từ `karaoke/`, chạy trong môi trường `.venv` hiện tại, rồi đưa `vocal.wav` và `instrumental.wav` sang pipeline này. Phần dựng video không phụ thuộc giao diện UVR.

# Phần Python của KaraokeMaker

Cách chạy, cách dùng và file xuất ra nằm ở [README.md](../README.md) ở thư mục gốc.

Thư mục này là API, căn lời và dựng video:

```text
access.py    # thư mục được phép đọc
server.py    # API, chỉ trên 127.0.0.1:8765
pipeline.py  # kiểm tra file, mở bản đã sửa, căn lời, xuất video
align.py     # căn lời vào vocal và kéo chữ về đoạn đang hát
lyrics.py    # bỏ dòng ghi chú, so khớp tên bài
media.py     # ảnh, thời lượng, chuyển vocal sang WAV
living.py    # hiệu ứng nền động
assets.py    # asset clip Studio ổn định
background.py # dựng nền trước, overlay lời, ghép tiếng
render.py    # dựng chữ, đếm ngược, overlay và mux
```

Chạy từ thư mục gốc của repo:

```bash
.venv-karaoke/bin/python -m karaoke
```

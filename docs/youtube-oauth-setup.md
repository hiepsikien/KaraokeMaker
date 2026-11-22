# Đăng ký YouTube OAuth cho KaraokeMaker

Tài liệu này hướng dẫn tạo OAuth client để ứng dụng đăng video lên **kênh YouTube cá nhân** của bạn. Làm một lần, sau đó chỉ cần bấm **Kết nối YouTube** trong ứng dụng.

> Ứng dụng chạy local tại `http://127.0.0.1:8765`. Callback OAuth cố định:
> `http://127.0.0.1:8765/api/publish/oauth/callback`

---

## 1. Chuẩn bị

- Một tài khoản Google có kênh YouTube.
- Quyền truy cập [Google Cloud Console](https://console.cloud.google.com/).
- Ứng dụng KaraokeMaker đã chạy được server Python (cổng 8765).

---

## 2. Tạo Google Cloud project

1. Vào [Google Cloud Console](https://console.cloud.google.com/).
2. Góc trên chọn **Select a project** → **New project**.
3. Đặt tên, ví dụ `KaraokeMaker`, bấm **Create**.
4. Chọn project vừa tạo.

---

## 3. Bật YouTube Data API v3

1. Vào **APIs & Services** → **Library**.
2. Tìm **YouTube Data API v3**.
3. Bấm **Enable**.

Nếu chưa bật, upload sẽ lỗi `403 accessNotConfigured`.

---

## 4. Cấu hình OAuth consent screen

1. Vào **APIs & Services** → **OAuth consent screen** (một số tài khoản hiển thị dưới **Google Auth Platform**).
2. Chọn **External** (trừ khi bạn có Google Workspace nội bộ).
3. Điền:
   - **App name:** `KaraokeMaker`
   - **User support email:** email của bạn
   - **Developer contact email:** email của bạn
4. Ở bước **Scopes**, thêm:
   - `https://www.googleapis.com/auth/youtube.force-ssl` — bắt buộc để upload, đọc/ghi playlist và channel.
   - Không cần `youtube.readonly` nếu đã có `youtube.force-ssl`; xin cả hai chỉ làm tăng rủi ro verification.
5. Ở bước **Test users**, thêm chính email Google của bạn.
6. Lưu.

> **Testing vs Production:** ở chế độ **Testing**, refresh token thường hết hạn sau **7 ngày**. Nếu dùng lâu dài, bấm **Publish app** để chuyển sang Production. Với app cá nhân, có thể để Testing và kết nối lại khi hết hạn, nhưng sẽ phải làm lại thao tác kết nối.

---

## 5. Tạo OAuth client

### Cách khuyến nghị: Desktop app

1. Vào **APIs & Services** → **Credentials**.
2. Bấm **Create credentials** → **OAuth client ID**.
3. **Application type:** `Desktop app`.
4. Đặt tên, ví dụ `KaraokeMaker Desktop`.
5. Bấm **Create**.
6. Tải file JSON hoặc ghi lại **Client ID** và **Client secret**.

Google cho phép desktop app dùng loopback `http://127.0.0.1:<port>`; ứng dụng dùng cổng cố định `8765`.

### Nếu bạn chọn Web application

Nếu Console bắt buộc loại Web application, thêm đúng redirect URI:

```text
http://127.0.0.1:8765/api/publish/oauth/callback
```

Sai một ký tự (thiếu `/`, dùng `localhost` thay `127.0.0.1`, thêm dấu `/` cuối) sẽ lỗi `redirect_uri_mismatch`.

---

## 6. Cấu hình ứng dụng

Tạo/ sửa file `.env` ở thư mục gốc repo:

```bash
GOOGLE_OAUTH_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=xxxxxxxxxxxxxxxx
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8765/api/publish/oauth/callback
```

- `.env` đã nằm trong `.gitignore`; **không commit** client secret.
- Khởi động lại server Python sau khi sửa `.env`.
- Token người dùng được lưu riêng trong thư mục dữ liệu ứng dụng với quyền `0600`, không nằm trong file dự án.

---

## 7. Kết nối trong ứng dụng

1. Mở trang **Đăng bài**.
2. Bấm **Kết nối YouTube**.
3. Trình duyệt mở trang đồng ý của Google.
4. Chọn tài khoản/kênh YouTube của bạn.
5. Đồng ý các quyền đã liệt kê.
6. Quay lại ứng dụng; trạng thái hiển thị **Đã kết nối**.

Sau đó bạn có thể:
- Chọn playlist.
- Nhập title/body (emoji được giữ nguyên).
- Chọn **Trẻ em có xem được**.
- Chọn chế độ đăng, mặc định **Công khai**.
- Bấm **Đăng ngay**.

---

## 8. Quyền và giới hạn cần biết

- `youtube.force-ssl` là scope nhạy cảm; app ở Testing chỉ dùng được với các **Test users** đã thêm.
- Mỗi upload tốn khoảng **1.600 quota units**; mặc định **10.000 units/ngày** → khoảng **6 video/ngày**.
- YouTube còn giới hạn bucket **Video Uploads** khoảng **100 calls/ngày**.
- Nếu kênh đã đặt "made for kids" ở cấp kênh, API có thể không cho đổi theo từng video.
- Video karaoke cover có thể bị **Content ID claim**; người dùng chịu trách nhiệm về quyền bài hát.

---

## 9. Lỗi thường gặp

| Lỗi | Nguyên nhân | Cách sửa |
|---|---|---|
| `redirect_uri_mismatch` | Redirect không khớp | Đăng ký đúng `http://127.0.0.1:8765/api/publish/oauth/callback`, dùng `127.0.0.1` không dùng `localhost` |
| `access_denied` | Không đồng ý quyền hoặc email không nằm trong Test users | Thêm email vào Test users, thử lại |
| `invalid_grant` | Refresh token hết hạn/thu hồi | Ngắt kết nối rồi kết nối lại |
| `accessNotConfigured` | Chưa bật YouTube Data API v3 | Bật API trong Library |
| `quotaExceeded` | Hết quota ngày | Đợi reset hoặc dùng project khác |
| `insufficientPermissions` | Token thiếu scope (ví dụ thêm playlist sau) | Ngắt kết nối, kết nối lại để cấp `youtube.force-ssl` |

---

## 10. Checklist nhanh

- [ ] Đã tạo Google Cloud project.
- [ ] Đã bật YouTube Data API v3.
- [ ] Đã cấu hình OAuth consent screen + Test user.
- [ ] Đã thêm scope `youtube.force-ssl`.
- [ ] Đã tạo OAuth client (Desktop app hoặc Web với redirect đúng).
- [ ] Đã điền `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` vào `.env`.
- [ ] Đã khởi động lại server Python.
- [ ] Bấm Kết nối YouTube và thấy trạng thái **Đã kết nối**.

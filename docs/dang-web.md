# Đăng sản phẩm và bài viết lên thanhduc.vn tự động

Công cụ chạy trên máy chủ Z2, trong container riêng `dang-web` (không đụng app đang chạy):

1. Lấy mã, tên, nhóm, giá, bảo hành, tồn từ database app ở chế độ **chỉ đọc**.
2. Xử lý ảnh ngay trên Z2: xoay đúng chiều, tách nền, nền trắng, ảnh vuông 1200×1200, đóng logo Thành Đức góc dưới phải, nén WebP.
3. Claude viết tên web, mô tả ngắn, mô tả chi tiết, tiêu đề và mô tả SEO (Yoast), chữ thay thế cho ảnh; có tra web để lấy đúng máy tương thích.
4. Đăng lên WooCommerce ở trạng thái **Bản nháp**. Người duyệt mở wp-admin, sửa nếu cần, bấm Đăng.

Web thanhduc.vn vẫn ở hosting cũ, công cụ chỉ gọi REST API của web.

## Chuẩn bị một lần

### 1. Mật khẩu ứng dụng WordPress

1. Đăng nhập `https://thanhduc.vn/wp-admin` bằng tài khoản quản trị.
2. Vào **Người dùng → Hồ sơ**, kéo xuống mục **Mật khẩu ứng dụng** (Application Passwords).
3. Ô tên gõ `Z2 dang web`, bấm **Thêm mật khẩu ứng dụng mới**.
4. Chép dãy mật khẩu hiện ra (chỉ hiện một lần). Không gửi vào chat.

Không thấy mục đó: plugin bảo mật đang tắt tính năng này, cần bật lại trong cài đặt plugin bảo mật.

### 2. Khóa Claude API

1. Vào `https://console.anthropic.com` → **Settings → API Keys → Create Key**, đặt tên `Z2 dang web`.
2. Vào **Billing** nạp tiền, và **Limits** đặt hạn mức tháng (ví dụ 10 USD) cho yên tâm.
3. Chép khóa (bắt đầu bằng `sk-ant-`). Không gửi vào chat.

### 3. Ghi vào deploy/.env trên Z2

```bash
nano ~/THANHDUC-APP/deploy/.env
```

Thêm cuối file (thay phần trong ngoặc nhọn):

```
WP_USER=<tên đăng nhập wp-admin>
WP_APP_PASSWORD=<mật khẩu ứng dụng vừa tạo>
ANTHROPIC_API_KEY=<khóa sk-ant-...>
```

Lưu: Ctrl+O, Enter, Ctrl+X.

### 4. Lấy code mới và kiểm tra

```bash
cd ~/THANHDUC-APP && git pull origin main
scripts/dang-web.sh kiem-tra
```

Lần đầu dựng image mất vài phút. Cuối cùng phải thấy `TẤT CẢ OK`.

## Làm hằng ngày

### Bỏ ảnh vào

Thư mục làm việc trên Z2: `~/THANHDUC-WEB`.

- `anh-goc/`: ảnh gốc đặt tên theo mã hàng: `TD-0123.jpg`, ảnh phụ `TD-0123-2.jpg`, `TD-0123-3.jpg`. Hoặc tạo thư mục `TD-0123/` bỏ nhiều ảnh vào. Nhận jpg, png, webp, heic.
- `ghi-chu/TD-0123.txt` (không bắt buộc): thông tin chắc chắn muốn AI dùng, ví dụ máy tương thích, số trang in.

Chép ảnh từ máy Windows sang Z2 (PowerShell):

```powershell
scp C:\anh-web\* tuan@192.168.10.200:~/THANHDUC-WEB/anh-goc/
```

### Chạy thử (không đăng)

```bash
cd ~/THANHDUC-APP && scripts/dang-web.sh san-pham --ma TD-0123,TD-0124 --thu
```

Bản xem trước nằm ở `~/THANHDUC-WEB/xem-truoc/`, ảnh đã xử lý ở `~/THANHDUC-WEB/anh-da-xu-ly/`. Kéo về Windows để mở:

```powershell
scp -r tuan@192.168.10.200:~/THANHDUC-WEB/xem-truoc tuan@192.168.10.200:~/THANHDUC-WEB/anh-da-xu-ly C:\thanhduc-web\
```

Mở `C:\thanhduc-web\xem-truoc\TD-0123.html` bằng trình duyệt.

### Đăng nháp thật

```bash
cd ~/THANHDUC-APP && scripts/dang-web.sh san-pham --ma TD-0123,TD-0124
```

Mỗi mã in ra link duyệt `.../wp-admin/post.php?post=...`. Kết quả, chi phí và các điểm **cần kiểm tra** ghi trong `~/THANHDUC-WEB/ket-qua/<ngày>.md`.

Công cụ tự bỏ qua mã đã có trên web (trùng SKU = mã TD) và mã đã đăng trước đó. Muốn đăng lại thêm `--lam-lai`.

### Bài viết

```bash
scripts/dang-web.sh bai-viet --chu-de "Máy in Canon LBP 2900 bị kẹt giấy: nguyên nhân và cách xử lý" --thu
scripts/dang-web.sh bai-viet --chu-de "..."
```

Hoặc ghi mỗi dòng một chủ đề vào `~/THANHDUC-WEB/chu-de.txt` rồi chạy `scripts/dang-web.sh bai-viet --tu-dong --so 2`.

## Chạy theo lịch (sau khi thử ổn)

```bash
crontab -e
```

Thêm dòng (7 giờ sáng thứ 2 – thứ 7, mỗi lần 2 sản phẩm đã có ảnh mà chưa đăng):

```
0 7 * * 1-6 cd ~/THANHDUC-APP && scripts/dang-web.sh san-pham --tu-dong --so 2 >> ~/THANHDUC-WEB/nhat-ky.txt 2>&1
```

## Tùy chỉnh trong deploy/.env

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `WEB_AI_MODEL` | `claude-opus-5-5` | Model viết bài. `claude-sonnet-5-5` rẻ bằng nửa |
| `WEB_AI_TRA_WEB` | `true` | Cho AI tra web để lấy đúng máy tương thích |
| `WEB_TACH_NEN` | `true` | Tách nền ảnh (tắt nếu ảnh đã nền trắng sẵn) |
| `WEB_ANH_DINH_DANG` | `webp` | `jpg` nếu hosting không nhận WebP (công cụ cũng tự đổi khi bị từ chối) |
| `WEB_DANG_GIA` | `true` | Ghi giá bán vào sản phẩm nháp |
| `WEB_VAT_PHAN_TRAM` | `0` | Giá web = giá bán trong app cộng % này (làm tròn nghìn). Đặt 8 hoặc 10 nếu web để giá gồm VAT |
| `WEB_HOTLINE` | trống | Hotline AI chèn cuối bài |
| `WEB_CHUYEN_MUC_BAI` | trống | Tên chuyên mục cho bài viết, ví dụ `Tin tức` |
| `WEB_THU_MUC` | `~/THANHDUC-WEB` | Thư mục làm việc |

Ghép nhóm hàng với danh mục web: công cụ tìm danh mục trùng tên nhóm hàng (bỏ dấu). Tên khác nhau thì tạo `~/THANHDUC-WEB/nhom-danh-muc.json`, ví dụ `{"Hộp mực": "Hộp mực máy in laser"}`.

## Chi phí

Mỗi lần chạy in chi phí AI ước tính. Với Opus 5.5 có tra web, khoảng 2.000–5.000đ mỗi sản phẩm hoặc bài viết. Xử lý ảnh chạy trên Z2, không tốn phí.

## An toàn

- Database app: kết nối đặt `default_transaction_read_only=on`, không ghi được gì.
- Mọi sản phẩm, bài viết tạo ra đều là Bản nháp.
- Khóa nằm trong `deploy/.env` trên Z2, không lên GitHub.
- Không bao giờ chạy `docker volume prune`, `docker system prune --volumes`, `docker compose down -v`.

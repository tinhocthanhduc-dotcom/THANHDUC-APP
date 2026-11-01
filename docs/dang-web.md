# Đăng sản phẩm và bài viết lên thanhduc.vn tự động

Công cụ chạy trên máy chủ Z2, trong container riêng `dang-web` (không đụng app đang chạy):

1. Lấy mã, tên, nhóm, giá, bảo hành, tồn từ database app ở chế độ **chỉ đọc**.
2. Xử lý ảnh ngay trên Z2: xoay đúng chiều, tách nền, nền trắng, ảnh vuông 1200×1200, đóng logo Thành Đức góc dưới phải, nén WebP.
3. AI viết tên web, mô tả ngắn, mô tả chi tiết, tiêu đề và mô tả SEO (Yoast), chữ thay thế cho ảnh; có tra web để lấy đúng máy tương thích. Có 4 cách viết:
   - **ChatGPT API** (khóa `OPENAI_API_KEY`, mặc định khi chỉ có khóa này),
   - **Claude API** (khóa `ANTHROPIC_API_KEY`),
   - **ChatGPT web, dán tay** (`--chatgpt-web`, không tốn phí API),
   - **Mẫu cố định, không AI** (`--khong-ai`, miễn phí, chỉ cho sản phẩm).
4. Đăng lên WooCommerce ở trạng thái **Bản nháp**. Người duyệt mở wp-admin, sửa nếu cần, bấm Đăng.

Web thanhduc.vn vẫn ở hosting cũ, công cụ chỉ gọi REST API của web.

## Chuẩn bị một lần

### 1. Mật khẩu ứng dụng WordPress

1. Đăng nhập `https://thanhduc.vn/wp-admin` bằng tài khoản quản trị.
2. Vào **Người dùng → Hồ sơ**, kéo xuống mục **Mật khẩu ứng dụng** (Application Passwords).
3. Ô tên gõ `Z2 dang web`, bấm **Thêm mật khẩu ứng dụng mới**.
4. Chép dãy mật khẩu hiện ra (chỉ hiện một lần). Không gửi vào chat.

Không thấy mục đó: plugin bảo mật đang tắt tính năng này, cần bật lại trong cài đặt plugin bảo mật.

### 2. Khóa AI (một trong hai)

- **ChatGPT (OpenAI):** vào `https://platform.openai.com/api-keys` → **Create new secret key**, đặt tên `Z2 dang web`. Trong **Limits** đặt hạn mức tháng. Khóa bắt đầu bằng `sk-`.
- **Claude:** vào `https://console.anthropic.com` → **Settings → API Keys → Create Key**; nạp tiền ở **Billing**, đặt hạn mức ở **Limits**. Khóa bắt đầu bằng `sk-ant-`.

Không gửi khóa vào chat. Không có khóa nào vẫn dùng được `--chatgpt-web` hoặc `--khong-ai`.

### 3. Ghi vào deploy/.env trên Z2

```bash
nano ~/THANHDUC-APP/deploy/.env
```

Thêm cuối file (thay phần trong ngoặc nhọn):

```
WP_USER=<tên đăng nhập wp-admin>
WP_APP_PASSWORD=<mật khẩu ứng dụng vừa tạo>
OPENAI_API_KEY=<khóa sk-...>
```

(Dùng Claude thì thay dòng cuối bằng `ANTHROPIC_API_KEY=<khóa sk-ant-...>`.)

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

**Kiểm tra trùng trước khi gọi AI:** công cụ tìm trên web (cả bản nháp) sản phẩm hoặc bài viết có tên gần giống, ví dụ sản phẩm cũ chưa ghi mã TD, hoặc bài cùng máy cùng lỗi. Thấy giống thì **dừng mã đó, không tốn tiền AI**, in ra link bài trên web và ghi vào `ket-qua/<ngày>.md` mục `CÓ THỂ TRÙNG`. Mở link xem:

- Đúng là trùng: không làm gì, lần chạy tự động sau sẽ không lấy lại mã/chủ đề đó.
- Không trùng: chạy lại lệnh đó kèm `--van-dang`.

### Không dùng API

Mẫu cố định (miễn phí, nội dung đơn giản; nên tự thêm thông số khi duyệt):

```bash
scripts/dang-web.sh san-pham --ma TD-0123 --khong-ai
```

ChatGPT web, dán tay:

```bash
scripts/dang-web.sh san-pham --ma TD-0123 --chatgpt-web
```

Lần đầu công cụ soạn câu lệnh rồi dừng, in ra 3 bước: `cat ~/THANHDUC-WEB/chatgpt/TD-0123.txt` để chép câu lệnh dán vào chatgpt.com; `nano ~/THANHDUC-WEB/chatgpt/TD-0123-tra-loi.txt` dán câu trả lời của ChatGPT; chạy lại đúng lệnh đó để xử lý ảnh và đăng nháp.

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
| `WEB_AI_NHA_CUNG_CAP` | trống | `openai`, `claude` hoặc `mau` (không AI). Trống: dùng loại nào có khóa, có cả hai thì Claude |
| `WEB_OPENAI_MODEL` | `gpt-5.5` | Model ChatGPT |
| `WEB_AI_MODEL` | `claude-opus-5-5` | Model Claude. `claude-sonnet-5-5` rẻ bằng nửa |
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

Mỗi lần chạy in chi phí AI (Claude: số tiền ước tính; ChatGPT: số token, xem tiền trên trang OpenAI). Khoảng vài nghìn đồng mỗi sản phẩm hoặc bài viết. `--chatgpt-web` và `--khong-ai` không tốn phí API. Xử lý ảnh chạy trên Z2, không tốn phí.

## An toàn

- Database app: kết nối đặt `default_transaction_read_only=on`, không ghi được gì.
- Mọi sản phẩm, bài viết tạo ra đều là Bản nháp.
- Khóa nằm trong `deploy/.env` trên Z2, không lên GitHub.
- Không bao giờ chạy `docker volume prune`, `docker system prune --volumes`, `docker compose down -v`.

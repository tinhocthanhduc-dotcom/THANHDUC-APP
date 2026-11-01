# Đăng sản phẩm và bài viết lên thanhduc.vn tự động

Công cụ chạy trên máy chủ Z2, trong container riêng `dang-web` (không đụng app đang chạy):

1. Lấy mã, tên, nhóm, giá, bảo hành, tồn từ database app ở chế độ **chỉ đọc**.
2. Xử lý ảnh ngay trên Z2: xoay đúng chiều, tách nền (mô hình isnet-general-use, lần đầu tải ~180 MB), bỏ vết sót và bóng mờ, nền trắng tinh, cắt sát rồi đặt giữa ảnh vuông 1200×1200 với lề 4%, làm nét nhẹ, đóng logo Thành Đức góc dưới phải (sản phẩm che logo thì tự thu nhỏ tối đa ~14%), nén WebP.
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

## Dùng trên app (màn Đăng web, khuyên dùng)

Giám đốc mở menu **Quản lý → Website → Đăng web** trên điện thoại hoặc máy tính:

- **Sản phẩm:** bấm **+ Soạn sản phẩm**, tìm mã, chụp ảnh ngay bằng điện thoại (tối đa 6 ảnh), chọn cách viết, bấm **Soạn bản xem trước**. Vài phút sau việc nằm ở ô **Chờ anh xem trước**: xem ảnh nền trắng có logo, sửa tên, mô tả, giá, danh mục, tiêu đề Google ngay trên màn rồi bấm **Đăng nháp lên thanhduc.vn**.
- **Bài viết:** gõ chủ đề hoặc thêm vào danh sách **Chủ đề chờ viết**. Có thể chụp hoặc chọn tối đa 4 ảnh: ảnh đầu làm ảnh đại diện, các ảnh sau chèn vào thân bài (trước các tiêu đề mục).
- **Ảnh trong thân bài:** khi đăng, sản phẩm tự chèn tối đa 3 ảnh của nó vào mô tả chi tiết (ảnh đầu sau đoạn mở đầu, ảnh sau trước các tiêu đề mục). Nếu mô tả đã có ảnh thì giữ nguyên.
- **Ảnh đã có logo:** ảnh banner hoặc ảnh đã đóng logo Thành Đức sẵn thì tick **Ảnh đã có logo Thành Đức, không đóng thêm** ở màn soạn để khỏi bị hai logo.
- **Có thể trùng:** web đã có sản phẩm hoặc bài gần giống thì app dừng lại (chưa tốn tiền AI), anh mở link xem rồi chọn **Đúng là trùng, bỏ qua**, **Không trùng, vẫn soạn** hoặc **Viết lại đè lên** bài cũ.
- **Chờ dán ChatGPT:** chọn cách ChatGPT web thì bấm **Chép câu lệnh**, dán vào chatgpt.com, chép câu trả lời dán lại vào ô rồi bấm **Đọc câu trả lời**.
- **Đã đăng:** mọi thứ đã lên web, trạng thái Nháp hay Đang hiện lấy từ web. Bấm một dòng: **Sửa trên web** mở thẳng trang sửa trong wp-admin; **Viết lại bằng AI** soạn bản mới, xem trước rồi ghi đè lên đúng bài đó.
- **Hàng hóa:** so toàn bộ Hàng hóa trong app với sản phẩm đang có trên web, chia **Đã có trên web** (trùng mã TD ở ô SKU, hoặc app đã đăng), **Có thể đã có** (tên gần giống) và **Chưa có**. Ở "Có thể đã có" bấm **Mở** xem rồi chọn **Đúng là nó** hoặc **Không phải**. Ở "Chưa có" bấm **Soạn ›**, hoặc tick nhiều mã đã có ảnh trong `anh-goc` rồi bấm **Soạn các mã đã chọn** (tối đa 10 mã một lần).
- **Cập nhật từ web:** danh sách trên web và trạng thái Nháp/Đang hiện chỉ lấy lại khi anh bấm nút này (ở tab Đã đăng hoặc Hàng hóa), không tự chạy.
- **Máy tính:** danh sách hiện thành bảng; màn soạn và xem trước chia 2 cột (trái xem, phải sửa và bấm).
- **Tự soạn mỗi sáng:** bật ở cuối tab Sản phẩm; máy soạn sẵn các mã đã có ảnh trong `anh-goc` và chủ đề chờ viết, anh vẫn phải bấm Đăng.

Màn này cần **máy đăng web** chạy nền trên Z2 (bật một lần, tự chạy lại khi Z2 khởi động):

```bash
cd ~/THANHDUC-APP && git pull origin main
scripts/dang-web.sh bat-may
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.tunnel.yml --env-file deploy/.env up -d --build
```

Lệnh thứ hai tạo thư mục `~/THANHDUC-WEB` đúng quyền và dựng máy đăng web (lần đầu vài phút). Lệnh thứ ba là lệnh cập nhật app thường lệ, để app nối vào thư mục đó. Sau này code đăng web có thay đổi thì chạy lại `scripts/dang-web.sh bat-may`. Xem nhật ký: `scripts/dang-web.sh nhat-ky`. Tắt: `scripts/dang-web.sh tat-may`.

## Làm hằng ngày bằng lệnh (không qua app)

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
| `WEB_MO_HINH_TACH_NEN` | `isnet-general-use` | Mô hình tách nền; `u2net` là bản cũ |
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

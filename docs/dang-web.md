# Đăng sản phẩm và bài viết lên thanhduc.vn tự động

Công cụ chạy trên máy chủ Z2, trong container riêng `dang-web` (không đụng app đang chạy):

1. Lấy mã, tên, nhóm, giá, bảo hành, tồn từ database app ở chế độ **chỉ đọc**.
2. Xử lý ảnh ngay trên Z2: xoay đúng chiều, tách nền (mô hình isnet-general-use, lần đầu tải ~180 MB), bỏ vết sót và bóng mờ, nền trắng tinh, cắt sát rồi đặt giữa ảnh vuông 1200×1200 với lề 4%, làm nét nhẹ, đóng logo Thành Đức góc dưới phải (sản phẩm che logo thì tự thu nhỏ tối đa ~14%), nén WebP.
3. AI viết tên web, mô tả ngắn, mô tả chi tiết, tiêu đề và mô tả SEO (Yoast), chữ thay thế cho ảnh; có tra web để lấy đúng máy tương thích. Có 4 cách viết:
   - **ChatGPT API** (khóa `OPENAI_API_KEY`, mặc định khi chỉ có khóa này),
   - **Claude API** (khóa `ANTHROPIC_API_KEY`),
   - **ChatGPT web, dán tay** (`--chatgpt-web`, không tốn phí API),
   - **Mẫu cố định, không AI** (`--khong-ai`, miễn phí, chỉ cho sản phẩm).
4. Người xem trước trong app rồi bấm **Duyệt và đăng**: sản phẩm lên WooCommerce công khai luôn, không cần vào wp-admin (từ 08/11/2026). Muốn sửa thêm trên wp-admin thì bấm **Chỉ lưu nháp**. Máy không bao giờ tự đăng.

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

Giám đốc hoặc Kinh doanh mở menu **Bán hàng → Đăng web** trên điện thoại hoặc máy tính (bật/tắt Tự soạn mỗi sáng chỉ Giám đốc):

- **Sản phẩm:** bấm **+ Soạn sản phẩm**, tìm mã, chụp ảnh ngay bằng điện thoại (tối đa 6 ảnh; chưa có ảnh vẫn đăng được, bản nháp gắn nhãn **Chưa có ảnh**, bổ sung sau bằng Sửa bằng AI › Ảnh), chọn cách viết, bấm **Soạn bản xem trước**. Vài phút sau việc nằm ở ô **Chờ anh xem trước**: xem ảnh nền trắng có logo, sửa tên, mô tả, giá, danh mục, tiêu đề Google ngay trên màn rồi bấm **Duyệt và đăng lên thanhduc.vn** (khách thấy ngay, màn ghi tên người duyệt) hoặc **Chỉ lưu nháp**.
- **Bài viết:** tắt từ 08/11/2026 (Đợt 4 rút gọn), bài viết làm ở app **Dịch vụ SEO**. Việc bài viết còn sót hiện chung ở tab Sản phẩm và chỉ đăng nháp; bài cũ vẫn xem, sửa ảnh ở Trên web › Bài viết.
- **Ảnh trong thân bài:** khi đăng, sản phẩm tự chèn tối đa 3 ảnh của nó vào mô tả chi tiết (ảnh đầu sau đoạn mở đầu, ảnh sau trước các tiêu đề mục). Nếu mô tả đã có ảnh thì giữ nguyên.
- **Ảnh đã có logo:** ảnh banner hoặc ảnh đã đóng logo Thành Đức sẵn thì tick **Ảnh đã có logo Thành Đức, không đóng thêm** ở màn soạn để khỏi bị hai logo.
- **Có thể trùng:** web đã có sản phẩm hoặc bài gần giống thì app dừng lại (chưa tốn tiền AI), anh mở link xem rồi chọn **Đúng là trùng, bỏ qua**, **Không trùng, vẫn soạn** hoặc **Viết lại đè lên** bài cũ.
- **Chờ dán ChatGPT:** chọn cách ChatGPT web thì bấm **Chép câu lệnh**, dán vào chatgpt.com, chép câu trả lời dán lại vào ô rồi bấm **Đọc câu trả lời**.
- **Trên web:** toàn bộ sản phẩm và bài viết đang có trên thanhduc.vn (cả nháp), chia theo cây danh mục, có mục **Chưa phân loại**. Lọc thêm: Khớp mã app, Không có mã TD, Nháp, Chưa có ảnh (sản phẩm); Đang hiện, Nháp, App đã đăng (bài viết). Mỗi dòng có **Sửa bằng AI**, **Xem ↗** và **wp-admin ↗**.
- **Sửa bằng AI:** tick phần cần sửa (Viết lại nội dung, Tiêu đề + mô tả Google, Ảnh) và/hoặc chọn danh mục mới. Không chọn ảnh mới thì máy tải ảnh đang có trên web về xử lý lại (nền trắng, logo). Sản phẩm không có mã TD thì AI dựa vào nội dung đang có và giữ giá web. Xem trước rồi bấm ghi đè: chỉ ghi phần đã tick, giữ nguyên trạng thái và đường dẫn.
- **Xếp danh mục bằng AI:** ở mục Chưa phân loại bấm nút này, AI đề xuất danh mục cho tối đa 40 mục một lượt; dòng chắc chắn tick sẵn, dòng khác anh chọn tay, bấm ghi thì chỉ đổi danh mục của các dòng đã tick (cần khóa AI).
- **Mã app chưa lên web / Có thể trùng** (trong Trên web › Sản phẩm, thay tab Hàng hóa cũ): mã trong app chưa có trên web thì bấm **Soạn ›** hoặc tick nhiều mã rồi bấm **Soạn các mã đã chọn** (tối đa 10 mã, có ảnh trong `anh-goc` thì dùng, không có vẫn đăng dạng chữ). Ở "Có thể trùng" mở xem rồi chọn **Đúng là nó** hoặc **Không phải**.
- **Cập nhật từ web:** danh sách trên web, danh mục và trạng thái Nháp/Đang hiện chỉ lấy lại cả loạt khi anh bấm nút này ở tab Trên web. Riêng khi mở màn Sửa ảnh hoặc Sửa bằng AI, máy tự lấy lại đúng mục đang mở (vừa lấy trong 2 phút thì thôi) để không bị khóa nút vì dữ liệu cũ (đợt 1, 02/11/2026).
- **Ghép mã TD (đợt 2, 02/11/2026):** Trên web › Sản phẩm › nút “Ghép mã TD”. Mỗi sản phẩm web chưa mang mã TD trong ô SKU được gợi ý mã giống nhất (chắc ≥ 90% / cần xem / không có gợi ý). Bấm Đúng, tick nhiều rồi “Đúng hết”, Chọn mã khác, hoặc ⋯ › Không có trong app. Mã ghép lưu trong `xac-nhan-web.json` (mỗi mã một sản phẩm web), “không có trong app” trong `bo-ghep.json`. Bấm “Ghi N mã lên web” tạo một việc `ghi_sku`: máy đăng web chỉ ghi ô SKU (WooCommerce batch, 100 dòng/lần), không đổi tên, giá, ảnh, trạng thái; mã trùng báo lỗi từng dòng. Chưa đồng bộ giá, còn hàng.
- **Hàng mới nhập chưa có trên web:** ô đầu tab Sản phẩm liệt kê mã nhập kho bán trong 30 ngày (đơn mua, sản xuất, phiếu nhập tay; không tính tồn đầu kỳ, hàng trả), còn tồn, chưa có trên web; tick rồi Soạn.
- **Sức khỏe Codex:** dải nhỏ đầu màn Đăng web báo Codex còn chạy không, lần hỏi việc gần nhất, còn đăng nhập ChatGPT không, số việc chờ và lỗi gần nhất.
- **Máy tính:** danh sách hiện thành bảng; màn soạn và xem trước chia 2 cột (trái xem, phải sửa và bấm).
- **Tự soạn mỗi sáng:** bật ở cuối tab Sản phẩm; máy lấy trước **Hàng mới nhập chưa có trên web** (nhập 30 ngày, còn tồn, đã có ảnh trong `anh-goc`), mới nhất trước, còn chỗ thì lấy mã khác có ảnh mà chưa có trên web. Máy chỉ soạn, anh vẫn phải bấm Duyệt và đăng.
- **Nhắc qua Zalo:** n8n trên Z2 hỏi cổng chỉ đọc `GET /n8n/viec-cho` (khóa n8n ở Quản trị › Trợ lý AI) lúc 8:00 và 16:00 rồi nhắn việc chờ kèm link. Cài: `docs/n8n-dang-web.md`.

Màn này cần **máy đăng web** (container `dang-web-may`) chạy nền trên Z2. Từ 02/11/2026 máy nằm trong `deploy/docker-compose.yml`, nên lệnh cập nhật app thường lệ tự dựng lại và bật nó:

```bash
cd ~/THANHDUC-APP && git pull origin main
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.tunnel.yml --env-file deploy/.env up -d --build
```

Máy mới cài lần đầu thì chạy `scripts/dang-web.sh bat-may` một lần trước lệnh cập nhật để tạo thư mục `~/THANHDUC-WEB` đúng quyền. Xem nhật ký: `scripts/dang-web.sh nhat-ky`. Tắt tạm: `scripts/dang-web.sh tat-may` (lần cập nhật sau sẽ bật lại).

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
- Sản phẩm chỉ lên công khai khi người bấm Duyệt và đăng trong app (máy kiểm tra có tên người duyệt); còn lại, kể cả bài viết, đều là Bản nháp.
- Khóa nằm trong `deploy/.env` trên Z2, không lên GitHub.
- Không bao giờ chạy `docker volume prune`, `docker system prune --volumes`, `docker compose down -v`.

## Ghép mã: ô "Đã có mã TD" (02/11/2026)
Ô thứ 5 liệt kê sản phẩm web đã có SKU là mã TD. Nút **Bỏ ghép** xóa liên kết trong app và tạo việc `ghi_sku` với `ma: ""` để xóa SKU trên web, sau đó sản phẩm quay lại danh sách gợi ý. Sản phẩm do app đăng không bỏ ghép được.

## Vẽ ảnh bài viết theo dàn ý (02/11/2026)
Mỗi lần Codex vẽ 2 ảnh (TUẤN 05/11), mỗi ảnh một hướng ý tưởng khác nhau lấy ngẫu nhiên (`ve_anh.HUONG_BAI`), Vẽ lại thì tránh hướng của lần trước và cấm cảnh rập khuôn thợ cầm hộp mực cạnh máy in. Bài mới: máy đăng web viết bài xong mới gửi yêu cầu vẽ, kèm dàn ý H1/H2/H3 và đoạn mở bài (`ve_anh.dan_y_bai`), để ảnh 1 minh họa tiêu đề bài, các ảnh còn lại minh họa từng mục H2. Bài đã có trên web lấy dàn ý từ `dan_y` trong danh sách bài (làm mới khi mở Sửa ảnh).

## Sửa ảnh chia 3 nhóm (TUẤN duyệt mẫu D8/8 ngày 02/11/2026)
- Sản phẩm: ① Ảnh đại diện (ảnh đầu của `images`), ② Ảnh album (các ảnh sau), ③ Ảnh trong mô tả (`anhs_mo_ta` đọc từ `description`, chỉ có khi lấy lại 1 sản phẩm lúc mở Sửa ảnh). Bài viết: ① và ③ Ảnh trong bài.
- Form gửi `vai_<key>`, `vitri_<key>` (số thứ tự mục H2, rỗng = cuối), `xoa_<key>`. Ảnh mô tả/trong bài bị bỏ nằm trong `bo_trong_bai` (src) và bị gỡ khỏi chữ bằng `xoa_anh_trong_bai`.
- Việc mới có `ba_nhom: true`: máy ghi `images` = đại diện + album; ảnh nhóm ③ chỉ đặt vào mô tả (`chen_sau_h2`), thay tại chỗ nếu là ảnh cũ. Việc cũ không có vai 3 nhóm vẫn chạy kiểu cũ.
- Ảnh trong mô tả trùng ảnh đại diện (cùng media id hoặc cùng file gốc bỏ đuôi -WxH) được đánh dấu và tick Bỏ sẵn.
- Soạn/sửa sản phẩm bằng AI không chèn ảnh đại diện vào mô tả nữa (chỉ ảnh thứ 2 trở đi).
- Codex vẽ ảnh cho mô tả sản phẩm: `ve_anh.tao(loai="san_pham")`, lệnh `lenh_ve_sp`; yêu cầu tách theo `loai` nên id sản phẩm và id bài viết không lẫn.

## Tên file và alt khi Sửa ảnh (TUẤN 02/11/2026)
- Ảnh có chỉnh hoặc ảnh mới tải lên web với tên `<slug-tên>-N.webp` (`ten_web`); file trên Z2 vẫn kèm mã việc để không đè nhau.
- Alt theo nhóm (`MayDangWeb._alt_nhom`): ① tên; ② tên + "góc N"; ③ tên – mục H2 chứa ảnh (`h2_truoc` cho ảnh cũ, mục anh chọn cho ảnh mới).
- Ô "Cập nhật alt cho tất cả ảnh" (`cap_nhat_alt`, tick sẵn): ảnh giữ nguyên chỉ đổi chữ alt qua `wp/v2/media/{id}` (`sua_alt`), ảnh dùng chung với sản phẩm khác thì bỏ qua; ảnh trong mô tả/bài đổi alt ngay trong HTML (`doi_alt_trong_bai`). Chỉ tick ô này mà không đổi gì khác vẫn tạo được việc.

## Bài mạng xã hội (TUẤN duyệt mẫu M1–M3, D1–D2 ngày 07/11/2026)
Tab **Mạng xã hội** trong màn Đăng web. Chọn một bài hoặc sản phẩm đã có trên web (danh sách lấy theo lần bấm
"Cập nhật từ web" gần nhất), chọn mẫu ảnh và cỡ. Máy đăng web lấy nội dung trên web, AI viết 3 bản
(Facebook 100–200 chữ, Zalo OA 50–100 chữ, Google Business dưới 1.500 ký tự, không ghi giá, có hotline), rồi ghép ảnh.
Anh sửa từng bản, bấm **Chép chữ**, **Tải ảnh**, dán tay lên từng kênh rồi bấm **Đã đăng** cho kênh đó; đủ 3 kênh thì việc
thành Đã đăng. App không tự đăng lên mạng.

- Ảnh: `dang_web/anh_mxh.py` (Pillow, font Be Vietnam Pro trong `dang_web/font/`, giấy phép OFL). 4 mẫu: Sản phẩm (ảnh
  sản phẩm trên web), Trước và sau (đúng 2 ảnh thợ chụp, tải lên lúc soạn), Mẹo sửa lỗi (ảnh đại diện của bài), Ưu đãi
  (chỉ chữ). Cỡ vuông 1080×1080, dọc 1080×1350 hoặc cả hai. AI không vẽ chữ.
- Khung Canva: chép PNG vào `~/THANHDUC-WEB/mau-anh/<mẫu>-<cỡ>.png` (ví dụ `truoc_sau-vuong.png`, `meo-doc.png`), cùng
  cỡ ảnh, chừa trong suốt vùng ảnh và vùng chữ theo `anh_mxh.vung()`: vùng ảnh từ trên 150px tới trên dải chữ 40px,
  lề 64px; dải chữ cao 250px (vuông) hoặc 300px (dọc) ở dưới cùng. Có khung thì máy dùng khung, không có thì dùng khung dựng sẵn.
- "Đổi chữ hoặc mẫu, ghép lại ảnh" chỉ ghép lại ảnh, không gọi AI. "Viết lại cả 3 bản" gọi AI lại và bỏ đánh dấu kênh.

# Gọi API từ app Apps Script

Mỗi app Apps Script dùng một tài khoản riêng, chỉ có vai trò nó cần, ví dụ `app-dieuphoi` vai trò `dispatcher`.
Mật khẩu để trong Script Properties (`TD_CORE_USER`, `TD_CORE_PASSWORD`), không viết trong code.

Mọi lỗi nghiệp vụ trả về dạng `{"error": "MA_LOI", "message": "câu tiếng Việt", "details": {...}}`.

## Vai trò

| Vai trò | Được làm |
| --- | --- |
| `sales`, `reception`, `dispatcher` | Tạo, sửa khách (trừ công nợ) |
| `finance` | Như trên, cộng công nợ, ngừng khách, xem lịch sử sửa |
| `warehouse` | Tạo, sửa danh mục hàng mã TD |
| `technician` | Chỉ xem |
| `director` | Mọi quyền nghiệp vụ |
| `admin` | Tạo tài khoản |

Mọi tài khoản đăng nhập đều xem được khách và hàng.

## Các đường dẫn

| Phương thức | Đường dẫn | Việc |
| --- | --- | --- |
| POST | `/auth/login` | Đăng nhập (form `username`, `password`), nhận `access_token` 12 giờ |
| GET | `/customers?q=` | Tìm khách không dấu theo tên, mã KTD, mã cũ, SĐT, MST (tối đa 20) |
| GET | `/customers/{ma}` | Lấy khách theo mã KTD hoặc mã cũ; khách đã gộp trả về khách đích |
| POST | `/customers` | Tạo khách `{"customer": {...}, "confirmation": {...}}` |
| PATCH | `/customers/{ma}` | Sửa, bắt buộc gửi `version` |
| GET | `/customers/{ma}/history` | Lịch sử sửa (kế toán, giám đốc) |
| GET | `/products?q=` | Tìm hàng theo mã TD, tên, nhóm |
| POST, PATCH | `/products`, `/products/{ma}` | Kho quản lý danh mục |
| POST | `/users` | Admin tạo tài khoản |

Danh sách đầy đủ, có thể bấm thử: `https://api.<ten-mien>/docs`.

## Mã lỗi khi tạo, sửa khách

| Mã | Ý nghĩa | Giao diện nên làm |
| --- | --- | --- |
| `DUPLICATE_TAX` | MST đã thuộc khách khác (`details.existing`) | Mở khách đó |
| `DUPLICATE_PHONE` | SĐT đã thuộc khách cá nhân khác | Mở khách đó |
| `PHONE_IN_USE` | SĐT trùng khách khác | Hỏi người dùng; nếu đúng là khách khác, gửi lại kèm `confirmation: {"mode": "CREATE_SEPARATE", "reason": "..."}` |
| `MISSING_PHONE`, `MISSING_IDENTITY` | Cá nhân thiếu SĐT; công ty thiếu cả MST lẫn SĐT | Báo thiếu |
| `VERSION_CONFLICT` | Người khác vừa sửa | Tải lại khách |
| `FIELD_FORBIDDEN` | Không có quyền sửa công nợ | Ẩn ô công nợ |

Kết quả tạo thành công có thể kèm `warnings` như `SIMILAR_NAME` (trùng tên, chỉ nhắc).

## Mẫu code Apps Script

```
function tdCore_(method, path, body) {
  const base = 'https://api.<ten-mien>';
  const cache = CacheService.getScriptCache();
  let token = cache.get('TD_CORE_TOKEN');
  if (!token) {
    const p = PropertiesService.getScriptProperties();
    const r = UrlFetchApp.fetch(base + '/auth/login', {
      method: 'post',
      payload: { username: p.getProperty('TD_CORE_USER'), password: p.getProperty('TD_CORE_PASSWORD') },
    });
    token = JSON.parse(r.getContentText()).access_token;
    cache.put('TD_CORE_TOKEN', token, 6 * 60 * 60);
  }
  const res = UrlFetchApp.fetch(base + path, {
    method: method,
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token },
    payload: body ? JSON.stringify(body) : undefined,
    muteHttpExceptions: true,
  });
  const data = JSON.parse(res.getContentText() || 'null');
  if (res.getResponseCode() >= 400) {
    const e = new Error(data.message || data.error);
    e.code = data.error;
    e.details = data.details;
    throw e;
  }
  return data;
}

// Ví dụ: tdCore_('get', '/customers?q=' + encodeURIComponent('minh phat'))
```


## Phiếu kho nháp (K3)

Tất cả tài khoản đã đăng nhập được xem; chỉ `warehouse` và `director` được tạo, sửa và hủy nháp.

| Phương thức | Đường dẫn | Việc |
| --- | --- | --- |
| GET | `/stock-docs` | Lọc `kind`, `warehouse` (kho nguồn), `status`, `from`, `to` (ngày ISO, gồm cả hai đầu) |
| GET | `/stock-docs/{id}` | Đầu phiếu cùng `lines`, sắp theo `line_no` |
| POST | `/stock-docs` | Tạo `NHAP`, cấp số và ghi audit trong cùng transaction |
| PATCH | `/stock-docs/{id}` | Bắt buộc `version`; chỉ trường gửi lên thay đổi; nếu gửi `lines` thì thay toàn bộ dòng |
| POST | `/stock-docs/{id}/cancel` | Bắt buộc `reason` không rỗng; có thể gửi `version`; chuyển `NHAP` sang `DA_HUY` |

Đầu vào tạo phiếu gồm `kind`, `warehouse_code`, `doc_date`, `lines`; có thể thêm `to_warehouse_code`, `supplier_code`, `customer_code`, `reason`, `note`.
Mỗi dòng gồm `product_code`, `qty`, `unit_cost`, `serial`, `note`. API tự cấp `id`, `line_no` từ 1; không nhận tên hàng thay cho mã.

- `PN` bắt buộc nhà cung cấp đang dùng và `unit_cost` nguyên đồng không âm.
- `CK` bắt buộc kho đích đang dùng, khác kho nguồn; các loại khác không có kho đích.
- `PX` và `CK`: `unit_cost` phải null hoặc không gửi; K4 sẽ tính khi ghi sổ.
- `KK`: `qty` có thể bằng 0, `unit_cost` tùy chọn (null hoặc số nguyên không âm), dùng cho kiểm kê đầu kỳ.
- Các loại khác cần `qty > 0`. Số lượng tối đa 14 chữ số, trong đó tối đa 3 chữ số thập phân; trả JSON dạng chuỗi để giữ chính xác.
- Phiếu phải có ít nhất một dòng. Kho và mã hàng phải tồn tại, đang dùng/đang bán.
- PATCH giữ nguyên số phiếu. Đổi `kind` hoặc năm của `doc_date` trả `NUMBER_LOCKED` (409); cần hủy nháp và lập phiếu mới. Đổi ngày trong cùng năm vẫn được.
- Không có API xóa, ghi sổ hoặc đảo sổ ở K3. Hủy nháp giữ nguyên dòng và số phiếu.

Lỗi nghiệp vụ: `BAD_TRANSFER`, `BAD_WAREHOUSE`, `BAD_SUPPLIER`, `BAD_CUSTOMER`, `BAD_PRODUCT` (danh sách `details.invalid_codes`), `BAD_QTY`, `BAD_UNIT_COST`, `VERSION_CONFLICT`, `NUMBER_LOCKED`, `DOC_POSTED`, `DOC_VOID`.
Các đầu vào sai kiểu, thiếu trường bắt buộc hoặc ngoài giới hạn trả 422. Phiếu đã ghi sổ hoặc hủy không được sửa/hủy nháp.

## Phiếu dịch vụ (D2)

Màn hình Dịch vụ chỉ gọi các hàm trong `app/service_tickets.py` (hoặc các đường dẫn dưới đây). Quyền kiểm ở lõi.

| Phương thức | Đường dẫn | Ghi chú |
|---|---|---|
| GET | `/service-tickets` | Lọc `status`, `assignee_id`, `overdue=true` (quá hạn lâu nhất lên đầu), `q` (số phiếu, serial, tên khách không dấu). Thợ chỉ nhận phiếu của mình |
| GET | `/service-tickets/{id}` | Kèm `steps` (lịch sử). Thợ xem phiếu người khác trả 404 |
| POST | `/service-tickets` | `customer_code`, `kind`, `symptom`, tùy chọn `priority`, `site_id`, `serial`. Điều phối, kinh doanh, lễ tân, giám đốc |
| POST | `/service-tickets/{id}/assign` | `assignee_id` (phải có vai trò `technician`), `version`, `note`. Chỉ điều phối; giao lại cũng là cách duyệt xin chuyển việc |
| POST | `/service-tickets/{id}/transition` | `to_status`, `version`, `note`, `appointment_at` (bắt buộc khi sang `DA_HEN`, phải có múi giờ) |
| POST | `/service-tickets/{id}/transfer-request` | Thợ đang được giao xin chuyển việc, `reason` bắt buộc |
| POST | `/service-tickets/{id}/transfer-reject` | Điều phối từ chối xin chuyển việc |

Luồng: `MOI` → (giao) `DA_GIAO` → `DA_NHAN` → `DA_HEN` (dời hẹn: `DA_HEN` → `DA_HEN`) → `DANG_LAM` → `HOAN_THANH`.
`DANG_LAM` ↔ `CHO_LINH_KIEN`. `KHONG_LAM_DUOC` từ `DA_NHAN`, `DA_HEN`, `DANG_LAM`, `CHO_LINH_KIEN`, bắt buộc lý do. `DA_HUY` chỉ điều phối, từ mọi trạng thái đang mở, bắt buộc lý do.
Hạn `due_at` tính lại mỗi lần chuyển theo bảng trong `app/service_sla.py`.

Lỗi: `FORBIDDEN`, `NOT_FOUND`, `BAD_CUSTOMER`, `BAD_ASSIGNEE`, `SAME_ASSIGNEE`, `BAD_STATUS`, `BAD_TRANSITION` (kèm `details.allowed`), `REASON_REQUIRED`, `APPOINTMENT_REQUIRED`, `BAD_APPOINTMENT`, `TRANSFER_PENDING`, `NO_TRANSFER`, `VERSION_CONFLICT`.

## Tiền và vật tư dịch vụ (D6)

| Phương thức | Đường dẫn | Ghi chú |
|---|---|---|
| POST | `/service-tickets/{id}/charges` | `billing` (`THU_TIEN` hoặc `MIEN_PHI` kèm `free_reason`), `lines`, `version`. Thay toàn bộ dòng; chỉ khi phiếu còn mở; thợ được giao hoặc điều phối |
| GET | `/service-tickets/receivables` | Danh sách cần thu (`from`, `to` theo ngày hoàn thành). Điều phối, kế toán, giám đốc |

Mỗi dòng: `kind` `PHI` (bắt buộc `description`, không có mã hàng, kho) hoặc `VAT_TU` (bắt buộc `product_code` đang bán và `warehouse_code` đang dùng), `qty` > 0, `unit_price` nguyên đồng, `vat_rate` 0, 5, 8 hoặc 10.
`amount` = làm tròn(`qty` × `unit_price`), `vat_amount` = làm tròn(`amount` × `vat_rate` / 100), làm tròn nửa lên.

- Sang `HOAN_THANH`: bắt buộc đã chọn `billing` (`BILLING_REQUIRED`); `THU_TIEN` mà tổng bằng 0 trả `EMPTY_CHARGES`. Lõi ghi `subtotal`, `vat_total`, `total`, `receivable` (0 nếu miễn phí), `priced_at`, rồi tự lập và ghi sổ một phiếu `PX` cho mỗi kho có vật tư, gắn khách và số DV. Thiếu tồn trả `NEGATIVE_STOCK` và phiếu giữ nguyên `DANG_LAM`.
- Sau khi hoàn thành, sửa tiền trả `PRICE_LOCKED`. Điều phối có thể: mở lại (`DANG_LAM`, bắt buộc lý do), hủy (`DA_HUY`, lý do), cả hai đều hủy các phiếu `PX` (sổ kho ghi đảo) và mở khóa giá; hoặc đóng hồ sơ (`DA_DONG`), sau đó không đổi được nữa.
- Nhận đơn kiểm `site_id` thuộc đúng khách và đang dùng (`BAD_SITE`), `serial` có trong danh mục thiết bị và không ghi cho khách khác (`BAD_SERIAL`).

## Tệp đính kèm (D4) và ảnh nghiệm thu (D5)

Các thao tác tệp gọi `app.attachments.add`, `list_for`, `open_file`, `hide`; lõi kiểm quyền xem/ghi theo phiếu. Ảnh JPEG/PNG/WEBP được xử lý và lưu JPEG, PDF giữ nguyên. Không xóa tệp, chỉ ẩn kèm lý do. Giới hạn tải lấy từ `TD_MAX_UPLOAD_MB` (mặc định 15 MB).

| Phương thức | Đường dẫn | Ghi chú |
|---|---|---|
| GET | `/attachments?entity=service_ticket&entity_id={id}` | API Bearer; mặc định bỏ tệp đã ẩn |
| POST | `/attachments` | API Bearer, multipart: `entity`, `entity_id`, `tag`, `file` |
| GET | `/attachments/{id}/file` | Đọc inline; nhận Bearer hoặc cookie web. Vẫn kiểm quyền phiếu và trạng thái ẩn ở lõi. Không cache |
| POST | `/attachments/{id}/hide` | API Bearer, `reason`, `version`; quyền ẩn theo lõi D4 |
| POST | `/dich-vu/{id}/anh/tai` | Form web có CSRF, multipart `tag` (`TRUOC`, `SAU`, `KHAC`), `file`; hai tag ảnh chỉ nhận ảnh |
| POST | `/dich-vu/{id}/anh/{attachment_id}/an` | Form web có CSRF, `version`, `reason` bắt buộc; D5 chỉ cho điều phối/giám đốc, tệp phải thuộc đúng phiếu |
| GET | `/dich-vu/{id}/bien-ban` | Trang HTML A4, dùng In của trình duyệt để in hoặc lưu PDF; chỉ hiện ảnh `SAU` chưa ẩn |

Lỗi tệp: `BAD_ENTITY`, `BAD_TAG`, `EMPTY_FILE`, `FILE_TOO_LARGE`, `BAD_FILE_TYPE`, `NOT_FOUND`, `FORBIDDEN`, `FILE_MISSING`, `REASON_REQUIRED`, `ALREADY_HIDDEN`, `VERSION_CONFLICT`. Hoàn thành phiếu dịch vụ thiếu tệp `SAU` còn hiện trả `PHOTO_REQUIRED`; trang dịch vụ hiển thị “Cần chụp ảnh sau sửa trước khi hoàn thành”.

## Giao diện tiền và vật tư dịch vụ (D6)

- Trang phiếu có phần tiền gọi `app.service_charges.set_charges`; form gửi `version` và CSRF, thay toàn bộ dòng. Giá nhập có dấu chấm hàng nghìn được đọc thành số nguyên đồng. Không gửi tổng từ JavaScript vào nghiệp vụ; các số đã lưu lấy từ kết quả lõi. VAT/làm tròn trên trình duyệt chỉ là tạm tính.
- Phí và vật tư thêm/xóa ngay trên form. Vật tư tìm sống không dấu, chọn đúng mã, điền giá bán mặc định; kho và VAT là nút chạm. Khi tiền đang thay đổi chưa lưu, nút Hoàn thành nhắc lưu trước để không mất dữ liệu nhập.
- Sau hoàn thành, form tiền chỉ đọc; hiện PX do lõi sinh. Mở lại (bắt buộc lý do) và Đóng hồ sơ gọi luồng `service_tickets.transition` hiện có; không ghi kho trực tiếp.
- `/dich-vu/can-thu` và `/dich-vu/can-thu.csv` gọi `service_charges.receivables`, chỉ cho điều phối/kế toán/giám đốc. Lọc `from`, `to` theo ngày hoàn thành giờ Việt Nam; để trống là toàn bộ. CSV UTF-8 BOM có số DV, khách, ngày hoàn thành và số phải thu, dòng tổng cộng; tên khách có ký tự công thức được thoát.
- Lỗi tiền và tồn hiện bằng tiếng Việt, giữ dữ liệu form; thiếu tồn nêu mã, kho, tồn hiện có và số cần. File lõi, models và migrations giữ nguyên.

## Điều phối và báo cáo (D7)

Chỉ `dispatcher`/`director` được GET `/dieu-phoi`, `/dieu-phoi/bao-cao` và `/dieu-phoi/bao-cao.csv`; vai trò khác trả 403. Các trang và CSV chỉ đọc. Dashboard có `view=open|overdue|parts`; quá hạn là phiếu còn mở với `due_at < now`, xếp hạn cũ nhất trước.

Báo cáo nhận `from`, `to` dạng `YYYY-MM-DD`, tính trọn ngày theo giờ Việt Nam; mặc định hôm nay và 6 ngày trước. Phiếu hoàn thành/không làm được được tính theo bước kết thúc cuối cùng trong kỳ và thợ tại bước đó. Khi mở lại rồi hoàn thành lại (D6), chỉ tính lần hoàn thành cuối, không đếm trùng; bỏ phiếu đã hủy hoặc đang mở lại, vẫn tính phiếu hoàn thành đã đóng hồ sơ (`DA_DONG`). Thời gian xử lý trung bình chỉ dùng phiếu hoàn thành có đủ mốc: lần giao gần nhất cho thợ hoàn thành đến bước hoàn thành (kể cả thời gian chờ).

Tỷ lệ trễ dùng thêm **đọc** `audit_log`, theo phạm vi được cho phép cho D7: ghép từng bước với snapshot cùng transaction, hành động, người thực hiện và trạng thái để so thời điểm bước với `before.due_at`. Một phiếu đóng trong kỳ có ít nhất một bước vượt hạn cũ được tính trễ một lần. Mẫu số là số phiếu đóng xác định được trễ/đúng hạn; phiếu thiếu dữ liệu hạn và chưa xác nhận được trễ được đếm ở “Chưa xác định trễ”, không coi là đúng hạn. Không tái tính hạn lịch sử theo cấu hình SLA hiện tại.

CSV UTF-8 BOM gồm bảng theo thợ, dòng trống rồi bảng theo ngày, cùng số liệu với trang. Thời gian trung bình xuất theo phút; tỷ lệ theo %. Tên thợ có ký tự công thức được thoát khi xuất CSV. Không có endpoint ghi trong D7.

## Báo giá (B1)

Quyền: `sales`, `director` tạo và thao tác; `finance` chỉ xem; vai trò khác trả `FORBIDDEN` (403). API dùng Bearer, màn hình dùng cookie và CSRF như các trang hiện có.

- `GET /quotes?q=&status=`: tìm số BG hoặc tên khách không dấu, lọc trạng thái.
- `GET /quotes/{id}`: đầu báo giá và `lines`.
- `POST /quotes`: `customer_code`, `contact_name`, `valid_until` (mặc định ngày VN + 7), `note`, `lines` (có thể rỗng khi nháp).
- `PATCH /quotes/{id}`: `version`, `lines` thay toàn bộ; tùy chọn sửa khách, liên hệ, hạn và ghi chú khi còn nháp.
- `POST /quotes/{id}/send`, `/accept`, `/reject`, `/revise`: body `{ "version": 1 }`.
- `POST /quotes/{id}/cancel`: body `{ "version": 1, "reason": "Khách đổi nhu cầu" }`.

Mỗi dòng: `product_code` tùy chọn, `description`, `qty` (> 0, tối đa 3 số lẻ), `unit`, `unit_price` (số nguyên đồng >= 0), `vat_rate` thuộc `VAT_RATES` (0/5/8/10). Có mã hàng và bỏ giá thì lấy giá bán hiện tại; bỏ mô tả/đơn vị thì lấy từ hàng. Dòng tự do bắt buộc mô tả và giá. Khách/hàng phải đang dùng khi tạo, sửa hoặc gửi.

Tiền tính tại lõi: `amount = ROUND_HALF_UP(qty × unit_price)`, rồi `vat_amount = ROUND_HALF_UP(amount × vat_rate / 100)`. `subtotal`, `vat_total`, `total` là tổng các dòng; không nhận tổng do trình duyệt gửi. Mỗi thao tác kiểm version và ghi audit trước/sau. Tạo cấp số BG trong cùng transaction; lỗi không mất số. `revise` chỉ áp dụng bản đã gửi, tạo nháp mới chép nguyên dòng/giá, hạn mới +7 ngày; bản cũ hủy với lý do `Thay bằng BG-…`, liên kết `replaced_by_id`. Hai bản và hai audit được lưu nguyên tử.

Luồng: `NHAP → DA_GUI → CHAP_NHAN / TU_CHOI`; `NHAP / DA_GUI → DA_HUY`. Hết hạn chỉ hiện nhãn trên bản còn `DA_GUI`, không tự đổi trạng thái.

| Mã lỗi | HTTP | Ý nghĩa |
| --- | --- | --- |
| FORBIDDEN | 403 | Không có quyền xem/ghi |
| NOT_FOUND | 404 | Không tìm thấy báo giá |
| VERSION_REQUIRED | 400 | Thiếu phiên bản hợp lệ ở lõi/web (API schema trả 422 nếu thiếu/sai kiểu) |
| VERSION_CONFLICT | 409 | Người khác vừa sửa, cần tải lại |
| BAD_STATUS | 409 | Sai bước chuyển trạng thái (lọc trạng thái sai trả 400) |
| PRICE_LOCKED | 409 | Chỉ sửa dòng khi còn nháp |
| REASON_REQUIRED | 400 | Phải nhập lý do hủy |
| EMPTY_LINES | 400 | Không gửi được báo giá rỗng |
| BAD_CUSTOMER | 400 | Khách không tồn tại hoặc ngừng/gộp |
| BAD_PRODUCT | 400 | Mã hàng sai/ngừng bán; `details.invalid_codes` liệt kê mã |
| PRICE_REQUIRED | 400 | Dòng tự do thiếu đơn giá |
| DESCRIPTION_REQUIRED | 400 | Dòng tự do thiếu mô tả |
| AMOUNT_TOO_LARGE | 400 | Số tiền vượt BigInteger |
| BAD_PRICE / BAD_VAT / BAD_FORM | 400 | Dữ liệu form chưa hợp lệ, giữ dữ liệu đã nhập |

Màn hình: `/bao-gia`, `/bao-gia/moi`, `/bao-gia/{id}`, `/bao-gia/{id}/in`. Bản in A4 có tổng bằng chữ tiếng Việt. Thông tin công ty lấy cấu hình `TD_COMPANY_NAME`, `TD_COMPANY_ADDRESS`, `TD_COMPANY_TAX_CODE`, `TD_COMPANY_PHONE`, `TD_COMPANY_HOTLINE`; mặc định là thông tin Thành Đức đã cung cấp.

## Đơn bán (B2)

B2 gồm lõi và API, chưa có màn hình (B3). Bearer authentication như các API khác.

| Vai trò | Xem | Lập, sửa, chốt, hủy | Giao |
| --- | --- | --- | --- |
| sales, director | Có | Có | Có |
| warehouse | Có | Không | Có |
| finance | Có | Không | Không |
| Vai trò khác | Không | Không | Không |

### API và dữ liệu

- `POST /sales-orders` → 201: `customer_code`, `contact_name`, `delivery_address` (bỏ qua thì lấy địa chỉ khách), `note`, `lines` (nháp có thể rỗng). Cấp số DB theo ngày Việt Nam trong transaction lưu đơn.
- `POST /sales-orders/from-quote/{quote_id}` → 201: `version` **của báo giá**, `warehouses` tùy chọn là ánh xạ số dòng báo giá sang kho, ví dụ `{ "version": 3, "warehouses": { "1": "K58", "3": "K145" } }`. Chỉ từ `CHAP_NHAN`; sao chép giá, VAT, số lượng, mô tả, đơn vị, liên hệ và ghi chú, không lấy lại giá bán hiện tại. Có thể ghi đè `contact_name`, `delivery_address`, `note` trong body. Dòng có mã thành `HANG`, dòng tự do thành `KHAC`. Chỉ ánh xạ kho cho dòng HANG; nháp có thể chưa chọn kho. Không sửa báo giá gốc.
- `PUT /sales-orders/{id}/lines`: `version` **của đơn**, `lines` thay toàn bộ; tùy chọn `contact_name`, `delivery_address`, `note`. Khách và liên kết báo giá cố định từ khi lập đơn. Chỉ sửa khi `NHAP`.
- `POST /sales-orders/{id}/confirm`: `{ "version": 1 }`. `NHAP → DA_CHOT`, bắt buộc ít nhất một dòng và kho cho tất cả dòng HANG. Khóa giá, ghi `receivable = total`, người/thời điểm chốt.
- `POST /sales-orders/{id}/deliver`: `{ "version": 2 }`. `DA_CHOT → DA_GIAO`, mỗi kho một phiếu PX, lý do `Giao đơn DB-…`, ghi chú từng dòng `DB-… dòng n`. Gọi `stock_docs.insert` + `stock_posting.post_in_tx` trong cùng transaction, gắn `stock_doc_id` vào dòng HANG. Dòng KHAC không xuất kho. Đơn chỉ có KHAC vẫn giao được mà không lập PX.
- `POST /sales-orders/{id}/cancel`: `{ "version": 3, "reason": "Khách trả hàng" }`. Cho phép từ `NHAP`, `DA_CHOT`, `DA_GIAO` sang `DA_HUY`. Nếu đã giao, gọi `stock_posting.void_in_tx` cho từng PX; không xóa phiếu, dòng hoặc liên kết PX. Giữ tổng tiền, thời điểm chốt/giao để tra cứu; số phải thu bằng 0 nếu đã chốt, còn null nếu chưa từng chốt. Mỗi PX được hủy một lần dù nhiều dòng cùng tham chiếu.
- `GET /sales-orders?q=&status=&customer_code=&from=2026-10-01&to=2026-10-31`: tìm số DB/tên khách không dấu, lọc trạng thái/khách và ngày tạo (giờ Việt Nam, bao gồm hai đầu).
- `GET /sales-orders/{id}`: đầu đơn cùng `lines`, tổng tiền chính thức, version, liên kết PX, thông tin chốt/giao.

Mỗi dòng nhận `kind` (`HANG`/`KHAC`), `product_code`, `warehouse_code`, `description`, `qty` (Numeric 14,3, > 0), `unit`, `unit_price` (nguyên đồng >= 0), `vat_rate` thuộc `VAT_RATES` (0/5/8/10). Dòng HANG bắt buộc mã đang bán; nếu đã chọn kho thì kho phải đang dùng. Theo điều chỉnh đã xác nhận cho B2, **nháp được thiếu kho, chỉ bắt buộc kho khi chốt**; ràng buộc DB cho phép `warehouse_code` null ở HANG. Dòng KHAC bắt buộc cả mã và kho null, có mô tả và đơn giá. Dòng HANG bỏ giá/mô tả/đơn vị lấy từ hàng như B1.

Dùng lại phần tính tiền B1 (`quotes._lines`, `_totals`, sử dụng `_round` và `_money_limit`): làm tròn HALF_UP tiền dòng trước, rồi VAT từng dòng; tổng cộng các dòng. Giá bán thay đổi không đổi giá đã lưu hoặc giá chép từ báo giá. Không nhận `amount`, `vat_amount`, `stock_doc_id`, trạng thái hay tổng do bên gọi gửi.

Tất cả thao tác ghi audit trước/sau và kiểm version của đối tượng nguồn khi đã tồn tại. Khóa đơn/báo giá khi ghi; partial unique index chỉ cho một đơn chưa `DA_HUY` trên mỗi `quote_id`. Hủy đơn giải phóng báo giá để tạo đơn mới. Giao, hủy, số phiếu và audit nguyên tử: bất kỳ lỗi nào rollback toàn bộ, kể cả PX và sổ kho của kho trước đó. Không ghi trực tiếp các bảng kho.

### Mã lỗi

| Mã | HTTP | Nội dung |
| --- | --- | --- |
| FORBIDDEN | 403 | Vai trò không có quyền thao tác |
| NOT_FOUND | 404 | Không tìm thấy đơn hoặc báo giá nguồn |
| VERSION_REQUIRED | 400 | Thiếu/sai version khi gọi lõi; API schema trả 422 nếu thiếu/sai kiểu |
| VERSION_CONFLICT | 409 | Version cũ; `details.current` là phiên bản hiện tại |
| BAD_STATUS | 409 | Sai bước trạng thái hoặc báo giá chưa CHAP_NHAN; bộ lọc trạng thái sai trả 400 |
| QUOTE_USED | 409 | Báo giá đã có đơn chưa hủy |
| PRICE_LOCKED | 409 | Chỉ sửa dòng khi NHAP |
| EMPTY_LINES | 400 | Chốt đơn rỗng |
| WAREHOUSE_REQUIRED | 400 | Chốt dòng HANG chưa chọn kho; `details.line_no` |
| REASON_REQUIRED | 400 | Hủy thiếu lý do |
| BAD_LINE | 400 | HANG thiếu mã, KHAC có mã/kho, hoặc ánh xạ kho không thuộc dòng HANG nguồn |
| BAD_CUSTOMER | 400 | Khách không tồn tại hoặc ngừng/gộp |
| BAD_PRODUCT | 400 | Hàng không tồn tại/ngừng bán; `details.invalid_codes` |
| BAD_WAREHOUSE | 400 | Kho đã chọn không tồn tại/ngừng dùng; `details.codes` |
| PRICE_REQUIRED / DESCRIPTION_REQUIRED | 400 | Dòng KHAC thiếu giá/mô tả |
| AMOUNT_TOO_LARGE | 400 | Tiền vượt giới hạn BigInteger |
| BAD_DATE_RANGE | 400 | Ngày bắt đầu sau ngày kết thúc |
| NEGATIVE_STOCK | 409 | Thiếu tồn, giữ nguyên DA_CHOT; `details.warehouse`, `product`, `available`, `requested` |

Lỗi từ lõi kho (ví dụ `DOC_NOT_POSTED`, `VOID_COST_CONFLICT`) được giữ nguyên mã/message/details và rollback toàn bộ đơn. Phản hồi lỗi theo chuẩn `{ "error": "CODE", "message": "Câu tiếng Việt", "details": {} }`.

## Màn hình đơn bán (B3)

`/don-ban` dùng `sales_orders.search` để lọc trạng thái, số/tên khách không dấu và khoảng ngày tạo (giờ Việt Nam). `/don-ban/moi`, `/don-ban/{id}` gọi create/update_lines; Chốt/Giao/Hủy gọi confirm/deliver/cancel. Mọi POST có CSRF và version. Tiền chỉ bỏ dấu chấm khi nhập, tổng chính thức do lõi trả; ô tiền không dùng type=number. Lỗi thiếu tồn hiện mã hàng, kho, tồn và số cần. Nháp được thiếu kho; chốt phải đủ kho. Nếu còn thay đổi chưa lưu, trình duyệt nhắc Lưu trước khi Chốt. Hủy đơn đã giao yêu cầu lý do và hỏi xác nhận trả hàng về kho.

Khách cố định sau khi tạo theo hợp đồng B2; có thể sửa liên hệ, địa chỉ giao, ghi chú và toàn bộ dòng khi nháp. Ô tìm/chọn khách tự điền địa chỉ, vẫn cho sửa. Chọn hàng tự điền mã/giá bán; loại dòng, kho và VAT dùng nút chạm.

Trên báo giá CHAP_NHAN, nút Tạo đơn bán mở `/don-ban/tu-bao-gia/{quote_id}` để chọn kho, địa chỉ và liên hệ, rồi gọi create_from_quote. Báo giá đã có đơn chưa hủy hiện link đơn thay nút; sau hủy có thể lập lại. `/don-ban/{id}/in-giao` in A4 bằng trình duyệt, dùng thông tin công ty và money_words của B1. Trang có trạng thái đơn, địa chỉ giao, số lượng/đơn vị, tổng và chữ ký Người giao/Người nhận. Không thêm màn hình ghi kho hoặc thay đổi lõi B1/B2/K/D.

## Báo thu (B4)

Báo thu không phải chứng từ xuất/nhập kho và không làm giảm `sales_orders.receivable`. Phải thu là số gốc đã chốt; đã thu chỉ gồm BT `DA_XAC_NHAN`, chờ gồm `CHO_XAC_NHAN`, còn nợ = phải thu − đã thu. Từ chối/hủy giải phóng số tiền để báo lại. Không xóa hoặc tạo số tiền âm.

### Quyền và API

- Sales/director báo thu; finance/director xác nhận, từ chối; chỉ director hủy BT đã xác nhận. Finance kiêm sales vẫn không tự xác nhận phiếu do mình báo (`SELF_CONFIRM`), trừ director. Quy tắc này không cấm kế toán tự từ chối phiếu của mình.
- Finance/director xem hàng chờ; sales/finance/director xem đơn cần thu. Vai trò xem đơn bán (kể cả warehouse) xem được số dư và lịch sử BT của đơn; warehouse không được báo/xử lý BT.
- `POST /sales-orders/{id}/receipts`: `{ "version": 2, "method": "TIEN_MAT", "amount": 600000, "received_at": "2026-10-18", "note": "Đã nhận tiền" }` → 201. Version **của đơn**, method `TIEN_MAT`/`CHUYEN_KHOAN`, amount nguyên đồng > 0 trong BigInteger. Ngày thu mặc định ngày VN; số BT lấy theo năm của ngày thu, trong transaction lưu BT. Chỉ đơn `DA_CHOT`/`DA_GIAO`.
- `GET /sales-orders/{id}/receipts`: `{ "balance": { "receivable": 1000000, "paid": 600000, "pending": 400000, "remaining": 400000 }, "receipts": [...] }`.
- `GET /sales-receipts?method=&from=&to=`: BT chờ xác nhận, lọc cách thu/ngày thu (bao gồm hai đầu), xếp `reported_at`, id tăng dần.
- `POST /sales-receipts/{id}/confirm`: `{ "version": 1 }`; `CHO_XAC_NHAN → DA_XAC_NHAN`.
- `POST /sales-receipts/{id}/reject`: `{ "version": 1, "reason": "Chưa nhận được tiền" }`; `CHO_XAC_NHAN → TU_CHOI`.
- `POST /sales-receipts/{id}/void`: `{ "version": 2, "reason": "Hoàn tiền cho khách" }`; `DA_XAC_NHAN → DA_HUY`. Version của ba thao tác này là **của BT**. Người/thời điểm xử lý ghi trong decided_by/decided_at; xác nhận cũ vẫn có trong audit nếu sau đó hủy.
- `GET /sales-orders/receivables`: các dòng `{ "order": {...}, "balance": {...}, "days": 8 }`, còn nợ > 0, đã chốt/giao, xếp thời điểm chốt cũ nhất trước. Số ngày tính theo lịch VN từ lúc chốt; tiền chờ vẫn là nợ đến khi xác nhận.

Lõi có `report`, `confirm`, `reject`, `void`, `list_pending`, `receivables`, `for_order`; `balance(order)` nhận đơn ORM đã load trong session và query tổng mới để tránh cache số dư. Tất cả thao tác ghi khóa **đơn trước, BT sau**; báo thu không tăng version đơn vì không đổi đầu đơn. Hai báo thu cùng version vẫn kiểm tổng sau khi chờ khóa nên không thu vượt. Tạo, xử lý, cấp số và audit trước/sau cùng transaction. `sales_orders.cancel` kiểm BT chờ/đã xác nhận sau khóa đơn, trước thao tác hủy kho; không thể lọt báo thu vào đơn đã hủy trong cuộc đua.

### Lỗi

| Mã | HTTP | Ý nghĩa |
| --- | --- | --- |
| OVER_PAYMENT | 409 | Đã thu + chờ + số mới vượt phải thu; details gồm paid, pending, remaining, available |
| BAD_STATUS | 409 | Đơn chưa chốt/đã hủy, hoặc sai bước xử lý BT |
| SELF_CONFIRM | 403 | Người báo tự xác nhận, không phải giám đốc |
| HAS_RECEIPTS | 409 | Tài chính phải từ chối hoặc hủy báo thu trước |
| REASON_REQUIRED | 400 | Từ chối/hủy thiếu lý do |
| VERSION_CONFLICT | 409 | Version đơn/BT cũ |
| VERSION_REQUIRED | 400 | Thiếu version khi gọi lõi/web; API thiếu/sai kiểu trả 422 |
| FORBIDDEN / NOT_FOUND | 403 / 404 | Không có quyền / không tìm thấy chứng từ |
| BAD_METHOD / BAD_DATE_RANGE | 400 | Bộ lọc cách thu/khoảng ngày sai |
| BAD_AMOUNT / BAD_FORM | 400 | Dữ liệu form chưa hợp lệ; giữ dữ liệu đã nhập |

### Web

Trang đơn đã chốt/giao có mục Thu tiền, số dư và lịch sử BT; sales/director có form báo thu, tiền mặc định còn nợ trừ chờ, dấu chấm nghìn khi gõ, cách thu nút chạm. `/bao-thu` là hàng chờ kế toán, có Xác nhận/Từ chối, lọc cách thu/ngày; director có Hủy BT đã xác nhận trong trang đơn. `/don-ban/can-thu` hiển thị đơn còn nợ và số ngày, mất khỏi danh sách khi đã xác nhận đủ. Mọi POST web có CSRF/version; lỗi giữ form. Menu Báo thu chỉ finance/director. Không sửa `stock_*` hoặc `service_*`; `doc_numbers.py` chỉ thêm prefix BT.

## Báo cáo bán hàng (B5)

`GET /bao-cao/ban-hang?from=2026-10-01&to=2026-10-31` mặc định tháng hiện tại giờ Việt Nam; `period=today|month|last_month` chọn nhanh. Finance/director xem toàn bộ; sales chỉ thấy dữ liệu có `created_by` của mình; vai trò khác trả `FORBIDDEN` (403).

- Doanh thu chỉ gồm đơn `DA_GIAO`, theo `delivered_at` giờ Việt Nam, bao gồm hai đầu khoảng ngày; hiển thị ngày có đơn giao. Tiền trước VAT/VAT/tổng là số nguyên đồng. Nhân viên là người lập đơn; khách hàng lấy top 20 theo tổng giảm dần.
- Tuổi nợ là số dư **hiện tại của mọi đơn đã chốt/giao**, độc lập khoảng doanh thu: phải thu trừ BT `DA_XAC_NHAN`; BT chờ chưa trừ nợ. Chia 0–7, 8–30, 31–60, trên 60 ngày lịch VN từ ngày chốt. Thêm `nhom=0_7|8_30|31_60|over_60` để mở danh sách.
- Chuyển đổi: báo giá lập trong khoảng, bỏ bản có `replaced_by_id`; mỗi báo giá chỉ đếm một lần khi có đơn chưa hủy. Sales chỉ tính báo giá và đơn liên kết do chính mình lập. Tỷ lệ làm tròn hai chữ số thập phân; không có báo giá thì 0%.
- `app.sales_reports` chỉ SELECT và tổng hợp SQL `GROUP BY`/SUM/COUNT, không load từng đơn để cộng và không tự flush dữ liệu đang chờ của caller. Không thay đổi lõi hoặc schema.

CSV: `GET /bao-cao/ban-hang/{table}.csv`, với table `ngay`, `nhan-vien`, `khach-hang`, `tuoi-no`, `don-no` (cần `nhom`), `bao-gia`; dùng cùng bộ lọc và quyền. UTF-8 có BOM, tiền nguyên đồng, bảo vệ ô văn bản khỏi công thức Excel. Khoảng ngày sai trả `BAD_DATE_RANGE` (400); nhóm sai trả `BAD_BUCKET` (400).

## Tài chính (T1)

Quỹ và danh mục thu chi: `app.finance_catalogs.create(session,user,model,body)`, `update(session,user,model,code,body)`, `search(session,user,model,q,active=...,kind=...,direction=...)`, `load(session,user,model,code)`, với model `Fund`/`CashCategory` và schema `FundIn`/`FundPatch`, `CashCategoryIn`/`CashCategoryPatch`. Finance/director được xem và sửa; vai trò khác 403. Mã bỏ mọi khoảng trắng, chuyển chữ hoa; không đổi mã khi sửa. Tìm không dấu theo mã/tên, ngân hàng/tài khoản/ghi chú. Không xóa, chỉ `active=false`.

`app.finance_mappings.set_mapping(session,user,key,fund_code=None,category_code=None,version=...)`: chỉ director sửa, finance xem. Bốn dòng cố định được migration tạo với mã đích rỗng, version 1; không tạo sẵn quỹ/danh mục. `THU_TIEN_MAT` chỉ quỹ TIEN_MAT đang dùng; `THU_CHUYEN_KHOAN` chỉ quỹ NGAN_HANG đang dùng; `DANH_MUC_BAN_HANG`/`DANH_MUC_DICH_VU` chỉ danh mục THU đang dùng. Chỉ truyền đúng trường đích của key; để mã đích `None` để bỏ ghép. `require(session,key)` dùng nội bộ các bước sau, đọc mã mới nhất đã ghép hoặc báo MAPPING_REQUIRED, không yêu cầu user.

Sửa kiểm version sau khi khóa bản ghi; tạo/sửa/bỏ ghép và audit trước/sau cùng transaction. Thao tác ghép khóa quỹ/danh mục trước, rồi khóa dòng ghép; sửa quỹ/danh mục khóa cùng bản ghi trước khi kiểm đang được ghép. Không ngừng dùng đích đang ghép hoặc đổi loại/chiều khiến ghép sai. Các lỗi rollback toàn bộ.

| Mã | HTTP | Ý nghĩa |
| --- | --- | --- |
| DUPLICATE_CODE | 409 | Mã đã có sau khi chuẩn hóa |
| BAD_CODE | 400 | Mã không hợp lệ, tối đa 32 ký tự chữ không dấu/số/gạch ngang/gạch dưới |
| ACCOUNT_REQUIRED | 400 | Quỹ ngân hàng thiếu số tài khoản |
| BAD_MAPPING | 400 / 409 | Sai key, sai trường đích, mã không tồn tại/ngừng dùng/sai loại hoặc chiều; 409 khi đổi danh mục làm hỏng ghép hiện tại |
| MAPPING_REQUIRED | 409 | Chưa ghép cấu hình cần dùng; details.key cho biết khóa |
| IN_USE | 409 | Không ngừng dùng quỹ/danh mục đang được ghép |
| VERSION_CONFLICT | 409 | Phiên bản đã cũ, giữ dữ liệu form vừa nhập |
| VERSION_REQUIRED / BAD_FORM | 400 | Thiếu version hoặc dữ liệu chưa hợp lệ |
| BAD_KIND / BAD_DIRECTION | 400 | Loại quỹ hoặc chiều thu chi chưa hợp lệ |
| FORBIDDEN / BAD_CSRF | 403 | Sai vai trò hoặc thiếu/sai CSRF |
| NOT_FOUND | 404 | Không tìm thấy quỹ/danh mục |

Web: `/quy`, `/danh-muc-thu-chi` dùng khuôn danh mục có sẵn, thêm ở `/moi`, sửa ở `/{code}`; POST có CSRF/version. `/ghep-tai-chinh` có bốn ô tìm và chọn qua `data-live`, kết quả chỉ có đích hợp lệ, không tự chọn gợi ý; nhãn đỏ “Chưa ghép” cho dòng rỗng. Finance không có nút lưu bảng ghép; director có. Lỗi giữ dữ liệu đã nhập. Menu Tài chính chỉ finance/director. Không có endpoint xóa; không sửa stock_*, service_*, sales_* hoặc quotes.py.

## Thu chi (T2)

`app.cash_docs` cung cấp `create(session,user,CashDocIn)`, `update(session,user,id,CashDocPatch)`, `cancel(session,user,id,CashDocCancel)`, `search(session,user,kind=None,fund=None,status=None,from_date=None,to_date=None)`, `load(session,user,id)`. Chỉ finance/director; phiếu `is_opening` (PT hoặc PC) chỉ director được lập, sửa, ghi hoặc hủy. Không xóa phiếu. Sửa/hủy nháp chỉ NHAP; đổi kind hoặc năm doc_date bị NUMBER_LOCKED, phải hủy nháp và lập mới. Cấp PT/PC/CQ qua next_number trong transaction lưu phiếu. Quỹ phải đang dùng; PT thường cần danh mục THU, PC thường cần CHI; CQ và đầu kỳ không có danh mục. Khách/nhà cung cấp không được có đồng thời, mã nếu có phải đang dùng. `source_type`/`source_id` hoặc cùng rỗng hoặc cùng có; cặp nguồn chỉ có một phiếu chưa DA_HUY.

`app.cash_posting.post(session,user,id,version)` và `void(session,user,id,reason,version)` là hai nơi ghi số dư/sổ. Khóa phiếu rồi kiểm version/trạng thái; khóa các dòng fund_balances theo mã quỹ tăng dần, tạo dòng còn thiếu bằng INSERT ON CONFLICT. PT tăng, PC giảm, CQ giảm nguồn/tăng đích cùng transaction. Kiểm mọi quỹ trước khi thêm sổ; không cho âm hoặc vượt BigInteger. Khi ghi, kiểm lại quỹ/danh mục còn dùng. Hủy DA_GHI phải có lý do, đảo chính xác dòng gốc vào ngày hủy giờ VN; quỹ/danh mục đã ngừng vẫn cho ghi đảo để sửa sai, nhưng không lập/ghi phiếu mới. Phiếu đầu kỳ luôn cần director.

`insert(session,user,body)`, `post_in_tx(session,user,id,version)`, `void_in_tx(session,user,id,reason,version)` dùng trong transaction của nơi gọi, không commit và không kiểm quyền thông thường (caller phải kiểm); riêng quyền đầu kỳ luôn kiểm. Caller phải rollback toàn bộ khi lỗi. Mỗi thao tác thành công ghi audit_log trước/sau cùng transaction. `balance(session,user,fund_code)` và `balances(session,user,codes)` chỉ SELECT, không tự tạo số dư khi xem.

Fund ledger chỉ INSERT: trigger chặn UPDATE/DELETE; mỗi phiếu có tối đa một dòng gốc và một dòng đảo cho từng quỹ. `entry_date` gốc là ngày phiếu, đảo là ngày hủy. `balance_after` là số dư thực tại thời điểm ghi. `statement(session,user,fund,start,end)` đọc trong một snapshot SQL, tính dư đầu từ tất cả dòng trước kỳ, dòng trong kỳ và dư cuối theo ngày kế toán bằng SUM OVER (đúng cả phiếu ghi lùi ngày); không bỏ dòng gốc của phiếu đã hủy vì đã có dòng đảo. Tổng thu/chi thuần tính PT/PC, dòng đảo giảm loại gốc; CQ và đầu kỳ hiện riêng, không lẫn thu chi/doanh thu. Không giới hạn một phiếu đầu kỳ, không tự đoán quỹ/danh mục hoặc tự tạo danh mục.

Web `/thu-chi` lọc loại/quỹ (cả nguồn và đích CQ)/trạng thái/ngày; mặc định đầu tháng đến hôm nay giờ VN. `/thu-chi/moi?loai=PT|PC|CQ`, `/{id}` dùng nút quỹ và ô tìm/chọn danh mục, khách, nhà cung cấp không dấu; tiền nguyên đồng có dấu chấm nghìn. Ghi sổ lưu nháp trước rồi gọi post; nếu thiếu tiền, giữ phiếu NHAP vừa lưu và dữ liệu form, không ghi sổ/số dư. `/{id}/post`, `/{id}/cancel` là POST có CSRF/version, hủy phải có lý do và hỏi xác nhận. `/{id}/in` in A4 có tiền bằng chữ và chỗ ký. `/so-quy/{fund}?from=...&to=...` chỉ đọc; `/quy` hiện số dư hiện tại và link sổ quỹ.

| Mã | HTTP | Ý nghĩa |
| --- | --- | --- |
| NEGATIVE_CASH | 409 | Không đủ tiền; details gồm fund, available, requested (nguyên đồng), form hiện quỹ/số dư/số cần |
| BAD_FUND / BAD_CATEGORY | 400 | Quỹ/danh mục không tồn tại, ngừng dùng hoặc sai chiều/không được có danh mục |
| BAD_TRANSFER / BAD_OPENING | 400 | CQ thiếu/trùng quỹ đích, loại khác có đích hoặc dùng CQ làm đầu kỳ |
| BAD_PARTNER / BAD_SOURCE | 400 | Hai đối tác đồng thời, đối tác sai/ngừng dùng hoặc thiếu một phần nguồn |
| SOURCE_USED | 409 | Nguồn đã có phiếu chưa hủy |
| BAD_AMOUNT / BAD_FORM | 400 | Tiền không phải số nguyên dương trong BigInteger hoặc dữ liệu chưa hợp lệ |
| AMOUNT_TOO_LARGE | 409 | Số dư sau biến động vượt BigInteger |
| NUMBER_LOCKED | 409 | Không đổi loại phiếu hoặc năm của số đã cấp |
| DOC_POSTED / DOC_VOID / DOC_NOT_POSTED | 409 | Không sửa/hủy nháp phiếu đã ghi/đã hủy; không ghi đảo phiếu chưa ghi |
| REASON_REQUIRED | 400 | Hủy thiếu lý do hoặc lý do quá dài |
| VERSION_REQUIRED / VERSION_CONFLICT | 400 / 409 | Thiếu version hoặc version cũ |
| BAD_KIND / BAD_STATUS / BAD_DATE_RANGE | 400 | Bộ lọc không hợp lệ |
| FORBIDDEN / BAD_CSRF / NOT_FOUND | 403 / 403 / 404 | Sai vai trò, thiếu CSRF hoặc không tìm thấy phiếu |

## Nối báo thu và sổ quỹ (T3)

SalesReceipt có đúng một nguồn: `order_id` hoặc `ticket_id` (check constraint DB), thêm `cash_doc_id` nullable. Migration `0016` nối `0015`, giữ nguyên BT cũ, không tự tạo PT hồi tố. BT xác nhận trước T3 chưa có PT vẫn có thể hủy theo hành vi cũ; chỉ PT đã gắn mới ghi đảo. Downgrade khôi phục order_id NOT NULL, vì vậy cần không còn BT dịch vụ khi hạ schema; migration không xóa dữ liệu tài chính để ép hạ phiên bản.

`confirm` khóa đơn/phiếu nguồn rồi BT; trong cùng transaction dùng `finance_mappings.require` lấy THU_TIEN_MAT/THU_CHUYEN_KHOAN và DANH_MUC_BAN_HANG/DANH_MUC_DICH_VU, gọi `cash_docs.insert` và `cash_posting.post_in_tx`. PT dùng ngày thu received_at, customer_code nguồn, đúng số tiền BT, nội dung “Thu BT-… cho DB-…/DV-…”, source_type `SALES_RECEIPT`, source_id là id BT. Gắn cash_doc_id và xác nhận BT cùng commit, kể cả audit. Thiếu ghép/lỗi ghi sổ rollback cả PT, sổ, số dư, bộ đếm và BT.

`void` chỉ director, lý do/version bắt buộc, dùng `cash_posting.void_in_tx` cho PT đã gắn rồi hủy BT trong cùng transaction. Thiếu quỹ hoặc lỗi sau ghi đảo cũng rollback toàn bộ; BT vẫn DA_XAC_NHAN, công nợ chưa đổi. PT nguồn SALES_RECEIPT không được hủy trực tiếp qua `/thu-chi`: SOURCE_LOCKED; giao diện bỏ nút hủy và hướng dẫn hủy BT. Không sửa cash_posting.py hoặc stock_*.

`report_for_ticket(session,user,ticket_id,SalesReceiptReport)` chỉ dispatcher/sales/director, phiếu HOAN_THANH/DA_DONG có receivable > 0, không miễn phí. Khóa ticket rồi kiểm version và tổng đã thu + chờ + số mới không vượt phải thu. `balance(order_or_ticket)` dùng chung, BT dịch vụ không lẫn công nợ đơn trùng id. Xác nhận/từ chối finance/director, tự xác nhận bị SELF_CONFIRM trừ director; void chỉ director. Mở lại hoặc hủy HOAN_THANH trong service_tickets.transition kiểm HAS_RECEIPTS sau khóa ticket, trước thay đổi vật tư/giá/trạng thái; chỉ khi đã từ chối/void hết mới được tiếp tục.

API thêm `POST /service-tickets/{id}/receipts` (cùng body BT đơn, version của ticket, trả 201) và `GET /service-tickets/{id}/receipts` (balance/receipts). Các response SalesReceiptOut có order_id/ticket_id/cash_doc_id nullable. `service_charges.receivables` vẫn trả list ServiceTicket, bổ sung thuộc tính chỉ đọc paid/pending/remaining từ SQL GROUP BY; chỉ còn nợ > 0, tiền chờ chưa trừ. API `/service-tickets/receivables`, web/CSV `/dich-vu/can-thu` bổ sung Đã thu, Chờ xác nhận, Còn nợ; tổng là nợ còn lại.

Web dịch vụ hoàn thành/đóng có thu tiền dùng chung `bao_thu_trong_don` với đơn bán. Hàng chờ `/bao-thu` có Nguồn DB/DV; BT đã xác nhận hiện số PT, finance/director có link `/thu-chi/{id}`, vai trò khác chỉ thấy số để giữ quyền T2. Lỗi giữ dữ liệu; mọi POST có CSRF/version. Fixture test chủ động ghép khi cần; production không tự ghép.

| Mã | HTTP | Ý nghĩa |
| --- | --- | --- |
| MAPPING_REQUIRED | 409 | “Giám đốc cần ghép quỹ và danh mục ở trang Ghép tài chính”; có link tới /ghep-tai-chinh |
| NEGATIVE_CASH | 409 | Không đủ tiền đảo PT; details quỹ/số dư/số cần, BT/PT không đổi |
| SOURCE_LOCKED | 409 | Phải hủy BT để ghi đảo PT nguồn báo thu |
| BAD_STATUS | 409 | DV chưa hoàn thành/đã đóng có thu, miễn phí hoặc sai trạng thái BT |
| OVER_PAYMENT | 409 | Đã thu + chờ + mới vượt phải thu; details available/paid/pending/remaining |
| HAS_RECEIPTS | 409 | Tài chính phải từ chối hoặc hủy báo thu trước khi mở lại/hủy DV |
| VERSION_CONFLICT / SELF_CONFIRM / REASON_REQUIRED / FORBIDDEN | 409 / 403 / 400 / 403 | Giữ quy tắc B4 cho cả DB/DV |

## Chốt sổ và điều chỉnh phiếu (T4)

Migration `0017` nối `0016`, thêm day_closings (khóa chính day, DA_CHOT/DA_MO) và cash_corrections (CHO_DUYET/DA_DUYET/TU_CHOI, unique một yêu cầu chờ theo doc_id). Hai bảng có version_id_col, FK người/quỹ/danh mục/phiếu; không seed ngày hoặc yêu cầu.

`day_closings.close(session,user,day,version)`: finance/director, chỉ ngày <= hôm nay VN. Ngày chưa có bản ghi dùng version **0**, các lần sau dùng version hiện tại. Còn NHAP cùng doc_date báo DRAFTS_OPEN kèm details.numbers. DA_MO được chốt lại. `unlock(session,user,day,reason,version)` chỉ director, lý do bắt buộc, không tự khóa lại. Mỗi lần chốt/mở tăng version, ghi audit trước/sau.

`check_open(session,day)` giữ advisory transaction lock theo namespace cố định + ngày, đọc trạng thái mới nhất; DA_CHOT báo DAY_CLOSED với details.day. Ngày chưa chốt không tự tạo dòng. Cash docs insert/create, update (cả ngày cũ/mới), cancel nháp; cash posting post_in_tx (ngày gốc), void_in_tx (ngày gốc + ngày hủy) đều gọi kiểm tra. Các ngày được khóa theo thứ tự tăng. Chốt/mở khóa dùng cùng advisory lock, vì vậy không lọt tạo/ghi/hủy khi chốt đồng thời. Xác nhận BT tự bị chặn và rollback qua cash_docs.insert/post_in_tx. Ngày hủy được lấy một lần để kiểm và ghi đảo cùng ngày, kể cả lúc qua nửa đêm.

`cash_posting.void` kiểm cash_docs.check_source trong lõi, không chỉ web: PT SALES_RECEIPT báo SOURCE_LOCKED. void_in_tx vẫn cho luồng BT gọi, nhưng chịu khóa ngày bình thường.

`cash_corrections.request(session,user,doc_id,CashCorrectionIn)` finance/director, version của phiếu gốc. Chỉ PT/PC DA_GHI, không đầu kỳ, không có source_type; nguồn tự động báo SOURCE_LOCKED. Lý do bắt buộc, new_amount nguyên dương; quỹ/danh mục mới mặc định từ phiếu gốc nếu không gửi, phải đang dùng/đúng chiều. Có yêu cầu CHO_DUYET báo CORRECTION_PENDING.

`approve(session,user,id,version)` chỉ director, version của yêu cầu. Khóa yêu cầu/phiếu, kiểm ngày hôm nay mở; khóa hợp quỹ cũ/mới theo mã tăng trước khi đảo. Trong cùng transaction: void_in_tx gốc với lý do “Điều chỉnh: …”, insert/post phiếu dương mới cùng loại ngày hôm nay, mô tả “Điều chỉnh cho PT-/PC-…”, gắn new_doc_id và audit. Chỉ luồng này truyền capability nội bộ để bỏ qua check ngày **gốc**; ngày hôm nay vẫn bắt buộc mở, ngày gốc vẫn DA_CHOT, không tạm mở khóa. Capability không nhận từ HTTP và cần director. Quỹ âm hoặc lỗi sau ghi mới rollback cả đảo, phiếu mới, bộ đếm, số dư, trạng thái yêu cầu và audit. `reject(session,user,id,reason,version)` chỉ director, lý do bắt buộc. Không có phiếu âm hoặc sửa/xóa dòng sổ.

Web `/chot-so`: 30 ngày gần nhất, tổng PT/PC thuần theo ngày sổ (đảo giảm loại gốc, không gồm CQ/đầu kỳ), nháp và trạng thái; finance chốt, director thêm mở khóa với lý do/xác nhận. `/thu-chi/{id}` có nhãn Đã chốt sổ và ẩn Hủy; PT/PC đủ điều kiện có form yêu cầu điều chỉnh, giữ dữ liệu lỗi; sau duyệt phiếu cũ có link phiếu mới. `/dieu-chinh` liệt kê yêu cầu chờ, số cũ/mới, director Duyệt/Từ chối. Mọi POST có CSRF/version, giao diện 360px.

| Mã | HTTP | Ý nghĩa |
| --- | --- | --- |
| DAY_CLOSED | 409 | Ngày đã chốt, details.day |
| DRAFTS_OPEN | 409 | Còn phiếu nháp, details.numbers |
| FUTURE_DAY | 400 | Không chốt ngày mai hoặc xa hơn |
| CORRECTION_PENDING | 409 | Phiếu đang có một yêu cầu chờ duyệt |
| SOURCE_LOCKED | 409 | Không hủy PT nguồn BT trực tiếp trong lõi; không điều chỉnh phiếu nguồn tự động |
| REASON_REQUIRED | 400 | Mở khóa/yêu cầu/từ chối thiếu lý do |
| BAD_STATUS | 409 | Ngày đã mở hoặc yêu cầu/phiếu không còn đủ điều kiện |
| VERSION_REQUIRED / VERSION_CONFLICT | 400 / 409 | Thiếu hoặc cũ version, kể cả version 0 của ngày chưa có dòng |
| NEGATIVE_CASH / FORBIDDEN / BAD_CSRF | 409 / 403 / 403 | Giữ kiểm số dư, quyền và CSRF |

### Báo cáo tài chính (T5)

Chỉ `finance`, `director`; vai trò khác trả `FORBIDDEN` (403). Tất cả hàm trong `app.finance_reports` chỉ SELECT, tổng hợp bằng SQL, không autoflush dữ liệu caller đang sửa.

- `funds`, `fund_total`: số dư hiện tại, gồm cả quỹ ngừng dùng và quỹ chưa phát sinh (số dư 0).
- `categories(session, user, direction, start=None, end=None)`, `category_totals`, `daily`: mặc định tháng hiện tại theo giờ Việt Nam. Lọc `fund_ledger.entry_date`, không lọc trạng thái phiếu (phiếu hủy vẫn giữ phát sinh gốc). Dòng đảo vào ngày hủy. Thu = tổng tiền có dấu của PT, chi = âm tổng tiền có dấu của PC; loại CQ và đầu kỳ. Số phiếu là số chứng từ khác nhau có phát sinh trong khoảng, mỗi phiếu tính một lần trong mỗi bảng kể cả có cả dòng gốc và đảo. Dòng tiền có đủ ngày; số dư cộng toàn bộ sổ từ trước khoảng cộng lũy kế mọi phát sinh, gồm đầu kỳ/CQ.
- `customers`, `debt_totals`, `customer_docs(session, user, customer_code)`: công nợ hiện tại, không giới hạn theo khoảng thu chi. Chỉ cộng chứng từ còn nợ: đơn DA_CHOT/DA_GIAO, dịch vụ HOAN_THANH/DA_DONG. GROUP BY báo thu riêng theo từng nguồn trước khi UNION ALL, tránh nhân dòng giữa hai nguồn. Chỉ DA_XAC_NHAN trừ nợ; CHO_XAC_NHAN chỉ hiện riêng. Ngày chốt là `confirmed_at` của đơn hoặc `priced_at` của dịch vụ, chuyển giờ Việt Nam; số ngày tính tới hôm nay, tối thiểu 0.

Web GET `/bao-cao/tai-chinh`: `period=today|month|last_month` hoặc `from=YYYY-MM-DD&to=YYYY-MM-DD`; `khach=...` mở danh sách chứng từ còn nợ, có link DB/DV. GET `/bao-cao/tai-chinh/{table}.csv` với `table=quy|thu|chi|dong-tien|cong-no|chung-tu`; CSV UTF-8 có BOM, tiền nguyên đồng, chuỗi người dùng được chống công thức Excel. Không có endpoint ghi hoặc migration.

Lỗi: `BAD_DATE_RANGE` (400) khi ngày không hợp lệ/đảo khoảng; `BAD_DIRECTION` (400) khi gọi hàm danh mục với chiều ngoài THU/CHI; `NOT_FOUND` (404) với bảng CSV không tồn tại.

### Đơn mua (M1)

API Bearer theo mẫu B2: GET/POST `/purchase-orders`; GET/PUT `/purchase-orders/{id}`; POST `/{id}/order`, `/{id}/cancel`; GET `/purchase-orders/suggest-cost/{product_code}` trả `unit_cost` hoặc null. Danh sách nhận `q` (số đơn), `status`, `supplier_code`. POST đặt/hủy và PUT sửa bắt buộc version; hủy thêm reason. Create/update nhận supplier_code, order_date, expected_date (nullable), note, lines; mỗi dòng có product_code, warehouse_code, qty (>0, tối đa 3 chữ số lẻ), unit_cost (nguyên đồng >=0), vat_rate (0/5/8/10, mặc định theo DEFAULT_VAT_RATE). Không nhận received_qty từ client.

- Ghi: warehouse/director; xem: thêm finance. Sai quyền `FORBIDDEN` (403).
- NHAP → DA_DAT qua `order`, ghi ordered_by/ordered_at; chỉ sửa NHAP, thay toàn bộ dòng và tính lại tiền HALF_UP từng dòng/VAT. Đơn không được rỗng. Sửa giữ số DM đã cấp, không cấp lại số.
- Hủy NHAP hoặc DA_DAT khi chưa nhận hàng, bắt buộc lý do; không xóa đơn. Mọi thay đổi khóa dòng, kiểm version và ghi audit trước/sau cùng transaction cấp số.
- Đơn giá gợi ý lấy đơn có lần đặt gần nhất, bỏ NHAP/DA_HUY, hòa thời gian dùng id và số dòng. M1 không ghi sổ kho/quỹ, received_qty chỉ để sẵn M2.

Lỗi: `EMPTY_ORDER`, `BAD_SUPPLIER`, `BAD_PRODUCT`/`BAD_WAREHOUSE` (kèm invalid_codes), `REASON_REQUIRED`, `AMOUNT_TOO_LARGE` (400); `PRICE_LOCKED`, `BAD_STATUS`, `CANNOT_CANCEL`, `VERSION_CONFLICT` (409); `NOT_FOUND` (404). Dữ liệu schema sai trả 422 theo FastAPI. Web hiển thị câu tiếng Việt và giữ dữ liệu nhập, POST có CSRF; ô tiền cho phép dấu chấm nghìn. Trang `/don-mua`, `/don-mua/moi`, `/don-mua/{id}`, `/don-mua/{id}/in`.

VAT công ty: `app.config.DEFAULT_VAT_RATE` là nguồn mặc định duy nhất (8%). Schema báo giá/đơn bán/dịch vụ/đơn mua và template dòng mới dùng giá trị này; gửi rõ 0 vẫn giữ 0, dòng đã lưu giữ VAT cũ. Không cập nhật dữ liệu cũ hoặc migration cho phần đổi mặc định VAT.

### Nhận hàng đơn mua (M2)

API Bearer: GET `/purchase-orders/{id}/receipts`; POST `/purchase-orders/{id}/receive` nhận `{version, receipt_date, lines:[{order_line_id, qty}]}` và trả danh sách lần nhận (mỗi kho một phần tử); POST `/purchase-receipts/{id}/void` nhận `{version, reason}` (version lần nhận); POST `/purchase-orders/{id}/close` nhận `{version, reason}` (version đơn). Quyền ghi warehouse/director; xem thêm finance, như M1. Mọi thao tác ghi có khóa, version và audit trước/sau. GET đơn trả thêm close_reason; tìm `status=CHO_NHAN` lọc DA_DAT + NHAN_MOT_PHAN.

- `app.purchase_receipts.receive(session,user,order_id,version,receipt_date,lines)`: chỉ DA_DAT/NHAN_MOT_PHAN; qty dương tối đa 3 chữ số lẻ, dòng phải thuộc đơn và không lặp, không vượt còn lại. Khóa đơn trước mọi thao tác nhận/hủy/đóng, PN nhóm theo kho tăng dần. Giá vốn/VAT lấy từ dòng đặt, không nhận giá hoặc thuế từ client. PN tạo bằng stock_docs.insert và ghi bằng stock_posting.post_in_tx trong cùng transaction; lỗi rollback cả số, PN, sổ, tồn, lần nhận và tiến độ.
- Lần cuối đủ số: tiền hàng/VAT bằng tiền dòng đặt trừ tổng các lần nhận DA_GHI trước. Tiền hàng bù được truyền qua tham số nội bộ `pn_amounts` của post_in_tx để giá trị tồn/sổ đúng tiền nhận, vẫn giữ unit_cost gốc. Không có tham số này trên API kho; luồng K4/B2/D6 cũ giữ nguyên cách tính. Nếu chia quá nhỏ làm tổng tiền đã làm tròn vượt tổng dòng, lần nhận cuối báo ROUNDING_CONFLICT; hủy/gộp lại lần nhận thay vì ghi số âm.
- `void(session,user,receipt_id,reason,version)`: gọi void_in_tx PN, trừ received_qty và tính lại DA_DAT/NHAN_MOT_PHAN/DA_NHAN. Thiếu tồn/giá trị tồn xung đột thì rollback toàn bộ. Nếu đơn đã đóng phần còn lại, xóa close_reason và ghi audit reopen_after_void. PN nguồn đơn mua không hủy trực tiếp qua stock_posting.void: SOURCE_LOCKED, phải hủy lần nhận để giữ tiến độ/công nợ đúng.
- `close(session,user,order_id,reason,version)`: chỉ NHAN_MOT_PHAN, chuyển DA_NHAN và lưu close_reason; không phát sinh PN hoặc tiền phải trả cho phần thiếu.
- `payable_by_order(session,user,order_id)`: SQL SUM total các lần nhận DA_GHI, trả int đồng, không dùng tổng đơn. Không có thanh toán/ghi sổ quỹ ở M2.

Web: GET/POST `/don-mua/{id}/nhan`, POST `/don-mua/{id}/close`, POST `/don-mua/lan-nhan/{id}/huy` đều dùng CSRF và phân quyền lõi. Form số lượng mặc định còn lại, 0 bỏ qua; API/lõi yêu cầu từng dòng gửi lên >0. Giữ ngày/số lượng khi lỗi. Trên chi tiết có tiến độ và lịch sử gồm PN có link, tiền, hủy có lý do/xác nhận.

Lỗi: BAD_STATUS/OVER_RECEIVE/VERSION_CONFLICT/SOURCE_LOCKED/NEGATIVE_STOCK/VOID_COST_CONFLICT (409), BAD_ORDER_LINE/DUPLICATE_LINE/BAD_QTY/EMPTY_RECEIPT/REASON_REQUIRED/ROUNDING_CONFLICT/BAD_RECEIPT_AMOUNT (400), FORBIDDEN (403), NOT_FOUND (404). Dữ liệu schema API sai trả 422. NEGATIVE_STOCK kèm warehouse, product, available, requested.

### Công nợ và trả nhà cung cấp (M3)

Chỉ finance/director xem và trả/hủy tiền NCC; nợ đầu kỳ chỉ director. API Bearer: GET `/supplier-debts?q=...` trả rows/totals NCC có số dư khác 0; GET `/supplier-debts/{code}` trả balance/timeline/opening/payments (kèm version để sửa/hủy); POST `/supplier-debts/{code}/pay` nhận `{fund_code, amount, paid_at, order_id?, note?}`; PUT `/supplier-debts/{code}/opening` nhận `{amount, as_of, note, version}`; POST `/supplier-payments/{id}/void` nhận `{reason, version}`. Tạo TN mới có version=1; hủy dùng version TN; đặt nợ đầu kỳ dùng version dòng đầu kỳ (0 khi chưa có).

`app.supplier_debts`:

- `balance(session,user,supplier_code)` trả opening/received/paid/remaining: đầu kỳ + SUM nhận DA_GHI − SUM trả DA_GHI. Các tổng theo NCC được GROUP BY độc lập trước khi nối, không nhân dòng. Số âm là đã trả trước, vẫn hiển thị. `order_balance` chỉ nhận/trả gắn cùng đơn; trả chung NCC không tự phân bổ vào đơn. `search`/`totals` tìm không dấu, chỉ cộng các NCC có số dư khác 0 trong kết quả lọc.
- `pay(session,user,supplier_code,fund_code,amount,paid_at,order_id=None,note='')`: khóa ngày kế toán (chỉ khóa, không kiểm chốt), rồi khóa suppliers FOR UPDATE trước khi tính nợ; cùng thứ tự ngày→NCC với phiếu quỹ thủ công. Không khóa ngược đơn mua (M2 khóa đơn→NCC). Kiểm cả giới hạn NCC và giới hạn đơn nếu có; đơn phải thuộc NCC. Ghép DANH_MUC_MUA_HANG chỉ chấp nhận danh mục CHI đang dùng; các key cũ vẫn THU. Đổi chiều/ngừng danh mục đang ghép vẫn bị chặn.
- TN + PC + ghi sổ + audit trong một transaction, PC source_type SUPPLIER_PAYMENT/source_id=id TN, có supplier_code và mô tả TN/NCC/DM. Reserve BIGSERIAL id để liên kết trước khi ghi PC, cash_doc_id NOT NULL; số TN/PC dùng doc_numbers trong transaction nên lỗi không tiêu số chứng từ. Quỹ/ngày chốt được kiểm bởi cash_docs.insert/cash_posting.post_in_tx, không kiểm DAY_CLOSED lần hai ở M3. Không ghi trực tiếp fund_* hoặc stock_*.
- `void(session,user,payment_id,reason,version)`: khóa ngày gốc/ngày hủy rồi NCC/TN, gọi cash_posting.void_in_tx PC, đánh DA_HUY và audit. cash_docs.check_source chặn hủy PC nguồn trả NCC trực tiếp (SOURCE_LOCKED), web cũng ẩn nút hủy PC.
- `set_opening(session,user,supplier_code,amount,as_of,note,version)`: director, amount >=0, khóa NCC và kiểm version; audit số cũ/mới, không xóa. Sửa đầu kỳ có thể làm số dư ròng thay đổi, không ghi sổ quỹ.
- `timeline`: nợ đầu kỳ, nhận gốc/hủy nhận, trả gốc/hủy trả, số dư chạy bằng SUM OVER. Ngày hủy chuyển giờ Việt Nam; có link DM/PN/TN/PC. Tổng cuối khớp balance kể cả sau hủy nhận đã trả tiền.

Web `/cong-no-ncc` và `/{code}`; form quỹ là nút chạm, tiền định dạng nghìn, ngày mặc định hôm nay giờ VN. Link từ đơn truyền `?don=id`; số mặc định không vượt cả nợ đơn và nợ NCC. Người dùng có thể mở NCC chưa có nợ để director nhập đầu kỳ. Mọi POST dùng CSRF; lỗi giữ dữ liệu đã nhập. Trên đơn mua chỉ finance/director thấy công nợ và link Trả tiền.

Lỗi: OVER_PAYMENT/VERSION_CONFLICT/MAPPING_REQUIRED/SOURCE_LOCKED/BAD_STATUS/DAY_CLOSED/NEGATIVE_CASH (409), BAD_ORDER/BAD_SUPPLIER/BAD_FUND/REASON_REQUIRED/BAD_AMOUNT (400), FORBIDDEN (403), NOT_FOUND (404). Schema API sai trả 422. Mọi thay đổi có audit và không xóa bản ghi.

### Trả hàng nhà cung cấp (M4)

Quyền ghi warehouse/director, xem thêm finance. API Bearer: POST `/purchase-receipts/{receipt_id}/returns` nhận `{version, return_date, reason, lines:[{receipt_line_id, qty}]}` (version **đơn mua**); GET `/purchase-orders/{order_id}/returns`; POST `/purchase-returns/{id}/void` nhận `{version, reason}` (version **phiếu trả**). Kết quả có số TH, stock_doc_id PX, tiền hàng/VAT/tổng, trạng thái, version và các dòng.

`app.purchase_returns.create(session,user,receipt_id,version,return_date,reason,lines)` khóa đơn mua rồi lần nhận như M2. Chỉ trả từ lần nhận DA_GHI; dòng phải thuộc lần nhận, không lặp, qty dương tối đa 3 chữ số lẻ và không vượt qty nhận trừ SUM qty trả DA_GHI. Lý do bắt buộc. Một TH liên kết một PX cùng kho, NCC và ngày trả; stock_docs.insert + stock_posting.post_in_tx trong cùng transaction. PX **xuất giá vốn bình quân do lõi kho tính**, không lấy giá mua làm giá vốn xuất. Tiền giảm nợ tính riêng bằng giá mua/VAT của dòng nhận, ROUND_HALF_UP; trả hết phần còn lại lấy tổng dòng nhận trừ các phiếu trả DA_GHI trước để bù làm tròn. Chia quá nhỏ làm tiền đã làm tròn vượt tổng dòng báo ROUNDING_CONFLICT, cần hủy/gộp lại phiếu trả. Lỗi rollback cả số TH/PX, tồn, phiếu trả và audit.

`void(session,user,return_id,reason,version)` cũng khóa đơn → lần nhận → phiếu trả rồi gọi stock_posting.void_in_tx PX; đánh DA_HUY, lưu lý do/người/giờ hủy. Tạo/hủy trả tăng version đơn nhưng **không đổi received_qty hoặc trạng thái đơn mua**. Mỗi thao tác ghi audit trước/sau cho phiếu trả và đơn. Không ghi trực tiếp stock_*/fund_*, không phát sinh thu chi.

`stock_posting.void` chặn PX trả NCC đang hiệu lực bằng SOURCE_LOCKED, phải hủy phiếu trả ở đơn mua. `purchase_receipts.void` báo HAS_RETURNS nếu lần nhận còn phiếu trả DA_GHI; hủy các phiếu trả trước.

Công nợ `supplier_debts.balance`, `order_balance`, `search`, `totals` có thêm `returned`: đầu kỳ + nhận DA_GHI − trả hàng DA_GHI − trả tiền DA_GHI (order_balance không có đầu kỳ). OVER_PAYMENT kiểm cả nợ NCC và nợ đơn sau khi trừ trả hàng. Nợ âm là đã trả trước. Timeline thêm TH/PX giảm nợ ngày trả và hủy TH tăng lại nợ ngày hủy (giờ VN), số dư chạy khớp balance; các tổng GROUP BY độc lập để không nhân dòng.

Web GET/POST `/don-mua/lan-nhan/{receipt_id}/tra`: số lượng mặc định 0 để bỏ qua dòng, giữ ngày/lý do/số lượng/version khi lỗi. POST `/don-mua/tra-hang/{id}/huy`: lý do và xác nhận. Mọi web POST kiểm CSRF và quyền; finance chỉ xem lịch sử, không có nút thao tác. Chi tiết đơn hiện đã nhận và đã trả riêng, lịch sử TH liên kết PX. Công nợ NCC thêm cột Trả hàng và các sự kiện TH/PX.

Lỗi: OVER_RETURN/BAD_STATUS/VERSION_CONFLICT/SOURCE_LOCKED/HAS_RETURNS/NEGATIVE_STOCK (409), BAD_RECEIPT_LINE/DUPLICATE_LINE/BAD_RETURN/BAD_QTY/EMPTY_RETURN/REASON_REQUIRED/ROUNDING_CONFLICT (400), FORBIDDEN (403), NOT_FOUND (404). Schema API sai trả 422. NEGATIVE_STOCK có warehouse/product/available/requested, web hiển thị mã hàng, kho, tồn và số cần.

### Báo cáo mua hàng, tuổi nợ NCC và lãi gộp (M5)

Không migration, không ghi dữ liệu. `app.purchase_reports` chỉ SELECT/GROUP BY/window, bao quanh thực thi bằng no_autoflush; không nạp từng chứng từ vào ORM để cộng. Khoảng ngày mặc định tháng hiện tại, ngày theo giờ Việt Nam, tiền nguyên đồng. Quyền mua theo NCC/hàng: warehouse, finance, director; tuổi nợ và lãi gộp: finance, director. Vai trò khác FORBIDDEN (403), kể cả URL CSV.

- `purchases_by_supplier(session,user,start=None,end=None)` và `purchase_totals`: chỉ purchase_receipts/purchase_returns DA_GHI, lọc receipt_date/return_date riêng. Nhận gồm received_amount/received_vat/received_total, trả returned_total; net_total = received_total − returned_total (gồm VAT). NCC ngừng dùng vẫn hiện nếu có phát sinh. Hủy nhận/trả loại khỏi báo cáo, không sử dụng dòng đảo sổ kho để tính mua.
- `purchases_by_product` và `product_totals`: số lượng nhận/trả riêng, net_amount trước VAT = tiền nhận − tiền trả. avg_cost là bình quân **nhận trong kỳ**: SUM tiền nhận chưa VAT / SUM qty nhận, làm tròn đồng, không phải giá vốn tồn kho; không có nhận thì null. Tiền giảm nợ khi trả đã có bù làm tròn M4, báo cáo dùng số đã lưu.
- `supplier_aging(session,user,as_of=None)` và `aging_totals`: dùng đúng truy vấn nguồn của supplier_debts.balance (nợ hiện tại, không phải snapshot lịch sử). Phân bổ số còn nợ dương vào lần nhận mới nhất trước bằng window SUM, nên tiền trả/trả hàng trừ khoản cũ nhất. Đầu kỳ có ngày riêng để tính tuổi. Nhóm days_0_30, days_31_60, days_61_90, days_over_90; tuổi tương lai tính 0. Nợ âm nằm ở advance (số dương đã trả trước), các nhóm tuổi bằng 0. Tổng nhóm tuổi − advance = remaining ròng; NCC không còn nợ/đã trả trước không hiện.
- `gross_margin(session,user,start=None,end=None)` trả documents/totals và products/product_totals. Đơn DA_GIAO theo delivered_at; dịch vụ HOAN_THANH/DA_DONG đã khóa tiền theo priced_at, gồm **đã thu đủ và miễn phí**. Doanh thu DB = subtotal, DV = SUM service_lines.amount; miễn phí doanh thu 0 dù dòng còn giá niêm yết. Không tính VAT. Giá vốn = −SUM stock_ledger.amount của PX DA_GHI được gắn từ dòng chứng từ, is_reversal=false. Khử trùng cặp chứng từ/PX trước khi nối sổ để nhiều dòng cùng PX không nhân giá vốn. Lãi = revenue − cost, rate làm tròn 1 số lẻ; doanh thu 0 thì rate=null. Bảng mã hàng chỉ doanh thu HANG/VAT_TU và giá vốn group theo product_code của ledger; PHI/KHAC chỉ thuộc doanh thu chứng từ. Đơn hủy, DV hủy/mở lại và PX hủy không đóng góp giá vốn đang ghi.

Web GET `/bao-cao-mua-hang?tab=ncc|hang|tuoi-no|lai-gop&from=YYYY-MM-DD&to=YYYY-MM-DD`, preset `period=today|month|last_month`. Tab tuổi nợ dùng `as_of=YYYY-MM-DD` (mặc định hôm nay VN), độc lập khoảng mua/lãi gộp. Lãi âm tô đỏ; bảng cuộn trong khung trên điện thoại, trang không cuộn ngang. Warehouse chỉ thấy hai tab mua và không vào được hai tab tài chính bằng URL.

CSV GET `/bao-cao-mua-hang/{table}.csv` với table=ncc/hang/tuoi-no/lai-gop/lai-gop-hang; giữ cùng tham số lọc. UTF-8 BOM, tiền không có dấu chấm nghìn và không có phần lẻ; qty tối đa 3 số lẻ, tỷ lệ 1 số lẻ. Có dòng cộng, bảo vệ ô chữ bắt đầu bằng ký tự công thức Excel, Cache-Control private/no-store. Chỉ có GET, không thêm API ghi. Lỗi BAD_DATE_RANGE/BAD_REPORT (400), FORBIDDEN (403).

### Tổng quan (R1)

`app.dashboard.summary(session,user,today=None)` chỉ đọc; director thấy toàn bộ, finance không có các key `gross_margin_month`, `inventory`, `inventory_total` và không chạy truy vấn của chúng. Vai trò khác FORBIDDEN (403). `today` là ngày neo báo cáo, mặc định ngày hiện tại Asia/Ho_Chi_Minh; hạn dịch vụ luôn so với thời điểm hiện tại.

- `revenue_today`, `revenue_month`, `revenue_last_month`: doanh thu chưa VAT = sales_reports.totals subtotal đơn DA_GIAO + ServiceTicket.subtotal của THU_TIEN, priced_at có, trạng thái HOAN_THANH/DA_DONG. Tháng này ngày 1 → today; tháng trước toàn tháng, kể cả qua năm/nhuận. Ngày giao/chốt giá đổi theo giờ VN. `revenue_daily` đủ 30 ngày đến today, không phát sinh = 0; SQL nhóm tổng ngày rồi chỉ ghép tối đa 30 tổng trong Python.
- Lãi gộp tháng này lấy nguyên `purchase_reports.gross_margin(...month_start,today)['totals']['profit']`, không tính lại; định nghĩa M5 vẫn gồm dịch vụ miễn phí với giá vốn.
- `funds`/`fund_total` từ finance_reports; `customer_debt` từ debt_totals.remaining; `supplier_debt` từ purchase_reports.aging_totals.remaining (có bù trả trước).
- Giá trị tồn theo kho = ROUND(SUM(qty × avg_cost)), gồm kho không phát sinh (0); tổng bằng SUM các giá trị đã làm tròn theo kho. Không dùng cột value thay công thức được yêu cầu.
- `services`: count trạng thái trong service_tickets.OPEN và count còn mở có due_at < now. `pending_receipts`: count/amount của CHO_XAC_NHAN, gồm cả nguồn DB và DV.

GET `/tong-quan`: lưới 2 cột điện thoại/4 cột desktop, link tới các trang báo cáo/quỹ/nợ/tồn/dịch vụ/báo thu có sẵn, SVG 30 cột và bảng số liệu ngày mở rộng. Tiền nguyên đồng có dấu chấm nghìn. Menu Tổng quan đầu tiên chỉ cho director/finance. GET `/` chuyển hai vai trò này sang `/tong-quan`; các vai trò khác và chưa đăng nhập giữ hướng `/khach-hang`. Không thêm POST, migration hoặc ghi sổ. Tất cả truy vấn dùng no_autoflush; kiểm chứng bằng transaction READ ONLY và đối chiếu audit/sổ kho/sổ quỹ không đổi.

### Cảnh báo theo luật (R2)

`app.alerts.collect(session,user,today=None,now=None) -> list[dict]` trả `AlertList` (vẫn là list), mỗi mục chỉ có level (DO/VANG), kind, title/detail tiếng Việt và link. Metadata `result.counts[kind]` là tổng số thật trước giới hạn; tối đa 20 mục/kind, ưu tiên DO rồi kind rồi thời điểm cũ nhất, khóa hòa theo link. Counts gồm các kind bằng 0, riêng CHAM_LUAN_CHUYEN bị loại cả metadata khi người xem không phải director. Quyền qua dashboard.check_reader: finance/director, còn lại FORBIDDEN (403).

Ngưỡng module `app.config`: ALERT_RECEIPT_PENDING_DAYS=1, ALERT_DEBT_DAYS=30, ALERT_SLOW_STOCK_DAYS=90, ALERT_CLOSING_LOOKBACK_DAYS=7. Ngày/giờ Asia/Ho_Chi_Minh; today/now có thể truyền để kiểm thử (now thiếu timezone hiểu là giờ VN).

- DV_QUA_HAN (DO): trạng thái OPEN và due_at < now, link phiếu DV.
- DUOI_TON_TOI_THIEU: mức >0 và tồn **nhỏ hơn** mức; không có balance coi là 0; tồn <=0 DO, còn lại VANG. Bằng mức không cảnh báo. Link thẻ kho.
- BAO_THU_CHO (VANG): CHO_XAC_NHAN, reported_at < now − số ngày ngưỡng (đủ số ngày chính xác chưa tính là cũ hơn), link /bao-thu.
- KHACH_VUOT_HAN_MUC (DO): cùng nguồn finance_reports._debts/customers, gồm DB và DV; nợ > credit_limit hoặc allow_debt=false và có nợ. Link chi tiết công nợ khách.
- KHACH_NO_QUA_HAN: dùng sales_reports._debts, nhóm theo khách, chỉ phần nợ quá ngưỡng; khoản cũ nhất >60 ngày DO, còn lại VANG. Trả đủ không còn cảnh báo.
- NCC_NO_QUA_HAN (VANG): dùng phép phân bổ FIFO M5, chỉ khoản phân bổ còn nợ quá ngưỡng; nợ mới, đã trả đủ/đã trả trước không cảnh báo. `_aging` chỉ thêm tùy chọn nội bộ overdue_days để trả overdue/oldest, không đổi kết quả các API M5 đang có và không chép công thức FIFO.
- DON_MUA_TRE (VANG): DA_DAT/NHAN_MOT_PHAN có expected_date < today, link đơn mua.
- CHUA_CHOT_SO (VANG): ngày có CashDoc DA_GHI trong [today − lookback, today), và day_closings.is_closed=false; không tính hôm nay/ngày không phát sinh, ngày đã mở khóa có thể hiện lại. Không giữ khóa advisory hay ghi ngày mới.
- CHAM_LUAN_CHUYEN (VANG, chỉ director): còn tồn >0, PN DA_GHI đầu tiên tại kho/mã cũ hơn ngưỡng, không có PX DA_GHI gốc trong [today − ngưỡng,today]. Dùng doc_date theo ngày nghiệp vụ; bỏ bút toán đảo và PX/PN đã hủy. Vì vậy xuất rồi hủy được coi là không xuất. Detail có ROUND(qty × avg_cost), link thẻ kho.

Tất cả chỉ SELECT, no_autoflush cả kiểm quyền, COUNT OVER trước LIMIT cho tổng chính xác; không nạp ORM chứng từ và không đổi audit/sổ kho/quỹ/công nợ. Chốt sổ nhóm theo ngày rồi gọi helper T4 cho từng ngày trong lookback. Trên `/tong-quan`, khối Cần xử lý ngay dưới lưới số, nhóm details/summary, chấm đỏ/vàng có nhãn truy cập, link cả dòng và thông báo “và N mục khác”; rỗng hiện “Không có việc cần xử lý”. Không thêm POST/migration/AI.

## Tóm tắt ngày (R3)

- `daily_digest.build(session, user, day)` chỉ đọc Tổng quan/cảnh báo bằng quyền giám đốc; kết quả JSON gồm ngày ISO, tiền nguyên đồng, số ô chính, quỹ/kho, counts thật theo kind và tối đa 5 cảnh báo đỏ đầu tiên. `alerts.collect(...).red_count` đếm toàn bộ cảnh báo đỏ trước giới hạn 20 dòng/kind; không đổi cấu trúc các mục cảnh báo R2.
- `daily_digest.save(session, user, day)` chỉ giám đốc đang hoạt động: khóa riêng theo ngày, tạo/ghi đè một bản, tăng version mỗi lần, ghi audit `digest.create`/`digest.update` trước/sau trong cùng transaction. `created_at`/`created_by` mô tả bản chụp hiện hành; nguồn CLI để `created_by=null`, audit vẫn ghi tài khoản giám đốc đã dùng. Không ghi hay khóa ngày sổ kho/quỹ/công nợ.
- `--day` neo khoảng doanh thu vào ngày yêu cầu; quỹ, nợ, tồn, việc đang mở và cảnh báo thời điểm vẫn theo số liệu nguồn tại lúc chạy, không tái dựng số dư lịch sử. Bản đã lưu giữ nguyên cho đến lần lưu đè kế tiếp.
- GET `/tong-quan/ngay?page=1` (30 bản/trang, mới nhất trước), GET `/tong-quan/ngay/{day}`: director/finance. Finance được lọc cả JSON dùng để render và văn bản sao chép, loại tồn kho, lãi gộp và CHAM_LUAN_CHUYEN. POST `/tong-quan/ngay` lưu hôm nay giờ Việt Nam, chỉ director và phải có `csrf`.
- Lỗi: `FORBIDDEN` (403), `BAD_CSRF` (403), `NOT_FOUND` (404, chưa lưu ngày đó), `VERSION_CONFLICT` (409). `python -m scripts.daily_digest --user tuan [--day YYYY-MM-DD]` kiểm tài khoản tồn tại, đang hoạt động, có vai trò director trước khi ghi; lỗi tài khoản thoát mã 1.

## Menu và quyền trang web (U1)

`app/web/nav.py` là cấu hình duy nhất cho nhãn/link, prefix và vai trò của từng mục, cùng thứ tự trang đầu. Giám đốc nhận mọi vai trò nghiệp vụ qua `has_role`. Người nhiều vai trò thấy hợp các mục được phép; trang đầu ưu tiên finance/director → Tổng quan, dispatcher → Điều phối, reception → Dịch vụ, rồi technician/sales/warehouse → Việc của tôi. Đăng nhập thành công luôn về trang đầu này, không chuyển theo `next` do trình duyệt gửi lên.

Dependency `check_navigation` gắn trên toàn bộ router web, trước khi đăng ký các route thường/tạo động/router con. Nó kiểm cả GET, kết quả tìm sống, CSV và POST; truy cập không được phép sẽ trả HTML 403 với câu “Bạn không có quyền vào trang này” và link về `/`. Prefix khớp ranh giới đường dẫn, prefix cụ thể nhất thắng; route chưa khai báo bị chặn mặc định. Đăng nhập, đăng xuất và `/` có luồng riêng. API Bearer và quyền ghi/kiểm phiếu được giao trong lõi giữ nguyên.

- `/ton-kho?canh-bao=1` thuộc Sắp hết (warehouse), khác Tồn kho/Thẻ kho (sales, warehouse, finance); các dạng bool theo cùng parser FastAPI/Pydantic (`true`, `on`, `yes`, `t`, `y`, kể cả chữ hoa) cũng nhận cùng quyền để không lách bằng query.
- `/dich-vu/can-thu` và CSV cùng prefix thuộc Báo thu (finance); các bộ chọn máy/vật tư, ảnh và biên bản dưới `/dich-vu/...` vẫn theo mục Dịch vụ. File `/attachments/{id}/file` tiếp tục kiểm thực thể ở lõi, gồm chỉ phiếu được giao cho thợ.
- `/viec-cua-toi` là trang công việc U2 cho mọi vai trò nghiệp vụ. Nhóm còn một mục hiện thẳng link; desktop nhóm dùng details/summary, mobile có nút Menu; không thêm thư viện JS.

`tests/test_nav.py` đối chiếu riêng bảng vai trò với menu HTML và HTTP, kiểm mọi prefix có route thật, mọi GET web thuộc một mục và mọi route web nhận dependency chặn quyền. Không migration, không đổi luật hoặc dữ liệu sổ kho/quỹ/công nợ.

## Việc của tôi và bước tiếp theo (U2)

`my_work.collect(session, user, today=None)` chỉ SELECT, không autoflush hoặc nạp ORM chứng từ. Mỗi nhóm có key, tiêu đề, tổng thật, tối đa 20 dòng và link danh sách lọc `/viec-cua-toi?nhom=...&trang=1` (20 dòng/trang). Nhóm của người nhiều vai trò được hợp theo key; giám đốc thấy mọi nhóm. Trang đầu theo vai trò giữ nguyên U1; tài khoản admin không có vai trò nghiệp vụ bị 403, không lỗi 500.

- Kỹ thuật: phiếu được giao cho mình, ưu tiên quá hạn rồi Khẩn/Gấp/Thường; chờ nhận, cần hẹn khách sau khi nhận, lịch hẹn hôm nay hoặc từ ngày trước, đang làm/chờ linh kiện, hoàn thành/đã đóng chưa báo thu đủ. Khoảng ngày hẹn theo Asia/Ho_Chi_Minh, SLA dùng hạn do lõi `service_sla` tính.
- Bán hàng: báo giá nháp/đã gửi, đơn nháp, đơn chờ giao, đơn đã giao chưa báo thu đủ và báo thu bị từ chối còn cần báo lại; chỉ chứng từ của mình, trừ giám đốc. “Chưa báo thu đủ” = phải thu − đã xác nhận − chờ xác nhận > 0, dùng tổng báo thu B4/T3, không tính lại công nợ.
- Kho: đơn bán chờ giao, đơn mua đã đặt/nhận một phần và Sắp hết. `stock_min_levels.low_stock_query` dùng chung với trang Sắp hết, kể cả mã chưa có balance; giữ điều kiện tồn <= mức tối thiểu hiện hành.
- Điều phối/lễ tân: phiếu mới chưa giao; điều phối thêm dịch vụ chưa báo thu/chờ đóng. Kế toán: báo thu chờ xác nhận. Menu Việc của tôi hiện cho các vai trò có nhóm.

Khối Bước tiếp theo chỉ chọn form/route hiện có theo quyền và trạng thái: gửi/chấp nhận báo giá, tạo đơn từ báo giá, chốt/giao/báo thu đơn, giao/nhận/hẹn/làm/hoàn thành dịch vụ, báo thu/đóng phiếu và xác nhận/từ chối báo thu. Nút ở đầu trang submit đúng form cũ bằng thuộc tính `form`, giữ CSRF, version, kiểm dữ liệu chưa lưu và validation của trình duyệt/lõi. GET `/bao-thu/{id}` cho finance/director hiển thị một báo thu; nút quyết định ở khối mới gửi `return_to=work`, thành công về Việc của tôi với thông báo xanh, các POST cũ giữ redirect cũ.

Lõi dịch vụ hiện yêu cầu hẹn khách trước khi bắt đầu, lưu cách thu/miễn phí và ảnh SAU trước khi hoàn thành; hoàn thành tự khóa giá. Nút chính dẫn tới bước còn thiếu, không thêm đường bỏ qua luật. Ràng buộc DB cũng không cho phiếu hoàn thành chưa khóa giá. KT1 bổ sung quyền báo thu cho thợ được giao phiếu; quyền đóng phiếu vẫn chỉ điều phối/giám đốc. Không đổi schema, luật tiền/kho hay quyền ghi.


## Thợ báo thu tại chỗ (KT1)

`check_ticket_reporter(user, ticket)` cho dispatcher/sales/director hoặc technician có `ticket.assignee_id == user.id`. `report_for_ticket` kiểm quyền sau `_ticket(... FOR UPDATE, populate_existing=True)` trong cùng transaction, trước version và trạng thái: thợ khác nhận `FORBIDDEN` 403, kể cả session đã tải người được giao cũ. API POST `/service-tickets/{id}/receipts` và web POST `/dich-vu/{id}/bao-thu` dùng chung kiểm này. Giữ nguyên điều kiện HOAN_THANH/DA_DONG có thu tiền, OVER_PAYMENT tính cả báo thu chờ, version, số BT và audit; báo thu không tạo PT hay ghi quỹ. Chỉ kế toán/giám đốc xác nhận; chỉ giám đốc hủy BT đã xác nhận.

`held_by(session, user)` chỉ SELECT/no_autoflush: trả `rows` (id/số BT, số DV hoặc DB, khách, amount nguyên đồng, received_at) và `total`. Chỉ tiền mặt CHO_XAC_NHAN do chính user báo; chuyển khoản, BT xác nhận/từ chối/hủy và người khác bị loại. Không lọc theo người hiện được giao phiếu, vì chuyển việc không chuyển tiền người báo đang giữ.

GET `/tien-toi-giu` cho mọi tài khoản đăng nhập, kể cả admin-only; tất cả chỉ thấy tiền mình giữ. Menu khai báo tại nav.py dùng `menu_roles` riêng (mặc định bằng roles) để hiện mục này cho kỹ thuật/giám đốc, trong khi vai trò khác vẫn được mở trang cá nhân. Sau Hoàn thành, thợ được giao có nút U2 “Báo đã thu”, dùng form cũ có CSRF/version, cách thu là nút chạm, số tiền mặc định còn nợ trừ chờ, nhập `50.000` lưu 50000; lịch sử từng BT giữ nguyên. Không migration, không sửa cash_posting, stock_* hoặc doc_counters.

## Kho riêng của thợ (KT2)

Warehouse có `kind=KHO|THO` (kho cũ giữ KHO), `holder_id` FK users: KHO không có holder, THO bắt buộc holder có vai trò technician thực. Unique index chỉ áp dụng THO đang active: một thợ có tối đa một kho đang dùng. API danh mục kho nhận/trả hai trường mới; kiểm version/audit như cũ. `BAD_HOLDER`, `DUPLICATE_HOLDER` (409), `WAREHOUSE_NOT_EMPTY` (409) khi ngừng kho thợ còn tồn hoặc đổi loại/người giữ khi còn hàng; khóa kho trước kiểm tồn để không lọt giao dịch ghi sổ đồng thời.

`service_charges.set_charges` kiểm `TECH_WAREHOUSE_ONLY` 409 nếu kỹ thuật (không kiêm điều phối/giám đốc) có kho THO active mà gửi VAT_TU từ kho khác. Thợ chưa có kho giữ cách cũ; điều phối/giám đốc được chọn bất kỳ kho hợp lệ. Chỉ giới hạn lúc sửa dòng, không tự đổi dữ liệu dòng cũ. Form gợi ý mã đang có tồn dương trong kho của người sửa và hiện số đang giữ; dòng mới chọn sẵn kho thợ. Dòng cũ do điều phối lưu ở kho khác vẫn hiển thị kho đã lưu và yêu cầu chọn lại khi thợ sửa.

Không đổi `stock_posting` hay giá vốn: CK giữa kho công ty/kho thợ bảo toàn số lượng và giá trị toàn công ty; chỉ khi dịch vụ hoàn thành, PX hiện có xuất vật tư một lần từ kho đã lưu trên dòng. Mở lại dùng release/void_in_tx cũ để đảo đúng PX. Kiểm kê kho thợ được phép, phải có reason (REASON_REQUIRED). Không thêm đường ghi tồn hoặc quỹ, không sửa doc_counters.

Kho THO bị loại khỏi lựa chọn và kiểm tra lõi đơn mua/đơn bán; nhận hàng kiểm lại loại kho phòng trường hợp kho đổi loại sau khi đặt đơn. Mức cảnh báo tồn không nhận kho thợ (BAD_WAREHOUSE 409); Sắp hết và cảnh báo R2 cũng loại kho thợ, kể cả mức đã tồn tại trước khi đổi loại. Báo cáo tồn chia Kho công ty / Thợ đang giữ, tổng toàn bộ vẫn gồm cả hai; báo cáo/tổng quan gốc không lọc bỏ kho thợ.

GET `/hang-toi-giu`: kỹ thuật chỉ kho active của mình, query `tho` khác mình bị FORBIDDEN 403; warehouse/director dùng `?tho={id}` để xem thợ. Mọi dữ liệu chỉ SELECT, gồm số lượng dương và CK DA_GHI gần nhất theo mã (bỏ phiếu đã hủy/dòng đảo). Link CK của thợ mở GET `/hang-toi-giu/phieu/{id}` chỉ đọc, chỉ phiếu liên quan kho mình; không mở quyền vào toàn bộ /phieu-kho hoặc bất kỳ POST nào. Thủ kho/giám đốc có nút Trả kho mở CK với nguồn đã chọn, vẫn phải chọn đích/dòng và ghi qua form cũ có CSRF/version. Menu Hàng tôi giữ dành cho kỹ thuật/giám đốc; thủ kho mở từ danh mục Kho → Xem hàng thợ giữ.

## Yêu cầu linh kiện và tạm dừng (KT3)

- `POST /service-tickets/{id}/part-requests`: `{version, product_code, qty, reason}`; version của phiếu dịch vụ, số lượng > 0. Thợ được giao hoặc điều phối tạo khi `DANG_LAM`/`CHO_LINH_KIEN`; tự chuyển `DANG_LAM` sang `CHO_LINH_KIEN`, có bước lịch sử và audit trong cùng transaction. `requester_id` là người bấm tạo.
- `GET /service-tickets/{id}/part-requests`: lịch sử yêu cầu của phiếu, theo quyền xem phiếu.
- `GET /part-requests?status=CHO_CAP`: thủ kho/giám đốc xem danh sách cũ trước.
- `POST /part-requests/{id}/supply`: `{version, warehouse_code}`; version của yêu cầu. Chỉ kho công ty `KHO` đang dùng; lập và ghi CK trong transaction. Kho nhận được tra **lúc cấp**, theo thợ đang được giao; `from_warehouse_code`, `to_warehouse_code`, `stock_doc_id` lưu dấu vết cấp hàng kể cả sau này đổi thợ.
- `POST /part-requests/{id}/out-of-stock`: `{version, note}`; thủ kho/giám đốc, bắt buộc ghi chú. Chuyển `HET_HANG`; giao diện chỉ mở form đơn mua điền sẵn mã, không tự lập đơn.
- `POST /part-requests/{id}/cancel`: `{version, note}`; người tạo hoặc điều phối/giám đốc hủy yêu cầu còn `CHO_CAP`, bắt buộc lý do.
- `POST /service-tickets/{id}/transition`: thêm `to_status=TAM_DUNG`, `hold_reason` là `CHO_KHACH_DUYET`, `CHO_KY_THUAT_KHAC`, `CHO_DOI_TAC`, `HEN_NGAY_KHAC` hoặc `KHAC`. `KHAC` bắt buộc `note`. Chỉ từ `DANG_LAM`; quay về `DANG_LAM` xóa `hold_reason`. Hủy/không làm được theo cùng luật chờ linh kiện. Tạm dừng không đóng phiếu, không ghi tồn.

Mã lỗi: `FORBIDDEN` (403), `NOT_FOUND` (404), `BAD_STATUS` (409), `VERSION_CONFLICT` (409), `NO_TECH_WAREHOUSE` (409: thợ đang được giao chưa có kho riêng), `BAD_PRODUCT`, `BAD_WAREHOUSE`, `REASON_REQUIRED`, `HOLD_REASON_REQUIRED`, `BAD_HOLD_REASON`, `NEGATIVE_STOCK` (409, kèm mã/kho/tồn/số cần). Thiếu tồn không tạo CK và không đổi yêu cầu. Mọi thay đổi yêu cầu ghi `part_request.create/supply/out_of_stock/cancel` vào audit với trước/sau.

`TECH_WAREHOUSE_ONLY` (409) chỉ áp cho vật tư thợ thêm hoặc thay mã/kho/số lượng. Dòng kho công ty được điều phối lưu trước có thể giữ nguyên khi thợ bổ sung phí; không thể nhân bản dòng đó. Lưu dòng tiền/vật tư cũng tăng version phiếu, kể cả không thay cách thu.

Web: `/yeu-cau-linh-kien` (Kho), các form yêu cầu/hủy trên `/dich-vu/{id}` đều có CSRF. `/dieu-phoi?view=paused` và `/dieu-phoi/bao-cao?status=TAM_DUNG&from=YYYY-MM-DD&to=YYYY-MM-DD` có lý do tạm dừng; báo cáo lọc các phiếu còn tạm dừng có bước tạm dừng gần nhất trong kỳ, theo giờ Việt Nam.

## Giữ máy khách (KT4)

Máy khách chỉ nằm trong `equipment_custody`, không lập chứng từ kho/quỹ và không thay tồn. Một máy chỉ có một bàn giao `CHO_NHAN`; khóa theo phiếu dịch vụ → thiết bị → dòng bàn giao. Bộ đệm `equipment.custody_location/holder_id/ticket_id` chỉ đổi cùng transaction với dòng `DA_NHAN`.

Theo xác nhận của thẻ, bảng không cho sửa nội dung hoặc xóa. Trigger chỉ cho chốt **một lần** `CHO_NHAN → DA_NHAN/DA_HUY`, chỉ đổi `status`, `confirmed_by`, `confirmed_at`, `version` (+1). Mọi dòng đã chốt bất biến. Hủy bàn giao không đổi người đang giữ.

| API | Nội dung và quyền |
| --- | --- |
| GET `/service-tickets/{id}/custody` | Lịch sử giữ máy; điều phối/kho/giám đốc, thợ được giao hoặc người tham gia giữ máy |
| POST `/service-tickets/{id}/custody/receive` | `{version, accessories, accessories_note, condition_note}`; version **thiết bị**. Thợ được giao/điều phối nhận về khi phiếu mở, có serial; ghi `THO_GIU`, `DA_NHAN` ngay cho thợ được giao |
| POST `/service-tickets/{id}/custody/handover` | `{version, location, holder_id?, partner_name?, expected_back?, accessories?, accessories_note?, condition_note?}`; version **thiết bị**. Người giữ/điều phối tạo chờ nhận. Kho kỹ thuật do thủ kho/điều phối giao tiếp, đối tác do điều phối xử lý. Nội dung bỏ trống kế thừa lần nhận trước |
| POST `/equipment-custody/{id}/confirm` | `{version}` của **dòng bàn giao**. Thợ nhận phải đúng `holder_id`; kho kỹ thuật do kho/điều phối, đối tác do điều phối xác nhận |
| POST `/equipment-custody/{id}/cancel` | `{version}` của **dòng bàn giao**; chỉ người giao hủy khi còn chờ |
| POST `/service-tickets/{id}/custody/return` | `{version, accessories, accessories_note?, condition_note?}`; version **thiết bị**. Chỉ thợ thực sự đang giữ máy trả khách, không cần là thợ được giao sửa |

`accessories` bắt buộc khai báo danh sách mã (`DAY_NGUON`, `KHAY_GIAY`, `HOP_MUC`, `DAY_USB`, `KHAC`); `[]` nghĩa là xác nhận không kèm phụ kiện. Web có nút Không kèm phụ kiện; không được chọn đồng thời nút đó và phụ kiện. `KHAC` phải có ghi chú. Khi trả, đối chiếu với **lần nhận khách mở chu kỳ**, kể cả phụ kiện đã thay đổi ở các lần bàn giao; thiếu phải ghi chú, vẫn được trả.

Ảnh bắt buộc là JPEG/PNG/WEBP, không dùng PDF hay ảnh đã ẩn. Thêm tag `NHAN_MAY`, `TRA_MAY` vào API tệp; khuyến nghị `POST /attachments` với `entity=equipment_custody`, **`entity_id` là id phiếu dịch vụ**, `tag`, `file`. Đây là vùng ảnh giữ máy riêng: thợ B nhận máy của thợ A đọc/tải ảnh giữ máy được, không đọc ảnh sửa chữa, tiền hoặc chuyển trạng thái phiếu của A. `NHAN_MAY` do thợ được giao/điều phối tải, `TRA_MAY` do thợ đang giữ tải. Ảnh của chu kỳ đã trả trước không thay thế ảnh nhận/trả mới. API vẫn nhận ảnh đúng tag ở entity `service_ticket` với quyền tệp hiện hữu.

Chuyển sang `HOAN_THANH`, `KHONG_LAM_DUOC`, `DA_HUY`, `DA_DONG` kiểm máy đã về `TAI_KHACH` trước khi kết thúc. Người chỉ được giao giữ máy bị `FORBIDDEN` (403) khi sửa phiếu, còn mở toàn bộ phiếu sửa của người khác vẫn 404. Đường đi Không sửa, trả máy: trả máy rồi mới Không làm được.

Mã lỗi tiếng Việt: `SERIAL_REQUIRED`, `CONDITION_REQUIRED`, `ACCESSORIES_REQUIRED`, `BAD_ACCESSORIES`, `PARTNER_REQUIRED`, `BAD_HOLDER`/`BAD_ASSIGNEE`, `BAD_LOCATION`, `PHOTO_REQUIRED`, `BAD_FILE_TYPE`, `MISSING_ACCESSORIES` (kèm danh sách thiếu), `REASON_REQUIRED`, `MACHINE_HELD` (409, kèm phiếu đang giữ), `HANDOVER_PENDING` (409), `MACHINE_NOT_RETURNED` (409), `VERSION_CONFLICT` (409), `BAD_STATUS` (409), `SAME_HOLDER` (409), `FORBIDDEN` (403), `NOT_FOUND` (404).

Web: khối Máy khách trên `/dich-vu/{id}`; `/giu-may/{id}` chỉ có thao tác giữ máy để người nhận không cần quyền sửa phiếu. Mọi POST có CSRF, gồm tải ảnh. `/thiet-bi/{serial}` có lịch sử; `/may-dang-giu?location=...` chỉ điều phối/kho/giám đốc, đỏ khi quá 7 ngày **từ lúc nhận khách trong chu kỳ hiện tại** (đổi người giữ không đặt lại tuổi) hoặc quá `expected_back`, theo giờ Việt Nam. Việc của tôi có Máy tôi đang giữ, Máy chờ tôi nhận, Máy chờ nhận vào kho kỹ thuật. Audit `custody.receive/handover/confirm/cancel/return` và `equipment.custody` lưu trước/sau trong cùng transaction.


## Chống gửi trùng form web (S0)

Form POST đã đăng nhập gửi cả `csrf` và `form_key` (UUID mới mỗi lần vẽ form).
Khóa được lưu bằng transaction riêng trước khi gọi nghiệp vụ. Gửi lại cùng khóa
và người dùng: phiếu đã xong trả 303 về URL kết quả cũ; đang xử lý trả 409
`ALREADY_SUBMITTED`. Thiếu/sai UUID: 400 `BAD_FORM_KEY`; khóa của người khác: 403
`FORBIDDEN`. CSRF vẫn được kiểm trước khóa. API JSON giữ nguyên hợp đồng hiện tại.

Form báo lỗi nghiệp vụ/nhập liệu được gỡ khóa để sửa và gửi lại; form vẽ lại có
khóa mới. Lỗi không xác định giữ `DANG_XU_LY`, kể cả khi chứng từ đã commit, để
không tạo trùng. Chỉ cập nhật khóa một lần sang `XONG` (trigger bảo vệ).

Dọn khóa cũ hơn 30 ngày bằng lệnh chạy tay: `python -m app.form_keys purge`.
Lệnh áp dụng cho cả khóa hoàn tất và khóa đang xử lý đã quá hạn; không có cron tự chạy.

## Giữ hàng và tồn khả dụng (S1)

Chốt đơn bán giữ phần tồn khả dụng cho từng dòng HANG trong cùng transaction;
thiếu hàng vẫn chốt và hiển thị phần thiếu. Tồn thực tế và giá vốn không thay đổi
khi giữ/nhả. `stock_reservations.available(session, warehouse_code, product_code)`
đọc tồn thực tế trừ tổng GIU; `quantities` là bản đọc theo lô dùng chung cho màn hình.
Dòng đơn hiện dùng PK `(order_id, line_no)`, nên `order_line_id` lưu `line_no` và có
FK ghép `(order_id, order_line_id)` về dòng đơn. Không đổi khóa dòng đơn cũ.

- `POST /sales-orders/{id}/reserve`: `version` của đơn; người lập đơn (sales), kho hoặc giám đốc giữ thêm phần còn thiếu. Dòng GIU cũ được chốt DA_NHA với lý do “Giữ thêm”, dòng mới giữ tổng mới.
- `POST /sales-orders/{id}/reservations/{reservation_id}/release`: `version`, `reason`; chỉ kho/giám đốc, đơn DA_CHOT. Nhả tay không hủy đơn.
- `PATCH /sales-orders/{id}/promised-date`: `version`, `promised_date` (ISO hoặc null để xóa); sales/giám đốc, đơn NHAP/DA_CHOT. Không mở khóa giá.
- Tạo/sửa nháp/tạo từ báo giá cũng nhận `promised_date`. `GET /sales-orders?holding=true` chỉ lấy đơn còn GIU.

PX và chiều xuất CK kiểm khả dụng trong `stock_posting`, sau khi khóa dòng tồn.
`RESERVED_STOCK` (409) có `warehouse`, `product`, `stock`, `reserved`, `available`,
`requested` và `orders` (các số đơn đang giữ). Thông báo tiếng Việt nêu đủ các số này.
Xuất quá tồn thực tế vẫn dùng `NEGATIVE_STOCK`. `post_in_tx(..., for_order_id=id)`
là tham số nội bộ cho giao đơn, bỏ qua phần GIU của chính đơn đó; API ghi sổ tay
không nhận tham số này. Giao thành công chốt GIU → DA_XUAT; hủy đơn chốt GIU → DA_NHA.

PN, KK và void không kiểm giữ hàng. Nếu tồn thấp hơn tổng giữ: trang tồn/thẻ kho
hiện “Giữ vượt tồn”, Tổng quan có cảnh báo đỏ `GIU_VUOT_TON`. Lịch sử giữ không xóa
và không sửa số lượng; trigger chỉ cho chốt một lần từ GIU sang DA_XUAT/DA_NHA.
Các mã khác: `REASON_REQUIRED`, `VERSION_CONFLICT`, `BAD_STATUS`, `FORBIDDEN`,
`BAD_RESERVATION` (mã dòng giữ trên form sai), `NOT_FOUND`.

Các đơn đã chốt trước khi nâng cấp chưa có dòng giữ: trang đơn hiện phần chưa giữ
và có nút “Giữ thêm” để phân bổ theo khả dụng hiện tại; migration không tự sửa sổ
hoặc tự chọn thứ tự giữ cho các đơn cũ.

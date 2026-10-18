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

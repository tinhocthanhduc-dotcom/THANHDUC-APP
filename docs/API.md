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

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

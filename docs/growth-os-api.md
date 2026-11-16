# Cổng chỉ đọc cho Growth OS (`/marketing/v1`)

TUẤN 16/11/2026, Phần A của phiếu "ERP cho Growth OS". Code: `app/marketing_api.py`, test: `tests/test_growth_os_doc.py`.

## Khóa và phạm vi

- Giám đốc tạo khóa ở **Quản trị › Trợ lý AI**, dòng **Growth OS · Marketing (chỉ đọc)** (agent `growth-os`, phạm vi `marketing_read`).
  Khóa chỉ hiện một lần; tạo lại thì khóa cũ bị thu hồi. Tạm dừng / thu hồi ở cùng chỗ.
- Gửi kèm `Authorization: Bearer tdai_…`.
- Chỉ dùng **từ trong máy chủ Z2**: Growth OS nối vào mạng `deploy_default` và gọi `http://api:8000`
  (như n8n). Yêu cầu đi qua Cloudflare (có `cf-ray` / `cf-connecting-ip`) bị từ chối 403.
- Chỉ có 3 đường GET dưới đây. Khóa này không mở được đường nào khác của app (khách hàng, đơn, sổ quỹ, `/ai/*`, `/n8n/*`…).
- Không trả: giá vốn, giá trị tồn, khách hàng, số điện thoại, đơn hàng.
- Mỗi lần đọc ghi một dòng nhật ký `ai.marketing_read` (đường, tham số, số dòng), xem ở Trợ lý AI › Nhật ký.

Lỗi: `401` thiếu/sai/đã thu hồi khóa · `403` khóa khác phạm vi hoặc đi qua Cloudflare · `423` giám đốc đang tạm dừng ·
`422` tham số sai · `404` không có mã · `405` mọi phương thức khác GET.

## GET /marketing/v1/products

Tham số: `updated_since` (ISO, tùy chọn), `limit` (1–1000, mặc định 500), `cursor` (lấy từ `next_cursor` của trang trước).

```json
{"as_of": "2026-11-16T21:30:00+07:00",
 "items": [{"code": "TD-0045", "name": "Hộp mực in 136X-W1360X", "group": "Hộp mực", "unit": "hộp",
            "sale_price": 200000, "price_includes_vat": false, "vat_rate": 8, "sale_price_with_vat": 216000,
            "warranty_months": 0, "active": true, "updated_at": "2026-11-16T09:12:03.120000+07:00", "data_version": 3}],
 "next_cursor": null}
```

- `sale_price` là giá bán **chưa VAT** đang lưu trong app (app luôn lưu giá chưa VAT). `sale_price_with_vat` = làm tròn `sale_price × 1,08`.
- Có cả mã đã ngừng bán (`active: false`) để Growth OS ẩn đi. Không có hàng chạy thử.
- `data_version` tăng mỗi lần sửa mã hàng; `updated_at` đổi theo. Đọc tăng dần: lưu `updated_at` lớn nhất đã thấy, lần sau gửi `updated_since` bằng giá trị đó (trùng thì bỏ qua theo `code`).
- Sắp theo `updated_at`, rồi `code`. `next_cursor` khác null thì gọi tiếp với `cursor=` (nhớ mã hóa URL dấu `+`).

## GET /marketing/v1/stock?codes=TD-0045,TD-0046

Tối đa 200 mã/lần.

```json
{"as_of": "…", "warehouses_counted": "Mọi kho bán hàng đang dùng (loại KHO). Không tính kho thợ, kho hàng lỗi/thanh lý.",
 "items": [{"code": "TD-0045", "available": 11, "min_level": 5, "as_of": "…",
            "by_warehouse": [{"warehouse": "145", "name": "Kho 145", "available": 3}, {"warehouse": "58", "name": "Kho 58", "available": 8}]}],
 "not_found": ["TD-9999"]}
```

- `available` = tồn − đang giữ cho đơn đã chốt, cộng mọi kho loại **KHO** đang dùng (hiện là kho 58 và 145).
  **Không** tính kho thợ (THO, hàng thợ mang theo xe) và kho hàng lỗi/thanh lý (LOI). Kho sản xuất không phải loại riêng:
  thành phẩm nhập về kho KHO nào thì tính ở kho đó.
- Mỗi kho không âm (giữ nhiều hơn tồn thì tính 0).
- `min_level` = tổng mức tồn tối thiểu đã đặt ở các kho bán hàng (0 = chưa đặt).

## GET /marketing/v1/products/{code}/margin-band

```json
{"code": "TD-0045", "band": "CAO", "reason": null, "as_of": "…"}
```

- Biên lãi gộp = (giá bán chưa VAT − giá vốn bình quân) / giá bán chưa VAT. Giá vốn lấy như cảnh báo "bán dưới giá vốn"
  (kho bán mặc định, chưa có thì bình quân các kho KHO còn hàng).
- `CAO` ≥ 30% · `VUA` 15–30% · `THAP` 0–15% · `AM` < 0. Ngưỡng ở `BANDS` trong `app/marketing_api.py`.
- `band: null` khi chưa tính được: `reason` = `CHUA_CO_GIA_VON` hoặc `CHUA_CO_GIA_BAN`.
- Không bao giờ trả con số giá vốn hay phần trăm.

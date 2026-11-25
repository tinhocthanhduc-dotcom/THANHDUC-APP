# Cổng chỉ đọc cho Growth OS (`/marketing/v1`)

TUẤN 16/11/2026, Phần A của phiếu "ERP cho Growth OS". Code: `app/marketing_api.py`, test: `tests/test_growth_os_doc.py`.

## Khóa và phạm vi

- Giám đốc tạo khóa ở **Quản trị › Trợ lý AI**, dòng **Growth OS · Marketing (chỉ đọc)** (agent `growth-os`, phạm vi `marketing_read`).
  Khóa chỉ hiện một lần; tạo lại thì khóa cũ bị thu hồi. Tạm dừng / thu hồi ở cùng chỗ.
- Gửi kèm `Authorization: Bearer tdai_…`.
- Chỉ dùng **từ trong máy chủ Z2**: Growth OS nối vào mạng `deploy_default` và gọi `http://api:8000`
  (như n8n). Yêu cầu đi qua Cloudflare (có `cf-ray` / `cf-connecting-ip`) bị từ chối 403.
- Chỉ có các đường GET dưới đây (products, stock, margin-band, orders, ink-rhythm/summary). Khóa này không mở được đường nào khác của app (khách hàng, đơn, sổ quỹ, `/ai/*`, `/n8n/*`…).
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

## Phần B: nguồn khách trên báo giá và đơn bán (migration 0106)

TUẤN duyệt mẫu 16/11/2026. Không bắt buộc nhập; trống = `null`.

| Ô | Ở đâu | Giá trị |
|---|---|---|
| `lead_source` | quotes, sales_orders | `WEB`, `GOOGLE_MAPS`, `FACEBOOK`, `ZALO`, `SHOPEE`, `TIKTOK_SHOP`, `KHACH_CU`, `GIOI_THIEU`, `KHAC` |
| `campaign_code` | quotes, sales_orders | chữ tự do ≤ 40, app viết hoa (vd. `C-2612-136X`, `TD136X`); đối chiếu với Growth OS sau |
| `lead_ref` | quotes, sales_orders | mã khách hỏi ≤ 40 |
| `web_order_ref` | sales_orders | mã đơn WooCommerce/Shopee/TikTok ≤ 60; màn hình chỉ cho nhập khi nguồn là Web/Shopee/TikTok Shop |

- Đơn lập từ báo giá kế thừa 3 ô đầu; người lập đơn vẫn đổi được khi đơn còn nháp.
- Sau khi chốt: chỉ giám đốc sửa ("Sửa nguồn khách" trong Thao tác khác), không đổi tiền, ghi nhật ký `sales_order.lead`.
- Thêm `updated_at` cho quotes và sales_orders (chuẩn bị Phần C).

## Phần C: GET /marketing/v1/orders

Tham số: `changed_since` (ISO), `limit` (1–500, mặc định 200), `cursor` (từ `next_cursor`). Trả cả đơn bán (`kind: DON`) và báo giá (`kind: BAO_GIA`), sắp theo giờ đổi.
Không trả đơn/báo giá của khách chạy thử.

Mỗi bản ghi: `id`, `kind`, `number`, `status`, `date` (ngày lập), `customer_hash`, `net_amount`, `vat_amount`, `discount_amount`,
`lines[]` (`product_code` — null với dòng dịch vụ/khác, `qty`, `net_amount`), `lead_source`, `campaign_code`, `lead_ref`, `web_order_ref`,
`quote_id`, `confirmed`, `confirmed_at`, `delivered_net`, `returned_net`, `net_revenue`, `returns[]`, `refunds[]`, `cancelled_at`, `version`, `updated_at`
(báo giá có thêm `valid_until`).

Không trả: tên khách, mã khách, số điện thoại, địa chỉ, người liên hệ, ghi chú, mô tả dòng, giá vốn.

**`customer_hash`**: HMAC-SHA256 của mã khách với khóa bí mật của app (20 ký tự đầu). Cùng khách thì cùng mã băm, kể cả giữa đơn và báo giá;
khách đã gộp dùng mã khách được gộp vào. Đổi `SECRET_KEY` của app thì mã băm đổi.

Trả lời 4 câu hỏi của phiếu:

1. **Đã xác nhận:** đơn ở `DA_CHOT`, `GIAO_MOT_PHAN`, `DA_GIAO` (`confirmed: true`). Nhưng app **ghi doanh thu lúc giao**:
   `net_revenue` = tiền hàng các lần giao còn hiệu lực − hàng khách trả (`DA_NHAN`). Đơn chốt chưa giao: `net_revenue = 0`.
   Báo giá luôn `net_revenue = 0`. Trạng thái báo giá: `NHAP`, `DA_GUI`, `CHAP_NHAN`, `TU_CHOI`, `DA_HUY`.
2. **Mọi số tiền `net_*` là sau giảm giá, trước VAT.** Giảm giá toàn đơn đã chia vào từng dòng; đơn nhập giá gồm VAT đã tách VAT.
3. **Trả hàng:** `returns[]` (`number`, `status` DA_NHAN/DA_HUY, `date`, `net_amount`, `voided_at`, `lines[]`). Chỉ trả `DA_NHAN` mới trừ doanh thu.
   **Hoàn tiền:** `refunds[]` chỉ gồm lần đã chi (`amount_incl_vat`, gồm VAT); đây là tiền trả lại khách, không đổi doanh thu.
4. **Đổi/hủy:** `version` tăng mỗi lần sửa, chốt, hủy, mở lại. `updated_at` = muộn nhất trong giờ sửa đơn, giờ giao/hủy lần giao,
   giờ trả/hủy trả hàng, giờ chi hoàn tiền. Đọc tăng dần bằng `changed_since` = `updated_at` lớn nhất đã thấy **trừ lùi 5 phút**
   (giao dịch dài có thể ghi giờ sớm hơn lúc lưu xong), bỏ trùng theo (`kind`, `id`, `version`, `updated_at`).

## Phần D: POST /marketing/v1/contribution (lãi đóng góp)

TUẤN 17/11/2026. **Khóa riêng** vì có giá vốn: Quản trị › Trợ lý AI, dòng **Growth OS · Lãi đóng góp (có giá vốn)**
(agent `growth-os-lai`, phạm vi `marketing_cost`). Chỉ giám đốc tạo được. Khóa đọc thường gọi đường này bị `403`;
khóa này cũng không gọi được các đường đọc khác. Cùng luật chỉ dùng trong Z2, tạm dừng, nhật ký `ai.marketing_read`.

Body: `{"order_ids": [12, 15], "marketing_cost": 500000}`. `order_ids` 1–500 id đơn bán (`kind: DON` ở Phần C).
`marketing_cost` (đồng, ≥ 0, mặc định 0) là chi phí marketing Growth OS đã phân bổ cho **cả nhóm đơn này**.

```json
{"as_of": "…", "calc_version": "td-2026-11-17.1",
 "items": [{"order_id": 12, "number": "DB-26-00012", "status": "DA_GIAO", "delivered_net": 450000, "returns": 150000,
            "net_revenue": 300000, "cogs_at_sale": 200000, "fees": 30000, "contribution_before_marketing": 70000,
            "missing": ["PHI_SAN", "PHI_THANH_TOAN"]}],
 "totals": {"net_revenue": 300000, "returns": 150000, "cogs_at_sale": 200000, "fees": 30000, "marketing_cost": 50000, "contribution": 20000},
 "not_found": [999], "missing": ["PHI_SAN", "PHI_THANH_TOAN"]}
```

- Mọi số chưa VAT. `net_revenue` giống Phần C (giao còn hiệu lực − trả `DA_NHAN`).
- `cogs_at_sale` = giá vốn **lúc xuất kho** (sổ kho `stock_ledger` của phiếu xuất gắn lần giao), trừ giá vốn hàng khách trả. Không dùng giá bình quân hiện tại.
- `fees` = chi phí phát sinh nội bộ đã duyệt của đơn (gửi hàng, gửi xe, đối tác…), tính vào lần giao như Báo cáo lãi gộp.
- Số liệu lấy chung nguồn với Báo cáo lãi gộp nên khớp với báo cáo trong app.
- `contribution_before_marketing` = `net_revenue − cogs_at_sale − fees`. `totals.contribution` trừ thêm `marketing_cost`.
- `missing`: `PHI_SAN`, `PHI_THANH_TOAN` (app chưa ghi phí sàn, phí cổng thanh toán); `GIA_VON` (có dòng hàng giao mà không có phiếu xuất);
  `CHUA_GIAO` (đơn chưa giao lần nào, doanh thu 0).
- Không trả giá vốn từng mã, không trả khách. Báo giá, đơn của khách chạy thử, id không có → `not_found`.
- Phiếu dịch vụ/sửa chữa chưa tính ở bản này (không có trong `order_ids`).

## GET /marketing/v1/ink-rhythm/summary (đếm khách đến hạn lấy mực)

TUẤN 17/11/2026, phiếu "Cơ hội bán hàng". Cùng khóa `growth-os` (`marketing_read`), cùng nhật ký. Không tham số.

```json
{"as_of": "2026-11-23T06:00:00+07:00", "goi": 18, "sap": 7, "hen": 3, "xong_tuan": 5, "list_path": "/khach-hang/nhac-muc"}
```

- Số lấy nguyên từ màn **Nhắc mực** (`ink_rhythm.collect`, theo ngày giờ VN lúc gọi), nên khớp các ô đầu màn đó.
- `goi` = đã quá nhịp, chờ gọi; `sap` = sắp tới nhịp trong 7 ngày; `hen` = đang hẹn / tạm ẩn sau cuộc gọi;
  `xong_tuan` = số cuộc gọi đã ghi từ thứ Hai tuần này. Khách đã chuyển nơi khác không tính.
- Chỉ số đếm: không mã khách, tên, SĐT, số đơn, tên hàng. `list_path` là đường trong app quản lý (cần đăng nhập).

## Gửi ảnh sản phẩm sang Đăng web (khóa `growth-os-dang`, TUẤN chọn phương án A 25/11/2026)

Khóa riêng "Growth OS · Gửi ảnh sản phẩm lên Đăng web" (phạm vi `marketing_post`), tạo ở Quản trị › Trợ lý AI, chỉ dùng
trong máy chủ Z2. App Dịch vụ SEO dùng khóa này khi anh chụp sản phẩm ở cổng tải và tick "Tạo trang sản phẩm trên web".

- `POST /marketing/v1/dang-web/san-pham` (multipart: `ref` mã phiếu bên DVSEO, `ma` mã TD, `ghi_chu`, `anh` × tối đa 6):
  tạo việc Đăng web loại sản phẩm, `tu_dang=true`. Gửi lại cùng `ref` trả việc cũ, không tạo trùng. Mã phải đang dùng.
- `GET /marketing/v1/dang-web/viec/{id}`: trạng thái (`trang_thai`, `nhan`), `tu_dang_luc`, `ly_do_khong_tu_dang`,
  `so_trung`, `link_xem`, `trang_thai_web`, `da_go`, `dung_tu_dang`. Chỉ đọc được việc khóa này gửi.
- `POST /marketing/v1/dang-web/viec/{id}/da-bao`: DVSEO đã nhắn Zalo cho anh thì gọi; máy hẹn tự đăng sau 4 giờ.
  Từ chối (409) khi việc chưa ở Xem trước, có lý do chặn, đã hoãn, hoặc tự đăng đang dừng.

Máy chỉ tự đăng khi đủ: soạn xong không giá, không số tháng bảo hành, AI không còn điểm chưa chắc, có ảnh, không trùng,
đã báo Zalo đủ 4 giờ, anh không bấm Hoãn, chưa quá 3 sản phẩm hôm đó, tự đăng không bị dừng (lỗi 2 lần liên tiếp tự dừng).

# LG2 — Kiểm quyền đọc API

Bản rà soát trên `main` aec08c9 (đã có LG1, PR #94). Không migration.
Đã kiểm toàn bộ **46 đường GET** trong `app/routers/*.py`, gồm hai alias quản trị
và đường tải tệp có cả Bearer/cookie. 19 đường GET được bổ sung cổng quyền đọc.

Giám đốc có mọi vai trò nghiệp vụ trong bảng, trừ quyền admin (giữ nguyên
`security.has_role`). Các kiểm service đã có được giữ nguyên; cổng đọc API mới
không được gắn vào POST/PUT/PATCH. Không đổi phép tính tiền, tồn, công nợ hoặc ghi sổ.

## Quyền kế toán đã được chốt

Theo xác nhận của người dùng ngày 22/10/2026, kế toán được đọc phiếu kho trên
cả web và API; quyền ghi giữ nguyên. Thêm `finance` vào đúng mục `/phieu-kho`
trong `web.nav`; API lấy quyền từ khai báo đó ở thời điểm request. Không chép
lại danh sách vai trò vào router. Các guard ghi của service không thay đổi.
Kế toán không thấy nút lưu/ghi sổ/hủy và POST/PATCH vẫn 403.

## Bảng đường đọc

| GET route | Vai trò được đọc (cộng giám đốc như trên) | Căn cứ / kiểm tra |
|---|---|---|
| `/attachments` | Theo thực thể; xem bảng tệp | Mới: `api_read.check_attachment_read`; giữ `attachments.list_for → _check` |
| `/attachments/{attachment_id}/file` | Theo thực thể; xem bảng tệp | Mới: `api_read.check_attachment_read`; giữ `attachments._get/open_file → _check`; Bearer hoặc cookie |
| `/auth/me` | Mọi tài khoản đang hoạt động, chỉ chính mình | Đã kiểm tại `security.current_user`; response `UserOut`, không trả mật khẩu |
| `/auth/users` | Admin | Đã kiểm tại `security.require("admin")`; giám đốc không tự có quyền admin |
| `/users` | Admin | Đã kiểm tại `security.require("admin")`; giám đốc không tự có quyền admin |
| `/customers` | Điều phối, lễ tân, kinh doanh, kế toán | Mới: `require_web_read("/khach-hang")`; service không tự kiểm vai trò đọc |
| `/customers/{code}` | Điều phối, lễ tân, kinh doanh, kế toán | Mới: `require_web_read("/khach-hang")` |
| `/customers/{code}/history` | Kế toán | Đã kiểm tại `security.require("finance")` |
| `/service-tickets/{ticket_id}/custody` | Kho, điều phối; thợ được giao hoặc liên quan giữ máy | Đã kiểm tại `equipment_custody.load → can_view`; giữ kiểm quyền từng máy |
| `/part-requests` | Kho | Đã kiểm tại `part_requests.search → check_supplier` |
| `/service-tickets/{ticket_id}/part-requests` | Điều phối, lễ tân; thợ của phiếu | Mới: `require_web_read("/dich-vu")`; giữ `part_requests.for_ticket → service_tickets.load` |
| `/products` | Kinh doanh, kho, kế toán | Mới: `require_web_read("/hang-hoa")`; `ProductOut` chỉ có giá bán, không có giá vốn |
| `/products/{code}` | Kinh doanh, kho, kế toán | Mới: `require_web_read("/hang-hoa")` |
| `/purchase-orders` | Kho, kế toán | Đã kiểm tại `purchase_orders.search → check_reader` |
| `/purchase-orders/suggest-cost/{product_code}` | Kho, kế toán | Đã kiểm tại `purchase_orders.suggest_cost → check_reader`; có unit_cost |
| `/purchase-orders/{order_id}` | Kho, kế toán | Đã kiểm tại `purchase_orders.load → check_reader` |
| `/purchase-orders/{order_id}/receipts` | Kho, kế toán | Đã kiểm tại `purchase_receipts.list_by_order → purchase_orders.check_reader` |
| `/purchase-orders/{order_id}/returns` | Kho, kế toán | Đã kiểm tại `purchase_returns.list_by_order → purchase_orders.check_reader` |
| `/quotes` | Kinh doanh, kế toán | Đã kiểm tại `quotes.search → check_reader` |
| `/quotes/{quote_id}` | Kinh doanh, kế toán | Đã kiểm tại `quotes.load → check_reader` |
| `/recovered-parts` | Kho, kế toán, điều phối; thợ giữ/được giao phiếu | Đã kiểm tại `service_recovered_parts.search → visible`; thợ được lọc bằng SQL |
| `/sales-orders` | Kinh doanh, kho, kế toán | Đã kiểm tại `sales_orders.search → check_reader` |
| `/sales-orders/{order_id}` | Kinh doanh, kho, kế toán | Đã kiểm tại `sales_orders.load → check_reader` |
| `/sales-orders/{order_id}/deliveries` | Kinh doanh, kho, kế toán | Đã kiểm tại router gọi `sales_orders.load` trước `deliveries` |
| `/sales-orders/{order_id}/technician-delivery` | Điều phối, kinh doanh, kho, kế toán; thợ được giao đơn | Đã kiểm tại `technician_deliveries.load/check_assignee`; payload riêng không có giá vốn |
| `/sales-orders/receivables` | Kinh doanh, kế toán | Đã kiểm tại `sales_receipts.receivables` |
| `/sales-orders/{order_id}/receipts` | Kinh doanh, kho, kế toán | Đã kiểm tại `sales_receipts.for_order → sales_orders.load` |
| `/sales-receipts` | Kế toán | Đã kiểm tại `sales_receipts.list_pending → check_finance` |
| `/service-tickets/{ticket_id}/receipts` | Điều phối, lễ tân; thợ của phiếu | Mới: `require_web_read("/dich-vu")`; giữ `sales_receipts.for_ticket → service_tickets.load` |
| `/sales-orders/{order_id}/returns` | Kho, kế toán | Mới: `require_cost_read` theo luật 2; giữ `sales_returns.for_order → sales_orders.load`; snapshot có unit_cost/cost_amount |
| `/sales-returns/{return_id}` | Kho, kế toán | Mới: `require_cost_read`; giữ `sales_returns.load → sales_orders.check_reader`; web kinh doanh vẫn xem được phiếu |
| `/sales-orders/{order_id}/refunds` | Kinh doanh, kho, kế toán | Đã kiểm tại `sales_refunds.for_order → sales_orders.load` |
| `/sales-refunds` | Kế toán | Đã kiểm tại `sales_refunds.pending → sales_receipts.check_finance` |
| `/service-tickets` | Điều phối, lễ tân; thợ chỉ phiếu của mình | Mới: `require_web_read("/dich-vu")`; giữ lọc `_own_only` tại `service_tickets.search` |
| `/service-tickets/receivables` | Điều phối, kế toán | Đã kiểm tại `service_charges.receivables` qua VIEWERS |
| `/service-tickets/{ticket_id}` | Điều phối, lễ tân; thợ của phiếu | Mới: `require_web_read("/dich-vu")`; giữ `service_tickets._get` trả 404 cho thợ khác |
| `/service-tickets/{ticket_id}/visits` | Điều phối, lễ tân; thợ của phiếu | Mới: `require_web_read("/dich-vu")`; router gọi `service_tickets.load` trước list lượt |
| `/service-tickets/{ticket_id}/recovered-parts` | Kho, kế toán, điều phối; thợ giữ/được giao phiếu | Đã kiểm tại `service_recovered_parts.search → visible`; kiểm nguồn riêng, không mở toàn bộ DV |
| `/stock-docs` | Kho, kế toán | Mới: `require_web_read("/phieu-kho")`, đọc trực tiếp `web.nav`, không chép danh sách vai trò |
| `/stock-docs/{doc_id}` | Kho, kế toán | Mới: `require_web_read("/phieu-kho")`; chia sẻ cùng khai báo web đã chốt |
| `/supplier-debts` | Kế toán | Đã kiểm tại `supplier_debts.search/totals → check_reader` |
| `/supplier-debts/{code}` | Kế toán | Đã kiểm tại `supplier_debts.balance → check_reader` trước chi tiết lịch sử |
| `/suppliers` | Kho, kế toán | Mới: `require_web_read("/nha-cung-cap")`; service danh mục không tự kiểm đọc |
| `/suppliers/{code}` | Kho, kế toán | Mới: `require_web_read("/nha-cung-cap")` |
| `/warehouses` | Kho | Mới: `require_web_read("/kho")` |
| `/warehouses/{code}` | Kho | Mới: `require_web_read("/kho")` |

## Tệp đính kèm

Cổng API mới kiểm thêm vai trò cho `stock_doc` và `service_ticket`. Kiểm nguồn
và thợ được giao/giữ máy tại `attachments.TARGETS` vẫn chạy. Tệp ẩn và thợ khác
phiếu giữ 404 như trước. Chỉ đường tải tệp nhận cookie; danh sách vẫn dùng Bearer.

| Thực thể | Kiểm nguồn và phạm vi được giữ |
|---|---|
| stock_doc | API dùng cùng quyền `/phieu-kho`; chỉ kho thêm tệp như cũ |
| service_ticket | API dùng cùng quyền `/dich-vu`; `service_tickets._get` lọc thợ được giao |
| equipment_custody | `equipment_custody.load → can_view`: kho/điều phối, thợ được giao hoặc liên quan giữ máy |
| sales_delivery | `technician_deliveries.load`: các vai trò nghiệp vụ hoặc thợ được giao đơn |
| technician_return_line | `technician_returns.load`: kho/điều phối hoặc thợ của yêu cầu; kiểm dòng có thật |
| pending_item | `pending_items.load → reader`: kho, kế toán, điều phối |
| pending_disposition | Kiểm hồ sơ gốc bằng `pending_items.load`; không cấp quyền theo id tệp đơn thuần |

## Giá vốn và phạm vi

`ProductOut` không có giá vốn. Nhận/trả mua và gợi ý giá mua đã kiểm kho/kế toán
ở service; nợ NCC đã kiểm kế toán. Phát hiện thêm snapshot khách trả chứa
`unit_cost` và `cost_amount`: hai GET này thêm `require_cost_read` cho kho/kế toán/
giám đốc theo luật 2. Không thêm schema ẩn giá cho thợ/kinh doanh trong LG2;
quyền web kinh doanh xem phiếu trả hàng vẫn giữ.

Phạm vi là **đường GET**. POST tạo/hủy trả hàng hiện cũng trả snapshot có vốn;
LG2 không sửa response hoặc quyền ghi của các đường đó theo luật 5. Cần thẻ riêng
nếu muốn giới hạn toàn bộ response nghiệp vụ ghi có giá vốn.

## Test cũ điều chỉnh

- `test_inventory_catalogs.test_other_roles_read_only`: kỳ vọng GET toàn bộ vai trò
  200 đổi thành 200 chỉ khi kế toán đọc NCC, các vai trò không có quyền web nhận
  403. Giữ nguyên mọi khẳng định POST/PATCH 403 và test dữ liệu/audit.
- `test_stock_docs.test_roles_read_only`: GET thợ/kinh doanh/lễ tân/điều phối/admin
  200 đổi thành 403 theo LG2; kế toán giữ 200 theo xác nhận. Giữ nguyên các
  khẳng định POST/PATCH/cancel 403.
- `test_nav.test_exact_menu_home_and_login_for_each_role[finance]`: thêm `/phieu-kho`
  vào menu kế toán theo xác nhận; các kỳ vọng quyền khác giữ nguyên.

`tests/test_lg2_api_read.py` kiểm từng đường mới có cả vai trò được phép và bị chặn,
Bearer/cookie tải tệp, thợ khác phiếu, giá vốn khách trả, chính sách dùng chung
ở thời điểm request, và kế toán vẫn bị chặn ghi kho. Không sửa kiểm tiền/tồn/
công nợ/giữ hàng/rollback.

## Kiểm chứng

Trên bản cuối, sau khi đồng bộ `main` aec08c9 và chốt quyền kế toán:

- `pytest -q --tb=short tests/test_lg2_api_read.py tests/test_stock_docs.py tests/test_nav.py`:
  **128 passed**, không bỏ qua case nào; gồm 37 case LG2 mới.
- Kế toán đọc 200 trên web/API và tải tệp phiếu kho qua Bearer/cookie; cả 5 đường
  ghi API và 5 đường ghi web đều 403, dữ liệu kho/sổ/giữ hàng/audit không đổi.
- So sánh AST với `main`: không đổi hàm xử lý POST/PUT/PATCH/DELETE trong router.
  Bảng rà soát đủ 46 đường GET; `git diff --check` sạch.

Trước khi cập nhật `main` và chốt quyền kế toán, 272 test API/nghiệp vụ (tạm loại
riêng case kế toán) và 286 test web/menu/giữ máy/giao thợ/báo thu/vật tư đều đạt.
Case kế toán đó đã chạy lại và đạt trong bộ 128 ở trên. Chưa chạy toàn bộ suite
cục bộ; CI tiếp tục kiểm tra đầy đủ.

Không đổi schema/model/migration; không đổi hàm xử lý endpoint ghi.

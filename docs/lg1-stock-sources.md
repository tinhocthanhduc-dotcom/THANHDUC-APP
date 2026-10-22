# LG1 — nguồn phiếu kho và chặn hủy trực tiếp

Migration `0047`, từ head `0046`. `stock_docs.source_kind` là text nullable; chỉ caller
nghiệp vụ truyền vào `insert`/`create`. Form/API tạo và sửa phiếu tay không nhận trường
này. `StockDocOut` và snapshot audit có trường nguồn để đọc và truy vết.

| Mã | Nơi sinh |
|---|---|
| PART_REQUEST | CK cấp linh kiện theo phiếu hoặc xin mang theo |
| SERVICE_TICKET | PX vật tư khi hoàn thành DV |
| TECHNICIAN_DELIVERY | CK bàn giao/trả kho của đơn giao thợ |
| TECHNICIAN_RETURN | CK tốt/hư khi kho nhận TK |
| RECOVERED_PART | PN linh kiện thu hồi vốn 0 |
| PENDING_ITEM | CK chuyển vào CXL qua luồng riêng |
| PENDING_DISPOSITION | CK/PX xử lý CXL, gồm thanh lý |
| PURCHASE_RECEIPT | PN nhận mua |
| PURCHASE_RETURN | PX trả mua |
| SALES_DELIVERY | PX giao bán từ kho thường/kho thợ |
| SALES_RETURN | PN khách trả |
| STOCK_OPENING | KK nháp từ file tồn đầu kỳ |

## Đường hủy

`stock_posting.void`: khóa thu hồi nếu có → khóa phiếu kho → kiểm mã nguồn → các kiểm
nguồn cũ → `void_in_tx`. Phiếu tự sinh báo `SOURCE_LOCKED` 409, kèm mô tả và URL nguồn.
Các kiểm cũ vẫn bảo vệ phiếu có quan hệ nguồn dù `source_kind` chưa có/để null.

Ngoại lệ `PUBLIC_VOID` (`STOCK_OPENING`, `PENDING_ITEM`): vẫn hủy được ở phiếu kho và
vẫn hiện nút Hủy. KK tồn đầu kỳ chưa có đường sửa sai nào khác; CK chuyển tay vào CXL
được `cancel_receipt_in_tx` đóng hồ sơ trong cùng transaction (đã tách/xử lý thì
`CXL_DA_XU_LY`), nên không có nguồn bị lệch.

PN thu hồi giữ ngoại lệ CXL1 khi còn liên kết thu hồi thật: `lock_for_stock_void`,
`cancel_receipt_in_tx`, đảo sổ và mở lại người giữ cùng transaction. Đã tách/xử lý
vẫn `CXL_DA_XU_LY`. Không thêm kiểm mã nguồn vào `void_in_tx`; mở lại DV và hủy qua
các nguồn cũ tiếp tục dùng đường nội bộ.

Màn phiếu có nguồn hiện “Sinh từ …” và link, ẩn form Hủy theo LG1. Đường POST
web/API vẫn kiểm tại lõi; ẩn form không thay thế quyền hoặc validation. Ngoại lệ
thu hồi vẫn xử lý được qua đường POST sẵn có, không thêm nút mới.

## Backfill cũ

- Ưu tiên FK: YC, dòng DV, hai CK của TK, thu hồi, XL, nhận/trả mua, GH/dòng đơn
  cũ, khách trả. Ưu tiên XL/thu hồi trước hồ sơ CXL đi kèm để không gán nhầm loại.
- PN thu hồi đã hủy có FK bị xóa: đọc `stock_doc_id` chính xác từ audit
  `recovered_part.receive`, không sửa audit.
- Không có bảng `stock_moves` hoặc cột `for_order_id` được lưu trong schema cũ.
  CK giao thợ dùng dòng giữ đã chốt `DA_NHA`, mã kho/mã hàng và tham chiếu số CK
  duy nhất trong `stock_reservations.reason`: `Chuyển cho thợ CK-…` hoặc
  `Thợ trả kho CK-…`. Đây là tham chiếu do lõi tạo, không dò `stock_docs.reason`
  hay đoán bằng khoảng thời gian. Test thay nội dung lý do trên CK vẫn backfill đúng.
- CK từ màn CXL riêng dùng audit `pending_item.create` có `stock_doc_id`, nguồn
  CHUYEN_TAY và nhóm ban đầu do luồng riêng đặt. Phiếu tay vào LOI cũng có hồ sơ
  CXL nên không được gán nguồn chỉ vì thấy FK `pending_items.stock_doc_id`.
  Test đổi nhóm hồ sơ sau nhập tay vẫn giữ phiếu tay null.
- **Fallback bằng dấu hiệu văn bản:** tồn đầu kỳ không có bảng nguồn, dùng marker
  cũ `kind=KK`, `reason='Kiểm kê đầu kỳ'` và ghi chú `Nhập từ file`/`Nhập từ file,
  phần …` trên phiếu hoặc snapshot audit tạo phiếu bất biến (nếu diễn giải đã sửa).
  Đây cũng là marker K5 dùng để chặn nhập trùng.
- Không đoán nguồn từ lý do tự do của phiếu. Phiếu lịch sử đã mất hết FK/tham
  chiếu/audit nhận dạng (ví dụ PX DV đã hủy và dòng đã được mở lại) không được
  gán nguồn suy đoán; phiếu đã hủy vẫn không thể ghi/hủy lại theo kiểm trạng thái.

Migration chỉ đổi nguồn, không đổi version, tồn, giữ hàng, số tiền, trạng thái
nguồn, sổ hay audit. Downgrade bỏ cột; upgrade lại suy nguồn từ bằng chứng cũ.

## Những đường sửa sai chưa có — để thẻ sau

- Chưa có thao tác đảo cấp cho YC đã DA_CAP.
- Chưa có hủy lần nhận TK đã DA_NHAN (gồm hai CK tốt/hư).
- CK bàn giao thợ có nghiệp vụ chuyển trả hiện có; chưa có nút đảo nguyên lần
  bàn giao cũ. Trả hàng là CK mới, không sửa lịch sử cũ.
- Chuyển vào CXL và KK đầu kỳ không được thêm nút hủy nguồn trong LG1.
- DV mở lại, nhận/trả mua, GH/khách trả, XL CXL và PN thu hồi đã có các đường xử lý
  được giữ. Thanh lý dẫn về GH để sửa, không dẫn tới nút hủy XL trực tiếp.

## Điều chỉnh test cũ

`test_stock_opening.test_kho_da_co_phat_sinh_thi_khong_nhap_dau_ky`: dựng một KK
**lập tay** rồi ghi/hủy để kiểm bất biến kho đã có lịch sử vẫn không nhập đầu kỳ.
Giữ nguyên khẳng định `WAREHOUSE_NOT_EMPTY`; không đổi kiểm tiền/tồn. KK đầu kỳ
tự sinh nay bị khóa theo LG1 và có test riêng.

Ba test `test_cxl1_pending_items.test_split_preserves_source_date_cost_and_prevents_void`,
`test_void_untouched_receipt_closes_pending_cancelled`,
`test_partial_inventory_prevents_receipt_void`: dùng CK lập tay thật thay cho helper
`transfer_in` (nay là nguồn tự sinh bị khóa). Giữ nguyên toàn bộ khẳng định tồn,
trạng thái, audit, số dòng và mã `CXL_DA_XU_LY` cho phiếu tay có hồ sơ đã xử lý.

`test_sales_deliveries.test_delivery_and_other_px_use_counter_before_stock_lock`:
wrapper theo dõi `insert` nhận/truyền tiếp keyword `source_kind`; giữ nguyên test
đồng thời và thứ tự khóa counter/tồn.

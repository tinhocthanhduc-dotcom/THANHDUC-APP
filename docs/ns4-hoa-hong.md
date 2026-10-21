# NS4 — Hoa hồng tháng

`app/commissions.py` chỉ đọc chứng từ nghiệp vụ, ghi quy tắc/điều chỉnh/bảng khóa riêng. Không gọi ghi sổ kho/quỹ, không tạo phiếu chi hoặc thưởng KPI. NS5 lấy tổng đã khóa qua `month_total(session, user_id, month)`; tháng chưa khóa trả 0.

## Tính lũy kế

Mỗi nguồn tính một lần bằng SQL cho toàn bộ người và nguồn, không truy vấn trong vòng lặp người/đơn:

```
entitled = round((fixed + pct / 100 × base) × min(1, max(0, paid) / receivable))
amount tháng này = entitled − tổng amount của nguồn ở các tháng đã khóa trước
```

Tỷ lệ là 1 nếu miễn phí hoặc phải thu bằng 0. Làm tròn đồng theo `round(numeric)` của PostgreSQL (0,5 thành 1); không dùng float để tính tiền. Tỷ lệ lưu 6 số lẻ, còn lũy kế làm tròn từ tỷ lệ chính xác trước khi lưu.

- Kinh doanh: nguồn là đơn, người tạo đơn; base là lãi gộp ròng các GH/TH còn hiệu lực tới cuối kỳ. Lãi âm hưởng 0. Đơn có lịch sử XL `THANH_LY` liên kết GH trước mốc kỳ bị loại cả đơn.
- Thợ: nguồn là phiếu, người bấm bước Hoàn thành đầu tiên. Dùng mốc hoàn thành đó để giữ một người hưởng/một nguồn và bắt đầu 90 ngày, không dùng người đang được giao sau khi mở lại. Quy tắc lấy theo ngày hoàn thành; base là tổng PHI chưa VAT. Miễn phí chỉ lấy cố định, gồm bảo hành `PHIEU_TRUOC` (không thay đổi số tiền hay cơ chế duyệt ngoại lệ trên phiếu).
- Bảo hành `PHIEU_TRUOC` tạo từ lúc hoàn thành tới hết 90 ngày, trước cuối kỳ, chưa hủy: nguồn gốc hưởng 0. Nếu đã khóa trước đó sẽ ra dòng âm; hủy bảo hành sau khi đã khóa dòng âm thì kỳ sau hoàn lại phần đó.
- Nguồn tạo trước `TD_COMMISSION_START` bị loại, mặc định `2027-01-01`. Docker Compose có truyền cấu hình này; không đổi ngược ngày trên hệ thống đã chạy khóa tháng.
- Quy tắc còn hiệu lực (chưa bị thay), ngày hiệu lực ≤ ngày nguồn. Ưu tiên người+loại → người → nhóm+loại → nhóm, rồi ngày hiệu lực mới nhất trong cùng phạm vi.
- Không có quy tắc: draft trả cảnh báo số 0; không lưu dòng 0 lúc khóa. Cảnh báo được hiện ở bảng tháng.
- Điều chỉnh là nguồn độc lập, không trừ vào lũy kế một đơn/phiếu khác. Thêm sai thì thêm dòng đối dấu, không sửa/xóa.

## Nguồn tài chính dùng chung

`sales_margin_sources` trong `app/kpi.py` tách từ projection NS3, dùng nguyên `_margin_sources` của Báo cáo lãi gộp để khử trùng liên kết phiếu kho. NS3 tiếp tục dùng cùng hàm; nhánh `financial=False` không đọc giá vốn.

`sales_receipts.totals(..., as_of=...)` dùng lại tổng BT xác nhận trừ HT đã chi, thêm giới hạn thời điểm xử lý/ngày thu khi gọi cho hoa hồng. Những chỗ gọi cũ không truyền `as_of` giữ nguyên công thức và truy vấn như trước. BT xác nhận trễ vào tháng sau, không cộng ngược vào kỳ đã khóa. Phải thu gồm VAT lấy GH còn hiệu lực trừ TH cùng nguồn đến mốc kỳ; dịch vụ lấy tổng phiếu.

Mốc tháng dùng `Asia/Ho_Chi_Minh`, tháng hiện tại dừng tại lúc xem. Phiếu đã hủy, trả hàng hoặc hoàn tiền làm thay đổi lũy kế kỳ chưa khóa; các tháng đã khóa luôn đọc snapshot.

## Khóa và bảo vệ

- Chỉ giám đốc thêm/thay quy tắc, thêm điều chỉnh hoặc khóa. Chỉ khóa tháng đã qua và liên tiếp từ tháng bắt đầu; không có mở khóa.
- Các ghi hoa hồng dùng advisory lock độc quyền chung. Draft dùng khóa đọc chia sẻ để header, số tính lại và điều chỉnh không trộn với một thao tác khóa/thay quy tắc đồng thời.
- Khóa tính lại draft, lưu dòng tự động khác 0, cộng điều chỉnh đã có, tạo header và audit trong một transaction. Lỗi bất kỳ bước nào rollback các dòng mới và header; điều chỉnh đã thêm trước đó vẫn giữ.
- Trigger DB chặn sửa/xóa dòng và tháng; chặn thêm dòng vào tháng đã khóa; kiểm tổng header bằng tổng các dòng. Quy tắc chỉ được cập nhật một lần `replaced_at` (như lịch sử lương NS1), còn mọi nội dung khác bất biến. Unique index bảo vệ phạm vi/ngày hiệu lực và một dòng tự động/nguồn/tháng.
- Audit: `commission.rule`, `commission.adjust`, `commission.lock`.

## Quyền và giao diện

- `/hoa-hong`: tự xem. Bỏ qua user_id trên URL. Dòng đơn bán chỉ có số đơn, tỷ lệ thu, tiền hoa hồng và ghi chú; không có base, pct, giá vốn hoặc lũy kế/previous trong context trả về.
- `/hoa-hong/thang` và CSV: giám đốc xem draft/đã khóa; kế toán chỉ tháng đã khóa (mặc định tháng khóa gần nhất), không có nút ghi. Chi tiết kinh doanh của kế toán cũng bỏ cơ sở/%.
- `/hoa-hong/quy-tac`: chỉ giám đốc, tìm người trong danh sách, chip loại phiếu, lịch sử và Thay bản này.
- Ô điều chỉnh dùng `data-money data-money-signed`; formatter chỉ giữ dấu âm khi có cờ này, các ô tiền cũ giữ nguyên. Server parse bằng parser tiền hiện có, tách dấu cho điều chỉnh.
- Thợ giữ 4 tab dưới, vào Hoa hồng từ menu tài khoản. Bảng cuộn ngang trong khung, cố định cột đầu ở 360px.

## Kiểm tra

`tests/test_ns4_commissions.py`: thu một phần/xác nhận trễ, trả và hoàn tiền khớp tổng lõi, cố định/PHI/miễn phí, bảo hành 89–90–91 ngày và hủy bảo hành, ưu tiên/thay quy tắc/rollback thay trùng, nguồn trước ngày bắt đầu, lỗ/thanh lý FIFO thực tế, khóa liên tiếp/lỗi giữa chừng/khóa đồng thời, điều chỉnh đối dấu, SQL bất biến, quyền GET/POST/CSV, ẩn giá vốn, tổng NS5 và CSV, 20 người không tăng truy vấn, ranh giới giờ Việt Nam/làm tròn đồng, không ghi quỹ/kho.

Database migration riêng: upgrade head → check → downgrade base → upgrade head → check. Chromium 360px: tìm người, nhập điều chỉnh âm, tạo/thay quy tắc, vuốt khóa, bảng/cột cố định, CSV, kế toán chỉ xem khóa, dữ liệu cá nhân thợ/kinh doanh và tháng kế tiếp; không tràn trang hoặc lỗi JavaScript.

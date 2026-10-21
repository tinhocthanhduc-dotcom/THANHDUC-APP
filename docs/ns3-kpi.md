# NS3 — KPI tự tính

`app.kpi` là lớp đọc; không gọi ghi sổ tiền/kho và không truy cập dữ liệu lương NS1. Chỉ thao tác mục tiêu ghi `kpi_targets` và audit `kpi.target`. Không khóa tháng hay tạo thưởng.

- `compute(session, month, user_ids=None)` trả bản đồ người → chỉ số, dùng một truy vấn nhân sự và một truy vấn SQL gộp (SUM / tỷ lệ / percentile_cont), không truy vấn theo từng nhân viên. `METRICS` là danh mục cố định 18 chỉ số, kèm chiều tốt, đơn vị, giải thích và hàm gộp.
- `my_kpi` loại lãi gộp ngay từ lựa chọn nguồn SQL; `detail` kiểm tra người xem, vai trò của người được xem và chặn lãi gộp với mọi người ngoài giám đốc. `board` chỉ giám đốc. Giám đốc không có KPI cá nhân.
- Tháng tính theo `Asia/Ho_Chi_Minh`; tháng hiện tại dừng tại thời điểm xem, không lấy tương lai. Tổng tiền giữ kiểu NUMERIC; chỉ trung vị dùng percentile_cont rồi đổi về NUMERIC trước khi hợp kiểu với các tổng tiền.
- Dữ liệu không có mẫu số/trung vị hiện “Chưa có dữ liệu”, không coi là đạt 100%. Chưa đặt mục tiêu không tính %. Mục tiêu 0: cao tốt có thực tế 0 → 100%, dương → 200%, âm → 0%; thấp tốt thực tế 0 → 100%, dương → 0%.
- Hoàn thành đếm riêng từng cặp phiếu/người bấm khác nhau trong tháng; các lần hoàn thành lặp lại của cùng người không đếm thêm. Tiền thu chỉ quy một lần về người hoàn thành gần nhất đến cuối kỳ (kể cả tiền thu trước lúc hoàn thành trong kỳ); bảo hành quy về người hoàn thành gần nhất trước lúc tạo bảo hành. Không nhân tiền theo số bước Hoàn thành.
- Nhận việc ghép mỗi bước giao với bước nhận kế tiếp của đúng người, trước một lần giao mới; bỏ lần giao không có nhận tương ứng. Báo hư dùng tình trạng/số lượng thực nhận của kho, có dự phòng cho dữ liệu cũ.
- Báo giá dùng audit `quote.send` đầu tiên làm thời điểm gửi. Doanh thu/lãi gộp dùng lại `_margin_sources` của Báo cáo lãi gộp, gồm giao từng phần và hàng trả theo tháng ghi nhận; chi tiết doanh thu của kinh doanh không trả giá vốn.
- Phiếu trễ hẹn/tồn đọng dựng trạng thái cuối kỳ từ service_steps; phiếu mở lại vẫn là đang mở. Tồn đọng loại toàn bộ trạng thái đóng của lõi, gồm `KHACH_HEN_SAU`.
- Chờ xử lý dựng trạng thái/qty từ audit bất biến trước mốc cuối kỳ, hoặc `before` đầu tiên sau mốc. Hồ sơ con tách sau mốc không xuất hiện ngược về tháng cũ dù giữ entered_at cũ. Hồ sơ chuyển đổi cũ không có audit dùng hàng nền; tuổi quá hạn dùng đúng entered_at và cấu hình pending_alert_days như totals.
- Lịch làm việc lấy work_days và attendance_holidays của NS2; chốt đúng hạn là trước (không gồm đúng) 10:00 hôm sau. Xác nhận/từ chối BT so ngày báo và ngày xử lý theo giờ Việt Nam; những chỉ số tiền chỉ lấy BT DA_XAC_NHAN.
- Mục tiêu riêng ưu tiên chung. Lưu theo lô trong một transaction, khóa advisory theo tháng bảo vệ cả ô chưa tồn tại, version chặn form cũ; chép tháng trước không ghi đè ô đã có. Để trống ô đã có giữ nguyên, không xóa mục tiêu.

## Kiểm tra

`tests/test_ns3_kpi.py`: dữ liệu chính xác cho 18 chỉ số, quyền/ẩn giá vốn, không ghi dữ liệu nghiệp vụ, mục tiêu/audit/version/chép, ranh giới giờ Việt Nam, giao hai tháng và trả tháng sau khớp Báo cáo lãi gộp, snapshot cuối tháng, truy vấn không tăng với 20 người, số tiền vượt giới hạn chính xác của double.

Kiểm trực tiếp migration trên database riêng: upgrade head → alembic check → downgrade base → upgrade head → alembic check. Kiểm Chromium 360px: thẻ thợ/kinh doanh, chứng từ, bảng giám đốc/cột cố định, CSV, ô tiền, mục tiêu thập phân, lưu và chép không ghi đè; không tràn trang, không lỗi JavaScript.

# NS5 — Bảng lương, tạm ứng và chi lương

NS5 lập bảng lương nháp cho các hồ sơ NS1 còn làm ít nhất một ngày trong tháng; giám đốc nhập tay thưởng/BHXH/thuế/khấu trừ, duyệt rồi kế toán chi qua lõi quỹ hiện có. Không tự tính thuế, BHXH, tăng ca, không chuyển tiền ngân hàng tự động và không mở lại bảng đã duyệt.

## Nguồn và công thức

- Dùng `employees.salary_on` tại ngày cuối tháng. Đổi lương giữa tháng không chia đôi.
- Dùng `attendance.month_summary`, giữ nguyên quy tắc ngày vào/nghỉ việc, phép, ngày lễ và thiếu giờ ra của NS2.
- Dùng `commissions.month_total` đã khóa. Trước tháng `TD_COMMISSION_START`, hoa hồng là 0, không yêu cầu khóa hoa hồng.
- Ba hàm trên giữ API đơn lẻ, thêm cách gọi với danh sách id để đọc theo lô. Màn 20 người không truy vấn lặp từng người.
- Công / công chuẩn không chặn trên. Lương công và `(ăn trưa + xăng) × tỷ lệ công` làm tròn half-up riêng; điện thoại/khác cộng nguyên. Thu nhập cộng hoa hồng/thưởng; thực nhận trừ tạm ứng/BHXH/thuế/khấu trừ, làm tròn đồng ở cuối. Không dùng float tính tiền.
- Tạm ứng lấy PC còn ghi sổ có **ngày kế toán `cash_docs.doc_date` trong tháng**, chưa gắn dòng lương. `paid_at` vẫn lưu thời điểm bấm chi thực tế, không thay bằng ngày ghi lùi. Cách này giữ ngày chi kế toán đã chọn đúng kỳ lương.
- Có cảnh báo chưa có lương, thực nhận âm, thiếu giờ ra. Hai trường hợp đầu chặn duyệt; thiếu giờ ra chỉ cảnh báo.

## Duyệt và bất biến

Giám đốc chỉ duyệt tháng đã qua, công đã khóa (`attendance.is_locked`) và hoa hồng đã khóa nếu đã áp dụng. Khóa tháng công được giữ trong transaction duyệt để không trộn với thao tác mở/bổ sung công. Khóa hồ sơ nhân viên để không trộn với thay bản lương NS1.

Trong một transaction: tính lại → chụp `payroll_lines` → khóa/gắn các tạm ứng đã trừ → tạo `payroll_months`/tổng → audit. Không chỉnh sửa số đã chụp khi các nguồn NS1–NS4 thay đổi sau đó. Không mở lại; sửa bằng khoản thưởng/khấu trừ kỳ sau.

Trigger bảo vệ:

- `payroll_inputs`: không sửa sau duyệt, không đổi khóa và không xóa.
- `payroll_months`: không sửa/xóa, kiểm tổng thực nhận khớp các dòng và tổng tạm ứng đã gắn.
- `payroll_lines`: không sửa/xóa thành phần lương; chỉ ghi chi một lần, PC phải khớp nguồn/số tiền và đã ghi sổ. Dòng 0 không có PC.
- `salary_advances`: không sửa nội dung/xóa; chỉ chuyển trạng thái hợp lệ, chi phải có PC khớp nguồn, chỉ gắn vào dòng lương đúng người một lần.

Thao tác NS5 dùng khóa advisory độc quyền; đọc nháp/đã duyệt dùng khóa chia sẻ để header và các dòng không bị trộn với lần duyệt/chi đồng thời. Dữ liệu nhập có version; cả lần lưu nhiều người rollback nếu một dòng cũ/sai.

## Chi và tạm ứng

- `pay` khóa các dòng đã duyệt, chưa chi. Mỗi người một PC nguồn `PAYROLL_LINE`, danh mục từ `finance_mappings.require('DANH_MUC_LUONG')`, quỹ/ngày do kế toán chọn. `cash_docs.insert` và `cash_posting.post_in_tx` chạy trong transaction ngoài; một phiếu lỗi rollback cả lần chọn, gồm PC, sổ quỹ và trạng thái đã chi. Net 0 chỉ đánh dấu đã chi.
- Tạm ứng có số TU, cần hồ sơ còn làm và bản lương hiện tại. Tổng CHO_DUYET + DA_DUYET + DA_CHI chưa trừ không vượt `TD_ADVANCE_MAX_PCT` (mặc định 50%) lương cơ bản. Khóa chung bảo vệ cả hai yêu cầu đồng thời.
- Người gửi chỉ hủy CHO_DUYET. Giám đốc duyệt/từ chối (từ chối cần ghi chú); kế toán chi PC nguồn `SALARY_ADVANCE` cùng danh mục Chi lương. Chi thất bại giữ DA_DUYET.
- Không cho chi tạm ứng ghi lùi vào tháng lương đã duyệt, hoặc tháng nhân viên không thuộc bảng lương, để không tạo khoản đã chi mà không có kỳ để trừ.
- Audit: `payroll.input`, `payroll.approve`, `payroll.pay`, `payroll.advance_request/cancel/decide/pay`.

### Phương án hủy phiếu chi

Áp dụng phương án dự phòng được thẻ cho phép: **chặn hủy/sửa riêng PC có `source_type='PAYROLL_LINE'`**; không tự đưa dòng về chưa chi. Chặn cả `SALARY_ADVANCE` vì tạm ứng có thể đã bị trừ ở snapshot. Kiểm ở màn tạo/sửa/hủy phiếu chi và `void_in_tx`; điều chỉnh quỹ cũng đã chặn nguồn tự động theo lõi hiện có. Sửa số lương bằng kỳ sau; chưa có luồng đảo trả tiền lương/tạm ứng riêng trong NS5. Không nới điều kiện hủy hay ghi đảo quỹ hiện có.

## Quyền và màn hình

- `/luong`, CSV nháp, lưu/duyệt: giám đốc.
- `/luong/chi`, CSV đã duyệt và chi: finance/giám đốc. Chỉ đọc snapshot, không lấy bản lương NS1 hoặc ô nhập nháp.
- `/phieu-luong`: các tháng đã duyệt của mình; `/phieu-luong/{id}` cho người khác trả 404, ngoại trừ finance/giám đốc xem bảng đã duyệt để chi. Có chi tiết tạm ứng đã trừ và nút In.
- `/tam-ung`: người thường chỉ thấy của mình; finance chỉ thêm yêu cầu đã duyệt/đã chi của người khác, không thấy pending/từ chối của người khác. Giám đốc thấy toàn bộ; số chờ duyệt vào Cần xử lý.
- Admin không có director nhận 403 với mọi route NS5, kể cả khi có thêm vai trò khác.
- Link Phiếu lương/Tạm ứng ở menu tài khoản chỉ hiện khi có hồ sơ, không có quyền admin bị chặn. Thợ giữ 4 tab dưới.
- UI0, ô tiền, quỹ bằng chip, duyệt bằng vuốt, bảng cuộn ngang và cột tên cố định 360px. Không xuất ngân hàng hoặc số tài khoản.

## Migration và kiểm thử

`0045` từ `0044` thêm bốn bảng, trigger, khóa ghép `DANH_MUC_LUONG` chiều CHI. Settings/Docker Compose có `TD_ADVANCE_MAX_PCT=50`.

`tests/test_ns5_payroll.py` kiểm công thức, ngày hiệu lực/ngày vào-nghỉ, cảnh báo/điều kiện duyệt, version/snapshot/rollback, tạm ứng một lần, hạn mức đồng thời, chi lỗi/chi ba người/quỹ giảm, zero net, hai lượt chi/duyệt đồng thời, quyền/404 riêng tư, CSV/tổng, mapping/menu, 20 người không tăng truy vấn, SQL bất biến và chặn nguồn PC giả/hủy riêng.

Migration kiểm trên database riêng: upgrade → check → downgrade base → upgrade → check. Chromium 360px kiểm cả luồng xin/duyệt/chi tạm ứng, nhập/lưu/vuốt duyệt lương, chi theo lô và PC, CSV, phiếu cá nhân/In/quyền/không tràn trang.

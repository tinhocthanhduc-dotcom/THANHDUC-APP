# BH1: serial máy bán và nhập bảo hành CARE

Giám đốc đặt **Bảo hành máy (tháng)** ở Hàng hóa, từ 0 đến 60. Mã có số tháng lớn hơn 0 yêu cầu một serial cho mỗi máy giao. Các màn kho giao, bán tại quầy và thợ giao đều dùng cùng kiểm tra. Có thể gõ từng máy hoặc dán danh sách cách nhau bằng dấu phẩy/xuống dòng. Hạn bảo hành tính theo tháng lịch từ ngày giao tại Việt Nam; hỗ trợ tận nơi thêm một tháng.

Serial đang thuộc khách khác hoặc khác mã hàng không dùng lại được. Khi hủy lần giao, máy mới bị tắt và bỏ khách; nếu serial đã có phiếu dịch vụ, phải dùng luồng trả hàng. Khi trả máy, chọn serial trong lần giao gốc. Lịch sử serial không bị xóa khi hủy giao hoặc trả hàng.

## Nhập từ CARE

1. Không cần tạo mã hàng chung. Model không khớp danh mục được lưu là thiết bị ngoài danh mục (`product_code` trống, `model_name` = tên máy trong CARE, migration 0062). Mã `MAY-CU-CARE` chỉ còn là ngoại lệ cũ trong ràng buộc mã hàng, script không dùng nữa.
2. Xuất CSV UTF-8 từ sheet **Máy đã bán** và **Sửa chữa**, với các cột:

```csv
serial,SĐT,tên khách,model,ngày hết hạn bảo hành,mã phiếu CARE
CARE-DEMO-001,0901234567,Khách mẫu,Model máy,31/12/2027,SC0001
```

Ngày chấp nhận `dd/mm/yyyy` hoặc `yyyy-mm-dd`. SĐT được chuẩn hóa theo quy tắc Danh bạ của app. Tên khách chỉ giúp đối chiếu; script chỉ ghép bằng SĐT đã có trong app. Model khớp chính xác tên hoặc mã hàng đang bán thì dùng mã đó, các trường hợp còn lại ghi tên máy tự do vào `model_name` (cột `model` trống → "Máy nhập từ CARE").

3. Chạy thử trên môi trường đã cấu hình kết nối app:

```bash
python scripts/import_care_warranty.py --csv may-da-ban.csv --csv sua-chua.csv > bao-cao-chay-thu.json
```

Không ghi dữ liệu nếu thiếu `--apply`. Báo cáo từng dòng ghi rõ tạo mới, cập nhật, đã có, hết hạn, thiếu serial, không khớp khách, trùng SĐT hoặc lỗi dữ liệu. Không tự tạo khách.

4. Kiểm tra báo cáo, sửa các dòng chưa ghép được rồi mới nhập:

```bash
python scripts/import_care_warranty.py --csv may-da-ban.csv --csv sua-chua.csv --apply > bao-cao-nhap.json
```

Chỉ nhập máy còn hạn tại ngày chạy (kể cả ngày hết hạn hôm nay). Chạy lại không tạo trùng serial; máy đã có chỉ điền hạn bảo hành nếu đang trống, không đè hạn đã có. Không suy đoán ngày hỗ trợ tận nơi cho dữ liệu CARE vì CSV không có ngày giao. Nhật ký ghi người giám đốc đang hoạt động đầu tiên; ghi chú máy có mã phiếu CARE.

## Dữ liệu thử và migration

`clear_pilot_data` giữ cấu hình hàng hóa/bảo hành và máy nhập CARE hoặc máy tạo tay; xóa thiết bị được sinh mới từ giao hàng cùng chứng từ thử. Không chạy script xóa dữ liệu này trên dữ liệu vận hành nếu chưa có kế hoạch riêng.

Migration `0060` nối `0059` của XG1. Downgrade bỏ các cột/bảng BH1. Nếu đã có mã `MAY-CU-CARE`, giữ ngoại lệ mã này trong constraint để không xóa cấu hình hoặc đổi mã trên chứng từ cũ; nếu chưa có thì khôi phục ràng buộc mã TD như trước.

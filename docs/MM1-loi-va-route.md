# MM1: hợp đồng lõi và route cho màn chọn nhiều máy

Migration `0061` nối `0060`: `service_tickets.split_from_id` nullable, FK tự tham chiếu, có index. Bảng `service_tickets` đã thuộc WIPE; không có bảng mới. API đọc phiếu trả thêm `split_from_id`.

## Chọn máy

Hai route giữ nguyên CSRF, `form_key`, `version`:

- `POST /dich-vu/{id}/may`
- `POST /tho/phieu/{id}/may`

Gửi lặp `machine_pick=SERIAL:<serial>` cho từng máy. Có thể thêm một `machine_pick=NEW`, kèm `new_serial`, `new_product_code` và `new_product_q` như SV1. Một giá trị vẫn gọi SV1 `set_machine`; từ hai giá trị gọi `set_machines`, máy mới được xếp sau các máy có sẵn. Không gửi `NONE` cùng danh sách nhiều máy.

Thành công trả 303 về phiếu đang thao tác, `?da_luu=1&tach=DV-xx,DV-yy`. `tach` chỉ gồm số phiếu tạo thêm, không gồm phiếu gốc. Giao diện mới dùng danh sách này để báo **Đã tách thêm N phiếu**. Khi lỗi, context `machine_form.machine_picks` giữ đầy đủ các giá trị đã gửi; các trường máy mới vẫn có trong `machine_form`.

Lõi `set_machines(session, user, ticket_id, version, serials, new_machine)` trả danh sách model phiếu, phiếu gốc trước. Quyền thao tác giữ SV1; quyền đặc biệt tạo/giao phiếu tách là tham số nội bộ, không nằm trong schema API tạo/giao phiếu. Phiếu tách không chép tiền, dòng vật tư hay báo thu. Lịch hẹn đã ghi được giữ nguyên kể cả đã qua; đây là sao chép lịch của việc đang làm, không mở một đường nhập giờ hẹn quá khứ cho API chuyển trạng thái thông thường. Lượt đang ở khách của phiếu tách bắt đầu đúng giờ lượt mở trên phiếu gốc.

Máy phải thuộc khách/địa điểm phù hợp. Máy chưa gắn địa điểm được chọn theo quy tắc SV1; máy đã có địa điểm khác bị từ chối. `SERIAL_DUPLICATE` báo máy trùng, `SERIAL_BUSY` 409 kèm `details.ticket_number` báo phiếu mở đang dùng máy. Chọn cùng máy từ hai yêu cầu nhiều máy được khóa theo serial trước khi kiểm phiếu bận.

## Nhận cả chuyến mang về xưởng

`POST /tho/phieu/{id}/mang-ve-xuong` (giữ alias cũ `/dich-vu/{id}/mang-ve-xuong`). Gửi `version` của phiếu gốc và các trường:

```text
trip-<ticket_id>-version
trip-<ticket_id>-machine_version
trip-<ticket_id>-accessories          # gửi lặp nếu nhiều phụ kiện
trip-<ticket_id>-accessories_note
trip-<ticket_id>-condition_note
```

Có thể dùng các trường không prefix như màn cũ cho máy gốc (`machine_version`, `accessories`, ...), các phiếu tách dùng prefix. `KHONG_CO` là giá trị form xác nhận không kèm phụ kiện; lõi nhận danh sách phụ kiện rỗng. Phiếu gốc bắt buộc có mặt. Gửi đủ phiếu tách còn ở khách, cùng thợ và đang làm; không trộn phiếu ngoài nhóm hoặc phiếu đã đổi bước/thợ.

Vật tư vẫn gửi lặp `material_product`, `material_qty`, chỉ ghi/xuất trên phiếu gốc. Mỗi phiếu máy phải có ảnh `NHAN_MAY` của chính phiếu đó; tải qua `POST /tho/phieu/{ticket_id}/anh` như hiện tại, không dùng chung ảnh phiếu gốc cho các máy khác.

Lõi `bring_trip(session, user, ident, version, items, materials)` nhận `items` dạng dict hoặc `WorkshopTripItem`, trả các phiếu gốc rồi phiếu tách theo id. Khóa toàn bộ phiếu trước máy; mỗi máy nhận qua lõi giữ máy hiện có, rồi chuyển `TAM_DUNG / CHO_SUA_XUONG` và có bàn giao `CHO_NHAN` về Kho kỹ thuật. Một lỗi bất kỳ rollback mọi máy, vật tư, lượt, lịch sử và audit trong chuyến. Context lỗi `workshop_form.trip_items` giữ dữ liệu theo máy cho giao diện mới.

Không gửi trường `trip-*` thì đường một máy vẫn hoạt động như trước. MM1 không thêm checkbox, khối nhận máy hay thay thẻ ảnh; phần giao diện do Claude thực hiện sau khi gộp.

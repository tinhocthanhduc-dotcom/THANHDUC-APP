# Tin nhắn Zalo Bot

Màn **Quản trị > Tin nhắn Zalo** (`/zalo/tin-nhan`), chỉ giám đốc. Lưu tin Zalo gửi về và tin trả lời từ màn này.

## Cài lần đầu

1. Thêm vào `deploy/.env` trên máy chủ (anh Tuấn tự sửa, không gửi cho ai):
   ```
   TD_ZALO_BOT_TOKEN=token-cua-bot
   TD_ZALO_WEBHOOK_SECRET=chuoi-ngau-nhien-8-den-256-ky-tu
   ```
   Tạo chuỗi ngẫu nhiên: `openssl rand -hex 32`.
2. Chạy lệnh cập nhật app chuẩn.
3. Mở màn Tin nhắn Zalo, mở "Cài đặt bot", bấm **Cài webhook**, đọc kết quả kiểm tra của Zalo.
4. Nhắn thử cho bot. Tin hiện trên màn và người nhắn nhận lời báo tự động lần đầu.

Cài webhook xong, lệnh `getUpdates` của bot này không dùng được nữa (workflow n8n "Lấy mã chat Zalo"). Mã chat xem trên màn.

## Giới hạn đợt 1

- Không lấy lại được tin trước lúc cài: Zalo không có lệnh xem lịch sử.
- Tin n8n gọi thẳng Zalo chưa hiện trên màn, vì webhook chỉ báo tin nhận được.
- Ảnh, sticker, tin thoại chỉ lưu dữ liệu Zalo gửi kèm (cột `raw`), chưa tải file về.
- Trong nhóm (Beta), tài liệu Zalo chỉ khẳng định bot nhận tin @nhắc tên và tin trả lời bot.
- Tài liệu Zalo không nói có gửi lại tin khi webhook lỗi, nên nên cập nhật app ngoài giờ làm.
- Mốc lưu 12 tháng đã chốt nhưng chưa có chức năng tự xóa.

## An toàn

- Webhook từ chối (403) nếu sai khóa `X-Bot-Api-Secret-Token`, và từ chối hết nếu chưa đặt khóa.
- Token không ghi vào log, nhật ký hay thông báo lỗi. Nhật ký thao tác chỉ ghi độ dài tin, không ghi nội dung.
- Trợ lý AI không có quyền đọc hay gửi tin ở màn này, và vẫn không được nhắn khách.

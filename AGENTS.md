# AGENTS.md — Hướng dẫn cho AI agent (Codex, Claude Code)

Đọc file này trước khi sửa code. Chi tiết kỹ thuật xem `README.md` và `docs/`.

## Bối cảnh
App quản lý hợp nhất của Công ty TNHH TM DV Tin Học Thành Đức (TP.HCM), chạy tại https://quanlythanhduc.com.
Công ty bán máy in mới/cũ, mực, linh kiện; sửa chữa, bảo trì, nạp mực; cung cấp mực định kỳ. 6–15 nhân viên dùng app, chủ yếu trên điện thoại.

Công nghệ: Python, FastAPI, PostgreSQL 16, Alembic, giao diện Jinja. Chạy bằng Docker Compose + Caddy/Cloudflare tunnel (`deploy/`).

## Thuật ngữ
| Từ | Nghĩa |
|---|---|
| Kho 58 / CH 58 MALO | Kho kiêm cửa hàng tại 58 Mã Lò |
| Kho 145 | Kho tại 145 Đường 24 |
| XNT | Xuất – Nhập – Tồn |
| Mã KTD | Mã khách hàng |
| Mã TD | Mã hàng hóa |
| Thợ / KTV | Kỹ thuật viên đi dịch vụ (màn hình `/tho`) |
| Nghiệm thu | Khách xác nhận hoàn thành công việc |
| Công nợ tháng | Khách trả gộp cuối tháng theo hóa đơn |

## Nghiệp vụ

### Đơn dịch vụ (không báo giá trước)
1. Đơn đến qua Zalo/hotline → tạo đơn, giao cho thợ.
2. Thợ nhận đơn, liên hệ khách, mang vật tư định mức; thiếu thì xin kho.
3. Làm xong → nghiệm thu → thu tiền khách, hoặc ghi công nợ tháng.
4. Thợ nộp tiền mặt về công ty vài ngày một lần, không cố định → app phải theo dõi được **tiền thợ đang giữ**.

### Thanh toán
- Khách có thể trả trước hoặc sau khi giao hàng/làm dịch vụ.
- Hình thức: tiền mặt, chuyển khoản vào tài khoản công ty, quẹt thẻ tại cửa hàng.
- Khách công nợ: mỗi việc nghiệm thu riêng; cuối tháng xuất hóa đơn tổng rồi thu.

### Kho
- Ghi sổ nhập/xuất cho cả hai kho đi qua một luồng duy nhất (app kho 58 cũ không còn tách kho 145).
- Mọi xuất kho phải gắn với đơn hàng, đơn dịch vụ hoặc lý do rõ ràng.
- Không sửa tồn kho trực tiếp; mọi thay đổi tồn phải có phiếu.

## Quy tắc dữ liệu (bắt buộc, xem README)
- Không ghi thẳng vào database; mọi thay đổi đi qua tầng nghiệp vụ/API.
- Sửa bản ghi phải kèm `version`; xung đột trả `VERSION_CONFLICT`.
- Mọi tạo/sửa ghi `audit_log`; không được sửa hoặc xóa nhật ký.
- Dữ liệu thử đánh dấu `is_test`.
- Tiền lưu số nguyên VNĐ, không dùng số thực.
- Không xóa cứng dữ liệu nghiệp vụ; ưu tiên đánh dấu ngừng dùng.

## Quy tắc khi sửa code
1. Đổi cấu trúc bảng: sửa `app/models.py` → `alembic revision --autogenerate` → **đọc lại file migration** → ghi rõ cách rollback trong mô tả PR.
2. Chạy test trước khi xong: `TD_TEST_DATABASE_URL=... pytest` (database test riêng, sẽ bị xóa sạch).
3. Không commit mật khẩu, API key, token, file `.env`. Cấu hình mẫu để ở `deploy/env.example`.
4. Không đưa dữ liệu thật của khách/nhân viên (SĐT, địa chỉ, lương) vào code, test hay tài liệu.
5. Giao diện, thông báo lỗi, tài liệu: tiếng Việt, dùng tốt trên điện thoại.
6. Thay đổi lớn: tạo branch + Pull Request, mô tả tiếng Việt: làm gì, vì sao, cách kiểm tra, cách quay lại.

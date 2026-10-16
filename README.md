# Thành Đức Core

Lõi Python + PostgreSQL dùng chung cho Sales, Kho, Finance, CARE và Điều phối.
Giai đoạn 0: đăng nhập, phân quyền, nhật ký thao tác, Danh bạ khách KTD, danh mục hàng mã TD, sao lưu.

Kế hoạch tổng thể: tài liệu "Kế hoạch chuyển sang Python + PostgreSQL + AI cho Thành Đức".

## Thư mục

| Đường dẫn | Nội dung |
| --- | --- |
| `app/` | Mã nguồn API (FastAPI) |
| `app/models.py` | Cấu trúc bảng |
| `app/customers.py` | Quy tắc khách hàng: mã KTD, chống trùng MST/SĐT, tìm không dấu |
| `migrations/` | Lịch sử thay đổi cấu trúc database (Alembic) |
| `scripts/` | Tạo tài khoản, nhập mẫu dữ liệu test, xóa dữ liệu test, sao lưu, khôi phục thử |
| `deploy/` | Docker Compose, Caddy (HTTPS), mẫu file cấu hình |
| `docs/TRIEN_KHAI_VPS.md` | Sổ tay dựng máy chủ từng bước |
| `docs/API.md` | Cách app Apps Script gọi API |
| `tests/` | Test chạy trên PostgreSQL thật |

Thư mục này nằm tạm trong repo SALE_THANHDUC cho tới khi có repo riêng THANHDUC-CORE.
Không đặt file `.js`, `.gs`, `.html`, `.json` trong `core/`: lệnh đẩy Apps Script của repo SALE đọc cả thư mục con.

## Quy tắc dữ liệu

- Không app nào ghi thẳng vào database; mọi thay đổi đi qua API để quy tắc nằm một chỗ.
- Mỗi lần sửa phải gửi kèm `version` đang thấy; người khác đã sửa trước thì bị từ chối `VERSION_CONFLICT`.
- Mọi tạo, sửa đều ghi `audit_log`. Database chặn sửa hoặc xóa nhật ký.
- Dữ liệu cũ chỉ nhập một phần để thử (`is_test`), xóa bằng `scripts.clear_test_data` trước khi chạy thật; sau đó nhập dữ liệu mới, chuẩn.

## Chạy test trên máy lập trình

Cần PostgreSQL 16 và một database rỗng chỉ dành cho test (test sẽ xóa sạch database này):

```
pip install -r requirements-dev.txt
createdb thanhduc_test
TD_TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost/thanhduc_test pytest
```

## Thêm thay đổi cấu trúc bảng

Sửa `app/models.py`, rồi tạo file migration và đọc lại trước khi commit:

```
TD_DATABASE_URL=... alembic revision --autogenerate -m "mo ta ngan"
alembic upgrade head
```

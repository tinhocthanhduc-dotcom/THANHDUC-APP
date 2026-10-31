# Thành Đức App

Hệ thống quản lý mới của Thành Đức viết bằng Python + PostgreSQL: Kho, Dịch vụ, Bán hàng, Mua hàng, Tài chính trên một database.
Giai đoạn 0: đăng nhập, phân quyền, nhật ký thao tác, Danh bạ khách KTD, danh mục hàng mã TD, sao lưu.

Kế hoạch tổng thể: tài liệu "Kế hoạch chuyển sang Python + PostgreSQL + AI cho Thành Đức".

## Thư mục

| Đường dẫn | Nội dung |
| --- | --- |
| `app/` | Mã nguồn API (FastAPI) |
| `app/models.py` | Cấu trúc bảng |
| `app/customers.py` | Quy tắc khách hàng: mã KTD, chống trùng MST/SĐT, tìm không dấu |
| `app/products.py` | Quy tắc danh mục hàng mã TD |
| `app/web/` | Giao diện web cho nhân viên (mẫu trang `.jinja`, `static/app.css`) |
| `migrations/` | Lịch sử thay đổi cấu trúc database (Alembic) |
| `scripts/` | Tạo tài khoản, nhập mẫu dữ liệu test, xóa dữ liệu test, sao lưu, khôi phục thử |
| `deploy/` | Docker Compose, Caddy (HTTPS), mẫu file cấu hình |
| `deploy/windows/` | Bộ cài trên Windows: đóng gói, cài lại một lệnh, khôi phục từ bản sao lưu (`HUONG-DAN-CAI-DAT.md`) |
| `docs/TRIEN_KHAI_VPS.md` | Sổ tay dựng máy chủ từng bước |
| `docs/API.md` | Cách app Apps Script gọi API |
| `tests/` | Test chạy trên PostgreSQL thật |

Code chuyển từ thư mục `core/` của repo SALE_THANHDUC sang đây ngày 17/10/2026, giữ nguyên lịch sử.
Các bước làm phân hệ Kho và ai làm bước nào: `/mnt/project-files/thiet-ke/kho-cac-buoc.md` trong project.

## Giao diện web

Mở tên miền của máy chủ trên máy tính hoặc điện thoại, đăng nhập bằng tài khoản do admin tạo.

| Trang | Việc |
| --- | --- |
| `/khach-hang` | Tìm khách khi đang gõ, thêm và sửa khách, người liên hệ, công nợ (kế toán, giám đốc) |
| `/hang-hoa` | Tìm mã hàng, Kho thêm và sửa mã TD, giá bán, ngừng bán |

Trang web gọi cùng tầng nghiệp vụ với API nên mọi quy tắc chống trùng, phân quyền, nhật ký đều giống nhau.
Đăng nhập giữ bằng cookie HttpOnly; mọi form có mã chống giả mạo (csrf).

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

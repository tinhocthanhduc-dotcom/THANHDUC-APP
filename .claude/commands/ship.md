---
description: Chạy dây chuyền 4 agent (Planner → Coder → Tester → Reviewer) cho một yêu cầu thay đổi
argument-hint: <mô tả yêu cầu bằng tiếng Việt>
---

Chạy dây chuyền 4 agent cho yêu cầu sau:

> $ARGUMENTS

Bạn là người điều phối. Không tự viết code — giao cho từng agent qua công cụ Agent, theo đúng thứ tự. Các agent bàn giao qua thư mục `.bangiao/`.

## Bước 0 — Chuẩn bị
1. `git status`: nếu có thay đổi chưa commit → **dừng**, báo người dùng.
2. Kiểm tra database test: `TD_TEST_DATABASE_URL` phải có và tên database có chữ `test` (chạy `scripts/chay-test.sh --migration`). Báo `DỪNG:` → **dừng**, chỉ người dùng xem `docs/day-chuyen-agent.md` mục Chuẩn bị.
3. Tạo nhánh mới từ nhánh hiện tại: `git checkout -b ship/<ten-ngan-khong-dau>` (nếu môi trường đã quy định tên nhánh, dùng nhánh đó).
4. Xóa nội dung cũ và tạo lại thư mục `.bangiao/`, ghi yêu cầu vào `.bangiao/00-yeu-cau.md`.

## Bước 1 — planner
Giao cho agent `planner`. Đọc `.bangiao/01-ke-hoach.md`.
- Dòng đầu là `TRẠNG THÁI: DỪNG – CẦN HỎI` → **dừng**, đưa các câu hỏi cho người dùng, chờ trả lời.

## Bước 2 — coder
Giao cho agent `coder`.

## Bước 3 — tester
Giao cho agent `tester`. Đọc `.bangiao/03-test.md`.
- `TRẠNG THÁI: RỚT` → giao lại `coder` sửa theo lỗi, rồi chạy lại `tester`. Tối đa **2 vòng**. Vẫn rớt → **dừng**, báo người dùng.

## Bước 4 — reviewer
Giao cho agent `reviewer`. Đọc `.bangiao/04-review.md`.
- `CẦN SỬA` → quay lại Bước 2 với các điểm cần sửa, tối đa **1 vòng**.
- `CHẶN` → **dừng**, báo lý do cho người dùng.
- `CHỐT` → sang Bước 5.

## Bước 5 — Kết thúc
1. Commit thay đổi (không gồm `.bangiao/`), message tiếng Việt.
2. **Không merge, không push lên `main`.** Hỏi người dùng có muốn push nhánh và tạo Pull Request không; mô tả PR lấy từ `.bangiao/04-review.md`.
3. Báo người dùng bằng tiếng Việt: đã làm gì, kết quả test, phán quyết review, cách kiểm tra bằng tay.

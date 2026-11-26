# Dây chuyền 4 agent (Claude Code)

Giao một yêu cầu sửa/thêm tính năng, Claude Code tự chạy 4 bước: lập kế hoạch → viết code → test → rà soát.

| Agent | Model | Được làm | Không được làm |
|---|---|---|---|
| `planner` | Opus | Đọc code, viết kế hoạch | Viết code |
| `coder` | Sonnet | Sửa `app/`, `migrations/`, `scripts/` | Sửa `tests/`, commit |
| `tester` | Sonnet | Viết test trong `tests/`, chạy pytest | Sửa code sản phẩm |
| `reviewer` | Opus | Đọc diff, ra phán quyết | Sửa bất kỳ file nào |

Các agent bàn giao qua thư mục `.bangiao/` (không đưa lên git):
`00-yeu-cau.md` → `01-ke-hoach.md` → `02-code.md` → `03-test.md` → `04-review.md`

## Chuẩn bị

Dây chuyền cần một database **test riêng** (bộ test xóa sạch nó mỗi lần chạy). Có 2 bảo vệ cứng:
`scripts/chay-test.sh` và `tests/conftest.py` đều **từ chối chạy** nếu tên database không có chữ `test`.
Mọi lệnh test/migration của agent đi qua `scripts/chay-test.sh`, script luôn ép alembic vào database test.

### Cách 1 (khuyên dùng): phiên Claude Code trên claude.ai/code
Không cần cài gì trên máy anh, không đụng máy chủ Z2. Trong phiên mới của repo này, gõ trước:
```
service postgresql start
su postgres -c "psql -c \"alter user postgres password 'postgres'\"" ; su postgres -c "createdb thanhduc_test"
pip install -r requirements-dev.txt
export TD_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/thanhduc_test
scripts/chay-test.sh --migration
```
Thấy `MIGRATION: ĐẠT` là sẵn sàng. Có thể đặt các lệnh này vào setup script của môi trường để khỏi gõ lại.

### Cách 2: máy tính riêng có Claude Code (không phải Z2)
1. Cài PostgreSQL 16, tạo database test: `createdb thanhduc_test`
2. `pip install -r requirements-dev.txt`
3. `export TD_TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost/thanhduc_test`
4. `scripts/chay-test.sh --migration` → thấy `MIGRATION: ĐẠT`.

⚠️ **Không chạy `/ship` trên máy chủ Z2.** Z2 đang chạy database thật của app, và quy tắc an toàn trên Z2
(`~/.claude/CLAUDE.md`) cấm sửa code trên máy chủ: code chỉ vào Z2 qua GitHub PR + lệnh cập nhật như thường lệ.

## Cách dùng
Mở Claude Code trong thư mục repo, gõ:
```
/ship Thêm cột "tiền thợ đang giữ" vào trang tổng quan, chỉ giám đốc và kế toán xem được
```

## Dây chuyền tự dừng khi
- Planner có câu hỏi chưa rõ → anh trả lời rồi gõ "tiếp tục".
- Test rớt quá 2 vòng sửa.
- Reviewer ra phán quyết **CHẶN**.

Dây chuyền **không tự merge, không tự push lên main**. Cuối cùng nó hỏi có tạo Pull Request không; GitHub Actions vẫn chạy test lại trên PR.

## Mẹo
- Mỗi lần `/ship` một yêu cầu nhỏ, rõ ràng. Việc lớn → chia nhỏ.
- Đọc `.bangiao/04-review.md` mục "Cách kiểm tra bằng tay" và thử trên app trước khi merge.
- Chạy test bằng tay: `scripts/chay-test.sh tests/test_x.py` (một file) hoặc `scripts/chay-test.sh` (toàn bộ: một máy chạy hơn 1 giờ, nên dây chuyền để GitHub Actions chạy toàn bộ trên PR).
- Tester không được nới lỏng test cũ để cho qua; Reviewer thấy vậy sẽ CHẶN.
- Chi phí: Opus chỉ chạy 2 lượt ngắn (kế hoạch, rà soát); phần lớn token ở Coder/Tester dùng Sonnet.

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

## Chuẩn bị (một lần trên máy chạy Claude Code)
1. Cài PostgreSQL 16, tạo database test riêng: `createdb thanhduc_test`
2. `pip install -r requirements-dev.txt`
3. Đặt biến: `export TD_TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost/thanhduc_test`
   ⚠️ Database này sẽ bị xóa sạch khi chạy test. **Không bao giờ** trỏ vào database thật.

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
- Chi phí: Opus chỉ chạy 2 lượt ngắn (kế hoạch, rà soát); phần lớn token ở Coder/Tester dùng Sonnet.

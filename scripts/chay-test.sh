#!/usr/bin/env bash
# Chạy test an toàn trên database test riêng (dùng cho người và dây chuyền agent /ship).
#   scripts/chay-test.sh                 → toàn bộ test
#   scripts/chay-test.sh tests/test_x.py → chỉ một file (thêm tham số pytest tùy ý)
#   scripts/chay-test.sh --migration     → alembic upgrade head + check + downgrade base (như CI)
# Cần TD_TEST_DATABASE_URL trỏ vào database có chữ "test" trong tên. Database này sẽ bị xóa sạch.
set -euo pipefail
cd "$(dirname "$0")/.."

url="${TD_TEST_DATABASE_URL:-}"
if [ -z "$url" ]; then
  echo "DỪNG: chưa đặt TD_TEST_DATABASE_URL (database test riêng)." >&2; exit 2
fi
name="${url##*/}"; name="${name%%\?*}"
case "${name,,}" in
  *test*) ;;
  *) echo "DỪNG: database '$name' không có chữ 'test' trong tên. Không chạy." >&2; exit 2 ;;
esac

# alembic đọc TD_DATABASE_URL: luôn ép về database test, không bao giờ dùng mặc định (database thật).
export TD_DATABASE_URL="$url"

if [ "${1:-}" = "--migration" ]; then
  alembic upgrade head
  alembic check
  alembic downgrade base
  echo "MIGRATION: ĐẠT"
else
  exec pytest -q "$@"
fi

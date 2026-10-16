#!/usr/bin/env bash
# Khôi phục thử một bản sao lưu vào database tạm, đếm số dòng, rồi xóa database tạm.
# Không đụng tới dữ liệu thật. Chạy mỗi tháng:  scripts/restore_check.sh [file.dump]
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=${BACKUP_DIR:-/var/backups/thanhduc}
FILE=${1:-$(find "$DIR/daily" -name '*.dump' | sort -r | head -1)}
DC="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
$DC exec -T db dropdb -U thanhduc --if-exists restore_check
$DC exec -T db createdb -U thanhduc restore_check
$DC exec -T db pg_restore -U thanhduc -d restore_check --no-owner < "$FILE"
$DC exec -T db psql -U thanhduc -d restore_check -c \
  "select (select count(*) from customers) as khach, (select count(*) from products) as hang, (select count(*) from audit_log) as nhat_ky"
$DC exec -T db dropdb -U thanhduc restore_check
echo "Khôi phục thử OK: $FILE"

#!/usr/bin/env bash
# Sao lưu database mỗi đêm. Cài vào cron của VPS (xem docs/TRIEN_KHAI_VPS.md):
#   0 1 * * * /opt/thanhduc/core/scripts/backup.sh >> /var/log/thanhduc-backup.log 2>&1
# Giữ 30 bản hằng ngày và 12 bản đầu tháng; đẩy ra ngoài VPS bằng rclone nếu có RCLONE_REMOTE.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source deploy/.env; set +a
DIR=${BACKUP_DIR:-/var/backups/thanhduc}
mkdir -p "$DIR/daily" "$DIR/monthly"
STAMP=$(date +%Y-%m-%d)
FILE="$DIR/daily/thanhduc-$STAMP.dump"
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec -T db \
  pg_dump -U thanhduc -d thanhduc --format=custom > "$FILE.tmp"
mv "$FILE.tmp" "$FILE"
# Tệp đính kèm (ảnh nghiệm thu, biên bản) đi cùng bản sao lưu database.
FILES="$DIR/daily/thanhduc-files-$STAMP.tar.gz"
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec -T api \
  tar -czf - -C /app/data files > "$FILES.tmp"
mv "$FILES.tmp" "$FILES"
if [ "$(date +%d)" = "01" ]; then cp "$FILE" "$FILES" "$DIR/monthly/"; fi
# Tên file có ngày nên sắp xếp theo tên là theo thời gian.
for kind in '*.dump' 'thanhduc-files-*.tar.gz'; do
  find "$DIR/daily" -name "$kind" | sort -r | tail -n +31 | xargs -r rm --
  find "$DIR/monthly" -name "$kind" | sort -r | tail -n +13 | xargs -r rm --
done
if [ -n "${RCLONE_REMOTE:-}" ]; then rclone sync "$DIR" "$RCLONE_REMOTE"; fi
echo "$(date -Is) OK $FILE $(du -h "$FILE" | cut -f1) $FILES $(du -h "$FILES" | cut -f1)"

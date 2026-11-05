#!/usr/bin/env bash
# Sao lưu database và tệp đính kèm trên máy Linux (VPS hoặc máy chủ Ubuntu ở văn phòng).
# Đặt lịch mỗi đêm bằng cron (crontab -e), ví dụ 23:00:
#   0 23 * * * /home/tuan/THANHDUC-APP/scripts/backup.sh >> /var/backups/thanhduc/backup.log 2>&1
# Giữ 30 bản hằng ngày và 12 bản đầu tháng; đẩy ra ngoài máy bằng rclone nếu có RCLONE_REMOTE.
# BACKUP_DIR, RCLONE_REMOTE lấy từ biến môi trường hoặc deploy/.env.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_compose.sh
DIR=${BACKUP_DIR:-$(env_get BACKUP_DIR)}
DIR=${DIR:-/var/backups/thanhduc}
REMOTE=${RCLONE_REMOTE:-$(env_get RCLONE_REMOTE)}
mkdir -p "$DIR/daily" "$DIR/monthly"
STAMP=$(date +%Y-%m-%d)
FILE="$DIR/daily/thanhduc-$STAMP.dump"
"${DC[@]}" exec -T db pg_dump -U thanhduc -d thanhduc --format=custom > "$FILE.tmp"
mv "$FILE.tmp" "$FILE"
# Tệp đính kèm (ảnh nghiệm thu, biên bản) đi cùng bản sao lưu database.
FILES="$DIR/daily/thanhduc-files-$STAMP.tar.gz"
"${DC[@]}" exec -T api tar -czf - -C /app/data files > "$FILES.tmp"
mv "$FILES.tmp" "$FILES"
# Dữ liệu n8n (workflow, mật khẩu đã mã hóa và khóa mã hóa) nếu máy có chạy n8n. Lỗi thì bỏ qua, không làm hỏng bản sao lưu chính.
N8N=${N8N_CONTAINER:-$(env_get CANH_MAY_N8N_CONTAINER)}
N8N=${N8N:-n8n-n8n-1}
N8NFILE=""
if docker inspect "$N8N" >/dev/null 2>&1; then
  if docker exec "$N8N" tar -czf - -C /home/node .n8n > "$DIR/daily/n8n-$STAMP.tar.gz.tmp"; then
    N8NFILE="$DIR/daily/n8n-$STAMP.tar.gz"
    mv "$N8NFILE.tmp" "$N8NFILE"
  else
    rm -f "$DIR/daily/n8n-$STAMP.tar.gz.tmp"
    echo "$(date -Is) Bỏ qua sao lưu n8n: không đọc được /home/node/.n8n trong $N8N"
  fi
fi
if [ "$(date +%d)" = "01" ]; then cp "$FILE" "$FILES" $N8NFILE "$DIR/monthly/"; fi
# Tên file có ngày nên sắp xếp theo tên là theo thời gian.
for kind in 'thanhduc-2*.dump' 'thanhduc-files-*.tar.gz' 'n8n-*.tar.gz'; do
  find "$DIR/daily" -name "$kind" | sort -r | tail -n +31 | xargs -r rm --
  find "$DIR/monthly" -name "$kind" | sort -r | tail -n +13 | xargs -r rm --
done
# rclone-ok: dấu "đẩy Google Drive thành công" cho scripts/canh_may.py.
if [ -n "$REMOTE" ]; then
  rclone sync "$DIR" "$REMOTE" --exclude backup.log --exclude canh-may.log --exclude rclone-ok --exclude '*.tmp'
  touch "$DIR/rclone-ok"
fi
echo "$(date -Is) OK $FILE $(du -h "$FILE" | cut -f1) $FILES $(du -h "$FILES" | cut -f1)"

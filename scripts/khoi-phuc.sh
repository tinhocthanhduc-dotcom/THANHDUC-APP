#!/usr/bin/env bash
# Khôi phục app Thành Đức trên máy Linux từ bản sao lưu của backup.sh hoặc backup.ps1
# (database thanhduc-<ngày>.dump + tệp đính kèm thanhduc-files-<ngày>.tar.gz cùng thư mục).
#   Thử bản sao lưu, KHÔNG đụng dữ liệu thật:
#     scripts/khoi-phuc.sh --chi-thu [file.dump | thư mục]
#   Khôi phục thật (xóa dữ liệu đang có, thay bằng bản sao lưu):
#     scripts/khoi-phuc.sh ~/chuyen-may/thanhduc-2026-11-01.dump
#   --khong-tunnel: chỉ bật database + app, không bật Cloudflare Tunnel (tập dượt khi máy cũ còn chạy).
#   --khong-hoi:    không hỏi gõ CO, không lưu bản hiện tại trước khi xóa.
# Để trống file = bản mới nhất trong $BACKUP_DIR/daily (mặc định /var/backups/thanhduc/daily).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_compose.sh

CHI_THU=0; KHONG_HOI=0; KHONG_TUNNEL=0; SAO_LUU=""
for a in "$@"; do
  case "$a" in
    --chi-thu) CHI_THU=1 ;;
    --khong-hoi) KHONG_HOI=1 ;;
    --khong-tunnel) KHONG_TUNNEL=1 ;;
    -*) echo "Không hiểu tùy chọn $a" >&2; exit 2 ;;
    *) SAO_LUU=$a ;;
  esac
done
if [ -z "$SAO_LUU" ]; then
  SAO_LUU=${BACKUP_DIR:-$(env_get BACKUP_DIR)}
  SAO_LUU=${SAO_LUU:-/var/backups/thanhduc}/daily
fi

xanh() { printf '\033[36m== %s\033[0m\n' "$*"; }
vang() { printf '\033[33m%s\033[0m\n' "$*"; }
loi() { printf '\033[31mLỖI: %s\033[0m\n' "$*" >&2; exit 1; }

# Tìm file .dump và file tệp đính kèm cùng ngày.
[ -e "$SAO_LUU" ] || loi "Không thấy $SAO_LUU"
if [ -d "$SAO_LUU" ]; then
  DUMP=$(find "$SAO_LUU" -name 'thanhduc-2*.dump' | sort -r | head -1)
  [ -n "$DUMP" ] || loi "Trong $SAO_LUU không có file thanhduc-<ngày>.dump"
else
  DUMP=$SAO_LUU
fi
DUMP=$(realpath "$DUMP")
TEP="$(dirname "$DUMP")/$(basename "$DUMP" | sed -e 's/^thanhduc-/thanhduc-files-/' -e 's/\.dump$/.tar.gz/')"
[ -f "$TEP" ] || TEP=""
echo "Database: $DUMP ($(du -h "$DUMP" | cut -f1))"
if [ -n "$TEP" ]; then echo "Tệp đính kèm: $TEP"; else vang "Không thấy file tệp đính kèm cùng ngày, chỉ khôi phục database."; fi

cho_db() {
  for _ in $(seq 40); do
    "${DC[@]}" exec -T db pg_isready -U thanhduc -d postgres </dev/null >/dev/null 2>&1 && return
    sleep 3
  done
  loi "Database chưa sẵn sàng sau 2 phút."
}
dem() {
  "${DC[@]}" exec -T db psql -U thanhduc -d "$1" -c \
    "select (select count(*) from customers) as khach, (select count(*) from products) as hang, (select count(*) from users) as tai_khoan, (select count(*) from audit_log) as nhat_ky"
}

"${DC[@]}" up -d db
cho_db
"${DC[@]}" cp "$DUMP" db:/tmp/restore.dump

if [ "$CHI_THU" = 1 ]; then
  xanh "Khôi phục thử vào database tạm restore_check"
  "${DC[@]}" exec -T db dropdb -U thanhduc --if-exists restore_check
  "${DC[@]}" exec -T db createdb -U thanhduc restore_check
  rc=0
  "${DC[@]}" exec -T db pg_restore -U thanhduc -d restore_check --no-owner --no-privileges /tmp/restore.dump || rc=$?
  dem restore_check
  "${DC[@]}" exec -T db dropdb -U thanhduc restore_check
  if [ -n "$TEP" ]; then
    echo "Số tệp đính kèm trong bản sao lưu:"
    tar -tzf "$TEP" | grep -vc '/$' || true
  fi
  [ "$rc" = 0 ] || vang "pg_restore có cảnh báo (mã $rc), xem các dòng phía trên."
  printf '\033[32mKhôi phục thử OK: %s. Dữ liệu thật không bị đụng tới.\033[0m\n' "$DUMP"
  exit 0
fi

if [ "$KHONG_HOI" = 0 ]; then
  printf '\033[31mLệnh này XÓA toàn bộ dữ liệu app đang có trên máy này và thay bằng bản sao lưu trên.\033[0m\n'
  tl=""
  read -r -p "Gõ CO để tiếp tục: " tl < /dev/tty || true
  [ "$tl" = "CO" ] || { echo "Đã hủy."; exit 1; }
  # Lưu bản hiện tại trước khi xóa, phòng chọn nhầm file.
  TRUOC="$HOME/thanhduc-truoc-khoi-phuc-$(date +%Y-%m-%d-%H%M).dump"
  "${DC[@]}" exec -T db pg_dump -U thanhduc -d thanhduc --format=custom > "$TRUOC" \
    || loi "Lưu bản hiện tại lỗi, dừng lại để không mất dữ liệu."
  echo "Đã lưu dữ liệu hiện tại vào $TRUOC"
fi

xanh "Khôi phục database"
# App phải tắt để không ai ghi vào database trong lúc thay.
"${DC[@]}" stop api >/dev/null 2>&1 || true
"${DC[@]}" exec -T db psql -U thanhduc -d postgres -c "DROP DATABASE IF EXISTS thanhduc WITH (FORCE)"
"${DC[@]}" exec -T db createdb -U thanhduc thanhduc
rc=0
"${DC[@]}" exec -T db pg_restore -U thanhduc -d thanhduc --no-owner --no-privileges /tmp/restore.dump || rc=$?
[ "$rc" = 0 ] || vang "pg_restore có cảnh báo (mã $rc). Xem số dòng bên dưới; nếu bằng 0 thì chụp màn hình gửi Claude."
dem thanhduc

xanh "Bật app (tự nâng cấp cấu trúc database nếu bản sao lưu cũ hơn mã nguồn)"
if [ "$KHONG_TUNNEL" = 1 ]; then "${DC[@]}" up -d db api; else "${DC[@]}" up -d; fi
ok=0
for _ in $(seq 60); do
  if "${DC[@]}" exec -T api python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')" </dev/null >/dev/null 2>&1; then ok=1; break; fi
  sleep 3
done
[ "$ok" = 1 ] || loi "App chưa lên sau 3 phút. Chạy: ${DC[*]} logs --tail 100 api"

if [ -n "$TEP" ]; then
  xanh "Khôi phục tệp đính kèm (ảnh, PDF)"
  "${DC[@]}" exec -T api sh -c 'tar -xzf - -C /app/data' < "$TEP"
fi
"${DC[@]}" exec -T db rm -f /tmp/restore.dump
printf '\033[32mKhôi phục xong từ %s. Đăng nhập bằng tài khoản cũ như trước.\033[0m\n' "$DUMP"
[ "$KHONG_TUNNEL" = 0 ] || vang "Chưa bật Cloudflare Tunnel (--khong-tunnel): người dùng chưa vào được máy này."

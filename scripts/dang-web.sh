#!/usr/bin/env bash
# Chạy công cụ đăng web trên Z2. Ví dụ:
#   scripts/dang-web.sh kiem-tra
#   scripts/dang-web.sh san-pham --ma TD-0001,TD-0002 --thu
#   scripts/dang-web.sh san-pham --tu-dong --so 3
#   scripts/dang-web.sh bat-may     bật máy đăng web chạy nền cho màn Đăng web trong app (tự chạy lại khi Z2 khởi động)
#   scripts/dang-web.sh tat-may     tắt máy đăng web chạy nền
#   scripts/dang-web.sh nhat-ky     xem 80 dòng nhật ký gần nhất của máy đăng web
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_compose.sh

# Thư mục làm việc nằm ngoài repo để git pull không đụng tới ảnh và kết quả.
WEB_DIR="$(env_get WEB_THU_MUC)"
WEB_DIR="${WEB_DIR:-$HOME/THANHDUC-WEB}"
mkdir -p "$WEB_DIR"/{anh-goc,anh-bai,ghi-chu,ket-qua,xem-truoc,anh-da-xu-ly,viec}
[ -f "$WEB_DIR/chu-de.txt" ] || printf '# Mỗi dòng một chủ đề bài viết. Dòng bắt đầu bằng # bị bỏ qua.\n' > "$WEB_DIR/chu-de.txt"
export WEB_THU_MUC="$WEB_DIR"

WEB=("${DC[@]}" -f deploy/docker-compose.web.yml --profile web)
case "${1:-}" in
  bat-may) exec "${WEB[@]}" up -d --build dang-web-may ;;
  tat-may) exec "${WEB[@]}" stop dang-web-may ;;
  nhat-ky) exec "${WEB[@]}" logs --tail 80 dang-web-may ;;
esac

# --build: lần đầu dựng image (vài phút), các lần sau dùng lại bộ nhớ đệm nên nhanh.
exec "${WEB[@]}" run --rm --build dang-web "$@"

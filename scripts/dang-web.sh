#!/usr/bin/env bash
# Chạy công cụ đăng web trên Z2. Ví dụ:
#   scripts/dang-web.sh kiem-tra
#   scripts/dang-web.sh san-pham --ma TD-0001,TD-0002 --thu
#   scripts/dang-web.sh san-pham --tu-dong --so 3
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_compose.sh

# Thư mục làm việc nằm ngoài repo để git pull không đụng tới ảnh và kết quả.
WEB_DIR="$(env_get WEB_THU_MUC)"
WEB_DIR="${WEB_DIR:-$HOME/THANHDUC-WEB}"
mkdir -p "$WEB_DIR"/{anh-goc,anh-bai,ghi-chu,ket-qua,xem-truoc,anh-da-xu-ly}
[ -f "$WEB_DIR/chu-de.txt" ] || printf '# Mỗi dòng một chủ đề bài viết. Dòng bắt đầu bằng # bị bỏ qua.\n' > "$WEB_DIR/chu-de.txt"
export WEB_THU_MUC="$WEB_DIR"

# --build: lần đầu dựng image (vài phút), các lần sau dùng lại bộ nhớ đệm nên nhanh.
exec "${DC[@]}" -f deploy/docker-compose.web.yml --profile web run --rm --build dang-web "$@"

#!/usr/bin/env bash
# Khôi phục thử một bản sao lưu vào database tạm, đếm số dòng, rồi xóa database tạm.
# Không đụng tới dữ liệu thật. Chạy mỗi tháng:  scripts/restore_check.sh [file.dump]
# Bản đầy đủ hơn (đếm cả tệp đính kèm): scripts/khoi-phuc.sh --chi-thu
set -euo pipefail
cd "$(dirname "$0")/.."
exec scripts/khoi-phuc.sh --chi-thu "$@"

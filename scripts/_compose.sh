# Dùng chung cho các script bash: đọc deploy/.env an toàn và dựng lệnh docker compose.
# Gọi bằng: source "$(dirname "$0")/_compose.sh" (sau khi cd vào thư mục repo).

# Đọc một biến trong deploy/.env, không chạy file đó như lệnh shell
# (file chép từ Windows có thể có \r, giá trị có dấu cách không ngoặc).
env_get() {
  { grep -E "^$1=" deploy/.env 2>/dev/null || true; } | tail -1 | cut -d= -f2- | tr -d '\r' \
    | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# Máy văn phòng chạy qua Cloudflare Tunnel thì thêm file tunnel, giống lệnh cập nhật.
DC=(docker compose -f deploy/docker-compose.yml)
if [ -n "$(env_get TUNNEL_TOKEN)" ]; then DC+=(-f deploy/docker-compose.tunnel.yml); fi
DC+=(--env-file deploy/.env)

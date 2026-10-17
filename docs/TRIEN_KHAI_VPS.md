# Sổ tay dựng máy chủ Thành Đức Core

Làm một lần khi thuê VPS. Mỗi bước có lệnh để chép nguyên văn. Chỗ nào ghi `<...>` thì thay bằng giá trị thật.

## 1. Thuê VPS

- Cấu hình tối thiểu: 2 vCPU, 4GB RAM, 80GB SSD, Ubuntu 24.04.
- Bật xác thực 2 bước cho tài khoản quản trị ở trang nhà cung cấp.
- Ghi lại địa chỉ IP của VPS.

## 2. Trỏ tên miền

Ở trang quản lý tên miền của công ty, tạo bản ghi `A`: tên `api` trỏ về IP của VPS.
Ví dụ được `api.<ten-mien-cong-ty>`. Chờ vài phút rồi kiểm tra: `ping api.<ten-mien-cong-ty>` ra đúng IP.

## 3. Đăng nhập lần đầu và khóa máy chủ

Trên máy tính của anh, tạo khóa SSH nếu chưa có: `ssh-keygen -t ed25519`. Sau đó:

```
ssh-copy-id root@<IP>
ssh root@<IP>
apt update && apt -y upgrade
adduser tuan && usermod -aG sudo tuan
mkdir -p /home/tuan/.ssh && cp ~/.ssh/authorized_keys /home/tuan/.ssh/ && chown -R tuan:tuan /home/tuan/.ssh
# Tắt đăng nhập bằng mật khẩu và tắt đăng nhập root
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh
# Tường lửa: chỉ mở SSH, HTTP, HTTPS
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

Mở cửa sổ mới, thử `ssh tuan@<IP>` được rồi mới đóng cửa sổ cũ.

## 4. Cài Docker và lấy mã nguồn

```
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker tuan   # đăng xuất, đăng nhập lại
sudo mkdir -p /opt/thanhduc && sudo chown tuan /opt/thanhduc
git clone https://github.com/tinhocthanhduc-dotcom/THANHDUC-APP.git /opt/thanhduc/core
cd /opt/thanhduc/core
```

Repo THANHDUC-APP để chế độ riêng tư, nên máy chủ cần quyền đọc repo (deploy key chỉ đọc) trước khi clone.

## 5. Tạo file cấu hình bí mật

```
cp deploy/env.example deploy/.env
chmod 600 deploy/.env
openssl rand -hex 24   # chép vào DB_PASSWORD
openssl rand -hex 32   # chép vào SECRET_KEY
nano deploy/.env       # điền DOMAIN=api.<ten-mien-cong-ty>
```

File `.env` chỉ nằm trên VPS. Không gửi qua chat, không đưa lên GitHub.

## 6. Chạy

```
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
curl https://api.<ten-mien-cong-ty>/health     # phải thấy {"ok":true}
```

Tạo tài khoản đầu tiên (giám đốc kiêm quản trị):

```
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec api \
  python -m scripts.create_admin --username tuan --name "Bùi Minh Tuấn" --roles admin,director
```

Tài khoản nhân viên và tài khoản riêng cho từng app Apps Script tạo qua `POST /users` (xem `docs/API.md`).

## 7. Sao lưu tự động

```
sudo mkdir -p /var/backups/thanhduc && sudo chown tuan /var/backups/thanhduc
crontab -e
# thêm dòng:
0 1 * * * /opt/thanhduc/core/scripts/backup.sh >> /var/backups/thanhduc/backup.log 2>&1
```

Đẩy bản sao lưu ra ngoài VPS (Google Drive công ty): `sudo apt install rclone`, chạy `rclone config`
tạo remote tên `gdrive`, rồi điền `RCLONE_REMOTE=gdrive:thanhduc-backup` trong `deploy/.env`.

Chạy thử ngay một lần: `scripts/backup.sh`, rồi `scripts/restore_check.sh`.
Thấy dòng "Khôi phục thử OK" là đạt cổng giai đoạn 0. Lặp lại `restore_check.sh` mỗi tháng.

## 8. Việc thường gặp

| Việc | Lệnh (chạy trong `/opt/thanhduc/core`) |
| --- | --- |
| Xem tình trạng | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps` |
| Xem lỗi API | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs --tail 100 api` |
| Cập nhật phiên bản mới | `git pull && docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build` |
| Khởi động lại | `docker compose -f deploy/docker-compose.yml --env-file deploy/.env restart api` |
| Đếm dữ liệu thử nghiệm | `docker compose ... exec api python -m scripts.clear_test_data` |
| Xóa dữ liệu thử nghiệm trước khi chạy thật | thêm `--yes` vào lệnh trên |

Trước mỗi lần cập nhật phiên bản mới, chạy `scripts/backup.sh` một lần.

## Khôi phục khi mất máy chủ

1. Thuê VPS mới, làm lại bước 2 tới 6 (dùng lại `deploy/.env` cũ nếu còn, nếu không thì tạo mới).
2. Lấy bản sao lưu mới nhất từ Google Drive về `/var/backups/thanhduc/daily/`.
3. Nạp lại:

```
DC="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
$DC stop api
$DC exec -T db dropdb -U thanhduc thanhduc
$DC exec -T db createdb -U thanhduc thanhduc
$DC exec -T db pg_restore -U thanhduc -d thanhduc --no-owner < /var/backups/thanhduc/daily/<file>.dump
$DC start api
```

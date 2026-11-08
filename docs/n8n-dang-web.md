# n8n trên Z2 nhắn Zalo sản phẩm Đăng web chờ duyệt (8:00 và 16:00)

Lúc 8:00 và 16:00, n8n hỏi app có sản phẩm nào ở màn **Đăng web** đang chờ người: chờ anh xem trước (chờ hơn 1 ngày
có ghi chú), có thể trùng, chờ dán ChatGPT, máy đăng bị lỗi, hoặc máy đăng web trên Z2 đang tắt. Có việc thì nhắn
Zalo một tin, mỗi dòng có link mở thẳng việc trong app. Không có việc thì không nhắn.

n8n **chỉ đọc**: không duyệt, không đăng. Anh bấm link, xem trước rồi tự bấm **Duyệt và đăng** trong app.

## Cài một lần

1. Cập nhật app trên Z2 như mọi lần:
   ```bash
   cd ~/THANHDUC-APP && git pull origin main
   docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.tunnel.yml --env-file deploy/.env up -d --build
   ```
2. Mở app bằng tài khoản giám đốc: **Quản trị → Trợ lý AI → Khóa truy cập** → ở dòng **n8n · Nhắn việc chờ duyệt**
   bấm **Tạo khóa**. Khóa chỉ hiện một lần, chép ra chỗ an toàn.
3. Nối n8n vào mạng nội bộ của app (khóa Trợ lý AI không cho đi qua Cloudflare, nên n8n gọi thẳng `http://api:8000`):
   ```bash
   docker network connect deploy_default n8n-n8n-1
   ```
   Để lần sau dựng lại n8n vẫn nối, mở `~/n8n/docker-compose.yml`, thêm vào service n8n và cuối file:
   ```yaml
   services:
     n8n:
       # ... giữ nguyên các dòng đang có
       networks: [default, app]
   networks:
     app:
       external: true
       name: deploy_default
   ```
4. Kiểm tra cổng từ Z2 (thay `<khóa>`):
   ```bash
   curl -s -H "Authorization: Bearer <khóa>" http://127.0.0.1:8010/n8n/viec-cho
   ```
   Phải thấy `{"so_viec": ..., "co_tin": ..., "tin": "..."}`. Báo 401 là sai khóa, 423 là khóa đang tạm dừng.
5. Đưa workflow vào n8n:
   ```bash
   cd ~/THANHDUC-APP
   docker cp deploy/n8n/dang-web-viec-cho.json n8n-n8n-1:/tmp/dang-web-viec-cho.json
   docker exec n8n-n8n-1 n8n import:workflow --input=/tmp/dang-web-viec-cho.json
   ```
6. Mở n8n (`http://192.168.10.200:5678`) → workflow **Đăng web - nhắn sản phẩm chờ duyệt** → nút **Cấu hình**, điền:
   - `app_url`: để nguyên `http://api:8000`
   - `khoa_n8n`: khóa ở bước 2
   - `zalo_token`: token bot Zalo (dùng chung bot canh máy: `CANH_MAY_ZALO_BOT_TOKEN`, hoặc `TD_ZALO_BOT_TOKEN`
     trong `~/THANHDUC-APP/deploy/.env`)
   - `zalo_chat_id`: mã chat của anh (`CANH_MAY_ZALO_CHAT_ID` trong cùng file)
7. **Save** → **Test workflow** (có việc chờ là Zalo nhận tin) → bật **Active**.

Không gửi khóa, token vào chat. Không xuất workflow đã điền ra ngoài vì trong đó có token.

## Đổi giờ, tắt

- Đổi giờ: nút **8:00 và 16:00**, sửa `0 8,16 * * *` (phút giờ * * *), giờ Việt Nam.
- Tắt tạm: tắt **Active** của workflow, hoặc **Tạm dừng** khóa n8n ở Trợ lý AI. App vẫn chạy bình thường.
- Lộ khóa: **Thu hồi** rồi **Tạo khóa** mới, dán lại vào nút Cấu hình.

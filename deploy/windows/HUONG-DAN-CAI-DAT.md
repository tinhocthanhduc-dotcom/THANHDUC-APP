# Bộ cài app Thành Đức: đóng gói, cài lại, khôi phục

Dùng khi: cất một bản app hoàn chỉnh để phòng hỏng máy, chuyển app sang máy văn phòng khác, hoặc thử xem bản sao lưu có dùng được không.

Ba script nằm trong `deploy\windows\` của mã nguồn:

| Script | Việc |
| --- | --- |
| `tao-bo-cai.ps1` | Chạy trên máy đang chạy app. Gom mã nguồn + dữ liệu mới nhất (+ image Docker nếu muốn) thành một file zip. |
| `cai-dat.ps1` | Chạy trên máy mới. Tạo cấu hình, bật app, nạp dữ liệu từ gói, một lệnh là xong. |
| `khoi-phuc.ps1` | Thử một bản sao lưu mà không đụng dữ liệu thật, hoặc khôi phục thật khi cần. |

## Trong gói zip có gì

```
thanhduc-bo-cai-<ngày giờ>.zip
  HUONG-DAN-CAI-DAT.md     file này
  PHIEN-BAN.txt            ngày đóng gói, mã phiên bản
  THANHDUC-APP\            mã nguồn đầy đủ
  sao-luu\daily\           database (.dump) + ảnh, PDF đính kèm (.tar.gz)
  images\                  (chỉ khi dùng -KemImage) image Docker, cài không cần Internet
```

Gói **không** chứa file `deploy\.env` (mật khẩu database, SECRET_KEY, mã tunnel). Cài lại sẽ tự tạo mật khẩu mới, mã tunnel lấy lại trong Cloudflare. Nhờ vậy gói lỡ rơi ra ngoài cũng không mở được tunnel của công ty. Tuy vậy gói **có dữ liệu khách hàng, công nợ**, nên chỉ cất ở Google Drive của công ty hoặc USB riêng.

## A. Tạo gói trên máy văn phòng

Mở PowerShell, chạy:

```
cd "C:\APPS SCRIPT\SALE_THANHDUC\THANHDUC-APP"
powershell -ExecutionPolicy Bypass -File deploy\windows\tao-bo-cai.ps1 -KemImage
```

- Mất khoảng 2–5 phút. App vẫn chạy bình thường trong lúc đóng gói.
- Xong sẽ hiện `XONG: D:\thanhduc-bo-cai\thanhduc-bo-cai-....zip (... MB)`.
- Bỏ `-KemImage` nếu chỉ cần gói nhỏ (vài chục MB); khi cài lại máy mới phải có Internet để tải image.
- Chép file zip lên Google Drive (ví dụ kéo vào thư mục `D:\thanhduc-backup`, Drive tự đồng bộ) hoặc USB.

Nên tạo gói mới sau mỗi đợt cập nhật lớn và ít nhất mỗi tháng một lần. Bản sao lưu hằng đêm lúc 23:00 vẫn chạy riêng, không thay cho việc này.

## B. Cài lên máy mới

1. **Tắt app ở máy cũ** nếu máy cũ còn chạy (`docker compose ... down` hoặc tắt máy). Hai máy cùng mã tunnel thì Cloudflare chia khách vào cả hai, dữ liệu sẽ lệch.
2. Cài **Docker Desktop** (https://www.docker.com/products/docker-desktop/), mở lên, chờ biểu tượng cá voi đứng yên. Settings > General: bật *Start Docker Desktop when you sign in*. Muốn dữ liệu Docker ở ổ D thì đổi trong Settings > Resources > Advanced > Disk image location **trước** khi cài.
3. Giải nén file zip vào một thư mục, ví dụ `D:\thanhduc`. Đường dẫn có dấu cách cũng được.
4. Lấy mã tunnel: dash.cloudflare.com > **Zero Trust** > **Networks > Tunnels** > `thanhduc-van-phong` > **Configure** > chép chuỗi dài sau `--token`.
5. Mở PowerShell, chạy:

```
cd D:\thanhduc\THANHDUC-APP
powershell -ExecutionPolicy Bypass -File deploy\windows\cai-dat.ps1
```

6. Script hỏi `TUNNEL_TOKEN`, dán mã ở bước 4, Enter. Sau đó tự làm hết:
   - tạo `deploy\.env` với mật khẩu mới,
   - nạp image (nếu gói có) hoặc tải và build,
   - nạp dữ liệu từ thư mục `sao-luu` trong gói, rồi bật app.
   Gói không có dữ liệu thì app chạy với database trống và script hỏi tên đăng nhập, mật khẩu Giám đốc.
7. Kiểm tra: trong Cloudflare tunnel hiện **HEALTHY**; trên điện thoại tắt wifi, mở https://quanlythanhduc.com, đăng nhập bằng tài khoản cũ.
8. Làm lại mục 1 (chạy liên tục) và mục 5 (sao lưu mỗi đêm) trong hướng dẫn `may-van-phong-lam-may-chu`, thay đường dẫn bằng thư mục mới.

Lệnh cập nhật về sau giống máy cũ, chạy trong thư mục mới. Nếu thư mục mới không có `.git` (giải nén từ zip) thì cài **GitHub Desktop**, clone lại repo THANHDUC-APP vào chỗ khác, chép file `deploy\.env` sang, rồi dùng bản clone đó cho các lần `git pull` sau. Dữ liệu nằm trong Docker nên đổi thư mục mã nguồn không mất dữ liệu.

Muốn dùng bản sao lưu khác thay cho bản trong gói:

```
powershell -ExecutionPolicy Bypass -File deploy\windows\cai-dat.ps1 -SaoLuu "D:\thanhduc-backup\daily"
```

## C. Thử bản sao lưu (mỗi tháng một lần, an toàn)

Trên máy đang chạy app:

```
cd "C:\APPS SCRIPT\SALE_THANHDUC\THANHDUC-APP"
powershell -ExecutionPolicy Bypass -File deploy\windows\khoi-phuc.ps1 -ChiThu
```

Script lấy bản mới nhất trong `D:\thanhduc-backup\daily`, khôi phục vào một database tạm, in số khách, số mã hàng, số tài khoản, số nhật ký và số tệp đính kèm, rồi xóa database tạm. Thấy dòng **Khôi phục thử OK** và các con số giống trên app là bản sao lưu dùng được. Dữ liệu thật không bị đụng tới.

## D. Khôi phục thật trên máy đang chạy

Chỉ dùng khi dữ liệu hỏng hoặc lỡ xóa nhầm. Mọi thứ nhập sau thời điểm sao lưu sẽ mất.

```
powershell -ExecutionPolicy Bypass -File deploy\windows\khoi-phuc.ps1 -SaoLuu "D:\thanhduc-backup\daily\thanhduc-2026-10-30.dump"
```

- Script hỏi, phải gõ `CO` mới làm.
- Trước khi xóa, script lưu dữ liệu hiện tại vào `C:\Users\<tên>\thanhduc-truoc-khoi-phuc-<giờ>.dump`, chọn nhầm file vẫn lấy lại được.
- App tắt khoảng 1–2 phút trong lúc khôi phục.
- Bản sao lưu cũ hơn mã nguồn cũng được: app tự nâng cấp cấu trúc database khi bật lên.

## Khi có lỗi

Chụp toàn bộ cửa sổ PowerShell gửi Claude trong project. Lệnh xem lỗi app:

```
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.tunnel.yml --env-file deploy/.env logs --tail 100 api
```

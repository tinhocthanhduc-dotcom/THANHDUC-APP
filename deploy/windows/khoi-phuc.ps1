# Khôi phục app Thành Đức từ bản sao lưu của scripts\backup.ps1 (database .dump + tệp đính kèm .tar.gz).
#   Thử bản sao lưu, KHÔNG đụng dữ liệu thật (nên làm mỗi tháng):
#     powershell -ExecutionPolicy Bypass -File deploy\windows\khoi-phuc.ps1 -ChiThu
#   Khôi phục thật (xóa dữ liệu đang có, thay bằng bản sao lưu):
#     powershell -ExecutionPolicy Bypass -File deploy\windows\khoi-phuc.ps1 -SaoLuu D:\thanhduc-backup\daily\thanhduc-2026-10-30.dump
# -SaoLuu nhận file .dump hoặc thư mục (lấy bản mới nhất). Để trống = D:\thanhduc-backup\daily.
param(
  [string]$SaoLuu = "D:\thanhduc-backup\daily",
  [switch]$ChiThu,
  [switch]$KhongHoi
)
$ErrorActionPreference = "Stop"
$app = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $app
$compose = @("compose", "-f", "deploy/docker-compose.yml", "-f", "deploy/docker-compose.tunnel.yml", "--env-file", "deploy/.env")

function Thu-Docker([string[]]$a) {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  & docker @a *> $null
  $ok = ($LASTEXITCODE -eq 0)
  $ErrorActionPreference = $old
  return $ok
}
function Chay([string[]]$a, [string]$loi) {
  & docker @compose @a
  if ($LASTEXITCODE -ne 0) { throw $loi }
}
function Cho-Db {
  foreach ($n in 1..40) {
    if (Thu-Docker ($compose + @("exec", "-T", "db", "pg_isready", "-U", "thanhduc", "-d", "postgres"))) { return }
    Start-Sleep -Seconds 3
  }
  throw "Database chưa sẵn sàng sau 2 phút."
}
function Dem([string]$db) {
  Chay @("exec", "-T", "db", "psql", "-U", "thanhduc", "-d", $db, "-c",
    "select (select count(*) from customers) as khach, (select count(*) from products) as hang, (select count(*) from users) as tai_khoan, (select count(*) from audit_log) as nhat_ky") "Đếm dữ liệu lỗi."
}

# Tìm file .dump và file tệp đính kèm cùng ngày.
if (-not (Test-Path $SaoLuu)) { throw "Không thấy $SaoLuu" }
if ((Get-Item $SaoLuu).PSIsContainer) {
  $f = Get-ChildItem $SaoLuu -Recurse -Filter "thanhduc-2*.dump" | Sort-Object Name -Descending | Select-Object -First 1
  if (-not $f) { throw "Trong $SaoLuu không có file thanhduc-<ngày>.dump" }
  $dump = $f.FullName
} else {
  $dump = (Resolve-Path $SaoLuu).Path
}
$tep = Join-Path (Split-Path $dump -Parent) ((Split-Path $dump -Leaf) -replace "^thanhduc-", "thanhduc-files-" -replace "\.dump$", ".tar.gz")
if (-not (Test-Path $tep)) { $tep = $null }
Write-Host "Database: $dump ($([math]::Round((Get-Item $dump).Length / 1MB, 1)) MB)"
if ($tep) { Write-Host "Tệp đính kèm: $tep" } else { Write-Host "Không thấy file tệp đính kèm cùng ngày, chỉ khôi phục database." -ForegroundColor Yellow }

Chay @("up", "-d", "db") "Không bật được database."
Cho-Db
Chay @("cp", $dump, "db:/tmp/restore.dump") "Chép file sao lưu vào container lỗi."

if ($ChiThu) {
  Write-Host "== Khôi phục thử vào database tạm restore_check" -ForegroundColor Cyan
  Chay @("exec", "-T", "db", "dropdb", "-U", "thanhduc", "--if-exists", "restore_check") "Xóa database tạm lỗi."
  Chay @("exec", "-T", "db", "createdb", "-U", "thanhduc", "restore_check") "Tạo database tạm lỗi."
  & docker @compose exec -T db pg_restore -U thanhduc -d restore_check --no-owner --no-privileges /tmp/restore.dump
  $rc = $LASTEXITCODE
  Dem "restore_check"
  Chay @("exec", "-T", "db", "dropdb", "-U", "thanhduc", "restore_check") "Xóa database tạm lỗi."
  if ($tep) {
    Chay @("cp", $tep, "api:/tmp/restore-files.tar.gz") "Chép file tệp đính kèm vào container lỗi (app có đang chạy không?)."
    Write-Host "Số tệp đính kèm trong bản sao lưu:"
    Chay @("exec", "-T", "api", "sh", "-c", 'tar -tzf /tmp/restore-files.tar.gz | grep -vc /$; rm /tmp/restore-files.tar.gz') "File tệp đính kèm bị hỏng."
  }
  if ($rc -ne 0) { Write-Host "pg_restore có cảnh báo (mã $rc), xem các dòng phía trên." -ForegroundColor Yellow }
  Write-Host "Khôi phục thử OK: $dump. Dữ liệu thật không bị đụng tới." -ForegroundColor Green
  exit 0
}

if (-not $KhongHoi) {
  Write-Host "Lệnh này XÓA toàn bộ dữ liệu app đang có trên máy này và thay bằng bản sao lưu trên." -ForegroundColor Red
  if ((Read-Host "Gõ CO để tiếp tục") -ne "CO") { Write-Host "Đã hủy."; exit 1 }
  # Lưu bản hiện tại trước khi xóa, phòng chọn nhầm file.
  $truoc = Join-Path $env:USERPROFILE ("thanhduc-truoc-khoi-phuc-" + (Get-Date -Format "yyyy-MM-dd-HHmm") + ".dump")
  Chay @("exec", "-T", "db", "pg_dump", "-U", "thanhduc", "-d", "thanhduc", "--format=custom", "-f", "/tmp/truoc.dump") "Lưu bản hiện tại lỗi, dừng lại để không mất dữ liệu."
  Chay @("cp", "db:/tmp/truoc.dump", $truoc) "Chép bản hiện tại ra máy lỗi."
  Write-Host "Đã lưu dữ liệu hiện tại vào $truoc"
}

Write-Host "== Khôi phục database" -ForegroundColor Cyan
# App phải tắt để không ai ghi vào database trong lúc thay.
Thu-Docker ($compose + @("stop", "api")) | Out-Null
Chay @("exec", "-T", "db", "psql", "-U", "thanhduc", "-d", "postgres", "-c", "DROP DATABASE IF EXISTS thanhduc WITH (FORCE)") "Xóa database cũ lỗi."
Chay @("exec", "-T", "db", "createdb", "-U", "thanhduc", "thanhduc") "Tạo database lỗi."
& docker @compose exec -T db pg_restore -U thanhduc -d thanhduc --no-owner --no-privileges /tmp/restore.dump
if ($LASTEXITCODE -ne 0) { Write-Host "pg_restore có cảnh báo (mã $LASTEXITCODE). Xem số dòng bên dưới; nếu bằng 0 thì chụp màn hình gửi Claude." -ForegroundColor Yellow }
Dem "thanhduc"

Write-Host "== Bật app (tự nâng cấp cấu trúc database nếu bản sao lưu cũ hơn mã nguồn)" -ForegroundColor Cyan
Chay @("up", "-d") "Bật app lỗi."
$ok = $false
foreach ($n in 1..60) {
  if (Thu-Docker ($compose + @("exec", "-T", "api", "python", "-c", 'import urllib.request;urllib.request.urlopen(''http://localhost:8000/health'')'))) { $ok = $true; break }
  Start-Sleep -Seconds 3
}
if (-not $ok) { throw "App chưa lên sau 3 phút. Chạy: docker $($compose -join ' ') logs --tail 100 api" }

if ($tep) {
  Write-Host "== Khôi phục tệp đính kèm (ảnh, PDF)" -ForegroundColor Cyan
  Chay @("cp", $tep, "api:/tmp/restore-files.tar.gz") "Chép file tệp đính kèm vào container lỗi."
  Chay @("exec", "-T", "api", "sh", "-c", "tar -xzf /tmp/restore-files.tar.gz -C /app/data && rm /tmp/restore-files.tar.gz") "Giải nén tệp đính kèm lỗi."
}
Write-Host "Khôi phục xong từ $dump. Đăng nhập bằng tài khoản cũ như trước." -ForegroundColor Green

# Cài app Thành Đức lên máy Windows (máy mới, hoặc cài lại sau khi hỏng máy) bằng một lệnh.
# Cần: Docker Desktop đã cài và đang chạy. Xem HUONG-DAN-CAI-DAT.md cùng thư mục.
#   powershell -ExecutionPolicy Bypass -File deploy\windows\cai-dat.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\windows\cai-dat.ps1 -SaoLuu D:\thanhduc-backup\daily
# Nếu gói cài có thư mục sao-luu (tạo bởi tao-bo-cai.ps1) thì tự lấy dữ liệu từ đó.
param(
  [string]$SaoLuu = "",
  [string]$TunnelToken = "",
  [string]$Domain = "quanlythanhduc.com"
)
$ErrorActionPreference = "Stop"
$app = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$goi = Split-Path $app -Parent
Set-Location $app
$compose = @("compose", "-f", "deploy/docker-compose.yml", "-f", "deploy/docker-compose.tunnel.yml", "--env-file", "deploy/.env")

# Chạy lệnh docker mà không dừng script khi lệnh ghi ra stderr; trả về đúng/sai.
function Thu-Docker([string[]]$a) {
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  & docker @a *> $null
  $ok = ($LASTEXITCODE -eq 0)
  $ErrorActionPreference = $old
  return $ok
}
function Chuoi-Ngau-Nhien([int]$soByte) {
  $b = New-Object byte[] $soByte
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
  return -join ($b | ForEach-Object { $_.ToString("x2") })
}

Write-Host "== 1. Kiểm tra Docker" -ForegroundColor Cyan
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Chưa cài Docker Desktop. Tải ở https://www.docker.com/products/docker-desktop/ rồi chạy lại." }
if (-not (Thu-Docker @("info"))) { throw "Docker Desktop chưa chạy. Mở Docker Desktop, chờ biểu tượng cá voi đứng yên rồi chạy lại." }

Write-Host "== 2. File cấu hình deploy\.env" -ForegroundColor Cyan
$envFile = Join-Path $app "deploy\.env"
if (Test-Path $envFile) {
  Write-Host "Đã có deploy\.env, giữ nguyên."
} else {
  if (-not $TunnelToken) {
    Write-Host "Dán mã Cloudflare Tunnel (chuỗi dài sau --token, lấy ở Cloudflare Zero Trust > Networks > Tunnels > thanhduc-van-phong)."
    Write-Host "Để trống nếu chưa có, app vẫn chạy nhưng chưa ra Internet."
    $TunnelToken = (Read-Host "TUNNEL_TOKEN").Trim()
  }
  $utf8 = New-Object Text.UTF8Encoding($false)
  $txt = [IO.File]::ReadAllText((Join-Path $app "deploy\env.example"), $utf8)
  $txt = $txt -replace "(?m)^DOMAIN=.*$", "DOMAIN=$Domain"
  $txt = $txt -replace "(?m)^DB_PASSWORD=.*$", ("DB_PASSWORD=" + (Chuoi-Ngau-Nhien 24))
  $txt = $txt -replace "(?m)^SECRET_KEY=.*$", ("SECRET_KEY=" + (Chuoi-Ngau-Nhien 32))
  # Không có mã thì đặt tạm "chua-co" để lệnh compose không báo thiếu; cloudflared sẽ báo lỗi cho tới khi điền mã thật.
  if (-not $TunnelToken) { $TunnelToken = "chua-co" }
  $txt = $txt -replace "(?m)^TUNNEL_TOKEN=.*$", "TUNNEL_TOKEN=$TunnelToken"
  [IO.File]::WriteAllText($envFile, $txt, $utf8)
  Write-Host "Đã tạo deploy\.env với mật khẩu database và SECRET_KEY mới (ngẫu nhiên)."
}

Write-Host "== 3. Image Docker" -ForegroundColor Cyan
$build = @("--build")
$images = @(Get-ChildItem (Join-Path $goi "images") -Filter "*.tar" -ErrorAction SilentlyContinue)
if ($images.Count -gt 0) {
  foreach ($i in $images) {
    Write-Host "Nạp $($i.Name) (không cần tải từ Internet)..."
    & docker load -i $i.FullName
    if ($LASTEXITCODE -ne 0) { throw "Nạp image lỗi: $($i.FullName)" }
  }
  $build = @()
} else {
  Write-Host "Không có image kèm gói, sẽ tải và build (cần Internet, lần đầu mất 5–10 phút)."
}

if (-not $SaoLuu) {
  $tim = Get-ChildItem (Join-Path $goi "sao-luu") -Recurse -Filter "thanhduc-2*.dump" -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
  if ($tim) { $SaoLuu = $tim.FullName; Write-Host "Tìm thấy bản sao lưu trong gói: $SaoLuu" }
}

if ($SaoLuu) {
  Write-Host "== 4. Khôi phục dữ liệu từ $SaoLuu" -ForegroundColor Cyan
  & docker @compose up -d @build db
  if ($LASTEXITCODE -ne 0) { throw "Không bật được database." }
  & (Join-Path $PSScriptRoot "khoi-phuc.ps1") -SaoLuu $SaoLuu -KhongHoi
} else {
  Write-Host "== 4. Bật app với database trống" -ForegroundColor Cyan
  & docker @compose up -d @build
  if ($LASTEXITCODE -ne 0) { throw "Bật app lỗi. Chạy: docker compose ... logs --tail 100 api, chụp gửi Claude." }
  Write-Host "Chờ app khởi động..."
  $ok = $false
  foreach ($n in 1..60) {
    if (Thu-Docker ($compose + @("exec", "-T", "api", "python", "-c", 'import urllib.request;urllib.request.urlopen(''http://localhost:8000/health'')'))) { $ok = $true; break }
    Start-Sleep -Seconds 3
  }
  if (-not $ok) { throw "App chưa lên sau 3 phút. Chạy: docker $($compose -join ' ') logs --tail 100 api" }
  Write-Host "Tạo tài khoản Giám đốc đầu tiên." -ForegroundColor Cyan
  $u = Read-Host "Tên đăng nhập (Enter = tuan)"
  if (-not $u) { $u = "tuan" }
  $ten = Read-Host "Họ tên (Enter = Bùi Minh Tuấn)"
  if (-not $ten) { $ten = "Bùi Minh Tuấn" }
  & docker @compose exec api python -m scripts.create_admin --username $u --name $ten --roles admin,director
}

Write-Host ""
Write-Host "XONG. Các phần đang chạy:" -ForegroundColor Green
& docker @compose ps
Write-Host "Mở https://$Domain trên điện thoại (tắt wifi) để thử. Tunnel trong Cloudflare phải hiện HEALTHY."
Write-Host "Nhớ đặt lịch sao lưu mỗi đêm (mục 5 trong HUONG-DAN-CAI-DAT.md)."

# Đóng gói bộ cài hoàn chỉnh của app Thành Đức trên máy văn phòng, để cất giữ hoặc cài sang máy khác.
#   powershell -ExecutionPolicy Bypass -File deploy\windows\tao-bo-cai.ps1
#   ... -KemImage      thêm image Docker để cài lại không cần Internet (gói nặng thêm khoảng 0,5–1 GB)
#   ... -KhongDuLieu   chỉ mã nguồn, không kèm bản sao lưu dữ liệu
# Gói KHÔNG chứa deploy\.env (mật khẩu, mã tunnel). Cài lại sẽ tạo mật khẩu mới; mã tunnel lấy lại trong Cloudflare.
param(
  [string]$Ra = "D:\thanhduc-bo-cai",
  [switch]$KemImage,
  [switch]$KhongDuLieu
)
$ErrorActionPreference = "Stop"
$app = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $app
$compose = @("compose", "-f", "deploy/docker-compose.yml", "-f", "deploy/docker-compose.tunnel.yml", "--env-file", "deploy/.env")
$ten = "thanhduc-bo-cai-" + (Get-Date -Format "yyyy-MM-dd-HHmm")
$dir = Join-Path $Ra $ten
New-Item -ItemType Directory -Force $dir | Out-Null

Write-Host "== 1. Mã nguồn" -ForegroundColor Cyan
robocopy $app (Join-Path $dir "THANHDUC-APP") /E /XD .git __pycache__ .pytest_cache .venv node_modules /XF .env *.dump *.tar.gz /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Chép mã nguồn lỗi (robocopy mã $LASTEXITCODE)." }
$global:LASTEXITCODE = 0
$phienBan = "Đóng gói lúc " + (Get-Date -Format "dd/MM/yyyy HH:mm") + " trên máy " + $env:COMPUTERNAME
if (Get-Command git -ErrorAction SilentlyContinue) {
  $phienBan += "`r`nMã nguồn: " + (& git -C $app log -1 '--format=%h %ci %s')
}
Set-Content (Join-Path $dir "PHIEN-BAN.txt") $phienBan -Encoding UTF8
Copy-Item (Join-Path $PSScriptRoot "HUONG-DAN-CAI-DAT.md") $dir

if (-not $KhongDuLieu) {
  Write-Host "== 2. Sao lưu dữ liệu mới nhất" -ForegroundColor Cyan
  & (Join-Path $app "scripts\backup.ps1") -BackupDir (Join-Path $dir "sao-luu")
  Remove-Item (Join-Path $dir "sao-luu\backup.log") -ErrorAction SilentlyContinue
}

if ($KemImage) {
  Write-Host "== 3. Image Docker" -ForegroundColor Cyan
  $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  $ds = @(& docker @compose config --images 2>$null) | Where-Object { $_ } | Sort-Object -Unique
  $co = @($ds | Where-Object { & docker image inspect $_ *> $null; $LASTEXITCODE -eq 0 })
  $ErrorActionPreference = $old
  if ($co.Count -eq 0) { throw "Không tìm thấy image nào. App có đang chạy trên máy này không?" }
  New-Item -ItemType Directory -Force (Join-Path $dir "images") | Out-Null
  Write-Host ("Lưu: " + ($co -join ", "))
  & docker save -o (Join-Path $dir "images\thanhduc-images.tar") @co
  if ($LASTEXITCODE -ne 0) { throw "Lưu image lỗi." }
}

Write-Host "== Nén thành file zip" -ForegroundColor Cyan
$zip = "$dir.zip"
Compress-Archive -Path (Join-Path $dir "*") -DestinationPath $zip -Force
Remove-Item $dir -Recurse -Force
Write-Host ("XONG: $zip (" + [math]::Round((Get-Item $zip).Length / 1MB, 1) + " MB)") -ForegroundColor Green
Write-Host "Chép file này lên Google Drive hoặc USB, cất ngoài máy văn phòng. Gói có dữ liệu khách nên không gửi cho người ngoài."

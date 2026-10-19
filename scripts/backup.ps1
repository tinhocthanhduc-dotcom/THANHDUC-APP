# Sao lưu database và tệp đính kèm trên máy Windows (bản của backup.sh cho máy văn phòng).
# Đặt lịch bằng Task Scheduler mỗi đêm, xem huong-dan "may-van-phong-lam-may-chu".
# BackupDir nên là thư mục Google Drive đồng bộ, để bản sao nằm ngoài máy này.
param([string]$BackupDir = "C:\thanhduc-backup")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$compose = @("compose", "-f", "deploy/docker-compose.yml", "-f", "deploy/docker-compose.tunnel.yml", "--env-file", "deploy/.env")
New-Item -ItemType Directory -Force "$BackupDir\daily", "$BackupDir\monthly" | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$dump = "$BackupDir\daily\thanhduc-$stamp.dump"
$files = "$BackupDir\daily\thanhduc-files-$stamp.tar.gz"
# Ghi ra file trong container rồi chép ra, vì PowerShell làm hỏng dữ liệu nhị phân khi chuyển hướng bằng ">".
& docker @compose exec -T db pg_dump -U thanhduc -d thanhduc --format=custom -f /tmp/backup.dump
if ($LASTEXITCODE -ne 0) { throw "pg_dump lỗi" }
& docker @compose cp db:/tmp/backup.dump $dump
& docker @compose exec -T api tar -czf /tmp/backup-files.tar.gz -C /app/data files
if ($LASTEXITCODE -ne 0) { throw "tar tệp đính kèm lỗi" }
& docker @compose cp api:/tmp/backup-files.tar.gz $files
if ((Get-Date).Day -eq 1) { Copy-Item $dump, $files "$BackupDir\monthly\" }
# Giữ 30 bản hằng ngày và 12 bản đầu tháng.
foreach ($kind in "thanhduc-2*.dump", "thanhduc-files-*.tar.gz") {
  Get-ChildItem "$BackupDir\daily" -Filter $kind | Sort-Object Name -Descending | Select-Object -Skip 30 | Remove-Item
  Get-ChildItem "$BackupDir\monthly" -Filter $kind | Sort-Object Name -Descending | Select-Object -Skip 12 | Remove-Item
}
"$(Get-Date -Format s) OK $dump $([math]::Round((Get-Item $dump).Length / 1MB, 1))MB $files" | Add-Content "$BackupDir\backup.log"

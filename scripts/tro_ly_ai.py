#!/usr/bin/env python3
"""Công cụ cho Claude trên máy chủ Z2 gọi cửa /ai của app (Trợ lý AI, TUẤN 01/11/2026).

Chỉ dùng thư viện có sẵn của Python, chạy thẳng trên máy chủ (không cần Docker):
    python3 ~/THANHDUC-APP/scripts/tro_ly_ai.py xem
Khóa đọc từ ~/.thanhduc-ai-key (chmod 600) hoặc biến TD_AI_KEY. App ở http://127.0.0.1:8010 (đổi bằng TD_AI_URL).
Không đọc database, không đọc deploy/.env. Mọi lệnh ghi đều được app ghi nhật ký; giao thợ/đổi hẹn chỉ là đề xuất.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _key():
    raw = os.environ.get('TD_AI_KEY', '').strip()
    if not raw:
        path = Path(os.environ.get('TD_AI_KEY_FILE', Path.home() / '.thanhduc-ai-key'))
        try:
            raw = path.read_text().strip()
        except OSError:
            sys.exit(f'Chưa có khóa: tạo khóa ở màn Trợ lý AI rồi lưu vào {path} (chmod 600).')
    return raw


def call(method, path, body=None):
    url = os.environ.get('TD_AI_URL', 'http://127.0.0.1:8010').rstrip('/') + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + _key(), 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b'null')
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read())
            message = detail.get('message') or detail.get('detail') or detail
        except ValueError:
            message = exc.reason
        sys.exit(f'Lỗi {exc.code}: {message}')
    except urllib.error.URLError as exc:
        sys.exit(f'Không kết nối được app ở {url}: {exc.reason}')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Trợ lý AI Thành Đức: xem việc, nhắc việc, đề xuất cho giám đốc duyệt.')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('xem', help='Ảnh chụp việc của mọi người, phiếu dịch vụ đang mở, chấm công hôm nay')
    p = sub.add_parser('nhac', help='Nhắc việc một nhân viên (tối đa 3 lần/người/ngày, hiện ở chuông + điện thoại)')
    p.add_argument('nguoi_id', type=int); p.add_argument('noi_dung'); p.add_argument('--phieu')
    p = sub.add_parser('ghi-chu', help='Ghi chú cho giám đốc, ví dụ việc sắp trễ hạn')
    p.add_argument('tieu_de'); p.add_argument('noi_dung', nargs='?', default=''); p.add_argument('--phieu')
    p = sub.add_parser('bao-cao', help='Gửi báo cáo cho giám đốc (nội dung từ --tep hoặc stdin)')
    p.add_argument('tieu_de'); p.add_argument('--tep')
    p = sub.add_parser('de-xuat-giao', help='Đề xuất giao/đổi thợ cho phiếu; chờ giám đốc duyệt')
    p.add_argument('phieu'); p.add_argument('tho_id', type=int); p.add_argument('ly_do')
    p = sub.add_parser('de-xuat-hen', help='Đề xuất dời giờ hẹn, ví dụ 2026-11-02T14:00; chờ giám đốc duyệt')
    p.add_argument('phieu'); p.add_argument('hen_luc'); p.add_argument('ly_do'); p.add_argument('--khung', choices=('SANG', 'CHIEU', 'GIO'), default='GIO')
    p = sub.add_parser('de-xuat-nhap', help='Đề xuất nhập thêm hàng; duyệt xong chỉ báo thủ kho, không tạo đơn')
    p.add_argument('ma_hang'); p.add_argument('so_luong', type=int); p.add_argument('ly_do')
    sub.add_parser('ket-qua', help='Các đề xuất gần đây đã được duyệt / từ chối')
    a = ap.parse_args(argv)
    if a.cmd == 'xem':
        out = call('GET', '/ai/tong-quan')
    elif a.cmd == 'nhac':
        out = call('POST', '/ai/nhac-viec', dict(nguoi_id=a.nguoi_id, noi_dung=a.noi_dung, phieu=a.phieu))
    elif a.cmd == 'ghi-chu':
        out = call('POST', '/ai/ghi-chu', dict(loai='GHI_CHU', tieu_de=a.tieu_de, noi_dung=a.noi_dung, phieu=a.phieu))
    elif a.cmd == 'bao-cao':
        text = Path(a.tep).read_text() if a.tep else sys.stdin.read()
        out = call('POST', '/ai/ghi-chu', dict(loai='BAO_CAO', tieu_de=a.tieu_de, noi_dung=text))
    elif a.cmd == 'de-xuat-giao':
        out = call('POST', '/ai/de-xuat', dict(loai='GIAO_VIEC', phieu=a.phieu, tho_id=a.tho_id, ly_do=a.ly_do))
    elif a.cmd == 'de-xuat-hen':
        out = call('POST', '/ai/de-xuat', dict(loai='DOI_HEN', phieu=a.phieu, hen_luc=a.hen_luc, khung_hen=a.khung, ly_do=a.ly_do))
    elif a.cmd == 'de-xuat-nhap':
        out = call('POST', '/ai/de-xuat', dict(loai='DE_XUAT_NHAP', ma_hang=a.ma_hang, so_luong=a.so_luong, ly_do=a.ly_do))
    else:
        out = call('GET', '/ai/de-xuat')
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()

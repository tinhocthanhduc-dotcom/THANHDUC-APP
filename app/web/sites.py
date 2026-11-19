"""Tên miền con dùng chung app (TUẤN duyệt 19/11/2026): care.quanlythanhduc.com cùng dữ liệu, cùng quyền,
chỉ khác logo, màu và menu. Thêm site mới = thêm một mục vào SITES (vd. zalo).

Nhận site theo nhãn đầu của Host (care.quanlythanhduc.com, care.localhost khi chạy thử).
Trang ngoài phạm vi site mở bằng trình duyệt thì chuyển về tên miền chính cùng đường dẫn.
Cookie đăng nhập giữ theo từng tên miền (không đặt Domain chung) vì marketing.* là app khác.
"""
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse

SITES = {
    'care': dict(
        name='Thành Đức Care', sub='Sửa chữa · Bảo hành', mark='TĐ', theme='#0f766e',
        # Menu theo thứ tự mẫu duyệt; (href, nhãn, href thay thế khi không mở được href chính).
        menu=(('/dich-vu', 'Phiếu sửa chữa', ''),
              ('/kiem-tra-may', 'Máy ở xưởng', ''),
              ('/thiet-bi', 'Quản lý thiết bị', ''),
              ('/may-da-ban', 'Máy đã bán', ''),
              ('/bao-hanh-sua-chua', 'Bảo hành sửa chữa', ''),
              ('/bang-gia?tab=goi', 'Bảng giá sửa', '')),
        # Trang mở được trên site (ngoài menu: trang con của phiếu, báo giá sửa, máy tại quầy).
        allow=('/dich-vu', '/xem-dich-vu', '/kiem-tra-may', '/thiet-bi', '/may-da-ban', '/bao-hanh-sua-chua',
               '/bang-gia', '/tra-gia', '/bao-gia-sua', '/bao-gia-gop', '/hoi-gia', '/quay-sua', '/may-dang-giu'),
    ),
    # zalo.quanlythanhduc.com (TUẤN duyệt mẫu 19/11/2026): tin nhắn khách qua Zalo Bot, trả lời, soạn sẵn, tự trả lời.
    'zalo': dict(
        name='Thành Đức Zalo', sub='Tin nhắn khách', mark='ZL', theme='#0b62d6',
        menu=(('/zalo/hop-thu', 'Tất cả tin', ''),
              ('/tin-zalo', 'Cần trả lời', ''),
              ('/tin-gui-khach', 'Chờ gửi khách', ''),
              ('/don-nhap-zalo', 'Đơn & phiếu nháp', '/phieu-nhap-zalo'),
              ('/zalo/tu-tra-loi', 'Trả lời tự động', ''),
              ('/zalo/bao-cao-sang', 'Tin gửi nội bộ', '')),
        allow=('/zalo', '/hop-zalo', '/tin-zalo', '/tin-gui-khach', '/don-nhap-zalo', '/phieu-nhap-zalo'),
    ),
}

# Mọi site đều mở được: đăng nhập, tài khoản, tệp tĩnh, link báo giá cho khách, hộp xem nhanh, QR.
COMMON = ('/', '/dang-nhap', '/dang-xuat', '/doi-mat-khau', '/ho-so-cua-toi', '/ca-nhan', '/static', '/sw.js',
          '/bg', '/bgg', '/bgh', '/hg', '/xem-nhanh', '/qr', '/tao-nhanh', '/api')


def key_of(host: str):
    """'care.quanlythanhduc.com:443' -> 'care'; tên miền chính hoặc không khớp -> None."""
    host = (host or '').split(':')[0].strip().lower().rstrip('.')
    parts = host.split('.')
    return parts[0] if len(parts) >= 2 and parts[0] in SITES else None


def site_for(request: Request):
    key = key_of(request.headers.get('host', ''))
    return dict(SITES[key], key=key) if key else None


def _under(path: str, prefixes) -> bool:
    return any(path == p or path.startswith(p.rstrip('/') + '/') for p in prefixes)


def allows(site, path: str) -> bool:
    return site is None or path == '/' or _under(path, site['allow']) or _under(path, COMMON[1:])


def main_url(request: Request, path: str) -> str:
    """Cùng đường dẫn trên tên miền chính (bỏ nhãn site đầu Host), giữ http/https như khách đang dùng."""
    host = request.headers.get('host', '')
    main = host.split('.', 1)[1] if '.' in host else host
    proto = request.headers.get('x-forwarded-proto', '').split(',')[0].strip().lower() or request.url.scheme
    query = urlsplit(str(request.url)).query
    return f'{proto}://{main}{path}' + ('?' + query if query else '')


def _navigation(request: Request) -> bool:
    """Chỉ chuyển trang khi trình duyệt mở trang (không chuyển fetch/ô tìm/POST để không vỡ thao tác trong trang)."""
    if request.method != 'GET':
        return False
    dest = request.headers.get('sec-fetch-dest')
    if dest is not None:
        return dest == 'document'
    return 'text/html' in request.headers.get('accept', '')


def home_url(user, site):
    """Trang đầu trên site: mục menu đầu tiên người dùng mở được; không có thì None (dùng trang đầu thường)."""
    from . import nav
    for href, _, alt in site['menu']:
        for target in (href, alt):
            if target and nav.allowed(user, target.split('?')[0]):
                return target
    return None


def menu_items(user, site):
    from . import nav
    out = []
    for href, label, alt in site['menu']:
        target = href if nav.allowed(user, href.split('?')[0]) else (alt if alt and nav.allowed(user, alt.split('?')[0]) else '')
        if target:
            out.append((target, label))
    return out


async def middleware(request: Request, call_next):
    site = site_for(request)
    request.state.site = site
    if site and _navigation(request) and not allows(site, request.url.path):
        return RedirectResponse(main_url(request, request.url.path), status_code=303)
    return await call_next(request)

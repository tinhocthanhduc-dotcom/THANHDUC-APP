"""U1: cấu hình duy nhất cho menu và quyền vào các đường dẫn web.

Prefix khớp ranh giới đường dẫn, ưu tiên mục cụ thể nhất; điều kiện query
phân biệt Sắp hết với Tồn kho. Mục chưa khai báo bị chặn mặc định.
"""
from pydantic import TypeAdapter, ValidationError

from ..errors import BusinessError
from ..security import has_role

# Thứ tự menu: Của tôi → Nghiệp vụ (theo dòng việc hằng ngày) → Quản lý (xem số, chốt tháng) → Thiết lập (ít dùng).
SECTIONS = ('Của tôi', 'Nghiệp vụ', 'Quản lý', 'Thiết lập')
GROUPS = (
    {'label': '', 'section': 'Của tôi', 'items': (
        {'label': 'Tổng quan', 'href': '/tong-quan', 'prefixes': ('/tong-quan',), 'roles': ('finance',)},
        {'label': 'Việc của tôi', 'href': '/viec-cua-toi', 'prefixes': ('/viec-cua-toi', '/giu-may', '/giao-don', '/tho-tra-hang'), 'roles': ('technician', 'sales', 'warehouse', 'dispatcher', 'reception', 'finance')},
        # Màn giao hàng mở từ Việc của tôi hoặc từ đơn: thủ kho và kinh doanh (Đợt 1b), menu sáng Việc của tôi (không sáng Danh mục › Kho).
        {'label': 'Giao hàng', 'href': '/kho/giao', 'prefixes': ('/kho/giao',), 'roles': ('warehouse', 'sales'), 'hidden': True, 'highlight': '/viec-cua-toi'},
        {'label': 'Hôm nay', 'href': '/tho', 'prefixes': ('/tho',), 'roles': ('technician',), 'technician_page': True},
        {'label': 'Tiền tôi giữ', 'href': '/tien-toi-giu', 'prefixes': ('/tien-toi-giu',),
         'roles': ('technician','dispatcher','reception','sales','warehouse','finance','director','admin'), 'menu_roles': ('technician',)},
        {'label': 'Hàng tôi giữ', 'href': '/hang-toi-giu', 'prefixes': ('/hang-toi-giu',), 'roles': ('technician','warehouse'), 'menu_roles': ('technician',)},
        {'label': 'Chấm công', 'href': '/cham-cong', 'prefixes': ('/cham-cong',), 'roles': ('sales','reception','dispatcher','warehouse','technician','finance','director','admin')},
        {'label': 'KPI của tôi', 'href': '/kpi', 'prefixes': ('/kpi',), 'roles': ('technician','sales','dispatcher','warehouse','finance')},
        {'label': 'Hoa hồng của tôi', 'href': '/hoa-hong', 'prefixes': ('/hoa-hong',), 'roles': ('sales','technician')},
    )},
    {'label': 'Khách hàng', 'section': 'Nghiệp vụ', 'items': (
        {'label': 'Khách hàng', 'href': '/khach-hang', 'prefixes': ('/khach-hang',), 'roles': ('dispatcher', 'reception', 'sales', 'finance')},
    )},
    {'label': 'Bán hàng', 'section': 'Nghiệp vụ', 'items': (
        {'label': 'Báo giá', 'href': '/bao-gia', 'prefixes': ('/bao-gia',), 'roles': ('sales', 'finance')},
        {'label': 'Đơn bán', 'href': '/don-ban', 'prefixes': ('/don-ban','/tra-hang'), 'roles': ('sales', 'warehouse', 'finance')},
    )},
    {'label': 'Dịch vụ', 'section': 'Nghiệp vụ', 'items': (
        {'label': 'Điều phối', 'href': '/dieu-phoi', 'prefixes': ('/dieu-phoi',), 'roles': ('dispatcher',)},
        {'label': 'Phiếu dịch vụ', 'href': '/dich-vu', 'prefixes': ('/dich-vu',), 'roles': ('dispatcher', 'reception', 'technician')},
        {'label': 'Máy đang giữ', 'href': '/may-dang-giu', 'prefixes': ('/may-dang-giu',), 'roles': ('dispatcher','warehouse')},
        {'label': 'Thiết bị của khách', 'href': '/thiet-bi', 'prefixes': ('/thiet-bi',), 'roles': ('dispatcher', 'reception', 'technician', 'sales')},
    )},
    {'label': 'Kho', 'section': 'Nghiệp vụ', 'items': (
        {'label': 'Tồn kho', 'href': '/ton-kho', 'prefixes': ('/ton-kho', '/the-kho'), 'roles': ('sales', 'warehouse', 'finance')},
        {'label': 'Hàng sắp hết', 'href': '/ton-kho?canh-bao=1', 'prefixes': ('/ton-kho',), 'roles': ('warehouse',), 'query': {'canh-bao': True}},
        {'label': 'Phiếu nhập xuất kho', 'href': '/phieu-kho', 'prefixes': ('/phieu-kho',), 'roles': ('warehouse', 'finance')},
        {'label': 'Yêu cầu linh kiện', 'href': '/yeu-cau-linh-kien', 'prefixes': ('/yeu-cau-linh-kien',), 'roles': ('warehouse',)},
        {'label':'Hàng chờ xử lý','href':'/cho-xu-ly','prefixes':('/cho-xu-ly',),'roles':('warehouse','finance','dispatcher')},
        {'label': 'Linh kiện thu hồi', 'href': '/linh-kien-thu-hoi', 'prefixes': ('/linh-kien-thu-hoi',), 'roles': ('technician','warehouse','dispatcher','finance')},
        {'label': 'Hàng hóa', 'href': '/hang-hoa', 'prefixes': ('/hang-hoa',), 'roles': ('sales', 'warehouse', 'finance')},
    )},
    {'label': 'Mua hàng', 'section': 'Nghiệp vụ', 'items': (
        {'label': 'Đơn mua', 'href': '/don-mua', 'prefixes': ('/don-mua',), 'roles': ('warehouse', 'finance')},
        {'label': 'Nhà cung cấp', 'href': '/nha-cung-cap', 'prefixes': ('/nha-cung-cap',), 'roles': ('warehouse', 'finance')},
    )},
    {'label': 'Tài chính', 'section': 'Nghiệp vụ', 'items': (
        {'label': 'Báo thu', 'href': '/bao-thu', 'prefixes': ('/bao-thu', '/hoan-tien', '/dich-vu/can-thu'), 'roles': ('finance',)},
        {'label': 'Thu chi · Sổ quỹ', 'href': '/thu-chi', 'prefixes': ('/thu-chi', '/so-quy'), 'roles': ('finance',)},
        {'label': 'Công nợ nhà cung cấp', 'href': '/cong-no-ncc', 'prefixes': ('/cong-no-ncc',), 'roles': ('finance',)},
        {'label': 'Điều chỉnh sổ', 'href': '/dieu-chinh', 'prefixes': ('/dieu-chinh',), 'roles': ('finance',)},
        {'label': 'Chốt sổ', 'href': '/chot-so', 'prefixes': ('/chot-so',), 'roles': ('finance',)},
    )},
    # Nhóm Báo cáo đã nói "báo cáo", tên mục chỉ còn phần riêng.
    {'label': 'Báo cáo', 'section': 'Quản lý', 'items': (
        {'label': 'Bán hàng', 'solo': 'Báo cáo bán hàng', 'href': '/bao-cao/ban-hang', 'prefixes': ('/bao-cao/ban-hang',), 'roles': ('sales', 'finance')},
        {'label': 'Mua hàng', 'solo': 'Báo cáo mua hàng', 'href': '/bao-cao-mua-hang', 'prefixes': ('/bao-cao-mua-hang',), 'roles': ('warehouse', 'finance')},
        {'label': 'Hàng chờ xử lý', 'solo': 'Báo cáo hàng chờ xử lý', 'href': '/bao-cao/cho-xu-ly', 'prefixes': ('/bao-cao/cho-xu-ly',), 'roles': ('finance',)},
        {'label': 'Tài chính', 'solo': 'Báo cáo tài chính', 'href': '/bao-cao/tai-chinh', 'prefixes': ('/bao-cao/tai-chinh',), 'roles': ('finance',)},
    )},
    {'label': 'Danh mục', 'section': 'Thiết lập', 'items': (
        {'label': 'Nhóm hàng', 'href': '/nhom-hang', 'prefixes': ('/nhom-hang',), 'roles': ('warehouse',)},
        {'label': 'Danh sách kho', 'href': '/kho', 'prefixes': ('/kho',), 'roles': ('warehouse',)},
        {'label': 'Tồn đầu kỳ', 'href': '/ton-dau-ky', 'prefixes': ('/ton-dau-ky',), 'roles': ('warehouse',)},
        {'label': 'Thiết lập tài chính', 'href': '/ghep-tai-chinh', 'prefixes': ('/quy', '/danh-muc-thu-chi', '/ghep-tai-chinh'), 'roles': ('finance',)},
    )},
)
# Tài khoản có phạm vi riêng: quyền giám đốc ở menu nghiệp vụ không bao gồm quản trị.
ACCOUNT_GROUPS = (
    # Xếp theo vòng tháng: duyệt công → bảng công → KPI → hoa hồng → lương → chi lương.
    {'label': 'Nhân sự & lương', 'section': 'Quản lý', 'items': (
        {'label': 'Duyệt chấm công', 'href': '/cham-cong/duyet', 'prefixes': ('/cham-cong/duyet',), 'roles': ('director',)},
        {'label': 'Bảng công', 'href': '/cham-cong/bang', 'prefixes': ('/cham-cong/bang',), 'roles': ('finance',)},
        {'label': 'KPI tổng', 'href': '/kpi/tong', 'prefixes': ('/kpi/tong',), 'roles': ('director',)},
        {'label': 'Hoa hồng tháng', 'href': '/hoa-hong/thang', 'prefixes': ('/hoa-hong/thang',), 'roles': ('finance',)},
        {'label': 'Bảng lương', 'href': '/luong', 'prefixes': ('/luong',), 'roles': ('director',), 'payroll_page': True},
        {'label': 'Chi lương', 'href': '/luong/chi', 'prefixes': ('/luong/chi',), 'roles': ('finance',), 'payroll_page': True},
    )},
    {'label': 'Quản trị', 'section': 'Thiết lập', 'items': (
        {'label': 'Hồ sơ nhân viên', 'href': '/nhan-su', 'prefixes': ('/nhan-su',), 'roles': ('admin','director')},
        {'label': 'Mục tiêu KPI', 'href': '/kpi/muc-tieu', 'prefixes': ('/kpi/muc-tieu',), 'roles': ('director',)},
        {'label': 'Quy tắc hoa hồng', 'href': '/hoa-hong/quy-tac', 'prefixes': ('/hoa-hong/quy-tac',), 'roles': ('director',)},
        {'label': 'Ngày lễ', 'href': '/cham-cong/ngay-le', 'prefixes': ('/cham-cong/ngay-le',), 'roles': ('director',)},
        {'label': 'Tài khoản nhân viên', 'href': '/tai-khoan', 'prefixes': ('/tai-khoan',), 'roles': ('admin',)},
        {'label': 'Quy chuẩn giao diện', 'href': '/ui/quy-chuan', 'prefixes': ('/ui/quy-chuan',), 'roles': ('director',)},
        {'label': 'Phiếu lương', 'href': '/phieu-luong', 'prefixes': ('/phieu-luong',), 'roles': ('sales','reception','dispatcher','warehouse','technician','finance','director'), 'account_page': True, 'payroll_page': True},
        {'label': 'Tạm ứng', 'href': '/tam-ung', 'prefixes': ('/tam-ung',), 'roles': ('sales','reception','dispatcher','warehouse','technician','finance','director'), 'account_page': True, 'payroll_page': True},
        {'label': 'Cá nhân', 'href': '/ca-nhan', 'prefixes': ('/ca-nhan',), 'roles': ('sales','reception','dispatcher','warehouse','technician','finance','director','admin'), 'account_page': True},
        {'label': 'Hồ sơ của tôi', 'href': '/ho-so-cua-toi', 'prefixes': ('/ho-so-cua-toi',), 'roles': ('sales','reception','dispatcher','warehouse','technician','finance','director','admin'), 'account_page': True},
        {'label': 'Đổi mật khẩu', 'href': '/doi-mat-khau', 'prefixes': ('/doi-mat-khau',), 'roles': ('sales','reception','dispatcher','warehouse','technician','finance','director','admin'), 'account_page': True},
    )},
)
_BOOL_QUERY = TypeAdapter(bool)
PUBLIC_PATHS = frozenset(('/', '/dang-nhap', '/dang-xuat'))
ROLE_LABELS = {'director': 'Giám đốc', 'finance': 'Kế toán', 'dispatcher': 'Điều phối',
               'reception': 'Lễ tân', 'technician': 'Kỹ thuật', 'sales': 'Kinh doanh',
               'warehouse': 'Thủ kho', 'admin': 'Quản trị'}
HOME_PAGES = (('finance', '/tong-quan'), ('dispatcher', '/dieu-phoi'), ('reception', '/dich-vu'),
              ('technician', '/viec-cua-toi'), ('sales', '/viec-cua-toi'), ('warehouse', '/viec-cua-toi'))


def items():
    return tuple(item for group in GROUPS for item in group['items'])


def matching(path, query=None):
    """Đường dẫn cụ thể thắng prefix cha, không dùng phép hợp quyền của cha."""
    query = query or {}
    matches = []
    for item in items() + tuple(item for group in ACCOUNT_GROUPS for item in group['items']):
        for prefix in item['prefixes']:
            base = prefix
            if not (path == base or path.startswith(base + '/') or path == base + '.csv'):
                continue
            conditions = tuple(item.get('query', {}).items())
            def same(key, expected):
                value = str(query.get(key, ''))
                if expected is True:
                    # Dùng đúng parser của FastAPI, gồm cả t/y và chữ hoa.
                    try:
                        return _BOOL_QUERY.validate_python(value)
                    except ValidationError:
                        return False
                return value == expected
            if all(same(key, expected) for key, expected in conditions):
                matches.append(((len(base), len(conditions)), item))
    return max(matches, key=lambda pair: pair[0])[1] if matches else None


# Khuôn 8: trang Cá nhân và menu tài khoản màn thợ dùng chung danh sách việc riêng của mỗi người.
PERSONAL = (('/cham-cong','Chấm công','Vào ca, ra ca, xin nghỉ hoặc bổ sung công.'),
            ('/phieu-luong','Phiếu lương','Lương từng tháng đã duyệt.'),
            ('/tam-ung','Tạm ứng','Xin tạm ứng và xem khoản đã chi.'),
            ('/kpi','KPI của tôi','Chỉ số tháng này, tạm tính.'),
            ('/hoa-hong','Hoa hồng của tôi','Hoa hồng theo quy tắc của vị trí.'))


def allowed(user, path, query=None):
    item = matching(path, query)
    if item and item.get('payroll_page') and 'admin' in user.roles and 'director' not in user.roles:return False
    return item is not None and (not item.get('technician_page') or 'technician' in user.roles) and has_role(user, *item['roles'])


def check_access(user, path, query=None):
    if not allowed(user, path, query):
        raise BusinessError('FORBIDDEN', 'Bạn không có quyền vào trang này', status=403)


def pure_technician(user):
    return set(user.roles)=={'technician'}


def home_url(user):
    if pure_technician(user): return '/tho'
    return next((href for role, href in HOME_PAGES if has_role(user, role)), '/viec-cua-toi')


def work_url(user):
    """Đích danh sách theo vai trò cho nơi còn dùng helper; mặc định an toàn."""
    return next((href for role, href in (('technician', '/dich-vu'), ('sales', '/don-ban'),
                                       ('warehouse', '/phieu-kho')) if has_role(user, role)), '/viec-cua-toi')


_ICON = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>'
# Biểu tượng tab dưới của màn thợ (SVG nét, không emoji).
TAB_ICONS = {
    '/tho': _ICON.format('<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"></path>'),
    '/hang-toi-giu': _ICON.format('<path d="M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10"></path>'),
    '/tien-toi-giu': _ICON.format('<path d="M3 7h18v12H3zM3 11h18M16 15h2"></path>'),
    '/thiet-bi': _ICON.format('<rect x="5" y="3" width="14" height="7" rx="1"></rect><path d="M3 10h18v8h-3M6 18H3v-8M7 14h10v7H7z"></path>'),
}


_NAV_ICON = '<svg class="nav-ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="{}"></path></svg>'
# Biểu tượng thanh menu bên trái: theo nhóm, và theo trang cho nhóm "Của tôi".
NAV_ICONS = {k: _NAV_ICON.format(v) for k, v in {
    'Bán hàng': 'M3 4h2l2.4 11h11l2-8H6.2M10 20h.01M17 20h.01',
    'Dịch vụ': 'M14.7 6.3a4 4 0 00-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 005.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z',
    'Kho': 'M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10',
    'Mua hàng': 'M6 2l-3 4v14h18V6l-3-4zM3 6h18M16 10a4 4 0 01-8 0',
    'Tài chính': 'M3 6h18v12H3zM3 10h18M7 15h3',
    'Báo cáo': 'M4 20V10M10 20V4M16 20v-7M22 20H2',
    'Danh mục': 'M4 5h16v14H4zM8 9h8M8 13h8M8 17h5',
    'Khách hàng': 'M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8z',
    'Nhân sự & lương': 'M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM23 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8',
    'Quản trị': 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4',
    '/viec-cua-toi': 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
    '/tong-quan': 'M3 12l9-8 9 8M5 10v10h14V10',
    '/cham-cong': 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2',
    '/kpi': 'M4 20V10M10 20V4M16 20v-7',
    '/hoa-hong': 'M12 2v20M17 6H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6',
    '/tho': 'M3 5h18v15H3zM3 10h18M8 3v4M16 3v4',
}.items()}
# Thanh dưới trên điện thoại: tối đa 3 lối tắt theo thứ tự này, ô thứ tư là Menu.
PHONE_TABS = (('/viec-cua-toi', 'Việc'), ('/tong-quan', 'Tổng quan'), ('/dieu-phoi', 'Điều phối'), ('/dich-vu', 'Dịch vụ'),
              ('/don-ban', 'Đơn bán'), ('/ton-kho', 'Tồn kho'), ('/bao-thu', 'Báo thu'), ('/cham-cong', 'Chấm công'))


TECH_LINKS = (('/tho','Hôm nay'),('/hang-toi-giu','Hàng tôi giữ'),('/tien-toi-giu','Tiền tôi giữ'),('/thiet-bi','Thiết bị'))
TECH_PATHS = ('/tho', '/hang-toi-giu', '/tien-toi-giu')


def technician_shell(user, path, query=None):
    """Khung màn thợ: thợ thuần luôn dùng; người vừa là thợ vừa vai khác dùng khi đang ở trang của thợ."""
    if pure_technician(user): return True
    if 'technician' not in user.roles: return False
    if any(path == p or path.startswith(p + '/') for p in TECH_PATHS): return True
    return (path.startswith('/giao-don/') or path.startswith('/tho-tra-hang')) and (query or {}).get('return_to') == 'tho'


# Việc riêng của thợ và con số của chính mình: giám đốc xem ở trang tổng, không cần trên menu.
DIRECTOR_HIDDEN = ('/kpi', '/hoa-hong', '/tien-toi-giu', '/hang-toi-giu')


def menu(user, path, query=None):
    current = matching(path, query)
    groups = []
    shell = technician_shell(user, path, query)
    if shell:
        groups = [dict(label='', section='Của tôi', items=[dict(href=href,label=label,active=(current is matching(href))) for href,label in TECH_LINKS], active=False)]
    for group in GROUPS + ACCOUNT_GROUPS:
        visible = [dict(item, active=item is current or (bool(current) and not item.get('highlight') and current.get('highlight')==item['href'])) for item in group['items'] if not item.get('hidden') and not pure_technician(user) and (not item.get('technician_page') or 'technician' in user.roles) and not item.get('account_page') and not (item.get('payroll_page') and 'admin' in user.roles and 'director' not in user.roles) and not (item['href'] in DIRECTOR_HIDDEN and 'director' in user.roles) and has_role(user, *item.get('menu_roles',item['roles']))]
        if visible:
            if len(visible) == 1 and visible[0].get('solo'): visible[0]['label'] = visible[0]['solo']  # Mục lẻ đứng riêng phải tự đủ nghĩa.
            for item in visible: item['icon'] = NAV_ICONS.get(item['href'], NAV_ICONS.get(group['label'], ''))
            groups.append(dict(label=group['label'], section=group['section'], items=visible, active=any(item['active'] for item in visible), icon=NAV_ICONS.get(group['label'], '')))
    groups.sort(key=lambda g: SECTIONS.index(g['section']))
    shown = {item['href']: item for group in groups for item in group['items']}
    tabs = [dict(href=href, label=label, icon=shown[href]['icon'], active=shown[href]['active']) for href, label in PHONE_TABS if href in shown][:3]
    crumb = next(((group['label'] or 'Của tôi', item['label']) for group in groups for item in group['items'] if item['active']), None)
    roles = ', '.join(label for role, label in ROLE_LABELS.items() if role in user.roles)
    # Người nhiều vai trong khung thợ: tab dưới là nhóm đầu, menu đầy đủ mở từ nút tài khoản.
    personal = [dict(href=h, label=l, hint=t) for h, l, t in PERSONAL if allowed(user, h)] if shell else []
    return dict(groups=groups, tabs=tabs, crumb=crumb, roles=roles, technician=shell, pure=pure_technician(user), home=home_url(user), personal=personal)

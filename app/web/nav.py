"""U1: cấu hình duy nhất cho menu và quyền vào các đường dẫn web.

Prefix khớp ranh giới đường dẫn, ưu tiên mục cụ thể nhất; điều kiện query
phân biệt Sắp hết với Tồn kho. Mục chưa khai báo bị chặn mặc định.
"""
from pydantic import TypeAdapter, ValidationError

from ..errors import BusinessError
from ..security import has_role

GROUPS = (
    {'label': '', 'items': (
        {'label': 'Hoa hồng của tôi', 'href': '/hoa-hong', 'prefixes': ('/hoa-hong',), 'roles': ('sales','technician')},
        {'label': 'KPI của tôi', 'href': '/kpi', 'prefixes': ('/kpi',), 'roles': ('technician','sales','dispatcher','warehouse','finance')},
        {'label': 'Chấm công', 'href': '/cham-cong', 'prefixes': ('/cham-cong',), 'roles': ('sales','reception','dispatcher','warehouse','technician','finance','director','admin')},
        {'label': 'Tổng quan', 'href': '/tong-quan', 'prefixes': ('/tong-quan',), 'roles': ('finance',)},
        {'label': 'Hôm nay', 'href': '/tho', 'prefixes': ('/tho',), 'roles': ('technician',), 'technician_page': True},
        {'label': 'Việc của tôi', 'href': '/viec-cua-toi', 'prefixes': ('/viec-cua-toi', '/giu-may', '/giao-don', '/tho-tra-hang'), 'roles': ('technician', 'sales', 'warehouse', 'dispatcher', 'reception', 'finance')},
    )},
    {'label': 'Bán hàng', 'items': (
        {'label': 'Báo giá', 'href': '/bao-gia', 'prefixes': ('/bao-gia',), 'roles': ('sales', 'finance')},
        {'label': 'Đơn bán', 'href': '/don-ban', 'prefixes': ('/don-ban','/tra-hang'), 'roles': ('sales', 'warehouse', 'finance')},
    )},
    {'label': 'Dịch vụ', 'items': (
        {'label': 'Máy đang giữ', 'href': '/may-dang-giu', 'prefixes': ('/may-dang-giu',), 'roles': ('dispatcher','warehouse')},
        {'label': 'Điều phối', 'href': '/dieu-phoi', 'prefixes': ('/dieu-phoi',), 'roles': ('dispatcher',)},
        {'label': 'Dịch vụ', 'href': '/dich-vu', 'prefixes': ('/dich-vu',), 'roles': ('dispatcher', 'reception', 'technician')},
        {'label': 'Tiền tôi giữ', 'href': '/tien-toi-giu', 'prefixes': ('/tien-toi-giu',),
         'roles': ('technician','dispatcher','reception','sales','warehouse','finance','director','admin'), 'menu_roles': ('technician',)},
        {'label': 'Hàng tôi giữ', 'href': '/hang-toi-giu', 'prefixes': ('/hang-toi-giu',), 'roles': ('technician','warehouse'), 'menu_roles': ('technician',)},
        {'label': 'Thiết bị', 'href': '/thiet-bi', 'prefixes': ('/thiet-bi',), 'roles': ('dispatcher', 'reception', 'technician', 'sales')},
    )},
    {'label': 'Kho', 'items': (
        {'label':'Chờ xử lý','href':'/cho-xu-ly','prefixes':('/cho-xu-ly',),'roles':('warehouse','finance','dispatcher')},
        {'label': 'Linh kiện thu hồi', 'href': '/linh-kien-thu-hoi', 'prefixes': ('/linh-kien-thu-hoi',), 'roles': ('technician','warehouse','dispatcher','finance')},
        {'label': 'Yêu cầu linh kiện', 'href': '/yeu-cau-linh-kien', 'prefixes': ('/yeu-cau-linh-kien',), 'roles': ('warehouse',)},
        {'label': 'Tồn kho', 'href': '/ton-kho', 'prefixes': ('/ton-kho', '/the-kho'), 'roles': ('sales', 'warehouse', 'finance')},
        {'label': 'Phiếu kho', 'href': '/phieu-kho', 'prefixes': ('/phieu-kho',), 'roles': ('warehouse',)},
        {'label': 'Sắp hết', 'href': '/ton-kho?canh-bao=1', 'prefixes': ('/ton-kho',), 'roles': ('warehouse',), 'query': {'canh-bao': True}},
        {'label': 'Tồn đầu kỳ', 'href': '/ton-dau-ky', 'prefixes': ('/ton-dau-ky',), 'roles': ('warehouse',)},
    )},
    {'label': 'Mua hàng', 'items': (
        {'label': 'Đơn mua', 'href': '/don-mua', 'prefixes': ('/don-mua',), 'roles': ('warehouse', 'finance')},
    )},
    {'label': 'Tài chính', 'items': (
        {'label': 'Báo thu', 'href': '/bao-thu', 'prefixes': ('/bao-thu', '/hoan-tien', '/dich-vu/can-thu'), 'roles': ('finance',)},
        {'label': 'Thu chi · Sổ quỹ', 'href': '/thu-chi', 'prefixes': ('/thu-chi', '/so-quy'), 'roles': ('finance',)},
        {'label': 'Chốt sổ', 'href': '/chot-so', 'prefixes': ('/chot-so',), 'roles': ('finance',)},
        {'label': 'Điều chỉnh', 'href': '/dieu-chinh', 'prefixes': ('/dieu-chinh',), 'roles': ('finance',)},
        {'label': 'Công nợ NCC', 'href': '/cong-no-ncc', 'prefixes': ('/cong-no-ncc',), 'roles': ('finance',)},
        {'label': 'Thiết lập tài chính', 'href': '/ghep-tai-chinh', 'prefixes': ('/quy', '/danh-muc-thu-chi', '/ghep-tai-chinh'), 'roles': ('finance',)},
    )},
    {'label': 'Báo cáo', 'items': (
        {'label': 'Báo cáo bán hàng', 'href': '/bao-cao/ban-hang', 'prefixes': ('/bao-cao/ban-hang',), 'roles': ('sales', 'finance')},
        {'label': 'Báo cáo mua hàng', 'href': '/bao-cao-mua-hang', 'prefixes': ('/bao-cao-mua-hang',), 'roles': ('warehouse', 'finance')},
        {'label': 'Báo cáo hàng chờ xử lý', 'href': '/bao-cao/cho-xu-ly', 'prefixes': ('/bao-cao/cho-xu-ly',), 'roles': ('finance',)},
        {'label': 'Báo cáo tài chính', 'href': '/bao-cao/tai-chinh', 'prefixes': ('/bao-cao/tai-chinh',), 'roles': ('finance',)},
    )},
    {'label': 'Danh mục', 'items': (
        {'label': 'Khách hàng', 'href': '/khach-hang', 'prefixes': ('/khach-hang',), 'roles': ('dispatcher', 'reception', 'sales', 'finance')},
        {'label': 'Hàng hóa', 'href': '/hang-hoa', 'prefixes': ('/hang-hoa',), 'roles': ('sales', 'warehouse', 'finance')},
        {'label': 'Kho', 'href': '/kho', 'prefixes': ('/kho',), 'roles': ('warehouse',)},
        {'label': 'Nhà cung cấp', 'href': '/nha-cung-cap', 'prefixes': ('/nha-cung-cap',), 'roles': ('warehouse', 'finance')},
    )},
)
# Tài khoản có phạm vi riêng: quyền giám đốc ở menu nghiệp vụ không bao gồm quản trị.
ACCOUNT_GROUPS = (
    {'label': 'Quản trị', 'items': (
        {'label': 'Hoa hồng tháng', 'href': '/hoa-hong/thang', 'prefixes': ('/hoa-hong/thang',), 'roles': ('finance',)},
        {'label': 'Quy tắc hoa hồng', 'href': '/hoa-hong/quy-tac', 'prefixes': ('/hoa-hong/quy-tac',), 'roles': ('director',)},
        {'label': 'KPI tổng', 'href': '/kpi/tong', 'prefixes': ('/kpi/tong',), 'roles': ('director',)},
        {'label': 'Mục tiêu KPI', 'href': '/kpi/muc-tieu', 'prefixes': ('/kpi/muc-tieu',), 'roles': ('director',)},
        {'label': 'Duyệt chấm công', 'href': '/cham-cong/duyet', 'prefixes': ('/cham-cong/duyet',), 'roles': ('director',)},
        {'label': 'Bảng công', 'href': '/cham-cong/bang', 'prefixes': ('/cham-cong/bang',), 'roles': ('finance',)},
        {'label': 'Ngày lễ', 'href': '/cham-cong/ngay-le', 'prefixes': ('/cham-cong/ngay-le',), 'roles': ('director',)},
        {'label': 'Nhân sự', 'href': '/nhan-su', 'prefixes': ('/nhan-su',), 'roles': ('admin','director')},
        {'label': 'Tài khoản nhân viên', 'href': '/tai-khoan', 'prefixes': ('/tai-khoan',), 'roles': ('admin',)},
        {'label': 'Quy chuẩn giao diện', 'href': '/ui/quy-chuan', 'prefixes': ('/ui/quy-chuan',), 'roles': ('director',)},
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


def allowed(user, path, query=None):
    item = matching(path, query)
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


def menu(user, path, query=None):
    current = matching(path, query)
    groups = []
    if pure_technician(user):
        links = (('/tho','Hôm nay'),('/hang-toi-giu','Hàng tôi giữ'),('/tien-toi-giu','Tiền tôi giữ'),('/thiet-bi','Thiết bị'))
        groups = [dict(label='', items=[dict(href=href,label=label,active=(current is matching(href))) for href,label in links], active=False)]
    for group in GROUPS + ACCOUNT_GROUPS:
        visible = [dict(item, active=item is current) for item in group['items'] if not pure_technician(user) and not item.get('technician_page') and not item.get('account_page') and not (item['href'] in ('/kpi','/hoa-hong') and 'director' in user.roles) and has_role(user, *item.get('menu_roles',item['roles']))]
        if visible:
            groups.append(dict(label=group['label'], items=visible, active=any(item['active'] for item in visible)))
    roles = ', '.join(label for role, label in ROLE_LABELS.items() if role in user.roles)
    return dict(groups=groups, roles=roles, technician=pure_technician(user))

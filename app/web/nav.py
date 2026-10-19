"""U1: cấu hình duy nhất cho menu và quyền vào các đường dẫn web.

Prefix khớp ranh giới đường dẫn, ưu tiên mục cụ thể nhất; điều kiện query
phân biệt Sắp hết với Tồn kho. Mục chưa khai báo bị chặn mặc định.
"""
from pydantic import TypeAdapter, ValidationError

from ..errors import BusinessError
from ..security import has_role

GROUPS = (
    {'label': '', 'items': (
        {'label': 'Tổng quan', 'href': '/tong-quan', 'prefixes': ('/tong-quan',), 'roles': ('finance',)},
        {'label': 'Việc của tôi', 'href': '/viec-cua-toi', 'prefixes': ('/viec-cua-toi',), 'roles': ('technician', 'sales', 'warehouse', 'dispatcher', 'reception', 'finance')},
    )},
    {'label': 'Bán hàng', 'items': (
        {'label': 'Báo giá', 'href': '/bao-gia', 'prefixes': ('/bao-gia',), 'roles': ('sales', 'finance')},
        {'label': 'Đơn bán', 'href': '/don-ban', 'prefixes': ('/don-ban',), 'roles': ('sales', 'warehouse', 'finance')},
    )},
    {'label': 'Dịch vụ', 'items': (
        {'label': 'Điều phối', 'href': '/dieu-phoi', 'prefixes': ('/dieu-phoi',), 'roles': ('dispatcher',)},
        {'label': 'Dịch vụ', 'href': '/dich-vu', 'prefixes': ('/dich-vu',), 'roles': ('dispatcher', 'reception', 'technician')},
        {'label': 'Tiền tôi giữ', 'href': '/tien-toi-giu', 'prefixes': ('/tien-toi-giu',),
         'roles': ('technician','dispatcher','reception','sales','warehouse','finance','director','admin'), 'menu_roles': ('technician',)},
        {'label': 'Thiết bị', 'href': '/thiet-bi', 'prefixes': ('/thiet-bi',), 'roles': ('dispatcher', 'reception', 'technician', 'sales')},
    )},
    {'label': 'Kho', 'items': (
        {'label': 'Tồn kho', 'href': '/ton-kho', 'prefixes': ('/ton-kho', '/the-kho'), 'roles': ('sales', 'warehouse', 'finance')},
        {'label': 'Phiếu kho', 'href': '/phieu-kho', 'prefixes': ('/phieu-kho',), 'roles': ('warehouse',)},
        {'label': 'Sắp hết', 'href': '/ton-kho?canh-bao=1', 'prefixes': ('/ton-kho',), 'roles': ('warehouse',), 'query': {'canh-bao': True}},
        {'label': 'Tồn đầu kỳ', 'href': '/ton-dau-ky', 'prefixes': ('/ton-dau-ky',), 'roles': ('warehouse',)},
    )},
    {'label': 'Mua hàng', 'items': (
        {'label': 'Đơn mua', 'href': '/don-mua', 'prefixes': ('/don-mua',), 'roles': ('warehouse', 'finance')},
    )},
    {'label': 'Tài chính', 'items': (
        {'label': 'Báo thu', 'href': '/bao-thu', 'prefixes': ('/bao-thu', '/dich-vu/can-thu'), 'roles': ('finance',)},
        {'label': 'Thu chi · Sổ quỹ', 'href': '/thu-chi', 'prefixes': ('/thu-chi', '/so-quy'), 'roles': ('finance',)},
        {'label': 'Chốt sổ', 'href': '/chot-so', 'prefixes': ('/chot-so',), 'roles': ('finance',)},
        {'label': 'Điều chỉnh', 'href': '/dieu-chinh', 'prefixes': ('/dieu-chinh',), 'roles': ('finance',)},
        {'label': 'Công nợ NCC', 'href': '/cong-no-ncc', 'prefixes': ('/cong-no-ncc',), 'roles': ('finance',)},
        {'label': 'Thiết lập tài chính', 'href': '/ghep-tai-chinh', 'prefixes': ('/quy', '/danh-muc-thu-chi', '/ghep-tai-chinh'), 'roles': ('finance',)},
    )},
    {'label': 'Báo cáo', 'items': (
        {'label': 'Báo cáo bán hàng', 'href': '/bao-cao/ban-hang', 'prefixes': ('/bao-cao/ban-hang',), 'roles': ('sales', 'finance')},
        {'label': 'Báo cáo mua hàng', 'href': '/bao-cao-mua-hang', 'prefixes': ('/bao-cao-mua-hang',), 'roles': ('warehouse', 'finance')},
        {'label': 'Báo cáo tài chính', 'href': '/bao-cao/tai-chinh', 'prefixes': ('/bao-cao/tai-chinh',), 'roles': ('finance',)},
    )},
    {'label': 'Danh mục', 'items': (
        {'label': 'Khách hàng', 'href': '/khach-hang', 'prefixes': ('/khach-hang',), 'roles': ('dispatcher', 'reception', 'sales', 'finance')},
        {'label': 'Hàng hóa', 'href': '/hang-hoa', 'prefixes': ('/hang-hoa',), 'roles': ('sales', 'warehouse', 'finance')},
        {'label': 'Kho', 'href': '/kho', 'prefixes': ('/kho',), 'roles': ('warehouse',)},
        {'label': 'Nhà cung cấp', 'href': '/nha-cung-cap', 'prefixes': ('/nha-cung-cap',), 'roles': ('warehouse', 'finance')},
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
    for item in items():
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
    return item is not None and has_role(user, *item['roles'])


def check_access(user, path, query=None):
    if not allowed(user, path, query):
        raise BusinessError('FORBIDDEN', 'Bạn không có quyền vào trang này', status=403)


def home_url(user):
    return next((href for role, href in HOME_PAGES if has_role(user, role)), '/viec-cua-toi')


def work_url(user):
    """Đích danh sách theo vai trò cho nơi còn dùng helper; mặc định an toàn."""
    return next((href for role, href in (('technician', '/dich-vu'), ('sales', '/don-ban'),
                                       ('warehouse', '/phieu-kho')) if has_role(user, role)), '/viec-cua-toi')


def menu(user, path, query=None):
    current = matching(path, query)
    groups = []
    for group in GROUPS:
        visible = [dict(item, active=item is current) for item in group['items'] if has_role(user, *item.get('menu_roles',item['roles']))]
        if visible:
            groups.append(dict(label=group['label'], items=visible, active=any(item['active'] for item in visible)))
    roles = ', '.join(label for role, label in ROLE_LABELS.items() if role in user.roles)
    return dict(groups=groups, roles=roles)

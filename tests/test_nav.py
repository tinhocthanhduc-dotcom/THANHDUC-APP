"""U1: cùng cấu hình cho menu và bộ chặn quyền; API/lõi giữ quyền cũ."""
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

import pytest

from app import service_tickets
from app.errors import BusinessError
from app.models import Attachment
from app.schemas import ServiceTicketIn
from app.web import check_navigation, nav, router
from conftest import PASSWORD
from test_web import service_people, service_login, d5_image

# Đối chiếu độc lập với bảng vai trò, không sinh kỳ vọng từ cấu hình cần kiểm thử.
EXPECTED = {
    'technician': {'/linh-kien-thu-hoi', '/viec-cua-toi', '/dich-vu', '/thiet-bi', '/tien-toi-giu', '/hang-toi-giu'},
    'dispatcher': {'/cho-xu-ly', '/linh-kien-thu-hoi', '/may-dang-giu', '/viec-cua-toi', '/dieu-phoi', '/dich-vu', '/thiet-bi', '/khach-hang'},
    'reception': {'/viec-cua-toi', '/dich-vu', '/thiet-bi', '/khach-hang'},
    'sales': {'/viec-cua-toi', '/thiet-bi', '/khach-hang', '/bao-gia', '/don-ban',
              '/bao-cao/ban-hang', '/hang-hoa', '/ton-kho'},
    'warehouse': {'/cho-xu-ly', '/linh-kien-thu-hoi', '/may-dang-giu', '/yeu-cau-linh-kien', '/viec-cua-toi', '/don-ban', '/hang-hoa', '/ton-kho', '/phieu-kho',
                  '/ton-kho?canh-bao=1', '/ton-dau-ky', '/kho', '/don-mua',
                  '/nha-cung-cap', '/bao-cao-mua-hang'},
    'finance': {'/cho-xu-ly', '/linh-kien-thu-hoi', '/viec-cua-toi', '/tong-quan', '/khach-hang', '/bao-gia', '/don-ban', '/bao-cao/ban-hang',
                '/hang-hoa', '/ton-kho', '/don-mua', '/nha-cung-cap', '/bao-cao-mua-hang',
                '/bao-thu', '/thu-chi', '/chot-so', '/dieu-chinh', '/cong-no-ncc',
                '/bao-cao/tai-chinh', '/ghep-tai-chinh'},
}
EXPECTED['director'] = set().union(*EXPECTED.values()) | {'/ui/quy-chuan'}  # UI0: trang mẫu giao diện, chỉ giám đốc
HOMES = {'finance': '/tong-quan', 'director': '/tong-quan', 'dispatcher': '/dieu-phoi',
         'reception': '/dich-vu', 'technician': '/tho', 'sales': '/viec-cua-toi',
         'warehouse': '/viec-cua-toi'}


class Menu(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'data-nav-item' in attrs:
            self.links.append(attrs)


@pytest.mark.parametrize('role', HOMES)
def test_exact_menu_home_and_login_for_each_role(client, make_user, role):
    user = make_user('nav.'+role, role)
    response = client.post('/dang-nhap', data={'username': user.username, 'password': PASSWORD,
                                             'next': '/'}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == HOMES[role]
    assert client.get('/', follow_redirects=False).headers['location'] == HOMES[role]
    page = client.get(HOMES[role])
    assert page.status_code == 200
    links = Menu(page.text).links
    expected = {'/tho','/hang-toi-giu','/tien-toi-giu','/thiet-bi'} if role=='technician' else EXPECTED[role]
    assert {a['href'] for a in links} == expected
    assert len(links) == len(expected)  # Không nhân đôi menu desktop/mobile.
    assert nav.ROLE_LABELS[role] in page.text and user.full_name in page.text
    assert 'data-web-menu' in page.text and '>Menu</summary>' in page.text


@pytest.mark.parametrize('roles,home', [
    (('warehouse', 'sales'), '/viec-cua-toi'),
    (('technician', 'reception'), '/dich-vu'),
    (('sales', 'reception', 'dispatcher'), '/dieu-phoi'),
    (('dispatcher', 'finance'), '/tong-quan'),
    (('technician', 'director'), '/tong-quan'),
])
def test_multi_role_union_and_home_priority(client, make_user, roles, home):
    user = make_user('nav.multi', *roles)
    response = client.post('/dang-nhap', data={'username': user.username, 'password': PASSWORD}, follow_redirects=False)
    assert response.headers['location'] == home
    assert client.get('/', follow_redirects=False).headers['location'] == home
    links = Menu(client.get(home).text).links
    assert {a['href'] for a in links} == set().union(*(EXPECTED[r] for r in roles))


def test_technician_blocked_pages_but_own_service_selectors_photos_and_report_work(client, db, service_people):
    p = service_people
    ticket = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001',
        serial='AB123', kind='SUA_TAI_CHO', symptom='Kẹt giấy'))
    service_tickets.assign(db, p['dispatcher'], ticket.id, p['technician'].id, ticket.version)
    csrf = service_login(client, p['technician'])
    for path in ('/hang-hoa', '/kho', '/nha-cung-cap', '/phieu-kho', '/ton-kho',
                 '/khach-hang', '/don-mua', '/thu-chi', '/hang-hoa?phan=1',
                 '/phieu-kho/tim-hang?q=may', '/khach-hang/moi', '/kho/moi'):
        page = client.get(path)
        assert page.status_code == 403, path
        assert 'Bạn không có quyền vào trang này' in page.text and 'href="/"' in page.text
    assert client.get('/dich-vu').status_code == 200
    assert client.get(f'/dich-vu/{ticket.id}').status_code == 200
    choices = client.get('/dich-vu/chon-khach?customer_code=KTD-000001')
    assert choices.status_code == 200 and 'AB123' in choices.text
    materials = client.get('/dich-vu/tim-vat-tu?q=may in')
    assert materials.status_code == 200 and 'TD-0001' in materials.text
    upload = client.post(f'/dich-vu/{ticket.id}/anh/tai', data={'csrf': csrf, 'tag': 'SAU'},
                        files={'file': ('sau.png', d5_image(), 'image/png')})
    assert upload.status_code == 200
    attachment = db.query(Attachment).one()
    assert client.get(f'/attachments/{attachment.id}/file').status_code == 200
    assert client.get(f'/dich-vu/{ticket.id}/bien-ban').status_code == 200
    csrf = service_login(client, p['other'])
    assert client.get(f'/dich-vu/{ticket.id}').status_code == 404
    assert client.get(f'/attachments/{attachment.id}/file').status_code == 404
    assert client.post(f'/dich-vu/{ticket.id}/anh/tai', data={'csrf': csrf, 'tag': 'SAU'},
                       files={'file': ('sau.png', d5_image(), 'image/png')}).status_code == 404


@pytest.mark.parametrize('role,denied', [
    ('sales', ('/phieu-kho', '/don-mua', '/thu-chi')),
    ('warehouse', ('/bao-gia', '/thu-chi', '/tong-quan')),
])
def test_sales_and_warehouse_server_guards(client, make_user, role, denied):
    user = make_user('nav.'+role, role)
    csrf = service_login(client, user)
    for path in denied:
        assert client.get(path).status_code == 403
    assert client.get('/ton-kho').status_code == 200
    assert client.post('/thu-chi/moi', data={'csrf': csrf}).status_code == 403


@pytest.mark.parametrize('role', ['sales', 'finance'])
@pytest.mark.parametrize('value', ['1', 'true', 'TRUE', 'yes', 'on', 't', 'T', 'y', 'Y'])
def test_stock_warning_filter_cannot_bypass_warehouse_role(client, make_user, role, value):
    service_login(client, make_user('nav.'+role, role))
    assert client.get('/ton-kho').status_code == 200
    assert client.get('/ton-kho?canh-bao='+value).status_code == 403
    assert client.get('/ton-kho?canh-bao=0').status_code == 200
    assert client.get('/ton-kho?canh-bao=0&canh-bao='+value).status_code == 403
    assert client.get('/ton-kho?canh-bao='+value+'&canh-bao=0').status_code == 200


def test_finance_specific_service_receivables_prefix_and_csv(client, make_user):
    service_login(client, make_user('nav.finance', 'finance'))
    assert client.get('/dich-vu').status_code == 403
    assert client.get('/dich-vu/can-thu').status_code == 200
    assert client.get('/dich-vu/can-thu.csv').status_code == 200
    assert client.get('/quy').status_code == 200
    assert client.get('/danh-muc-thu-chi').status_code == 200


def test_director_every_menu_destination_works(client, make_user):
    service_login(client, make_user('nav.director', 'director'))
    for item in nav.items():
        if item.get('technician_page'): continue  # Chỉ người có vai trò technician mới vào /tho.
        response = client.get(item['href'])
        assert response.status_code == 200, (item['href'], response.text[:200])


def test_every_prefix_is_real_every_web_get_is_mapped_and_every_route_is_guarded():
    routes = [r for r in router.routes if 'GET' in r.methods]
    for item in nav.items():
        for prefix in item['prefixes']:
            assert urlsplit(prefix).path == prefix, prefix  # Prefix phải là đường dẫn route thật.
            base = prefix
            assert any(r.path == base or r.path.startswith(base+'/') or r.path == base+'.csv' for r in routes), prefix
        parts = urlsplit(item['href'])
        assert nav.matching(parts.path, dict(parse_qsl(parts.query))) is item
    for route in router.routes:
        assert any(dep.call is check_navigation for dep in route.dependant.dependencies), route.path
        if 'GET' in route.methods and route.path not in nav.PUBLIC_PATHS:
            assert nav.matching(route.path) is not None, route.path


def test_exact_active_item_group_and_single_item_not_dropdown(client, make_user):
    service_login(client, make_user('nav.warehouse', 'warehouse'))
    page = client.get('/ton-kho?canh-bao=1')
    links = Menu(page.text).links
    assert [a['href'] for a in links if a.get('aria-current') == 'page'] == ['/ton-kho?canh-bao=1']
    assert 'class="nav-group on"' in page.text
    # Mua hàng chỉ có Đơn mua: link trực tiếp, không summary xổ thêm một tầng.
    assert '<summary>Mua hàng</summary>' not in page.text
    assert any(a['href'] == '/don-mua' for a in links)
    assert nav.matching('/thu-chi-khac') is None
    assert nav.matching('/ton-kho-khac') is None
    with pytest.raises(BusinessError) as exc:
        nav.check_access(make_user('nav.director', 'director'), '/trang-chua-khai-bao')
    assert exc.value.status == 403


def test_work_page_replaces_redirect_and_logged_out_home(client, make_user):
    assert client.get('/', follow_redirects=False).headers['location'] == '/dang-nhap'
    for role, target in (('technician', '/dich-vu'), ('sales', '/don-ban'), ('warehouse', '/phieu-kho')):
        user=make_user('work.'+role, role)
        service_login(client, user)
        assert client.get('/viec-cua-toi').status_code == 200
        assert nav.work_url(user) == target


def test_login_returns_to_allowed_next_page_else_role_home(client, make_user):
    from conftest import PASSWORD

    make_user("seller_next", "sales")
    page = client.post("/dang-nhap", data={"username": "seller_next", "password": PASSWORD, "next": "/bao-gia"}, follow_redirects=False)
    assert page.headers["location"] == "/bao-gia"
    client.cookies.clear()
    page = client.post("/dang-nhap", data={"username": "seller_next", "password": PASSWORD, "next": "/thu-chi"}, follow_redirects=False)
    assert page.headers["location"] == "/viec-cua-toi"

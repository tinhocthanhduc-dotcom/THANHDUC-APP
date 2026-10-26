"""TD1: mỗi lượt gọi máy chủ được bấm giờ, đếm truy vấn; trang Tốc độ app chỉ giám đốc/quản trị xem; trình duyệt gửi số đo về."""
import json
import shutil

from app import perf
from test_tk_tai_khoan import web_login


def lines():
    return [json.loads(line) for line in open(perf._file(perf.datetime.now(perf.VN).date()), encoding='utf-8')]


def fresh():
    shutil.rmtree(perf.folder(), ignore_errors=True)


def test_each_request_logs_route_time_queries_and_size(client, make_user):
    fresh()
    director = make_user('giamdoc.perf', 'director')
    web_login(client, director)
    r = client.get('/viec-cua-toi')
    assert r.status_code == 200
    assert 'data-perf-route="/viec-cua-toi"' in r.text and '/static/perf.js' in r.text
    row = [x for x in lines() if x['k'] == 's' and x['r'] == '/viec-cua-toi'][-1]
    assert row['m'] == 'GET' and row['st'] == 200 and row['ms'] > 0 and row['q'] > 0 and row['b'] == len(r.content)
    # Đường dẫn ghi theo mẫu route, không theo số phiếu cụ thể.
    client.get('/dich-vu/999999')
    assert any(x['k'] == 's' and x['r'] == '/dich-vu/{ticket_id}' for x in lines())


def test_page_only_for_director_and_admin(client, make_user):
    fresh()
    web_login(client, make_user('banhang.perf', 'sales'))
    assert client.get('/toc-do').status_code == 403
    web_login(client, make_user('quantri.perf', 'admin'))
    assert client.get('/toc-do').status_code == 200
    web_login(client, make_user('giamdoc.perf2', 'director'))
    client.get('/viec-cua-toi'); client.get('/ca-nhan/thong-bao')
    page = client.get('/toc-do?ky=7-ngay')
    assert page.status_code == 200 and 'Tốc độ app' in page.text
    assert 'GET /viec-cua-toi' in page.text and 'Hỏi nền 15 giây' in page.text
    assert 'href="/toc-do"' in client.get('/viec-cua-toi').text  # có trong menu Quản trị


def test_browser_beacon_needs_csrf_and_is_summarised(client, make_user):
    fresh()
    csrf = web_login(client, make_user('kythuat.perf', 'technician'))
    bad = client.post('/ca-nhan/toc-do', data=dict(csrf='sai', route='/tho', ready='900'))
    assert bad.status_code == 403
    for ready in ('900', '1500', '3500'):
        ok = client.post('/ca-nhan/toc-do', data=dict(csrf=csrf, route='/tho', nav='navigate', ttfb='300', ready=ready, load='4000', kb='120.5'))
        assert ok.status_code == 204
    # Số đo vô lý bị bỏ qua, không làm hỏng tệp.
    client.post('/ca-nhan/toc-do', data=dict(csrf=csrf, route='/tho', ready='abc'))
    rows = [x for x in lines() if x['k'] == 'b']
    assert len(rows) == 3 and rows[0]['r'] == '/tho' and rows[0]['kb'] == 120.5
    s = perf.summary(1)
    screen = s['screens'][0]
    assert screen['route'] == '/tho' and screen['n'] == 3 and screen['ok'] == 67 and screen['p50'] == 1500
    assert s['browser_ok'] == 67


def test_slow_sql_logged_without_parameters(client, make_user, monkeypatch):
    fresh()
    monkeypatch.setattr(perf, 'SLOW_SQL_MS', 0)
    web_login(client, make_user('giamdoc.perf3', 'director'))
    client.get('/khach-hang?q=bí-mật-khách')
    slow = [x for x in lines() if x['k'] == 'q']
    assert slow and all('bí-mật' not in x['sql'] and 'bi-mat' not in x['sql'] for x in slow)
    assert perf.summary(1)['slow']


def test_old_files_removed_and_logging_can_be_turned_off(client, make_user, monkeypatch):
    fresh()
    perf.folder().mkdir(parents=True)
    old = perf.folder() / 'toc-do-2000-01-01.jsonl'
    old.write_text('{}\n')
    perf._cleaned.clear()
    web_login(client, make_user('giamdoc.perf4', 'director'))
    assert not old.exists()
    monkeypatch.setattr(perf.get_settings(), 'perf_log', False)
    before = len(lines())
    client.get('/viec-cua-toi')
    assert len(lines()) == before

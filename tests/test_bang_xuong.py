"""Bảng xưởng (TUẤN duyệt mẫu 17/11/2026): mỗi máy ở xưởng một hàng, bước đúng theo trạng thái, việc của mình mới có nút."""
from app import repair_inspection as ri
from app import repair_quotes as rq
from app import workshop_board as wb
from app.models import RepairInspection
from test_gui_bao_gia_sua import quoted, ready, tomorrow  # noqa: F401
from test_kiem_tra_may import book, form
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people, NOW  # noqa: F401
from test_web import service_login
from test_xg1_workshop import holding, workshop  # noqa: F401


def row(db, user, t):
    return next(x for x in wb.build(db, user)['rows'] if x['t'].id == t.id)


def test_board_moves_through_steps_and_owner_buttons(db, workshop, make_user):
    p, a, b, t, eq = workshop
    disp, tech, sales = p['dispatcher'], p['technician'], p['sales']
    r = row(db, tech, t)
    assert r['stage'] == 's1' and r['who'] == 'Kho' and r['action']['href'] == f'/kiem-tra-may/{t.id}'
    assert row(db, sales, t)['action'] is None  # kinh doanh không kiểm tra máy
    boss = make_user('bx.boss', 'director')
    fuser, feed, opts = book(db, boss)
    ri.save(db, tech, t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    r = row(db, sales, t)
    assert r['stage'] == 's2' and r['mine'] and r['action']['label'] == 'Duyệt · gửi'
    assert 'Sửa chữa cụm sấy · Cấp 2' in r['sub']
    assert row(db, tech, t)['action'] is None  # thợ không duyệt giá
    ri.apply(db, disp, t.id, t.version, {})
    db.refresh(t)
    v = rq.create(db, disp, t.id, t.version)
    rq.mark_sent(db, disp, v.id, 'COPY')
    r = row(db, disp, t)
    assert r['stage'] == 's3' and r['time'] == 'Gửi hôm nay' and 'chưa mở link' in r['note'] and not r['late']
    db.refresh(t)
    rq.agree(db, disp, v.id, t.version, 'PA1', 'ZALO', 'Anh Nam kế toán', tomorrow())
    r = row(db, disp, t)
    assert r['stage'] == 's4' and r['time'] == 'Còn 1 ngày' and r['action']['label'] == 'Sửa xong'
    counts = wb.build(db, disp)['counts']
    assert counts['s4'] == 1 and counts['all'] >= 1


def test_not_yet_goes_back_to_check_and_filters(db, quoted):
    p, t, fuser, boss = quoted
    disp = p['dispatcher']
    v = rq.create(db, disp, t.id, t.version)
    rq.mark_sent(db, disp, v.id, 'COPY')
    rq.not_yet(db, disp, v.id, 'Giá cao', None, 'ZALO', '')
    r = row(db, disp, t)
    assert r['stage'] == 's1' and r['note'].startswith('Kiểm tra lại: Báo giá lần 1: Giá cao')
    rows = wb.build(db, disp)['rows']
    assert [x['t'].id for x in wb.pick(rows, 's1', t.number)] == [t.id]
    assert wb.pick(rows, 's3') == [] and wb.pick(rows, 's1', 'khong-co-may-nay') == []


def test_web_board_page(client, db, quoted):
    p, t, fuser, boss = quoted
    service_login(client, p['sales'])
    page = client.get('/kiem-tra-may?view=tat-ca').text
    assert 'Bảng xưởng' in page and t.number in page and 'Chờ duyệt giá' in page and 'Duyệt · gửi' in page
    assert 'Kinh doanh hoặc giám đốc' in page
    page = client.get('/kiem-tra-may?view=s6').text
    assert t.number not in page and 'Không có máy nào ở mục này.' in page
    client.cookies.clear()
    service_login(client, p['dispatcher'])
    dp = client.get('/dieu-phoi').text
    assert 'href="/kiem-tra-may?view=tat-ca"' in dp and 'href="/kiem-tra-may?view=s6"' in dp

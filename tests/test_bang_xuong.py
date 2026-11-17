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


def test_plan_hours_missing_parts_and_eta(db, workshop, make_user):
    """Kho chọn gói + linh kiện: app tự ra giờ công (giờ chuẩn theo cấp), linh kiện thiếu, ngày xong dự kiến."""
    from decimal import Decimal
    p, a, b, t, eq = workshop
    boss = make_user('bx.boss', 'director')
    fuser, feed, opts = book(db, boss)
    fuser.hours_2 = Decimal('1.5')
    db.commit()
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy'], parts=[('TD-0001', 999)]))
    pl = wb.plan(db, db.get(RepairInspection, t.id))
    assert pl['hours_text'] == '1,5 giờ' and [m['code'] for m in pl['missing']] == ['TD-0001']
    assert pl['eta'] == wb.add_work_days(wb._now().date(), 2)  # 1 ngày công + 1 ngày chờ linh kiện
    r = row(db, p['sales'], t)
    assert ('Thiếu 1 LK', 'warn') in r['chips'] and r['note'].startswith('Công 1,5 giờ')


def test_save_inspection_confirms_custody_for_warehouse(client, db, workshop, make_user):
    """Lưu kết quả kiểm tra = kho nhận máy luôn (khỏi bấm riêng); thợ lưu thì không nhận thay kho."""
    from app import equipment_custody as custody
    from app.models import EquipmentCustody
    from app.schemas import CustodyHandover
    p, a, b, t, eq = workshop
    boss = make_user('bx.boss', 'director')
    fuser, feed, opts = book(db, boss)
    db.refresh(eq)
    if custody.pending(db, eq.serial) is None:
        custody.handover(db, p['dispatcher'], t.id, CustodyHandover(version=eq.version, location='KHO_KY_THUAT'))
    keeper = make_user('bx.kho', 'warehouse', 'technician')
    csrf = service_login(client, keeper)
    data = {'csrf': csrf, 'model': 'Canon LBP 2900', 'dv': str(fuser.id), f'opt_{fuser.id}': str(opts['Thay bao lụa sấy'])}
    r = client.post(f'/kiem-tra-may/{t.id}', data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == f'/kiem-tra-may?da_kiem_tra={t.id}'
    db.expire_all()
    assert custody.pending(db, eq.serial) is None
    assert db.query(EquipmentCustody).filter_by(serial=eq.serial, status='DA_NHAN', location='KHO_KY_THUAT').count() >= 1

"""Bảng xưởng (TUẤN duyệt mẫu 17/11/2026): mỗi máy ở xưởng một hàng, bước đúng theo trạng thái, việc của mình mới có nút."""
import pytest
from app import repair_inspection as ri
from app.errors import BusinessError
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
    disp, tech, sales = p['dispatcher'], p['technician'], p['sales']  # disp kiêm kinh doanh (như chị Thanh)
    kho = make_user('bx.kho_dp', 'dispatcher', 'warehouse')  # như anh Thạnh: kiểm tra máy, không duyệt giá
    r = row(db, tech, t)
    assert r['stage'] == 's1' and r['who'] == 'Kho' and r['action']['href'] == f'/kiem-tra-may/{t.id}'
    assert row(db, sales, t)['action'] is None  # kinh doanh không kiểm tra máy
    boss = make_user('bx.boss', 'director')
    fuser, feed, opts = book(db, boss)
    ri.save(db, tech, t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    r = row(db, disp, t)
    assert r['stage'] == 's2' and r['mine'] and r['action']['label'] == 'Duyệt · gửi'
    assert 'Sửa chữa cụm sấy · Cấp 2' in r['sub']
    assert row(db, tech, t)['action'] is None  # thợ không duyệt giá
    assert row(db, kho, t)['action'] is None and row(db, kho, t)['who'] == 'Kinh doanh hoặc giám đốc'
    assert row(db, sales, t)['action'] is None  # kinh doanh không có quyền điều phối thì không mở được khung xưởng
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
    service_login(client, p['dispatcher'])
    page = client.get('/kiem-tra-may?view=tat-ca').text
    assert 'Bảng xưởng' in page and t.number in page and 'Chờ duyệt giá' in page and 'Duyệt · gửi' in page
    assert 'Kinh doanh hoặc giám đốc' in page
    page = client.get('/kiem-tra-may?view=s6').text
    assert t.number not in page and 'Không có máy nào ở mục này.' in page
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


def test_only_sales_or_director_quote(client, db, workshop, make_user):
    """TUẤN 17/11/2026: kho/điều phối chỉ kiểm tra máy; duyệt giá, gửi khách, ghi khách trả lời là kinh doanh hoặc giám đốc."""
    import pytest
    from app import service_workshop
    from app.errors import BusinessError
    p, a, b, t, eq = workshop
    kho = make_user('bx.kho_dp', 'dispatcher', 'warehouse')
    boss = make_user('bx.boss', 'director')
    fuser, feed, opts = book(db, boss)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    tid, ver = t.id, t.version
    csrf = service_login(client, kho)
    page = client.get(f'/dich-vu/{tid}').text
    assert 'Kinh doanh hoặc giám đốc duyệt giá' in page and 'id="workshop-approve"' not in page and 'Duyệt giá</button>' not in page
    assert client.post(f'/kiem-tra-may/{tid}/ap-gia', data={'csrf': csrf, 'version': str(ver)}).status_code == 403
    for call in (lambda: ri.apply(db, kho, tid, ver, {}), lambda: rq.create(db, kho, tid, ver),
                 lambda: service_workshop.quote_approve(db, kho, tid, ver, tomorrow()),
                 lambda: service_workshop.quote_decline(db, kho, tid, ver, 'FREE', 0, 'Khách thôi')):
        with pytest.raises(BusinessError) as e:
            call()
        assert e.value.status == 403
    db.expire_all()
    assert db.get(RepairInspection, tid).applied_at is None


def test_agree_assigns_repair_technician(db, workshop, make_user):
    """Khách đồng ý · giao thợ sửa: gợi ý thợ ít việc mở nhất, chọn thợ thì phiếu về màn thợ đó."""
    from app import service_workshop
    p, a, b, t, eq = workshop
    disp = p['dispatcher']
    boss = make_user('bx.boss', 'director')
    free_tech = make_user('bx.tho_ranh', 'technician')
    fuser, feed, opts = book(db, boss)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    ri.apply(db, disp, t.id, t.version, {})
    db.refresh(t)
    load = service_workshop.repairers(db)
    assert load[0][1] == 0 and free_tech.id in [u.id for u, n in load if n == 0]
    assert dict((u.id, n) for u, n in load)[p['technician'].id] >= 1
    v = rq.create(db, disp, t.id, t.version)
    rq.mark_sent(db, disp, v.id, 'COPY')
    db.refresh(t)
    rq.agree(db, disp, v.id, t.version, 'PA1', 'ZALO', 'Anh Nam', tomorrow(), '', free_tech.id)
    db.refresh(t)
    assert t.assignee_id == free_tech.id and t.quote_approved_at is not None
    r = row(db, disp, t)
    assert r['stage'] == 's4' and r['who'] == 'Thợ ' + free_tech.full_name


def test_handoffs_reach_the_right_person(db, workshop, make_user):
    """Đợt 1d: máy mới về → kho (chuông); kiểm tra xong → kinh doanh/giám đốc (chuông); gửi khách → chờ trả lời có hạn 2 ngày."""
    from app import my_work, notifications
    p, a, b, t, eq = workshop
    disp = p['dispatcher']
    kho = make_user('bx.kho_dp', 'dispatcher', 'warehouse')
    boss = make_user('bx.boss', 'director')

    def ids(user, key):
        return [r['id'] for r in my_work.filtered(db, user, key)['rows']]

    def bell(user):
        return {x['id'] for x in notifications.items(db, user)}

    assert ids(kho, 'service_ws_check') == [t.id] and f'service_ws_check:{t.id}' in bell(kho)
    assert 'service_ws_quote' not in {g['key'] for g in my_work.collect(db, kho)}  # kho không duyệt giá
    fuser, feed, opts = book(db, boss)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    assert ids(kho, 'service_ws_check') == [] and ids(disp, 'service_ws_quote') == [t.id]
    assert f'service_ws_quote:{t.id}' in bell(disp)
    row = my_work.filtered(db, disp, 'service_ws_quote')['rows'][0]
    assert row['link'] == f'/dich-vu/{t.id}#gui-khach' and row['due_at'] is not None
    ri.apply(db, disp, t.id, t.version, {})
    db.refresh(t)
    v = rq.create(db, disp, t.id, t.version)
    rq.mark_sent(db, disp, v.id, 'COPY')
    assert ids(disp, 'service_ws_quote') == [] and ids(disp, 'service_ws_reply') == [t.id]
    rq.not_yet(db, disp, v.id, 'Giá cao', None, 'ZALO', '')
    assert ids(kho, 'service_ws_check') == [t.id]  # khách chưa đồng ý: về kho kiểm tra lại


def test_technician_reports_repaired_then_kho_closes(client, db, workshop, make_user):
    """Đợt 2: khách đồng ý giao thợ sửa → thợ thấy trên màn thợ, báo sửa xong (ảnh + đã sửa gì) → kho thấy Chờ chốt, chốt sửa xong."""
    from app import my_work, notifications, service_workshop
    from app.errors import BusinessError
    from test_web import d5_image
    import pytest
    p, a, b, t, eq = workshop
    disp = p['dispatcher']
    kho = make_user('bx.kho_dp', 'dispatcher', 'warehouse')
    boss = make_user('bx.boss', 'director')
    tho = make_user('bx.tho_sua', 'technician')
    fuser, feed, opts = book(db, boss)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy'], parts=[('TD-0001', 1)]))
    ri.apply(db, disp, t.id, t.version, {})
    db.refresh(t)
    v = rq.create(db, disp, t.id, t.version)
    rq.mark_sent(db, disp, v.id, 'COPY')
    db.refresh(t)
    rq.agree(db, disp, v.id, t.version, 'PA1', 'ZALO', 'Anh Nam', tomorrow(), '', tho.id)
    db.refresh(t)
    assert [r['id'] for r in my_work.filtered(db, tho, 'service_ws_repair')['rows']] == [t.id]
    assert f'service_ws_repair:{t.id}' in {x['id'] for x in notifications.items(db, tho)}
    r = row(db, tho, t)
    assert r['stage'] == 's4' and r['action']['label'] == 'Báo sửa xong' and r['mine']
    tid, ver = t.id, t.version
    # thợ khác không báo thay được; thiếu ảnh thì không lưu
    with pytest.raises(BusinessError) as e:
        service_workshop.report_repaired(db, p['technician'], tid, ver, 'Thay bao lụa')
    assert e.value.status == 403
    with pytest.raises(BusinessError) as e:
        service_workshop.report_repaired(db, tho, tid, ver, 'Thay bao lụa')
    assert e.value.code == 'PHOTO_REQUIRED'
    csrf = service_login(client, tho)
    home = client.get('/tho').text
    assert 'Máy ở xưởng giao bạn sửa' in home and f'/tho/phieu/{tid}#sua-xuong' in home
    page = client.get(f'/tho/phieu/{tid}').text
    assert 'Sửa máy ở xưởng' in page and 'Thay bao lụa sấy' in page and 'TD-0001' in page
    res = client.post(f'/tho/phieu/{tid}/bao-sua-xong', data={'csrf': csrf, 'version': str(ver), 'fix': 'Thay bao lụa sấy, test in 20 trang'},
                      files={'photo': ('sau.png', d5_image(), 'image/png')}, follow_redirects=False)
    assert res.status_code == 303 and res.headers['location'] == '/tho?sua_xong=1'
    db.expire_all()
    t = db.get(type(t), tid)
    assert t.status == 'TAM_DUNG' and t.hold_reason == 'CHO_SUA_XUONG' and t.repaired_by is None  # chưa đụng tiền, kho
    assert my_work.filtered(db, tho, 'service_ws_repair')['rows'] == []
    assert [x['id'] for x in my_work.filtered(db, kho, 'service_ws_close')['rows']] == [tid]
    r = row(db, kho, t)
    assert r['stage'] == 's5' and r['action']['label'] == 'Chốt · giao trả' and 'test in 20 trang' in r['note']
    with pytest.raises(BusinessError) as e:
        service_workshop.report_repaired(db, tho, tid, t.version, 'Báo lần hai')
    assert e.value.code == 'ALREADY_REPORTED'
    client.cookies.clear()
    service_login(client, kho)
    page = client.get(f'/dich-vu/{tid}').text
    assert 'báo sửa xong' in page and 'test in 20 trang' in page and 'id="workshop-repaired"' in page


def _agreed(db, p, make_user, tho, parts=()):
    boss = make_user('bx.boss', 'director')
    fuser, feed, opts = book(db, boss)
    t = p['workshop'][3]
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy'], parts=list(parts)))
    ri.apply(db, p['dispatcher'], t.id, t.version, {})
    db.refresh(t)
    v = rq.create(db, p['dispatcher'], t.id, t.version)
    rq.mark_sent(db, p['dispatcher'], v.id, 'COPY')
    db.refresh(t)
    rq.agree(db, p['dispatcher'], v.id, t.version, 'PA1', 'ZALO', 'Anh Nam', tomorrow(), '', tho.id)
    db.refresh(t)
    return t, fuser, opts, v


def test_extra_materials_not_charged_keep_approval_and_leave_stock(db, workshop, make_user):
    """Vật tư phát sinh không tính khách: dòng giá 0, khách không phải đồng ý lại, kho trừ khi chốt, tổng khách giữ nguyên."""
    from app import service_workshop
    from app.models import ServiceTicket
    from test_technician_stock import balance
    from test_mv1_workshop import photo
    from test_xg1_workshop import repaired
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    tho = make_user('bx.tho_sua', 'technician')
    t, fuser, opts, v = _agreed(db, p, make_user, tho)
    total = sum(x.amount + x.vat_amount for x in t.lines)
    stock = balance(db, 'K58')
    photo(db, p, t, 'SAU', who='dispatcher')
    service_workshop.report_repaired(db, tho, t.id, t.version, 'Vệ sinh, thay bánh xe', [('TD-0001', 'K58', 1)])
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    extra = [x for x in t.lines if x.kind == 'VAT_TU']
    assert len(extra) == 1 and extra[0].unit_price == 0 and extra[0].amount == 0 and 'không tính khách' in extra[0].description
    assert t.quote_approved_at is not None and sum(x.amount + x.vat_amount for x in t.lines) == total
    assert row(db, p['dispatcher'], t)['stage'] == 's5'
    repaired(db, p, t, repaired_by=tho.id)
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.total == total and balance(db, 'K58') == stock - 1 and all(x.stock_doc_id for x in t.lines if x.kind == 'VAT_TU')


def test_extra_goes_back_to_kho_then_keep_previous_quote(db, workshop, make_user):
    """Phát sinh · báo lại khách: về kho kiểm tra lại → duyệt giá → gửi lần 2 → khách chỉ giữ lần 1: giá về lần 1, cùng thợ sửa tiếp."""
    from app import my_work, service_workshop
    from app.models import ServiceTicket
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    disp = p['dispatcher']
    kho = make_user('bx.kho_dp', 'dispatcher', 'warehouse')
    tho = make_user('bx.tho_sua', 'technician')
    t, fuser, opts, v1 = _agreed(db, p, make_user, tho)
    total1 = sum(x.amount + x.vat_amount for x in t.lines)
    service_workshop.report_extra(db, tho, t.id, t.version, 'Bánh xe kéo giấy mòn, cần thay')
    db.refresh(t)
    r = row(db, kho, t)
    assert t.quote_approved_at is None and r['stage'] == 's1' and 'Phát sinh khi sửa' in r['note']
    assert t.id in [x['id'] for x in my_work.filtered(db, kho, 'service_ws_check')['rows']]
    ri.save(db, kho, t.id, form(fuser, opts, ['Thay bao lụa sấy'], parts=[('TD-0001', 1)]))
    r = row(db, disp, t)
    assert r['stage'] == 's2' and 'Phát sinh khi sửa' in r['note'] and r['action']['label'] == 'Duyệt · gửi'
    ri.apply(db, disp, t.id, t.version, {})
    db.refresh(t)
    v2 = rq.create(db, disp, t.id, t.version)
    rq.mark_sent(db, disp, v2.id, 'COPY')
    assert v2.no == 2 and rq.agreed_before(db, t.id).id == v1.id
    rq.keep_previous(db, disp, v2.id, 'Khách chỉ sửa cụm sấy')
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.quote_approved_at is not None and t.assignee_id == tho.id
    assert sum(x.amount + x.vat_amount for x in t.lines) == total1
    assert row(db, tho, t)['stage'] == 's4' and db.get(type(v2), v2.id).status == 'CHUA_DONG_Y'
    assert t.id in [x['id'] for x in my_work.filtered(db, tho, 'service_ws_repair')['rows']]


def test_cannot_repair_goes_to_sales_then_decline(db, workshop, make_user):
    """Không sửa được: kinh doanh thấy (chuông) → báo khách → Khách không sửa → chờ trả."""
    from app import my_work, notifications, service_workshop
    from app.models import ServiceTicket
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    disp = p['dispatcher']
    tho = make_user('bx.tho_sua', 'technician')
    t, fuser, opts, v = _agreed(db, p, make_user, tho)
    with pytest.raises(BusinessError) as e:
        service_workshop.report_failed(db, tho, t.id, t.version, 'Lý do lạ')
    assert e.value.code == 'REASON_REQUIRED'
    service_workshop.report_failed(db, tho, t.id, t.version, 'Không có linh kiện', 'Hết hàng hãng')
    r = row(db, disp, t)
    assert r['stage'] == 's3' and r['stage_label'] == 'Không sửa được' and r['action']['label'] == 'Báo khách · không sửa'
    assert f'service_ws_failed:{t.id}' in {x['id'] for x in notifications.items(db, disp)}
    assert my_work.filtered(db, tho, 'service_ws_repair')['rows'] == []
    with pytest.raises(BusinessError) as e:
        service_workshop.report_repaired(db, tho, t.id, t.version, 'Sửa xong')
    assert e.value.code == 'ALREADY_REPORTED'
    db.refresh(t)
    service_workshop.quote_decline(db, disp, t.id, t.version, 'FREE', 0, 'Không sửa được, miễn phí kiểm tra')
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.hold_reason == 'SUA_XONG_CHO_TRA' and row(db, disp, t)['stage'] == 's6'


def test_technician_screen_three_buttons_web(client, db, workshop, make_user):
    from app.models import ServiceTicket
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    tho = make_user('bx.tho_sua', 'technician')
    t, fuser, opts, v = _agreed(db, p, make_user, tho)
    tid, ver = t.id, t.version
    csrf = service_login(client, tho)
    page = client.get(f'/tho/phieu/{tid}').text
    assert 'Vật tư phát sinh · không tính khách' in page and 'Phát sinh · báo lại khách' in page and 'Không sửa được' in page
    r = client.post(f'/tho/phieu/{tid}/khong-sua-duoc', data={'csrf': csrf, 'version': str(ver), 'reason': 'Ngoài khả năng'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/tho?khong_sua=1'
    assert 'Đã báo không sửa được' in client.get('/tho?khong_sua=1').text
    client.cookies.clear()
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{tid}').text
    assert 'không sửa được' in page and 'Ngoài khả năng' in page and 'id="workshop-decline"' in page
    db.expire_all()
    assert db.get(ServiceTicket, tid).quote_approved_at is not None

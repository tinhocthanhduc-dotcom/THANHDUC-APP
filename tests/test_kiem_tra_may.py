"""Báo giá sửa chữa đợt 3 (TUẤN duyệt mẫu M3/M4 14/11/2026): thợ kiểm tra máy, điều phối áp báo giá chính thức.

Điều kiện xong: cấp tự tính theo số phương án chính, chọn cấp khác phải có lý do; thợ không thấy giá; áp giá ghi mỗi dịch vụ
một dòng phí trọn gói (tên khách, không linh kiện), giữ dòng khác, áp lại thay dòng cũ không nhân đôi, đổi giá bỏ "khách đồng ý";
thiếu giá không áp; dưới sàn lãi chỉ giám đốc áp; lãi chỉ giám đốc/kế toán thấy; hoa hồng thợ = gói − giá vốn linh kiện trong gói.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from starlette.datastructures import FormData

from app import commissions, kpi
from app import price_book as pb
from app import repair_inspection as ri
from app import service_charges, service_tickets
from app import service_workshop as svc
from app.errors import BusinessError
from app.models import AuditLog, RepairGroup, RepairInspection, ServiceStep
from app.schemas import ServiceCharges
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people, NOW  # noqa: F401
from test_web import service_login
from test_xg1_workshop import holding, workshop  # noqa: F401


JAN_AT = datetime(2027, 1, 1, 9, tzinfo=ri.TZ)


def err(code, fn, *a, **kw):
    with pytest.raises(BusinessError) as e:
        fn(*a, **kw)
    assert e.value.code == code
    return e.value


def book(db, boss):
    a4 = db.scalar(select(RepairGroup).where(RepairGroup.code == 'LASER_A4_DON'))
    fuser = pb.save_service(db, boss, dict(kind='GOI', name='Sửa chữa cụm sấy', active='1', option_rows='3',
                                           customer_name='Sửa chữa cụm sấy máy {model}', description='Khắc phục lỗi cụm sấy.',
                                           opt_name_0='Thay bao lụa sấy', opt_main_0='1', opt_name_1='Thay rulo ép', opt_main_1='1',
                                           opt_name_2='Thay bạc ép',
                                           **{f'g{a4.id}_p1': '250.000', f'g{a4.id}_p2': '450.000', f'g{a4.id}_ch2': '600.000',
                                              f'g{a4.id}_bh2': '3', f'g{a4.id}_p3': '650.000', f'g{a4.id}_bh3': '3'}))
    feed = pb.save_service(db, boss, dict(kind='GOI', name='Sửa chữa bộ kéo giấy', active='1', option_rows='0',
                                          **{f'g{a4.id}_p1': '150.000'}))
    pb.save_model(db, boss, 'Canon LBP 2900', a4.id)
    opts = {o.name: o.id for o in pb.options(db, fuser.id)}
    return fuser, feed, opts


def form(fuser, opts, picks=(), level='', reason='', parts=(), genuine=False, extra=(), model='canon lbp 2900', version=''):
    items = [('model', model), ('dv', str(fuser.id)), ('cap_%d' % fuser.id, level), ('ly_do_%d' % fuser.id, reason), ('version', version)]
    items += [('opt_%d' % fuser.id, str(opts[p])) for p in picks]
    for code, qty in parts:
        items += [('lk_%d' % fuser.id, code), ('sl_%d' % fuser.id, str(qty))]
    if genuine:
        items.append(('ch_%d' % fuser.id, '1'))
    return FormData(items + list(extra))


def test_inspection_levels_reason_and_permissions(db, workshop, make_user):
    p, a, b, t, eq = workshop
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    tech = p['technician']
    # Bỏ trống cấp: app tự ra cấp theo số phương án chính (1 chính + 1 phụ → cấp 2).
    row = ri.save(db, tech, t.id, form(fuser, opts, ['Thay bao lụa sấy', 'Thay bạc ép'], parts=[('td-0001', 2)]))
    item = row.items[0]
    assert (row.model_name, row.model_id is not None) == ('Canon LBP 2900', True)
    assert (item.level_auto, item.level, item.options, item.parts) == (2, 2, ['Thay bao lụa sấy', 'Thay bạc ép'], [dict(code='TD-0001', qty=2)])
    # Chọn cấp khác app tính mà không ghi lý do: chặn, giữ bản cũ.
    err('LEVEL_REASON', ri.save, db, tech, t.id, form(fuser, opts, ['Thay bao lụa sấy'], level='3', version=str(row.version)))
    db.rollback()
    assert db.get(RepairInspection, t.id).items[0].level == 2
    row = ri.save(db, tech, t.id, form(fuser, opts, ['Thay bao lụa sấy'], level='3', reason='Cháy cả cụm, thay cụm', version=str(row.version)))
    assert (row.items[0].level_auto, row.items[0].level, row.items[0].level_reason) == (2, 3, 'Cháy cả cụm, thay cụm')
    # Phiên bản cũ: không ghi đè người vừa sửa.
    err('STALE', ri.save, db, tech, t.id, form(fuser, opts, [], version='1'))
    db.rollback()
    err('BAD_PART', ri.save, db, tech, t.id, form(fuser, opts, [], parts=[('TD-9999', 1)]))
    db.rollback()
    err('BAD_QTY', ri.save, db, tech, t.id, form(fuser, opts, [], parts=[('TD-0001', '1,5')]))
    db.rollback()
    err('NO_SERVICE', ri.save, db, tech, t.id, FormData([('model', 'Canon LBP 2900')]))
    db.rollback()
    # Kinh doanh, kho không ghi được kết quả kiểm tra.
    for role in ('sales', 'warehouse'):
        assert err('FORBIDDEN', ri.save, db, p[role], t.id, form(fuser, opts)).status == 403
        db.rollback()
    assert db.scalar(select(AuditLog).where(AuditLog.action == 'repair_inspection.save', AuditLog.entity_id == str(t.id)))
    rows = ri.waiting_rows(db)
    assert [(r['ticket'].id, r['stage']) for r in rows] == [(t.id, 'Chờ điều phối báo giá')]


def test_apply_writes_one_package_line_keeps_others_and_reapplies(db, workshop, make_user):
    p, a, b, t, eq = workshop
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    disp = p['dispatcher']
    service_charges.set_charges(db, disp, t.id, ServiceCharges(version=t.version, billing='THU_TIEN', lines=[
        dict(kind='PHI', description='Phí vệ sinh', qty=1, unit_price=50000, vat_rate=8)]))
    err('NO_INSPECTION', ri.apply, db, disp, t.id, t.version, {})
    db.rollback()
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy'], parts=[('TD-0001', 1)]))
    # Thợ không áp giá.
    assert err('FORBIDDEN', ri.apply, db, p['technician'], t.id, t.version, {}).status == 403
    db.rollback()
    ri.apply(db, disp, t.id, t.version, {})
    db.refresh(t)
    lines = sorted((x.kind, x.description, x.unit_price, x.vat_amount) for x in t.lines)
    # Một dòng trọn gói theo tên khách, không có linh kiện; dòng khác giữ nguyên.
    assert lines == [('PHI', 'Phí vệ sinh', 50000, 4000), ('PHI', 'Sửa chữa cụm sấy máy Canon LBP 2900', 450000, 36000)]
    row = db.get(RepairInspection, t.id)
    assert row.applied_at and row.warranty_months == 3 and row.applied_lines == ['Sửa chữa cụm sấy máy Canon LBP 2900']
    assert ri.waiting_rows(db)[0]['stage'] == 'Đã báo giá, chưa gửi khách'
    svc.quote_approve(db, disp, t.id, t.version, service_tickets._now().date() + timedelta(days=1), 'Khách đồng ý')
    assert t.quote_approved_at
    # Khách chọn chính hãng (PA2): áp lại thay dòng cũ, không nhân đôi; đổi giá thì bỏ "khách đồng ý".
    ri.apply(db, disp, t.id, t.version, {fuser.id: True})
    db.refresh(t)
    assert sorted((x.description, x.unit_price) for x in t.lines) == [('Phí vệ sinh', 50000), ('Sửa chữa cụm sấy máy Canon LBP 2900', 600000)]
    assert t.quote_approved_at is None and db.get(RepairInspection, t.id).items[0].genuine
    # Thợ sửa kết quả sau khi báo giá: phải áp lại.
    row = ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy', 'Thay rulo ép']))
    assert row.applied_at is None
    # Thiếu giá (kéo giấy chỉ có cấp 1, chọn cấp 2): không áp, phiếu giữ nguyên.
    before = sorted((x.description, x.unit_price) for x in t.lines)
    ri.save(db, p['technician'], t.id, FormData([('model', 'Canon LBP 2900'), ('dv', str(feed.id)), ('cap_%d' % feed.id, '2'),
                                                   ('ly_do_%d' % feed.id, 'Mòn bánh xe, thay cả bộ')]))
    err('NO_PRICE', ri.apply, db, disp, t.id, t.version, {})
    db.rollback()
    db.refresh(t)
    assert sorted((x.description, x.unit_price) for x in t.lines) == before
    assert db.scalar(select(AuditLog).where(AuditLog.action == 'repair_inspection.apply', AuditLog.entity_id == str(t.id)))


def test_below_floor_needs_director(db, workshop, make_user, monkeypatch):
    p, a, b, t, eq = workshop
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy'], parts=[('TD-0001', 1)]))
    monkeypatch.setattr(ri, 'FLOOR_PCT', 101)
    err('BELOW_FLOOR', ri.apply, db, p['dispatcher'], t.id, t.version, {})
    db.rollback()
    db.refresh(t)
    assert not t.lines
    ri.apply(db, boss, t.id, t.version, {})
    db.refresh(t)
    assert [x.unit_price for x in t.lines] == [450000]


def test_web_technician_inspects_dispatcher_applies_margin_hidden(client, db, workshop, make_user):
    p, a, b, t, eq = workshop
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    csrf = service_login(client, p['technician'])
    page = client.get('/kiem-tra-may')
    assert page.status_code == 200 and t.number in page.text and 'Chờ kiểm tra' in page.text
    page = client.get(f'/kiem-tra-may/{t.id}')
    assert page.status_code == 200 and 'Sửa chữa cụm sấy' in page.text and 'Thay bao lụa sấy' in page.text
    assert '450.000' not in page.text  # thợ không thấy giá
    r = client.post(f'/kiem-tra-may/{t.id}', data={'csrf': csrf, 'model': 'Canon LBP 2900', 'dv': str(fuser.id),
                                                   f'opt_{fuser.id}': str(opts['Thay bao lụa sấy']), f'cap_{fuser.id}': '3'},
                    follow_redirects=False)
    assert r.status_code == 400 and 'LEVEL_REASON' in r.text and 'Thay bao lụa sấy' in r.text
    r = client.post(f'/kiem-tra-may/{t.id}', data={'csrf': csrf, 'model': 'Canon LBP 2900', 'dv': str(fuser.id),
                                                   f'opt_{fuser.id}': str(opts['Thay bao lụa sấy']), f'lk_{fuser.id}': 'TD-0001',
                                                   f'sl_{fuser.id}': '1'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith('/kiem-tra-may?da_kiem_tra=')
    client.cookies.clear()
    csrf = service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}')
    assert page.status_code == 200 and 'Báo giá sửa chữa' in page.text and 'Áp vào báo giá' in page.text
    assert 'Sửa chữa cụm sấy máy Canon LBP 2900' in page.text and 'Lãi trực tiếp' not in page.text
    db.refresh(t)
    r = client.post(f'/kiem-tra-may/{t.id}/ap-gia', data={'csrf': csrf, 'version': str(t.version), f'pa_{fuser.id}': '1'}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == f'/dich-vu/{t.id}?da_ap_gia=1'
    db.refresh(t)
    assert [x.unit_price for x in t.lines] == [450000]
    r = client.post(f'/kiem-tra-may/{t.id}/ap-gia', data={'csrf': csrf, 'version': '1'}, follow_redirects=False)
    assert r.status_code == 409 and 'VERSION_CONFLICT' in r.text
    client.cookies.clear()
    service_login(client, boss)
    page = client.get(f'/dich-vu/{t.id}')
    assert 'Lãi trực tiếp' in page.text and 'vốn' in page.text
    client.cookies.clear()
    service_login(client, p['sales'])
    assert client.get('/kiem-tra-may').status_code == 403


def test_commission_base_is_package_minus_part_cost(db, workshop, make_user, monkeypatch):
    p, a, b, t, eq = workshop
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    disp = p['dispatcher']
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    ri.apply(db, disp, t.id, t.version, {})
    db.refresh(t)
    # Linh kiện trong gói: dòng vật tư giá 0 (xuất kho lúc Sửa xong), giá vốn 1.000/cái × 2.
    lines = [service_charges.line_input(x) for x in t.lines] + [dict(kind='VAT_TU', description='Bao lụa', product_code='TD-0001',
                                                                    warehouse_code='K58', qty=2, unit_price=0, vat_rate=8)]
    service_charges.set_charges(db, disp, t.id, ServiceCharges(version=t.version, billing='THU_TIEN', lines=lines))
    svc.quote_approve(db, disp, t.id, t.version, service_tickets._now().date() + timedelta(days=1), 'Khách đồng ý')
    photo(db, p, t, 'SAU', who='dispatcher')
    svc.workshop_repaired(db, disp, t.id, t.version, repaired_by=p['technician'].id, mode='FULL', reason='', fault='Rách bao lụa',
                          fix='Thay bao lụa', warranty_months=3, warranty_scope='')
    db.add(ServiceStep(ticket_id=t.id, action='transition', from_status='TAM_DUNG', to_status='HOAN_THANH', actor_id=disp.id,
                       assignee_id=p['technician'].id, at=JAN_AT + timedelta(hours=2)))
    t.created_at = JAN_AT  # hoa hồng tính từ 01/2027 (TD_COMMISSION_START mặc định)
    db.commit()
    monkeypatch.setattr(commissions, '_now', lambda: JAN_AT + timedelta(days=3))
    monkeypatch.setattr(kpi, '_now', lambda: JAN_AT + timedelta(days=3))
    month = JAN_AT.date()
    row = next(r for r in commissions.draft(db, month) if r['source'] == 'PHIEU_DV' and r['ticket_id'] == t.id)
    assert row['base'] == 450000 - 2000


def test_codex_drafts_customer_description_with_safety_checks(db, workshop, make_user, monkeypatch):
    """TUẤN 14/11: ô Mô tả kết quả cho khách do Codex trên Z2 soạn sẵn; số lạ/mã hàng bị bỏ, quá 3 phút dùng mẫu, điều phối sửa được."""
    from types import SimpleNamespace
    from app import zalo_agent
    from app.models import ZaloAiJob
    p, a, b, t, eq = workshop
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    two = FormData([('model', 'Canon LBP 2900'), ('dv', str(fuser.id)), ('opt_%d' % fuser.id, str(opts['Thay bao lụa sấy'])),
                    ('dv', str(feed.id))])
    # Z2 tắt: không gửi lượt, dùng mẫu bảng giá, lưu vẫn được.
    monkeypatch.setattr(zalo_agent, 'z2_alive', lambda s: False)
    row = ri.save(db, p['technician'], t.id, two)
    assert row.ai_job_id is None and ri.descriptions(db, row)['texts'][fuser.id] == ('Khắc phục lỗi cụm sấy.', 'mau')
    monkeypatch.setattr(zalo_agent, 'z2_alive', lambda s: True)
    monkeypatch.setattr(zalo_agent, 'config', lambda s: SimpleNamespace(staff_engine='codex'))
    row = ri.save(db, p['technician'], t.id, FormData(list(two.multi_items()) + [('note', 'bao lụa rách'), ('version', str(row.version))]))
    job = db.get(ZaloAiJob, row.ai_job_id)
    assert job and job.log_id is None and 'Canon LBP 2900' in job.prompt and 'Thay bao lụa sấy' in job.prompt
    assert ri.ai_state(db, row)['status'] == 'DANG'
    zalo_agent.take_jobs(db)
    zalo_agent.answer_job(db, job.id, '```json\n{"1": "Máy Canon LBP 2900 in lại rõ nét, hết nhăn giấy.", "2": "Máy kéo giấy ổn định trong 2 ngày."}\n```')
    texts = ri.descriptions(db, row)['texts']
    # Số "2 ngày" không có trong tên model: bỏ, dùng mẫu (trống); số trong tên model được giữ.
    assert texts[fuser.id] == ('Máy Canon LBP 2900 in lại rõ nét, hết nhăn giấy.', 'ai') and texts[feed.id] == ('', 'mau')
    assert ri._clean_note('Thay bao lụa TD-0001 cho máy', 'x') is None
    # Điều phối sửa mô tả rồi áp: bản duyệt được lưu, lưu lại kiểm tra không đổi thì giữ bản duyệt.
    db.query(ZaloAiJob).filter_by(id=job.id).update(dict(answer='{"1": "Cụm sấy hoạt động lại bình thường."}'))
    db.commit()
    ri.apply(db, p['dispatcher'], t.id, t.version, {}, {fuser.id: 'Cụm sấy được sửa, máy in lại bình thường.'})
    row = db.get(RepairInspection, t.id)
    notes = {i.service_id: i.customer_note for i in row.items}
    assert notes == {fuser.id: 'Cụm sấy được sửa, máy in lại bình thường.', feed.id: ''}
    row = ri.save(db, p['technician'], t.id, FormData(list(two.multi_items()) + [('note', 'bao lụa rách'), ('version', str(row.version))]))
    assert {i.service_id: i.customer_note for i in row.items}[fuser.id] == 'Cụm sấy được sửa, máy in lại bình thường.'
    # Quá 3 phút chưa xong: báo lỗi, dùng mẫu.
    job2 = ZaloAiJob(log_id=None, engine='codex', prompt='x')
    db.add(job2)
    db.flush()
    job2.created_at = ri._now() - timedelta(minutes=4)
    row.ai_job_id = job2.id
    db.commit()
    assert ri.ai_state(db, row)['status'] == 'LOI'

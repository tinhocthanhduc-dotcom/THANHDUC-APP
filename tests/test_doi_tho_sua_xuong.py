"""Đổi thợ sửa máy ở xưởng (TUẤN duyệt mẫu PV1anDADorn327t1Wdjt77 20/11/2026)."""
import pytest

from app import my_work, repair_quotes as rq, service_workshop
from app.errors import BusinessError
from app.models import ServiceStep, ServiceTicket
from test_bang_xuong import _agreed, row
from test_gui_bao_gia_sua import quoted, ready, tomorrow  # noqa: F401
from test_kiem_tra_may import book, form
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people, NOW  # noqa: F401
from test_web import service_login
from test_xg1_workshop import holding, workshop  # noqa: F401
from app import repair_inspection as ri


def test_change_repairer_keeps_workshop_and_moves_work(db, workshop, make_user):
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    disp = p['dispatcher']
    tho_a = make_user('dt.tho_a', 'technician')
    tho_b = make_user('dt.tho_b', 'technician')
    t, *_ = _agreed(db, p, make_user, tho_a)
    assert service_workshop.can_change_repairer(db, disp, t)
    assert not service_workshop.can_change_repairer(db, tho_a, t)
    # thợ không đổi được, thiếu lý do / cùng thợ bị chặn
    with pytest.raises(BusinessError) as e:
        service_workshop.change_repairer(db, tho_a, t.id, t.version, tho_b.id, 'Thợ nghỉ')
    assert e.value.status == 403
    with pytest.raises(BusinessError) as e:
        service_workshop.change_repairer(db, disp, t.id, t.version, tho_b.id, '')
    assert e.value.code == 'REASON_REQUIRED'
    with pytest.raises(BusinessError) as e:
        service_workshop.change_repairer(db, disp, t.id, t.version, tho_b.id, 'Khác', 'x')
    assert e.value.code == 'REASON_REQUIRED'
    with pytest.raises(BusinessError) as e:
        service_workshop.change_repairer(db, disp, t.id, t.version, tho_a.id, 'Thợ nghỉ')
    assert e.value.code == 'SAME_ASSIGNEE'
    t2, old = service_workshop.change_repairer(db, disp, t.id, t.version, tho_b.id, 'Thợ bận việc khác', 'đi công trình')
    assert old == tho_a.id
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.assignee_id == tho_b.id and t.status == 'TAM_DUNG' and t.hold_reason == 'CHO_SUA_XUONG' and t.quote_approved_at is not None
    step = db.query(ServiceStep).filter_by(ticket_id=t.id).order_by(ServiceStep.id.desc()).first()
    assert step.action == 'reassign' and step.assignee_id == tho_b.id
    assert step.note.startswith(f'Đổi thợ sửa: {tho_a.full_name} → {tho_b.full_name} · Thợ bận việc khác · đi công trình')
    assert [r['id'] for r in my_work.filtered(db, tho_b, 'service_ws_repair')['rows']] == [t.id]
    assert my_work.filtered(db, tho_a, 'service_ws_repair')['rows'] == []
    r = row(db, disp, t)
    assert r['stage'] == 's4' and r['extra']['label'] == 'Đổi thợ'
    # thợ mới báo sửa xong được, thợ cũ thì không
    with pytest.raises(BusinessError) as e:
        service_workshop.report_failed(db, tho_a, t.id, t.version, 'Không có linh kiện')
    assert e.value.status == 403
    service_workshop.report_failed(db, tho_b, t.id, t.version, 'Không có linh kiện')
    db.refresh(t)
    assert not service_workshop.can_change_repairer(db, disp, t)
    with pytest.raises(BusinessError) as e:
        service_workshop.change_repairer(db, disp, t.id, t.version, tho_a.id, 'Thợ nghỉ')
    assert e.value.code == 'ALREADY_REPORTED'


def test_not_before_customer_agrees(db, workshop, make_user):
    p, a, b, t, eq = workshop
    tho_b = make_user('dt.tho_b', 'technician')
    with pytest.raises(BusinessError) as e:
        service_workshop.change_repairer(db, p['dispatcher'], t.id, t.version, tho_b.id, 'Thợ nghỉ')
    assert e.value.code == 'BAD_STATUS'


def test_web_give_later_then_assign_and_change(client, db, workshop, make_user):
    """Khách đồng ý chọn "Để kho giao sau" (không chọn thợ): giao thợ sửa trên phiếu không cần lý do; đổi lại thì cần lý do."""
    p, a, b, t, eq = workshop
    disp = p['dispatcher']
    boss = make_user('bx.boss', 'director')
    tho_b = make_user('dt.tho_b', 'technician')
    fuser, feed, opts = book(db, boss)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    ri.apply(db, disp, t.id, t.version, {})
    db.refresh(t)
    v = rq.create(db, disp, t.id, t.version)
    rq.mark_sent(db, disp, v.id, 'COPY')
    db.refresh(t)
    rq.agree(db, disp, v.id, t.version, 'PA1', 'ZALO', 'Anh Nam', tomorrow(), '', None)
    db.refresh(t)
    t.assignee_id, t.counter_intake = None, True  # máy khách mang tới quầy: chưa có thợ
    db.commit()
    assert row(db, disp, t)['action']['label'] == 'Giao thợ sửa'
    csrf = service_login(client, disp)
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'id="workshop-change-tech"' in page and 'Chưa giao thợ sửa' in page and 'value="Thợ nghỉ"' not in page
    res = client.post(f'/dich-vu/{t.id}/xuong/doi-tho', data={'csrf': csrf, 'version': str(t.version), 'assignee_id': str(tho_b.id)},
                      follow_redirects=False)
    assert res.status_code == 303 and 'doi_tho_sua=1' in res.headers['location']
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.assignee_id == tho_b.id and t.hold_reason == 'CHO_SUA_XUONG'
    page = client.get(f'/dich-vu/{t.id}?doi_tho_sua=1').text
    assert f'Đã giao máy cho {tho_b.full_name} sửa' in page
    assert 'Đổi thợ sửa' in page and 'value="Thợ nghỉ"' in page
    res = client.post(f'/dich-vu/{t.id}/xuong/doi-tho', data={'csrf': csrf, 'version': str(t.version), 'assignee_id': str(p['technician'].id)})
    assert res.status_code == 400 and 'Chọn lý do đổi thợ sửa' in res.text
    db.expire_all()
    assert db.get(ServiceTicket, t.id).assignee_id == tho_b.id


def test_zalo_to_new_and_old_technician(db, workshop, make_user, monkeypatch):
    from app import config, zalo_bot
    from app.models import ZaloChat
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    tho_a = make_user('dt.tho_a', 'technician')
    tho_b = make_user('dt.tho_b', 'technician')
    t, *_ = _agreed(db, p, make_user, tho_a)
    for u in (tho_a, tho_b):
        db.add(ZaloChat(chat_id=f'c-{u.id}', chat_type='PRIVATE', display_name=u.full_name, user_id=u.id))
    db.commit()
    service_workshop.change_repairer(db, p['dispatcher'], t.id, t.version, tho_b.id, 'Thợ nghỉ')
    assert service_workshop.notify_repairer(t.id, tho_a.id) == 0  # chưa cấu hình bot: không gửi
    settings = config.get_settings().model_copy(update={'zalo_bot_token': 'token'})
    monkeypatch.setattr(config, 'get_settings', lambda: settings)
    sent = []
    monkeypatch.setattr(zalo_bot, 'send_text', lambda session, chat, text, user, kind: sent.append((chat.chat_id, text, kind)))
    assert service_workshop.notify_repairer(t.id, tho_a.id) == 2
    by = {c: text for c, text, kind in sent}
    assert 'được giao sửa máy ở xưởng' in by[f'c-{tho_b.id}'] and f'/tho/phieu/{t.id}#sua-xuong' in by[f'c-{tho_b.id}']
    assert 'Thợ nghỉ' in by[f'c-{tho_b.id}'] and t.number in by[f'c-{tho_b.id}']
    assert f'đã chuyển cho {tho_b.full_name} sửa' in by[f'c-{tho_a.id}']

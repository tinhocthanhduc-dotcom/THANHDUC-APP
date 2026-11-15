"""Đội AI: Trưởng nhóm AI đọc tin Zalo (TUẤN duyệt 15/11/2026: soạn nháp người bấm 1 lần, Q5 đọc nhóm, Q6 hạn 30 phút, Q7 chỉ Z2)."""
import re
from datetime import datetime

import pytest
from sqlalchemy import select

from app import my_work, service_tickets, zalo_doc_tin as dt, zalo_phieu_nhap as pn
from app.models import Customer, ServiceTicket, VirtualAgentLog, ZaloAiJob, ZaloChat, ZaloMessage, ZaloTicketDraft, ZaloTriage
from test_web import csrf_of, service_login


@pytest.fixture
def setup(db, make_user):
    db.add(Customer(code='KTD-000001', name='Minh Phát', search_text='ktd-000001 minh phat 0903111222', address='58 Mã Lò'))
    db.add(Customer(code='KTD-000002', name='Sao Mai', search_text='ktd-000002 cong ty sao mai 0903777888'))
    db.commit()
    users = dict(dp=make_user('dt.dp', 'dispatcher'), sales=make_user('dt.sales', 'sales'), kt=make_user('dt.kt', 'finance'),
                 tho=make_user('dt.tho', 'technician'), gd=make_user('dt.gd', 'director'))
    linked = ZaloChat(chat_id='c-minh', chat_type='PRIVATE', display_name='Chị Hạnh', customer_code='KTD-000001')
    loose = ZaloChat(chat_id='c-loose', chat_type='PRIVATE', display_name='Anh Dũng')
    group = ZaloChat(chat_id='g-1', chat_type='GROUP', display_name='Văn phòng Sao Mai')
    staff = ZaloChat(chat_id='c-staff', chat_type='PRIVATE', display_name='Thợ', user_id=users['tho'].id)
    db.add_all([linked, loose, group, staff])
    db.commit()
    return dict(users=users, linked=linked, loose=loose, group=group, staff=staff)


def msg(db, chat, text, name='Khách'):
    m = ZaloMessage(chat_pk=chat.id, direction='IN', kind='text', text=text, sender_name=name, status='NHAN',
                    message_at=datetime.now(dt.TZ))
    db.add(m)
    db.commit()
    return m


def answer(db, triage, text):
    job = db.get(ZaloAiJob, triage.job_id)
    job.status, job.answer = 'XONG', text
    db.commit()


def test_enqueue_skips_small_talk_staff_and_drafted(db, setup):
    s = setup
    ok = msg(db, s['linked'], 'Máy in phòng kế toán kẹt giấy liên tục, qua sớm giúp chị')
    msg(db, s['linked'], 'ok em')
    msg(db, s['staff'], 'Tra tồn mực 107A')
    grp = msg(db, s['group'], 'Bên mình hết mực máy HP 107a, mai mang qua 2 hộp nhé', 'Anh Dũng')
    assert dt.enqueue(db) == 2
    rows = {r.message_id: r for r in db.scalars(select(ZaloTriage))}
    assert set(r.status for r in rows.values()) == {'CHO_AI', 'BO_QUA'} and len(rows) == 3   # tin nhân viên không đọc
    job = db.get(ZaloAiJob, rows[ok.id].job_id)
    assert job.engine == 'claude' and 'kẹt giấy' in job.prompt and 'Minh Phát' in job.prompt
    assert 'nhóm "Văn phòng Sao Mai"' in db.get(ZaloAiJob, rows[grp.id].job_id).prompt
    assert dt.enqueue(db) == 0


def test_repair_message_becomes_draft_and_one_tap_creates_ticket(db, setup, monkeypatch):
    s = setup
    m = msg(db, s['linked'], 'Máy in phòng kế toán kẹt giấy liên tục, qua sớm giúp chị')
    dt.enqueue(db)
    t = db.scalar(select(ZaloTriage))
    answer(db, t, 'Đây: {"loai":"SUA","tom_tat":"Kẹt giấy, cần thợ","mo_ta":"Kẹt giấy liên tục phòng kế toán","muc_do":"GAP"}')
    assert dt.collect(db) == 1
    db.expire_all()
    t = db.get(ZaloTriage, t.id)
    draft = db.get(ZaloTicketDraft, t.draft_id)
    assert t.status == 'XONG' and t.done_at is not None and t.customer_code == 'KTD-000001' and t.customer_sure
    assert draft.from_triage and draft.customer_sure and draft.kind == 'SUA_TAI_CHO' and draft.priority == 'GAP' and draft.message_id == m.id
    assert db.scalar(select(VirtualAgentLog).where(VirtualAgentLog.action == 'DOC_TIN')).reason.startswith('Tạo phiếu nháp')
    ticket = pn.create_and_assign(db, s['users']['dp'], draft.id, draft.version, s['users']['tho'].id)
    db.expire_all()
    ticket = db.get(ServiceTicket, ticket.id)
    assert ticket.assignee_id == s['users']['tho'].id and ticket.status == 'DA_GIAO' and ticket.customer_code == 'KTD-000001'
    assert db.get(ZaloTicketDraft, draft.id).status == 'DA_TAO'
    with pytest.raises(Exception):
        pn.create_and_assign(db, s['users']['dp'], draft.id, draft.version, s['users']['tho'].id)


def test_price_question_goes_to_sales_with_guessed_customer(db, setup, client):
    s = setup
    msg(db, s['loose'], 'Báo giá giúp anh 5 hộp mực Canon 337, số anh 0903 777 888')
    dt.enqueue(db)
    t = db.scalar(select(ZaloTriage))
    answer(db, t, '{"loai":"HOI_GIA","tom_tat":"Hỏi giá 5 hộp Canon 337","so_dien_thoai":"0903777888"}')
    dt.collect(db)
    db.expire_all()
    t = db.get(ZaloTriage, t.id)
    assert t.owner_id == s['users']['sales'].id and t.customer_code == 'KTD-000002' and not t.customer_sure and t.done_at is None
    assert t.due_at is not None
    group = [g for g in my_work.collect(db, s['users']['sales']) if g['key'] == 'zalo_inbox'][0]
    assert group['count'] == 1 and group['rows'][0]['customer'] == 'Sao Mai' and group['rows'][0]['link'] == f'/tin-zalo#tz-{t.id}'
    csrf = service_login(client, s['users']['sales'])
    page = client.get('/tin-zalo').text
    assert 'Hỏi giá 5 hộp Canon 337' in page and 'customer_code=KTD-000002' in page and 'kiểm lại' in page
    form = page[page.index(f'action="/tin-zalo/{t.id}/xong"'):]
    key = re.search(r'name="form_key" value="([^"]+)"', form).group(1)
    assert client.post(f'/tin-zalo/{t.id}/xong', data=dict(csrf=csrf_of(page), form_key=key), follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(ZaloTriage, t.id).done_by == s['users']['sales'].id
    del csrf


def test_unknown_customer_repair_and_ai_error_go_to_dispatcher(db, setup):
    s = setup
    msg(db, s['loose'], 'Máy in nhà mình không lên nguồn, bên em qua xem giúp')
    msg(db, s['loose'], 'Cho chị hỏi bên em có làm thứ 7 không ạ')
    dt.enqueue(db)
    a, b = db.scalars(select(ZaloTriage).order_by(ZaloTriage.id)).all()
    answer(db, a, '{"loai":"SUA","tom_tat":"Máy không lên nguồn"}')
    job = db.get(ZaloAiJob, b.job_id)
    job.status, job.error = 'LOI', 'quá giờ'
    db.commit()
    dt.collect(db)
    db.expire_all()
    a, b = db.get(ZaloTriage, a.id), db.get(ZaloTriage, b.id)
    assert a.draft_id is None and a.owner_id == s['users']['dp'].id and 'gắn khách' in dt.label(a)
    assert b.status == 'LOI' and b.owner_id == s['users']['dp'].id


def test_due_after_work_hours(db):
    sat_evening = datetime(2026, 11, 21, 18, 0, tzinfo=dt.TZ)
    start = dt.get_settings().work_start
    due = dt.due_after(db, sat_evening)
    assert due.date().isoformat() == '2026-11-23' and due.astimezone(dt.TZ).time() == (datetime.combine(due.date(), start) + dt.DUE).time()
    mon = datetime(2026, 11, 16, 9, 0, tzinfo=dt.TZ)
    assert dt.due_after(db, mon) == datetime(2026, 11, 16, 9, 30, tzinfo=dt.TZ)


def test_draft_page_shows_one_tap(db, setup, client, monkeypatch):
    s = setup
    msg(db, s['linked'], 'Máy in kẹt giấy, qua giúp chị')
    dt.enqueue(db)
    t = db.scalar(select(ZaloTriage))
    answer(db, t, '{"loai":"SUA","tom_tat":"Kẹt giấy","mo_ta":"Kẹt giấy"}')
    dt.collect(db)
    db.expire_all()
    draft_id = db.get(ZaloTriage, t.id).draft_id
    monkeypatch.setattr(pn, 'suggest', lambda session, row, now=None: dict(id=s['users']['tho'].id, name='Dt Tho', why='rảnh, gần'))
    service_login(client, s['users']['dp'])
    page = client.get(f'/phieu-nhap-zalo/{draft_id}').text
    assert 'Tạo phiếu và giao Tho' in page and 'Sửa rồi tạo' in page and 'Trưởng nhóm AI đọc tin này' in page
    form = page[page.index('tao-va-giao'):]
    key = re.search(r'name="form_key" value="([^"]+)"', form).group(1)
    draft = db.get(ZaloTicketDraft, draft_id)
    res = client.post(f'/phieu-nhap-zalo/{draft_id}/tao-va-giao', data=dict(csrf=csrf_of(page), form_key=key, version=str(draft.version),
                                                                          tho=str(s['users']['tho'].id)), follow_redirects=False)
    assert res.status_code == 303 and '/dich-vu/' in res.headers['location']
    service_tickets  # noqa: B018 - giữ import cho rõ phân hệ

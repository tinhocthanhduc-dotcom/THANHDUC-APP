"""TUẤN 30/10/2026 (mẫu DuHangChiPhi): thợ xác nhận hàng mang theo trước khi đến khách."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import my_work, notifications, service_goods, service_tickets
from app.errors import BusinessError
from app.models import ServiceStep, ServiceTicket
from test_doi_tho_hang_theo import supplied
from test_my_work import people, purchase_people, ticket, NOW
from test_technician_stock import held
from test_web import service_login, csrf_of


def back_to_appointment(db, p, t):
    db.refresh(t)
    return service_tickets.transition(db, p['technician'], t.id, 'DA_HEN', t.version, note='Khách bận', appointment_at=NOW+timedelta(days=1))


@pytest.fixture
def waiting(db, held):
    """Phiếu đã có linh kiện cấp cho thợ, hẹn quay lại: chưa đến khách lần hai."""
    p, a, b = held
    t = ticket(db, p, 'DANG_LAM')
    supplied(db, p, t)
    db.refresh(t)
    service_tickets.transition(db, p['technician'], t.id, 'DANG_LAM', t.version)  # cấp xong phiếu ở Chờ linh kiện
    back_to_appointment(db, p, t)
    return p, t


def arrive(db, p, t, who='technician'):
    db.refresh(t)
    return service_tickets.transition(db, p[who], t.id, 'DANG_LAM', t.version)


def test_arrival_blocked_until_goods_confirmed(db, waiting):
    p, t = waiting
    s = service_goods.state(db, t)
    assert s['required'] and s['step'] is None
    assert [(r['code'], r['label']) for r in s['items']] == [('TD-0001', 'Đã cấp')]
    with pytest.raises(BusinessError) as e:
        arrive(db, p, t)
    assert e.value.code == 'GOODS_UNCONFIRMED'
    db.rollback()
    service_goods.confirm(db, p['technician'], t.id, t.version)
    step = db.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id).order_by(ServiceStep.id.desc()))
    assert step.action == 'goods_ok' and step.note.startswith('Đã nhận đủ hàng') and step.note.endswith('Hàng: TD-0001×2')
    assert arrive(db, p, t).status == 'DANG_LAM'


def test_dispatcher_and_tickets_without_goods_are_not_blocked(db, held):
    p, a, b = held
    plain = ticket(db, p, 'DA_HEN')
    assert not service_goods.state(db, plain)['required']
    assert arrive(db, p, plain).status == 'DANG_LAM'


def test_dispatcher_can_mark_arrival(db, waiting):
    p, t = waiting
    assert arrive(db, p, t, 'dispatcher').status == 'DANG_LAM'


def test_new_goods_after_confirm_need_reconfirm(db, waiting):
    p, t = waiting
    service_goods.confirm(db, p['technician'], t.id, t.version)
    arrive(db, p, t)
    supplied(db, p, t, qty=1)
    db.refresh(t); service_tickets.transition(db, p['technician'], t.id, 'DANG_LAM', t.version)
    back_to_appointment(db, p, t)
    assert service_goods.state(db, t)['step'] is None
    with pytest.raises(BusinessError) as e:
        arrive(db, p, t)
    assert e.value.code == 'GOODS_UNCONFIRMED'


def test_short_report_notifies_dispatcher_and_warehouse_and_unlocks(db, waiting):
    p, t = waiting
    key = service_goods.state(db, t)['items'][0]['key']
    with pytest.raises(BusinessError) as e:
        service_goods.confirm(db, p['technician'], t.id, t.version, [key], 'Lý do lạ')
    assert e.value.code == 'REASON_REQUIRED'
    db.rollback()
    service_goods.confirm(db, p['technician'], t.id, t.version, [key], 'Kho chưa đưa', 'Ghé kho lấy sau')
    note = db.scalar(select(ServiceStep.note).where(ServiceStep.action == 'goods_short'))
    assert note.startswith('Báo thiếu: ') and 'Kho chưa đưa. Ghé kho lấy sau' in note
    for role in ('dispatcher', 'warehouse'):
        ids = [n['id'] for n in notifications.items(db, p[role])]
        assert f'service_goods_short:{t.id}' in ids
    assert f'service_goods_short:{t.id}' not in [n['id'] for n in notifications.items(db, p['sales'])]
    arrive(db, p, t)  # báo thiếu vẫn được đi
    db.refresh(t)
    assert any(g['key'] == 'service_goods_short' and g['count'] == 1 for g in my_work.collect(db, p['dispatcher']))


def test_only_assigned_technician_confirms(db, waiting):
    p, t = waiting
    for who in ('other_tech', 'dispatcher'):
        with pytest.raises(BusinessError) as e:
            service_goods.confirm(db, p[who], t.id, t.version)
        assert e.value.code in ('FORBIDDEN', 'NOT_FOUND')
        db.rollback()


def test_technician_screen_shows_goods_card_and_locks_arrival(client, db, waiting):
    p, t = waiting
    service_login(client, p['technician'])
    html = client.get(f'/tho/phieu/{t.id}').text
    assert 'Hàng mang theo' in html and 'data-goods-locked' in html and 'Đã nhận đủ hàng' in html
    csrf = csrf_of(html)
    r = client.post(f'/tho/phieu/{t.id}/du-hang', data=dict(csrf=csrf, version=t.version, action='short', reason='Kho chưa đưa'), follow_redirects=False)
    assert r.status_code == 400 and 'Bấm vào dòng hàng còn thiếu' in r.text
    r = client.post(f'/tho/phieu/{t.id}/du-hang', data=dict(csrf=csrf, version=t.version, action='ok', return_to='tho'), follow_redirects=False)
    assert r.status_code == 303
    html = client.get(f'/tho/phieu/{t.id}').text
    assert 'data-goods-done' in html and 'data-goods-locked' not in html
    service_login(client, p['dispatcher'])
    assert 'Thợ · Đã nhận đủ hàng' in client.get(f'/dich-vu/{t.id}').text


# ---- Việc 2: chi phí phát sinh thợ tự trả ----
from datetime import date

from app import attachments, cash_docs, cash_posting, finance_catalogs, finance_mappings, purchase_reports, service_charges, service_expenses
from app.models import Attachment, CashCategory, CashDoc, Fund, ServiceExpense
from app.schemas import CashCategoryIn, CashDocIn, FundIn, ServiceCharges
from test_web import d5_image

DAY = NOW.date()


@pytest.fixture
def working(db, held):
    p, a, b = held
    return p, ticket(db, p, 'DANG_LAM')


@pytest.fixture
def fund(db, working):
    p, t = working
    finance_catalogs.create(db, p['director'], Fund, FundIn(code='TM', name='Tiền mặt', kind='TIEN_MAT'))
    finance_catalogs.create(db, p['director'], CashCategory, CashCategoryIn(code='CPTHO', name='Hoàn chi phí thợ', direction='CHI'))
    finance_mappings.set_mapping(db, p['director'], 'DANH_MUC_CHI_PHI_THO', category_code='CPTHO', version=1)
    doc = cash_docs.insert(db, p['director'], CashDocIn(kind='PT', doc_date=DAY, fund_code='TM', is_opening=True, amount=10_000_000))
    cash_posting.post_in_tx(db, p['director'], doc.id, doc.version); db.commit()
    return 'TM'


def spend(db, p, t, amount='150.000', who='technician', **kw):
    return service_expenses.create(db, p[who], t.id, kw.get('kind', 'LINH_KIEN'), kw.get('description', 'Bánh răng sấy M402'), kw.get('place', 'Tân Bình'), amount, kw.get('photo'))


def test_technician_records_expense_with_photo_and_history(db, working):
    p, t = working
    row = spend(db, p, t, photo=('hd.png', d5_image()))
    assert (row.amount, row.status, row.technician_id) == (150000, 'CHO_DUYET', p['technician'].id)
    assert db.scalar(select(Attachment.entity).where(Attachment.entity_id == str(row.id))) == 'service_expense'
    step = db.scalar(select(ServiceStep).where(ServiceStep.action == 'expense'))
    assert step.note == 'Mua linh kiện ngoài: Bánh răng sấy M402 · 150.000 đồng'


@pytest.mark.parametrize('who,status,code', [('other_tech', 'DANG_LAM', 'FORBIDDEN'), ('technician', 'DA_HEN', 'BAD_STATUS')])
def test_only_assigned_technician_after_arrival(db, held, who, status, code):
    p, a, b = held
    t = ticket(db, p, status)
    with pytest.raises(BusinessError) as e:
        spend(db, p, t, who=who)
    assert e.value.code in (code, 'NOT_FOUND')


@pytest.mark.parametrize('amount', ['0', '-5', 'abc', '100.000.001'])
def test_bad_amount(db, working, amount):
    p, t = working
    with pytest.raises(BusinessError) as e:
        spend(db, p, t, amount)
    assert e.value.code == 'BAD_AMOUNT'


def test_approved_expense_counts_in_gross_margin_and_pay_posts_one_cash_doc(db, working, fund):
    p, t = working
    first = spend(db, p, t)
    second = spend(db, p, t, '50.000', kind='GUI_XE', description='', place='')
    rejected = spend(db, p, t, '20.000', description='Nước uống')
    with pytest.raises(BusinessError) as e:
        service_expenses.decide(db, p['technician'], first.id, first.version, True)
    assert e.value.code == 'FORBIDDEN'
    db.rollback()
    for row in (first, second):
        db.refresh(row); service_expenses.decide(db, p['finance'], row.id, row.version, True)
    db.refresh(rejected)
    with pytest.raises(BusinessError) as e:
        service_expenses.decide(db, p['finance'], rejected.id, rejected.version, False, '')
    assert e.value.code == 'REASON_REQUIRED'
    db.rollback(); db.refresh(rejected)
    service_expenses.decide(db, p['finance'], rejected.id, rejected.version, False, 'Không thuộc phiếu này')
    # Hoàn thành phiếu: doanh thu 500.000, giá vốn = chi phí đã duyệt 200.000 (khoản bị từ chối không tính).
    db.refresh(t)
    attachments.add(db, p['technician'], 'service_ticket', str(t.id), 'SAU', 'sau.png', d5_image())
    service_charges.set_charges(db, p['technician'], t.id, ServiceCharges(version=t.version, billing='THU_TIEN',
        lines=[dict(kind='PHI', description='Công sửa', qty=1, unit_price=500000, vat_rate=0)]))
    db.refresh(t); service_tickets.transition(db, p['technician'], t.id, 'HOAN_THANH', t.version)
    doc = next(d for d in purchase_reports.gross_margin(db, p['finance'], DAY, DAY)['documents'] if d['kind'] == 'DV')
    assert (doc['revenue'], doc['cost'], doc['profit']) == (500000, 200000, 300000)
    cash = service_expenses.pay(db, p['finance'], [first.id, second.id], 'TM', DAY.isoformat())
    db.expire_all()
    assert (cash.kind, cash.status, cash.amount, cash.category_code, cash.source_type) == ('PC', 'DA_GHI', 200000, 'CPTHO', 'SERVICE_EXPENSE')
    assert {r.status for r in db.scalars(select(ServiceExpense).where(ServiceExpense.id.in_([first.id, second.id])))} == {'DA_HOAN'}
    with pytest.raises(BusinessError) as e:
        service_expenses.pay(db, p['finance'], [first.id], 'TM', DAY.isoformat())
    assert e.value.code == 'BAD_STATUS'
    # Đã hoàn vẫn tính giá vốn.
    assert next(d for d in purchase_reports.gross_margin(db, p['finance'], DAY, DAY)['documents'] if d['kind'] == 'DV')['cost'] == 200000


def test_pay_needs_mapping_and_single_technician(db, held):
    p, a, b = held
    t1, t2 = ticket(db, p, 'DANG_LAM'), ticket(db, p, 'DANG_LAM', who='other_tech')
    rows = [spend(db, p, t1), spend(db, p, t2, who='other_tech')]
    for r in rows:
        db.refresh(r); service_expenses.decide(db, p['finance'], r.id, r.version, True)
    finance_catalogs.create(db, p['director'], Fund, FundIn(code='TM', name='Tiền mặt', kind='TIEN_MAT'))
    with pytest.raises(BusinessError) as e:
        service_expenses.pay(db, p['finance'], [rows[0].id], 'TM', DAY)
    assert e.value.code == 'MAPPING_REQUIRED'
    db.rollback()
    with pytest.raises(BusinessError) as e:
        service_expenses.pay(db, p['finance'], [r.id for r in rows], 'TM', DAY)
    assert e.value.code == 'MIXED_TECHNICIANS'


def test_technician_deletes_own_pending_only(db, working):
    p, t = working
    row = spend(db, p, t)
    service_expenses.decide(db, p['finance'], row.id, row.version, True)
    db.refresh(row)
    with pytest.raises(BusinessError) as e:
        service_expenses.cancel(db, p['technician'], row.id, row.version)
    assert e.value.code == 'BAD_STATUS'
    db.rollback()
    other = spend(db, p, t, '10.000')
    assert service_expenses.cancel(db, p['technician'], other.id, other.version).status == 'DA_HUY'


def test_expense_screens(client, db, working, fund):
    p, t = working
    service_login(client, p['technician'])
    html = client.get(f'/tho/phieu/{t.id}').text
    assert 'Chi phí phát sinh <span>' in html and 'id="service-expense"' in html
    csrf = csrf_of(html)
    r = client.post(f'/tho/phieu/{t.id}/chi-phi', data=dict(csrf=csrf, kind='LINH_KIEN', description='Bánh răng', amount='', return_to='tho'), follow_redirects=False)
    assert r.status_code == 400 and 'Nhập số tiền' in r.text and 'value="Bánh răng"' in r.text
    r = client.post(f'/tho/phieu/{t.id}/chi-phi', data=dict(csrf=csrf, kind='LINH_KIEN', description='Bánh răng', amount='150.000', return_to='tho'),
                    files={'photo': ('hd.png', d5_image(), 'image/png')}, follow_redirects=False)
    assert r.status_code == 303
    html = client.get(f'/tho/phieu/{t.id}').text
    assert 'data-expenses' in html and '150.000' in html and 'ảnh hóa đơn' in html and 'Chờ duyệt' in html
    assert client.get('/chi-phi-tho').status_code == 403
    service_login(client, p['finance'])
    html = client.get('/chi-phi-tho').text
    assert 'Bánh răng' in html and 'Chờ duyệt <span>1</span>' in html
    row = db.scalar(select(ServiceExpense))
    assert client.post(f'/chi-phi-tho/{row.id}/duyet', data=dict(csrf=csrf_of(html), version=row.version), follow_redirects=False).status_code == 303
    html = client.get('/chi-phi-tho?tab=cho-hoan').text
    assert 'Hoàn tiền · lập phiếu chi' in html
    r = client.post('/chi-phi-tho/hoan-tien', data=dict(csrf=csrf_of(html), expense_ids=str(row.id), fund_code='TM', pay_date=DAY.isoformat()), follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith('/thu-chi/')
    assert db.scalar(select(CashDoc).where(CashDoc.source_type == 'SERVICE_EXPENSE')).amount == 150000
    service_login(client, p['dispatcher'])
    assert 'Chi phí phát sinh thợ đã trả' in client.get(f'/dich-vu/{t.id}').text

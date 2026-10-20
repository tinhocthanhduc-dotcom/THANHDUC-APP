"""UX1b: transaction thật trên PostgreSQL và form thợ qua HTTP."""
import pytest
from sqlalchemy import select, func

from app import attachments, equipment, equipment_custody, sales_receipts, service_charges, service_tickets
from app.models import AuditLog, FundLedger, SalesReceipt, ServiceTicket, ServiceVisit, StockDoc, StockLedger
from app.schemas import CustodyIntake, EquipmentIn, ServiceCharges
from test_my_work import people, purchase_people, ticket, Page
from test_technician_stock import held, transfer, balance
from test_web import service_login, d5_image


@pytest.fixture
def job(db, held):
    p, own, _ = held
    transfer(db, p)
    t = ticket(db, p, 'DANG_LAM')
    attachments.add(db, p['technician'], 'service_ticket', str(t.id), 'SAU', 'sau.png', d5_image())
    return p, own, t


def form(client, p, t, who='technician'):
    service_login(client, p[who])
    page = client.get(f'/tho/phieu/{t.id}')
    data = Page(page.text).forms['service-complete']['fields']
    return {**data, 'billing': 'THU_TIEN', 'line_kind': ['PHI', 'VAT_TU'],
        'line_description': ['Công sửa', 'Drum'], 'line_product': ['', 'TD-0001'],
        'line_qty': ['1', '1'], 'line_price': ['500.000', '100.000'],
        'warehouse_0': '', 'warehouse_1': 'K501', 'vat_0': '8', 'vat_1': '8',
        'note': 'Đã chạy thử tại khách', 'amount': '300.000', 'method': 'TIEN_MAT'}


def post(client, t, data):
    return client.post(f'/tho/phieu/{t.id}/hoan-thanh', data=data, follow_redirects=False)


def snapshot(db, t):
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    return (t.version, t.status, t.billing, t.priced_at, t.receivable,
        [(x.id, x.description, x.qty, x.unit_price, x.stock_doc_id) for x in t.lines],
        [(x.id, x.ended_at, x.outcome, x.note) for x in db.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id==t.id))],
        *(db.scalar(select(func.count()).select_from(model)) for model in (StockDoc, StockLedger, SalesReceipt, FundLedger, AuditLog)))


def unchanged_error(client, db, t, data, code):
    before = snapshot(db, t)
    response = post(client, t, data)
    assert response.status_code in (400, 409)
    assert f'data-error-code="{code}"' in response.text
    assert 'data-technician-ticket' in response.text
    from decimal import Decimal
    from app.service_charges import _round
    total=sum(_round(Decimal(qty)*int(price.replace('.','')))+_round(Decimal(_round(Decimal(qty)*int(price.replace('.',''))))*int(data[f'vat_{i}'])/100) for i,(qty,price) in enumerate(zip(data['line_qty'],data['line_price'])))
    assert 'Đã chạy thử tại khách' in response.text and f'{total:,}'.replace(',','.') in response.text
    assert snapshot(db, t) == before
    return response


@pytest.mark.parametrize('method', ['TIEN_MAT', 'CHUYEN_KHOAN'])
def test_success_charges_stock_receipt_visit_and_held_money(client, db, job, method):
    p, own, t = job
    data = form(client, p, t); data['method'] = method
    response = post(client, t, data)
    assert response.status_code == 303
    db.expire_all(); db.refresh(t)
    assert t.status == 'HOAN_THANH' and t.priced_at
    assert (t.subtotal, t.vat_total, t.total, t.receivable) == (600000, 48000, 648000, 648000)
    assert balance(db, own.code) == 2
    receipt = db.scalars(select(SalesReceipt).where(SalesReceipt.ticket_id==t.id)).one()
    assert (receipt.amount, receipt.method, receipt.status, receipt.reported_by) == (300000, method, 'CHO_XAC_NHAN', p['technician'].id)
    assert sales_receipts.held_by(db, p['technician'])['total'] == (300000 if method=='TIEN_MAT' else 0)
    visit = db.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id==t.id)).one()
    assert visit.outcome == 'XONG' and visit.ended_at and visit.note == data['note']
    assert db.query(StockDoc).filter_by(kind='PX', status='DA_GHI').count() == 1
    assert db.query(FundLedger).count() == 0


def test_photo_required_rolls_back_new_charges(client, db, held):
    p, _, _ = held; transfer(db, p); t = ticket(db, p, 'DANG_LAM')
    unchanged_error(client, db, t, form(client, p, t), 'PHOTO_REQUIRED')


def test_over_payment_rolls_back_price_stock_visit_and_preserves_input(client, db, job):
    p, _, t = job; data = form(client, p, t); data.update(amount='900.000', method='CHUYEN_KHOAN')
    response = unchanged_error(client, db, t, data, 'OVER_PAYMENT')
    assert 'value="900.000"' in response.text and 'value="CHUYEN_KHOAN" checked' in response.text


def test_negative_stock_rolls_back_everything(client, db, job):
    p, _, t = job; data = form(client, p, t); data['line_qty'] = ['1', '4']
    unchanged_error(client, db, t, data, 'NEGATIVE_STOCK')


@pytest.mark.parametrize('amount', ['', '0'])
def test_free_with_reason_without_receipt(client, db, job, amount):
    p, _, t = job; data = form(client, p, t); data.update(billing='MIEN_PHI', free_reason='Bảo hành drum', amount=amount)
    assert post(client, t, data).status_code == 303
    db.expire_all(); db.refresh(t)
    assert t.status == 'HOAN_THANH' and t.receivable == 0
    assert db.query(SalesReceipt).count() == 0


def test_free_with_collection_rolls_back(client, db, job):
    p, _, t = job; data = form(client, p, t); data.update(billing='MIEN_PHI', free_reason='Bảo hành drum')
    unchanged_error(client, db, t, data, 'BAD_STATUS')


def test_free_reason_required(client, db, job):
    p, _, t = job; data = form(client, p, t); data.update(billing='MIEN_PHI', free_reason='', amount='0')
    unchanged_error(client, db, t, data, 'REASON_REQUIRED')


def test_machine_must_be_returned(client, db, job):
    p, _, t = job
    eq = equipment.create(db, p['dispatcher'], EquipmentIn(serial='UX1B-MACHINE', product_code='TD-0001', customer_code=t.customer_code))
    t.kind = 'MANG_VE'; t.serial = eq.serial; db.commit()
    attachments.add(db, p['technician'], 'equipment_custody', str(t.id), 'NHAN_MAY', 'may.png', d5_image())
    equipment_custody.receive(db, p['technician'], t.id, CustodyIntake(version=eq.version, accessories=[], condition_note='Vỏ trầy'))
    unchanged_error(client, db, t, form(client, p, t), 'MACHINE_NOT_RETURNED')


def test_other_technician_forbidden(client, db, job):
    p, _, t = job; data = form(client, p, t)
    data['csrf'] = service_login(client, p['other_tech']); data.pop('form_key')
    before = snapshot(db, t)
    assert post(client, t, data).status_code == 403
    assert snapshot(db, t) == before


def test_stale_version_and_single_external_version_check(client, db, job, monkeypatch):
    p, _, t = job; data = form(client, p, t); data['version'] = str(t.version - 1)
    unchanged_error(client, db, t, data, 'VERSION_CONFLICT')
    data = form(client, p, t)
    calls = []; check = service_tickets._check_version
    def counted(ticket, version):
        calls.append(version); return check(ticket, version)
    monkeypatch.setattr(service_tickets, '_check_version', counted)
    assert post(client, t, data).status_code == 303
    assert calls == [int(data['version'])]


def test_same_form_key_is_exactly_once(client, db, job):
    p, _, t = job; data = form(client, p, t)
    first = post(client, t, data); before = snapshot(db, t)
    second = post(client, t, data)
    assert first.status_code == second.status_code == 303
    assert first.headers['location'] == second.headers['location']
    assert snapshot(db, t) == before
    assert db.query(SalesReceipt).count() == 1
    assert db.query(StockDoc).filter_by(kind='PX').count() == 1


def test_existing_saved_lines_survive_failed_replacement(client, db, job):
    p, _, t = job
    service_charges.set_charges(db, p['technician'], t.id, ServiceCharges(version=t.version, billing='THU_TIEN',
        lines=[dict(kind='PHI', description='Dòng cũ', qty=1, unit_price=120000, vat_rate=0)]))
    data = form(client, p, t); data['amount'] = '900.000'
    unchanged_error(client, db, t, data, 'OVER_PAYMENT')


@pytest.mark.parametrize('amount', ['-1', '1.23', 'abc', '999999999999999999999999'])
def test_invalid_amount_keeps_all_inputs(client, db, job, amount):
    p, _, t = job; data = form(client, p, t); data['amount'] = amount
    unchanged_error(client, db, t, data, 'BAD_AMOUNT')


def test_one_completion_form_and_dispatcher_keeps_separate_forms(client, db, job):
    p, _, t = job; form(client, p, t)
    page = client.get(f'/tho/phieu/{t.id}'); forms = Page(page.text).forms
    assert forms['service-complete']['action'] == f'/tho/phieu/{t.id}/hoan-thanh'
    assert 'service-charges' not in forms and 'service-HOAN_THANH' not in forms
    assert 'Bấm Lưu tiền trước' not in page.text
    service_login(client, p['dispatcher']); forms = Page(client.get(f'/dich-vu/{t.id}').text).forms
    assert 'service-charges' in forms and 'service-HOAN_THANH' in forms


def test_csrf_and_non_technician_rejected(client, db, job):
    p, _, t = job; data = form(client, p, t)
    before = snapshot(db, t)
    assert client.request('POST', f'/tho/phieu/{t.id}/hoan-thanh', data={k:v for k,v in data.items() if k!='csrf'}).status_code == 403
    assert snapshot(db, t) == before
    data['csrf'] = service_login(client, p['dispatcher']); data.pop('form_key')
    assert post(client, t, data).status_code == 403
    assert snapshot(db, t) == before


@pytest.mark.parametrize('amount', ['', '0'])
def test_paid_job_without_onsite_collection(client, db, job, amount):
    p, _, t = job; data = form(client, p, t); data['amount'] = amount
    assert post(client, t, data).status_code == 303
    db.expire_all(); db.refresh(t)
    assert t.status == 'HOAN_THANH' and t.receivable == 648000
    assert db.query(SalesReceipt).count() == 0

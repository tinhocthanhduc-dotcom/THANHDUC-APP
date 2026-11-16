"""Ghi chú phiếu AI (TUẤN duyệt 15/11/2026): lưu ý từ lịch sử máy, khách; chỉ đọc; thợ không thấy số tiền nợ."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import ghi_chu_phieu as gc, nhan_vien_ao as nva, service_tickets
from app.models import Customer, Equipment, ServiceStep, ServiceTicket, VirtualAgent
from test_web import csrf_of, service_login

N = [0]


@pytest.fixture
def p(db, make_user):
    db.add(Customer(code='KTD-000101', name='Minh Phát', search_text='minh phat', note='Gọi chị Lan trước 30 phút, vào cổng sau.'))
    db.add(Customer(code='KTD-000102', name='Khách Lạ', search_text='khach la'))
    gd = make_user('gc.gd', 'director')
    out = dict(gd=gd, dp=make_user('gc.dp', 'dispatcher'), tho=make_user('gc.tho', 'technician'), thanh=make_user('gc.thanh', 'technician'))
    db.add(Equipment(serial='KHX12345', customer_code='KTD-000101', model_name='Canon LBP 2900', created_by=gd.id))
    db.commit()
    return out


def ticket(db, p, *, status='DA_GIAO', days_ago=0, serial='KHX12345', code='KTD-000101', kind='SUA_TAI_CHO', **kw):
    N[0] += 1
    now = service_tickets._now()
    t = ServiceTicket(number=f'DV-26-9{N[0]:04d}', customer_code=code, serial=serial, kind=kind, symptom='Kẹt giấy', status=status,
                      created_by=p['dp'].id, created_at=now - timedelta(days=days_ago), assignee_id=p['tho'].id, **kw)
    db.add(t)
    db.flush()
    if status in ('HOAN_THANH', 'DA_DONG'):
        db.add(ServiceStep(ticket_id=t.id, action='complete', from_status='DANG_LAM', to_status='HOAN_THANH', actor_id=p['thanh'].id,
                           at=now - timedelta(days=days_ago)))
    db.commit()
    return t


def priced(t, amount):
    t.billing, t.subtotal, t.vat_total, t.total, t.receivable = 'THU_TIEN', amount, 0, amount, amount
    t.priced_at = service_tickets._now() - timedelta(days=80)


def test_machine_history_warranty_and_repeat(db, p):
    ticket(db, p, status='DA_DONG', days_ago=13, warranty_months=1, fault_found='Rulo kéo giấy mòn', fix_done='Thay rulo kéo giấy',
           repaired_by=p['thanh'].id)
    ticket(db, p, status='DA_DONG', days_ago=40, warranty_months=0, fault_found='Kẹt giấy', fix_done='Vệ sinh')
    t = ticket(db, p)
    rows = gc.notes(db, p['dp'], ticket=t)
    texts = [r['text'] for r in rows]
    assert rows[0]['icon'] == '🛡' and rows[0]['warn'] and 'Còn bảo hành sửa đến' in rows[0]['text']
    assert any('Thay rulo kéo giấy' in x and 'Quay lại sau 13 ngày' in x for x in texts)
    assert any('quay lại lần 3' in x for x in texts)
    assert any('Gọi chị Lan' in x for x in texts)
    assert len(rows) <= gc.LIMIT
    # Chỉ đọc: không đổi phiếu nào.
    db.expire_all()
    assert db.get(ServiceTicket, t.id).version == t.version


def test_new_customer_and_open_ticket_same_place(db, p):
    rows = gc.notes(db, p['dp'], customer_code='KTD-000102', part='customer')
    assert [r['icon'] for r in rows] == ['🆕']
    a = ticket(db, p, code='KTD-000102', serial=None, kind='MUC_DINH_KY')
    b = ticket(db, p, code='KTD-000102', serial=None)
    rows = gc.notes(db, p['dp'], ticket=b)
    assert any(r['icon'] == '📍' and r['link'] == a.number for r in rows)
    assert not any(r['icon'] == '🆕' for r in rows)   # đã có phiếu khác


def test_debt_amount_only_for_office(db, p):
    old = ticket(db, p, status='DA_DONG', days_ago=80, serial=None, kind='SUA_TAI_CHO')
    priced(old, 2_350_000)
    db.commit()
    t = ticket(db, p, serial=None)
    office = [r for r in gc.notes(db, p['dp'], ticket=t) if r['icon'] == '💰']
    tech = [r for r in gc.notes(db, p['tho'], ticket=t) if r['icon'] == '💰']
    assert office and '2.350.000' in office[0]['text']
    assert tech and 'nợ cũ quá hạn' in tech[0]['text'] and not re.search(r'\d\.\d{3}', tech[0]['text'])


def test_switch_off_and_rules(db, p):
    t = ticket(db, p)
    assert gc.notes(db, p['dp'], ticket=t)
    row = nva.rows(db)['GHI_CHU']
    row.config = dict(rules=['khach_moi'])
    db.commit()
    assert not any('Gọi chị Lan' in r['text'] for r in gc.notes(db, p['dp'], ticket=t))
    row.enabled = False
    db.commit()
    assert gc.notes(db, p['dp'], ticket=t) == [] and gc.zalo_lines(db, p['tho'], t) == ''


def test_zalo_lines_two_most_important(db, p):
    ticket(db, p, status='DA_DONG', days_ago=5, warranty_months=1, fix_done='Thay rulo')
    t = ticket(db, p)
    text = gc.zalo_lines(db, p['tho'], t)
    assert text.startswith('\nLưu ý:\n') and text.count('\n') == 3 and '🛡' in text


def test_pages_show_box(db, p, client):
    ticket(db, p, status='DA_DONG', days_ago=5, warranty_months=1, fix_done='Thay rulo')
    old = ticket(db, p, status='DA_DONG', days_ago=80, serial=None)
    priced(old, 1_200_000)
    db.commit()
    t = ticket(db, p)
    service_login(client, p['dp'])
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'data-ghi-chu-ai' in page and 'Còn bảo hành sửa đến' in page and '1.200.000' in page
    service_login(client, p['tho'])
    page = client.get(f'/tho/phieu/{t.id}').text
    assert 'Lưu ý trước khi đi' in page and 'nợ cũ quá hạn' in page and '1.200.000' not in page.split('data-ghi-chu-ai')[1].split('</details>')[0]
    service_login(client, p['dp'])
    page = client.get('/dich-vu/moi?customer_code=KTD-000101').text
    assert 'data-ghi-chu-may' in page and 'Gọi chị Lan' in page


def test_settings_save(db, p, client):
    service_login(client, p['gd'])
    page = client.get('/nhan-vien-ao').text
    assert 'Ghi chú phiếu AI' in page
    form = page[page.index('action="/nhan-vien-ao/GHI_CHU"'):]
    key = re.search(r'name="form_key" value="([^"]+)"', form).group(1)
    res = client.post('/nhan-vien-ao/GHI_CHU', data=dict(csrf=csrf_of(page), form_key=key, cai_dat='1', bat='1', rules=['bao_hanh', 'xyz'],
                      recent_days='60', repeat_count='4', repeat_days='30', zalo='0'), follow_redirects=False)
    assert res.status_code == 303
    db.expire_all()
    c = db.get(VirtualAgent, 'GHI_CHU').config
    assert c['rules'] == ['bao_hanh'] and c['recent_days'] == 60 and c['repeat_count'] == 4 and c['zalo'] is False

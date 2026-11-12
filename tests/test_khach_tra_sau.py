"""Khách trả sau trên màn thợ (TUẤN duyệt mẫu 12/11/2026)."""
from datetime import UTC, datetime, timedelta

from app import khach_tra_sau as kts, sales_receipts as svc
from app.models import SalesReceipt, ServiceStep
from test_receipt_cash import fails, t3_people, ticket  # noqa: F401  (fixture)
from test_web import service_login


def closed(db, p, days_ago=3, **changes):
    t = ticket(db, p, status='DA_DONG', **changes)
    db.add(ServiceStep(ticket_id=t.id, action='close', from_status='HOAN_THANH', to_status='DA_DONG', actor_id=p['dispatcher'].id,
                       at=datetime.now(UTC) - timedelta(days=days_ago)))
    db.commit()
    return t


def test_list_only_own_recent_closed_with_debt(db, t3_people, configured_finance):
    p = t3_people; tech = p['technician']
    mine = closed(db, p)
    old = closed(db, p, days_ago=120)
    other = closed(db, p, assignee_id=p['dispatcher'].id)
    free = closed(db, p, billing='MIEN_PHI')
    open_ticket = ticket(db, p)  # HOAN_THANH: vẫn ở Hôm nay, không vào đây
    ids = [r['id'] for r in kts.tickets(db, tech)]
    assert ids == [mine.id]
    assert kts.tickets(db, tech)[0]['due'] == 500000
    assert [r['id'] for r in kts.tickets(db, tech, q=mine.number)] == [mine.id]
    assert kts.tickets(db, tech, q='khong co ai') == []
    fails('FORBIDDEN', kts.tickets, db, p['sales'])
    for t in (old, other, free, open_ticket):
        fails('NOT_ELIGIBLE', kts.one, db, tech, t.id)
    # báo một phần: còn 200.000 vẫn hiện; báo đủ thì biến mất
    svc.report_for_ticket(db, tech, mine.id, svc.SalesReceiptReport(version=mine.version, amount=300000, method='TIEN_MAT'))
    db.expire_all()
    assert kts.tickets(db, tech)[0]['due'] == 200000
    t = db.get(type(mine), mine.id)
    svc.report_for_ticket(db, tech, t.id, svc.SalesReceiptReport(version=t.version, amount=200000, method='CHUYEN_KHOAN'))
    assert kts.tickets(db, tech) == []


def test_web_flow_reports_and_blocks_over_payment(client, db, t3_people, configured_finance):
    p = t3_people; tech = p['technician']
    t = closed(db, p)
    csrf = service_login(client, tech)
    held = client.get('/tien-toi-giu')
    assert 'data-late-entry' in held.text and '/tien-toi-giu/khach-tra-sau' in held.text
    assert f'data-late-ticket="{t.id}"' not in client.get('/tho').text  # không hiện lại trên Hôm nay
    page = client.get('/tien-toi-giu/khach-tra-sau')
    assert page.status_code == 200 and f'data-late-ticket="{t.id}"' in page.text
    assert f'data-late-ticket="{t.id}"' in client.get('/tien-toi-giu/khach-tra-sau', params={'q': t.number, 'phan': '1'}).text
    form = client.get(f'/tien-toi-giu/khach-tra-sau/{t.id}')
    assert form.status_code == 200 and 'value="500.000"' in form.text and 'data-swipe' in form.text
    data = dict(csrf=csrf, version=t.version, amount='600.000', method='TIEN_MAT', received_at='')
    bad = client.post(f'/tien-toi-giu/khach-tra-sau/{t.id}', data=data)
    assert bad.status_code == 409 and 'data-error-code="OVER_PAYMENT"' in bad.text and 'value="600.000"' in bad.text
    ok = client.post(f'/tien-toi-giu/khach-tra-sau/{t.id}', data=dict(data, amount='500.000'), follow_redirects=False)
    assert ok.status_code == 303
    r = db.query(SalesReceipt).filter(SalesReceipt.ticket_id == t.id).one()
    assert ok.headers['location'] == f'/tien-toi-giu?da_bao={r.id}'
    assert (r.status, r.amount, r.method, r.reported_by, r.note) == ('CHO_XAC_NHAN', 500000, 'TIEN_MAT', tech.id, 'Khách trả sau')
    done = client.get(ok.headers['location'])
    assert 'data-late-done' in done.text and f'data-held-receipt="{r.id}"' in done.text
    assert client.get(f'/tien-toi-giu/khach-tra-sau/{t.id}').status_code == 409  # đã báo đủ
    service_login(client, p['sales'])
    assert 'data-late-entry' not in client.get('/tien-toi-giu').text
    assert client.get('/tien-toi-giu/khach-tra-sau').status_code == 403

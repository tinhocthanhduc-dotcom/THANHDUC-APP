from datetime import datetime, timedelta
from html.parser import HTMLParser

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app import (attachments, my_work as svc, purchase_orders, quotes, sales_orders,
                 sales_receipts, service_charges, service_sla, service_tickets, stock_min_levels)
from app.errors import BusinessError
from app.models import AuditLog, Customer, FundLedger, Quote, SalesOrder, SalesReceipt, ServiceTicket, StockLedger
from app.schemas import (PurchaseOrderIn, QuoteIn, SalesOrderIn, SalesReceiptReport, ServiceCharges,
                         ServiceTicketIn, StockMinLevelIn)
from app.web import nav
from conftest import PASSWORD
from test_purchase_orders import purchase_people, DAY
from test_purchase_reports import clock, received
from test_web import service_login, d5_image, b1_form

NOW = datetime(2026, 10, 18, 10, tzinfo=service_sla.TZ)


@pytest.fixture
def people(db, purchase_people, make_user, monkeypatch):
    p = purchase_people
    db.add(Customer(code='KTD-000001', name='Minh Phát', search_text='minh phat'))
    db.commit()
    for role in ('dispatcher', 'reception', 'other_tech', 'other_sales'):
        p[role] = make_user('u2.'+role, 'technician' if role=='other_tech' else 'sales' if role=='other_sales' else role)
    clock(monkeypatch, NOW)
    monkeypatch.setattr(svc, '_now', lambda: NOW)
    monkeypatch.setattr(quotes, '_today', lambda: DAY)
    return p


def groups(db, p, role='director'):
    return {g['key']: g for g in svc.collect(db, p[role], DAY)}


def order(db, p, status='NHAP', who='sales', amount=1000):
    row = sales_orders.create(db, p[who], SalesOrderIn(customer_code='KTD-000001', lines=[
        dict(kind='KHAC', description='Công lắp đặt', qty=1, unit_price=amount, vat_rate=0)]))
    if status != 'NHAP':sales_orders.confirm(db, p[who], row.id, row.version)
    if status == 'DA_GIAO':sales_orders.deliver(db, p[who], row.id, row.version)
    if status == 'DA_HUY':sales_orders.cancel(db, p[who], row.id, 'Không lấy hàng', row.version)
    return row


def ticket(db, p, status='MOI', who='technician', priority='THUONG', appointment=NOW, free=False):
    row = service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code='KTD-000001',
        kind='SUA_TAI_CHO', symptom='Máy bị kẹt giấy', priority=priority))
    if status == 'MOI':return row
    tech = p[who]
    service_tickets.assign(db, p['dispatcher'], row.id, tech.id, row.version)
    if status == 'DA_GIAO':return row
    service_tickets.transition(db, tech, row.id, 'DA_NHAN', row.version)
    if status == 'DA_NHAN':return row
    future=appointment if appointment>service_tickets._now() else service_tickets._now()+timedelta(days=1)
    service_tickets.transition(db, tech, row.id, 'DA_HEN', row.version, appointment_at=future)
    if future!=appointment:
        # Historical/overdue fixtures age the saved appointment, not the transition.
        db.execute(text('UPDATE service_tickets SET appointment_at=:appointment, due_at=:due WHERE id=:id'),
            dict(id=row.id,appointment=appointment,due=service_sla.due_at('DA_HEN',row.priority,service_tickets._now(),appointment)))
        db.commit()
        db.refresh(row)
    if status == 'DA_HEN':return row
    service_tickets.transition(db, tech, row.id, 'DANG_LAM', row.version)
    if status == 'DANG_LAM':return row
    if status == 'CHO_LINH_KIEN':
        return service_tickets.transition(db, tech, row.id, status, row.version)
    attachments.add(db, tech, 'service_ticket', str(row.id), 'SAU', 'sau.png', d5_image())
    service_charges.set_charges(db, tech, row.id, ServiceCharges(version=row.version,
        billing='MIEN_PHI' if free else 'THU_TIEN', free_reason='Bảo hành' if free else '',
        lines=[] if free else [dict(kind='PHI', description='Công sửa', qty=1, unit_price=500000, vat_rate=0)]))
    if status=='HOAN_THANH':
        # AD1: tests needing the intermediate state must have a pending recovery.
        from app.models import ServiceRecoveredPart
        db.add(ServiceRecoveredPart(ticket_id=row.id,product_code='TD-0001',qty=1,
            note='Chưa nộp (dữ liệu test)',holder_id=tech.id,created_by=tech.id,status='CHO_NOP'))
        db.commit()
    service_tickets.transition(db, tech, row.id, 'HOAN_THANH', row.version)
    if status == 'DA_DONG' and row.status!='DA_DONG':service_tickets.transition(db, p['dispatcher'], row.id, status, row.version)
    return row


def report(db, p, parent, amount, role='sales'):
    body=SalesReceiptReport(version=parent.version, amount=amount, method='TIEN_MAT', received_at=DAY)
    return (sales_receipts.report_for_ticket(db,p['dispatcher'],parent.id,body) if isinstance(parent,ServiceTicket)
            else sales_receipts.report(db,p[role],parent.id,body))


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms={}; self.current=None; self.primary=None; self.shortcuts={}; self.group_links=[]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='form':
            self.current=dict(a, fields={})
            if 'id' in a:self.forms[a['id']]=self.current
        if tag=='input' and self.current is not None and (a.get('type')=='hidden' or a.get('name','').startswith('deliver_')):
            self.current['fields'][a['name']]=a.get('value','')
        # UX3 submits the edited lines with the combined main action, as a browser does.
        if tag=='input' and self.current is not None and self.current.get('id') in ('quote-edit','order-edit') and a.get('type') not in ('hidden','file','submit') and a.get('name'):
            if a.get('type') not in ('radio','checkbox') or 'checked' in a:
                fields=self.current['fields'];key=a['name'];value=a.get('value','')
                fields[key]=([*fields[key],value] if isinstance(fields.get(key),list) else [fields[key],value]) if key in fields else value
        if 'data-next-primary' in a:self.primary=a
        if 'data-work-shortcut' in a:self.shortcuts[a['href']]=a

    def handle_endtag(self, tag):
        if tag=='form':self.current=None


def next_action(client, response, **fields):
    page=Page(response.text)
    assert page.primary is not None, response.text[:500]
    primary=page.primary
    if 'href' in primary:
        assert not fields
        return client.get(primary['href'])
    form=page.forms[primary['form']]
    data={**form['fields'], **fields}
    if primary.get('name'):data[primary['name']]=primary['value']
    return client.post(form['action'],data=data)


@pytest.mark.parametrize('role,required', [
    ('technician', {'service_assigned','service_today','service_working','service_unreported'}),
    ('sales', {'quotes_open','orders_draft','orders_waiting','orders_unreported','receipts_rejected'}),
    ('warehouse', {'orders_waiting','purchases_waiting','stock_low'}),
    ('dispatcher', {'service_unassigned','service_unreported','service_close'}),
    ('reception', {'service_unassigned'}),
    ('finance', {'receipts_pending'}),
    ('director', {'service_assigned','quotes_open','orders_waiting','purchases_waiting','receipts_pending'}),
])
def test_role_page_groups_and_no_redirect(client, db, people, role, required):
    service_login(client,people[role])
    page=client.get('/viec-cua-toi',follow_redirects=False)
    assert page.status_code==200 and 'Việc của tôi' in page.text
    found=groups(db,people,role)
    assert required <= found.keys()
    assert len(found)==len({g['key'] for g in svc.collect(db,people[role])})
    assert all(len(g['rows'])<=20 for g in found.values())
    assert not any(g['count'] for g in found.values())


def test_admin_default_no_500_and_no_business_menu(client, make_user, db):
    user=make_user('u2.admin','admin')
    assert nav.work_url(user)=='/viec-cua-toi'
    response=client.post('/dang-nhap',data={'username':user.username,'password':PASSWORD},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/tai-khoan'  # F3: trang đích quản trị, không rơi vào 403.
    assert client.get('/viec-cua-toi').status_code==403  # U1 không cấp quyền nghiệp vụ cho admin-only.
    assert svc.collect(db,user)==[]


def test_ownership_technician_sales_director_and_multi_role_no_duplicates(db, people, make_user):
    p=people;a=ticket(db,p,'DA_GIAO');b=ticket(db,p,'DA_GIAO','other_tech')
    mine=order(db,p);other=order(db,p,who='other_sales')
    assert [r['id'] for r in groups(db,p,'technician')['service_assigned']['rows']]==[a.id]
    assert [r['id'] for r in groups(db,p,'sales')['orders_draft']['rows']]==[mine.id]
    director=groups(db,p)
    assert {r['id'] for r in director['service_assigned']['rows']}=={a.id,b.id}
    assert {r['id'] for r in director['orders_draft']['rows']}=={mine.id,other.id}
    combined=make_user('u2.multi','sales','warehouse','technician','finance')
    rows=svc.collect(db,combined)
    assert len(rows)==len({g['key'] for g in rows})
    assert sum(g['key']=='orders_waiting' for g in rows)==1


def test_service_states_appointment_vietnam_boundaries_priority_and_overdue(db, people):
    p=people
    assigned=ticket(db,p,'DA_GIAO',priority='THUONG')
    urgent=ticket(db,p,'DA_GIAO',priority='KHAN')
    accepted=ticket(db,p,'DA_NHAN')
    morning=ticket(db,p,'DA_HEN',appointment=NOW.replace(hour=0,minute=0))
    late=ticket(db,p,'DA_HEN',appointment=NOW.replace(hour=23,minute=30))
    future=ticket(db,p,'DA_HEN',appointment=NOW+timedelta(days=1))
    past=ticket(db,p,'DA_HEN',appointment=NOW-timedelta(days=1))
    working=ticket(db,p,'DANG_LAM');waiting=ticket(db,p,'CHO_LINH_KIEN')
    completed=ticket(db,p,'HOAN_THANH');closed=ticket(db,p,'DA_DONG');ticket(db,p,'HOAN_THANH',free=True)
    found=groups(db,p,'technician')
    assert [r['id'] for r in found['service_assigned']['rows']]==[urgent.id,assigned.id]
    assert [r['id'] for r in found['service_accepted']['rows']]==[accepted.id]
    assert {r['id'] for r in found['service_today']['rows']}=={morning.id,late.id}
    assert found['service_today']['count']==2 and future.id not in {r['id'] for r in found['service_today']['rows']}
    assert [r['id'] for r in found['service_appointment_overdue']['rows']]==[past.id]
    assert {r['id'] for r in found['service_working']['rows']}=={working.id,waiting.id}
    assert {r['id'] for r in found['service_unreported']['rows']}=={completed.id}
    # Phiếu đã đóng còn thiếu = công nợ khách: điều phối vẫn thấy để báo thu, thợ thì không.
    assert {completed.id,closed.id}<={r['id'] for r in groups(db,p,'dispatcher')['service_unreported']['rows']}
    assert next(r for r in found['service_today']['rows'] if r['id']==morning.id)['overdue'] is True
    report(db,p,completed,500000)
    report(db,p,closed,500000)
    assert groups(db,p,'technician')['service_unreported']['count']==0
    assert groups(db,p,'dispatcher')['service_close']['count']==2  # cả hai phiếu vẫn chờ nộp thu hồi, không theo tiền


def test_sales_groups_states_net_pending_and_rejected_redo(db, people, configured_finance):
    p=people
    a=quotes.create(db,p['sales'],QuoteIn(customer_code='KTD-000001',lines=[]))
    b=quotes.create(db,p['sales'],QuoteIn(customer_code='KTD-000001',lines=[dict(description='Công',qty=1,unit_price=100)]))
    quotes.send(db,p['sales'],b.id,b.version)
    c=quotes.create(db,p['sales'],QuoteIn(customer_code='KTD-000001',lines=[dict(description='Công',qty=1,unit_price=100)]))
    quotes.send(db,p['sales'],c.id,c.version);quotes.accept(db,p['sales'],c.id,c.version)
    draft=order(db,p);waiting=order(db,p,'DA_CHOT');delivered=order(db,p,'DA_GIAO');order(db,p,'DA_HUY')
    r=report(db,p,delivered,400)
    sales_receipts.reject(db,p['finance'],r.id,'Ghi nhầm',r.version)
    data=groups(db,p,'sales')
    assert {row['id'] for row in data['quotes_open']['rows']}=={a.id,b.id}
    assert [row['id'] for row in data['orders_draft']['rows']]==[draft.id]
    assert [row['id'] for row in data['orders_waiting']['rows']]==[waiting.id]
    assert [row['id'] for row in data['orders_unreported']['rows']]==[delivered.id]
    assert [row['id'] for row in data['receipts_rejected']['rows']]==[r.id]
    replacement=report(db,p,delivered,1000)
    assert groups(db,p,'sales')['orders_unreported']['count']==0
    assert groups(db,p,'sales')['receipts_rejected']['count']==0
    assert groups(db,p,'finance')['receipts_pending']['count']==1
    sales_receipts.confirm(db,p['finance'],replacement.id,replacement.version)
    assert groups(db,p,'finance')['receipts_pending']['count']==0


def test_warehouse_waiting_and_same_low_stock_query(db, people):
    p=people
    order(db,p,'DA_CHOT','other_sales');order(db,p);order(db,p,'DA_GIAO')
    a=purchase_orders.create(db,p['warehouse'],PurchaseOrderIn(supplier_code='NCC-0001',order_date=DAY,
        lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=2,unit_cost=1)]))
    purchase_orders.order(db,p['warehouse'],a.id,a.version)
    from app import purchase_receipts
    purchase_receipts.receive(db,p['warehouse'],a.id,a.version,DAY,[dict(order_line_id=a.lines[0].id,qty=1)])
    received(db,p,qty=10,product='TD-0002')
    stock_min_levels.set_level(db,p['warehouse'],'K58','TD-0001',StockMinLevelIn(min_qty=2,version=0))
    stock_min_levels.set_level(db,p['warehouse'],'K145','TD-0002',StockMinLevelIn(min_qty=3,version=0))
    stock_min_levels.set_level(db,p['warehouse'],'K58','TD-0002',StockMinLevelIn(min_qty=5,version=0))
    found=groups(db,p,'warehouse')
    assert found['orders_waiting']['count']==1
    assert [r['id'] for r in found['purchases_waiting']['rows']]==[a.id]
    reference=list(db.execute(stock_min_levels.low_stock_query()).mappings())
    assert found['stock_low']['count']==len(reference)==2
    assert [(r['warehouse_code'],r['product_code']) for r in found['stock_low']['rows']]==[(r['warehouse_code'],r['product_code']) for r in reference]


def test_warehouse_scope_filters_queue_only(db, people):
    p=people
    order(db,p,'DA_CHOT','other_sales')
    a=purchase_orders.create(db,p['warehouse'],PurchaseOrderIn(supplier_code='NCC-0001',order_date=DAY,
        lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=2,unit_cost=1)]))
    purchase_orders.order(db,p['warehouse'],a.id,a.version)
    b=purchase_orders.create(db,p['warehouse'],PurchaseOrderIn(supplier_code='NCC-0001',order_date=DAY,
        lines=[dict(product_code='TD-0001',warehouse_code='K145',qty=2,unit_cost=1)]))
    purchase_orders.order(db,p['warehouse'],b.id,b.version)
    stock_min_levels.set_level(db,p['warehouse'],'K58','TD-0001',StockMinLevelIn(min_qty=2,version=0))
    stock_min_levels.set_level(db,p['warehouse'],'K145','TD-0002',StockMinLevelIn(min_qty=3,version=0))
    p['warehouse'].warehouse_codes=['K145'];db.commit()
    found=groups(db,p,'warehouse')
    assert [r['id'] for r in found['purchases_waiting']['rows']]==[b.id]
    assert [r['warehouse_code'] for r in found['stock_low']['rows']]==['K145']
    assert found['orders_waiting']['count']==1  # đơn không có dòng lấy kho: mọi thủ kho đều thấy
    p['warehouse'].warehouse_codes=[];db.commit()
    assert groups(db,p,'warehouse')['purchases_waiting']['count']==2
    assert groups(db,p)['purchases_waiting']['count']==2


def test_true_counts_twenty_rows_filtered_pages_and_role_guard(client, db, people):
    p=people
    for i in range(25):order(db,p)
    service_login(client,p['sales'])
    page=client.get('/viec-cua-toi')
    g=groups(db,p,'sales')['orders_draft']
    assert g['count']==25 and len(g['rows'])==20
    assert page.text.split('data-work-group="', 1)[1].split('"', 1)[0]=='orders_draft'
    assert g['link'] in page.text
    detail=client.get(g['link']);assert detail.status_code==200
    assert detail.text.count('data-work-group=')==1
    assert 'Trang sau' in detail.text
    last=svc.filtered(db,p['sales'],'orders_draft',2,DAY)
    assert last['count']==25 and len(last['rows'])==5
    assert not {r['id'] for r in g['rows']} & {r['id'] for r in last['rows']}
    assert client.get('/viec-cua-toi?nhom=receipts_pending').status_code==403
    assert client.get('/viec-cua-toi?nhom=orders_draft&trang=0').status_code==400


def test_collect_is_read_only_no_autoflush_audit_or_ledger_changes(db, engine, people):
    p=people;ticket(db,p,'DA_GIAO');order(db,p)
    before=[db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]
    sql=[];loaded=[]
    def capture(conn,cursor,statement,parameters,context,executemany):sql.append(statement)
    with Session(engine) as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        event.listen(session,'loaded_as_persistent',lambda s,o:loaded.append(type(o)))
        event.listen(engine,'before_cursor_execute',capture)
        try:svc.collect(session,p['director'],DAY)
        finally:event.remove(engine,'before_cursor_execute',capture)
    assert not loaded and all(q.lstrip().upper().startswith(('SELECT','WITH')) for q in sql)
    customer=db.get(Customer,'KTD-000001');customer.name='Chưa lưu'
    svc.collect(db,p['director'],DAY);assert db.is_modified(customer);db.rollback()
    assert [db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]==before


def test_sales_end_to_end_only_follow_next_actions(client, db, people, configured_finance):
    p=people;csrf=service_login(client,p['sales'])
    home=client.get('/viec-cua-toi');assert '/bao-gia/moi' in Page(home.text).shortcuts
    page=client.get('/bao-gia/moi')
    page=next_action(client,page,**{k:v for k,v in b1_form(csrf,qty='1',unit_price='120.000',vat_0=0).items() if k!='csrf'})
    assert page.status_code==200
    assert db.query(Quote).one().status=='DA_GUI'  # UX3: Lưu và gửi trong một lần.
    page=next_action(client,page)  # Chấp nhận
    page=next_action(client,page)  # GET chọn kho / tạo từ báo giá
    page=next_action(client,page)  # POST Tạo đơn và chốt (dòng tự do không cần kho)
    assert page.status_code==200
    page=next_action(client,page)  # Giao hàng: mở màn giao chung (Đợt 1b)
    page=next_action(client,page)  # Giao đủ
    o=db.query(SalesOrder).one();assert o.status=='DA_GIAO'
    page=next_action(client,page,amount='120.000',method='TIEN_MAT',received_at=DAY.isoformat(),note='Thu đủ')
    assert page.status_code==200
    r=db.query(SalesReceipt).one();assert r.status=='CHO_XAC_NHAN'
    service_login(client,p['finance'])
    pending=groups(db,p,'finance')['receipts_pending']['rows'][0]
    page=client.get(pending['link'])
    page=next_action(client,page)
    assert page.status_code==200 and '/viec-cua-toi?xong=confirmed' in str(page.url)
    db.expire_all();assert r.status=='DA_XAC_NHAN'
    assert 'Đã xác nhận báo thu.' in page.text
    assert groups(db,p,'sales')['orders_unreported']['count']==0


def test_service_end_to_end_existing_core_holds_appointment_price_photo_rules(client, db, people, configured_finance):
    p=people;csrf=service_login(client,p['reception'])
    page=client.get('/dich-vu/moi')
    page=next_action(client,page,customer_code='KTD-000001',kind='SUA_TAI_CHO',priority='THUONG',symptom='Máy in kẹt giấy',appointment_choice='CHIEU:2026-10-19')
    t=db.query(ServiceTicket).one();assert t.status=='MOI'
    service_login(client,p['dispatcher']);page=client.get(str(page.url))
    page=next_action(client,page,assignee_id=p['technician'].id)
    service_login(client,p['technician']);page=client.get(str(page.url))
    page=next_action(client,page);db.expire_all();assert t.status=='DA_NHAN'
    assert 'Đã đến khách' in page.text
    page=next_action(client,page);db.expire_all();assert t.status=='DANG_LAM'
    assert 'Lưu tiền trước khi hoàn thành' in page.text
    page=next_action(client,page,billing='THU_TIEN',free_reason='',line_kind='PHI',line_description='Công sửa',line_qty='1',line_price='500.000',line_product='',warehouse_0='',vat_0=0)
    parsed=Page(page.text);assert parsed.primary['form']=='photo-SAU-form'
    photo=parsed.forms[parsed.primary['form']]
    page=client.post(photo['action'],data=photo['fields'],files={'file':('sau.png',d5_image(),'image/png')})
    page=next_action(client,page);db.expire_all();assert t.status=='DA_DONG' and t.priced_at is not None
    assert Page(page.text).primary['data-next-route']==f'/dich-vu/{t.id}/bao-thu'  # KT1: thợ được báo thu phiếu của mình.
    service_login(client,p['dispatcher']);page=client.get(str(page.url))
    assert Page(page.text).primary['form']=='receipt-report'
    assert 'service-DA_DONG' not in Page(page.text).forms
    page=next_action(client,page,method='TIEN_MAT',amount='500.000',received_at=DAY.isoformat())
    assert Page(page.text).primary is None
    assert groups(db,p,'technician')['service_unreported']['count']==0


def test_next_actions_permissions_self_confirmation_csrf_and_stale_version(client, db, people):
    p=people;o=order(db,p,'DA_CHOT')
    service_login(client,p['technician'])
    assert Page(client.get('/dich-vu/moi').text).primary is None
    service_login(client,p['warehouse'])
    page=client.get(f'/don-ban/{o.id}');assert Page(page.text).primary['data-next-route']==f'/kho/giao/{o.id}'
    service_login(client,p['finance'])
    assert Page(client.get(f'/don-ban/{o.id}').text).primary is None
    csrf=service_login(client,p['sales'])
    page=client.get(f'/kho/giao/{o.id}')  # Đợt 1b: giao hàng ở một màn chung
    assert client.post(Page(page.text).forms['warehouse-deliver']['action'],data={'version':o.version}).status_code==403
    assert next_action(client,page,version=o.version-1).status_code==409
    dual=p['sales'];dual.roles=['sales','finance'];db.commit()
    r=report(db,p,o,1000)
    service_login(client,dual);page=client.get(f'/bao-thu/{r.id}')
    assert Page(page.text).primary['data-next-route']==f'/bao-thu/{r.id}/reject'


@pytest.mark.parametrize('state,target', [
    ('DA_GIAO','DA_NHAN'), ('DA_NHAN','DA_HEN'), ('DA_HEN','DANG_LAM'),
    ('CHO_LINH_KIEN','DANG_LAM'), ('HOAN_THANH','DA_DONG'),
])
def test_service_next_step_matches_existing_transition_form(client, db, people, state, target):
    t=ticket(db,people,state,free=True)
    service_login(client,people['dispatcher'] if state=='HOAN_THANH' else people['technician'])
    page=Page(client.get(f'/dich-vu/{t.id}').text)
    if state=='HOAN_THANH':
        assert page.primary is None and 'service-DA_DONG' not in page.forms
    else:
        assert page.primary['form']=='service-'+target
        assert page.forms[page.primary['form']]['fields']['to_status']==target
        assert page.primary['data-next-route']==f'/dich-vu/{t.id}/transition'
    service_login(client,people['reception'])
    assert Page(client.get(f'/dich-vu/{t.id}').text).primary is None


def test_closed_service_still_unreported_stays_visible_with_authorized_report_step(client, db, people):
    t=ticket(db,people,'DA_DONG')
    service_login(client,people['dispatcher'])
    page=client.get(f'/dich-vu/{t.id}')
    assert Page(page.text).primary['data-next-route']==f'/dich-vu/{t.id}/bao-thu'
    page=next_action(client,page,method='TIEN_MAT',amount='500.000',received_at=DAY.isoformat())
    assert page.status_code==200 and Page(page.text).primary is None
    assert groups(db,people,'technician')['service_unreported']['count']==0

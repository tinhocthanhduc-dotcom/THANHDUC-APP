from datetime import timedelta, date
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from app import quotes, sales_orders, service_tickets
from app.models import Quote, SalesOrder, ServiceTicket, AuditLog, StockReservation
from app.schemas import QuoteIn
from app.web import steps
from test_sales_orders import people, stocked, body, GOOD
from test_web import service_login, b1_form, service_people
from test_my_work import Page


def order_form(csrf, version=0, **changes):
    raw=dict(csrf=csrf,version=version,customer_code='KTD-000001',contact_name='Chị Lan',delivery_address='58 Mã Lò',
             fulfillment='TAI_QUAY',payment_term='TRA_NGAY',product_code='TD-0001',description='',qty='1',unit='hộp',
             unit_price='450.000',kind_0='HANG',warehouse_0='K58',vat_0='8',then='confirm')
    return raw|changes


def test_quote_atomic_save_send_and_one_history(client,db,stocked):
    q=quotes.create(db,stocked['sales'],QuoteIn(customer_code='KTD-000001',lines=[dict(description='Công',qty=1,unit_price=100)]))
    csrf=service_login(client,stocked['sales'])
    response=client.post(f'/bao-gia/{q.id}',data=b1_form(csrf,version=q.version,qty='1',unit_price='450.000',then='send'),follow_redirects=False)
    assert response.status_code==303
    db.expire_all();assert q.status=='DA_GUI' and q.total==495000
    assert db.query(AuditLog).filter_by(action='quote.send',entity_id=str(q.id)).count()==1
    html=client.get(response.headers['location']).text
    assert 'aria-current="step">● Đã gửi' in html
    assert html.count('data-next-primary')==1


def test_quote_send_error_rolls_back_update_and_keeps_input(client,db,stocked):
    q=quotes.create(db,stocked['sales'],QuoteIn(customer_code='KTD-000001',lines=[dict(description='Công',qty=1,unit_price=100)]))
    version=q.version;csrf=service_login(client,stocked['sales'])
    r=client.post(f'/bao-gia/{q.id}',data=b1_form(csrf,version=version,unit_price='450.000',valid_until='2026-01-01',then='send'))
    assert r.status_code==400 and 'QUOTE_EXPIRED' in r.text
    db.expire_all();assert q.status=='NHAP' and q.lines[0].unit_price==100 and q.version==version
    assert 'value="450.000"' in r.text
    assert db.query(AuditLog).filter_by(action='quote.update_lines',entity_id=str(q.id)).count()==0


def test_quote_new_send_error_creates_nothing(client,db,stocked):
    csrf=service_login(client,stocked['sales'])
    assert client.post('/bao-gia/moi',data=b1_form(csrf,then='send',valid_until='2026-01-01')).status_code==400
    assert db.query(Quote).count()==0


def test_order_save_confirm_new_and_same_form_key_once(client,db,stocked):
    csrf=service_login(client,stocked['sales']);page=Page(client.get('/don-ban/moi').text)
    raw=order_form(csrf,form_key=page.forms['order-edit']['fields']['form_key'])
    first=client.post('/don-ban/moi',data=raw,follow_redirects=False)
    second=client.post('/don-ban/moi',data=raw,follow_redirects=False)
    assert first.status_code==second.status_code==303 and first.headers['location']==second.headers['location']
    order=db.query(SalesOrder).one();assert order.status=='DA_CHOT'
    assert db.query(StockReservation).filter_by(order_id=order.id).count()==1
    assert db.query(AuditLog).filter_by(action='sales_order.confirm',entity_id=str(order.id)).count()==1


def test_order_update_confirm_add_line(client,db,stocked):
    order=sales_orders.create(db,stocked['sales'],body(lines=[GOOD|dict(qty=1)]));csrf=service_login(client,stocked['sales'])
    r=client.post(f'/don-ban/{order.id}',data=order_form(csrf,order.version,product_code=['TD-0001','TD-0002'],description=['',''],qty=['1','1'],unit=['hộp','cái'],unit_price=['450.000','900.000'],kind_1='HANG',warehouse_1='K145',vat_1=8))
    assert r.status_code==200
    db.expire_all();assert order.status=='DA_CHOT' and len(order.lines)==2
    assert db.query(StockReservation).filter_by(order_id=order.id).count()==2


@pytest.mark.parametrize('new',[False,True])
def test_order_missing_warehouse_rolls_back_save(client,db,stocked,new):
    order=None if new else sales_orders.create(db,stocked['sales'],body(lines=[GOOD|dict(qty=1)]))
    csrf=service_login(client,stocked['sales']);old=order.version if order else 0
    r=client.post('/don-ban/moi' if new else f'/don-ban/{order.id}',data=order_form(csrf,old,product_code=['TD-0001','TD-0002'],description=['','Dòng mới'],qty=['1','1'],unit=['hộp','cái'],unit_price=['450.000','900.000'],kind_1='HANG',warehouse_1='',vat_1=8))
    assert r.status_code==400 and 'Dòng 2 chưa chọn kho' in r.text
    db.expire_all()
    if new:assert db.query(SalesOrder).count()==0
    else:assert order.status=='NHAP' and len(order.lines)==1 and order.lines[0].unit_price==GOOD['unit_price'] and order.version==old
    assert 'Dòng mới' in r.text and db.query(StockReservation).count()==0


def test_order_credit_error_and_director_override(client,db,stocked):
    csrf=service_login(client,stocked['sales'])
    raw=order_form(csrf,payment_term='CONG_NO',due_days=30)
    r=client.post('/don-ban/moi',data=raw);assert r.status_code==409 and 'CREDIT_LIMIT' in r.text
    assert db.query(SalesOrder).count()==0
    csrf=service_login(client,stocked['director'])
    r=client.post('/don-ban/moi',data=raw|dict(csrf=csrf,credit_override_reason='Khách quen được duyệt'))
    assert r.status_code==200 and db.query(SalesOrder).one().status=='DA_CHOT'


@pytest.mark.parametrize('valid',[True,False])
def test_create_service_and_assign_atomic(client,db,service_people,valid):
    p=service_people;csrf=service_login(client,p['dispatcher'])
    assignee=p['technician'] if valid else p['warehouse']
    r=client.post('/dich-vu/moi',data=dict(csrf=csrf,customer_code='KTD-000001',kind='SUA_TAI_CHO',priority='THUONG',symptom='Máy không in',assignee_id=assignee.id,appointment_choice='CHIEU:'+(service_tickets._now()+timedelta(days=1)).date().isoformat()))
    if valid:
        assert r.status_code==200;t=db.query(ServiceTicket).one();assert t.status=='DA_GIAO' and t.assignee_id==assignee.id
        assert any(step.action=='assign' for step in t.steps)
    else:assert r.status_code==400 and db.query(ServiceTicket).count()==0


def test_missing_conditions_disabled_and_terminal_no_steps(client,db,stocked):
    service_login(client,stocked['sales']);html=client.get('/bao-gia/moi').text
    primary=Page(html).primary
    assert 'disabled' in primary and '✗ Có ít nhất 1 dòng' in html
    assert html.count('data-next-primary')==1
    q=quotes.create(db,stocked['sales'],QuoteIn(customer_code='KTD-000001',lines=[]));quotes.cancel(db,stocked['sales'],q.id,'Khách hủy',q.version)
    html=client.get(f'/bao-gia/{q.id}').text
    assert 'data-workflow-terminal' in html and 'data-workflow-steps' not in html
    order=sales_orders.create(db,stocked['sales'],body());sales_orders.cancel(db,stocked['sales'],order.id,'Hủy',order.version)
    assert 'data-workflow-steps' not in client.get(f'/don-ban/{order.id}').text


def test_steps_current_delivery_collection_and_appointment(db,stocked):
    o=sales_orders.create(db,stocked['sales'],body(lines=[GOOD|dict(qty=2)]));sales_orders.confirm(db,stocked['sales'],o.id,o.version)
    from app.schemas import SalesDeliveryLineIn
    sales_orders.deliver(db,stocked['warehouse'],o.id,o.version,[SalesDeliveryLineIn(order_line_no=1,qty=1)])
    assert steps.order(o)['labels'][steps.order(o)['current']]=='Giao hàng'
    sales_orders.deliver(db,stocked['warehouse'],o.id,o.version)
    assert steps.order(o)['labels'][steps.order(o)['current']]=='Thu tiền'
    from types import SimpleNamespace
    t=SimpleNamespace(status='DA_HEN',appointment_at=service_tickets._now())
    assert 'Hẹn' not in steps.service(t)['labels']
    t.status='KHACH_HEN_SAU';assert not steps.service(t)['labels']


def test_service_scheduled_has_only_current_form(client,db,service_people):
    p=service_people;t=service_tickets.create(db,p['dispatcher'],__import__('app.schemas',fromlist=['ServiceTicketIn']).ServiceTicketIn(customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Máy không in',appointment_at=service_tickets._now()+timedelta(days=1)))
    service_tickets.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    service_tickets.transition(db,p['dispatcher'],t.id,'DA_NHAN',t.version)
    service_tickets.transition(db,p['dispatcher'],t.id,'DA_HEN',t.version,appointment_at=service_tickets._now()+timedelta(days=1))
    service_login(client,p['dispatcher']);html=client.get(f'/dich-vu/{t.id}').text
    assert html.count('data-next-primary')==1
    assert 'id="service-HOAN_THANH"' not in html and 'id="service-charges"' not in html and 'id="part-create"' not in html
    assert 'data-other-work' in html


def test_service_check_custody_blocks_then_allows_close(client,db,service_people):
    from app import attachments, service_charges
    from app.models import Equipment
    from app.schemas import ServiceTicketIn, ServiceCharges
    from test_web import d5_image
    p=service_people
    t=service_tickets.create(db,p['dispatcher'],ServiceTicketIn(customer_code='KTD-000001',kind='MANG_VE',serial='AB123',symptom='Mang máy về sửa',appointment_at=service_tickets._now()+timedelta(days=1)))
    service_tickets.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    service_tickets.transition(db,p['dispatcher'],t.id,'DA_NHAN',t.version)
    service_tickets.transition(db,p['dispatcher'],t.id,'DANG_LAM',t.version)
    attachments.add(db,p['dispatcher'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành'))
    service_tickets.transition(db,p['dispatcher'],t.id,'HOAN_THANH',t.version)
    eq=db.get(Equipment,'AB123');eq.custody_location='KHO_KY_THUAT';db.commit()
    service_login(client,p['dispatcher']);html=client.get(f'/dich-vu/{t.id}').text
    assert '✗ Máy khách đã trả' in html and 'disabled' in Page(html).primary
    eq.custody_location='TAI_KHACH';db.commit()
    html=client.get(f'/dich-vu/{t.id}').text
    assert '✓ Máy khách đã trả' in html and 'disabled' not in Page(html).primary
    assert Page(html).primary['form']=='service-DA_DONG'


def test_exception_forms_inside_details_and_error_opens(client,db,stocked):
    order=sales_orders.create(db,stocked['sales'],body(lines=[GOOD|dict(qty=1)]));sales_orders.confirm(db,stocked['sales'],order.id,order.version)
    csrf=service_login(client,stocked['warehouse']);html=client.get(f'/don-ban/{order.id}').text
    class Details(HTMLParser):
        def __init__(self,html):
            super().__init__();self.stack=[];self.forms={};self.feed(html)
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='details':self.stack.append('data-other-work' in attrs)
            if tag=='form':self.forms[attrs.get('action','')]=any(self.stack)
        def handle_endtag(self,tag):
            if tag=='details':self.stack.pop()
    assert Details(html).forms[f'/don-ban/{order.id}/release']
    csrf=service_login(client,stocked['sales']);html=client.get(f'/don-ban/{order.id}').text
    for action in ['options','promise','cancel']:
        assert Details(html).forms[f'/don-ban/{order.id}/{action}']
    r=client.post(f'/don-ban/{order.id}/options',data=dict(csrf=csrf,version=order.version,fulfillment='WRONG'))
    assert r.status_code==400 and 'data-other-work open' in r.text


def test_inactive_assignee_creates_no_ticket(client,db,service_people):
    p=service_people;p['technician'].active=False;db.commit();csrf=service_login(client,p['dispatcher'])
    r=client.post('/dich-vu/moi',data=dict(csrf=csrf,customer_code='KTD-000001',kind='SUA_TAI_CHO',priority='THUONG',symptom='Máy không in',assignee_id=p['technician'].id,appointment_choice='CHIEU:'+(service_tickets._now()+timedelta(days=1)).date().isoformat()))
    assert r.status_code==400 and db.query(ServiceTicket).count()==0


def test_shortage_warns_but_partial_reservation_still_allowed(client,db,stocked):
    csrf=service_login(client,stocked['sales'])
    r=client.get('/don-ban/dieu-kien',params=order_form(csrf,qty='8'))
    assert 'TD-0001 ở kho K58: thiếu 3 · Sẽ giữ thiếu' in r.text
    assert 'data-condition="stock-K58-TD-0001" data-ok="0" data-blocking="0"' in r.text
    assert client.post('/don-ban/moi',data=order_form(csrf,qty='8')).status_code==200
    reservation=db.query(StockReservation).one();assert reservation.qty==5


@pytest.mark.parametrize('kind',['quote','order'])
def test_combined_stale_version_keeps_saved_prices(client,db,stocked,kind):
    csrf=service_login(client,stocked['sales'])
    if kind=='quote':
        row=quotes.create(db,stocked['sales'],QuoteIn(customer_code='KTD-000001',lines=[dict(description='Công',qty=1,unit_price=100)]))
        raw=b1_form(csrf,version=999,unit_price='900.000',then='send');path=f'/bao-gia/{row.id}';price=100
    else:
        row=sales_orders.create(db,stocked['sales'],body(lines=[GOOD]));raw=order_form(csrf,999,unit_price='900.000');path=f'/don-ban/{row.id}';price=GOOD['unit_price']
    r=client.post(path,data=raw)
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text
    db.expire_all();assert row.status=='NHAP' and row.lines[0].unit_price==price
    assert 'value="900.000"' in r.text

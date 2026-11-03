"""TUẤN 03/11/2026: nút Mở lại để sửa trên đơn bán đã chốt và đơn mua đã đặt."""
import re
from app import purchase_orders, sales_orders
from app.models import PurchaseOrder, SalesOrder
from test_purchase_orders import purchase_people, body as po_body
from test_sales_orders import people, stocked, body
from test_web import csrf_of, service_login


def test_sales_order_reopen_button_and_post(client,db,stocked):
    s=stocked['sales'];o=sales_orders.create(db,s,body());sales_orders.confirm(db,s,o.id,o.version)
    service_login(client,s)
    html=client.get(f'/don-ban/{o.id}').text
    assert 'data-order-reopen' in html and 'Mở lại để sửa' in html
    r=client.post(f'/don-ban/{o.id}/reopen',data={'csrf':csrf_of(html),'version':o.version,'reason':'Khách đổi số lượng'},follow_redirects=False)
    assert r.status_code==303, re.findall(r'data-error-code="[^"]*"[^>]*>[^<]*',r.text)
    db.expire_all()
    assert db.get(SalesOrder,o.id).status=='NHAP'
    assert 'data-order-reopen' not in client.get(f'/don-ban/{o.id}').text


def test_sales_order_reopen_hidden_for_warehouse(client,db,stocked):
    s=stocked['sales'];o=sales_orders.create(db,s,body());sales_orders.confirm(db,s,o.id,o.version)
    service_login(client,stocked['warehouse'])
    assert 'data-order-reopen' not in client.get(f'/don-ban/{o.id}').text


def test_purchase_reopen_button_and_post(client,db,purchase_people):
    u=purchase_people['warehouse'];row=purchase_orders.create(db,u,po_body());purchase_orders.order(db,u,row.id,row.version)
    service_login(client,u)
    html=client.get(f'/don-mua/{row.id}').text
    assert 'data-purchase-reopen' in html
    r=client.post(f'/don-mua/{row.id}/reopen',data={'csrf':csrf_of(html),'version':row.version,'reason':'NCC báo giá mới'},follow_redirects=False)
    assert r.status_code==303
    db.expire_all()
    assert db.get(PurchaseOrder,row.id).status=='NHAP'


# ---- Nhóm A (mẫu SuaPhieu duyệt 03/11/2026) ----
import pytest
from sqlalchemy import select
from app import service_tickets
from app.errors import BusinessError
from app.models import AuditLog, Customer, CustomerSite, ServiceTicket
from app.schemas import ServiceTicketIn


def _fail(code, fn, *args):
    with pytest.raises(BusinessError) as exc: fn(*args)
    assert exc.value.code == code


def test_a1_delivery_info_after_confirm_until_fully_delivered(client,db,stocked):
    s=stocked['sales'];o=sales_orders.create(db,s,body());sales_orders.confirm(db,s,o.id,o.version)
    _fail('FORBIDDEN',sales_orders.change_delivery_info,db,stocked['warehouse'],o.id,o.version,'A','B','C')
    service_login(client,s)
    html=client.get(f'/don-ban/{o.id}').text
    assert 'data-delivery-info' in html
    r=client.post(f'/don-ban/{o.id}/delivery-info',data={'csrf':csrf_of(html),'version':o.version,'contact_name':'Anh Hùng 0912',
        'delivery_address':'12 Lũy Bán Bích','note':'Gọi trước'},follow_redirects=False)
    assert r.status_code==303
    db.expire_all();o=db.get(SalesOrder,o.id)
    assert (o.contact_name,o.delivery_address,o.note,o.status)==('Anh Hùng 0912','12 Lũy Bán Bích','Gọi trước','DA_CHOT')
    log=db.scalar(select(AuditLog).where(AuditLog.action=='sales_order.delivery_info'))
    assert log.before['delivery_address']=='58 Mã Lò' and log.after['delivery_address']=='12 Lũy Bán Bích'
    sales_orders.deliver(db,stocked['warehouse'],o.id,o.version)
    _fail('BAD_STATUS',sales_orders.change_delivery_info,db,s,o.id,o.version,'A','B','C')
    assert 'data-delivery-info' not in client.get(f'/don-ban/{o.id}').text


def test_a2_change_customer_only_on_plain_draft(client,db,stocked):
    s=stocked['sales']
    db.add(Customer(code='KTD-000002',name='Minh Phương',address='45 Âu Cơ',search_text='minh phuong'));db.commit()
    o=sales_orders.create(db,s,body())
    _fail('BAD_CUSTOMER',sales_orders.change_customer,db,s,o.id,o.version,'KTD-000009')  # khách ngừng
    service_login(client,s)
    html=client.get(f'/don-ban/{o.id}').text
    assert 'data-change-customer' in html
    r=client.post(f'/don-ban/{o.id}/change-customer',data={'csrf':csrf_of(html),'version':o.version,'customer_code':'KTD-000002'},follow_redirects=False)
    assert r.status_code==303
    db.expire_all();o=db.get(SalesOrder,o.id)
    assert (o.customer_code,o.delivery_address,o.contact_name)==('KTD-000002','45 Âu Cơ','') and len(o.lines)==2
    sales_orders.confirm(db,s,o.id,o.version)
    _fail('BAD_STATUS',sales_orders.change_customer,db,s,o.id,o.version,'KTD-000001')


@pytest.fixture
def ticket_people(db, make_user):
    db.add(Customer(code='KTD-000005',name='Minh Phát',address='58 Mã Lò',search_text='minh phat'));db.commit()
    p={r:make_user('a3.'+r,r) for r in ('dispatcher','technician')}
    db.add(CustomerSite(customer_code='KTD-000005',name='Chi nhánh Tân Phú',address='12 Lũy Bán Bích',created_by=p['dispatcher'].id))
    db.commit()
    return p


def test_a3_dispatcher_edits_ticket_info_technician_cannot(client,db,ticket_people):
    p=ticket_people;site=db.scalar(select(CustomerSite))
    t=service_tickets.create(db,p['dispatcher'],ServiceTicketIn(customer_code='KTD-000005',kind='SUA_TAI_CHO',symptom='Kẹt giấy'))
    t=service_tickets.assign(db,p['dispatcher'],t.id,p['technician'].id,t.version)
    _fail('FORBIDDEN',service_tickets.update_info,db,p['technician'],t.id,t.version,'Khác','GAP',None)
    _fail('BAD_FORM',service_tickets.update_info,db,p['dispatcher'],t.id,t.version,' ','GAP',None)
    service_login(client,p['dispatcher'])
    html=client.get(f'/dich-vu/{t.id}').text
    assert 'data-ticket-info' in html and 'Chi nhánh Tân Phú' in html
    r=client.post(f'/dich-vu/{t.id}/update-info',data={'csrf':csrf_of(html),'version':t.version,'symptom':'Kẹt giấy khay 2, lỗi E3',
        'priority':'KHAN','site_id':str(site.id)},follow_redirects=False)
    assert r.status_code==303
    db.expire_all();t=db.get(ServiceTicket,t.id)
    assert (t.symptom,t.priority,t.site_id,t.status)==('Kẹt giấy khay 2, lỗi E3','KHAN',site.id,'DA_GIAO')
    from app import notifications
    assert any(i['id'].startswith('ticket_info:') for i in notifications.items(db,db.get(type(p['technician']),p['technician'].id)))
    service_login(client,p['technician'])
    assert 'data-ticket-info' not in client.get(f'/dich-vu/{t.id}').text


def test_a4_copy_cancelled_sales_and_purchase_orders(client,db,stocked):
    s=stocked['sales'];o=sales_orders.create(db,s,body())
    _fail('BAD_STATUS',sales_orders.copy_cancelled,db,s,o.id)
    sales_orders.cancel(db,s,o.id,'Khách đổi mẫu',o.version)
    service_login(client,s)
    html=client.get(f'/don-ban/{o.id}').text
    assert 'data-order-copy' in html
    r=client.post(f'/don-ban/{o.id}/copy',data={'csrf':csrf_of(html),'version':o.version},follow_redirects=False)
    assert r.status_code==303
    new=db.scalar(select(SalesOrder).where(SalesOrder.id!=o.id))
    assert new.status=='NHAP' and new.customer_code==o.customer_code and new.total==o.total and len(new.lines)==len(o.lines)
    assert db.get(SalesOrder,o.id).status=='DA_HUY'


def test_a4_copy_cancelled_purchase(db,purchase_people):
    u=purchase_people['warehouse'];row=purchase_orders.create(db,u,po_body(price_includes_vat=True))
    purchase_orders.cancel(db,u,row.id,'Không mua nữa',row.version)
    new=purchase_orders.copy_cancelled(db,u,row.id)
    assert new.status=='NHAP' and new.id!=row.id and new.total==row.total and new.lines[0].qty==row.lines[0].qty

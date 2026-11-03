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

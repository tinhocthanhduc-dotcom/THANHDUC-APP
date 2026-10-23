"""Danh sách báo giá / đơn bán: lọc trực tiếp gửi cả form, kể cả ô chọn để trống."""
from app import quotes
from app.schemas import QuoteIn
from test_sales_orders import people, stocked
from test_web import service_login


def test_live_filter_with_empty_selects_returns_rows(client,db,stocked):
    q=quotes.create(db,stocked['sales'],QuoteIn(customer_code='KTD-000001',lines=[dict(description='Lắp đặt',qty=1,unit_price=500)]))
    service_login(client,stocked['sales'])
    r=client.get('/bao-gia?status=NHAP&nguoi=&ky=&q=&phan=1')
    assert r.status_code==200 and q.number in r.text
    r=client.get(f"/bao-gia?status=&nguoi={stocked['sales'].id}&ky=&q=&phan=1")
    assert r.status_code==200 and q.number in r.text
    assert q.number not in client.get('/bao-gia?status=&nguoi=999999&ky=&q=&phan=1').text
    assert client.get('/don-ban?status=NHAP&fulfillment=&nguoi=&q=&from=&to=&phan=1').status_code==200

"""Hộp xem nhanh: trả hàng, thợ trả kho, phiếu kho sinh ra — quyền như trang đầy đủ, không giá vốn."""
from app import technician_returns
from test_sales_returns import sold, people, purchase_people, receive  # noqa: F401
from test_technician_stock import held, transfer  # noqa: F401
from test_web import service_login


def _quick(client, path):
    return client.get('/xem-nhanh', params={'u': path})


def test_sales_return_and_its_stock_doc(client, db, sold):
    p, o, gh = sold
    r = receive(db, p, o, gh)
    service_login(client, p['sales'])
    html = _quick(client, f'/tra-hang/{r.id}').text
    assert r.number in html and o.number in html and f'href="/don-ban/{o.id}"' in html and '400.000' in html
    service_login(client, p['warehouse'])
    pn = r.lines[0].stock_doc_id
    res = _quick(client, f'/phieu-kho/{pn}')
    assert res.status_code == 200 and 'Đã ghi sổ' in res.text and 'K58' in res.text
    assert 'giá vốn' not in res.text.lower() and '<form' not in res.text


def test_technician_return_only_owner_or_warehouse(client, db, held):
    p, _, _ = held
    transfer(db, p, qty=2)
    r = technician_returns.create(db, p['technician'], [dict(product_code='TD-0001', qty=2, to_warehouse_code='K58', condition='TOT')])[0]
    for role in ('warehouse', 'technician', 'sales', 'finance'):
        service_login(client, p[role])
        full = client.get(f'/tho-tra-hang/{r.id}').status_code == 200
        res = _quick(client, f'/tho-tra-hang/{r.id}')
        assert (res.status_code == 200) == full, role
        if full:
            assert r.number in res.text and 'Chờ nhận' in res.text

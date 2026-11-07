"""Hộp xem nhanh phiếu sản xuất: thợ chỉ xem phiếu của mình, như trang đầy đủ."""
from test_production import shop, create  # noqa: F401
from test_production import held, people, purchase_people  # noqa: F401
from test_web import service_login


def test_production_quick_view(client, db, shop):
    p = shop[0]
    row = create(db, shop)
    for role in ('technician', 'other_tech', 'warehouse', 'sales'):
        if role not in p: continue
        service_login(client, p[role])
        full = client.get(f'/san-xuat/{row.number}').status_code == 200
        res = client.get('/xem-nhanh', params={'u': f'/san-xuat/{row.number}'})
        assert (res.status_code == 200) == full, role
        if full:
            assert row.number in res.text and 'Kế hoạch' in res.text and 'Bước tiếp' in res.text

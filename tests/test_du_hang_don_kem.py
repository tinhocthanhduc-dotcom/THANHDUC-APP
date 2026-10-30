"""Hàng mang theo: đơn giao cùng chuyến hiện trong danh sách kiểm hàng (TUẤN 30/10/2026)."""
from app import service_goods, technician_deliveries
from app.schemas import SalesDeliveryAssign
from test_technician_deliveries import setup, people, purchase_people, assigned, move, ticket


def test_trip_order_goods_listed_until_handed_over(db, setup):
    p = setup
    t = ticket(db, p, 'DA_HEN')
    o = assigned(db, p)
    technician_deliveries.assign(db, p['dispatcher'], o.id, SalesDeliveryAssign(version=o.version, assignee_id=p['technician'].id, service_ticket_id=t.id))
    rows = service_goods.state(db, t)['items']
    assert [(r['group'], r['qty'], r['label']) for r in rows] == [('Đơn giao kèm ' + o.number, 4, 'Chờ kho')]
    db.refresh(o); move(db, p, o, 4)
    assert service_goods.state(db, t)['items'][0]['label'] == 'Kho đã giao'



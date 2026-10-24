"""TUẤN 24/10: máy đang được giữ theo phiếu khác không gắn được vào phiếu mới (tránh phiếu kẹt không hoàn thành)."""
import pytest

from app import service_tickets, service_workshop as svc
from app.errors import BusinessError
from app.models import ServiceTicket
from app.schemas import ServiceMachineSet, ServiceTicketIn
from test_mv1_workshop import onsite, held, people, purchase_people, intake, photo, ticket  # noqa: F401
from test_web import service_login


@pytest.fixture
def holding(db, onsite):
    p, a, b, t, eq = onsite
    photo(db, p, t)
    svc.bring(db, p['technician'], t.id, t.version, intake(eq), [])
    db.expire_all()
    return p, t, eq


def test_held_machine_cannot_be_put_on_another_ticket(db, holding):
    p, a, eq = holding
    b = ticket(db, p, 'DANG_LAM')
    before = db.get(ServiceTicket, b.id).version
    with pytest.raises(BusinessError) as e:
        service_tickets.set_machine(db, p['technician'], b.id, ServiceMachineSet(version=b.version, serial=eq.serial))
    assert e.value.code == 'MACHINE_HELD' and e.value.status == 409 and a.number in e.value.message
    db.expire_all(); b = db.get(ServiceTicket, b.id)
    assert b.serial is None and b.version == before
    with pytest.raises(BusinessError) as e:
        service_tickets.create(db, p['dispatcher'], ServiceTicketIn(customer_code=a.customer_code, kind='SUA_TAI_CHO', symptom='Kẹt giấy', serial=eq.serial))
    assert e.value.code == 'MACHINE_HELD'


def test_picker_shows_held_machine_disabled(db, holding, client):
    p, a, eq = holding
    b = ticket(db, p, 'DANG_LAM')
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{b.id}').text
    chip = page.split(f'value="SERIAL:{eq.serial}"')[1].split('</label>')[0]
    assert 'disabled' in chip and f'Đang giữ theo {a.number}' in chip

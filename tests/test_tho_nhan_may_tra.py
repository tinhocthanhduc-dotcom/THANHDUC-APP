"""TUẤN 24/10: thợ nhận máy sửa xong từ kho để đi trả khách — một khung gọn, một nút nhận."""
from datetime import timedelta
from app import service_workshop as svc, service_tickets, equipment_custody as custody
from app.models import Equipment
from test_my_work import people, purchase_people
from test_technician_stock import held
from test_mv1_workshop import onsite, photo
from test_xg1_workshop import holding, workshop, quote, approve, repaired
from test_web import service_login


def test_return_pickup_is_one_compact_card(client,db,workshop):
    p,a,b,t,eq=workshop;quote(db,p,t);approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher');repaired(db,p,t)
    svc.schedule_return(db,p['dispatcher'],t.id,t.version,service_tickets._now()+timedelta(days=1),None,p['technician'].id,'Giao trả máy')
    hand=custody.pending(db,eq.serial)
    csrf=service_login(client,p['technician'])
    page=client.get(f'/tho/phieu/{t.id}').text
    assert 'Nhận máy đã sửa để trả khách' in page and 'Đã nhận máy' in page
    assert 'Bàn giao chờ nhận' not in page and 'Nội dung bàn giao chờ nhận' not in page and 'Phụ kiện lúc nhận' not in page
    # Bước 1 chỉ nhận máy: chưa hiện thu tiền, đầu phiếu ghi việc đã sửa thay cho lỗi khách báo.
    assert 'Trả máy đã sửa xong' not in page and 'Vuốt để Hoàn thành' not in page
    assert 'Đã sửa xong' in page and 'Thay drum' in page and 'tho-symptom' not in page
    r=client.post(f'/giu-may/{t.id}/xac-nhan',data=dict(csrf=csrf,custody_id=hand.id,version=hand.version,tho_return=f'/tho/phieu/{t.id}'),follow_redirects=False)
    assert r.status_code in (200,303)
    db.expire_all();assert db.get(Equipment,eq.serial).custody_location=='THO_GIU'
    page=client.get(f'/tho/phieu/{t.id}').text
    assert 'Trả máy đã sửa xong' in page and 'Nhận máy đã sửa để trả khách' not in page


def test_dispatcher_still_sees_pending_detail(client,db,workshop):
    p,a,b,t,eq=workshop;quote(db,p,t);approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher');repaired(db,p,t)
    svc.schedule_return(db,p['dispatcher'],t.id,t.version,service_tickets._now()+timedelta(days=1),None,p['technician'].id,'Giao trả máy')
    service_login(client,p['dispatcher'])
    assert 'Bàn giao chờ nhận' in client.get(f'/dich-vu/{t.id}').text

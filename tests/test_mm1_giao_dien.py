"""MM1 giao diện: tick nhiều máy, báo đã tách phiếu, màn thợ nhận nhiều máy một chuyến."""
from bs4 import BeautifulSoup
from sqlalchemy import select
from app.models import ServiceTicket
from test_mm1_machine_split import job, split
from test_mv1_workshop import onsite, photo
from test_technician_stock import held
from test_my_work import people, purchase_people
from test_web import service_login


def test_picker_is_checkboxes_without_none_chip(db,job,client):
    p,_,t,eqs=job;service_login(client,p['dispatcher'])
    page=BeautifulSoup(client.get(f'/dich-vu/{t.id}').text,'html.parser')
    picks=page.find_all('input',attrs={'name':'machine_pick'})
    assert picks and all(i['type']=='checkbox' for i in picks)
    assert not page.find('input',attrs={'name':'machine_pick','value':'NONE'})
    assert page.find('input',attrs={'name':'machine_pick','value':'SERIAL:'+eqs[0].serial}).has_attr('checked')
    assert page.find(attrs={'data-machine-split-note':True})


def test_two_picks_show_split_notice(db,job,client):
    p,_,t,eqs=job;csrf=service_login(client,p['dispatcher'])
    r=client.post(f'/dich-vu/{t.id}/may',data=dict(csrf=csrf,version=t.version,machine_pick=['SERIAL:'+eqs[0].serial,'SERIAL:'+eqs[1].serial]))
    assert r.status_code==200
    db.expire_all();child=db.scalars(select(ServiceTicket).where(ServiceTicket.split_from_id==t.id)).one()
    notice=BeautifulSoup(r.text,'html.parser').find(attrs={'data-split-done':True})
    assert notice and child.number in notice.text


def test_trip_screen_lists_split_machines_and_brings_all(db,job,client):
    p,_,t,eqs=job;eqs=eqs[:2];tickets=split(db,p,t,eqs);csrf=service_login(client,p['technician'])
    child=tickets[1];page=client.get(f'/tho/phieu/{t.id}')
    assert page.status_code==200
    soup=BeautifulSoup(page.text,'html.parser')
    block=soup.find(attrs={'data-trip-machine':str(child.id)})
    assert block and eqs[1].serial in block.text
    assert soup.find('textarea',attrs={'name':f'trip-{child.id}-condition_note'})
    assert soup.find('input',attrs={'name':f'trip-{child.id}-version'})
    for x in tickets:photo(db,p,x)
    data=dict(csrf=csrf,version=t.version,machine_version=eqs[0].version,accessories='KHONG_CO',condition_note='Máy gốc',
        material_product='TD-0001',material_qty='1')
    data.update({f'trip-{child.id}-version':child.version,f'trip-{child.id}-machine_version':eqs[1].version,
        f'trip-{child.id}-accessories':'KHONG_CO',f'trip-{child.id}-condition_note':'Máy hai'})
    r=client.post(f'/tho/phieu/{t.id}/mang-ve-xuong',data=data,follow_redirects=False)
    assert r.status_code==303,r.text[:500]
    db.expire_all();assert all(db.get(ServiceTicket,x.id).hold_reason=='CHO_SUA_XUONG' for x in tickets)


def test_trip_error_keeps_child_fields(db,job,client):
    p,_,t,eqs=job;eqs=eqs[:2];tickets=split(db,p,t,eqs);photo(db,p,t);csrf=service_login(client,p['technician'])
    child=tickets[1]
    data=dict(csrf=csrf,version=t.version,machine_version=eqs[0].version,accessories='KHONG_CO',condition_note='Máy gốc',
        material_product='TD-0001',material_qty='1')
    data.update({f'trip-{child.id}-version':child.version,f'trip-{child.id}-machine_version':eqs[1].version,
        f'trip-{child.id}-accessories':'KHONG_CO',f'trip-{child.id}-condition_note':'Ghi chú giữ lại'})
    r=client.post(f'/tho/phieu/{t.id}/mang-ve-xuong',data=data,follow_redirects=False)
    assert r.status_code==400 and 'Ghi chú giữ lại' in r.text

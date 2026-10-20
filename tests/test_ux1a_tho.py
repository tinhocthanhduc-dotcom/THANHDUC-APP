"""UX1a: màn thợ dùng route cũ, không thay đổi nghiệp vụ."""
from html.parser import HTMLParser
from urllib.parse import parse_qs,urlsplit

import pytest
from sqlalchemy import select

from app import attachments,equipment,service_charges,service_tickets,sales_orders
from app.models import Customer,CustomerPhone,CustomerSite,ServiceStep,Warehouse
from app.schemas import EquipmentIn,ServiceCharges
from app.web import next_steps,nav
from conftest import PASSWORD
from test_my_work import people,purchase_people,ticket,NOW,Page
from test_technician_stock import held
from test_technician_deliveries import setup,assigned
from test_web import service_login,d5_image
from web_forms import Forms


class Screen(HTMLParser):
    def __init__(self,html):
        super().__init__();self.tags=[];self.other=False;self.other_depth=0;self.forms={};self.current=None;self.feed(html)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);self.tags.append((tag,a,self.other))
        if tag=='details' and (self.other or 'tho-other' in a.get('class','')):
            self.other_depth+=1;self.other=True
        if tag=='form':self.current=a.get('id');self.forms[self.current]=dict(a,other=self.other)
    def handle_endtag(self,tag):
        if tag=='details' and self.other_depth:
            self.other_depth-=1;self.other=bool(self.other_depth)


def login(client,p,role='technician'):
    service_login(client,p[role])


@pytest.mark.parametrize('role',['sales','warehouse','finance','dispatcher','director'])
def test_only_literal_technician_can_enter(client,people,role):
    login(client,people,role)
    assert client.get('/tho').status_code==403
    assert client.get('/tho/phieu/1').status_code==403


def test_ownership_service_and_delivery_list(client,db,setup):
    p=setup;mine=ticket(db,p,'DA_GIAO');other=ticket(db,p,'DA_GIAO',who='other_tech')
    a=assigned(db,p);b=assigned(db,p);b.delivery_assignee_id=p['other_tech'].id;db.commit()
    login(client,p);page=client.get('/tho');assert page.status_code==200
    assert mine.number in page.text and a.number in page.text and other.number not in page.text and b.number not in page.text
    login(client,p,'other_tech');assert client.get(f'/tho/phieu/{mine.id}').status_code==403


def test_home_brand_and_minimal_menu(client,people):
    p=people;r=client.post('/dang-nhap',data=dict(username=p['technician'].username,password=PASSWORD),follow_redirects=False)
    assert r.status_code==303 and r.headers['location']=='/tho'
    assert client.get('/',follow_redirects=False).headers['location']=='/tho'
    page=client.get('/tho');links=[a['href'] for tag,a,_ in Screen(page.text).tags if tag=='a' and 'data-nav-item' in a]
    assert links==['/tho','/hang-toi-giu','/tien-toi-giu','/thiet-bi']
    assert next(a['href'] for tag,a,_ in Screen(page.text).tags if a.get('class')=='brand')=='/tho'
    login(client,p,'dispatcher');assert client.get('/',follow_redirects=False).headers['location']=='/dieu-phoi'


def test_vietnam_appointment_order_overdue_and_unappointed_last(client,db,people):
    p=people;late=ticket(db,p,'DA_HEN',appointment=NOW.replace(hour=14));early=ticket(db,p,'DA_HEN',appointment=NOW.replace(hour=9))
    working=ticket(db,p,'DANG_LAM');unscheduled=ticket(db,p,'DA_GIAO')
    login(client,p);page=client.get('/tho')
    assert page.text.index(early.number)<page.text.index(late.number)<page.text.index(working.number)<page.text.index(unscheduled.number)
    a=next(a for tag,a,_ in Screen(page.text).tags if a.get('data-job')==f'service:{early.id}')
    assert 'service-overdue' in a['class']
    assert '18/10/2026 09:00' in page.text


@pytest.mark.parametrize('state',['DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM'])
def test_card_next_action_matches_u2(client,db,people,state):
    p=people;t=ticket(db,p,state);label='Nhận đơn' if state=='DA_GIAO' else next_steps.service(db,p['technician'],t)['primary']['label']
    login(client,p);page=client.get('/tho')
    labels=[a['data-next-label'] for tag,a,_ in Screen(page.text).tags if 'data-next-label' in a]
    assert labels==[label]


def test_only_current_step_and_exceptions_are_collapsed(client,db,people):
    p=people;t=ticket(db,p,'DA_HEN');login(client,p)
    page=client.get(f'/tho/phieu/{t.id}');screen=Screen(page.text)
    assert page.status_code==200 and 'service-DANG_LAM' in screen.forms
    assert 'service-HOAN_THANH' not in screen.forms and 'service-charges' not in screen.forms and 'service-assign' not in screen.forms
    assert screen.forms['service-transfer']['other'] and screen.forms['service-no-show']['other']
    assert 'service-DA_HEN' not in screen.forms
    service_tickets.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    page=client.get(f'/tho/phieu/{t.id}');screen=Screen(page.text)
    assert 'service-complete' in screen.forms and 'service-charges' not in screen.forms and 'service-assign' not in screen.forms
    assert screen.forms['service-DA_HEN']['other'] and screen.forms['service-TAM_DUNG']['other'] and screen.forms['part-create']['other']
    assert all(not f['other'] for key,f in screen.forms.items() if key=='service-complete')


def test_only_own_material_warehouse_and_completion_checks(client,db,held):
    p,own,_=held;t=ticket(db,p,'DANG_LAM');login(client,p)
    page=client.get(f'/tho/phieu/{t.id}');screen=Screen(page.text)
    assert not any('data-charge-warehouse' in a for tag,a,_ in screen.tags)
    assert 'Vật tư đang giữ' in page.text and 'name="line_price"' not in page.text
    checks={a['data-check']:a['data-ok'] for _,a,_ in screen.tags if 'data-check' in a}
    assert checks['photo']==checks['billing']=='0' and '✗' in page.text
    button=next(a for tag,a,_ in screen.tags if 'data-tho-primary' in a);assert 'disabled' in button
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    service_charges.set_charges(db,p['technician'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành'))
    page=client.get(f'/tho/phieu/{t.id}');screen=Screen(page.text)
    assert all(a['data-ok']=='1' for _,a,_ in screen.tags if 'data-check' in a)
    assert 'disabled' not in next(a for _,a,_ in screen.tags if 'data-tho-primary' in a)


def test_safe_return_to_transition_replay_and_error_keeps_screen(client,db,people):
    p=people;t=ticket(db,p,'DA_GIAO');login(client,p);path=f'/tho/phieu/{t.id}'
    page=client.get(path);form=Page(page.text).forms['service-DA_NHAN'];data=form['fields']
    assert data['return_to']=='tho'
    assert client.request('POST',form['action'],data={k:v for k,v in data.items() if k!='csrf'}).status_code==403
    response=client.post(form['action'],data=data,follow_redirects=False)
    assert response.status_code==303 and response.headers['location']==path+'?da_luu=1'
    assert client.post(form['action'],data=data,follow_redirects=False).headers['location']==path+'?da_luu=1'
    db.expire_all();assert len(service_tickets.load(db,p['technician'],t.id).steps)==3
    form=Page(client.get(path).text).forms['service-DA_HEN']
    error=client.post(form['action'],data={**form['fields'],'appointment_at':'','note':'Giữ lại ghi chú'})
    assert error.status_code==400 and 'data-technician-ticket' in error.text and 'Giữ lại ghi chú' in error.text
    form=Page(error.text).forms['service-DA_HEN']
    response=client.post(form['action'],data={**form['fields'],'appointment_at':NOW.isoformat(),'return_to':'https://x.com'},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']==f'/dich-vu/{t.id}?da_luu=1'


def test_contacts_maps_site_overrides_and_missing_hidden(client,db,people):
    p=people;customer=db.get(Customer,'KTD-000001');customer.address='58 Mã Lò, Hồ Chí Minh';customer.phones=[CustomerPhone(phone='0901234567')]
    site=CustomerSite(customer_code=customer.code,name='Văn phòng',address='12 Nguyễn Văn A, Q.1',contact_phone='+84 912 345 678',created_by=p['dispatcher'].id)
    db.add(site);db.commit();t=ticket(db,p,'DA_GIAO');t.site_id=site.id;db.commit()
    login(client,p);page=client.get('/tho');links=[a.get('href','') for tag,a,_ in Screen(page.text).tags if tag=='a']
    assert 'tel:+84912345678' in links
    maps=next(href for href in links if href.startswith('https://www.google.com/maps/search/'))
    assert parse_qs(urlsplit(maps).query)=={'api':['1'],'query':[site.address]}
    t.site_id=None;customer.address='';customer.phones=[];db.commit()
    page=client.get('/tho');links=[a.get('href','') for tag,a,_ in Screen(page.text).tags if tag=='a']
    assert not any(link.startswith(('tel:','https://www.google.com/maps/')) for link in links)


def test_completed_reports_then_returns_to_today(client,db,people):
    p=people;t=ticket(db,p,'HOAN_THANH');login(client,p);path=f'/tho/phieu/{t.id}'
    page=client.get(path);assert page.status_code==200 and 'Phải thu' in page.text
    form=Page(page.text).forms['receipt-report']
    response=client.post(form['action'],data={**form['fields'],'amount':'300.000','method':'TIEN_MAT','received_at':NOW.date().isoformat()},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']==path+'?da_luu=1'
    db.expire_all();form=Page(client.get(path).text).forms['receipt-report']
    response=client.post(form['action'],data={**form['fields'],'amount':'200.000','method':'TIEN_MAT','received_at':NOW.date().isoformat()},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/tho'


def test_charges_photo_and_completion_post_return_to_mobile(client,db,people):
    p=people;t=ticket(db,p,'DANG_LAM');login(client,p);path=f'/tho/phieu/{t.id}'
    page=client.get(path);screen=Screen(page.text)
    photo=next(a for _,a,_ in screen.tags if 'data-tech-photos' in a)
    response=client.post(path+'/anh',data={'csrf':photo['data-csrf']},files={'file':('sau.png',d5_image(),'image/png')},follow_redirects=False)
    assert response.status_code==201 and response.json()['id']
    form=Page(client.get(path).text).forms['service-complete']
    response=client.post(form['action'],data={**form['fields'],'billing':'MIEN_PHI','free_reason':'Bảo hành'},follow_redirects=False)
    assert response.headers['location']=='/tho'


def test_pending_parts_show_status_and_continue_without_charges(client,db,people):
    p=people;t=ticket(db,p,'CHO_LINH_KIEN');login(client,p)
    page=client.get(f'/tho/phieu/{t.id}');screen=Screen(page.text)
    assert page.status_code==200 and 'Tiếp tục làm' in page.text and 'Linh kiện cần cấp' in page.text
    assert 'service-charges' not in screen.forms and 'service-HOAN_THANH' not in screen.forms


def test_part_request_returns_and_preserves_invalid_form(client,db,held):
    p,_,_=held;t=ticket(db,p,'DANG_LAM');login(client,p);path=f'/tho/phieu/{t.id}'
    form=Page(client.get(path).text).forms['part-create']
    response=client.post(form['action'],data={**form['fields'],'product_code':'TD-0001','qty':'0','reason':'Chờ drum'})
    assert response.status_code==400 and 'data-technician-ticket' in response.text and 'Chờ drum' in response.text
    form=Page(response.text).forms['part-create']
    response=client.post(form['action'],data={**form['fields'],'product_code':'TD-0001','qty':'1','reason':'Chờ drum'},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']==path+'?da_luu=1'
    assert 'Chờ drum' in client.get(path).text


def test_machine_check_and_bring_back_current_block(client,db,people):
    from app import equipment_custody
    from app.schemas import CustodyIntake
    p=people;eq=equipment.create(db,p['dispatcher'],EquipmentIn(serial='UX-MACHINE',product_code='TD-0001',customer_code='KTD-000001'))
    t=ticket(db,p,'DANG_LAM');t.kind='MANG_VE';t.serial=eq.serial;db.commit()
    attachments.add(db,p['technician'],'equipment_custody',str(t.id),'NHAN_MAY','nhan.png',d5_image())
    equipment_custody.receive(db,p['technician'],t.id,CustodyIntake(version=eq.version,accessories=[],condition_note='Vỏ trầy'))
    login(client,p);page=client.get(f'/tho/phieu/{t.id}');screen=Screen(page.text)
    check=next(a for _,a,_ in screen.tags if a.get('data-check')=='machine')
    assert check['data-ok']=='0' and 'Máy đã trả khách' in page.text
    assert '/tra-khach' in page.text


def test_delivery_from_today_uses_existing_post_and_returns_to_today(client,db,setup):
    p=setup;o=assigned(db,p);login(client,p)
    page=client.get(f'/giao-don/{o.id}?return_to=tho')
    form=next(f for f in Forms(page.text,page.url).forms if f['path']==f'/giao-don/{o.id}/failed')
    assert form['fields']['return_to']=='tho'
    response=client.post(form['path'],data={**form['fields'],'reason':'KHACH_VANG','note':'Đã gọi khách nhưng không gặp'},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/tho'


def test_contact_prefers_contact_phone():
    from types import SimpleNamespace as NS
    from app.web.technician import contact
    customer = NS(address='12 Lê Lợi', phones=[NS(phone='0901 111 111', is_contact=False), NS(phone='0909 999 999', is_contact=True)])
    assert contact(customer)['phone'] == '0909999999'

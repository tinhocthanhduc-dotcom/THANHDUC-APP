from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import attachments, service_charges, service_tickets, technician_work, part_requests, my_work
from app.models import Attachment, Product, ServiceLine, StockDoc, SalesReceipt, ServiceVisit, TechnicianReturn, StockLedger
from app.schemas import ServiceCharges, PartRequestIn
from test_my_work import people,purchase_people,ticket,Page,DAY
from test_technician_stock import held,transfer,balance
from test_ux1b_hoan_thanh_gop import job,snapshot
from test_web import service_login,d5_image


def data(client,p,t,**changes):
    service_login(client,p['technician']);html=client.get(f'/tho/phieu/{t.id}').text
    fields=Page(html).forms['service-complete']['fields']
    return fields|dict(customer_amount='540.000',collection_mode='FULL',method='TIEN_MAT',free_reason='',material_product=['TD-0001'],material_qty=['2'],unit_price='1',note='Đã chạy thử')|changes


def post(client,t,form):return client.post(f'/tho/phieu/{t.id}/hoan-thanh',data=form,follow_redirects=False)


def test_customer_total_material_zero_px_cost_and_report(client,db,job):
    p,own,t=job;r=post(client,t,data(client,p,t));assert r.status_code==303
    db.expire_all();assert (t.subtotal,t.vat_total,t.receivable)==(500000,40000,540000)
    materials=[l for l in t.lines if l.kind=='VAT_TU'];fees=[l for l in t.lines if l.kind=='PHI']
    assert len(materials)==len(fees)==1 and materials[0].unit_price==materials[0].amount==materials[0].vat_amount==0
    assert fees[0].description==technician_work.FEE_DESCRIPTION
    px=db.get(StockDoc,materials[0].stock_doc_id);assert px.lines[0].qty==2 and px.lines[0].unit_cost==1000
    assert balance(db,own.code)==1 and service_charges.material_costs(db,[t.id])[t.id]==2000
    receipt=db.scalar(select(SalesReceipt).where(SalesReceipt.ticket_id==t.id));assert receipt.amount==540000 and receipt.status=='CHO_XAC_NHAN'
    service_login(client,p['dispatcher']);html=client.get(f'/dich-vu/{t.id}').text;assert 'Chi phí vật tư (giá vốn): 2.000' in html
    from app.web.dispatch import _report,TZ
    start=t.steps[-1].at.astimezone(TZ).date();since=__import__('datetime').datetime.combine(start,__import__('datetime').time.min,TZ)
    rows,_=_report(db,start,start,since,since+timedelta(days=1));row=next(row for row in rows if row['user_id']==p['technician'].id)
    assert (row['revenue'],row['material_cost'],row['margin'])==(500000,2000,498000)
    csv=client.get(f'/dieu-phoi/bao-cao.csv?from={start}&to={start}');assert 'Chi phí vật tư' in csv.text and '498000' in csv.text


@pytest.mark.parametrize('total',[100001,100000,6,7])
def test_rounding_actual_and_full_receipt_always_agree(client,db,job,total):
    p,_,t=job;net,tax=technician_work.net_from_gross(total)
    r=post(client,t,data(client,p,t,customer_amount=str(total)));assert r.status_code==303
    db.expire_all();assert t.receivable==net+tax
    receipt=db.scalar(select(SalesReceipt).where(SalesReceipt.ticket_id==t.id));assert receipt.amount==t.receivable
    possible=[n for n in range(max(0,net-4),net+5) if n+service_charges._round(Decimal(n)*8/100)==total]
    if possible:assert t.receivable==total


@pytest.mark.parametrize('mode,partial,amount',[('FULL','',540000),('PARTIAL','100.000',100000),('','',0)])
def test_collection_modes(client,db,job,mode,partial,amount):
    p,_,t=job;assert post(client,t,data(client,p,t,collection_mode=mode,partial_amount=partial)).status_code==303
    db.expire_all();assert t.receivable==540000
    rows=list(db.scalars(select(SalesReceipt).where(SalesReceipt.ticket_id==t.id)));assert [r.amount for r in rows]==([amount] if amount else [])


@pytest.mark.parametrize('partial',['0','540.000','900.000'])
def test_invalid_partial_rolls_back(client,db,job,partial):
    p,_,t=job;form=data(client,p,t,collection_mode='PARTIAL',partial_amount=partial);before=snapshot(db,t)
    r=post(client,t,form);assert r.status_code==400 and 'BAD_PARTIAL_AMOUNT' in r.text and snapshot(db,t)==before


def test_free_reason_and_material_cost(client,db,job):
    p,_,t=job;form=data(client,p,t,collection_mode='FREE',customer_amount='',free_reason='');before=snapshot(db,t)
    assert post(client,t,form).status_code==400 and snapshot(db,t)==before
    form=data(client,p,t,collection_mode='FREE',customer_amount='',free_reason='Bảo hành drum')
    assert post(client,t,form).status_code==303;db.expire_all();assert t.billing=='MIEN_PHI' and t.receivable==0
    assert db.query(SalesReceipt).count()==0 and service_charges.material_costs(db,[t.id])[t.id]==2000


def test_zero_customer_total_and_overstock_no_mutation(client,db,job):
    p,_,t=job
    for changes,code in [(dict(customer_amount='0'),'CUSTOMER_AMOUNT_REQUIRED'),(dict(material_qty=['4']),'NEGATIVE_STOCK')]:
        form=data(client,p,t,**changes);before=snapshot(db,t);r=post(client,t,form)
        assert r.status_code==400 or r.status_code==409
        assert code in r.text and snapshot(db,t)==before


def test_photos_three_auto_upload_hide_own_and_permission(client,db,held):
    p,_,_=held;t=ticket(db,p,'DANG_LAM');csrf=service_login(client,p['technician']);rows=[]
    for i in range(3):
        r=client.post(f'/tho/phieu/{t.id}/anh',data=dict(csrf=csrf),files={'file':(f'{i}.png',d5_image(),'image/png')});assert r.status_code==201;rows.append(r.json())
    assert db.query(Attachment).filter_by(entity_id=str(t.id),tag='SAU',hidden=False).count()==3
    html=client.get(f'/tho/phieu/{t.id}').text;assert 'data-check="photo"' in html and 'multiple' in html
    csrf_b=service_login(client,p['other_tech']);assert client.post(f'/tho/phieu/{t.id}/anh/{rows[0]["id"]}/xoa',data=dict(csrf=csrf_b,version=1)).status_code==403
    csrf=service_login(client,p['technician']);assert client.post(f'/tho/phieu/{t.id}/anh/{rows[0]["id"]}/xoa',data=dict(csrf=csrf,version=1)).status_code==200
    db.expire_all();a=db.get(Attachment,rows[0]['id']);assert a.hidden and a.hidden_reason=='Thợ xóa trước khi hoàn thành'
    assert db.query(Attachment).filter_by(entity_id=str(t.id),hidden=False).count()==2
    assert post(client,t,data(client,p,t,material_product=[],material_qty=[])).status_code==303
    csrf=service_login(client,p['technician']);assert client.post(f'/tho/phieu/{t.id}/anh/{rows[1]["id"]}/xoa',data=dict(csrf=csrf,version=1)).status_code==409


def test_fee_suggestions_and_no_cost_in_technician_screens(client,db,job):
    p,_,t=job;db.add(Product(code='TD-9001',name='Vệ sinh máy',group_name='Dịch vụ',sale_price=540000));db.commit();service_login(client,p['technician'])
    html=client.get(f'/tho/phieu/{t.id}').text;assert 'data-fee-suggestion="540000"' in html and 'Vệ sinh máy' in html
    assert 'Chi phí vật tư' not in html and 'giá vốn' not in html and 'line_price' not in html and 'name="vat_' not in html
    assert html.index('data-tech-collection')>html.index('Lịch sử')
    assert 'form="service-complete" type="radio" name="collection_mode"' in html
    assert 'giá vốn' not in client.get('/tho').text


def test_warranty_defaults_free_and_cannot_charge(client,db,job):
    p,_,t=job;t.kind='BAO_HANH';t.warranty_basis='MAY_CON_HAN';t.billing='MIEN_PHI';t.free_reason='Bảo hành máy';db.commit()
    service_login(client,p['technician']);html=client.get(f'/tho/phieu/{t.id}').text;assert 'value="FREE" checked' in html and 'Bảo hành máy' in html
    form=data(client,p,t,collection_mode='FULL');before=snapshot(db,t);assert post(client,t,form).status_code==403 and snapshot(db,t)==before
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',billing_reason='Thu thêm dịch vụ ngoài bảo hành',lines=[dict(kind='PHI',description='Dịch vụ bổ sung',qty=1,unit_price=500000,vat_rate=8)]))
    html=client.get(f'/tho/phieu/{t.id}').text;assert 'value="FREE" checked' not in html
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành máy',lines=[]))
    assert post(client,t,data(client,p,t,collection_mode='FREE',free_reason='Bảo hành máy')).status_code==303;db.expire_all();assert t.receivable==0 and t.billing=='MIEN_PHI'


def transfer_form(client,p,t,failed=False):
    csrf=service_login(client,p['technician']);return dict(csrf=csrf,version=t.version,assignee_id=p['other_tech'].id,reason='Nhờ thợ chuyên môn hỗ trợ',failed='1' if failed else '0')


@pytest.mark.parametrize('status',['DA_GIAO','DA_NHAN','DA_HEN','DANG_LAM','CHO_LINH_KIEN','TAM_DUNG'])
def test_direct_transfer_clears_charges_returns_stock_preserves_appointment(client,db,held,status):
    p,own,_=held;transfer(db,p);t=ticket(db,p,'DANG_LAM' if status in ('CHO_LINH_KIEN','TAM_DUNG') else status)
    if status in ('CHO_LINH_KIEN','TAM_DUNG'):service_tickets.transition(db,p['technician'],t.id,status,t.version,note='Chờ xử lý',hold_reason='KHAC' if status=='TAM_DUNG' else '')
    service_charges.set_charges(db,p['technician'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[dict(kind='VAT_TU',product_code='TD-0001',warehouse_code=own.code,qty=2,unit_price=1),dict(kind='PHI',description='Phí cũ',qty=1,unit_price=500000)]))
    appointment=t.appointment_at;form=transfer_form(client,p,t,status in ('DANG_LAM','CHO_LINH_KIEN','TAM_DUNG'));r=client.post(f'/tho/phieu/{t.id}/chuyen-tho',data=form,follow_redirects=False);assert r.status_code==303
    db.expire_all();assert t.assignee_id==p['other_tech'].id and t.status=='DA_GIAO' and t.appointment_at==appointment and not t.lines
    request=db.scalar(select(TechnicianReturn).where(TechnicianReturn.ticket_id==t.id));assert request.status=='CHO_NHAN' and request.lines[0].qty==2 and request.technician_id==p['technician'].id
    assert balance(db,own.code)==3 and request.stock_doc_id is None
    if status=='DANG_LAM':assert db.scalar(select(ServiceVisit).where(ServiceVisit.ticket_id==t.id)).outcome=='KHONG_LAM_DUOC'
    assert client.get(f'/tho/phieu/{t.id}').status_code==403
    service_login(client,p['other_tech']);assert 'Nhận đơn' in client.get('/tho').text
    service_login(client,p['dispatcher']);assert 'Phiếu vừa chuyển thợ' in client.get('/viec-cua-toi').text


@pytest.mark.parametrize('change',[dict(reason=''),dict(assignee_id='SELF'),dict(failed='0')])
def test_transfer_invalid_rolls_back(client,db,job,change):
    p,_,t=job;form=transfer_form(client,p,t,True)|change
    if form['assignee_id']=='SELF':form['assignee_id']=p['technician'].id
    before=snapshot(db,t);r=client.post(f'/tho/phieu/{t.id}/chuyen-tho',data=form);assert r.status_code in (400,409) and snapshot(db,t)==before
    assert db.query(TechnicianReturn).count()==0


def test_transfer_other_and_completed_forbidden(client,db,job):
    p,_,t=job;form=transfer_form(client,p,t,True);form['csrf']=service_login(client,p['other_tech']);assert client.post(f'/tho/phieu/{t.id}/chuyen-tho',data=form).status_code==403
    assert post(client,t,data(client,p,t)).status_code==303;db.expire_all();form=transfer_form(client,p,t,True);assert client.post(f'/tho/phieu/{t.id}/chuyen-tho',data=form).status_code==409


def test_contextual_other_actions_and_parts_atomic(client,db,held):
    p,_,_=held;t=ticket(db,p,'DA_HEN');service_login(client,p['technician']);html=client.get(f'/tho/phieu/{t.id}').text
    assert 'service-transfer"' in html and 'Khách hẹn lại' not in html and 'Xin chuyển việc' not in html
    service_tickets.transition(db,p['technician'],t.id,'DANG_LAM',t.version);html=client.get(f'/tho/phieu/{t.id}').text
    assert 'service-transfer"' not in html and 'service-transfer-failed' in html and 'Cần linh kiện' in html
    before=snapshot(db,t);csrf=service_login(client,p['technician']);form=dict(csrf=csrf,version=t.version,product_code='MISSING',qty=1,reason='Cần thay',return_to='tho')
    r=client.post(f'/dich-vu/{t.id}/yeu-cau-linh-kien',data=form);assert r.status_code==400 and snapshot(db,t)==before
    form['product_code']='TD-0001';assert client.post(f'/dich-vu/{t.id}/yeu-cau-linh-kien',data=form,follow_redirects=False).status_code==303;db.expire_all();assert t.status=='CHO_LINH_KIEN'

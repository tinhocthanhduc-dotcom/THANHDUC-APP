"""MV1: workshop custody, immutable early issues, locked return and repair attribution."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select,func

from app import (attachments,equipment,equipment_custody as custody,service_charges,service_tickets as svc,
    service_workshop as workshop,technician_work,stock_docs,stock_posting)
from app.errors import BusinessError
from app.models import (AuditLog,Equipment,EquipmentCustody,FundLedger,SalesReceipt,ServiceLine,ServiceStep,ServiceVisit,StockDoc,StockLedger)
from app.schemas import CustodyIntake,CustodyHandover,EquipmentIn,ServiceCharges,StockDocIn
from test_my_work import people,purchase_people,ticket,NOW,DAY
from test_technician_stock import held,transfer,balance
from test_web import d5_image,service_login


def error(code,fn,*args,**kw):
    with pytest.raises(BusinessError) as e:fn(*args,**kw)
    assert e.value.code==code
    return e.value


def snapshot(db,t):
    db.expire_all();db.refresh(t)
    eq=db.get(Equipment,t.serial) if t.serial else None
    return (svc.snapshot(t),[(x.id,x.line_no,x.product_code,x.warehouse_code,x.qty,x.unit_price,x.vat_rate,x.stock_doc_id) for x in t.lines],
        (eq.custody_location,eq.custody_holder_id,eq.custody_ticket_id,eq.version) if eq else None,
        [(v.id,v.outcome,v.ended_at) for v in db.scalars(select(ServiceVisit).where(ServiceVisit.ticket_id==t.id))],
        [db.query(m).count() for m in (StockDoc,StockLedger,EquipmentCustody,ServiceStep,SalesReceipt,FundLedger,AuditLog)])


@pytest.fixture
def onsite(db,held):
    p,a,b=held;transfer(db,p,qty=4)
    t=ticket(db,p,'DANG_LAM')
    eq=equipment.create(db,p['dispatcher'],EquipmentIn(serial='MV1-MAY',product_code='TD-0001',customer_code=t.customer_code))
    t.serial=eq.serial;db.commit()
    return p,a,b,t,eq


def intake(eq,**kw):
    return CustodyIntake(version=eq.version,accessories=kw.pop('accessories',[]),condition_note=kw.pop('condition_note','Vỏ xước nhẹ'),**kw)


def photo(db,p,t,tag='NHAN_MAY',who='technician'):
    attachments.add(db,p[who],'equipment_custody' if tag in ('NHAN_MAY','TRA_MAY') else 'service_ticket',str(t.id),tag,'may.png',d5_image())


@pytest.mark.parametrize('missing,code',[('photo','PHOTO_REQUIRED'),('condition','CONDITION_REQUIRED')])
def test_intake_missing_data_is_atomic(db,onsite,missing,code):
    p,a,_,t,eq=onsite
    if missing!='photo':photo(db,p,t)
    before=snapshot(db,t)
    error(code,workshop.bring,db,p['technician'],t.id,t.version,intake(eq,condition_note='' if missing=='condition' else 'Vỏ xước'),[])
    assert snapshot(db,t)==before and balance(db,a.code)==4


@pytest.mark.parametrize('qty',[0,2])
def test_bring_keeps_number_and_issues_once_at_average_cost(db,onsite,qty):
    p,a,_,t,eq=onsite;number=t.number;photo(db,p,t)
    workshop.bring(db,p['technician'],t.id,t.version,intake(eq),[dict(product_code='TD-0001',qty=qty,warehouse_code='K58',unit_price=999999,vat_rate=10)])
    assert t.number==number and t.kind=='MANG_VE' and t.status=='DANG_LAM'
    assert eq.custody_location=='THO_GIU' and eq.custody_holder_id==p['technician'].id and eq.custody_ticket_id==t.id
    assert balance(db,a.code)==4-qty
    parts=[x for x in t.lines if x.kind=='VAT_TU']
    assert len(parts)==bool(qty)
    if qty:
        line=parts[0];doc=db.get(StockDoc,line.stock_doc_id)
        assert line.warehouse_code==a.code and line.unit_price==line.vat_rate==0
        assert doc.kind=='PX' and doc.status=='DA_GHI' and doc.source_kind=='SERVICE_TICKET'
        assert doc.lines[0].qty==qty and doc.lines[0].unit_cost==1000
        assert service_charges.material_costs(db,[t.id])[t.id]==Decimal(qty*1000)
    step=db.scalar(select(ServiceStep).where(ServiceStep.ticket_id==t.id).order_by(ServiceStep.id.desc()))
    assert step.action=='workshop' and step.actor_id==p['technician'].id
    assert db.query(FundLedger).count()==db.query(SalesReceipt).count()==0


def test_over_stock_rolls_back_intake_type_and_issue(db,onsite):
    p,a,_,t,eq=onsite;photo(db,p,t);before=snapshot(db,t)
    error('NEGATIVE_STOCK',workshop.bring,db,p['technician'],t.id,t.version,intake(eq),[dict(product_code='TD-0001',qty=5)])
    assert snapshot(db,t)==before and balance(db,a.code)==4


def test_warranty_bring_preserves_kind_and_link(db,onsite):
    p,_,_,t,eq=onsite;parent=ticket(db,p,'DA_DONG')
    t.kind='BAO_HANH';t.warranty_of_id=parent.id;t.warranty_basis='PHIEU_TRUOC';db.commit();photo(db,p,t)
    workshop.bring(db,p['technician'],t.id,t.version,intake(eq),[])
    assert t.kind=='BAO_HANH' and t.warranty_of_id==parent.id and t.warranty_basis=='PHIEU_TRUOC'


@pytest.fixture
def brought(db,onsite):
    p,a,b,t,eq=onsite;photo(db,p,t)
    workshop.bring(db,p['technician'],t.id,t.version,intake(eq),[dict(product_code='TD-0001',qty=2)])
    return p,a,b,t,eq


def test_issued_line_cannot_be_deleted_changed_or_forged(db,brought):
    p,a,_,t,_=brought;original=service_charges.line_input(t.lines[0]);before=snapshot(db,t)
    for fields in ([],[original|dict(qty=1)],[original|dict(warehouse_code='K58')],[original|dict(stock_doc_id=original['stock_doc_id']+1)],[original,original]):
        error('LINE_ISSUED',service_charges.set_charges,db,p['technician'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành',lines=fields))
        assert snapshot(db,t)==before and balance(db,a.code)==2


@pytest.mark.parametrize('target',['DA_HUY','KHONG_LAM_DUOC'])
def test_open_ticket_cancellation_does_not_reverse_installed_parts(db,brought,target):
    p,a,_,t,_=brought;doc_id=t.lines[0].stock_doc_id
    photo(db,p,t,'TRA_MAY');custody.return_customer(db,p['technician'],t.id,intake(db.get(Equipment,t.serial)))
    svc.transition(db,p['dispatcher'],t.id,target,t.version,'Khách không sửa tiếp')
    assert db.get(StockDoc,doc_id).status=='DA_GHI' and balance(db,a.code)==2
    assert service_charges.material_costs(db,[t.id])[t.id]==2000


def test_technician_cancel_only_returns_unused_allocation(db,brought):
    p,a,_,t,_=brought
    # Return the machine first using KT4, then cancel. Installed parts stay issued.
    photo(db,p,t,'TRA_MAY');custody.return_customer(db,p['technician'],t.id,intake(db.get(Equipment,t.serial)))
    from app import technician_returns
    own=technician_returns.proposal(db,t)
    assert sum(row['qty'] for row in own['rows'])==0
    svc.transition(db,p['technician'],t.id,'DA_HUY',t.version,'Khách không sửa nữa')
    assert balance(db,a.code)==2 and db.get(StockDoc,t.lines[0].stock_doc_id).status=='DA_GHI'


@pytest.fixture
def repaired(db,brought):
    p,a,b,t,eq=brought
    # B has a different average cost.
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code=b.code,supplier_code='NCC-0001',doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty=2,unit_cost=3000)]));stock_posting.post(db,p['warehouse'],doc.id,doc.version)
    svc.assign(db,p['dispatcher'],t.id,p['other_tech'].id,t.version,'Sửa tại xưởng')
    row=custody.handover(db,p['dispatcher'],t.id,CustodyHandover(version=eq.version,location='THO_GIU',holder_id=p['other_tech'].id))
    custody.confirm(db,p['other_tech'],row.id,row.version)
    svc.transition(db,p['other_tech'],t.id,'DA_NHAN',t.version)
    svc.transition(db,p['other_tech'],t.id,'DANG_LAM',t.version)
    photo(db,p,t,'SAU','other_tech')
    workshop.repair_done(db,p['other_tech'],t.id,t.version,'540.000','FULL','',[dict(product_code='TD-0001',qty=1)],fix='Đã sửa xong')
    return p,a,b,t,eq


def test_second_technician_preserves_early_line_and_posts_only_new_parts(db,repaired):
    p,a,b,t,_=repaired
    assert t.status=='TAM_DUNG' and t.hold_reason=='SUA_XONG_CHO_TRA' and t.repaired_by==p['other_tech'].id
    assert t.total==t.receivable==540000 and t.subtotal==500000 and t.vat_total==40000
    parts=[x for x in t.lines if x.kind=='VAT_TU']
    assert len(parts)==2 and {x.warehouse_code for x in parts}=={a.code,b.code}
    assert len({x.stock_doc_id for x in parts})==2 and all(db.get(StockDoc,x.stock_doc_id).status=='DA_GHI' for x in parts)
    assert balance(db,a.code)==2 and balance(db,b.code)==1
    assert service_charges.material_costs(db,[t.id])[t.id]==5000
    assert db.query(SalesReceipt).count()==0 and db.query(FundLedger).count()==0
    assert db.scalar(select(ServiceStep).where(ServiceStep.ticket_id==t.id).order_by(ServiceStep.id.desc())).action=='repair_done'


def test_only_dispatcher_can_schedule_ready_hold_and_confirm_required(db,repaired,make_user):
    p,_,_,t,eq=repaired;c=make_user('mv1.returner','technician')
    error('FORBIDDEN',svc.transition,db,p['other_tech'],t.id,'DA_HEN',t.version,'Đi trả máy',NOW+timedelta(days=2))
    workshop.schedule_return(db,p['dispatcher'],t.id,t.version,NOW+timedelta(days=2),None,c.id,'Trả máy đã sửa')
    assert t.assignee_id==c.id and t.status=='DA_GIAO'
    pending=custody.pending(db,eq.serial);assert pending.holder_id==c.id and eq.custody_holder_id==p['other_tech'].id
    svc.transition(db,c,t.id,'DA_NHAN',t.version)
    error('MACHINE_NOT_RECEIVED',svc.transition,db,c,t.id,'DANG_LAM',t.version)
    custody.confirm(db,c,pending.id,pending.version)
    svc.transition(db,c,t.id,'DANG_LAM',t.version)
    assert eq.custody_holder_id==c.id and t.repaired_by==p['other_tech'].id
    before=snapshot(db,t)
    error('CHARGES_LOCKED',technician_work.complete,db,c,t.id,t.version,540001,'FULL','',[])
    assert snapshot(db,t)==before
    error('MACHINE_NOT_RETURNED',technician_work.complete,db,c,t.id,t.version,540000,'FULL','',[])
    assert snapshot(db,t)==before
    attachments.add(db,c,'equipment_custody',str(t.id),'TRA_MAY','tra.png',d5_image())
    technician_work.complete(db,c,t.id,t.version,540000,'FULL','',[],return_machine=intake(eq))
    assert t.status=='DA_DONG' and t.receivable==540000 and eq.custody_location=='TAI_KHACH'
    receipt=db.scalars(select(SalesReceipt)).one();assert receipt.amount==540000 and receipt.reported_by==c.id
    assert service_charges.material_costs(db,[t.id])[t.id]==5000


@pytest.mark.parametrize('missing,code',[('accessories','ACCESSORIES_REQUIRED'),('condition','CONDITION_REQUIRED'),('photo','PHOTO_REQUIRED')])
def test_bring_web_requires_intake_details_and_csrf(client,db,onsite,missing,code):
    p,a,_,t,eq=onsite
    if missing!='photo':photo(db,p,t)
    csrf=service_login(client,p['technician']);before=snapshot(db,t)
    form=dict(csrf=csrf,version=t.version,machine_version=eq.version,accessories='KHONG_CO',condition_note='Vỏ xước',material_product='TD-0001',material_qty='1',return_to='tho')
    if missing=='accessories':form.pop('accessories')
    if missing=='condition':form['condition_note']=''
    r=client.post(f'/dich-vu/{t.id}/mang-ve-xuong',data=form)
    assert r.status_code==400 and f'data-error-code="{code}"' in r.text
    assert snapshot(db,t)==before and balance(db,a.code)==4
    form['csrf']='invalid';assert client.post(f'/dich-vu/{t.id}/mang-ve-xuong',data=form).status_code==403
    assert snapshot(db,t)==before


def test_workshop_web_success_and_second_intake_hidden(client,db,onsite):
    p,a,_,t,eq=onsite;photo(db,p,t)
    csrf=service_login(client,p['technician'])
    r=client.post(f'/dich-vu/{t.id}/mang-ve-xuong',data=dict(csrf=csrf,version=t.version,machine_version=eq.version,accessories='KHONG_CO',condition_note='Vỏ xước',material_product='TD-0001',material_qty='2',return_to='tho'),follow_redirects=False)
    assert r.status_code==303,r.text
    db.expire_all();db.refresh(t)
    page=client.get(f'/tho/phieu/{t.id}');assert page.status_code==200,page.text
    assert 'Máy khách' in page.text and 'workshop-bring' not in page.text
    assert '/sua-xong-cho-tra' in page.text and 'Sửa xong, chờ trả máy' in page.text
    assert balance(db,a.code)==2


@pytest.mark.parametrize('who',['other_tech','warehouse','dispatcher'])
def test_intake_other_actors_forbidden(client,db,onsite,who):
    p,_,_,t,eq=onsite;before=snapshot(db,t)
    csrf=service_login(client,p[who])
    r=client.post(f'/dich-vu/{t.id}/mang-ve-xuong',data=dict(csrf=csrf,version=t.version,machine_version=eq.version,accessories='KHONG_CO',condition_note='Vỏ xước'))
    assert r.status_code==403 and snapshot(db,t)==before


def test_early_issue_failure_after_post_rolls_back_everything(db,onsite,monkeypatch):
    p,a,_,t,eq=onsite;photo(db,p,t);before=snapshot(db,t);post=stock_posting.post_in_tx
    def fail(*args,**kw):
        post(*args,**kw);raise BusinessError('TEST_EARLY_FAIL','Lỗi sau ghi PX')
    monkeypatch.setattr(stock_posting,'post_in_tx',fail)
    error('TEST_EARLY_FAIL',workshop.bring,db,p['technician'],t.id,t.version,intake(eq),[dict(product_code='TD-0001',qty=2)])
    assert snapshot(db,t)==before and balance(db,a.code)==4


@pytest.mark.parametrize('missing,code',[('result','FIX_REQUIRED'),('photo','PHOTO_REQUIRED'),('mode','BAD_COLLECTION'),('free_reason','REASON_REQUIRED')])
def test_repair_done_missing_data_rolls_back(db,brought,missing,code):
    p,a,_,t,_=brought
    if missing!='photo':photo(db,p,t,'SAU')
    before=snapshot(db,t)
    error(code,workshop.repair_done,db,p['technician'],t.id,t.version,540000,
        '' if missing=='mode' else 'FREE' if missing=='free_reason' else 'FULL','',[],fix='' if missing=='result' else 'Đã sửa xong')
    assert snapshot(db,t)==before and balance(db,a.code)==2


def test_wrong_hold_cannot_be_scheduled(db,brought):
    p,_,_,t,_=brought
    svc.transition(db,p['technician'],t.id,'TAM_DUNG',t.version,'Chờ khách',hold_reason='KHAC')
    before=snapshot(db,t)
    error('BAD_TRANSITION',svc.transition,db,p['dispatcher'],t.id,'DA_HEN',t.version,'Trả máy',NOW+timedelta(days=1))
    assert error('FORBIDDEN',svc.transition,db,p['technician'],t.id,'DA_HEN',t.version,'Trả máy',NOW+timedelta(days=1)).status==403
    assert snapshot(db,t)==before


def test_second_technician_cannot_change_first_issued_row(db,brought):
    p,a,_,t,_=brought;svc.assign(db,p['dispatcher'],t.id,p['other_tech'].id,t.version,'Đổi thợ sửa')
    original=service_charges.line_input(t.lines[0]);before=snapshot(db,t)
    for entries in ([],[original|dict(qty=1)]):
        error('LINE_ISSUED',service_charges.set_charges,db,p['other_tech'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành',lines=entries))
        assert snapshot(db,t)==before and balance(db,a.code)==2
    error('TECH_WAREHOUSE_ONLY',service_charges.set_charges,db,p['other_tech'],t.id,ServiceCharges(version=t.version,billing='MIEN_PHI',free_reason='Bảo hành',lines=[original,original|dict(stock_doc_id=None)]))
    assert snapshot(db,t)==before


@pytest.fixture
def returning(db,repaired,make_user):
    p,a,b,t,eq=repaired;p['returner']=make_user('mv1.c','technician')
    workshop.schedule_return(db,p['dispatcher'],t.id,t.version,NOW+timedelta(days=1),None,p['returner'].id)
    pending=custody.pending(db,eq.serial);custody.confirm(db,p['returner'],pending.id,pending.version)
    svc.transition(db,p['returner'],t.id,'DA_NHAN',t.version);svc.transition(db,p['returner'],t.id,'DANG_LAM',t.version)
    return p,a,b,t,eq


@pytest.mark.parametrize('mode,amount',[('FULL',540000),('PARTIAL',100000),('',0)])
def test_return_web_uses_fixed_fee_without_report_or_parts(client,db,returning,mode,amount):
    p,a,b,t,eq=returning;photo(db,p,t,'TRA_MAY','returner')
    csrf=service_login(client,p['returner']);page=client.get(f'/tho/phieu/{t.id}');assert page.status_code==200,page.text
    assert 'Trả máy đã sửa xong' in page.text and 'name="repair_result"' not in page.text
    assert 'name="material_product"' not in page.text and 'name="free_reason"' not in page.text
    r=client.post(f'/tho/phieu/{t.id}/hoan-thanh',data=dict(csrf=csrf,version=t.version,customer_amount='540000',collection_mode=mode,partial_amount='100000',method='TIEN_MAT',return_machine='1',machine_version=eq.version,accessories='KHONG_CO',return_to='tho'),follow_redirects=False)
    assert r.status_code==303,r.text
    db.expire_all();db.refresh(t);db.refresh(eq)
    assert t.status=='DA_DONG' and t.receivable==540000 and t.repaired_by==p['other_tech'].id
    assert eq.custody_location=='TAI_KHACH' and balance(db,a.code)==2 and balance(db,b.code)==1
    receipts=list(db.scalars(select(SalesReceipt)));assert [r.amount for r in receipts]==([amount] if amount else [])
    assert db.query(FundLedger).count()==0 and service_charges.material_costs(db,[t.id])[t.id]==5000


@pytest.mark.parametrize('change',['amount','materials','legacy-fee'])
def test_return_web_cannot_edit_price_or_materials(client,db,returning,change):
    p,_,_,t,eq=returning;photo(db,p,t,'TRA_MAY','returner');before=snapshot(db,t)
    csrf=service_login(client,p['returner'])
    body=dict(csrf=csrf,version=t.version,customer_amount='540001' if change=='amount' else '540000',collection_mode='FULL',return_machine='1',machine_version=eq.version,accessories='KHONG_CO',return_to='tho')
    if change=='materials':body.update(material_product='TD-0001',material_qty='1')
    if change=='legacy-fee':body.update(line_kind='PHI',line_price='1')
    r=client.post(f'/tho/phieu/{t.id}/hoan-thanh',data=body)
    assert r.status_code==409 and 'CHARGES_LOCKED' in r.text
    assert snapshot(db,t)==before


def test_receipt_failure_rolls_back_return_and_completion(db,returning,monkeypatch):
    from app import sales_receipts
    p,_,_,t,eq=returning;photo(db,p,t,'TRA_MAY','returner');before=snapshot(db,t)
    original=sales_receipts.report_for_ticket_in_tx
    def fail(*args,**kw):
        original(*args,**kw);raise BusinessError('TEST_RECEIPT_FAIL','Lỗi sau báo thu')
    monkeypatch.setattr(sales_receipts,'report_for_ticket_in_tx',fail)
    error('TEST_RECEIPT_FAIL',technician_work.complete,db,p['returner'],t.id,t.version,540000,'FULL','',[],return_machine=intake(eq))
    assert snapshot(db,t)==before


def test_reopen_reverses_all_early_docs_and_recompletion_issues_all_again(db,returning):
    p,a,b,t,eq=returning;photo(db,p,t,'TRA_MAY','returner')
    technician_work.complete(db,p['returner'],t.id,t.version,540000,'','',[],return_machine=intake(eq))
    old={x.stock_doc_id for x in t.lines if x.stock_doc_id}
    svc.transition(db,p['dispatcher'],t.id,'DANG_LAM',t.version,'Kiểm tra sửa lại')
    assert all(db.get(StockDoc,doc).status=='DA_HUY' for doc in old)
    assert t.priced_at is None and all(x.stock_doc_id is None for x in t.lines)
    assert balance(db,a.code)==4 and balance(db,b.code)==2
    technician_work.complete(db,p['returner'],t.id,t.version,540000,'','',[],fix='Đã sửa xong')
    assert t.status=='DA_DONG' and balance(db,a.code)==2 and balance(db,b.code)==1
    new={x.stock_doc_id for x in t.lines if x.stock_doc_id}
    assert len(new)==2 and not new.intersection(old) and service_charges.material_costs(db,[t.id])[t.id]==5000


def test_commission_belongs_to_repairer_not_returner(db,returning,configured_finance,monkeypatch):
    from datetime import datetime,UTC,date
    from app import commissions,sales_receipts
    from app.config import get_settings
    p,_,_,t,eq=returning;photo(db,p,t,'TRA_MAY','returner')
    technician_work.complete(db,p['returner'],t.id,t.version,540000,'FULL','',[],return_machine=intake(eq))
    row=db.scalars(select(SalesReceipt)).one();sales_receipts.confirm(db,p['finance'],row.id,row.version)
    monkeypatch.setenv('TD_COMMISSION_START','2026-01-01');get_settings.cache_clear()
    try:
        commissions.add_rule(db,p['director'],dict(target='THO',pct=10,fixed_amount=0,effective_from='2026-01-01',reason='MV1 thử hoa hồng'))
        month=NOW.date().replace(day=1)
        rows=[x for x in commissions.draft(db,month) if x['ticket_id']==t.id]
        assert len(rows)==1 and rows[0]['user_id']==p['other_tech'].id and rows[0]['amount']==50000
        assert not any(x['user_id']==p['returner'].id for x in rows)
    finally:get_settings.cache_clear()


def test_free_repair_and_return_still_uses_early_parts_without_receipt(db,brought):
    p,a,_,t,eq=brought;photo(db,p,t,'SAU')
    workshop.repair_done(db,p['technician'],t.id,t.version,'','FREE','Bảo hành',[],fix='Đã sửa xong')
    assert t.status=='TAM_DUNG' and t.receivable==0 and t.billing=='MIEN_PHI'
    doc=t.lines[0].stock_doc_id
    workshop.schedule_return(db,p['dispatcher'],t.id,t.version,NOW+timedelta(days=1),None,p['technician'].id)
    assert t.status=='DA_HEN' and custody.pending(db,eq.serial) is None
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    photo(db,p,t,'TRA_MAY')
    technician_work.complete(db,p['technician'],t.id,t.version,0,'','',[],return_machine=intake(eq))
    assert t.status=='DA_DONG' and t.receivable==0 and db.query(SalesReceipt).count()==0 and db.query(FundLedger).count()==0
    assert t.lines[0].stock_doc_id==doc and balance(db,a.code)==2 and service_charges.material_costs(db,[t.id])[t.id]==2000


def test_two_products_use_one_early_px(db,onsite):
    p,a,_,t,eq=onsite
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code=a.code,supplier_code='NCC-0001',doc_date=DAY,
        lines=[dict(product_code='TD-0002',qty=2,unit_cost=2000)]));stock_posting.post(db,p['warehouse'],doc.id,doc.version)
    photo(db,p,t)
    workshop.bring(db,p['technician'],t.id,t.version,intake(eq),[dict(product_code='TD-0001',qty=1),dict(product_code='TD-0002',qty=1)])
    ids={x.stock_doc_id for x in t.lines};assert len(ids)==1
    doc=db.get(StockDoc,ids.pop());assert len(doc.lines)==2 and doc.status=='DA_GHI'
    assert {x.product_code:x.unit_cost for x in doc.lines}=={'TD-0001':1000,'TD-0002':2000}
    assert service_charges.material_costs(db,[t.id])[t.id]==3000


def test_repair_done_error_after_px_rolls_back_price_repairer_and_stock(db,brought,monkeypatch):
    p,a,_,t,_=brought;photo(db,p,t,'SAU');before=snapshot(db,t);record=svc._record
    def fail(session,user,t,action,*args,**kw):
        record(session,user,t,action,*args,**kw)
        if action=='repair_done':raise BusinessError('TEST_REPAIR_FAIL','Lỗi sau chốt sửa xong')
    monkeypatch.setattr(svc,'_record',fail)
    error('TEST_REPAIR_FAIL',workshop.repair_done,db,p['technician'],t.id,t.version,540000,'FULL','',[dict(product_code='TD-0001',qty=1)],fix='Đã sửa xong')
    assert snapshot(db,t)==before and balance(db,a.code)==2


def test_schedule_failure_rolls_back_assignment_appointment_and_handover(db,repaired,make_user,monkeypatch):
    p,_,_,t,eq=repaired;c=make_user('mv1.schedule.c','technician');before=snapshot(db,t);original=custody.handover_in_tx
    def fail(*args,**kw):
        original(*args,**kw);raise BusinessError('TEST_SCHEDULE_FAIL','Lỗi sau tạo bàn giao')
    monkeypatch.setattr(custody,'handover_in_tx',fail)
    error('TEST_SCHEDULE_FAIL',workshop.schedule_return,db,p['dispatcher'],t.id,t.version,NOW+timedelta(days=1),None,c.id)
    assert snapshot(db,t)==before and custody.pending(db,eq.serial) is None


def test_ready_work_group_dashboard_schedule_form_and_permissions(client,db,repaired):
    from app import my_work
    p,_,_,t,_=repaired
    group=next(g for g in my_work.collect(db,p['dispatcher']) if g['key']=='service_return_ready')
    assert group['title']=='Máy sửa xong chờ trả' and group['count']==1
    csrf=service_login(client,p['dispatcher']);page=client.get(f'/dich-vu/{t.id}')
    assert page.status_code==200 and 'id="service-schedule-return"' in page.text
    assert t.number in client.get('/dieu-phoi?view=return_ready').text
    assert 'Máy sửa xong chờ trả' in client.get('/viec-cua-toi').text
    csrf=service_login(client,p['other_tech'])
    assert client.post(f'/dich-vu/{t.id}/hen-tra-may',data=dict(csrf=csrf,version=t.version,appointment_choice='GIO',appointment_at=(NOW+timedelta(days=1)).isoformat(),assignee_id=p['other_tech'].id)).status_code==403


def test_version_conflict_on_workshop_actions_leaves_state_unchanged(db,onsite):
    p,_,_,t,eq=onsite;photo(db,p,t);before=snapshot(db,t)
    error('VERSION_CONFLICT',workshop.bring,db,p['technician'],t.id,t.version-1,intake(eq),[])
    assert snapshot(db,t)==before
    body=intake(eq).model_copy(update={'version':eq.version+1})
    error('VERSION_CONFLICT',workshop.bring,db,p['technician'],t.id,t.version,body,[])
    assert snapshot(db,t)==before


def test_migration_and_pilot_classification_keep_legacy_rows(db,engine,people):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from scripts import clear_pilot_data
    p=people;t=ticket(db,p,'DANG_LAM');ident=t.id
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'));cfg.attributes['database_url']=engine.url.render_as_string(hide_password=False)
    before=t.receivable,t.total,t.status
    db.commit();command.downgrade(cfg,'0054')
    try:
        assert db.scalar(text("select count(*) from information_schema.columns where table_name='service_tickets' and column_name='repaired_by'"))==0
        db.commit();command.upgrade(cfg,'head');db.expire_all();db.refresh(t)
        assert t.repaired_by is None and (t.receivable,t.total,t.status)==before
        assert 'SUA_XONG_CHO_TRA' in db.scalar(text("select pg_get_constraintdef(oid) from pg_constraint where conname='ck_service_tickets_hold_reason'"))
        assert 'service_tickets' in clear_pilot_data.WIPE and 'users' in clear_pilot_data.KEEP
        assert clear_pilot_data.all_tables(db)==clear_pilot_data.KEEP|clear_pilot_data.WIPE
    finally:
        db.rollback();command.upgrade(cfg,'head')


def test_report_recognizes_completion_day_and_includes_all_early_costs(db,returning,monkeypatch):
    from datetime import datetime,UTC
    from app import purchase_reports
    p,_,_,t,eq=returning;old_day=t.priced_at.astimezone(svc.service_sla.TZ).date()
    finished=t.priced_at+timedelta(days=1)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return finished.astimezone(tz or UTC)
    monkeypatch.setattr(service_charges,'datetime',Clock)
    photo(db,p,t,'TRA_MAY','returner')
    technician_work.complete(db,p['returner'],t.id,t.version,540000,'','',[],return_machine=intake(eq))
    day=finished.astimezone(svc.service_sla.TZ).date()
    assert t.priced_at==finished
    previous=purchase_reports.gross_margin(db,p['finance'],old_day,old_day)
    assert not any(row['id']==t.id and row['kind']=='DV' for row in previous['documents'])
    current=purchase_reports.gross_margin(db,p['finance'],day,day)
    row=next(row for row in current['documents'] if row['id']==t.id and row['kind']=='DV')
    assert row['revenue']==500000 and row['cost']==5000 and row['profit']==495000


def test_early_cost_is_frozen_even_when_average_cost_changes(db,brought):
    p,a,_,t,_=brought;old=t.lines[0].stock_doc_id
    d=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code=a.code,supplier_code='NCC-0001',doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty=2,unit_cost=5000)]));stock_posting.post(db,p['warehouse'],d.id,d.version)
    photo(db,p,t,'SAU')
    workshop.repair_done(db,p['technician'],t.id,t.version,540000,'FULL','',[dict(product_code='TD-0001',qty=1)],fix='Đã sửa xong')
    assert db.get(StockDoc,old).lines[0].unit_cost==1000
    current={x.stock_doc_id for x in t.lines if x.stock_doc_id}-{old}
    assert len(current)==1 and db.get(StockDoc,current.pop()).lines[0].unit_cost==3000
    assert service_charges.material_costs(db,[t.id])[t.id]==5000


def test_return_proposal_does_not_request_installed_allocated_parts(db,onsite):
    from app import part_requests,technician_returns
    from app.schemas import PartRequestIn
    p,a,_,t,eq=onsite
    request=part_requests.create(db,p['technician'],t.id,PartRequestIn(version=t.version,product_code='TD-0001',qty=2,reason='Cần drum'))
    part_requests.supply(db,p['warehouse'],request.id,'K58',request.version)
    svc.transition(db,p['technician'],t.id,'DANG_LAM',t.version);photo(db,p,t)
    workshop.bring(db,p['technician'],t.id,t.version,intake(eq),[dict(product_code='TD-0001',qty=2)])
    assert balance(db,a.code)==4
    assert sum(row['qty'] for row in technician_returns.proposal(db,t)['rows'])==0

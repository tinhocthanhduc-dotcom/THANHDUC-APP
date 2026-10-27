"""XG1: dispatcher workshop quotes share the existing stock/custody/return transactions."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import service_workshop as svc,service_tickets,service_charges,equipment_custody as custody,attachments,my_work
from app.errors import BusinessError
from app.models import Equipment,EquipmentCustody,ServiceStep,ServiceTicket,StockDoc,StockLedger,FundLedger,SalesReceipt,AuditLog
from app.schemas import CustodyHandover,ServiceCharges
from test_my_work import people,purchase_people,NOW
from test_technician_stock import held,transfer,balance
from test_mv1_workshop import onsite,photo,intake,snapshot
from test_web import service_login


def error(code,fn,*a,**kw):
    with pytest.raises(BusinessError) as e:fn(*a,**kw)
    assert e.value.code==code
    return e.value


@pytest.fixture
def holding(db,onsite):
    p,a,b,t,eq=onsite
    photo(db,p,t)
    svc.bring(db,p['technician'],t.id,t.version,intake(eq),[])
    return p,a,b,t,eq


@pytest.fixture
def workshop(db,holding):
    p,a,b,t,eq=holding
    svc.hand_to_workshop(db,p['technician'],t.id,t.version,CustodyHandover(version=eq.version,location='KHO_KY_THUAT'))
    hand=custody.pending(db,eq.serial)
    custody.confirm(db,p['warehouse'],hand.id,hand.version)
    return p,a,b,t,eq


def quote(db,p,t):
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[
        dict(kind='PHI',description='Công sửa xưởng',qty=1,unit_price=100000,vat_rate=8),
        dict(kind='VAT_TU',description='Drum',product_code='TD-0001',warehouse_code='K58',qty=1,unit_price=50000,vat_rate=8)]))


def approve(db,p,t):svc.quote_approve(db,p['dispatcher'],t.id,t.version,service_tickets._now().date()+timedelta(days=1),'Khách đồng ý')


def repaired(db,p,t,**kw):
    args=dict(repaired_by=p['other_tech'].id,mode='FULL',reason='',fault='Hỏng drum',fix='Thay drum',warranty_months=3,warranty_scope='')
    args.update(kw)
    return svc.workshop_repaired(db,p['dispatcher'],t.id,t.version,**args)


def test_handover_hides_technician_job_and_warehouse_can_confirm(client,db,holding):
    p,a,b,t,eq=holding
    svc.hand_to_workshop(db,p['technician'],t.id,t.version,CustodyHandover(version=eq.version,location='KHO_KY_THUAT'))
    assert t.status=='TAM_DUNG' and t.hold_reason=='CHO_SUA_XUONG' and t.assignee_id==p['technician'].id
    row=custody.pending(db,eq.serial);assert row.status=='CHO_NHAN' and row.location=='KHO_KY_THUAT'
    csrf=service_login(client,p['technician'])
    assert t.number not in client.get('/tho').text
    assert all(x.id!=t.id for x in service_tickets.search(db,p['technician']))
    custody.confirm(db,p['warehouse'],row.id,row.version)
    assert eq.custody_location=='KHO_KY_THUAT'
    assert db.query(StockDoc).filter_by(kind='PX').count()==0


@pytest.mark.parametrize('case,code',[('partner','PARTNER_REQUIRED'),('other','FORBIDDEN'),('status','BAD_STATUS'),('holder','MACHINE_NOT_HELD')])
def test_handover_failures_roll_back(db,holding,case,code):
    p,a,b,t,eq=holding
    who=p['other_tech'] if case=='other' else p['technician']
    if case=='status':t.status='DA_HEN';db.commit()
    if case=='holder':eq.custody_holder_id=p['other_tech'].id;db.commit()
    before=snapshot(db,t)
    error(code,svc.hand_to_workshop,db,who,t.id,t.version,CustodyHandover(version=eq.version,location='DOI_TAC' if case=='partner' else 'KHO_KY_THUAT'))
    assert snapshot(db,t)==before


def test_quote_editable_until_completed_approval_resets_only_on_change(db,workshop):
    p,a,b,t,eq=workshop
    quote(db,p,t)
    assert db.query(StockDoc).filter_by(kind='PX').count()==0
    error('QUOTE_NOT_APPROVED',repaired,db,p,t)
    approve(db,p,t)
    actor=t.quote_approved_by;stamp=t.quote_approved_at
    assert actor==p['dispatcher'].id and stamp and t.promised_return_on
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[service_charges.line_input(x) for x in t.lines]))
    assert t.quote_approved_at==stamp
    entries=[service_charges.line_input(x) for x in t.lines];entries[0]['unit_price']+=1
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=entries))
    assert t.quote_approved_at is t.quote_approved_by is None
    error('QUOTE_NOT_APPROVED',repaired,db,p,t)
    assert db.query(StockDoc).filter_by(kind='PX').count()==0


@pytest.mark.parametrize('case,code',[('empty','EMPTY_CHARGES'),('missing','BAD_APPOINTMENT'),('past','BAD_APPOINTMENT'),('role','FORBIDDEN')])
def test_approval_validation_is_atomic(db,workshop,case,code):
    p,a,b,t,eq=workshop
    if case!='empty':quote(db,p,t)
    before=snapshot(db,t)
    error(code,svc.quote_approve,db,p['technician'] if case=='role' else p['dispatcher'],t.id,t.version,
        None if case=='missing' else service_tickets._now().date()+timedelta(days=-1 if case=='past' else 1),'Gọi khách')
    assert snapshot(db,t)==before


@pytest.mark.parametrize('case,code',[('approval','QUOTE_NOT_APPROVED'),('photo','PHOTO_REQUIRED'),('repairer','REPAIRER_REQUIRED'),('months','WARRANTY_REQUIRED'),('five','WARRANTY_REQUIRED'),('fix','FIX_REQUIRED')])
def test_repaired_validation_rolls_back_state_and_px(db,workshop,case,code):
    p,a,b,t,eq=workshop;quote(db,p,t)
    if case!='approval':approve(db,p,t)
    if case!='photo':photo(db,p,t,'SAU',who='dispatcher')
    kw={'repaired_by':None} if case=='repairer' else {'warranty_months':None} if case=='months' else {'warranty_months':5} if case=='five' else {'fix':''} if case=='fix' else {}
    before=snapshot(db,t)
    error(code,repaired,db,p,t,**kw)
    assert snapshot(db,t)==before and db.query(StockDoc).filter_by(kind='PX').count()==0


def test_repaired_posts_company_stock_and_locks_price(db,workshop):
    p,a,b,t,eq=workshop;quote(db,p,t);approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher')
    before=balance(db,'K58');repaired(db,p,t)
    assert t.status=='TAM_DUNG' and t.hold_reason=='SUA_XONG_CHO_TRA' and t.priced_at and t.repaired_by==p['other_tech'].id
    assert t.warranty_months==3 and t.warranty_scope=='Bảo hành hạng mục và linh kiện đã sửa chữa'
    line=next(x for x in t.lines if x.kind=='VAT_TU');doc=db.get(StockDoc,line.stock_doc_id)
    assert doc.kind=='PX' and doc.warehouse_code=='K58' and doc.lines[0].qty==1 and doc.lines[0].unit_cost==1000
    assert balance(db,'K58')==before-1 and service_charges.return_locked(t)
    error('CHARGES_LOCKED',quote,db,p,t)


@pytest.mark.parametrize('mode,fee,receivable',[('FULL',100000,108000),('FREE',0,0)])
def test_decline_removes_unissued_materials_and_never_issues_px(db,workshop,mode,fee,receivable):
    p,a,b,t,eq=workshop;quote(db,p,t)
    if mode=='FREE':
        before=snapshot(db,t);error('REASON_REQUIRED',svc.quote_decline,db,p['dispatcher'],t.id,t.version,mode,fee,'');assert snapshot(db,t)==before
    svc.quote_decline(db,p['dispatcher'],t.id,t.version,mode,fee,'Khách không sửa')
    assert t.declined_at and t.repaired_by is None and t.receivable==receivable and t.priced_at
    assert t.status=='TAM_DUNG' and t.hold_reason=='SUA_XONG_CHO_TRA' and service_charges.return_locked(t)
    assert not any(x.kind=='VAT_TU' for x in t.lines) and db.query(StockDoc).filter_by(kind='PX').count()==0
    assert t.lines[0].description=='Phí kiểm tra' if mode=='FULL' else not t.lines


def test_dispatch_cannot_resume_or_change_workshop_hold_reason(db,workshop):
    p,a,b,t,eq=workshop;before=snapshot(db,t)
    for target,kw in [('DANG_LAM',{}),('TAM_DUNG',dict(hold_reason='KHAC'))]:
        error('BAD_TRANSITION',service_tickets.transition,db,p['dispatcher'],t.id,target,t.version,'Đổi bước',**kw)
        assert snapshot(db,t)==before


def test_dispatch_workshop_forms_primary_sequence_and_handover_ui(client,db,holding):
    p,a,b,t,eq=holding
    csrf=service_login(client,p['technician'])
    page=client.get(f'/tho/phieu/{t.id}')
    assert page.status_code==200 and 'Bàn giao máy về xưởng' in page.text and 'id="workshop-handover"' in page.text
    assert 'data-tech-completion' not in page.text and 'Sửa xong, chờ trả máy' not in page.text
    r=client.post(f'/tho/phieu/{t.id}/ban-giao-xuong',data=dict(csrf=csrf,version=t.version,machine_version=eq.version,location='KHO_KY_THUAT'))
    assert r.status_code==200,r.text
    db.refresh(t)
    csrf=service_login(client,p['dispatcher'])
    page=client.get(f'/dich-vu/{t.id}')
    assert page.status_code==200 and 'id="workshop-approve"' in page.text and 'Khách không sửa' in page.text
    assert 'id="service-DANG_LAM"' not in page.text
    quote(db,p,t)
    r=client.post(f'/dich-vu/{t.id}/xuong/dong-y',data=dict(csrf=csrf,version=t.version,promised_return_on=(service_tickets._now().date()+timedelta(days=1)).isoformat(),note='Gọi khách'))
    assert r.status_code==200,r.text
    db.refresh(t)
    assert t.quote_approved_at and 'id="workshop-repaired"' in r.text and 'Sửa xong' in r.text
    # Phương án A (TUẤN 27/10): kết quả 2 nút + lỗi · việc đã làm gập, không còn ô gõ cách xử lý.
    assert 'data-report-fold' in r.text and 'name="repair_result"' in r.text and 'name="fix"' not in r.text
    hand=custody.pending(db,eq.serial);custody.confirm(db,p['warehouse'],hand.id,hand.version)
    photo(db,p,t,'SAU',who='dispatcher')
    db.refresh(t)
    r=client.post(f'/dich-vu/{t.id}/xuong/sua-xong',data=dict(csrf=csrf,version=t.version,repaired_by=p['technician'].id,repair_result='XONG',work=['Thay drum','Vệ sinh máy'],fix_extra='Thay bánh xe',fault=t.fault_found or '',warranty_months='3',mode='FULL'))
    db.refresh(t)
    assert r.status_code==200 and 'Đã lưu' in r.text
    assert t.fix_done=='Đã sửa xong: Thay drum, Vệ sinh máy, Thay bánh xe'


@pytest.mark.parametrize('late', ['promised','partner','waiting','before'])
def test_queue_and_r2_share_overdue_rule(client,db,workshop,monkeypatch,late):
    from datetime import datetime,UTC
    p,a,b,t,eq=workshop
    stamp=db.scalar(select(ServiceStep.at).where(ServiceStep.ticket_id==t.id,ServiceStep.action=='workshop_handover'))
    now=stamp+timedelta(days=3 if late=='waiting' else 1)
    monkeypatch.setattr(my_work,'_now',lambda:now)
    from app.web import dispatch
    monkeypatch.setattr(dispatch,'_now',lambda:now)
    if late=='promised':t.promised_return_on=now.date()-timedelta(days=1)
    if late=='partner':
        custody.handover(db,p['dispatcher'],t.id,CustodyHandover(version=eq.version,location='DOI_TAC',partner_name='Sửa máy đối tác',expected_back=now.date()-timedelta(days=1)))
        row=custody.pending(db,eq.serial);custody.confirm(db,p['dispatcher'],row.id,row.version)
    db.commit()
    found=my_work.filtered(db,p['dispatcher'],'service_workshop')
    assert found['count']==1 and found['rows'][0]['overdue']==(late!='before')
    assert found['rows'][0]['workshop_days']==(3 if late=='waiting' else 1)
    csrf=service_login(client,p['dispatcher'])
    page=client.get('/viec-cua-toi?nhom=service_workshop')
    assert page.status_code==200 and 'Máy chờ sửa ở xưởng' in page.text and eq.serial in page.text
    page=client.get('/dieu-phoi/bao-cao')
    assert page.status_code==200,page.text
    assert 'Máy ở xưởng quá hẹn · '+str(0 if late=='before' else 1) in page.text


def start_return(db,p,t,eq):
    svc.schedule_return(db,p['dispatcher'],t.id,t.version,service_tickets._now()+timedelta(days=1),None,p['technician'].id,'Giao trả máy')
    hand=custody.pending(db,eq.serial)
    if hand:custody.confirm(db,p['technician'],hand.id,hand.version)
    service_tickets.transition(db,p['technician'],t.id,'DANG_LAM',t.version)


@pytest.mark.parametrize('decline', [False,True])
def test_end_to_end_repair_or_decline_with_locked_fee_and_commission(db,workshop,configured_finance,monkeypatch,decline):
    from app import technician_work,sales_receipts,commissions
    from app.config import get_settings
    p,a,b,t,eq=workshop;quote(db,p,t)
    if decline:svc.quote_decline(db,p['dispatcher'],t.id,t.version,'FULL',100000,'Không sửa tiếp')
    else:approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher');repaired(db,p,t)
    expected=t.receivable
    start_return(db,p,t,eq)
    photo(db,p,t,'TRA_MAY');photo(db,p,t,'BIEN_BAN')
    technician_work.complete(db,p['technician'],t.id,t.version,expected,'FULL','',[],return_machine=intake(eq))
    assert t.status=='DA_DONG' and t.receivable==expected and eq.custody_location=='TAI_KHACH'
    receipt=db.scalar(select(SalesReceipt).where(SalesReceipt.ticket_id==t.id))
    assert receipt.amount==expected
    sales_receipts.confirm(db,p['finance'],receipt.id,receipt.version)
    monkeypatch.setenv('TD_COMMISSION_START','2026-01-01');get_settings.cache_clear()
    try:
        commissions.add_rule(db,p['director'],dict(target='THO',pct=10,fixed_amount=5000,effective_from='2026-01-01',reason='XG1'))
        # Actual completion steps use DB time, not the historical appointment fixture clock.
        step=db.scalar(select(ServiceStep).where(ServiceStep.ticket_id==t.id,ServiceStep.to_status=='HOAN_THANH').order_by(ServiceStep.id.desc()))
        month=step.at.astimezone(service_tickets.service_sla.TZ).date().replace(day=1)
        rows=[x for x in commissions.draft(db,month) if x['ticket_id']==t.id]
        if decline:assert rows==[] and t.repaired_by is None
        else:assert len(rows)==1 and rows[0]['amount']>0 and rows[0]['user_id']==p['other_tech'].id
    finally:get_settings.cache_clear()


def test_free_decline_completes_without_after_photo_with_one_return_photo(db,workshop):
    # TUẤN 24/10: trả máy chỉ cần 1 ảnh (ảnh trả máy chụp kèm biên bản đã ký), không bắt thêm ảnh biên bản riêng.
    from app import technician_work
    p,a,b,t,eq=workshop
    svc.quote_decline(db,p['dispatcher'],t.id,t.version,'FREE',0,'Khách không sửa')
    start_return(db,p,t,eq)
    before=snapshot(db,t)
    error('PHOTO_REQUIRED',technician_work.complete,db,p['technician'],t.id,t.version,0,'','',[],return_machine=intake(eq))
    assert snapshot(db,t)==before
    photo(db,p,t,'TRA_MAY')
    technician_work.complete(db,p['technician'],t.id,t.version,0,'','',[],return_machine=intake(eq))
    assert t.status=='DA_DONG' and t.receivable==0 and t.warranty_months==0
    assert db.query(SalesReceipt).count()==0 and db.query(StockDoc).filter_by(kind='PX').count()==0
    assert not attachments.has_visible(db,'service_ticket',t.id,'SAU')


def test_free_warranty_repair_can_skip_customer_approval(db,workshop):
    p,a,b,t,eq=workshop;t.kind='BAO_HANH';t.warranty_basis='MAY_CON_HAN';t.billing='MIEN_PHI';t.free_reason='Máy còn bảo hành';db.commit()
    photo(db,p,t,'SAU',who='dispatcher')
    repaired(db,p,t,mode='FREE',reason='Máy còn bảo hành')
    assert t.hold_reason=='SUA_XONG_CHO_TRA' and t.receivable==0 and t.quote_approved_at is None


def test_machine_at_partner_must_return_before_repaired(db,holding):
    p,a,b,t,eq=holding
    svc.hand_to_workshop(db,p['technician'],t.id,t.version,CustodyHandover(version=eq.version,location='DOI_TAC',partner_name='Đối tác A'))
    row=custody.pending(db,eq.serial);custody.confirm(db,p['dispatcher'],row.id,row.version)
    quote(db,p,t);approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher');before=snapshot(db,t)
    error('MACHINE_AT_PARTNER',repaired,db,p,t)
    assert snapshot(db,t)==before
    custody.handover(db,p['dispatcher'],t.id,CustodyHandover(version=eq.version,location='KHO_KY_THUAT'))
    row=custody.pending(db,eq.serial);custody.confirm(db,p['warehouse'],row.id,row.version)
    repaired(db,p,t)
    assert t.priced_at and eq.custody_location=='KHO_KY_THUAT'


@pytest.mark.parametrize('months,day,basis',[(3,'2026-04-30','PHIEU_TRUOC'),(3,'2026-05-01','HET_HAN'),(0,'2026-01-31','HET_HAN'),(None,'2026-04-30','PHIEU_TRUOC'),(None,'2026-05-02','HET_HAN')])
def test_ticket_warranty_calendar_months_and_legacy_days(client,db,people,monkeypatch,months,day,basis):
    from datetime import datetime,date
    from app import service_warranty
    from test_service_warranty import previous,warranty
    p=people;parent=previous(db,p,days=(NOW.date()-date(2026,1,31)).days,warranty_months=months,warranty_scope='Drum và cụm sấy')
    monkeypatch.setattr(service_tickets,'_now',lambda:datetime.combine(date.fromisoformat(day),__import__('datetime').time(10),service_tickets.service_sla.TZ))
    child=warranty(db,p,parent)
    assert child.warranty_basis==basis and (child.billing=='MIEN_PHI')==(basis=='PHIEU_TRUOC')
    service_login(client,p['dispatcher'])
    page=client.get(f'/dich-vu/{parent.id}/bien-ban')
    assert page.status_code==200,page.text
    assert ('Không bảo hành' if months==0 else 'Bảo hành: '+('3 tháng, đến 30/04/2026' if months==3 else '90 ngày, đến 01/05/2026')) in page.text


def test_cancel_workshop_after_customer_machine_return_keeps_no_px(db,workshop):
    p,a,b,t,eq=workshop
    custody.handover(db,p['dispatcher'],t.id,CustodyHandover(version=eq.version,location='THO_GIU',holder_id=p['technician'].id))
    row=custody.pending(db,eq.serial);custody.confirm(db,p['technician'],row.id,row.version)
    photo(db,p,t,'TRA_MAY');custody.return_customer(db,p['technician'],t.id,intake(eq))
    service_tickets.transition(db,p['dispatcher'],t.id,'DA_HUY',t.version,'Khách hủy đơn')
    assert t.status=='DA_HUY' and db.query(StockDoc).filter_by(kind='PX').count()==0


@pytest.mark.parametrize('op',['approve','decline','repair'])
@pytest.mark.parametrize('role',['technician','warehouse'])
def test_dispatch_operations_forbid_other_roles_and_rollback(db,workshop,op,role):
    p,a,b,t,eq=workshop;quote(db,p,t);approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher');before=snapshot(db,t)
    if op=='approve':fn,args=svc.quote_approve,(db,p[role],t.id,t.version,service_tickets._now().date()+timedelta(days=1),'Khách đồng ý')
    elif op=='decline':fn,args=svc.quote_decline,(db,p[role],t.id,t.version,'FULL',100000,'Không sửa')
    else:fn,args=svc.workshop_repaired,(db,p[role],t.id,t.version,p['technician'].id,'FULL','','Hỏng','Thay drum',3,'')
    assert error('FORBIDDEN',fn,*args).status==403 and snapshot(db,t)==before


def test_quote_approve_api_returns_new_fields(client,db,workshop):
    from conftest import PASSWORD
    p,a,b,t,eq=workshop;quote(db,p,t)
    r=client.post('/auth/login',data=dict(username=p['dispatcher'].username,password=PASSWORD))
    h=dict(Authorization='Bearer '+r.json()['access_token'])
    day=(service_tickets._now().date()+timedelta(days=1)).isoformat()
    r=client.post(f'/service-tickets/{t.id}/quote-approve',headers=h,json=dict(version=t.version,promised_return_on=day,note='Khách duyệt'))
    assert r.status_code==200,r.text
    assert r.json()['quote_approved_by']==p['dispatcher'].id and r.json()['promised_return_on']==day


def test_stock_failure_rolls_back_workshop_completion(db,workshop):
    p,a,b,t,eq=workshop;quote(db,p,t)
    entries=[service_charges.line_input(x) for x in t.lines]
    next(x for x in entries if x['kind']=='VAT_TU')['qty']=10
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=entries))
    approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher');before=snapshot(db,t);qty=balance(db,'K58')
    error('NEGATIVE_STOCK',repaired,db,p,t)
    assert snapshot(db,t)==before and balance(db,'K58')==qty


def test_reopening_declined_dossier_clears_old_refusal_and_warranty(db,workshop):
    from app import technician_work
    p,a,b,t,eq=workshop
    svc.quote_decline(db,p['dispatcher'],t.id,t.version,'FREE',0,'Không sửa')
    start_return(db,p,t,eq);photo(db,p,t,'TRA_MAY');photo(db,p,t,'BIEN_BAN')
    technician_work.complete(db,p['technician'],t.id,t.version,0,'','',[],return_machine=intake(eq))
    service_tickets.transition(db,p['dispatcher'],t.id,'DANG_LAM',t.version,'Khách đồng ý sửa lại')
    assert t.status=='DANG_LAM' and t.declined_at is None and t.warranty_months is None and t.priced_at is None


def test_reassignment_cannot_skip_workshop_quote_stage(db,workshop):
    p,a,b,t,eq=workshop;before=snapshot(db,t)
    error('BAD_STATUS',service_tickets.assign,db,p['dispatcher'],t.id,p['other_tech'].id,t.version,'Đổi người xử lý')
    assert snapshot(db,t)==before


def test_return_appointment_and_handover_are_one_technician_screen(client,db,workshop):
    # TUẤN 24/10: Đã hẹn trả + Trả máy gộp một màn, ẩn Loại dịch vụ, chỉ 1 ảnh trả máy; vuốt Hoàn thành ghi luôn đã đến khách.
    p,a,b,t,eq=workshop;quote(db,p,t);approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher');repaired(db,p,t)
    svc.schedule_return(db,p['dispatcher'],t.id,t.version,service_tickets._now()+timedelta(days=1),None,p['technician'].id,'Giao trả máy')
    hand=custody.pending(db,eq.serial);custody.confirm(db,p['technician'],hand.id,hand.version)
    db.expire_all();db.refresh(t);eq=db.get(Equipment,t.serial)
    assert t.status=='DA_HEN'
    csrf=service_login(client,p['technician'])
    page=client.get(f'/tho/phieu/{t.id}').text
    assert 'Trả máy đã sửa xong' in page and 'Vuốt để Hoàn thành' in page and 'form="service-complete"' in page
    assert 'Loại dịch vụ' not in page and 'name="kind"' not in page and 'data-photo-tag="BIEN_BAN"' not in page
    assert 'id="service-DANG_LAM"' not in page
    form=dict(csrf=csrf,version=t.version,customer_amount=str(t.receivable),collection_mode='FULL',method='TIEN_MAT',
        return_machine='1',machine_version=eq.version,accessories='KHONG_CO')
    version=t.version
    r=client.post(f'/tho/phieu/{t.id}/hoan-thanh',data=form)
    assert r.status_code==400 and 'data-error-code="PHOTO_REQUIRED"' in r.text
    db.expire_all();db.refresh(t)
    # Báo cáo chạy thử 27/10: lỗi khi trả máy rollback cả bước Đã đến khách (trước đây phiếu đã sang DANG_LAM).
    assert t.status=='DA_HEN' and t.version==version and db.query(SalesReceipt).count()==0 and eq.custody_location=='THO_GIU'
    photo(db,p,t,'TRA_MAY')
    form.update(csrf=service_login(client,p['technician']),version=t.version)
    r=client.post(f'/tho/phieu/{t.id}/hoan-thanh',data=form,follow_redirects=False)
    assert r.status_code==303,r.text
    db.expire_all();db.refresh(t);eq=db.get(Equipment,t.serial)
    assert t.status=='DA_DONG' and eq.custody_location=='TAI_KHACH'
    assert db.scalar(select(SalesReceipt).where(SalesReceipt.ticket_id==t.id)).amount==t.receivable

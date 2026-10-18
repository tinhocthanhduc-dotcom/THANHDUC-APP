from concurrent.futures import ThreadPoolExecutor
from datetime import UTC,date,datetime
from threading import Barrier

import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import cash_docs,cash_posting,finance_mappings,sales_orders,sales_receipts as svc,service_charges,service_tickets
from app.errors import BusinessError
from app.models import AuditLog,CashDoc,CashCategory,Customer,FundLedger,SalesReceipt,ServiceTicket,User
from app.schemas import CashDocIn,SalesOrderIn,SalesReceiptReport,ServiceTicketIn


@pytest.fixture
def t3_people(db,make_user):
    db.add(Customer(code='KTD-000001',name='Minh Phát',search_text='minh phat'));db.commit()
    return {r:make_user('t3.'+r,r) for r in ('sales','finance','director','dispatcher','technician')}


def order(db,p):
    o=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='KHAC',description='Đơn bán',qty=1,unit_price=1000000,vat_rate=0)]))
    return sales_orders.confirm(db,p['sales'],o.id,o.version)


def ticket(db,p,**changes):
    t=service_tickets.create(db,p['dispatcher'],ServiceTicketIn(customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Kẹt giấy'))
    for k,v in dict({'status':'HOAN_THANH','assignee_id':p['technician'].id,'free_reason':'Bảo hành','billing':'THU_TIEN','receivable':500000,'total':500000,'subtotal':500000,'vat_total':0,
                    'priced_at':datetime(2026,10,18,10,tzinfo=UTC)},**changes).items():setattr(t,k,v)
    db.commit();return t


def report(db,p,parent,amount=600000,method='TIEN_MAT'):
    body=SalesReceiptReport(version=parent.version,amount=amount,method=method,received_at=date(2026,10,18))
    return svc.report_for_ticket(db,p['dispatcher'],parent.id,body) if isinstance(parent,ServiceTicket) else svc.report(db,p['sales'],parent.id,body)


def fails(code,fn,*args,**kwargs):
    with pytest.raises(BusinessError) as exc:fn(*args,**kwargs)
    assert exc.value.code==code
    return exc.value


def test_missing_mapping_no_partial_receipt_cash_or_audit(db,t3_people):
    p=t3_people;o=order(db,p);r=report(db,p,o)
    count=db.query(AuditLog).count()
    fails('MAPPING_REQUIRED',svc.confirm,db,p['finance'],r.id,r.version)
    db.expire_all()
    assert r.status=='CHO_XAC_NHAN' and r.cash_doc_id is None and r.version==1
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==0
    assert db.query(AuditLog).count()==count


def test_confirm_methods_mapping_and_source_once(db,t3_people,configured_finance):
    p=t3_people;o=order(db,p)
    for amount,method,fund in [(600000,'TIEN_MAT','QUY-TM'),(400000,'CHUYEN_KHOAN','TK-ACB')]:
        r=report(db,p,o,amount,method);svc.confirm(db,p['finance'],r.id,r.version)
        pt=db.get(CashDoc,r.cash_doc_id)
        assert pt.status=='DA_GHI' and pt.kind=='PT' and pt.category_code=='THU-BH' and pt.fund_code==fund
        assert pt.customer_code==o.customer_code and pt.doc_date==r.received_at and pt.amount==amount
        assert pt.description==f'Thu {r.number} cho {o.number}'
        assert pt.source_type=='SALES_RECEIPT' and pt.source_id==str(r.id)
        assert cash_posting.balance(db,p['finance'],fund)==amount
        fails('VERSION_CONFLICT',svc.confirm,db,p['finance'],r.id,1)
    assert svc.balance(o)['remaining']==0 and db.query(CashDoc).count()==2


def test_mapping_missing_category_and_post_failure_rollback_all(db,t3_people,configured_finance,monkeypatch):
    p=t3_people;o=order(db,p);r=report(db,p,o)
    finance_mappings.set_mapping(db,p['director'],'DANH_MUC_BAN_HANG',version=2)
    fails('MAPPING_REQUIRED',svc.confirm,db,p['finance'],r.id,r.version)
    assert db.query(CashDoc).count()==0
    finance_mappings.set_mapping(db,p['director'],'DANH_MUC_BAN_HANG',category_code='THU-BH',version=3)
    actual=cash_posting.post_in_tx
    def fail_after_post(*args):
        actual(*args)
        raise BusinessError('TEST_FAILURE','Lỗi thử nghiệm sau ghi sổ')
    monkeypatch.setattr(cash_posting,'post_in_tx',fail_after_post)
    fails('TEST_FAILURE',svc.confirm,db,p['finance'],r.id,r.version)
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==0
    assert cash_posting.balance(db,p['finance'],'QUY-TM')==0
    db.expire_all();assert r.cash_doc_id is None and r.status=='CHO_XAC_NHAN'
    monkeypatch.setattr(cash_posting,'post_in_tx',actual)
    svc.confirm(db,p['finance'],r.id,r.version)
    assert db.get(CashDoc,r.cash_doc_id).number=='PT-26-00001'


def test_void_negative_preserves_bt_then_reverse_linked_pt(db,t3_people,configured_finance):
    p=t3_people;o=order(db,p);r=report(db,p,o);svc.confirm(db,p['finance'],r.id,r.version)
    db.add(CashCategory(code='CHI',name='Chi',direction='CHI'));db.commit()
    pc=cash_docs.create(db,p['finance'],CashDocIn(kind='PC',doc_date=date(2026,10,18),fund_code='QUY-TM',category_code='CHI',amount=600000))
    cash_posting.post(db,p['finance'],pc.id,pc.version)
    count=db.query(FundLedger).count();audits=db.query(AuditLog).count()
    fails('NEGATIVE_CASH',svc.void,db,p['director'],r.id,'Hoàn tiền',r.version)
    db.expire_all()
    assert r.status=='DA_XAC_NHAN' and db.get(CashDoc,r.cash_doc_id).status=='DA_GHI'
    assert db.query(FundLedger).count()==count and db.query(AuditLog).count()==audits
    cash_posting.void(db,p['finance'],pc.id,'Chi nhầm',pc.version)
    svc.void(db,p['director'],r.id,'Hoàn tiền',r.version)
    assert r.status=='DA_HUY' and db.get(CashDoc,r.cash_doc_id).status=='DA_HUY'
    assert cash_posting.balance(db,p['finance'],'QUY-TM')==0
    assert db.query(FundLedger).filter_by(doc_id=r.cash_doc_id,is_reversal=True).count()==1


def test_service_report_overpay_confirm_receivables_and_reopen_guard(db,t3_people,configured_finance):
    p=t3_people;t=ticket(db,p);r=report(db,p,t,500000)
    fails('OVER_PAYMENT',report,db,p,t,1)
    assert svc.balance(t)==dict(receivable=500000,paid=0,pending=500000,remaining=500000)
    for target in ('DANG_LAM','DA_HUY'):
        fails('HAS_RECEIPTS',service_tickets.transition,db,p['dispatcher'],t.id,target,t.version,'Khách đổi ý')
    rows=service_charges.receivables(db,p['finance'])
    assert rows[0].id==t.id and rows[0].remaining==500000 and rows[0].pending==500000
    svc.confirm(db,p['finance'],r.id,r.version)
    assert db.get(CashDoc,r.cash_doc_id).category_code=='THU-DV'
    assert db.get(CashDoc,r.cash_doc_id).description==f'Thu {r.number} cho {t.number}'
    assert service_charges.receivables(db,p['finance'])==[]
    fails('HAS_RECEIPTS',service_tickets.transition,db,p['dispatcher'],t.id,'DANG_LAM',t.version,'Mở lại')
    svc.void(db,p['director'],r.id,'Hoàn lại',r.version)
    assert service_charges.receivables(db,p['finance'])[0].remaining==500000
    service_tickets.transition(db,p['dispatcher'],t.id,'DANG_LAM',t.version,'Mở lại')
    assert t.status=='DANG_LAM'


def test_service_invalid_status_roles_reject_and_xor_db(db,t3_people):
    p=t3_people;free=ticket(db,p,billing='MIEN_PHI',receivable=0);working=ticket(db,p,status='DANG_LAM')
    for t in (free,working):fails('BAD_STATUS',report,db,p,t,500000)
    t=ticket(db,p,status='DA_DONG')
    body=SalesReceiptReport(version=t.version,method='TIEN_MAT',amount=500000)
    for role in ('finance','technician'):
        fails('FORBIDDEN',svc.report_for_ticket,db,p[role],t.id,body)
    r=svc.report_for_ticket(db,p['sales'],t.id,body)
    svc.reject(db,p['finance'],r.id,'Chưa nhận tiền',r.version)
    assert svc.balance(t)['pending']==0
    assert svc.report_for_ticket(db,p['director'],t.id,body).status=='CHO_XAC_NHAN'
    o=order(db,p)
    for order_id,ticket_id in [(None,None),(o.id,t.id)]:
        with pytest.raises(IntegrityError) as exc:
            with db.begin_nested():
                db.execute(text('update sales_receipts set order_id=:oid,ticket_id=:tid where id=:id'),dict(oid=order_id,tid=ticket_id,id=r.id))
        assert 'ck_sales_receipts_source' in str(exc.value)


def test_concurrent_ticket_reports_and_reopen_are_serialized(db,engine,t3_people):
    p=t3_people;t=ticket(db,p);tid,uid,version=t.id,p['dispatcher'].id,t.version
    barrier=Barrier(2)
    def attempt(action):
        with Session(engine,expire_on_commit=False) as session:
            user=session.get(User,uid);session.get(ServiceTicket,tid)
            barrier.wait(timeout=10)
            try:
                if action=='report':svc.report_for_ticket(session,user,tid,SalesReceiptReport(version=version,method='TIEN_MAT',amount=500000))
                else:service_tickets.transition(session,user,tid,'DANG_LAM',version,'Mở lại')
                return action
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,['report','reopen']))
    assert sorted(results) in [sorted(['report','HAS_RECEIPTS']),sorted(['VERSION_CONFLICT','reopen'])]
    db.expire_all()
    assert not (t.status=='DANG_LAM' and db.query(SalesReceipt).count())


def test_concurrent_confirmation_creates_only_one_pt(db,engine,t3_people,configured_finance):
    p=t3_people;r=report(db,p,order(db,p));rid,uid=r.id,p['finance'].id;barrier=Barrier(2)
    def confirm(_):
        with Session(engine) as s:
            user=s.get(User,uid);s.get(SalesReceipt,rid);barrier.wait(timeout=10)
            try:svc.confirm(s,user,rid,1);return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(confirm,[1,2]))==['OK','VERSION_CONFLICT']
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==1
    assert cash_posting.balance(db,p['finance'],'QUY-TM')==600000


def test_concurrent_ticket_reports_cannot_overpay(db,engine,t3_people):
    p=t3_people;t=ticket(db,p);tid,uid,version=t.id,p['sales'].id,t.version;barrier=Barrier(2)
    def report_ticket(_):
        with Session(engine) as s:
            user=s.get(User,uid);barrier.wait(timeout=10)
            try:svc.report_for_ticket(s,user,tid,SalesReceiptReport(version=version,amount=300000,method='TIEN_MAT'));return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(report_ticket,[1,2]))==['OK','OVER_PAYMENT']
    assert svc.balance(t)['pending']==300000 and db.query(SalesReceipt).count()==1


def test_void_failure_after_reversal_rolls_back_everything(db,t3_people,configured_finance,monkeypatch):
    p=t3_people;r=report(db,p,order(db,p));svc.confirm(db,p['finance'],r.id,r.version)
    actual=cash_posting.void_in_tx;count=db.query(AuditLog).count()
    def fail_after_void(*args):
        actual(*args)
        raise BusinessError('TEST_VOID','Lỗi sau ghi đảo')
    monkeypatch.setattr(cash_posting,'void_in_tx',fail_after_void)
    fails('TEST_VOID',svc.void,db,p['director'],r.id,'Hoàn lại',r.version)
    db.expire_all();assert r.status=='DA_XAC_NHAN' and db.get(CashDoc,r.cash_doc_id).status=='DA_GHI'
    assert db.query(FundLedger).count()==1 and db.query(AuditLog).count()==count
    assert cash_posting.balance(db,p['finance'],'QUY-TM')==600000


def test_direct_cash_void_source_is_locked_in_core(db,t3_people,configured_finance):
    p=t3_people;r=report(db,p,order(db,p));svc.confirm(db,p['finance'],r.id,r.version)
    pt=db.get(CashDoc,r.cash_doc_id)
    fails('SOURCE_LOCKED',cash_posting.void,db,p['director'],pt.id,'Hủy trực tiếp',pt.version)
    assert cash_posting.balance(db,p['finance'],'QUY-TM')==600000
    svc.void(db,p['director'],r.id,'Hủy từ báo thu',r.version)
    assert cash_posting.balance(db,p['finance'],'QUY-TM')==0

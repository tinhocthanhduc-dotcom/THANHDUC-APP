from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import cash_docs, cash_posting, day_closings, finance_catalogs, finance_mappings, purchase_orders, purchase_receipts, supplier_debts as svc
from app.errors import BusinessError
from app.models import AuditLog, CashDoc, CashCategory, FinanceMapping, Fund, FundBalance, FundLedger, Supplier, SupplierOpeningDebt, SupplierPayment, User
from app.schemas import FundIn, CashCategoryIn, CashCategoryPatch, CashDocIn
from test_purchase_orders import purchase_people, body, fail, DAY


@pytest.fixture
def m3_people(db,purchase_people,monkeypatch):
    p=purchase_people
    monkeypatch.setattr(cash_docs,'today',lambda:DAY)
    for code,kind,account in [('QUY-TM','TIEN_MAT',''),('TK-ACB','NGAN_HANG','123')]:
        finance_catalogs.create(db,p['finance'],Fund,FundIn(code=code,name=code,kind=kind,account_no=account))
    for code,direction in [('CHI-MH','CHI'),('THU-BH','THU')]:
        finance_catalogs.create(db,p['finance'],CashCategory,CashCategoryIn(code=code,name=code,direction=direction))
    finance_mappings.set_mapping(db,p['director'],'DANH_MUC_MUA_HANG',category_code='CHI-MH',version=1)
    doc=cash_docs.create(db,p['director'],CashDocIn(kind='PT',doc_date=date(2026,10,1),fund_code='QUY-TM',amount=5000000,is_opening=True))
    cash_posting.post(db,p['director'],doc.id,doc.version)
    return p


def received(db,p,supplier_code='NCC-0001'):
    row=purchase_orders.create(db,p['warehouse'],body(supplier_code=supplier_code,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=1,unit_cost=1000000,vat_rate=0)]))
    purchase_orders.order(db,p['warehouse'],row.id,row.version)
    receipt=purchase_receipts.receive(db,p['warehouse'],row.id,row.version,DAY,[dict(order_line_id=row.lines[0].id,qty=1)])[0]
    return row,receipt


def pay(db,p,amount=600000,**changes):
    args=dict(supplier_code='NCC-0001',fund_code='QUY-TM',amount=amount,paid_at=DAY)
    args.update(changes)
    return svc.pay(db,p['finance'],**args)


def test_balance_opening_receipts_payments_and_voided_receipt(db,m3_people):
    p=m3_people;o,r=received(db,p)
    svc.set_opening(db,p['director'],'NCC-0001',200000,DAY,'Nợ cũ',0)
    assert svc.balance(db,p['finance'],'NCC-0001')==dict(opening=200000,received=1000000,returned=0,paid=0,remaining=1200000)
    payment=pay(db,p,600000,order_id=o.id)
    assert svc.balance(db,p['finance'],'NCC-0001')==dict(opening=200000,received=1000000,returned=0,paid=600000,remaining=600000)
    assert svc.order_balance(db,p['finance'],o.id)['remaining']==400000
    purchase_receipts.void(db,p['warehouse'],r.id,'Trả lại hàng',r.version)
    assert svc.balance(db,p['finance'],'NCC-0001')['remaining']==-400000
    assert svc.order_balance(db,p['finance'],o.id)['remaining']==-600000
    assert svc.timeline(db,p['finance'],'NCC-0001')[-1]['balance']==-400000


def test_pay_posts_linked_pc_correct_category_fund_and_source(db,m3_people):
    p=m3_people;o,r=received(db,p);payment=pay(db,p,order_id=o.id)
    doc=db.get(CashDoc,payment.cash_doc_id)
    assert payment.number=='TN-26-00001' and payment.version==1
    assert (doc.kind,doc.status,doc.category_code,doc.supplier_code)==('PC','DA_GHI','CHI-MH','NCC-0001')
    assert doc.source_type=='SUPPLIER_PAYMENT' and doc.source_id==str(payment.id)
    assert payment.number in doc.description and o.number in doc.description
    assert db.get(FundBalance,'QUY-TM').balance==4400000
    assert db.scalar(select(FundLedger).where(FundLedger.doc_id==doc.id)).amount==-600000
    fail('SOURCE_LOCKED',cash_posting.void,db,p['director'],doc.id,'Hủy trực tiếp',doc.version)


def test_payment_errors_leave_no_tn_or_pc_and_preserve_numbers(db,m3_people):
    p=m3_people;o,r=received(db,p)
    db.add(Supplier(code='NCC-0002',name='Khác'));db.commit()
    wrong,_=received(db,p,'NCC-0002')
    n_docs=db.query(CashDoc).count();n_ledger=db.query(FundLedger).count();n_audit=db.query(AuditLog).count()
    fail('OVER_PAYMENT',pay,db,p,1000001)
    with pytest.raises(BusinessError) as exc:
        pay(db,p,1,order_id=wrong.id)
    assert exc.value.code=='BAD_ORDER'
    finance_mappings.set_mapping(db,p['director'],'DANH_MUC_MUA_HANG',version=2)
    fail('MAPPING_REQUIRED',pay,db,p,1)
    finance_mappings.set_mapping(db,p['director'],'DANH_MUC_MUA_HANG',category_code='CHI-MH',version=3)
    with pytest.raises(BusinessError) as exc:pay(db,p,1,fund_code='TK-ACB')
    assert exc.value.code=='NEGATIVE_CASH'
    day_closings.close(db,p['finance'],DAY,0)
    fail('DAY_CLOSED',pay,db,p,1)
    day_closings.unlock(db,p['director'],DAY,'Mở lại',1)
    db.get(Fund,'QUY-TM').active=False;db.commit()
    fail('BAD_FUND',pay,db,p,1)
    db.get(Fund,'QUY-TM').active=True;db.commit()
    assert db.query(SupplierPayment).count()==0 and db.query(CashDoc).count()==n_docs and db.query(FundLedger).count()==n_ledger
    assert pay(db,p,1).number=='TN-26-00001'


def test_order_and_supplier_caps_are_both_checked(db,m3_people):
    p=m3_people;a,_=received(db,p);b,_=received(db,p)
    pay(db,p,700000,order_id=a.id)
    with pytest.raises(BusinessError) as exc:pay(db,p,400000,order_id=a.id)
    assert exc.value.code=='OVER_PAYMENT'
    pay(db,p,1000000)
    with pytest.raises(BusinessError) as exc:pay(db,p,400000,order_id=b.id)
    assert exc.value.code=='OVER_PAYMENT'
    assert svc.balance(db,p['finance'],'NCC-0001')['remaining']==300000


def test_concurrent_payments_lock_supplier_one_winner(db,engine,m3_people):
    p=m3_people;received(db,p);barrier=Barrier(2)
    def worker(pair):
        role,paid_at=pair
        with Session(engine) as s:
            user=s.get(User,p[role].id);barrier.wait()
            try:svc.pay(s,user,'NCC-0001','QUY-TM',600000,paid_at);return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(worker,[('finance',DAY),('director',date(2026,10,17))]))
    assert sorted(result)==['OK','OVER_PAYMENT']
    assert db.query(SupplierPayment).count()==1
    assert svc.balance(db,p['finance'],'NCC-0001')['remaining']==400000


def test_void_restores_cash_and_debt_version_reason_and_audit(db,m3_people):
    p=m3_people;received(db,p);payment=pay(db,p);doc=db.get(CashDoc,payment.cash_doc_id)
    fail('REASON_REQUIRED',svc.void,db,p['finance'],payment.id,' ',payment.version)
    fail('VERSION_CONFLICT',svc.void,db,p['finance'],payment.id,'Hủy trả',payment.version+1)
    svc.void(db,p['finance'],payment.id,'Trả nhầm',payment.version)
    assert payment.status=='DA_HUY' and doc.status=='DA_HUY' and payment.voided_by==p['finance'].id
    assert db.get(FundBalance,'QUY-TM').balance==5000000
    assert svc.balance(db,p['finance'],'NCC-0001')['remaining']==1000000
    assert db.scalar(select(FundLedger).where(FundLedger.doc_id==doc.id,FundLedger.is_reversal.is_(True))).amount==600000
    assert db.scalar(select(AuditLog).where(AuditLog.action=='supplier_payment.void')).before['status']=='DA_GHI'
    fail('BAD_STATUS',svc.void,db,p['finance'],payment.id,'Hủy nữa',payment.version)


def test_opening_director_only_versions_audit_old_and_new(db,m3_people):
    p=m3_people
    fail('FORBIDDEN',svc.set_opening,db,p['finance'],'NCC-0001',1,DAY,'',0)
    row=svc.set_opening(db,p['director'],'NCC-0001',1000000,DAY,'Nợ cũ',0)
    fail('VERSION_CONFLICT',svc.set_opening,db,p['director'],'NCC-0001',900000,DAY,'Sai bản',0)
    svc.set_opening(db,p['director'],'NCC-0001',900000,DAY,'Điều chỉnh',row.version)
    logs=list(db.scalars(select(AuditLog).where(AuditLog.entity=='supplier_opening_debt').order_by(AuditLog.id)))
    assert logs[-1].before['amount']==1000000 and logs[-1].after['amount']==900000 and row.created_by==p['director'].id
    assert svc.balance(db,p['finance'],'NCC-0001')['remaining']==900000


def test_mapping_chi_only_old_keys_stay_thu_and_protect_direction(db,m3_people):
    p=m3_people
    fail('BAD_MAPPING',finance_mappings.set_mapping,db,p['director'],'DANH_MUC_MUA_HANG',None,'THU-BH',2)
    assert [r.code for r in finance_mappings.valid_options(db,p['finance'],'DANH_MUC_MUA_HANG')]==['CHI-MH']
    fail('BAD_MAPPING',finance_catalogs.update,db,p['finance'],CashCategory,'CHI-MH',CashCategoryPatch(version=1,direction='THU'))
    fail('BAD_MAPPING',finance_mappings.set_mapping,db,p['director'],'DANH_MUC_BAN_HANG',None,'CHI-MH',1)


def test_permissions_api_balance_timeline_pay_void(client,db,m3_people,login):
    p=m3_people;o,_=received(db,p)
    for role in ('sales','warehouse','technician'):
        fail('FORBIDDEN',svc.balance,db,p[role],'NCC-0001')
        fail('FORBIDDEN',svc.pay,db,p[role],'NCC-0001','QUY-TM',1,DAY)
    h=login('m3.api','finance')
    assert client.get('/supplier-debts',headers=h).status_code==200
    assert client.get('/supplier-debts/NCC-0001',headers=h).status_code==200
    r=client.post('/supplier-debts/NCC-0001/pay',headers=h,json=dict(fund_code='QUY-TM',amount=600000,paid_at=str(DAY),order_id=o.id))
    assert r.status_code==201,r.text
    detail=client.get('/supplier-debts/NCC-0001',headers=h).json()
    assert detail['payments'][0]['version']==r.json()['version'] and detail['opening'] is None
    assert client.post(f'/supplier-payments/{r.json()["id"]}/void',headers=h,json=dict(version=r.json()['version'],reason='API hủy')).status_code==200
    data=dict(amount=100000,as_of=str(DAY),note='API đầu kỳ',version=0)
    assert client.put('/supplier-debts/NCC-0001/opening',headers=h,json=data).status_code==403
    director=login('m3.api.director','director')
    opening=client.put('/supplier-debts/NCC-0001/opening',headers=director,json=data)
    assert opening.status_code==200
    assert client.get('/supplier-debts/NCC-0001',headers=h).json()['opening']['version']==opening.json()['version']
    assert client.put('/supplier-debts/NCC-0001/opening',headers=director,json=data).status_code==409


def test_failure_after_post_and_closed_day_void_roll_back(db,m3_people,monkeypatch):
    p=m3_people;received(db,p);real=cash_posting.post_in_tx
    before=(db.query(CashDoc).count(),db.query(FundLedger).count(),db.query(AuditLog).count())
    def fail_after(*args,**kwargs):
        real(*args,**kwargs)
        raise BusinessError('TEST_FAILURE','Lỗi sau ghi sổ.')
    with monkeypatch.context() as patch:
        patch.setattr(cash_posting,'post_in_tx',fail_after)
        fail('TEST_FAILURE',pay,db,p,600000)
    assert db.query(SupplierPayment).count()==0
    assert (db.query(CashDoc).count(),db.query(FundLedger).count(),db.query(AuditLog).count())==before
    assert db.get(FundBalance,'QUY-TM').balance==5000000
    payment=pay(db,p);assert payment.number=='TN-26-00001'
    day_closings.close(db,p['finance'],DAY,0)
    fail('DAY_CLOSED',svc.void,db,p['finance'],payment.id,'Hủy trả',payment.version)
    assert payment.status=='DA_GHI' and db.get(CashDoc,payment.cash_doc_id).status=='DA_GHI'


def test_timeline_search_totals_no_receipt_payment_cross_multiplication(db,m3_people):
    p=m3_people;received(db,p);received(db,p)
    pay(db,p,200000);pay(db,p,300000)
    svc.set_opening(db,p['director'],'NCC-0001',100000,DAY,'Nợ đầu kỳ',0)
    result=svc.balance(db,p['finance'],'NCC-0001')
    assert result==dict(opening=100000,received=2000000,returned=0,paid=500000,remaining=1600000)
    assert svc.search(db,p['finance'],'minh phat')[0]['remaining']==1600000
    assert svc.totals(db,p['finance'],'minh phat')==result
    assert svc.search(db,p['finance'],'khong co')==[]
    timeline=svc.timeline(db,p['finance'],'NCC-0001')
    assert timeline[-1]['balance']==1600000 and len(timeline)==5
    assert {r['kind'] for r in timeline}=={'opening','receive','payment'}

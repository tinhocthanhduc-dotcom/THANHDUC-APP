from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app import cash_docs as docs, cash_posting as posting, finance_catalogs as catalogs
from app.errors import BusinessError
from app.models import AuditLog, CashDoc, CashCategory, Fund, FundBalance, FundLedger, User
from app.schemas import CashDocIn, CashDocPatch, CashDocCancel, FundIn, FundPatch, CashCategoryIn, CashCategoryPatch

DAY=date(2026,10,10)


@pytest.fixture
def cash_people(db,make_user,monkeypatch):
    monkeypatch.setattr(docs,'today',lambda:date(2026,10,18))
    people={r:make_user('t2.'+r,r) for r in ('finance','director','sales')}
    u=people['director']
    for code,kind,account in [('QUY-TM','TIEN_MAT',''),('TK-ACB','NGAN_HANG','123')]:
        catalogs.create(db,u,Fund,FundIn(code=code,name=code,kind=kind,account_no=account))
    for code,direction in [('THU-BH','THU'),('CHI-KHAC','CHI')]:
        catalogs.create(db,u,CashCategory,CashCategoryIn(code=code,name='Thu bán hàng' if direction=='THU' else 'Chi khác',direction=direction))
    return people


def make_doc(db,user,kind='PT',amount=1000000,**changes):
    data=dict(kind=kind,doc_date=DAY,fund_code='QUY-TM',amount=amount,category_code='THU-BH' if kind=='PT' else 'CHI-KHAC' if kind=='PC' else None)
    data.update(changes)
    return docs.create(db,user,CashDocIn(**data))


def posted(db,user,kind='PT',amount=1000000,**changes):
    row=make_doc(db,user,kind,amount,**changes)
    return posting.post(db,user,row.id,row.version)


def opening(db,people,amount=5000000):
    return posted(db,people['director'],amount=amount,is_opening=True,category_code=None,doc_date=date(2026,10,1))


def fails(code,fn,*args,**kwargs):
    with pytest.raises(BusinessError) as exc:
        fn(*args,**kwargs)
    assert exc.value.code==code
    return exc.value


def test_opening_roles_income_expense_balances_and_snapshots(db,cash_people):
    p=cash_people;f=p['finance'];o=opening(db,p)
    assert o.number=='PT-26-00001'
    fails('FORBIDDEN',make_doc,db,f,is_opening=True,category_code=None)
    pt=posted(db,f);pc=posted(db,f,'PC',2000000)
    assert pt.number=='PT-26-00002' and pc.number=='PC-26-00001'
    assert posting.balance(db,f,'QUY-TM')==4000000
    lines=db.scalars(select(FundLedger).where(FundLedger.doc_id.in_([pt.id,pc.id])).order_by(FundLedger.id)).all()
    assert [(r.amount,r.balance_after) for r in lines]==[(1000000,6000000),(-2000000,4000000)]
    assert db.get(FundBalance,'QUY-TM').version>=3
    fails('FORBIDDEN',docs.update,db,f,o.id,CashDocPatch(version=o.version,is_opening=False,category_code='THU-BH'))
    fails('FORBIDDEN',posting.void,db,f,o.id,'Sai đầu kỳ',o.version)


def test_negative_cash_rolls_back_everything_and_transfer_is_atomic(db,cash_people):
    f=cash_people['finance'];opening(db,cash_people)
    too_big=make_doc(db,f,'PC',10000000)
    count=db.query(FundLedger).count();audits=db.query(AuditLog).count()
    error=fails('NEGATIVE_CASH',posting.post,db,f,too_big.id,too_big.version)
    assert error.details==dict(fund='QUY-TM',available=5000000,requested=10000000)
    assert db.query(FundLedger).count()==count and db.query(AuditLog).count()==audits
    assert docs.load(db,f,too_big.id).status=='NHAP'
    move=posted(db,f,'CQ',1000000,to_fund_code='TK-ACB')
    assert move.number=='CQ-26-00001'
    assert posting.balance(db,f,'QUY-TM')==4000000 and posting.balance(db,f,'TK-ACB')==1000000
    report=posting.statement(db,f,'QUY-TM',date(2026,10,1),DAY)
    assert report['income']==report['expense']==0 and report['transfer_out']==1000000
    assert report['closing']==4000000
    bad=make_doc(db,f,'CQ',10000000,to_fund_code='TK-ACB')
    count=db.query(FundLedger).count()
    fails('NEGATIVE_CASH',posting.post,db,f,bad.id,bad.version)
    assert db.query(FundLedger).count()==count and posting.balance(db,f,'TK-ACB')==1000000


def test_categories_transfers_partner_and_inactive_validation(db,cash_people):
    f=cash_people['finance']
    fails('BAD_CATEGORY',make_doc,db,f,category_code='CHI-KHAC')
    fails('BAD_CATEGORY',make_doc,db,f,'PC',category_code='THU-BH')
    fails('BAD_TRANSFER',make_doc,db,f,'CQ',to_fund_code='QUY-TM')
    fails('BAD_TRANSFER',make_doc,db,f,'CQ')
    fails('BAD_CATEGORY',make_doc,db,f,'CQ',to_fund_code='TK-ACB',category_code='THU-BH')
    fails('BAD_CATEGORY',make_doc,db,cash_people['director'],is_opening=True)
    fails('BAD_PARTNER',make_doc,db,f,customer_code='KTD-000001',supplier_code='NCC-0001')
    doc=make_doc(db,f)
    catalogs.update(db,f,Fund,'QUY-TM',FundPatch(version=1,active=False))
    fails('BAD_FUND',posting.post,db,f,doc.id,doc.version)
    fails('BAD_FUND',make_doc,db,f)
    catalogs.update(db,f,Fund,'QUY-TM',FundPatch(version=2,active=True))
    catalogs.update(db,f,CashCategory,'THU-BH',CashCategoryPatch(version=1,active=False))
    fails('BAD_CATEGORY',posting.post,db,f,doc.id,doc.version)
    assert db.query(FundLedger).count()==0 and db.query(FundBalance).count()==0
    assert make_doc(db,f,'PC').number=='PC-26-00001'  # Lỗi không tốn số.


def test_void_insufficient_then_reverse_on_cancellation_date(db,cash_people):
    f=cash_people['finance'];income=posted(db,f)
    spent=posted(db,f,'PC',600000)
    fails('NEGATIVE_CASH',posting.void,db,f,income.id,'Thu nhầm',income.version)
    assert docs.load(db,f,income.id).status=='DA_GHI' and db.query(FundLedger).count()==2
    fails('REASON_REQUIRED',posting.void,db,f,spent.id,' ',spent.version)
    posting.void(db,f,spent.id,'Chi nhầm',spent.version)
    assert posting.balance(db,f,'QUY-TM')==1000000
    posting.void(db,f,income.id,'Thu nhầm',income.version)
    assert posting.balance(db,f,'QUY-TM')==0
    reversed_rows=db.scalars(select(FundLedger).where(FundLedger.is_reversal).order_by(FundLedger.id)).all()
    assert [(r.amount,r.balance_after,r.entry_date) for r in reversed_rows]==[(600000,1000000,date(2026,10,18)),(-1000000,0,date(2026,10,18))]
    assert db.query(FundLedger).count()==4
    report=posting.statement(db,f,'QUY-TM',DAY,date(2026,10,18))
    assert report['income']==report['expense']==report['closing']==0
    fails('DOC_NOT_POSTED',posting.void,db,f,income.id,'Lặp',income.version)


def test_transfer_void_checks_destination_and_reverses_both_funds(db,cash_people):
    f=cash_people['finance'];opening(db,cash_people)
    transfer=posted(db,f,'CQ',1000000,to_fund_code='TK-ACB')
    expense=posted(db,f,'PC',700000,fund_code='TK-ACB')
    fails('NEGATIVE_CASH',posting.void,db,f,transfer.id,'Chuyển nhầm',transfer.version)
    assert posting.balance(db,f,'QUY-TM')==4000000 and posting.balance(db,f,'TK-ACB')==300000
    posting.void(db,f,expense.id,'Chi nhầm',expense.version)
    posting.void(db,f,transfer.id,'Chuyển nhầm',transfer.version)
    assert posting.balance(db,f,'QUY-TM')==5000000 and posting.balance(db,f,'TK-ACB')==0
    report=posting.statement(db,f,'QUY-TM',date(2026,10,1),date(2026,10,31))
    assert report['income']==report['expense']==0 and report['transfer_in']==report['transfer_out']==1000000


def test_versions_cancel_draft_number_lock_source_unique_and_audit(db,cash_people):
    f=cash_people['finance']
    doc=make_doc(db,f,source_type='sales_receipt',source_id='1')
    fails('SOURCE_USED',make_doc,db,f,source_type='sales_receipt',source_id='1')
    number=doc.number
    for change in [dict(kind='PC',category_code='CHI-KHAC'),dict(doc_date=date(2027,1,1))]:
        fails('NUMBER_LOCKED',docs.update,db,f,doc.id,CashDocPatch(version=1,**change))
    assert docs.load(db,f,doc.id).number==number
    doc=docs.update(db,f,doc.id,CashDocPatch(version=1,amount=1200000,doc_date=date(2026,10,11)))
    fails('VERSION_CONFLICT',docs.update,db,f,doc.id,CashDocPatch(version=1,amount=2))
    fails('VERSION_CONFLICT',posting.post,db,f,doc.id,1)
    fails('VERSION_REQUIRED',posting.post,db,f,doc.id,None)
    docs.cancel(db,f,doc.id,CashDocCancel(version=doc.version,reason='Nhập nhầm'))
    assert db.query(FundLedger).count()==0 and db.query(FundBalance).count()==0
    replacement=make_doc(db,f,source_type='sales_receipt',source_id='1')
    assert replacement.number=='PT-26-00002'
    logs=db.scalars(select(AuditLog).where(AuditLog.entity=='cash_doc').order_by(AuditLog.id)).all()
    assert [a.action for a in logs]==['cash_doc.create','cash_doc.update','cash_doc.cancel','cash_doc.create']
    assert logs[1].before['amount']==1000000 and logs[1].after['amount']==1200000
    fails('DOC_VOID',docs.update,db,f,doc.id,CashDocPatch(version=doc.version,amount=3))


def test_ledger_insert_only_trigger(db,cash_people):
    f=cash_people['finance'];posted(db,f)
    for query in ['update fund_ledger set amount=1','delete from fund_ledger']:
        with pytest.raises(DBAPIError) as exc:
            with db.begin_nested():
                db.execute(text(query))
        assert 'fund_ledger chi duoc them' in str(exc.value)
    assert db.query(FundLedger).count()==1 and posting.balance(db,f,'QUY-TM')==1000000


def test_two_concurrent_expenses_only_one_succeeds(db,engine,cash_people):
    f=cash_people['finance'];opening(db,cash_people,1000000)
    a=make_doc(db,f,'PC',700000);b=make_doc(db,f,'PC',700000)
    user_id=f.id;ids=[a.id,b.id];barrier=Barrier(2)
    def worker(doc_id):
        with Session(engine) as session:
            user=session.get(User,user_id)
            barrier.wait(timeout=10)
            try:
                posting.post(session,user,doc_id,1)
                return 'OK'
            except BusinessError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(worker,ids))
    assert sorted(outcomes)==['NEGATIVE_CASH','OK']
    assert posting.balance(db,f,'QUY-TM')==300000
    assert db.query(FundLedger).filter(FundLedger.doc_id.in_(ids)).count()==1
    db.expire_all()
    assert sorted(docs.load(db,f,id).status for id in ids)==['DA_GHI','NHAP']


def test_statement_period_transfer_and_backdated_entries(db,cash_people):
    f=cash_people['finance'];opening(db,cash_people)
    posted(db,f,doc_date=date(2026,10,12))
    posted(db,f,'PC',2000000,doc_date=date(2026,10,11))  # Ghi sau nhưng ngày kế toán trước.
    report=posting.statement(db,f,'QUY-TM',date(2026,10,11),date(2026,10,12))
    assert {k:report[k] for k in ('opening','income','expense','closing')}==dict(opening=5000000,income=1000000,expense=2000000,closing=4000000)
    assert [r['balance'] for r in report['entries']]==[3000000,4000000]
    empty=posting.statement(db,f,'QUY-TM',date(2026,10,13),date(2026,10,14))
    assert empty['opening']==empty['closing']==4000000 and not empty['entries']
    assert posting.balance(db,f,'TK-ACB')==0 and db.get(FundBalance,'TK-ACB') is None


def test_internal_helpers_share_transaction_rollback_and_permissions(db,cash_people):
    f=cash_people['finance'];s=cash_people['sales']
    for fn,args in [(docs.load,(1,)),(docs.search,()),(posting.balance,('QUY-TM',)),(posting.post,(1,1)),(posting.void,(1,'Hủy',1))]:
        fails('FORBIDDEN',fn,db,s,*args)
    with pytest.raises(RuntimeError):
        with docs._transaction(db):
            row=docs.insert(db,f,CashDocIn(kind='PT',doc_date=DAY,fund_code='QUY-TM',category_code='THU-BH',amount=1000000))
            posting.post_in_tx(db,f,row.id,row.version)
            raise RuntimeError('Rollback toàn bộ')
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==db.query(FundBalance).count()==0
    assert make_doc(db,f).number=='PT-26-00001'


def test_opposite_transfers_lock_funds_in_same_order(db,engine,cash_people):
    f=cash_people['finance'];opening(db,cash_people)
    posted(db,cash_people['director'],amount=5000000,is_opening=True,category_code=None,fund_code='TK-ACB')
    a=make_doc(db,f,'CQ',1000000,to_fund_code='TK-ACB')
    b=make_doc(db,f,'CQ',1000000,fund_code='TK-ACB',to_fund_code='QUY-TM')
    user_id=f.id;barrier=Barrier(2)
    def worker(doc_id):
        with Session(engine) as session:
            session.execute(text("SET LOCAL statement_timeout='5s'"))
            user=session.get(User,user_id);barrier.wait(timeout=10)
            return posting.post(session,user,doc_id,1).status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(worker,[a.id,b.id]))==['DA_GHI','DA_GHI']
    assert posting.balance(db,f,'QUY-TM')==posting.balance(db,f,'TK-ACB')==5000000
    assert db.query(FundLedger).count()==6


def test_internal_void_rolls_back_and_posted_cannot_be_edited(db,cash_people):
    f=cash_people['finance'];row=posted(db,f)
    fails('DOC_POSTED',docs.update,db,f,row.id,CashDocPatch(version=row.version,amount=1))
    fails('DOC_POSTED',docs.cancel,db,f,row.id,CashDocCancel(version=row.version,reason='Hủy'))
    before=db.query(AuditLog).count()
    with pytest.raises(RuntimeError):
        with docs._transaction(db):
            posting.void_in_tx(db,f,row.id,'Thử rollback',row.version)
            raise RuntimeError('Caller rollback')
    assert docs.load(db,f,row.id).status=='DA_GHI'
    assert posting.balance(db,f,'QUY-TM')==1000000
    assert db.query(FundLedger).count()==1 and db.query(AuditLog).count()==before

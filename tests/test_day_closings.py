from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from threading import Barrier

import pytest
from sqlalchemy import select,text
from sqlalchemy.orm import Session

from app import cash_docs as docs,cash_posting as posting,cash_corrections as corrections,day_closings as days
from app.errors import BusinessError
from app.models import AuditLog,CashCorrection,CashDoc,DayClosing,FundLedger,User
from app.schemas import CashCorrectionIn,CashDocCancel,CashDocPatch
from test_cash import cash_people,make_doc,posted,fails,DAY

TODAY=date(2026,10,18)


def correction(db,user,doc,amount=900000,**kw):
    return corrections.request(db,user,doc.id,CashCorrectionIn(version=doc.version,reason='Sai số tiền',new_amount=amount,**kw))


def test_close_blocks_create_update_post_cancel_old_and_new_day(db,cash_people):
    f=cash_people['finance'];p=posted(db,f)
    row=days.close(db,f,DAY,0)
    assert row.status=='DA_CHOT' and row.version==1
    fails('DAY_CLOSED',make_doc,db,f)
    fails('DAY_CLOSED',docs.update,db,f,p.id,CashDocPatch(version=p.version,description='Sửa'))
    fails('DAY_CLOSED',posting.post,db,f,p.id,p.version)
    fails('DAY_CLOSED',posting.void,db,f,p.id,'Hủy',p.version)
    fails('DAY_CLOSED',docs.cancel,db,f,p.id,CashDocCancel(version=p.version,reason='Hủy'))
    next_doc=make_doc(db,f,doc_date=DAY+timedelta(days=1))
    fails('DAY_CLOSED',docs.update,db,f,next_doc.id,CashDocPatch(version=next_doc.version,doc_date=DAY))
    posted(db,f,doc_date=DAY+timedelta(days=1))
    assert posting.balance(db,f,'QUY-TM')==2000000
    assert db.query(DayClosing).count()==1


def test_drafts_future_unlock_permissions_reclose_versions_audit(db,cash_people):
    f,d=cash_people['finance'],cash_people['director'];doc=make_doc(db,f)
    e=fails('DRAFTS_OPEN',days.close,db,f,DAY,0)
    assert e.details['numbers']==[doc.number]
    fails('FUTURE_DAY',days.close,db,f,TODAY+timedelta(days=1),0)
    docs.cancel(db,f,doc.id,CashDocCancel(version=doc.version,reason='Hủy nháp'))
    row=days.close(db,f,DAY,0)
    fails('FORBIDDEN',days.unlock,db,f,DAY,'Mở',row.version)
    fails('REASON_REQUIRED',days.unlock,db,d,DAY,' ',row.version)
    row=days.unlock(db,d,DAY,'Kiểm tra lại',row.version)
    assert row.status=='DA_MO'
    posted(db,f)
    assert db.get(DayClosing,DAY).status=='DA_MO'
    fails('VERSION_CONFLICT',days.close,db,f,DAY,1)
    row=days.close(db,f,DAY,row.version)
    assert row.status=='DA_CHOT' and row.version==3
    logs=db.scalars(select(AuditLog).where(AuditLog.entity=='day_closing').order_by(AuditLog.id)).all()
    assert [r.action for r in logs]==['day_closing.close','day_closing.unlock','day_closing.close']
    assert logs[1].before['status']=='DA_CHOT' and logs[1].after['status']=='DA_MO'


def test_void_checks_today_and_original_but_correction_can_skip_only_original(db,cash_people):
    f,d=cash_people['finance'],cash_people['director'];p=posted(db,f)
    days.close(db,f,TODAY,0)
    fails('DAY_CLOSED',posting.void,db,f,p.id,'Hủy',p.version)
    req=correction(db,f,p)
    fails('DAY_CLOSED',corrections.approve,db,d,req.id,req.version)
    assert db.get(CashDoc,p.id).status=='DA_GHI' and db.query(FundLedger).count()==1
    days.unlock(db,d,TODAY,'Mở ngày hiện tại',1)
    days.close(db,f,DAY,0)
    fails('DAY_CLOSED',posting.void_in_tx,db,f,p.id,'Hủy',p.version)
    db.rollback()
    fails('FORBIDDEN',posting.void_in_tx,db,d,p.id,'Lách',p.version,_correction_permit=True)
    db.rollback()
    corrections.approve(db,d,req.id,req.version)
    assert days.is_closed(db,DAY)
    assert db.get(CashDoc,p.id).status=='DA_HUY' and posting.balance(db,f,'QUY-TM')==900000


def test_correction_pending_approve_positive_today_and_audit(db,cash_people):
    f,d=cash_people['finance'],cash_people['director'];p=posted(db,f)
    days.close(db,f,DAY,0)
    req=correction(db,f,p)
    fails('CORRECTION_PENDING',correction,db,f,p)
    fails('FORBIDDEN',corrections.approve,db,f,req.id,req.version)
    fails('VERSION_CONFLICT',corrections.approve,db,d,req.id,2)
    corrections.approve(db,d,req.id,req.version)
    new=db.get(CashDoc,req.new_doc_id)
    assert p.status=='DA_HUY' and new.kind=='PT' and new.amount==900000 and new.doc_date==TODAY and new.status=='DA_GHI'
    assert new.description.startswith('Điều chỉnh cho '+p.number)
    assert posting.balance(db,f,'QUY-TM')==900000 and db.query(CashDoc).filter(CashDoc.amount<=0).count()==0
    assert db.query(FundLedger).count()==3
    assert db.get(DayClosing,DAY).status=='DA_CHOT'
    logs=db.scalars(select(AuditLog).where(AuditLog.entity=='cash_correction').order_by(AuditLog.id)).all()
    assert [a.action for a in logs]==['cash_correction.request','cash_correction.approve']
    assert logs[-1].before['status']=='CHO_DUYET' and logs[-1].after['new_doc_id']==new.id


def test_correction_reject_source_opening_and_no_partial_negative(db,cash_people):
    f,d=cash_people['finance'],cash_people['director'];p=posted(db,f)
    req=correction(db,f,p)
    fails('REASON_REQUIRED',corrections.reject,db,d,req.id,'',req.version)
    fails('FORBIDDEN',corrections.reject,db,f,req.id,'Không',req.version)
    corrections.reject(db,d,req.id,'Không đúng',req.version)
    assert req.status=='TU_CHOI' and req.decide_reason=='Không đúng'
    new=correction(db,f,p)
    posted(db,f,'PC',500000)
    n=db.query(FundLedger).count();audit=db.query(AuditLog).count()
    fails('NEGATIVE_CASH',corrections.approve,db,d,new.id,new.version)
    db.expire_all();assert new.status=='CHO_DUYET' and p.status=='DA_GHI'
    assert db.query(FundLedger).count()==n and db.query(AuditLog).count()==audit
    assert db.query(CashDoc).count()==2
    source=posted(db,f,source_type='SALES_RECEIPT',source_id='123')
    fails('SOURCE_LOCKED',correction,db,f,source)
    initial=posted(db,d,is_opening=True,category_code=None)
    fails('BAD_STATUS',correction,db,f,initial)
    fails('FORBIDDEN',days.close,db,cash_people['sales'],TODAY,0)


def test_close_and_create_race_cannot_leave_draft_on_closed_day(db,engine,cash_people):
    uid=cash_people['finance'].id;barrier=Barrier(2)
    def worker(action):
        with Session(engine) as s:
            u=s.get(User,uid);barrier.wait(timeout=10)
            try:
                if action=='close':days.close(s,u,DAY,0)
                else:make_doc(s,u)
                return action
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,['close','create']))
    assert sorted(results) in [sorted(['close','DAY_CLOSED']),sorted(['DRAFTS_OPEN','create'])]
    assert not (days.is_closed(db,DAY) and db.query(CashDoc).filter_by(doc_date=DAY,status='NHAP').count())


def test_correction_new_fund_category_and_failed_new_post_rollback(db,cash_people,monkeypatch):
    f,d=cash_people['finance'],cash_people['director'];p=posted(db,f)
    req=correction(db,f,p,new_fund_code='TK-ACB',new_description='Chuyển đúng quỹ')
    actual=posting.post_in_tx
    def failed(*args):
        actual(*args)
        raise BusinessError('TEST_ERROR','Lỗi sau ghi mới')
    monkeypatch.setattr(posting,'post_in_tx',failed)
    fails('TEST_ERROR',corrections.approve,db,d,req.id,req.version)
    db.expire_all();assert req.status=='CHO_DUYET' and p.status=='DA_GHI'
    assert posting.balance(db,f,'QUY-TM')==1000000 and posting.balance(db,f,'TK-ACB')==0
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==1
    monkeypatch.setattr(posting,'post_in_tx',actual)
    corrections.approve(db,d,req.id,req.version)
    assert posting.balance(db,f,'QUY-TM')==0 and posting.balance(db,f,'TK-ACB')==900000


def test_receipt_confirmation_closed_day_rolls_back(db,configured_finance,t3_people,monkeypatch):
    from test_receipt_cash import order,report
    from app import sales_receipts
    monkeypatch.setattr(docs,'today',lambda:TODAY)
    p=t3_people;r=report(db,p,order(db,p))
    days.close(db,p['finance'],r.received_at,0)
    fails('DAY_CLOSED',sales_receipts.confirm,db,p['finance'],r.id,r.version)
    db.expire_all();assert r.status=='CHO_XAC_NHAN' and r.cash_doc_id is None
    assert db.query(CashDoc).count()==0 and db.query(FundLedger).count()==0

from test_receipt_cash import t3_people


def test_close_and_post_serialize_without_late_post(db,engine,cash_people):
    doc=make_doc(db,cash_people['finance']);did,uid=doc.id,cash_people['finance'].id;barrier=Barrier(2)
    def worker(action):
        with Session(engine) as s:
            u=s.get(User,uid);barrier.wait(timeout=10)
            try:
                if action=='close':days.close(s,u,DAY,0)
                else:posting.post(s,u,did,1)
                return action
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,['close','post']))
    assert sorted(results) in [sorted(['close','post']),sorted(['DRAFTS_OPEN','post'])]
    db.expire_all();assert doc.status=='DA_GHI' and db.query(FundLedger).count()==1
    if 'close' in results:assert days.is_closed(db,DAY)

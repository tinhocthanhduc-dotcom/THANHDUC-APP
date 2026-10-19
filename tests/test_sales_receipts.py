from datetime import date, datetime, UTC, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import sales_orders as orders, sales_receipts as svc
from app.errors import BusinessError
from app.models import AuditLog, SalesOrder, SalesReceipt, User
from app.schemas import SalesOrderIn, SalesReceiptReport
from app.security import issue_token
from test_sales_orders import people


pytestmark=pytest.mark.usefixtures('configured_finance')


@pytest.fixture
def order(db,people,monkeypatch):
    monkeypatch.setattr(svc,'_today',lambda:date(2026,10,18))
    o=orders.create(db,people['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[
        dict(kind='KHAC',description='Đơn một triệu',qty=1,unit_price=1000000,vat_rate=0)]))
    orders.confirm(db,people['sales'],o.id,o.version)
    orders.deliver(db,people['sales'],o.id,o.version)
    return o


def report(db,user,o,amount=600000,**changes):
    return svc.report(db,user,o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=amount,**changes))


def fail(code,fn,*args):
    with pytest.raises(BusinessError) as exc:
        fn(*args)
    assert exc.value.code==code
    return exc.value


def test_two_reports_capacity_numbers_and_balance(db,people,order):
    a=report(db,people['sales'],order,600000)
    b=report(db,people['sales'],order,400000)
    assert [a.number,b.number]==['BT-26-00001','BT-26-00002']
    assert a.status=='CHO_XAC_NHAN' and a.version==1 and a.received_at==date(2026,10,18)
    assert svc.balance(order)==dict(receivable=1000000,paid=0,pending=1000000,remaining=1000000,advance=0,available=0)
    assert fail('OVER_PAYMENT',report,db,people['sales'],order,1).status==409
    assert db.query(SalesReceipt).count()==2
    assert order.receivable==1000000 and order.version==3


def test_report_only_confirmed_or_delivered(db,people):
    draft=orders.create(db,people['sales'],SalesOrderIn(customer_code='KTD-000001'))
    fail('BAD_STATUS',report,db,people['sales'],draft)
    orders.cancel(db,people['sales'],draft.id,'Không bán nữa',draft.version)
    fail('BAD_STATUS',report,db,people['sales'],draft)


def test_finance_confirmation_balance_pending_and_remaining(db,people,order):
    a=report(db,people['sales'],order,600000)
    b=report(db,people['sales'],order,400000)
    svc.confirm(db,people['finance'],a.id,a.version)
    assert svc.balance(order)==dict(receivable=1000000,paid=600000,pending=400000,remaining=400000,advance=0,available=0)
    assert a.decided_by==people['finance'].id and a.decided_at is not None and a.version==2
    assert [r.id for r in svc.list_pending(db,people['finance'])]==[b.id]
    svc.confirm(db,people['finance'],b.id,b.version)
    assert svc.balance(order)['remaining']==0 and svc.receivables(db,people['sales'])==[]


def test_reject_reason_releases_capacity(db,people,order):
    r=report(db,people['sales'],order,1000000)
    fail('REASON_REQUIRED',svc.reject,db,people['finance'],r.id,'  ',r.version)
    svc.reject(db,people['finance'],r.id,'Chưa nhận được tiền',r.version)
    assert svc.balance(order)['pending']==0
    new=report(db,people['sales'],order,1000000)
    assert new.number=='BT-26-00002' and r.status=='TU_CHOI'
    fail('BAD_STATUS',svc.confirm,db,people['finance'],r.id,r.version)


def test_self_confirmation_and_director_exception(db,people,order,make_user):
    r=report(db,people['sales'],order,100000)
    assert fail('FORBIDDEN',svc.confirm,db,people['sales'],r.id,r.version).status==403
    dual=make_user('dual','sales','finance')
    own=report(db,dual,order,200000)
    assert fail('SELF_CONFIRM',svc.confirm,db,dual,own.id,own.version).status==403
    # Kế toán khác vẫn xác nhận được phiếu của người có hai vai trò.
    svc.confirm(db,people['finance'],own.id,own.version)
    director=report(db,people['director'],order,100000)
    svc.confirm(db,people['director'],director.id,director.version)
    assert director.status=='DA_XAC_NHAN'


def test_cancel_blocked_until_pending_rejected_or_confirmed_voided(db,people,order):
    gh=orders.deliveries(db,order.id)[0]
    orders.void_delivery(db,people['director'],gh.id,'Chưa giao',gh.version)
    pending=report(db,people['sales'],order,600000)
    confirmed=report(db,people['sales'],order,400000)
    svc.confirm(db,people['finance'],confirmed.id,confirmed.version)
    fail('HAS_RECEIPTS',orders.cancel,db,people['sales'],order.id,'Khách hủy',order.version)
    svc.reject(db,people['finance'],pending.id,'Chưa nhận tiền',pending.version)
    fail('HAS_RECEIPTS',orders.cancel,db,people['sales'],order.id,'Khách hủy',order.version)
    svc.void(db,people['director'],confirmed.id,'Hoàn tiền cho khách',confirmed.version)
    orders.cancel(db,people['sales'],order.id,'Khách hủy',order.version)
    assert order.status=='DA_HUY' and len(svc.for_order(db,people['finance'],order.id)['receipts'])==2


def test_void_director_reason_and_debt_restored(db,people,order):
    r=report(db,people['sales'],order,600000)
    fail('BAD_STATUS',svc.void,db,people['director'],r.id,'Không hợp lệ',r.version)
    svc.confirm(db,people['finance'],r.id,r.version)
    assert svc.balance(order)['remaining']==400000
    fail('FORBIDDEN',svc.void,db,people['finance'],r.id,'Hoàn tiền',r.version)
    fail('REASON_REQUIRED',svc.void,db,people['director'],r.id,'',r.version)
    svc.void(db,people['director'],r.id,'Hoàn tiền',r.version)
    assert r.status=='DA_HUY' and r.amount==600000 and svc.balance(order)['remaining']==1000000
    fail('BAD_STATUS',svc.void,db,people['director'],r.id,'Hủy lại',r.version)


def test_versions_and_audit_every_operation(db,people,order):
    fail('VERSION_CONFLICT',svc.report,db,people['sales'],order.id,SalesReceiptReport(version=1,method='TIEN_MAT',amount=1))
    a=report(db,people['sales'],order,600000)
    svc.confirm(db,people['finance'],a.id,a.version)
    fail('VERSION_CONFLICT',svc.void,db,people['director'],a.id,'Hoàn tiền',1)
    svc.void(db,people['director'],a.id,'Hoàn tiền',a.version)
    b=report(db,people['sales'],order,400000)
    svc.reject(db,people['finance'],b.id,'Chưa nhận',b.version)
    fail('VERSION_CONFLICT',svc.confirm,db,people['finance'],b.id,1)
    logs=list(db.scalars(select(AuditLog).where(AuditLog.entity=='sales_receipt').order_by(AuditLog.id)))
    assert [r.action for r in logs]==['sales_receipt.report','sales_receipt.confirm','sales_receipt.void','sales_receipt.report','sales_receipt.reject']
    for log in logs:
        assert log.after['amount']>0 and log.after['number'].startswith('BT-26-')
        if log.action=='sales_receipt.report':
            assert log.before is None
        else:
            assert log.after['version']==log.before['version']+1


def test_concurrent_reports_lock_order_not_only_receipts(db,engine,people,order):
    oid,uid,version=order.id,people['sales'].id,order.version
    barrier=Barrier(2)
    def attempt(_):
        with Session(engine,expire_on_commit=False) as s:
            u=s.get(User,uid)
            orders.load(s,u,oid)
            barrier.wait(timeout=10)
            try:
                return svc.report(s,u,oid,SalesReceiptReport(version=version,method='TIEN_MAT',amount=600000)).number
            except BusinessError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,[1,2]))==['BT-26-00001','OVER_PAYMENT']
    assert svc.balance(order)['pending']==600000
    assert report(db,people['sales'],order,400000).number=='BT-26-00002'


def test_report_cancel_race_never_leaves_receipt_on_cancelled_order(db,engine,people,order):
    gh=orders.deliveries(db,order.id)[0]
    orders.void_delivery(db,people['director'],gh.id,'Chưa giao',gh.version)
    oid,uid,version=order.id,people['sales'].id,order.version
    barrier=Barrier(2)
    def attempt(action):
        with Session(engine,expire_on_commit=False) as s:
            u=s.get(User,uid)
            barrier.wait(timeout=10)
            try:
                if action=='report':
                    svc.report(s,u,oid,SalesReceiptReport(version=version,method='TIEN_MAT',amount=100000))
                else:
                    orders.cancel(s,u,oid,'Khách hủy',version)
                return action
            except BusinessError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(attempt,['report','cancel']))
    db.expire_all()
    assert sorted(result) in [sorted(['report','HAS_RECEIPTS']),sorted(['VERSION_CONFLICT','cancel'])]
    assert not (order.status=='DA_HUY' and db.query(SalesReceipt).count())


def test_pending_filters_oldest_and_receivables_age(db,people,order):
    a=report(db,people['sales'],order,100000,received_at=date(2026,10,1))
    b=svc.report(db,people['sales'],order.id,SalesReceiptReport(version=order.version,method='CHUYEN_KHOAN',amount=200000,received_at=date(2026,10,2)))
    a.reported_at=datetime(2026,10,3,tzinfo=UTC)
    b.reported_at=datetime(2026,10,4,tzinfo=UTC)
    orders.deliveries(db,order.id)[0].delivered_at=datetime(2026,10,10,16,tzinfo=UTC)
    db.commit()
    assert [r.id for r in svc.list_pending(db,people['finance'])]==[a.id,b.id]
    assert [r.id for r in svc.list_pending(db,people['finance'],'CHUYEN_KHOAN',date(2026,10,2),date(2026,10,2))]==[b.id]
    fail('BAD_METHOD',svc.list_pending,db,people['finance'],'SAI')
    fail('BAD_DATE_RANGE',svc.list_pending,db,people['finance'],None,date(2026,10,2),date(2026,10,1))
    rows=svc.receivables(db,people['finance'])
    assert rows[0]['days']==8 and rows[0]['balance']['pending']==300000


def test_api_routes_permissions_and_validation(client,db,people,order):
    h=lambda role:{'Authorization':'Bearer '+issue_token(people[role])}
    url=f'/sales-orders/{order.id}/receipts'
    data=dict(version=order.version,method='TIEN_MAT',amount=1000000,received_at='2026-10-18')
    r=client.post(url,headers=h('sales'),json=data)
    assert r.status_code==201 and r.json()['number']=='BT-26-00001'
    rid=r.json()['id']
    assert client.post(f'/sales-receipts/{rid}/confirm',headers=h('sales'),json=dict(version=1)).status_code==403
    assert client.get('/sales-receipts',headers=h('sales')).status_code==403
    assert client.get('/sales-orders/receivables',headers=h('finance')).status_code==200
    assert client.get('/sales-orders/receivables',headers=h('warehouse')).status_code==403
    assert client.get(url,headers=h('technician')).status_code==403
    for role in ('finance','warehouse','technician'):
        assert client.post(url,headers=h(role),json=data).status_code==403
    for bad in (0,-1,1.5):
        assert client.post(url,headers=h('sales'),json={**data,'amount':bad}).status_code==422
    assert client.post(url,headers=h('sales'),json={k:v for k,v in data.items() if k!='version'}).status_code==422
    r=client.post(f'/sales-receipts/{rid}/confirm',headers=h('finance'),json=dict(version=1))
    assert r.status_code==200 and r.json()['status']=='DA_XAC_NHAN'
    assert client.get(url,headers=h('finance')).json()['balance']['remaining']==0
    assert client.get('/sales-orders/receivables',headers=h('finance')).json()==[]
    assert client.post(f'/sales-receipts/{rid}/void',headers=h('director'),json=dict(version=2,reason='Hoàn tiền')).status_code==200
    assert client.get('/sales-orders/receivables',headers=h('finance')).json()[0]['balance']['remaining']==1000000


def test_concurrent_confirmation_one_winner_and_one_audit(db,engine,people,order):
    receipt=report(db,people['sales'],order,600000)
    rid,uid,version=receipt.id,people['finance'].id,receipt.version
    barrier=Barrier(2)
    def decide(_):
        with Session(engine,expire_on_commit=False) as s:
            user=s.get(User,uid)
            s.get(SalesReceipt,rid)  # Hai identity map đều đã đọc version cũ.
            barrier.wait(timeout=10)
            try:
                svc.confirm(s,user,rid,version)
                return 'OK'
            except BusinessError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decide,[1,2]))==['OK','VERSION_CONFLICT']
    assert svc.balance(order)['paid']==600000
    assert db.query(AuditLog).filter_by(action='sales_receipt.confirm',entity_id=str(rid)).count()==1


def test_receivables_oldest_first_and_pending_is_still_debt(db,people,order):
    older=orders.create(db,people['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[
        dict(kind='KHAC',description='Đơn cũ',qty=1,unit_price=1000000,vat_rate=0)]))
    orders.confirm(db,people['sales'],older.id,older.version)
    orders.deliver(db,people['sales'],older.id,older.version)
    orders.deliveries(db,order.id)[0].delivered_at=datetime(2026,10,10,16,tzinfo=UTC)
    orders.deliveries(db,older.id)[0].delivered_at=datetime(2026,10,1,16,tzinfo=UTC)
    db.commit()
    pending=report(db,people['sales'],older,1000000)
    rows=svc.receivables(db,people['finance'])
    assert [r['order'].id for r in rows]==[older.id,order.id]
    assert rows[0]['balance']['pending']==1000000 and rows[0]['days']==17
    svc.confirm(db,people['finance'],pending.id,pending.version)
    assert [r['order'].id for r in svc.receivables(db,people['sales'])]==[order.id]

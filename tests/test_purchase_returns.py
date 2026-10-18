from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import purchase_returns as svc, purchase_receipts, purchase_orders, supplier_debts, stock_docs, stock_posting
from app.models import AuditLog, FundLedger, PurchaseReturn, StockBalance, StockDoc, StockLedger, User
from app.schemas import StockDocIn
from test_purchase_orders import purchase_people, DAY
from test_purchase_receipts import placed, receive
from test_supplier_debts import m3_people, received, pay


def fail(code, fn, *args, **kwargs):
    from app.errors import BusinessError
    with pytest.raises(BusinessError) as exc:fn(*args,**kwargs)
    assert exc.value.code==code
    return exc.value


def returned(db,p,order,receipt,qty=1,**changes):
    args=dict(receipt_id=receipt.id,version=order.version,return_date=DAY,reason='Hàng lỗi',
        lines=[dict(receipt_line_id=receipt.lines[0].id,qty=qty)])
    args.update(changes)
    return svc.create(db,p['warehouse'],**args)


def test_partial_return_posts_average_cost_px_but_reduces_debt_at_purchase_price(db,purchase_people):
    p=purchase_people;order=placed(db,p,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=3,unit_cost=1001)])
    receipt=receive(db,p,order)[0]
    extra=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code='K58',supplier_code='NCC-0001',doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty=3,unit_cost=2001)]))
    stock_posting.post(db,p['warehouse'],extra.id,extra.version)
    version=order.version;result=returned(db,p,order,receipt)
    doc=db.get(StockDoc,result.stock_doc_id);bal=db.get(StockBalance,('K58','TD-0001'))
    assert result.number=='TH-26-00001' and (result.amount,result.vat_amount,result.total)==(1001,80,1081)
    assert (doc.kind,doc.status,doc.supplier_code,doc.warehouse_code)==('PX','DA_GHI','NCC-0001','K58')
    assert result.number in doc.reason and order.number in doc.reason and doc.lines[0].unit_cost is None
    assert db.scalar(select(StockLedger).where(StockLedger.doc_id==doc.id)).amount==-1501
    assert (bal.qty,bal.value)==(5,7505)
    assert supplier_debts.balance(db,p['finance'],'NCC-0001')==dict(opening=0,received=3243,returned=1081,paid=0,remaining=2162)
    assert supplier_debts.order_balance(db,p['finance'],order.id)['remaining']==2162
    assert order.lines[0].received_qty==3 and order.status=='DA_NHAN' and order.version==version+1
    assert db.query(FundLedger).count()==0


@pytest.mark.parametrize('cost',[1001,17])
def test_three_returns_sum_to_receipt_including_final_vat_residual_and_voided_rows(db,purchase_people,cost):
    p=purchase_people;order=placed(db,p,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=3,unit_cost=cost)])
    receipt=receive(db,p,order)[0]
    rows=[returned(db,p,order,receipt) for _ in range(3)]
    assert sum(r.amount for r in rows)==receipt.amount
    assert sum(r.vat_amount for r in rows)==receipt.vat_amount
    assert sum(r.total for r in rows)==receipt.total
    assert supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==0
    svc.void(db,p['warehouse'],rows[-1].id,'Trả lại cho kho',rows[-1].version)
    again=returned(db,p,order,receipt)
    assert (again.amount,again.vat_amount)==(rows[-1].amount,rows[-1].vat_amount)
    assert again.number=='TH-26-00004' and order.lines[0].received_qty==3
    assert db.get(StockBalance,('K58','TD-0001')).qty==0
    assert db.query(FundLedger).count()==0


def test_validation_foreign_over_empty_duplicate_quantity_reason_and_voided_receipt(db,purchase_people):
    p=purchase_people;order=placed(db,p);receipt=receive(db,p,order)[0]
    other_order=placed(db,p);other_receipt=receive(db,p,other_order)[0]
    fail('BAD_RECEIPT_LINE',returned,db,p,order,receipt,1,**{'lines':[dict(receipt_line_id=other_receipt.lines[0].id,qty=1)]})
    fail('OVER_RETURN',returned,db,p,order,receipt,2)
    fail('REASON_REQUIRED',returned,db,p,order,receipt,1,**{'reason':' '})
    fail('EMPTY_RETURN',returned,db,p,order,receipt,1,**{'lines':[]})
    fail('DUPLICATE_LINE',returned,db,p,order,receipt,1,**{'lines':[dict(receipt_line_id=receipt.lines[0].id,qty=1)]*2})
    for qty in (0,-1,'0.0001','NaN'):fail('BAD_RETURN',returned,db,p,order,receipt,qty)
    assert db.query(PurchaseReturn).count()==0 and db.query(StockDoc).filter_by(kind='PX').count()==0
    purchase_receipts.void(db,p['warehouse'],receipt.id,'Hủy nhận',receipt.version)
    fail('BAD_STATUS',returned,db,p,order,receipt)


def test_negative_stock_rolls_back_return_px_numbers_and_audit(db,purchase_people):
    p=purchase_people;order=placed(db,p);receipt=receive(db,p,order)[0]
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PX',warehouse_code='K58',doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty='1.5')]))
    stock_posting.post(db,p['warehouse'],doc.id,doc.version)
    version=order.version;n_audit=db.query(AuditLog).count();n_docs=db.query(StockDoc).count()
    fail('NEGATIVE_STOCK',returned,db,p,order,receipt)
    assert db.query(PurchaseReturn).count()==0 and db.query(StockDoc).count()==n_docs and db.query(AuditLog).count()==n_audit
    assert order.version==version and order.lines[0].received_qty==Decimal('1.5')
    stock_posting.void(db,p['warehouse'],doc.id,'Hủy xuất thử',doc.version)
    assert returned(db,p,order,receipt).number=='TH-26-00001'
    assert db.query(FundLedger).count()==0


def test_void_restores_stock_debt_and_guards_direct_stock_void_receipt_void(db,purchase_people):
    p=purchase_people;order=placed(db,p);receipt=receive(db,p,order)[0];result=returned(db,p,order,receipt)
    doc=db.get(StockDoc,result.stock_doc_id)
    fail('SOURCE_LOCKED',stock_posting.void,db,p['warehouse'],doc.id,'Hủy trực tiếp PX',doc.version)
    fail('HAS_RETURNS',purchase_receipts.void,db,p['warehouse'],receipt.id,'Hủy nhận',receipt.version)
    fail('REASON_REQUIRED',svc.void,db,p['warehouse'],result.id,' ',result.version)
    fail('VERSION_CONFLICT',svc.void,db,p['warehouse'],result.id,'Hủy trả',result.version+1)
    svc.void(db,p['director'],result.id,'Nhận lại hàng',result.version)
    assert result.status=='DA_HUY' and result.voided_by==p['director'].id and result.voided_at
    assert db.get(StockDoc,result.stock_doc_id).status=='DA_HUY'
    assert db.get(StockBalance,('K58','TD-0001')).qty==Decimal('1.5')
    assert supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==receipt.total
    timeline=supplier_debts.timeline(db,p['finance'],'NCC-0001')
    events=[e for e in timeline if e['kind'] in ('return','void_return')]
    assert [(e['kind'],e['change']) for e in events]==[('return',-result.total),('void_return',result.total)]
    assert events[0]['number']==result.number and events[0]['stock_number']==doc.number
    assert timeline[-1]['balance']==receipt.total
    fail('BAD_STATUS',svc.void,db,p['warehouse'],result.id,'Hủy lần nữa',result.version)
    purchase_receipts.void(db,p['warehouse'],receipt.id,'Hủy nhận',receipt.version)
    assert supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==0
    assert db.query(FundLedger).count()==0


def test_paid_order_return_creates_advance_and_payment_caps_include_returns(db,m3_people):
    p=m3_people;order,receipt=received(db,p)
    pay(db,p,1000000,order_id=order.id);n_fund=db.query(FundLedger).count()
    result=returned(db,p,order,receipt,Decimal('0.4'))
    assert supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==-400000
    assert supplier_debts.order_balance(db,p['finance'],order.id)['remaining']==-400000
    fail('OVER_PAYMENT',pay,db,p,1)
    fail('OVER_PAYMENT',pay,db,p,1,**{'order_id':order.id})
    assert db.query(FundLedger).count()==n_fund
    svc.void(db,p['warehouse'],result.id,'Giữ hàng',result.version)
    assert supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==0
    # Nợ mới 1 triệu, trả hàng 400k -> cả giới hạn NCC và đơn còn 600k.
    another,rec=received(db,p);returned(db,p,another,rec,Decimal('0.4'))
    fail('OVER_PAYMENT',pay,db,p,600001)
    fail('OVER_PAYMENT',pay,db,p,600001,**{'order_id':another.id})
    assert db.query(FundLedger).count()==n_fund


def test_roles_versions_and_audit(db,purchase_people):
    p=purchase_people;order=placed(db,p);receipt=receive(db,p,order)[0];old=order.version
    for role in ('sales','technician','finance'):
        fail('FORBIDDEN',svc.create,db,p[role],receipt.id,order.version,DAY,'Trả hàng',[dict(receipt_line_id=receipt.lines[0].id,qty=1)])
    result=returned(db,p,order,receipt,Decimal('0.5'))
    fail('VERSION_CONFLICT',returned,db,p,order,receipt,Decimal('0.5'),**{'version':old})
    for role in ('sales','technician','finance'):fail('FORBIDDEN',svc.void,db,p[role],result.id,'Hủy trả',result.version)
    assert svc.load(db,p['finance'],result.id).id==result.id
    assert svc.list_by_order(db,p['finance'],order.id)[0].id==result.id
    fail('FORBIDDEN',svc.list_by_order,db,p['sales'],order.id)
    log=db.scalar(select(AuditLog).where(AuditLog.action=='purchase_return.create'))
    assert log.before is None and log.after['number']==result.number
    svc.void(db,p['warehouse'],result.id,'Hủy trả',result.version)
    log=db.scalar(select(AuditLog).where(AuditLog.action=='purchase_return.void'))
    assert log.before['status']=='DA_GHI' and log.after['status']=='DA_HUY' and log.reason=='Hủy trả'


def test_concurrent_returns_same_order_version_only_one_wins(db,engine,purchase_people):
    p=purchase_people;order=placed(db,p);receipt=receive(db,p,order)[0]
    ident,version,line_id=receipt.id,order.version,receipt.lines[0].id;barrier=Barrier(2)
    def worker(role):
        with Session(engine) as s:
            user=s.get(User,p[role].id);barrier.wait()
            try:svc.create(s,user,ident,version,DAY,'Trả lỗi',[dict(receipt_line_id=line_id,qty=1)]);return 'OK'
            except Exception as exc:return exc.code if hasattr(exc,'code') else str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,['warehouse','director']))
    assert sorted(results)==['OK','VERSION_CONFLICT']
    db.expire_all();assert db.query(PurchaseReturn).count()==1
    assert db.get(StockBalance,('K58','TD-0001')).qty==Decimal('0.5')


def test_failure_after_post_rolls_back_all_changes(db,purchase_people,monkeypatch):
    from app.errors import BusinessError
    p=purchase_people;order=placed(db,p);receipt=receive(db,p,order)[0];version=order.version
    real=stock_posting.post_in_tx
    def fail_after(*args,**kwargs):
        real(*args,**kwargs)
        raise BusinessError('TEST_FAILURE','Lỗi thử sau ghi sổ.')
    monkeypatch.setattr(stock_posting,'post_in_tx',fail_after)
    fail('TEST_FAILURE',returned,db,p,order,receipt)
    assert db.query(PurchaseReturn).count()==0 and db.query(StockDoc).filter_by(kind='PX').count()==0
    assert order.version==version and db.get(StockBalance,('K58','TD-0001')).qty==Decimal('1.5')


def test_api_return_list_and_void(client,db,purchase_people,login):
    p=purchase_people;order=placed(db,p);receipt=receive(db,p,order)[0];headers=login('m4.api','warehouse')
    response=client.post(f'/purchase-receipts/{receipt.id}/returns',headers=headers,json=dict(version=order.version,
        return_date=str(DAY),reason='Hàng lỗi',lines=[dict(receipt_line_id=receipt.lines[0].id,qty=1)]))
    assert response.status_code==201,response.text
    data=response.json();assert data['number']=='TH-26-00001'
    assert client.get(f'/purchase-orders/{order.id}/returns',headers=headers).json()[0]['id']==data['id']
    response=client.post(f'/purchase-returns/{data["id"]}/void',headers=headers,json=dict(version=data['version'],reason='Giữ lại hàng'))
    assert response.status_code==200 and response.json()['status']=='DA_HUY'

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import purchase_orders as orders, purchase_receipts as svc, stock_docs, stock_posting
from app.errors import BusinessError
from app.models import AuditLog, FundLedger, PurchaseReceipt, PurchaseReceiptLine, StockBalance, StockDoc, StockLedger, Warehouse
from app.schemas import StockDocIn
from test_purchase_orders import purchase_people, body, DAY, fail


@pytest.fixture(autouse=True)
def no_m2_fund_writes(db):
    yield
    db.rollback()
    assert db.scalar(select(func.count()).select_from(FundLedger))==0


def placed(db,p,**changes):
    row=orders.create(db,p['warehouse'],body(**changes))
    return orders.order(db,p['warehouse'],row.id,row.version)


def receive(db,p,row,qty=None,line=None):
    return svc.receive(db,p['warehouse'],row.id,row.version,DAY,
        [dict(order_line_id=(line or row.lines[0]).id,qty=qty or (line or row.lines[0]).qty)])


def test_full_receive_posts_pn_cost_quantity_state_and_audits(db,purchase_people):
    p=purchase_people;row=placed(db,p);version=row.version
    r=receive(db,p,row)[0]
    doc=db.get(StockDoc,r.stock_doc_id);bal=db.get(StockBalance,('K58','TD-0001'))
    assert (doc.kind,doc.status,doc.supplier_code,doc.reason)==('PN','DA_GHI','NCC-0001','Nhận hàng '+row.number)
    assert (bal.qty,bal.value,bal.avg_cost)==(Decimal('1.5'),150000,100000)
    assert (r.amount,r.vat_amount,r.total)==(150000,12000,162000)
    assert r.lines[0].unit_cost==100000 and r.lines[0].vat_rate==8
    assert row.status=='DA_NHAN' and row.lines[0].received_qty==row.lines[0].qty and row.version==version+1
    assert svc.payable_by_order(db,p['finance'],row.id)==162000
    assert svc.list_by_order(db,p['finance'],row.id)[0].id==r.id
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='purchase_receipt.receive'))==1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='purchase_order.receive'))==1
    fail('SOURCE_LOCKED',stock_posting.void,db,p['warehouse'],doc.id,'Hủy trực tiếp PN',doc.version)


def test_two_warehouses_produce_two_receipts_and_pns(db,purchase_people):
    p=purchase_people;row=placed(db,p,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=2,unit_cost=100000),
        dict(product_code='TD-0002',warehouse_code='K145',qty=3,unit_cost=200000)])
    receipts=svc.receive(db,p['warehouse'],row.id,row.version,DAY,[dict(order_line_id=l.id,qty=l.qty) for l in row.lines])
    assert {r.warehouse_code for r in receipts}=={'K58','K145'} and len({r.stock_doc_id for r in receipts})==2
    assert all(db.get(StockDoc,r.stock_doc_id).status=='DA_GHI' for r in receipts)
    assert db.get(StockBalance,('K58','TD-0001')).value==200000
    assert db.get(StockBalance,('K145','TD-0002')).value==600000
    assert row.status=='DA_NHAN'
    assert svc.payable_by_order(db,p['finance'],row.id)==row.total


def test_three_fractional_receives_final_residual_matches_order_and_stock(db,purchase_people):
    p=purchase_people;row=placed(db,p,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=3,unit_cost=1001)])
    results=[]
    for qty in ('0.5','0.7','1.8'):results.extend(receive(db,p,row,Decimal(qty)))
    assert [r.amount for r in results]==[501,701,1801]
    assert sum(r.amount for r in results)==row.subtotal==3003
    assert sum(r.vat_amount for r in results)==row.vat_total==240
    assert svc.payable_by_order(db,p['finance'],row.id)==row.total==3243
    bal=db.get(StockBalance,('K58','TD-0001'))
    assert (bal.qty,bal.value,bal.avg_cost)==(3,3003,1001)
    assert db.get(StockDoc,results[-1].stock_doc_id).lines[0].unit_cost==1001
    # Hủy lần cuối rồi nhận lại vẫn bù đúng, chỉ cộng DA_GHI.
    svc.void(db,p['warehouse'],results[-1].id,'Nhận lại',results[-1].version)
    again=receive(db,p,row,Decimal('1.8'))[0]
    assert again.amount==1801 and svc.payable_by_order(db,p['finance'],row.id)==3243


def test_validation_status_over_receive_foreign_duplicate_and_quantity(db,purchase_people):
    p=purchase_people;u=p['warehouse'];row=orders.create(db,u,body());other=orders.create(db,u,body())
    fail('BAD_STATUS',svc.receive,db,u,row.id,row.version,DAY,[dict(order_line_id=row.lines[0].id,qty=1)])
    orders.cancel(db,u,row.id,'Bỏ đơn',row.version)
    fail('BAD_STATUS',svc.receive,db,u,row.id,row.version,DAY,[dict(order_line_id=row.lines[0].id,qty=1)])
    orders.order(db,u,other.id,other.version)
    fail('BAD_ORDER_LINE',svc.receive,db,u,other.id,other.version,DAY,[dict(order_line_id=row.lines[0].id,qty=1)])
    fail('OVER_RECEIVE',svc.receive,db,u,other.id,other.version,DAY,[dict(order_line_id=other.lines[0].id,qty=2)])
    for qty in (0,-1,'0.0001','NaN'):
        fail('BAD_QTY',svc.receive,db,u,other.id,other.version,DAY,[dict(order_line_id=other.lines[0].id,qty=qty)])
    fail('DUPLICATE_LINE',svc.receive,db,u,other.id,other.version,DAY,[dict(order_line_id=other.lines[0].id,qty=1)]*2)
    fail('EMPTY_RECEIPT',svc.receive,db,u,other.id,other.version,DAY,[])
    assert db.query(StockDoc).count()==0 and db.query(PurchaseReceipt).count()==0


def test_void_restores_progress_then_close_and_void_reopens(db,purchase_people):
    p=purchase_people;row=placed(db,p);r=receive(db,p,row,Decimal('0.5'))[0]
    assert row.status=='NHAN_MOT_PHAN'
    fail('CANNOT_CANCEL',orders.cancel,db,p['warehouse'],row.id,'Hủy đơn',row.version)
    fail('REASON_REQUIRED',svc.close,db,p['warehouse'],row.id,' ',row.version)
    svc.close(db,p['warehouse'],row.id,'NCC không giao tiếp',row.version)
    assert row.status=='DA_NHAN' and row.close_reason=='NCC không giao tiếp'
    fail('BAD_STATUS',svc.receive,db,p['warehouse'],row.id,row.version,DAY,[dict(order_line_id=row.lines[0].id,qty=1)])
    fail('REASON_REQUIRED',svc.void,db,p['warehouse'],r.id,' ',r.version)
    svc.void(db,p['warehouse'],r.id,'Trả lại hàng',r.version)
    assert db.get(StockDoc,r.stock_doc_id).status=='DA_HUY' and r.status=='DA_HUY'
    assert row.status=='DA_DAT' and row.close_reason=='' and row.lines[0].received_qty==0
    assert db.get(StockBalance,('K58','TD-0001')).qty==0
    assert svc.payable_by_order(db,p['finance'],row.id)==0
    assert db.scalar(select(AuditLog).where(AuditLog.action=='purchase_order.reopen_after_void')).before['close_reason']=='NCC không giao tiếp'
    assert svc.receive(db,p['warehouse'],row.id,row.version,DAY,[dict(order_line_id=row.lines[0].id,qty=1)])[0].status=='DA_GHI'


def test_void_after_stock_used_fails_atomically(db,purchase_people):
    p=purchase_people;row=placed(db,p);r=receive(db,p,row)[0]
    out=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PX',warehouse_code='K58',doc_date=DAY,lines=[dict(product_code='TD-0001',qty='1.5')]))
    stock_posting.post(db,p['warehouse'],out.id,out.version)
    audit_count=db.query(AuditLog).count();version=row.version;rv=r.version
    fail('NEGATIVE_STOCK',svc.void,db,p['warehouse'],r.id,'Trả hàng',rv)
    assert r.status=='DA_GHI' and r.version==rv and row.status=='DA_NHAN' and row.version==version
    assert row.lines[0].received_qty==Decimal('1.5') and db.get(StockDoc,r.stock_doc_id).status=='DA_GHI'
    assert db.query(AuditLog).count()==audit_count and db.query(StockLedger).count()==2


def test_second_warehouse_failure_rolls_back_docs_numbers_balances_and_audits(db,purchase_people):
    p=purchase_people;row=placed(db,p,lines=[dict(product_code='TD-0001',warehouse_code='K145',qty=1,unit_cost=100000),
        dict(product_code='TD-0002',warehouse_code='K58',qty=1,unit_cost=200000)])
    db.get(Warehouse,'K58').active=False;db.commit();n=db.query(AuditLog).count()
    parts=[dict(order_line_id=l.id,qty=1) for l in row.lines]
    fail('BAD_WAREHOUSE',svc.receive,db,p['warehouse'],row.id,row.version,DAY,parts)
    assert db.query(StockDoc).count()==db.query(StockBalance).count()==db.query(PurchaseReceipt).count()==0
    assert row.status=='DA_DAT' and all(l.received_qty==0 for l in row.lines)
    assert db.query(AuditLog).count()==n
    db.get(Warehouse,'K58').active=True;db.commit()
    rs=svc.receive(db,p['warehouse'],row.id,row.version,DAY,parts)
    assert sorted(db.get(StockDoc,r.stock_doc_id).number for r in rs)==['PN-26-00001','PN-26-00002']


def test_failure_after_actual_post_rolls_back_everything(db,purchase_people,monkeypatch):
    p=purchase_people;row=placed(db,p);real=stock_posting.post_in_tx
    def fail_after(*args,**kwargs):
        real(*args,**kwargs)
        raise BusinessError('TEST_FAILURE','Thử lỗi sau ghi sổ.')
    monkeypatch.setattr(stock_posting,'post_in_tx',fail_after)
    fail('TEST_FAILURE',svc.receive,db,p['warehouse'],row.id,row.version,DAY,[dict(order_line_id=row.lines[0].id,qty=1)])
    assert db.query(StockDoc).count()==db.query(StockLedger).count()==db.query(StockBalance).count()==db.query(PurchaseReceipt).count()==0
    assert row.status=='DA_DAT' and row.lines[0].received_qty==0


def test_roles_stale_versions_and_repeated_void(db,purchase_people):
    p=purchase_people;row=placed(db,p);version=row.version;r=receive(db,p,row,Decimal('0.5'))[0]
    fail('VERSION_CONFLICT',svc.receive,db,p['warehouse'],row.id,version,DAY,[dict(order_line_id=row.lines[0].id,qty=1)])
    fail('VERSION_CONFLICT',svc.close,db,p['warehouse'],row.id,'Đóng đơn',version)
    fail('VERSION_CONFLICT',svc.void,db,p['warehouse'],r.id,'Hủy nhận',r.version+1)
    for role in ('sales','technician','finance'):
        fail('FORBIDDEN',svc.receive,db,p[role],row.id,row.version,DAY,[dict(order_line_id=row.lines[0].id,qty=1)])
        fail('FORBIDDEN',svc.void,db,p[role],r.id,'Hủy nhận',r.version)
        fail('FORBIDDEN',svc.close,db,p[role],row.id,'Đóng đơn',row.version)
    svc.void(db,p['director'],r.id,'Giám đốc hủy',r.version)
    fail('BAD_STATUS',svc.void,db,p['warehouse'],r.id,'Hủy lần nữa',r.version)


def test_concurrent_receives_same_version_one_winner(db,engine,purchase_people):
    p=purchase_people;row=placed(db,p);ident,version,line_id=row.id,row.version,row.lines[0].id;barrier=Barrier(2)
    def worker(role):
        with Session(engine) as s:
            user=s.get(type(p[role]),p[role].id);barrier.wait()
            try:svc.receive(s,user,ident,version,DAY,[dict(order_line_id=line_id,qty=1)]);return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,['warehouse','director']))
    assert sorted(results)==['OK','VERSION_CONFLICT']
    db.expire_all();assert orders.load(db,p['warehouse'],ident).lines[0].received_qty==1
    assert db.query(PurchaseReceipt).count()==1 and db.query(StockDoc).count()==1


def test_api_receive_list_close_and_void(client,db,purchase_people,login):
    p=purchase_people;row=placed(db,p);headers=login('m2.api','warehouse')
    url=f'/purchase-orders/{row.id}'
    r=client.post(url+'/receive',headers=headers,json={'version':row.version,'receipt_date':str(DAY),'lines':[dict(order_line_id=row.lines[0].id,qty='0.5')]})
    assert r.status_code==201,r.text
    data=r.json()[0];db.expire_all();assert client.get(url+'/receipts',headers=headers).json()[0]['id']==data['id']
    r=client.post(url+'/close',headers=headers,json={'version':row.version,'reason':'NCC hết hàng'})
    assert r.status_code==200 and r.json()['close_reason']=='NCC hết hàng'
    r=client.post(f'/purchase-receipts/{data["id"]}/void',headers=headers,json={'version':data['version'],'reason':'Trả hàng'})
    assert r.status_code==200 and r.json()['status']=='DA_HUY'
    assert client.get(url,headers=headers).json()['status']=='DA_DAT'


def test_rounding_adjusts_vat_on_last_receive_and_ignores_voided_amounts(db,purchase_people):
    p=purchase_people;row=placed(db,p,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=3,unit_cost=17)])
    results=[receive(db,p,row,Decimal(1))[0] for _ in range(3)]
    assert [r.vat_amount for r in results]==[1,1,2]
    assert sum(r.vat_amount for r in results)==row.vat_total==4
    assert svc.payable_by_order(db,p['finance'],row.id)==55


def test_stock_posting_amount_override_rejects_invalid_amount_without_posting(db,purchase_people):
    p=purchase_people
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code='K58',supplier_code='NCC-0001',doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty=1,unit_cost=1001)]))
    for amounts in ({1:-1},{2:1001},{1:True}):
        with pytest.raises(BusinessError) as exc:
            with orders._transaction(db):stock_posting.post_in_tx(db,p['warehouse'],doc.id,doc.version,pn_amounts=amounts)
        assert exc.value.code=='BAD_RECEIPT_AMOUNT'
    assert doc.status=='NHAP' and db.query(StockLedger).count()==0

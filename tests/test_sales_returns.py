"""S5: trả hàng, vốn PX gốc, hoàn tiền và báo cáo trên PostgreSQL thật."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import (cash_docs, cash_posting, finance_catalogs, finance_mappings, finance_reports, inventory_catalogs,
    my_work, purchase_reports, sales_orders, sales_receipts, sales_reports, sales_returns as svc, sales_refunds as refunds, stock_docs, stock_posting)
from app.models import (AuditLog, CashCategory, CashDoc, FinanceMapping, FundLedger, SalesRefund, SalesReturn, StockBalance, StockDoc, StockLedger, Warehouse)
from app.schemas import (CashCategoryIn, SalesOrderIn, SalesReceiptReport, SalesReturnIn, SalesRefundIn, StockDocIn, WarehouseIn)
from test_stock_reservations import people, purchase_people, stock, fail
from test_my_work import DAY
from test_technician_deliveries import assigned, move, deliver, setup as tech_setup


@pytest.fixture
def sold(db,people):
    p=people;stock(db,p,10)
    o=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=10,unit_price=200000,vat_rate=0)]))
    sales_orders.confirm(db,p['sales'],o.id,o.version)
    sales_orders.deliver(db,p['warehouse'],o.id,o.version)
    return p,o,sales_orders.deliveries(db,o.id)[0]


def receive(db,p,o,gh,qty=2,condition='TOT',warehouse=None,**kw):
    return svc.create(db,p['sales'],o.id,SalesReturnIn(version=o.version,reason='Khách trả lại',lines=[dict(delivery_id=gh.id,order_line_no=1,qty=qty,condition=condition,warehouse_code=warehouse)],**kw))


def pay(db,p,o,amount):
    row=sales_receipts.report(db,p['sales'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=amount))
    return sales_receipts.confirm(db,p['finance'],row.id,row.version)


def request(db,p,o,amount,who='sales'):
    return refunds.request(db,p[who],o.id,SalesRefundIn(version=o.version,method='TIEN_MAT',amount=amount,reason='Hoàn khách trả hàng'))


def mapping(db,p):
    cat=finance_catalogs.create(db,p['finance'],CashCategory,CashCategoryIn(code='HOAN-BH',name='Hoàn tiền bán',direction='CHI'))
    finance_mappings.set_mapping(db,p['director'],'DANH_MUC_HOAN_TIEN_BAN',category_code=cat.code,version=1)


def counts(db):
    return tuple(db.query(model).count() for model in (SalesReturn,SalesRefund,StockDoc,StockLedger,CashDoc,FundLedger,AuditLog))


def test_01_good_return_cost_receivable_status_and_source_lock(db,sold):
    p,o,gh=sold;r=receive(db,p,o,gh)
    assert r.total==400000 and o.receivable==1600000 and o.status=='DA_GIAO'
    assert db.get(StockBalance,('K58','TD-0001')).qty==2
    pn=stock_docs.load(db,r.lines[0].stock_doc_id)
    assert pn.kind=='PN' and pn.status=='DA_GHI' and pn.customer_code==o.customer_code and pn.supplier_code is None
    assert pn.reason==f'Trả hàng {r.number} của {o.number}' and r.lines[0].unit_cost==100
    assert db.scalar(select(func.sum(StockLedger.amount)).where(StockLedger.doc_id==pn.id))==200
    fail('SOURCE_LOCKED',stock_posting.void,db,p['director'],pn.id,'Không hủy riêng',pn.version)


def test_02_defect_warehouse_not_sale_service_or_technician_transfer(db,sold):
    from app import service_charges
    from app.schemas import ServiceCharges
    from test_my_work import ticket
    p,o,gh=sold
    fail('NO_DEFECT_WAREHOUSE',receive,db,p,o,gh,condition='LOI')
    inventory_catalogs.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K900',name='Kho hàng lỗi',kind='LOI'))
    r=receive(db,p,o,gh,condition='LOI')
    assert db.get(StockBalance,('K900','TD-0001')).qty==2 and db.get(StockBalance,('K58','TD-0001')).qty==0
    fail('BAD_WAREHOUSE',sales_orders.create,db,p['sales'],SalesOrderIn(customer_code=o.customer_code,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K900',qty=1,unit_price=1)]))
    t=ticket(db,p,'DANG_LAM')
    fail('BAD_WAREHOUSE',service_charges.set_charges,db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[dict(kind='VAT_TU',product_code='TD-0001',warehouse_code='K900',qty=1,unit_price=1)]))
    inventory_catalogs.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K501',name='Kho thợ',kind='THO',holder_id=p['technician'].id))
    fail('BAD_WAREHOUSE',stock_docs.create,db,p['warehouse'],StockDocIn(kind='CK',warehouse_code='K900',to_warehouse_code='K501',doc_date=DAY,lines=[dict(product_code='TD-0001',qty=1)]))


def test_03_over_return_no_writes_and_void_gh_rejected(db,sold):
    p,o,gh=sold;receive(db,p,o,gh,8);old=counts(db)
    fail('OVER_RETURN',receive,db,p,o,gh,3)
    assert counts(db)==old and o.receivable==400000
    fail('HAS_RETURNS',sales_orders.void_delivery,db,p['director'],gh.id,'Nhầm giao',gh.version)


def test_03_return_voided_delivery(db,sold):
    p,o,gh=sold;sales_orders.void_delivery(db,p['director'],gh.id,'Nhầm giao',gh.version)
    # Có một GH mới để đơn ở trạng thái đã giao, GH cũ vẫn không được trả.
    sales_orders.deliver(db,p['warehouse'],o.id,o.version)
    fail('OVER_RETURN',receive,db,p,o,gh)


def test_04_rounding_reconciles_vat_and_cost(db,people):
    p=people;stock(db,p,3)
    o=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=3,unit_price=33333,vat_rate=8)]))
    sales_orders.confirm(db,p['sales'],o.id,o.version);sales_orders.deliver(db,p['warehouse'],o.id,o.version)
    gh=sales_orders.deliveries(db,o.id)[0]
    a=receive(db,p,o,gh,1);b=receive(db,p,o,gh,2)
    assert a.total+b.total==gh.total and a.vat_total+b.vat_total==8000 and o.receivable==0
    assert sum(l.cost_amount for r in (a,b) for l in r.lines)==300
    assert sales_orders.effective_total(o)==0


def test_05_refund_net_paid_all_readers(db,sold,configured_finance):
    p,o,gh=sold;pay(db,p,o,2000000);receive(db,p,o,gh)
    assert sales_receipts.balance(o)['advance']==400000
    fail('OVER_REFUND',request,db,p,o,400001)
    r=request(db,p,o,400000);fail('OVER_REFUND',request,db,p,o,1)
    mapping(db,p);refunds.decide(db,p['finance'],r.id,r.version,True)
    pc=cash_docs.load(db,p['finance'],r.cash_doc_id)
    assert pc.kind=='PC' and pc.status=='DA_GHI' and pc.amount==400000
    assert db.scalar(select(func.sum(FundLedger.amount)).where(FundLedger.doc_id==pc.id))==-400000
    balance=sales_receipts.balance(o)
    assert balance['paid']==1600000 and balance['refunded']==400000 and balance['advance']==0 and balance['remaining']==0
    assert sales_orders.summaries(db,[o])[o.id]['paid']==1600000
    assert finance_reports.debt_totals(db,p['finance'])['remaining']==0
    assert not sales_receipts.receivables(db,p['finance'])
    assert all(not g['rows'] or all(row['id']!=o.id for row in g['rows']) for g in my_work.collect(db,p['sales']) if g['key']=='orders_unreported')
    fail('SOURCE_LOCKED',cash_posting.void,db,p['director'],pc.id,'Không hủy PC riêng',pc.version)
    returned=db.scalar(select(SalesReturn))
    fail('REFUND_PAID',svc.void,db,p['director'],returned.id,returned.version,'Hủy trả hàng')


def test_06_self_confirm_missing_mapping_and_reject_reason(db,sold,configured_finance):
    p,o,gh=sold;pay(db,p,o,2000000);receive(db,p,o,gh);r=request(db,p,o,400000)
    fail('SELF_CONFIRM',refunds.decide,db,p['sales'],r.id,r.version,True)
    old=counts(db);fail('MAPPING_REQUIRED',refunds.decide,db,p['finance'],r.id,r.version,True)
    assert counts(db)==old and r.status=='CHO_DUYET'
    fail('REASON_REQUIRED',refunds.decide,db,p['finance'],r.id,r.version,False)
    refunds.decide(db,p['finance'],r.id,r.version,False,'Khách chưa cần hoàn')
    assert r.status=='TU_CHOI' and sales_receipts.balance(o)['advance']==400000


def test_07_reports_return_day_negative_revenue_and_cost(db,sold,monkeypatch):
    p,o,gh=sold
    gh.delivered_at=datetime(2026,10,1,4,tzinfo=UTC);db.commit()
    monkeypatch.setattr(svc,'_now',lambda:datetime(2026,10,3,4,tzinfo=UTC))
    r=receive(db,p,o,gh)
    day=DAY.replace(day=3)
    assert sales_reports.daily(db,p['finance'],day,day)[0]['subtotal']==-400000
    margin=purchase_reports.gross_margin(db,p['finance'],day,day)
    assert margin['totals']['revenue']==-400000 and margin['totals']['cost']==-200
    assert margin['products'][0]['cost']==-200 and margin['documents'][0]['kind']=='TH'
    assert sales_reports.by_customer(db,p['finance'],day,day)[0]['total']==-400000


def test_08_cancel_confirmed_deposit_auto_refund_and_pending_block(db,people,configured_finance):
    p=people
    o=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='KHAC',description='Đơn đặt trước',qty=1,unit_price=1000000,vat_rate=0)]))
    sales_orders.confirm(db,p['sales'],o.id,o.version);pay(db,p,o,500000)
    pending=sales_receipts.report(db,p['sales'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=1))
    fail('HAS_RECEIPTS',sales_orders.cancel,db,p['sales'],o.id,'Khách hủy',o.version)
    sales_receipts.reject(db,p['finance'],pending.id,'Chưa nhận',pending.version)
    sales_orders.cancel(db,p['sales'],o.id,'Khách hủy',o.version)
    r=db.scalar(select(SalesRefund).where(SalesRefund.order_id==o.id))
    assert o.status=='DA_HUY' and r.amount==500000 and r.status=='CHO_DUYET'
    mapping(db,p);refunds.decide(db,p['finance'],r.id,r.version,True)
    assert sales_receipts.balance(o)['advance']==0


def test_09_void_delivery_same_vietnamese_day_only(db,sold):
    p,o,gh=sold
    gh.delivered_at=gh.delivered_at-timedelta(days=1);db.commit()
    fail('USE_RETURN',sales_orders.void_delivery,db,p['director'],gh.id,'Nhầm giao',gh.version)
    gh.delivered_at=gh.delivered_at+timedelta(days=1);db.commit()
    sales_orders.void_delivery(db,p['director'],gh.id,'Nhầm giao',gh.version)
    assert gh.status=='DA_HUY'


def test_10_void_return_restores_receivable_and_stock(db,sold):
    p,o,gh=sold;r=receive(db,p,o,gh)
    svc.void(db,p['director'],r.id,r.version,'Khách lấy lại')
    assert o.receivable==2000000 and db.get(StockBalance,('K58','TD-0001')).qty==0 and r.status=='DA_HUY'


def test_10_void_after_returned_stock_sold_rolls_back(db,sold):
    p,o,gh=sold;r=receive(db,p,o,gh)
    stock(db,p,2,kind='PX');old=counts(db)
    fail('NEGATIVE_STOCK',svc.void,db,p['director'],r.id,r.version,'Không còn hàng')
    assert r.status=='DA_NHAN' and o.receivable==1600000 and counts(db)==old


def test_11_return_technician_delivery_to_original_warehouse(db,tech_setup):
    p=tech_setup;o=assigned(db,p);move(db,p,o,4);deliver(db,p,o,4)
    gh=sales_orders.deliveries(db,o.id)[0];assert gh.lines[0].warehouse_code=='K501'
    r=receive(db,p,o,gh)
    assert r.lines[0].warehouse_code=='K58' and db.get(StockBalance,('K501','TD-0001')).qty==0


def test_no_delete_and_permissions_version(db,sold):
    p,o,gh=sold
    fail('FORBIDDEN',svc.create,db,p['technician'],o.id,SalesReturnIn(version=o.version,reason='Trả',lines=[dict(delivery_id=gh.id,order_line_no=1,qty=1)]))
    r=receive(db,p,o,gh)
    fail('VERSION_CONFLICT',svc.void,db,p['director'],r.id,r.version+1,'Nhầm trả')
    with pytest.raises(DBAPIError):db.execute(text('DELETE FROM sales_returns'));db.commit()
    db.rollback();assert db.query(SalesReturn).count()==1


def test_api_web_forms_replay_refund_queue_and_print(db,sold,configured_finance,client):
    from app.security import issue_token
    from test_web import service_login
    from web_forms import Forms
    p,o,gh=sold;pay(db,p,o,2000000)
    auth={'Authorization':'Bearer '+issue_token(p['technician'])}
    denied=client.post(f'/sales-orders/{o.id}/returns',headers=auth,json=dict(version=o.version,reason='Khách trả',lines=[dict(delivery_id=gh.id,order_line_no=1,qty='2')]))
    assert denied.status_code==403
    service_login(client,p['sales'])
    page=client.get(f'/don-ban/{o.id}/tra-hang');assert page.status_code==200
    form=next(f for f in Forms(page.text,page.url).forms if f['path']==f'/don-ban/{o.id}/tra-hang')['fields']
    form.update({f'qty_{gh.id}_1':'2',f'condition_{gh.id}_1':'TOT',f'warehouse_{gh.id}_1':'K58','reason':'Khách trả hai hộp'})
    assert client.request('POST',f'/don-ban/{o.id}/tra-hang',data={'version':o.version,'reason':'Giả form'}).status_code==403
    assert client.post(f'/don-ban/{o.id}/tra-hang',data=form,follow_redirects=False).status_code==303
    assert client.post(f'/don-ban/{o.id}/tra-hang',data=form,follow_redirects=False).status_code==303
    assert db.query(SalesReturn).count()==1
    row=db.scalar(select(SalesReturn));assert 'Khách hàng' in client.get(f'/tra-hang/{row.id}/in').text
    page=client.get(f'/don-ban/{o.id}')
    assert page.status_code==200 and 'Có trả hàng' in page.text
    form=next(f for f in Forms(page.text,page.url).forms if f['path']==f'/don-ban/{o.id}/hoan-tien')['fields']
    form.update(amount='400.001',method='TIEN_MAT',reason='Giữ nguyên lý do vừa nhập')
    response=client.post(f'/don-ban/{o.id}/hoan-tien',data=form)
    assert response.status_code==409 and 'OVER_REFUND' in response.text and 'Giữ nguyên lý do vừa nhập' in response.text
    form=next(f for f in Forms(response.text,response.url).forms if f['path']==f'/don-ban/{o.id}/hoan-tien')['fields']
    form.update(amount='400.000',method='TIEN_MAT',reason='Hoàn hàng trả')
    assert client.post(f'/don-ban/{o.id}/hoan-tien',data=form,follow_redirects=False).status_code==303
    refund=db.scalar(select(SalesRefund))
    auth={'Authorization':'Bearer '+issue_token(p['sales'])}
    response=client.post(f'/sales-refunds/{refund.id}/approve',headers=auth,json={'version':refund.version})
    assert response.status_code==403 and response.json()['error']=='SELF_CONFIRM'
    service_login(client,p['finance']);page=client.get('/bao-thu?tab=hoan-tien')
    assert page.status_code==200 and refund.number in page.text
    action=f'/hoan-tien/{refund.id}/approve'
    form=next(f for f in Forms(page.text,page.url).forms if f['path']==action)['fields']
    response=client.post(action,data=form)
    assert response.status_code==409 and 'MAPPING_REQUIRED' in response.text
    mapping(db,p)
    page=client.get('/bao-thu?tab=hoan-tien');form=next(f for f in Forms(page.text,page.url).forms if f['path']==action)['fields']
    assert client.post(action,data=form,follow_redirects=False).status_code==303
    assert client.post(action,data=form,follow_redirects=False).status_code==303
    db.refresh(refund);assert refund.status=='DA_CHI'
    assert db.query(CashDoc).filter_by(source_type='SALES_REFUND').count()==1
    auth={'Authorization':'Bearer '+issue_token(p['sales'])}
    assert client.get(f'/sales-orders/{o.id}/receipts',headers=auth).json()['balance']['refunded']==400000


def test_cost_remainder_split_condition_and_duplicate_product_lines(db,people):
    p=people;stock(db,p,2);stock(db,p,1,lines=[dict(product_code='TD-0001',qty=1,unit_cost=101)])
    inventory_catalogs.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K900',name='Lỗi',kind='LOI'))
    o=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=3,unit_price=33333,vat_rate=8)]))
    sales_orders.confirm(db,p['sales'],o.id,o.version);sales_orders.deliver(db,p['warehouse'],o.id,o.version);gh=sales_orders.deliveries(db,o.id)[0]
    r=svc.create(db,p['warehouse'],o.id,SalesReturnIn(version=o.version,reason='Một tốt, một lỗi',lines=[dict(delivery_id=gh.id,order_line_no=1,qty=1,condition=c) for c in ('TOT','LOI')]))
    final=receive(db,p,o,gh,1)
    assert sum(l.cost_amount for row in (r,final) for l in row.lines)==301
    assert r.total+final.total==gh.total and o.receivable==0
    assert db.get(StockBalance,('K58','TD-0001')).value+db.get(StockBalance,('K900','TD-0001')).value==301


def test_closed_day_blocks_refund_approval_without_cash_writes(db,sold,configured_finance):
    from app import day_closings
    p,o,gh=sold;pay(db,p,o,2000000);receive(db,p,o,gh);r=request(db,p,o,400000);mapping(db,p)
    day_closings.close(db,p['finance'],cash_docs.today(),0)
    before=counts(db)
    fail('DAY_CLOSED',refunds.decide,db,p['finance'],r.id,r.version,True)
    assert counts(db)==before and r.status=='CHO_DUYET'


def test_two_refund_approvals_serialize_and_do_not_double_pay(db,engine,sold,configured_finance):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from app.models import User
    p,o,gh=sold;pay(db,p,o,2000000);receive(db,p,o,gh);r=request(db,p,o,400000);mapping(db,p)
    rid,version,uid=r.id,r.version,p['finance'].id;barrier=Barrier(2)
    def run(_):
        with Session(engine,expire_on_commit=False) as session:
            user=session.get(User,uid);barrier.wait(timeout=10)
            try:
                refunds.decide(session,user,rid,version,True)
                return 'OK'
            except Exception as exc:return getattr(exc,'code',str(exc))
    db.rollback()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert sorted(results)==['OK','VERSION_CONFLICT']
    assert db.query(CashDoc).filter_by(source_type='SALES_REFUND').count()==1


def test_pending_refund_rechecked_after_return_void_and_no_delete(db,sold,configured_finance):
    p,o,gh=sold;pay(db,p,o,2000000);r=receive(db,p,o,gh);refund=request(db,p,o,400000)
    svc.void(db,p['director'],r.id,r.version,'Khách lấy lại hàng')
    fail('OVER_REFUND',refunds.decide,db,p['finance'],refund.id,refund.version,True)
    with pytest.raises(DBAPIError):db.execute(text('DELETE FROM sales_refunds'));db.commit()
    db.rollback();assert db.query(SalesRefund).count()==1


def test_over_return_is_bounded_by_each_delivery(db,people):
    p=people;stock(db,p,10)
    o=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=10,unit_price=200000,vat_rate=0)]))
    sales_orders.confirm(db,p['sales'],o.id,o.version)
    sales_orders.deliver(db,p['warehouse'],o.id,o.version,[dict(order_line_no=1,qty=4)])
    sales_orders.deliver(db,p['warehouse'],o.id,o.version,[dict(order_line_no=1,qty=6)])
    gh=sales_orders.deliveries(db,o.id)[0]
    fail('OVER_RETURN',receive,db,p,o,gh,5)
    receive(db,p,o,gh,4)
    fail('OVER_RETURN',receive,db,p,o,gh,1)
    assert o.receivable==1200000


def test_cost_uses_the_correct_px_line_when_product_repeats(db,people):
    p=people;stock(db,p,2);stock(db,p,1,lines=[dict(product_code='TD-0001',qty=1,unit_cost=101)])
    o=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=q,unit_price=200000,vat_rate=0) for q in (1,2)]))
    sales_orders.confirm(db,p['sales'],o.id,o.version);sales_orders.deliver(db,p['warehouse'],o.id,o.version)
    gh=sales_orders.deliveries(db,o.id)[0]
    row=svc.create(db,p['sales'],o.id,SalesReturnIn(version=o.version,reason='Trả dòng thứ hai',lines=[dict(delivery_id=gh.id,order_line_no=2,qty=2)]))
    assert row.lines[0].cost_amount==201 and row.lines[0].unit_cost==101
    assert db.get(StockBalance,('K58','TD-0001')).value==201

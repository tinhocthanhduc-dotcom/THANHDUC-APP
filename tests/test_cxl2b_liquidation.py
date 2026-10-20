"""CXL2b: CI chạy PostgreSQL thật; không nới kiểm thử tiền/kho/quyền/rollback cũ."""
from datetime import UTC,datetime,timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func,select

from app import (pending_dispositions as svc,pending_items,pending_liquidation as liq,sales_orders,sales_returns,
    sales_reports,stock_docs,stock_posting,stock_reservations,supplier_debts,finance_reports,technician_deliveries,my_work)
from app.models import (CashDoc,DayClosing,FundLedger,PendingDisposition,PendingItem,PendingItemMove,SalesDelivery,StockBalance,StockDoc,StockLedger,StockReservation,Warehouse)
from app.schemas import SalesOrderIn,SalesOrderOptions,SalesDeliveryAssign,SalesReturnIn,StockDocIn
from test_cxl2a_dispositions import lot,people,purchase_people,counts,make
from test_cxl1_pending_items import assert_pending_invariant,error
from test_web import service_login


@pytest.fixture
def liquidation(db,lot):
    p,item=lot
    first=pending_items.split(db,p['warehouse'],item.id,3,'THANH_LY',item.version)
    second=pending_items.split(db,p['warehouse'],item.id,4,'THANH_LY',item.version)
    first.entered_at=datetime.now(UTC)-timedelta(days=20);second.entered_at=datetime.now(UTC)-timedelta(days=10);db.commit()
    return p,item,first,second


def body(qty=5,price=200,fulfillment='TAI_QUAY',warehouse='CXL'):
    return SalesOrderIn(customer_code='KTD-000001',fulfillment=fulfillment,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code=warehouse,qty=qty,unit_price=price,vat_rate=8)])


def order(db,p,qty=5,price=200,fulfillment='TAI_QUAY',confirm=True):
    row=sales_orders.create(db,p['sales'],body(qty,price,fulfillment))
    if confirm:sales_orders.confirm(db,p['sales'],row.id,row.version)
    return row


def request(db,p,item,qty=3,amount=250,rate=8):
    return svc.create(db,p['warehouse'],item.id,'TRA_NCC',qty,item.version,supplier_code='NCC-0001',amount=amount,vat_rate=rate,reason='NCC nhận hàng lỗi giảm nợ')


@pytest.mark.parametrize('rate,vat',[(0,0),(5,13),(8,20),(10,25)])
def test_supplier_credit_only_after_finance_approval_vat_and_difference(db,lot,rate,vat):
    p,item=lot;before=counts(db);balance=supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']
    row=request(db,p,item,rate=rate)
    assert row.status=='CHO_DUYET' and row.stock_doc_id is None and item.qty_left==10
    assert counts(db)[:2]==before[:2] and supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==balance
    for role in ('warehouse','sales','technician'):assert error('FORBIDDEN',svc.approve,db,p[role],row.id,row.version).status==403
    svc.approve(db,p['finance'],row.id,row.version)
    assert row.status=='DA_GHI' and db.get(StockDoc,row.stock_doc_id).kind=='PX' and row.cost_value==300
    assert (row.amount,row.vat_amount,row.total)==(250,vat,250+vat) and item.qty_left==7
    assert supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==balance-(250+vat)
    assert supplier_debts.timeline(db,p['finance'],'NCC-0001')[-1]['balance']==balance-(250+vat)
    assert finance_reports.pending_return_difference(db,p['finance'])==-50
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==0;assert_pending_invariant(db)


def test_credit_void_restores_debt_exact_item_and_closed_day_blocks(db,lot):
    p,item=lot;row=request(db,p,item);svc.approve(db,p['finance'],row.id,row.version)
    day=row.decided_at.astimezone(svc.TZ).date();db.add(DayClosing(day=day,status='DA_CHOT',closed_by=p['finance'].id,closed_at=datetime.now(UTC)));db.commit();before=counts(db)
    error('DAY_CLOSED',svc.void,db,p['finance'],row.id,row.version,'Sai hồ sơ');assert row.status=='DA_GHI' and counts(db)==before and item.qty_left==7
    db.delete(db.get(DayClosing,day));db.commit();svc.void(db,p['finance'],row.id,row.version,'Sai hồ sơ')
    assert row.status=='DA_HUY' and item.qty_left==10 and db.get(StockBalance,('CXL','TD-0001')).qty==10
    assert supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==0 and supplier_debts.timeline(db,p['finance'],'NCC-0001')[-1]['balance']==0
    assert finance_reports.pending_return_difference(db,p['finance'])==0;assert_pending_invariant(db)


def test_credit_reject_requires_reason_and_creates_no_stock_or_debt(db,lot):
    p,item=lot;row=request(db,p,item);before=counts(db)
    error('REASON_REQUIRED',svc.reject,db,p['finance'],row.id,row.version,' ')
    svc.reject(db,p['finance'],row.id,row.version,'NCC chưa đồng ý');assert row.status=='TU_CHOI' and item.qty_left==10
    assert counts(db)==before and row.stock_doc_id is None and supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==0
    assert_pending_invariant(db)


def test_liquidation_requires_matching_group_and_reserves_only_eligible_quantity(db,lot,liquidation):
    p,item,first,second=liquidation
    # Three other defective pieces share the same physical balance; only seven are saleable.
    assert db.get(StockBalance,('CXL','TD-0001')).qty==10 and stock_reservations.available(db,'CXL','TD-0001')==7
    o=order(db,p,5);assert stock_reservations.available(db,'CXL','TD-0001')==2
    competitor=order(db,p,3,confirm=False);before=counts(db)
    error('CXL_NOT_ENOUGH',sales_orders.confirm,db,p['sales'],competitor.id,competitor.version)
    assert competitor.status=='NHAP' and counts(db)==before and stock_reservations.available(db,'CXL','TD-0001')==2
    error('CXL_QTY_RESERVED',make,db,p,first,'SUA_XONG',3,to_warehouse_code='K58')
    error('CXL_QTY_RESERVED',pending_items.set_group,db,p['warehouse'],first.id,'HANG_LOI',first.version)
    assert_pending_invariant(db)


def test_no_liquidation_group_rejected_and_wrong_product_not_eligible(db,lot):
    p,item=lot;before=counts(db)
    error('BAD_WAREHOUSE',sales_orders.create,db,p['sales'],body());assert counts(db)==before
    assert liq.choices(db,['TD-0001'])=={};assert_pending_invariant(db)


def test_fifo_multiple_source_lots_one_px_per_disposition_and_normal_sales_money(db,liquidation):
    p,item,first,second=liquidation;o=order(db,p)
    sales_orders.deliver(db,p['warehouse'],o.id,o.version);gh=sales_orders.deliveries(db,o.id)[0];rows=liq.delivery_rows(db,gh.id)
    assert [(r.item_id,r.qty) for r in rows]==[(first.id,3),(second.id,2)]
    assert first.status=='XONG' and first.qty_left==0 and second.qty_left==2 and item.qty_left==3
    assert len({r.stock_doc_id for r in rows})==2 and sum(r.cost_value for r in rows)==500
    assert all(db.get(StockDoc,r.stock_doc_id).kind=='PX' and db.get(StockDoc,r.stock_doc_id).status=='DA_GHI' for r in rows)
    moves=list(db.scalars(select(PendingItemMove).where(PendingItemMove.disposition_id.in_([r.id for r in rows]))))
    assert [(m.item_id,m.qty) for m in moves]==[(first.id,3),(second.id,2)]
    assert (o.status,o.receivable,gh.subtotal,gh.vat_total,gh.total)==('DA_GIAO',1080,1000,80,1080)
    assert finance_reports.debt_totals(db,p['finance'])['remaining']==1080
    assert sales_reports.totals(db,p['finance'])['total']==1080 and sales_returns.original_cost(db,gh.lines[0])==(100,500)
    assert liq.sold_quantities(db,[first.id,second.id])=={first.id:3,second.id:2};assert_pending_invariant(db)


def test_below_average_needs_director_confirmation(db,liquidation):
    p,*_=liquidation;o=order(db,p,2,price=99,confirm=False);before=counts(db)
    exc=error('CAN_GIAM_DOC_DUYET_GIA',sales_orders.confirm,db,p['sales'],o.id,o.version)
    assert exc.message=='Giá thanh lý dưới giá vốn, cần giám đốc chốt đơn.' and o.status=='NHAP' and counts(db)==before
    sales_orders.confirm(db,p['director'],o.id,o.version);assert o.status=='DA_CHOT';assert_pending_invariant(db)


def test_void_delivery_restores_exact_fifo_items_and_money(db,liquidation):
    p,item,first,second=liquidation;o=order(db,p);sales_orders.deliver(db,p['warehouse'],o.id,o.version);gh=sales_orders.deliveries(db,o.id)[0]
    rows=liq.delivery_rows(db,gh.id);error('SOURCE_LOCKED',svc.void,db,p['director'],rows[0].id,rows[0].version,'Không hủy riêng')
    sales_orders.void_delivery(db,p['director'],gh.id,'Khách hẹn giao lại',gh.version)
    assert (first.qty_left,first.status,second.qty_left,item.qty_left,o.receivable)==(3,'DANG_CHO',4,3,0)
    assert all(r.status=='DA_HUY' for r in rows) and liq.sold_quantities(db,[first.id,second.id])=={}
    assert db.get(StockBalance,('CXL','TD-0001')).qty==10;assert_pending_invariant(db)


@pytest.mark.parametrize('condition,warehouse',[('LOI','CXL'),('TOT','K58')])
def test_liquidation_return_uses_original_cost_and_existing_s5_flow(db,liquidation,condition,warehouse):
    p,item,first,second=liquidation;o=order(db,p);sales_orders.deliver(db,p['warehouse'],o.id,o.version);gh=sales_orders.deliveries(db,o.id)[0]
    row=sales_returns.create(db,p['warehouse'],o.id,SalesReturnIn(version=o.version,reason='Khách trả lại',lines=[dict(delivery_id=gh.id,order_line_no=1,qty=2,condition=condition,warehouse_code=warehouse)]))
    assert row.lines[0].cost_amount==200 and row.total==432 and o.receivable==648
    if condition=='LOI':
        new=db.scalar(select(PendingItem).where(PendingItem.stock_doc_id==row.lines[0].stock_doc_id));assert (new.group_code,new.source,new.qty_left)==('HANG_LOI','KHACH_TRA',2)
    assert_pending_invariant(db)


@pytest.mark.parametrize('operation',['credit','delivery'])
def test_failure_after_stock_post_rolls_back_all_stock_debt_and_moves(db,liquidation,monkeypatch,operation):
    p,item,first,second=liquidation;o=order(db,p);row=request(db,p,item,qty=1) if operation=='credit' else None
    before=counts(db);qty=db.get(StockBalance,('CXL','TD-0001')).qty;apply=svc.apply_posted_in_tx
    def broken(*args,**kw):apply(*args,**kw);raise RuntimeError('Lỗi sau ghi kho')
    monkeypatch.setattr(svc,'apply_posted_in_tx',broken)
    with pytest.raises(RuntimeError):
        if row:svc.approve(db,p['finance'],row.id,row.version)
        else:sales_orders.deliver(db,p['warehouse'],o.id,o.version)
    assert counts(db)==before and db.get(StockBalance,('CXL','TD-0001')).qty==qty and o.receivable==0
    assert supplier_debts.balance(db,p['finance'],'NCC-0001')['remaining']==0
    if row:assert row.status=='CHO_DUYET' and row.stock_doc_id is None
    assert db.query(SalesDelivery).count()==0;assert_pending_invariant(db)


def test_liquidation_tho_giao_blocked_on_choice_confirm_and_assign(db,liquidation):
    p,*_=liquidation
    exc=error('THANH_LY_KHONG_GIAO_THO',sales_orders.create,db,p['sales'],body(1,fulfillment='THO_GIAO'))
    assert exc.message=='Hàng thanh lý giao tại kho hoặc kho giao, không giao qua thợ.'
    o=order(db,p,1,confirm=False);error('THANH_LY_KHONG_GIAO_THO',sales_orders.change_options,db,p['sales'],o.id,SalesOrderOptions(version=o.version,fulfillment='THO_GIAO'))
    # Legacy/raw data must also be rejected at the two later business boundaries.
    o.fulfillment='THO_GIAO';db.commit();error('THANH_LY_KHONG_GIAO_THO',sales_orders.confirm,db,p['sales'],o.id,o.version)
    o.fulfillment='TAI_QUAY';db.commit();sales_orders.confirm(db,p['sales'],o.id,o.version)
    o.fulfillment='THO_GIAO';db.commit();error('THANH_LY_KHONG_GIAO_THO',technician_deliveries.assign,db,p['dispatcher'],o.id,SalesDeliveryAssign(version=o.version,assignee_id=p['technician'].id))
    assert db.query(StockLedger).filter(StockLedger.qty_out>0).count()==1;assert_pending_invariant(db)


def test_web_credit_approval_permissions_warehouse_options_and_financial_line(db,liquidation,client):
    p,item,first,second=liquidation;row=request(db,p,item,qty=1)
    csrf=service_login(client,p['warehouse']);assert client.get('/cho-xu-ly/duyet-tra-ncc').status_code==403
    assert client.post(f'/cho-xu-ly/xl/{row.id}/duyet',data=dict(csrf=csrf,version=row.version)).status_code==403
    service_login(client,p['sales']);r=client.get('/don-ban/tim-hang?q=may');assert r.status_code==200 and 'data-liquidation-choices' in r.text and 'CXL' in r.text
    o=order(db,p,1,confirm=False);assert 'Kho Chờ Xử lý · thanh lý còn' in client.get(f'/don-ban/{o.id}').text
    csrf=service_login(client,p['finance']);r=client.get('/cho-xu-ly/duyet-tra-ncc');assert r.status_code==200 and row.number in r.text
    assert client.post(f'/cho-xu-ly/xl/{row.id}/duyet',data=dict(csrf=csrf,version=row.version),follow_redirects=False).status_code==303
    assert 'Chênh lệch trả hàng lỗi cho NCC' in client.get('/bao-cao/tai-chinh').text
    assert_pending_invariant(db)


def test_two_orders_cannot_reserve_the_same_liquidation_supply(db,liquidation,engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from app.models import User
    p,*_=liquidation;orders=[order(db,p,5,confirm=False) for _ in range(2)];jobs=[(o.id,o.version) for o in orders];user_id=p['sales'].id;barrier=Barrier(2)
    def confirm(job):
        with Session(engine) as session:
            user=session.get(User,user_id);barrier.wait(timeout=10)
            try:sales_orders.confirm(session,user,*job);return 'ok'
            except svc.BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(confirm,jobs))
    assert sorted(result)==['CXL_NOT_ENOUGH','ok']
    assert db.scalar(select(func.sum(StockReservation.qty)).where(StockReservation.status=='GIU'))==5
    assert stock_reservations.available(db,'CXL','TD-0001')==2;assert_pending_invariant(db)


def test_migration_existing_xl_money_defaults_and_down_up(db,lot):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    p,item=lot;row=make(db,p,item,'HUY',1,reason='Lịch sử CXL2a');ident=row.id
    spec=importlib.util.spec_from_file_location('migration40',Path(__file__).parents[1]/'migrations/versions/0040_cxl2b_tra_ncc_thanh_ly.py');migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(db.connection())):
        migration.downgrade();migration.upgrade()
        values=db.execute(text('select amount,vat_rate,vat_amount,total,delivery_id,order_line_no,voided_at from pending_dispositions where id=:id'),dict(id=ident)).one()
        assert tuple(values)==(0,0,0,0,None,None,None)
    db.commit();db.expire_all();assert db.get(PendingDisposition,ident).status=='CHO_DUYET';assert_pending_invariant(db)

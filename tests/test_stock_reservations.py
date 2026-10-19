"""S1: dựng tồn/đơn qua lõi, kiểm tranh chấp bằng PostgreSQL thật."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import (alerts, attachments, inventory_catalogs, purchase_returns, sales_orders,
                 service_charges, service_tickets, stock_docs, stock_posting, stock_reservations as svc)
from app.errors import BusinessError
from app.models import (AuditLog, FundLedger, PurchaseReturn, SalesOrder, StockBalance, StockDoc,
                        StockLedger, StockReservation, User, Warehouse)
from app.schemas import SalesOrderIn, ServiceCharges, StockDocIn, WarehouseIn
from app.security import issue_token
from test_my_work import people, purchase_people, ticket, DAY
from test_purchase_reports import received, returned
from test_web import service_login, d5_image
from web_forms import Forms, key_for


def fail(code, fn, *args, **kwargs):
    with pytest.raises(BusinessError) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == code, exc.value.message
    return exc.value


def stock(db, p, qty=7, warehouse='K58', kind='PN', **changes):
    body=dict(kind=kind,warehouse_code=warehouse,doc_date=DAY,
              lines=[dict(product_code='TD-0001',qty=qty,unit_cost=100 if kind=='PN' else None)])
    if kind=='PN':body['supplier_code']='NCC-0001'
    body.update(changes)
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(**body))
    return stock_posting.post(db,p['warehouse'],doc.id,doc.version)


def order(db, p, qty=4, warehouse='K58', confirm=True, who='sales', lines=None):
    lines=lines or [dict(kind='HANG',product_code='TD-0001',warehouse_code=warehouse,qty=qty,unit_price=200,vat_rate=0)]
    row=sales_orders.create(db,p[who],SalesOrderIn(customer_code='KTD-000001',lines=lines))
    if confirm:sales_orders.confirm(db,p[who],row.id,row.version)
    return row


def holds(db, row, status='GIU'):
    return list(db.scalars(select(StockReservation).where(StockReservation.order_id==row.id,
        StockReservation.status==status).order_by(StockReservation.id)))


@pytest.fixture(autouse=True)
def invariants(db):
    yield
    db.rollback()
    invalid=db.scalar(select(func.count()).select_from(StockReservation).join(SalesOrder,SalesOrder.id==StockReservation.order_id)
        .where(StockReservation.status=='GIU',SalesOrder.status.in_(('DA_HUY','DA_GIAO'))))
    assert invalid==0
    assert all(value>=0 for value in db.scalars(select(func.sum(StockReservation.qty)).where(
        StockReservation.status=='GIU').group_by(StockReservation.warehouse_code,StockReservation.product_code)))


def test_confirm_keeps_stock_without_moving_ledger(db,people):
    p=people;stock(db,p)
    before=db.query(StockLedger).count()
    row=order(db,p)
    held=holds(db,row)
    assert len(held)==1 and held[0].qty==4 and held[0].order_line_id==row.lines[0].line_no
    assert db.get(StockBalance,('K58','TD-0001')).qty==7
    assert svc.available(db,'K58','TD-0001')==3
    assert db.query(StockLedger).count()==before and db.query(FundLedger).count()==0
    assert db.scalar(select(AuditLog).where(AuditLog.action=='stock_reservation.keep')).after['qty']=='4.000'


def test_manual_px_and_service_cannot_take_reserved_stock(db,people):
    p=people;stock(db,p);row=order(db,p)
    exc=fail('RESERVED_STOCK',stock,db,p,5,kind='PX')
    assert exc.status==409 and Decimal(exc.details['stock'])==7 and Decimal(exc.details['reserved'])==4
    assert Decimal(exc.details['available'])==3 and Decimal(exc.details['requested'])==5
    assert exc.details['orders']==[row.number]
    stock(db,p,3,kind='PX')
    t=ticket(db,p,'DANG_LAM')
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[
        dict(kind='VAT_TU',product_code='TD-0001',warehouse_code='K58',description='Mực',qty=1,unit_price=200,vat_rate=0)]))
    before=db.query(StockDoc).count()
    fail('RESERVED_STOCK',service_tickets.transition,db,p['technician'],t.id,'HOAN_THANH',t.version)
    db.refresh(t)
    assert t.status=='DANG_LAM' and db.query(StockDoc).count()==before
    assert db.get(StockBalance,('K58','TD-0001')).qty==4 and holds(db,row)[0].qty==4


def test_ck_to_technician_cannot_take_holds(db,people):
    p=people;stock(db,p);order(db,p)
    inventory_catalogs.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K501',name='Kho thợ',kind='THO',holder_id=p['technician'].id))
    fail('RESERVED_STOCK',stock,db,p,4,kind='CK',to_warehouse_code='K501')
    assert db.get(StockBalance,('K58','TD-0001')).qty==7
    assert db.get(StockBalance,('K501','TD-0001')) is None  # dòng tồn 0 cũng rollback


def test_purchase_return_cannot_take_reserved_stock(db,people):
    p=people;po,receipt=received(db,p,qty=7,cost=100)
    row=order(db,p)
    before=db.query(StockDoc).count()
    fail('RESERVED_STOCK',returned,db,p,po,receipt,qty=4)
    assert db.query(PurchaseReturn).count()==0 and db.query(StockDoc).count()==before
    assert svc.available(db,'K58','TD-0001')==3 and holds(db,row)[0].qty==4


def test_delivery_consumes_own_hold_atomically_and_cancel_keeps_export_history(db,people):
    p=people;stock(db,p);row=order(db,p)
    sales_orders.deliver(db,p['warehouse'],row.id,row.version)
    assert row.status=='DA_GIAO' and not holds(db,row)
    assert holds(db,row,'DA_XUAT')[0].qty==4
    assert svc.available(db,'K58','TD-0001')==3
    doc=db.get(StockDoc,row.lines[0].stock_doc_id)
    assert (doc.kind,doc.status)==('PX','DA_GHI')
    sales_orders.cancel(db,p['sales'],row.id,'Khách đổi ý',row.version)
    assert db.get(StockBalance,('K58','TD-0001')).qty==7 and holds(db,row,'DA_XUAT')[0].qty==4


def test_two_concurrent_confirms_share_seven_as_five_plus_two(db,people,engine):
    p=people;stock(db,p)
    rows=[order(db,p,5,confirm=False),order(db,p,5,confirm=False)]
    ids=[r.id for r in rows];uid=p['sales'].id
    barrier=Barrier(2)
    def confirm(oid):
        with Session(engine,expire_on_commit=False) as session:
            user=session.get(User,uid)
            barrier.wait(timeout=10)
            result=sales_orders.confirm(session,user,oid,1)
            return holds(session,result)[0].qty,svc.for_order(session,result)[1]['missing']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(confirm,ids))
    assert sorted(results)==[(Decimal(2),Decimal(3)),(Decimal(5),Decimal(0))]
    assert sum(db.scalars(select(StockReservation.qty).where(StockReservation.status=='GIU')))==7
    assert db.get(StockBalance,('K58','TD-0001')).qty==7


def test_concurrent_confirm_and_px_never_steal_holds(db,people,engine):
    p=people;stock(db,p);row=order(db,p,5,confirm=False)
    oid,uid,wid=row.id,p['sales'].id,p['warehouse'].id
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PX',warehouse_code='K58',doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty=5)]))
    did,dv=doc.id,doc.version;barrier=Barrier(2)
    def run(which):
        with Session(engine,expire_on_commit=False) as session:
            user=session.get(User,uid if which=='confirm' else wid)
            barrier.wait(timeout=10)
            try:
                if which=='confirm':sales_orders.confirm(session,user,oid,1)
                else:stock_posting.post(session,user,did,dv)
                return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(run,['confirm','post']))
    assert outcomes[0]=='OK' and outcomes[1] in ('OK','RESERVED_STOCK')
    db.expire_all()
    qty=db.get(StockBalance,('K58','TD-0001')).qty
    held=sum(db.scalars(select(StockReservation.qty).where(StockReservation.status=='GIU')))
    assert (qty,held) in ((Decimal(2),Decimal(2)),(Decimal(7),Decimal(5)))


def test_partial_hold_then_restock_keep_more_and_deliver(db,people):
    p=people;stock(db,p,2);row=order(db,p,5)
    assert row.status=='DA_CHOT' and svc.for_order(db,row)[1]['missing']==3
    first=holds(db,row)[0];first_id=first.id
    stock(db,p,3)
    svc.keep_more(db,p['sales'],row.id,row.version)
    db.refresh(first)
    assert (first.status,first.reason)==('DA_NHA','Giữ thêm')
    current=holds(db,row)[0]
    assert current.id!=first_id and current.qty==5 and current.closed_at is None
    sales_orders.deliver(db,p['warehouse'],row.id,row.version)
    assert db.get(StockBalance,('K58','TD-0001')).qty==0
    assert len(holds(db,row,'DA_XUAT'))==1


def test_zero_available_does_not_insert_zero_hold(db,people):
    p=people;row=order(db,p)
    assert not holds(db,row) and svc.for_order(db,row)[1]['missing']==4
    assert db.query(StockReservation).count()==0
    stock(db,p)
    svc.keep_more(db,p['warehouse'],row.id,row.version)
    assert holds(db,row)[0].qty==4


def test_cancel_releases_all_holds_with_reason(db,people):
    p=people;stock(db,p);row=order(db,p)
    sales_orders.cancel(db,p['sales'],row.id,'Khách không lấy hàng',row.version)
    released=holds(db,row,'DA_NHA')[0]
    assert released.reason=='Khách không lấy hàng' and released.closed_by==p['sales'].id
    assert released.closed_at is not None and svc.available(db,'K58','TD-0001')==7


def test_manual_release_permissions_reason_and_keep_more_owner(db,people):
    p=people;stock(db,p);row=order(db,p);hid=holds(db,row)[0].id
    fail('REASON_REQUIRED',svc.release,db,p['warehouse'],row.id,hid,'',row.version)
    assert fail('FORBIDDEN',svc.release,db,p['sales'],row.id,hid,'Khách trễ',row.version).status==403
    svc.release(db,p['warehouse'],row.id,hid,'Khách trễ',row.version)
    assert row.status=='DA_CHOT' and not holds(db,row) and svc.available(db,'K58','TD-0001')==7
    assert fail('FORBIDDEN',svc.keep_more,db,p['other_sales'],row.id,row.version).status==403
    svc.keep_more(db,p['sales'],row.id,row.version)
    svc.release(db,p['director'],row.id,holds(db,row)[0].id,'Giám đốc nhả',row.version)
    assert not holds(db,row)


@pytest.mark.parametrize('operation',['kk','void_pn'])
def test_kk_and_void_pn_can_leave_overheld_and_alert_red(client,db,people,operation):
    p=people;pn=stock(db,p);row=order(db,p)
    if operation=='kk':stock(db,p,3,kind='KK')
    else:stock_posting.void(db,p['warehouse'],pn.id,'Hủy phiếu nhập',pn.version)
    assert svc.available(db,'K58','TD-0001')<0 and holds(db,row)[0].qty==4
    notices=alerts.collect(db,p['director'],today=DAY)
    assert notices.counts['GIU_VUOT_TON']==1
    warning=next(r for r in notices if r['kind']=='GIU_VUOT_TON')
    assert warning['level']=='DO' and warning['link']=='/the-kho/K58/TD-0001'
    service_login(client,p['director'])
    assert 'Giữ vượt tồn' in client.get('/ton-kho').text
    assert 'Giữ vượt tồn' in client.get('/tong-quan').text


def test_two_warehouses_deliver_two_px_and_close_both_groups(db,people):
    p=people;stock(db,p);stock(db,p,3,warehouse='K145')
    lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code=wh,qty=qty,unit_price=200) for wh,qty in [('K58',4),('K145',2)]]
    row=order(db,p,lines=lines)
    assert {(h.warehouse_code,h.qty) for h in holds(db,row)}=={('K58',4),('K145',2)}
    sales_orders.deliver(db,p['warehouse'],row.id,row.version)
    assert len({line.stock_doc_id for line in row.lines})==2 and len(holds(db,row,'DA_XUAT'))==2
    assert svc.available(db,'K58','TD-0001')==3 and svc.available(db,'K145','TD-0001')==1


def test_multiple_lines_same_product_do_not_overreserve_or_overexport(db,people):
    p=people;stock(db,p)
    lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=5,unit_price=200) for _ in range(2)]
    row=order(db,p,lines=lines)
    assert [r.qty for r in holds(db,row)]==[5,2]
    before=db.query(StockDoc).count()
    fail('NEGATIVE_STOCK',sales_orders.deliver,db,p['warehouse'],row.id,row.version)
    assert db.query(StockDoc).count()==before and db.get(StockBalance,('K58','TD-0001')).qty==7
    assert len(holds(db,row))==2


def test_foreign_order_holds_are_not_bypassed_on_delivery(db,people):
    p=people;stock(db,p,3);first=order(db,p,4);second=order(db,p,2)
    stock(db,p,2)
    svc.keep_more(db,p['sales'],second.id,second.version)
    before=db.query(StockDoc).count()
    fail('RESERVED_STOCK',sales_orders.deliver,db,p['warehouse'],first.id,first.version)
    assert first.status=='DA_CHOT' and db.query(StockDoc).count()==before
    assert sum(h.qty for h in holds(db,first))==3 and sum(h.qty for h in holds(db,second))==2


def test_reservation_history_cannot_be_deleted_or_changed(db,people):
    p=people;stock(db,p);row=order(db,p);rid=holds(db,row)[0].id
    for sql in ('delete from stock_reservations where id=:id','update stock_reservations set qty=1 where id=:id'):
        with pytest.raises(DBAPIError):db.execute(text(sql),{'id':rid})
        db.rollback()
    assert holds(db,row)[0].qty==4


def test_promise_date_version_and_audit_without_unlocking_prices(db,people):
    p=people;stock(db,p);row=order(db,p);version=row.version
    sales_orders.set_promised_date(db,p['sales'],row.id,DAY+timedelta(days=2),version)
    assert row.promised_date==DAY+timedelta(days=2) and row.version>version
    fail('VERSION_CONFLICT',sales_orders.set_promised_date,db,p['sales'],row.id,DAY,version)
    stale=row.version
    svc.keep_more(db,p['warehouse'],row.id,row.version)
    fail('VERSION_CONFLICT',svc.release,db,p['warehouse'],row.id,holds(db,row)[0].id,'Nhả',stale)
    assert db.scalar(select(AuditLog).where(AuditLog.action=='sales_order.promise')).after['promised_date']==(DAY+timedelta(days=2)).isoformat()


def test_web_hold_summary_pickers_filter_release_and_csrf(client,db,people):
    p=people;stock(db,p);row=order(db,p,5,confirm=False)
    csrf=service_login(client,p['sales'])
    page=client.post(f'/don-ban/{row.id}/confirm',data=dict(csrf=csrf,version=row.version))
    assert page.status_code==200 and 'Đã giữ 5/5' in page.text
    card=client.get('/the-kho/K58/TD-0001')
    assert 'Đang giữ' in card.text and 'Khả dụng' in card.text and row.number in card.text
    assert row.number in client.get('/don-ban?holding=1').text
    chooser=client.get('/don-ban/tim-hang?q=may in&warehouse=K58')
    assert 'Kho K58: còn 2 (đang giữ 5)' in chooser.text
    assert client.post(f'/don-ban/{row.id}/reserve',data={'version':2}).status_code==403
    csrf=service_login(client,p['warehouse'])
    assert 'Kho K58: còn 2 (đang giữ 5)' in client.get('/phieu-kho/tim-hang?q=may in&warehouse=K58').text
    db.refresh(row);rid=holds(db,row)[0].id
    page=client.post(f'/don-ban/{row.id}/release',data=dict(csrf=csrf,version=row.version,reservation_id=rid,reason='Khách chưa đến'))
    assert page.status_code==200 and 'Chưa giữ' in page.text and 'Thiếu 5' in page.text
    assert row.number not in client.get('/don-ban?holding=1').text
    service_login(client,p['dispatcher'])
    assert 'Kho K58: còn 7 (đang giữ 0)' in client.get('/dich-vu/tim-vat-tu?q=may in&warehouse=K58').text


def test_web_shortage_promise_overdue_and_stale_data_preserved(client,db,people):
    p=people;stock(db,p,2);row=order(db,p,5,confirm=False)
    csrf=service_login(client,p['sales'])
    page=client.post(f'/don-ban/{row.id}/confirm',data=dict(csrf=csrf,version=row.version))
    assert page.status_code==200 and 'Thiếu 3' in page.text and 'Giữ thêm' in page.text
    db.refresh(row)
    version=row.version
    page=client.post(f'/don-ban/{row.id}/promise',data=dict(csrf=csrf,version=version,promised_date=(DAY-timedelta(days=1)).isoformat()))
    assert page.status_code==200
    assert 'Quá hẹn' in client.get('/don-ban').text
    page=client.post(f'/don-ban/{row.id}/promise',data=dict(csrf=csrf,version=version,promised_date='2026-11-30'))
    assert page.status_code==409 and 'VERSION_CONFLICT' in page.text and 'value="2026-11-30"' in page.text
    stock(db,p,3);db.refresh(row)
    page=client.post(f'/don-ban/{row.id}/reserve',data=dict(csrf=csrf,version=row.version))
    assert page.status_code==200 and 'Đã giữ 5/5' in page.text


def test_api_readonly_roles_and_promise(client,db,people):
    p=people;row=order(db,p)
    def auth(role):return {'Authorization':'Bearer '+issue_token(p[role])}
    assert client.post(f'/sales-orders/{row.id}/reserve',json={'version':row.version},headers=auth('finance')).status_code==403
    response=client.patch(f'/sales-orders/{row.id}/promised-date',json=dict(version=row.version,promised_date='2026-11-01'),headers=auth('sales'))
    assert response.status_code==200 and response.json()['promised_date']=='2026-11-01'


def test_second_warehouse_failure_rolls_back_first_px_and_reservation_closure(db,people):
    p=people;stock(db,p,7);stock(db,p,1,warehouse='K145')
    row=order(db,p,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code=wh,qty=qty,unit_price=200)
        for wh,qty in [('K145',1),('K58',8)]])
    docs,ledger=db.query(StockDoc).count(),db.query(StockLedger).count()
    fail('NEGATIVE_STOCK',sales_orders.deliver,db,p['warehouse'],row.id,row.version)
    assert row.status=='DA_CHOT' and db.query(StockDoc).count()==docs and db.query(StockLedger).count()==ledger
    assert len(holds(db,row))==2 and not holds(db,row,'DA_XUAT')
    assert db.get(StockBalance,('K145','TD-0001')).qty==1 and db.get(StockBalance,('K58','TD-0001')).qty==7


def test_each_reservation_lifecycle_has_before_after_actor_and_reason(db,people):
    p=people;stock(db,p);row=order(db,p)
    svc.release(db,p['warehouse'],row.id,holds(db,row)[0].id,'Khách hẹn lại',row.version)
    svc.keep_more(db,p['sales'],row.id,row.version)
    sales_orders.deliver(db,p['warehouse'],row.id,row.version)
    logs=list(db.scalars(select(AuditLog).where(AuditLog.entity=='stock_reservation').order_by(AuditLog.id)))
    assert [r.action for r in logs]==['stock_reservation.keep','stock_reservation.release','stock_reservation.keep','stock_reservation.export']
    assert logs[0].before is None and logs[0].after['status']=='GIU' and logs[0].user_id==p['sales'].id
    assert logs[1].before['status']=='GIU' and logs[1].after['status']=='DA_NHA'
    assert logs[1].reason=='Khách hẹn lại' and logs[1].user_id==p['warehouse'].id
    assert logs[3].before['status']=='GIU' and logs[3].after['status']=='DA_XUAT'
    assert logs[3].after['closed_by']==p['warehouse'].id and logs[3].after['closed_at']

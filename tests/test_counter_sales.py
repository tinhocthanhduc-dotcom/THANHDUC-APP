"""S3: điều kiện nhận/thanh toán, tại quầy atomic và hạn nợ theo giờ VN."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import (alerts, customers, my_work, quotes, sales_orders as svc, sales_receipts,
                 sales_reports, stock_docs, stock_posting, stock_reservations)
from app.models import (AuditLog, Customer, FundLedger, SalesDelivery, SalesOrder, SalesReceipt,
                        StockBalance, StockDoc, StockLedger, StockReservation, User)
from app.schemas import (CustomerPatch, SalesCounterSale, SalesDeliveryIn, SalesOrderIn,
                         SalesOrderOptions, SalesOrderUpdate, SalesReceiptReport, StockDocIn)
from app.security import issue_token
from test_stock_reservations import people, purchase_people, stock, fail
from test_my_work import DAY, ticket
from test_web import service_login
from web_forms import key_for


def draft(db,p,amount=500000,qty=2,**terms):
    return svc.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(
        kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=qty,unit_price=amount//qty,vat_rate=0)],**terms))


def counter(o,**kw):
    return SalesCounterSale(**dict(version=o.version,method='TIEN_MAT',amount=o.total,**kw))


def credit(db,p,limit=10000000,allow=True):
    c=db.get(Customer,'KTD-000001')
    customers.update(db,p['finance'],c.code,CustomerPatch(version=c.version,allow_debt=allow,credit_limit=limit,phones=['0900000001']))


def groups(db,p,role='sales',today=DAY):
    return {g['key']:g for g in my_work.collect(db,p[role],today)}


def test_counter_posts_goods_and_pending_report_then_finance_confirms(db,people,configured_finance):
    p=people;stock(db,p,5);o=draft(db,p)
    svc.counter_sale(db,p['sales'],o.id,counter(o))
    assert o.status=='DA_GIAO' and o.receivable==500000
    gh=svc.deliveries(db,o.id);assert len(gh)==1 and gh[0].total==500000
    px=db.get(StockDoc,gh[0].lines[0].stock_doc_id)
    assert px.status=='DA_GHI' and px.kind=='PX' and px.lines[0].qty==2
    assert db.get(StockBalance,('K58','TD-0001')).qty==3
    r=db.query(SalesReceipt).one();assert r.status=='CHO_XAC_NHAN' and r.amount==500000
    assert db.query(FundLedger).count()==0  # PT chỉ sinh khi kế toán xác nhận.
    assert sales_receipts.balance(o)['pending']==500000
    actions=list(db.scalars(select(AuditLog.action).where(AuditLog.entity.in_(('sales_order','sales_receipt'))).order_by(AuditLog.id)))
    assert actions[-4:]==['sales_order.confirm','sales_order.deliver','sales_receipt.report','sales_order.counter_sale']
    sales_receipts.confirm(db,p['finance'],r.id,r.version)
    assert sales_receipts.balance(o)['remaining']==0 and sales_receipts.balance(o)['paid']==500000


def test_counter_and_manual_export_keep_counter_before_balance_lock_order(db,engine,people,monkeypatch):
    p=people;stock(db,p,5);o=draft(db,p)
    oid,version,uid,wid=o.id,o.version,p['sales'].id,p['warehouse'].id
    db.commit()
    export_ready, counter_started = Event(), Event()
    original=svc.lock_counter

    def locking(session,prefix,day):
        if prefix=='PX':
            counter_started.set()
        return original(session,prefix,day)

    monkeypatch.setattr(svc,'lock_counter',locking)

    def export():
        with Session(engine,expire_on_commit=False) as session:
            session.execute(text("set local statement_timeout = '10s'"))
            user=session.get(User,wid)
            doc=stock_docs.insert(session,user,StockDocIn(kind='PX',warehouse_code='K58',
                doc_date=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date(),lines=[dict(product_code='TD-0001',qty=1)]))
            export_ready.set()  # PX counter held; no balance lock yet.
            assert counter_started.wait(5)
            stock_posting.post_in_tx(session,user,doc.id,doc.version)
            session.commit()

    def sale():
        assert export_ready.wait(5)
        with Session(engine,expire_on_commit=False) as session:
            session.execute(text("set local statement_timeout = '10s'"))
            user=session.get(User,uid)
            svc.counter_sale(session,user,oid,SalesCounterSale(version=version,method='TIEN_MAT',amount=500000))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(export),pool.submit(sale)]
        for future in futures:
            future.result(timeout=20)
    db.expire_all()
    assert db.get(SalesOrder,oid).status=='DA_GIAO'
    assert db.get(StockBalance,('K58','TD-0001')).qty==2
    assert db.query(SalesReceipt).count()==1 and db.query(SalesDelivery).count()==1


def test_counter_shortage_rolls_back_everything_including_numbers_and_holds(db,people):
    p=people;stock(db,p,1);o=draft(db,p)
    old=(db.query(AuditLog).count(),db.query(StockLedger).count())
    fail('NEGATIVE_STOCK',svc.counter_sale,db,p['sales'],o.id,counter(o))
    db.refresh(o)
    assert o.status=='NHAP' and o.version==1 and o.receivable is None and o.confirmed_at is None
    assert not db.query(SalesDelivery).count() and not db.query(SalesReceipt).count() and not db.query(StockReservation).count()
    assert db.query(StockDoc).filter_by(kind='PX').count()==0
    assert (db.query(AuditLog).count(),db.query(StockLedger).count())==old
    assert db.get(StockBalance,('K58','TD-0001')).qty==1
    assert db.execute(text("select prefix from doc_counters where prefix in ('GH','PX','BT')")).all()==[]


def test_failure_in_report_after_delivery_rolls_back_entire_counter(db,people,monkeypatch):
    p=people;stock(db,p,5);o=draft(db,p)
    before=(db.query(AuditLog).count(),db.query(StockLedger).count())
    def stop(*args):raise svc.BusinessError('REPORT_FAILED','Báo thu chưa lưu được.',status=409)
    monkeypatch.setattr(sales_receipts,'report_in_tx',stop)
    fail('REPORT_FAILED',svc.counter_sale,db,p['sales'],o.id,counter(o))
    assert o.status=='NHAP' and o.version==1 and o.receivable is None
    assert db.query(SalesDelivery).count()==db.query(SalesReceipt).count()==db.query(StockReservation).count()==0
    assert db.query(StockDoc).filter_by(kind='PX').count()==0
    assert (db.query(AuditLog).count(),db.query(StockLedger).count())==before
    assert db.get(StockBalance,('K58','TD-0001')).qty==5


def test_wrong_counter_amount_or_terms_are_rejected_without_writes(db,people):
    p=people;o=draft(db,p)
    body=counter(o).model_copy(update={'amount':499999})
    fail('BAD_AMOUNT',svc.counter_sale,db,p['sales'],o.id,body)
    svc.change_options(db,p['sales'],o.id,SalesOrderOptions(version=o.version,fulfillment='KHACH_LAY_SAU'))
    fail('BAD_COUNTER_SALE',svc.counter_sale,db,p['sales'],o.id,counter(o))
    svc.change_options(db,p['sales'],o.id,SalesOrderOptions(version=o.version,fulfillment='TAI_QUAY',payment_term='DAT_COC',deposit_amount=100000))
    fail('BAD_COUNTER_SALE',svc.counter_sale,db,p['sales'],o.id,counter(o))
    assert o.status=='NHAP' and not db.query(SalesDelivery).count() and not db.query(SalesReceipt).count()


def test_web_counter_double_click_same_key_creates_one_set_and_csrf_checked(db,client,people):
    p=people;stock(db,p,5);o=draft(db,p)
    csrf=service_login(client,p['sales']);path=f'/don-ban/{o.id}/counter-sale'
    page=client.get(f'/don-ban/{o.id}')
    assert 'Bán tại quầy' in page.text and 'name="amount" value="500.000"' in page.text
    assert client.post(path,data=dict(version=o.version,method='TIEN_MAT',amount='500.000')).status_code==403
    data=dict(csrf=csrf,form_key=key_for(client,path),version=o.version,method='TIEN_MAT',amount='500.000',note='Tại cửa hàng')
    a=client.post(path,data=data,follow_redirects=False);b=client.post(path,data=data,follow_redirects=False)
    assert a.status_code==b.status_code==303 and a.headers['location']==b.headers['location']
    assert db.query(SalesDelivery).count()==db.query(SalesReceipt).count()==1
    assert db.query(StockDoc).filter_by(kind='PX').count()==1


def test_credit_forbidden_sale_and_director_override_requires_reason_and_audit(db,people):
    p=people;o=draft(db,p,payment_term='CONG_NO')
    exc=fail('CREDIT_LIMIT',svc.confirm,db,p['sales'],o.id,o.version)
    assert exc.status==409 and 'Cần giám đốc chốt' in exc.message
    fail('REASON_REQUIRED',svc.confirm,db,p['director'],o.id,o.version)
    svc.confirm(db,p['director'],o.id,o.version,'Khách quen được giám đốc duyệt')
    assert o.credit_override_by==p['director'].id and o.credit_override_reason=='Khách quen được giám đốc duyệt'
    log=db.scalar(select(AuditLog).where(AuditLog.entity_id==str(o.id),AuditLog.action=='sales_order.confirm'))
    assert log.before['credit_override_by'] is None and log.after['credit_override_by']==p['director'].id
    assert log.reason==o.credit_override_reason


def test_credit_current_debt_eight_million_new_three_fails_two_passes(db,people):
    p=people;stock(db,p,20);credit(db,p)
    old=draft(db,p,amount=8000000);svc.confirm(db,p['sales'],old.id,old.version);svc.deliver(db,p['sales'],old.id,old.version)
    over=draft(db,p,amount=3000000,payment_term='CONG_NO')
    exc=fail('CREDIT_LIMIT',svc.confirm,db,p['sales'],over.id,over.version)
    assert exc.details['remaining']==8000000
    equal=draft(db,p,amount=2000000,payment_term='CONG_NO')
    svc.confirm(db,p['sales'],equal.id,equal.version)
    assert equal.status=='DA_CHOT' and equal.credit_override_by is None
    # Chốt chưa giao không tăng nợ (S2).
    assert equal.receivable==0


@pytest.mark.parametrize('term',['TRA_NGAY','DAT_COC'])
def test_immediate_and_deposit_skip_credit_check(db,people,term):
    p=people;o=draft(db,p,payment_term=term,deposit_amount=100000 if term=='DAT_COC' else None,due_days=180)
    svc.confirm(db,p['sales'],o.id,o.version)
    assert o.status=='DA_CHOT' and o.due_days==0 and o.credit_override_by is None


def test_due_date_boundary_overdue_alerts_and_same_day_payment(db,people):
    p=people;stock(db,p,10);credit(db,p)
    o=draft(db,p,payment_term='CONG_NO',due_days=15)
    svc.confirm(db,p['sales'],o.id,o.version);svc.deliver(db,p['sales'],o.id,o.version)
    gh=svc.deliveries(db,o.id)[0];gh.delivered_at=datetime.fromisoformat('2026-11-01T23:30:00+07:00');db.commit()
    for day,late in [(date(2026,11,16),0),(date(2026,11,17),1)]:
        summary=svc.summaries(db,[o],day)[o.id]
        assert summary['due_date']==date(2026,11,16) and summary['overdue_days']==late
        found=[r for r in alerts.collect(db,p['director'],today=day) if r['kind']=='KHACH_NO_QUA_HAN']
        assert bool(found)==bool(late)
        if found:assert found[0]['level']=='VANG' and 'quá hạn 1 ngày' in found[0]['detail']
        assert bool(svc.search(db,p['sales'],overdue=True,today=day))==bool(late)
    rows=sales_reports.aging_orders(db,p['finance'],'0_7',date(2026,11,17))
    assert rows[0]['days']==1
    immediate=draft(db,p);svc.confirm(db,p['sales'],immediate.id,immediate.version);svc.deliver(db,p['sales'],immediate.id,immediate.version)
    svc.deliveries(db,immediate.id)[0].delivered_at=gh.delivered_at;db.commit()
    assert svc.summaries(db,[immediate],date(2026,11,2))[immediate.id]['overdue_days']==1


def test_deposit_reminder_default_report_amount_and_pending_removes_group(db,client,people):
    p=people;o=draft(db,p,amount=1000000,payment_term='DAT_COC',deposit_amount=500000)
    svc.confirm(db,p['sales'],o.id,o.version)
    assert groups(db,p)['orders_deposit']['rows'][0]['amount']==500000
    assert not groups(db,p,'other_sales')['orders_deposit']['count']
    assert groups(db,p,'director')['orders_deposit']['count']==1
    csrf=service_login(client,p['sales']);page=client.get(f'/don-ban/{o.id}?coc=1')
    assert 'Báo thu cọc' in page.text and 'name="amount" value="500.000"' in page.text
    posted=client.post(f'/don-ban/{o.id}/bao-thu',data=dict(csrf=csrf,version=o.version,method='TIEN_MAT',amount='500.000',received_at='2026-10-18'))
    assert posted.status_code==200 and not groups(db,p)['orders_deposit']['count']
    assert o.status=='DA_CHOT'  # Không tự giao hay ghi quỹ khi báo cọc.


def test_deposit_never_blocks_delivery_and_bad_amounts_rejected(db,people):
    p=people;stock(db,p,5)
    for amount in (0,500001):
        fail('BAD_DEPOSIT',draft,db,p,payment_term='DAT_COC',deposit_amount=amount)
    o=draft(db,p,payment_term='DAT_COC',deposit_amount=500000)
    svc.confirm(db,p['sales'],o.id,o.version);svc.deliver(db,p['warehouse'],o.id,o.version)
    assert o.status=='DA_GIAO' and groups(db,p)['orders_deposit']['count']==1


def test_late_pickup_visible_to_creator_and_warehouse_until_delivered(db,people):
    p=people;stock(db,p,5)
    o=draft(db,p,fulfillment='KHACH_LAY_SAU',promised_date=DAY-timedelta(days=1))
    svc.confirm(db,p['sales'],o.id,o.version)
    for role in ('sales','warehouse','director'):
        assert groups(db,p,role)['orders_pickup_late']['rows'][0]['id']==o.id
        assert groups(db,p,role)['orders_pickup_late']['rows'][0]['overdue'] is True
    assert groups(db,p,'other_sales')['orders_pickup_late']['count']==0
    svc.deliver(db,p['warehouse'],o.id,o.version)
    assert not groups(db,p)['orders_pickup_late']['count'] and not groups(db,p,'warehouse')['orders_pickup_late']['count']


def test_options_after_confirm_only_fulfillment_and_version_is_checked(db,people):
    p=people;o=draft(db,p)
    svc.change_options(db,p['sales'],o.id,SalesOrderOptions(version=o.version,payment_term='CONG_NO'))
    assert o.due_days==30
    svc.change_options(db,p['sales'],o.id,SalesOrderOptions(version=o.version,payment_term='TRA_NGAY'))
    svc.confirm(db,p['sales'],o.id,o.version)
    version=o.version
    fail('PRICE_LOCKED',svc.change_options,db,p['sales'],o.id,SalesOrderOptions(version=version,payment_term='DAT_COC',deposit_amount=1))
    svc.change_options(db,p['sales'],o.id,SalesOrderOptions(version=version,fulfillment='THO_GIAO'))
    assert o.fulfillment=='THO_GIAO' and o.payment_term=='TRA_NGAY'
    fail('VERSION_CONFLICT',svc.change_options,db,p['sales'],o.id,SalesOrderOptions(version=version,fulfillment='TAI_QUAY'))
    log=db.scalar(select(AuditLog).where(AuditLog.action=='sales_order.change_options').order_by(AuditLog.id.desc()))
    assert log.before['fulfillment']=='TAI_QUAY' and log.after['fulfillment']=='THO_GIAO'


def test_carrier_is_saved_audited_printed_and_errors_keep_input(db,client,people):
    p=people;stock(db,p,5);o=draft(db,p,fulfillment='VAN_CHUYEN')
    svc.confirm(db,p['sales'],o.id,o.version)
    csrf=service_login(client,p['warehouse'])
    page=client.get(f'/don-ban/{o.id}');assert 'name="carrier_name"' in page.text and 'name="tracking_code"' in page.text
    data=dict(csrf=csrf,version=o.version,delivery_form=1,deliver_1=3,carrier_name='Giao nhanh',tracking_code='VN-123')
    wrong=client.post(f'/don-ban/{o.id}/deliver',data=data)
    assert wrong.status_code==409 and 'Giao nhanh' in wrong.text and 'VN-123' in wrong.text
    good=client.post(f'/don-ban/{o.id}/deliver',data={**data,'deliver_1':2})
    assert good.status_code==200
    gh=svc.deliveries(db,o.id)[0]
    assert (gh.carrier_name,gh.tracking_code)==('Giao nhanh','VN-123')
    printed=client.get(f'/don-ban/{o.id}/giao/{gh.id}/in')
    assert printed.status_code==200 and 'Giao nhanh' in printed.text and 'VN-123' in printed.text
    log=db.scalar(select(AuditLog).where(AuditLog.action=='sales_delivery.create'))
    assert log.after['carrier_name']=='Giao nhanh'


def test_counter_api_permissions_and_version_conflict(db,client,people):
    p=people;stock(db,p,5);o=draft(db,p);path=f'/sales-orders/{o.id}/counter-sale'
    for role in ('finance','technician','warehouse'):
        r=client.post(path,headers={'Authorization':'Bearer '+issue_token(p[role])},json=counter(o).model_dump(mode='json'))
        assert r.status_code==403
    h={'Authorization':'Bearer '+issue_token(p['sales'])}
    bad=client.post(path,headers=h,json=counter(o).model_copy(update={'version':99}).model_dump(mode='json'))
    assert bad.status_code==409 and bad.json()['error']=='VERSION_CONFLICT'
    good=client.post(path,headers=h,json=counter(o).model_dump(mode='json'))
    assert good.status_code==200 and good.json()['status']=='DA_GIAO'


def test_web_terms_create_update_filters_and_overdue_labels(db,client,people,monkeypatch):
    from test_web import b3_form
    p=people;csrf=service_login(client,p['sales'])
    page=client.get('/don-ban/moi')
    assert 'Nhận hàng' in page.text and 'Thanh toán' in page.text and 'name="fulfillment" value="TAI_QUAY" checked' in page.text
    created=client.post('/don-ban/moi',data=b3_form(csrf,customer_code='KTD-000001',payment_term='DAT_COC',deposit_amount='500.000',
        fulfillment='KHACH_LAY_SAU',qty=['2','1'],unit_price=['500.000','0'],vat_0='0',vat_1='0'),follow_redirects=False)
    assert created.status_code==303
    o=db.query(SalesOrder).one()
    assert o.deposit_amount==500000 and o.fulfillment=='KHACH_LAY_SAU'
    page=client.get('/don-ban?fulfillment=TAI_QUAY');assert o.number not in page.text
    assert o.number in client.get('/don-ban?fulfillment=KHACH_LAY_SAU').text
    svc.confirm(db,p['sales'],o.id,o.version);stock(db,p,5);svc.deliver(db,p['warehouse'],o.id,o.version)
    svc.deliveries(db,o.id)[0].delivered_at=datetime.fromisoformat('2026-10-01T23:30:00+07:00');db.commit()
    page=client.get('/don-ban?overdue=1');assert o.number in page.text and 'Quá hạn' in page.text and 'Đã giao đủ' in page.text and 'Còn nợ' in page.text
    page=client.get('/don-ban/can-thu');assert 'Hạn' in page.text and 'Quá hạn (ngày)' in page.text and 'order-overdue' in page.text


def test_migration_old_orders_become_thirty_day_credit_without_changing_alerts(db,engine,people,monkeypatch):
    p=people;stock(db,p,5);o=draft(db,p)
    svc.confirm(db,p['sales'],o.id,o.version);svc.deliver(db,p['warehouse'],o.id,o.version)
    oid=o.id;svc.deliveries(db,oid)[0].delivered_at=datetime.fromisoformat('2026-09-03T23:30:00+07:00');db.commit();db.rollback()
    file=Path(__file__).parents[1]/'migrations/versions/0029_s3_ban_tai_quay.py'
    spec=importlib.util.spec_from_file_location('s3_upgrade',file);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with engine.begin() as conn:
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(conn)))
        migration.downgrade();migration.upgrade()
        row=conn.execute(text('select fulfillment,payment_term,due_days from sales_orders where id=:id'),dict(id=oid)).one()
        assert tuple(row)==('TAI_QUAY','CONG_NO',30)
    db.expire_all();o=db.get(SalesOrder,oid)
    found=[r for r in alerts.collect(db,p['director'],today=DAY) if r['kind']=='KHACH_NO_QUA_HAN']
    assert len(found)==1 and found[0]['level']=='VANG' and '15 ngày' in found[0]['detail']
    assert svc.summaries(db,[o],DAY)[oid]['overdue_days']==15

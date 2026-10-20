"""S2: giao thực tế, công nợ và báo cáo theo GH trên PostgreSQL thật."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import (finance_reports, purchase_reports, sales_orders as svc, sales_receipts,
                 sales_reports, stock_reservations, stock_posting)
from app.models import AuditLog, SalesDelivery, SalesDeliveryLine, StockBalance, StockDoc, StockLedger, User, Warehouse
from app.schemas import SalesOrderIn, SalesReceiptReport
from test_stock_reservations import people, purchase_people, stock, order, holds, fail
from test_my_work import DAY
from test_web import service_login


def send(db,p,o,qty,**kw):
    return svc.deliver(db,p['warehouse'],o.id,o.version,[dict(order_line_no=1,qty=qty)],**kw)


def test_partial_delivery_splits_hold_and_preserves_company_stock(db,people):
    p=people;stock(db,p,10);o=order(db,p,10)
    assert o.receivable==0
    send(db,p,o,6,receiver_name='Chị Lan',note='Lần đầu')
    gh=svc.deliveries(db,o.id)[0]
    assert gh.number=='GH-26-00001' and gh.total==1200 and gh.receiver_name=='Chị Lan'
    assert o.status=='GIAO_MOT_PHAN' and o.receivable==1200
    assert db.get(StockBalance,('K58','TD-0001')).qty==4
    assert [(r.status,r.qty) for r in db.scalars(select(stock_reservations.StockReservation).order_by(stock_reservations.StockReservation.id))]==[
        ('DA_NHA',10),('DA_XUAT',6),('GIU',4)]
    assert holds(db,o,'DA_NHA')[0].reason=='Tách khi giao '+gh.number
    assert db.get(StockDoc,gh.lines[0].stock_doc_id).lines[0].qty==6
    assert stock_reservations.for_order(db,o)[1]['held']==4


def test_final_delivery_reconciles_rounding_and_vat(db,people):
    p=people;stock(db,p,3)
    o=order(db,p,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=3,unit_price=33333,vat_rate=8)])
    send(db,p,o,1);send(db,p,o,2)
    rows=svc.deliveries(db,o.id)
    assert [r.number for r in rows]==['GH-26-00001','GH-26-00002']
    assert sum(r.subtotal for r in rows)==o.subtotal==99999
    assert sum(r.vat_total for r in rows)==o.vat_total==8000
    assert rows[1].vat_total==5333 and o.receivable==o.total and o.status=='DA_GIAO'
    assert not holds(db,o)


@pytest.mark.parametrize('qty,code',[(11,'OVER_DELIVERY'),(0,'EMPTY_DELIVERY')])
def test_invalid_delivery_rolls_back_everything(db,people,qty,code):
    p=people;stock(db,p,10);o=order(db,p,10)
    old=(db.query(AuditLog).count(),db.query(StockLedger).count())
    fail(code,send,db,p,o,qty)
    assert db.query(SalesDelivery).count()==0 and db.query(StockDoc).filter_by(kind='PX').count()==0
    assert (db.query(AuditLog).count(),db.query(StockLedger).count())==old
    assert holds(db,o)[0].qty==10 and o.status=='DA_CHOT'


def test_select_one_warehouse_and_other_line_only(db,people):
    p=people;stock(db,p,10);stock(db,p,5,warehouse='K145')
    o=order(db,p,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code=wh,qty=2,unit_price=100,vat_rate=0) for wh in ('K58','K145')]+[
        dict(kind='KHAC',description='Phí giao',qty=1,unit_price=30000,vat_rate=0)])
    svc.deliver(db,p['warehouse'],o.id,o.version,[dict(order_line_no=2,qty=1),dict(order_line_no=3,qty=1)])
    gh=svc.deliveries(db,o.id)[0]
    assert len(gh.lines)==2 and gh.total==30100 and gh.lines[1].stock_doc_id is None
    docs=list(db.scalars(select(StockDoc).where(StockDoc.kind=='PX')))
    assert len(docs)==1 and docs[0].warehouse_code=='K145'
    assert [(r.warehouse_code,r.qty) for r in holds(db,o)]==[('K58',2),('K145',1)]


def test_advance_paid_is_not_debt_and_converts_as_delivered(db,people,configured_finance):
    p=people;stock(db,p,10)
    o=order(db,p,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=10,unit_price=100000,vat_rate=0)])
    r=sales_receipts.report(db,p['sales'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=1000000))
    fail('OVER_PAYMENT',sales_receipts.report,db,p['sales'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=1))
    sales_receipts.confirm(db,p['finance'],r.id,r.version)
    assert sales_receipts.balance(o)['remaining']==0 and sales_receipts.balance(o)['advance']==1000000
    send(db,p,o,6)
    assert sales_receipts.balance(o)['advance']==400000
    fail('OVER_PAID',svc.close_remaining,db,p['sales'],o.id,'Không lấy nữa',o.version)
    send(db,p,o,4)
    assert sales_receipts.balance(o)['advance']==0 and sales_receipts.balance(o)['remaining']==0
    assert not sales_receipts.receivables(db,p['finance'])


def test_close_remaining_releases_stock_and_lowers_report_capacity(db,people):
    p=people;stock(db,p,10);o=order(db,p,10)
    send(db,p,o,6)
    fail('REASON_REQUIRED',svc.close_remaining,db,p['sales'],o.id,'',o.version)
    svc.close_remaining(db,p['sales'],o.id,'Khách lấy đủ',o.version)
    assert o.lines[0].closed_qty==4 and o.status=='DA_GIAO' and not holds(db,o)
    assert o.receivable==1200 and svc.effective_total(o)==1200
    fail('OVER_PAYMENT',sales_receipts.report,db,p['sales'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=1201))
    logs=list(db.scalars(select(AuditLog).where(AuditLog.action=='sales_order.close_remaining')))
    assert len(logs)==1 and logs[0].reason=='Khách lấy đủ' and logs[0].before and logs[0].after


def test_void_one_delivery_director_only_reopens_without_keeping(db,people):
    p=people;stock(db,p,10);o=order(db,p,10)
    send(db,p,o,6);a=svc.deliveries(db,o.id)[0]
    send(db,p,o,4);b=svc.deliveries(db,o.id)[1]
    fail('FORBIDDEN',svc.void_delivery,db,p['sales'],b.id,'Nhầm',b.version)
    fail('REASON_REQUIRED',svc.void_delivery,db,p['director'],b.id,'',b.version)
    svc.void_delivery(db,p['director'],b.id,'Nhầm số',b.version)
    assert o.status=='GIAO_MOT_PHAN' and o.receivable==1200 and not holds(db,o)
    assert db.get(StockBalance,('K58','TD-0001')).qty==4
    assert db.get(StockDoc,b.lines[0].stock_doc_id).status=='DA_HUY'
    fail('VERSION_CONFLICT',svc.void_delivery,db,p['director'],b.id,'Nhầm',1)
    fail('BAD_STATUS',svc.cancel,db,p['sales'],o.id,'Hủy đơn',o.version)
    svc.void_delivery(db,p['director'],a.id,'Nhầm cả lần đầu',a.version)
    assert o.status=='DA_CHOT' and o.receivable==0 and not holds(db,o)
    stock_reservations.keep_more(db,p['warehouse'],o.id,o.version)
    assert holds(db,o)[0].qty==10
    svc.cancel(db,p['sales'],o.id,'Không giao nữa',o.version)
    assert o.status=='DA_HUY'


def test_reports_follow_each_delivery_day_and_first_delivery_age(db,people):
    p=people;stock(db,p,10);o=order(db,p,10)
    assert finance_reports.debt_totals(db,p['finance'])['remaining']==0
    send(db,p,o,6);a=svc.deliveries(db,o.id)[0]
    send(db,p,o,4);b=svc.deliveries(db,o.id)[1]
    a.delivered_at=datetime.fromisoformat('2026-10-02T23:30:00+07:00')
    b.delivered_at=datetime.fromisoformat('2026-10-03T00:30:00+07:00')
    db.commit()
    rows=sales_reports.daily(db,p['finance'],DAY.replace(day=1),DAY)
    assert [(r['day'].day,r['subtotal']) for r in rows]==[(2,1200),(3,800)]
    margin=purchase_reports.gross_margin(db,p['finance'],DAY.replace(day=2),DAY.replace(day=2))
    assert margin['totals']['revenue']==1200 and margin['totals']['cost']==600
    assert margin['products'][0]['revenue']==1200
    assert finance_reports.debt_totals(db,p['finance'])['remaining']==2000
    debts=sales_reports.aging_orders(db,p['finance'],'8_30',DAY)
    assert debts[0]['days']==16 and debts[0]['remaining']==2000


def test_concurrent_partial_delivery_never_exceeds_order(db,engine,people):
    p=people;stock(db,p,10);o=order(db,p,10)
    oid,version,uid=o.id,o.version,p['warehouse'].id
    barrier=Barrier(2)
    def run(_):
        with Session(engine,expire_on_commit=False) as s:
            u=s.get(User,uid);svc.load(s,u,oid);barrier.wait(timeout=10)
            try:
                svc.deliver(s,u,oid,version,[dict(order_line_no=1,qty=6)])
                return 'OK'
            except Exception as e:
                return getattr(e,'code',str(e))
    db.rollback()
    with ThreadPoolExecutor(max_workers=2) as ex:
        results=list(ex.map(run,range(2)))
    assert sorted(results)==['OK','VERSION_CONFLICT']
    assert db.scalar(select(func.sum(SalesDeliveryLine.qty)))==6
    assert db.query(SalesDelivery).count()==1
    assert db.get(StockBalance,('K58','TD-0001')).qty==4


def test_second_warehouse_failure_is_atomic_with_gh_and_holds(db,people):
    p=people;stock(db,p,5);stock(db,p,1,warehouse='K145')
    o=order(db,p,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code=wh,qty=q,unit_price=100,vat_rate=0) for wh,q in [('K145',1),('K58',6)]])
    fail('NEGATIVE_STOCK',svc.deliver,db,p['warehouse'],o.id,o.version)
    assert db.query(SalesDelivery).count()==0 and db.query(StockDoc).filter_by(kind='PX').count()==0
    assert o.receivable==0 and o.status=='DA_CHOT'
    assert sum(r.qty for r in holds(db,o))==6


def test_web_partial_form_errors_csrf_print_and_director_void(db,client,people):
    p=people;stock(db,p,10);o=order(db,p,10)
    csrf=service_login(client,p['warehouse'])
    page=client.get(f'/don-ban/{o.id}')
    assert page.status_code==200 and 'name="deliver_1"' in page.text
    assert client.post(f'/don-ban/{o.id}/deliver',data=dict(version=o.version,deliver_1='6')).status_code==403
    response=client.post(f'/don-ban/{o.id}/deliver',data=dict(csrf=csrf,version=o.version,delivery_form='1',deliver_1='11',receiver_name='Giữ tên',note='Giữ ghi chú'))
    assert response.status_code==409 and 'OVER_DELIVERY' in response.text and 'Giữ tên' in response.text and 'Giữ ghi chú' in response.text
    response=client.post(f'/don-ban/{o.id}/deliver',data=dict(csrf=csrf,version=o.version,delivery_form='1',deliver_1='6',receiver_name='Chị Lan'),follow_redirects=False)
    assert response.status_code==303
    db.expire_all();gh=svc.deliveries(db,o.id)[0]
    detail=client.get(f'/don-ban/{o.id}')
    assert gh.number in detail.text and 'Giao một phần' in detail.text and 'Đã đóng' in detail.text
    printed=client.get(f'/don-ban/{o.id}/giao/{gh.id}/in')
    assert printed.status_code==200 and gh.number in printed.text and 'Bằng chữ' in printed.text and 'Người nhận' in printed.text
    assert 'Hủy lần giao' not in detail.text
    csrf=service_login(client,p['director'])
    assert 'Hủy lần giao' in client.get(f'/don-ban/{o.id}').text
    response=client.post(f'/don-ban/{o.id}/void-delivery',data=dict(csrf=csrf,version=gh.version,delivery_id=gh.id,reason='Sai số'),follow_redirects=False)
    assert response.status_code==303


def test_migration_backfills_legacy_delivery_preserving_px_and_receipts(db,engine,people,monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    p=people;stock(db,p,10);o=order(db,p,10);send(db,p,o,10)
    bt=sales_receipts.report(db,p['sales'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=100))
    bt_id,bt_number,bt_version=bt.id,bt.number,bt.version
    waiting=order(db,p,1)
    oid,wid,px,stamp,total,waiting_total=o.id,waiting.id,o.lines[0].stock_doc_id,o.delivered_at,o.total,waiting.total
    db.rollback()
    file=Path(__file__).parents[1]/'migrations/versions/0028_s2_giao_mot_phan.py'
    spec=importlib.util.spec_from_file_location('s2_migration',file)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    assert migration.down_revision=='0027'
    s3_spec=importlib.util.spec_from_file_location('s3_migration',file.parent/'0029_s3_ban_tai_quay.py')
    s3=importlib.util.module_from_spec(s3_spec);s3_spec.loader.exec_module(s3)
    s5_spec=importlib.util.spec_from_file_location('s5_migration',file.parent/'0031_s5_tra_hang_hoan_tien.py')
    s5=importlib.util.module_from_spec(s5_spec);s5_spec.loader.exec_module(s5)
    cxl2b_spec=importlib.util.spec_from_file_location('cxl2b_migration',file.parent/'0040_cxl2b_tra_ncc_thanh_ly.py')
    cxl2b=importlib.util.module_from_spec(cxl2b_spec);cxl2b_spec.loader.exec_module(cxl2b)
    with engine.begin() as conn:
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(conn)))
        monkeypatch.setattr(s3,'op',Operations(MigrationContext.configure(conn)))
        monkeypatch.setattr(s5,'op',Operations(MigrationContext.configure(conn)))
        monkeypatch.setattr(cxl2b,'op',Operations(MigrationContext.configure(conn)))
        # Hạ các lớp phụ thuộc GH trước khi tái hiện schema S1/S2.
        cxl2b.downgrade()
        s5.downgrade()
        s3.downgrade()
        migration.downgrade()
        assert conn.scalar(text('select receivable from sales_orders where id=:id'),dict(id=wid))==waiting_total
        migration.upgrade()
        gh=conn.execute(text('select * from sales_deliveries where order_id=:id'),dict(id=oid)).mappings().one()
        assert gh['delivered_at']==stamp and gh['total']==total and gh['number'].startswith('GH-26-')
        line=conn.execute(text('select * from sales_delivery_lines where delivery_id=:id'),dict(id=gh['id'])).mappings().one()
        assert line['stock_doc_id']==px and line['qty']==10
        assert conn.scalar(text('select receivable from sales_orders where id=:id'),dict(id=oid))==total
        assert conn.scalar(text('select receivable from sales_orders where id=:id'),dict(id=wid))==0
        old_bt=conn.execute(text('select number,amount,status,version from sales_receipts where id=:id'),dict(id=bt_id)).one()
        assert tuple(old_bt)==(bt_number,100,'CHO_XAC_NHAN',bt_version)
        s3.upgrade()
        s5.upgrade()
        cxl2b.upgrade()


def test_direct_stock_void_cannot_bypass_delivery_ownership(db,people):
    p=people;stock(db,p,10);o=order(db,p,10);send(db,p,o,6)
    gh=svc.deliveries(db,o.id)[0];px=db.get(StockDoc,gh.lines[0].stock_doc_id)
    fail('SOURCE_LOCKED',stock_posting.void,db,p['warehouse'],px.id,'Không qua GH',px.version)
    assert px.status=='DA_GHI' and gh.status=='DA_GIAO' and o.receivable==1200


def test_closing_keeps_actual_rounding_capacity_and_pending_overpaid_block(db,people):
    p=people;stock(db,p,3)
    o=order(db,p,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=3,unit_price=33333,vat_rate=8)])
    send(db,p,o,1);send(db,p,o,1)
    assert o.receivable==72000  # VAT từng GH: 2.667, không tính lại thành 5.333.
    pending=sales_receipts.report(db,p['sales'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=72001))
    fail('OVER_PAID',svc.close_remaining,db,p['sales'],o.id,'Lấy hai thôi',o.version)
    sales_receipts.reject(db,p['finance'],pending.id,'Chưa thu',pending.version)
    svc.close_remaining(db,p['sales'],o.id,'Lấy hai thôi',o.version)
    assert svc.effective_total(o)==o.receivable==72000
    fail('OVER_PAYMENT',sales_receipts.report,db,p['sales'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=72001))


def test_partial_debt_flows_through_work_alerts_dashboard_and_digest(db,people,monkeypatch):
    from app import alerts, dashboard, daily_digest, my_work
    p=people;stock(db,p,10);o=order(db,p,10)
    assert finance_reports.debt_totals(db,p['finance'])['remaining']==0
    send(db,p,o,6)
    gh=svc.deliveries(db,o.id)[0]
    gh.delivered_at=datetime.now(UTC)-timedelta(days=70)
    db.commit()
    assert finance_reports.debt_totals(db,p['finance'])['remaining']==1200
    assert sales_receipts.receivables(db,p['finance'])[0]['balance']['remaining']==1200
    groups={g['key']:g for g in my_work.collect(db,p['sales'])}
    assert groups['orders_waiting']['count']==1 and groups['orders_unreported']['count']==1
    found=[r for r in alerts.collect(db,p['director']) if r['kind']=='KHACH_NO_QUA_HAN']
    assert len(found)==1 and found[0]['level']=='DO'
    today=datetime.now(sales_reports.TZ).date()
    summary=dashboard.summary(db,p['director'],today)
    digest=daily_digest.build(db,p['director'],today)
    # Core reports feed the summary and digest; neither derives debt from order.total.
    assert summary['customer_debt']==digest['customer_debt']==1200
    assert db.query(SalesDelivery).count()==1


def test_api_partial_lines_close_and_delivery_versions(client,db,people):
    from app.security import issue_token
    p=people;stock(db,p,10);o=order(db,p,10)
    h={'Authorization':'Bearer '+issue_token(p['warehouse'])}
    url=f'/sales-orders/{o.id}'
    r=client.post(url+'/deliver',headers=h,json=dict(version=o.version,receiver_name='Anh Minh',lines=[dict(order_line_no=1,qty=6)]))
    assert r.status_code==200 and r.json()['status']=='GIAO_MOT_PHAN' and r.json()['receivable']==1200
    shipments=client.get(url+'/deliveries',headers=h)
    assert shipments.status_code==200 and len(shipments.json())==1
    gh=shipments.json()[0]
    assert gh['version']==1 and gh['lines'][0]['qty']=='6.000'
    assert client.post(url+'/close-remaining',headers=h,json=dict(version=r.json()['version'],reason='Không lấy nữa')).status_code==403
    h={'Authorization':'Bearer '+issue_token(p['sales'])}
    closed=client.post(url+'/close-remaining',headers=h,json=dict(version=r.json()['version'],reason='Không lấy nữa'))
    assert closed.status_code==200 and closed.json()['lines'][0]['closed_qty']=='4.000'


def test_partial_hold_exports_only_reserved_quantity_and_keeps_only_remainder(db,people):
    p=people;stock(db,p,4);o=order(db,p,10)
    assert holds(db,o)[0].qty==4
    stock(db,p,6)
    send(db,p,o,6)
    assert not holds(db,o) and holds(db,o,'DA_XUAT')[0].qty==4
    assert stock_reservations.for_order(db,o)[1]['missing']==4
    stock_reservations.keep_more(db,p['sales'],o.id,o.version)
    assert holds(db,o)[0].qty==4
    svc.close_remaining(db,p['sales'],o.id,'Không nhận phần cuối',o.version)
    assert not holds(db,o)


def test_delivery_and_other_px_use_counter_before_stock_lock(db,engine,people,monkeypatch):
    """Giống PX dịch vụ: giữ counter trong cùng transaction tới khi ghi xong."""
    from threading import Event
    from app import stock_docs
    from app.schemas import StockDocIn
    p=people;stock(db,p,10);o=order(db,p,6)
    oid,version,uid=o.id,o.version,p['warehouse'].id
    counter_locked,delivery_insert=Event(),Event()
    original=stock_docs.insert
    def insert(session,user,body):
        if body.reason.startswith('Giao GH-'):
            delivery_insert.set()
        return original(session,user,body)
    monkeypatch.setattr(stock_docs,'insert',insert)
    def other_px():
        with Session(engine,expire_on_commit=False) as s:
            u=s.get(User,uid)
            with stock_docs._transaction(s):
                px=stock_docs.insert(s,u,StockDocIn(kind='PX',warehouse_code='K58',doc_date=DAY,
                    reason='Xuất khác song song',lines=[dict(product_code='TD-0001',qty=2)]))
                counter_locked.set()
                assert delivery_insert.wait(timeout=15)
                stock_posting.post_in_tx(s,u,px.id,px.version)
    def delivery():
        assert counter_locked.wait(timeout=15)
        with Session(engine,expire_on_commit=False) as s:
            svc.deliver(s,s.get(User,uid),oid,version)
    db.rollback()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(other_px);b=pool.submit(delivery)
        a.result(timeout=25);b.result(timeout=25)
    assert db.get(StockBalance,('K58','TD-0001')).qty==2
    assert db.query(SalesDelivery).count()==1 and db.query(StockDoc).filter_by(kind='PX',status='DA_GHI').count()==2

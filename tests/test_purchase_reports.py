import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app import (attachments, finance_mappings, purchase_orders, purchase_receipts, purchase_returns,
                 purchase_reports as svc, sales_orders, sales_receipts, service_charges, service_tickets, supplier_debts)
from app.errors import BusinessError
from app.models import AuditLog, Customer, FundLedger, StockLedger, Supplier
from app.schemas import PurchaseOrderIn, SalesOrderIn, SalesReceiptReport, ServiceCharges, ServiceTicketIn
from test_purchase_orders import purchase_people, DAY
from test_supplier_debts import m3_people


@pytest.fixture
def report_people(db,m3_people,monkeypatch):
    monkeypatch.setattr(svc,'_today',lambda:DAY)
    p=m3_people
    db.add(Customer(code='KTD-000001',name='Khách Thành Đức'))
    db.commit()
    return p


def received(db,p,qty=1,cost=100000,day=DAY,supplier='NCC-0001',vat=8,product='TD-0001',warehouse='K58'):
    order=purchase_orders.create(db,p['warehouse'],PurchaseOrderIn(supplier_code=supplier,order_date=day,
        lines=[dict(product_code=product,warehouse_code=warehouse,qty=qty,unit_cost=cost,vat_rate=vat)]))
    purchase_orders.order(db,p['warehouse'],order.id,order.version)
    receipt=purchase_receipts.receive(db,p['warehouse'],order.id,order.version,day,[dict(order_line_id=order.lines[0].id,qty=qty)])[0]
    return order,receipt


def returned(db,p,order,receipt,qty=1,day=DAY):
    return purchase_returns.create(db,p['warehouse'],receipt.id,order.version,day,'Hàng lỗi',
        [dict(receipt_line_id=receipt.lines[0].id,qty=qty)])


def clock(monkeypatch,stamp):
    class Frozen(datetime):
        @classmethod
        def now(cls,tz=None):return stamp.astimezone(tz) if tz else stamp.replace(tzinfo=None)
    monkeypatch.setattr(sales_orders,'datetime',Frozen)
    monkeypatch.setattr(service_charges,'datetime',Frozen)
    monkeypatch.setattr(service_tickets,'_now',lambda:stamp)


def sold(db,p,lines=None):
    lines=lines or [dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=1,unit_price=250000,vat_rate=10)]
    o=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=lines))
    sales_orders.confirm(db,p['sales'],o.id,o.version)
    sales_orders.deliver(db,p['warehouse'],o.id,o.version)
    return o


def completed(db,p,billing='THU_TIEN',qty=1,price=300000):
    u=p['director'];tech=p['technician']
    t=service_tickets.create(db,p['sales'],ServiceTicketIn(customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Kẹt giấy'))
    service_tickets.assign(db,u,t.id,tech.id,t.version)
    service_tickets.transition(db,tech,t.id,'DA_NHAN',t.version)
    service_tickets.transition(db,tech,t.id,'DA_HEN',t.version,appointment_at=service_tickets._now()+timedelta(days=1))
    service_tickets.transition(db,tech,t.id,'DANG_LAM',t.version)
    attachments.add(db,tech,'service_ticket',str(t.id),'SAU','sau.pdf',b'%PDF-1.4')
    service_charges.set_charges(db,tech,t.id,ServiceCharges(version=t.version,billing=billing,
        free_reason='Bảo hành' if billing=='MIEN_PHI' else '',lines=[
            dict(kind='VAT_TU',product_code='TD-0001',warehouse_code='K58',qty=qty,unit_price=price,vat_rate=10),
            dict(kind='PHI',description='Công sửa',qty=1,unit_price=200000,vat_rate=8)]))
    return service_tickets.transition(db,tech,t.id,'HOAN_THANH',t.version)


def test_supplier_purchases_active_receipts_returns_voids_and_dates(db,report_people):
    p=report_people;f=p['finance'];o,a=received(db,p,qty=2,cost=100,day=date(2026,10,5))
    _,b=received(db,p,cost=1000,day=date(2026,10,10))
    t=returned(db,p,o,a,Decimal('0.5'),date(2026,10,15))
    purchase_receipts.void(db,p['warehouse'],b.id,'Nhận nhầm',b.version)
    received(db,p,cost=999,day=date(2026,9,30))
    rows=svc.purchases_by_supplier(db,f,date(2026,10,1),date(2026,10,31))
    assert rows==[dict(supplier_code='NCC-0001',name='Minh Phát',received_amount=200,received_vat=16,received_total=216,returned_total=54,net_total=162)]
    assert svc.purchase_totals(db,f)['net_total']==162
    assert svc.purchases_by_supplier(db,p['warehouse'],date(2026,10,15),date(2026,10,15))[0]['net_total']==-54
    assert svc.purchases_by_supplier(db,f,date(2026,10,6),date(2026,10,9))==[]
    purchase_returns.void(db,p['warehouse'],t.id,'Giữ hàng',t.version)
    assert svc.purchase_totals(db,f)['returned_total']==0


def test_product_receipt_quantities_net_before_vat_and_weighted_purchase_price(db,report_people):
    p=report_people;o,r=received(db,p,qty=2,cost=100,vat=10)
    received(db,p,qty=3,cost=201,vat=5)
    returned(db,p,o,r,Decimal('0.5'))
    rows=svc.purchases_by_product(db,p['warehouse'])
    assert rows==[dict(product_code='TD-0001',name='Máy in',received_qty=Decimal('5.000'),returned_qty=Decimal('0.500'),net_amount=753,avg_cost=161)]
    assert svc.product_totals(db,p['finance'])==dict(received_qty=Decimal('5'),returned_qty=Decimal('0.5'),net_amount=753)
    # Chỉ trả trong kỳ: không bịa giá mua bình quân khi kỳ đó không nhận.
    assert svc.purchases_by_product(db,p['finance'],DAY+timedelta(days=1),DAY+timedelta(days=1))==[]
    returned(db,p,o,r,Decimal('0.5'),DAY+timedelta(days=1))
    only=svc.purchases_by_product(db,p['finance'],DAY+timedelta(days=1),DAY+timedelta(days=1))[0]
    assert only['avg_cost'] is None and only['net_amount']==-50


def test_supplier_aging_reverse_fifo_opening_and_advance_match_balance(db,report_people):
    p=report_people;f=p['finance']
    supplier_debts.set_opening(db,p['director'],'NCC-0001',100000,DAY-timedelta(days=100),'Nợ cũ',0)
    received(db,p,cost=200000,day=DAY-timedelta(days=40),vat=0)
    received(db,p,cost=300000,day=DAY-timedelta(days=5),vat=0)
    supplier_debts.pay(db,f,'NCC-0001','QUY-TM',150000,DAY)
    db.add(Supplier(code='NCC-0002',name='Đã trả trước'));db.commit()
    o,r=received(db,p,cost=50000,supplier='NCC-0002',vat=0)
    supplier_debts.pay(db,f,'NCC-0002','QUY-TM',50000,DAY)
    returned(db,p,o,r)
    rows=svc.supplier_aging(db,f,DAY);a,b=rows
    assert a==dict(supplier_code='NCC-0001',name='Minh Phát',days_0_30=300000,days_31_60=150000,days_61_90=0,days_over_90=0,advance=0,remaining=450000)
    assert b['advance']==50000 and b['remaining']==-50000 and all(b[k]==0 for k in svc.BUCKETS)
    for row in rows:
        assert sum(row[k] for k in svc.BUCKETS)-row['advance']==supplier_debts.balance(db,f,row['supplier_code'])['remaining']
    assert svc.aging_totals(db,f)['remaining']==400000


def test_aging_boundaries_and_return_reduces_oldest_debt(db,report_people):
    p=report_people
    for days in (30,31,60,61,90,91):received(db,p,cost=100,day=DAY-timedelta(days=days),vat=0)
    row=svc.supplier_aging(db,p['finance'],DAY)[0]
    assert [row[k] for k in svc.BUCKETS]==[100,200,200,100]
    o,r=received(db,p,cost=100,day=DAY,vat=0)
    returned(db,p,o,r)
    row=svc.supplier_aging(db,p['finance'],DAY)[0]
    assert [row[k] for k in svc.BUCKETS]==[200,200,200,0]
    assert row['remaining']==600


def test_gross_margin_average_cost_shared_px_free_lines_vat_and_cancelled_order(db,report_people,monkeypatch):
    p=report_people;clock(monkeypatch,datetime(2026,10,18,23,30,tzinfo=svc.TZ))
    received(db,p,qty=5,cost=100000);received(db,p,qty=5,cost=200000)
    o=sold(db,p,[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=1,unit_price=250000,vat_rate=10),
        dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=1,unit_price=300000,vat_rate=8),
        dict(kind='KHAC',description='Công lắp',qty=1,unit_price=200000,vat_rate=5)])
    assert o.lines[0].stock_doc_id==o.lines[1].stock_doc_id
    cancelled=sold(db,p);gh=sales_orders.deliveries(db,cancelled.id)[0]
    sales_orders.void_delivery(db,p['director'],gh.id,'Bỏ lần giao',gh.version)
    sales_orders.cancel(db,p['sales'],cancelled.id,'Bỏ đơn',cancelled.version)
    result=svc.gross_margin(db,p['finance'],DAY,DAY);row=result['documents'][0]
    assert len(result['documents'])==1 and row['number']==sales_orders.deliveries(db,o.id)[0].number
    assert (row['revenue'],row['cost'],row['profit'],row['rate'])==(750000,300000,450000,Decimal('60.0'))
    assert o.total>row['revenue']
    assert result['products']==[dict(product_code='TD-0001',name='Máy in',revenue=550000,cost=300000,profit=250000,rate=Decimal('45.5'))]
    assert result['totals']==dict(documents=1,revenue=750000,cost=300000,profit=450000,rate=Decimal('60.0'))
    assert result['product_totals']['profit']==250000


def test_service_margin_parts_fees_closed_paid_in_full_and_free(db,report_people,monkeypatch):
    p=report_people;clock(monkeypatch,datetime(2026,10,18,23,30,tzinfo=svc.TZ))
    received(db,p,qty=10,cost=100000)
    ticket=completed(db,p);free=completed(db,p,billing='MIEN_PHI')
    finance_mappings.set_mapping(db,p['director'],'THU_TIEN_MAT',fund_code='QUY-TM',version=1)
    finance_mappings.set_mapping(db,p['director'],'DANH_MUC_DICH_VU',category_code='THU-BH',version=1)
    receipt=sales_receipts.report_for_ticket(db,p['director'],ticket.id,SalesReceiptReport(version=ticket.version,method='TIEN_MAT',amount=ticket.receivable,received_at=DAY))
    sales_receipts.confirm(db,p['finance'],receipt.id,receipt.version)
    assert ticket.status=='DA_DONG'
    data=svc.gross_margin(db,p['director'],DAY,DAY)
    rows={r['id']:r for r in data['documents']}
    assert len(rows)==2 and (rows[ticket.id]['revenue'],rows[ticket.id]['cost'],rows[ticket.id]['profit'])==(500000,100000,400000)
    assert (rows[free.id]['revenue'],rows[free.id]['cost'],rows[free.id]['profit'],rows[free.id]['rate'])==(0,100000,-100000,None)
    assert data['totals']==dict(documents=2,revenue=500000,cost=200000,profit=300000,rate=Decimal('60.0'))
    assert data['products'][0]['revenue']==300000 and data['products'][0]['cost']==200000


def test_margin_vietnam_window_and_service_reopened_excluded(db,report_people,monkeypatch):
    p=report_people;received(db,p,qty=10)
    clock(monkeypatch,datetime(2026,10,18,23,30,tzinfo=svc.TZ));a=sold(db,p);t=completed(db,p)
    clock(monkeypatch,datetime(2026,10,19,0,30,tzinfo=svc.TZ));b=sold(db,p)
    db.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
    assert {r['number'] for r in svc.gross_margin(db,p['finance'],DAY,DAY)['documents']}=={sales_orders.deliveries(db,a.id)[0].number,t.number}
    assert svc.gross_margin(db,p['finance'],DAY+timedelta(days=1),DAY+timedelta(days=1))['documents'][0]['number']==sales_orders.deliveries(db,b.id)[0].number
    service_tickets.transition(db,p['director'],t.id,'DANG_LAM',t.version,'Sửa lại')
    assert [r['number'] for r in svc.gross_margin(db,p['finance'],DAY,DAY)['documents']]==[sales_orders.deliveries(db,a.id)[0].number]


def test_reports_all_queries_readonly_no_orm_loads_no_autoflush_no_ledger_or_audit_changes(db,engine,report_people):
    p=report_people;received(db,p)
    before=[db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]
    sql=[];loaded=[]
    def capture(conn,cursor,statement,parameters,context,executemany):sql.append(statement)
    with Session(engine) as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        event.listen(session,'loaded_as_persistent',lambda s,obj:loaded.append(type(obj)))
        event.listen(engine,'before_cursor_execute',capture)
        try:
            svc.purchases_by_supplier(session,p['finance']);svc.purchase_totals(session,p['finance'])
            svc.purchases_by_product(session,p['warehouse']);svc.product_totals(session,p['warehouse'])
            svc.supplier_aging(session,p['finance']);svc.aging_totals(session,p['director']);svc.gross_margin(session,p['finance'])
        finally:event.remove(engine,'before_cursor_execute',capture)
    assert not loaded and all(q.lstrip().upper().startswith(('SELECT','WITH')) for q in sql)
    assert len(sql)==10 and sum('GROUP BY' in q.upper() for q in sql)>=8
    supplier=db.get(Supplier,'NCC-0001');supplier.name='Chưa lưu'
    assert svc.purchases_by_supplier(db,p['finance'])[0]['name']=='Minh Phát'
    assert db.is_modified(supplier);db.rollback()
    assert [db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]==before


def test_core_permissions_and_empty_defaults(db,report_people):
    p=report_people
    for role in ('sales','technician'):
        for fn in (svc.purchases_by_supplier,svc.purchase_totals,svc.purchases_by_product,svc.product_totals,svc.supplier_aging,svc.aging_totals,svc.gross_margin):
            with pytest.raises(BusinessError) as exc:fn(db,p[role])
            assert exc.value.code=='FORBIDDEN' and exc.value.status==403
    for fn in (svc.supplier_aging,svc.aging_totals,svc.gross_margin):
        with pytest.raises(BusinessError) as exc:fn(db,p['warehouse'])
        assert exc.value.code=='FORBIDDEN'
    assert svc.purchases_by_supplier(db,p['warehouse'])==[] and svc.supplier_aging(db,p['finance'])==[]
    assert svc.gross_margin(db,p['finance'])['totals']==dict(documents=0,revenue=0,cost=0,profit=0,rate=None)
    assert svc.date_range()==(date(2026,10,1),date(2026,10,31))
    with pytest.raises(BusinessError) as exc:svc.purchases_by_product(db,p['finance'],DAY,DAY-timedelta(days=1))
    assert exc.value.code=='BAD_DATE_RANGE'


def test_web_tabs_csv_bom_integer_money_and_permissions(client,db,report_people):
    from test_web import service_login
    p=report_people;received(db,p,cost=120000)
    service_login(client,p['finance'])
    counts=[db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]
    for tab,heading in [('ncc','Mua theo NCC'),('hang','Theo mã hàng'),('tuoi-no','Tuổi nợ NCC'),('lai-gop','Lãi gộp')]:
        page=client.get('/bao-cao-mua-hang',params={'tab':tab,'from':str(DAY),'to':str(DAY)})
        assert page.status_code==200 and heading in page.text and 'purchase-report-scroll' in page.text
        result=client.get(f'/bao-cao-mua-hang/{tab}.csv',params={'from':str(DAY),'to':str(DAY)})
        assert result.status_code==200 and result.content.startswith(b'\xef\xbb\xbf')
        csv_rows=list(csv.reader(io.StringIO(result.content.decode('utf-8-sig'))))
        assert csv_rows[0][0] in ('Mã NCC','Mã hàng','Chứng từ')
        if tab=='ncc':assert csv_rows[1][2:]==['120000','9600','129600','0','129600']
    assert client.get('/bao-cao-mua-hang/lai-gop-hang.csv').content.startswith(b'\xef\xbb\xbf')
    assert [db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]==counts
    assert client.get('/bao-cao-mua-hang?from=bad').status_code==400
    assert client.get('/bao-cao-mua-hang?tab=unknown').status_code==400
    assert client.get('/bao-cao-mua-hang/tuoi-no.csv?as_of=bad').status_code==400
    service_login(client,p['warehouse']);page=client.get('/bao-cao-mua-hang')
    assert page.status_code==200 and 'tab=tuoi-no' not in page.text and 'tab=lai-gop' not in page.text
    assert client.get('/bao-cao-mua-hang?tab=hang').status_code==200
    for table in ('tuoi-no','lai-gop','lai-gop-hang'):
        assert client.get(f'/bao-cao-mua-hang/{table}.csv').status_code==403
        if table!='lai-gop-hang':assert client.get('/bao-cao-mua-hang?tab='+table).status_code==403
    for role in ('sales','technician'):
        service_login(client,p[role])
        assert client.get('/bao-cao-mua-hang').status_code==403
        assert client.get('/bao-cao-mua-hang/ncc.csv').status_code==403


def test_margin_product_costs_do_not_multiply_when_one_px_has_two_products(db,report_people):
    p=report_people;received(db,p,qty=5,cost=100000);received(db,p,qty=3,cost=400000,product='TD-0002')
    order=sold(db,p,[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=2,unit_price=350000,vat_rate=10),
        dict(kind='HANG',product_code='TD-0002',warehouse_code='K58',qty=1,unit_price=900000,vat_rate=8)])
    assert order.lines[0].stock_doc_id==order.lines[1].stock_doc_id
    result=svc.gross_margin(db,p['finance'])
    assert result['totals']==dict(documents=1,revenue=1600000,cost=600000,profit=1000000,rate=Decimal('62.5'))
    products={r['product_code']:r for r in result['products']}
    assert (products['TD-0001']['revenue'],products['TD-0001']['cost'])==(700000,200000)
    assert (products['TD-0002']['revenue'],products['TD-0002']['cost'])==(900000,400000)
    assert result['product_totals']==dict(revenue=1600000,cost=600000,profit=1000000,rate=Decimal('62.5'))


def test_money_sums_remain_exact_above_javascript_integer_precision(db,report_people):
    p=report_people;huge=9007199254740993
    for _ in range(2):
        received(db,p,cost=huge,vat=0)
        sold(db,p,[dict(kind='KHAC',description='Dòng tự do',qty=1,unit_price=huge,vat_rate=0)])
    assert svc.purchase_totals(db,p['finance'])['net_total']==huge*2
    assert svc.purchases_by_product(db,p['finance'])[0]['avg_cost']==huge
    result=svc.gross_margin(db,p['finance'])
    assert result['totals']==dict(documents=2,revenue=huge*2,cost=0,profit=huge*2,rate=Decimal('100.0'))
    assert result['products']==[]

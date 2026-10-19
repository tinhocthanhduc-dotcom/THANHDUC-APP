from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app import sales_reports as svc, sales_receipts
from app.errors import BusinessError
from app.models import AuditLog, Customer, Quote, SalesOrder, SalesOrderLine, SalesDelivery, SalesDeliveryLine, SalesReceipt
from app.schemas import SalesReceiptReport


@pytest.fixture
def report_people(db,make_user,monkeypatch,configured_finance):
    monkeypatch.setattr(svc,'_today',lambda:date(2026,10,18))
    db.add_all([Customer(code='KTD-000001',name='Minh Phát'),Customer(code='KTD-000002',name='Thành Công')])
    db.commit()
    return {k:make_user('report.'+k,role) for k,role in [('a','sales'),('b','sales'),('finance','finance')]}


def order(db,p,n,**changes):
    data=dict(number=f'DB-26-{n:05}',customer_code='KTD-000001',created_by=p['a'].id,
              status='DA_GIAO',created_at=datetime.fromisoformat('2026-10-01T09:00:00+07:00'),
              confirmed_at=datetime.fromisoformat('2026-10-08T23:30:00+07:00'),confirmed_by=p['a'].id,
              delivered_at=datetime.fromisoformat('2026-10-01T23:30:00+07:00'),delivered_by=p['b'].id,
              subtotal=100000,vat_total=10000,total=110000,receivable=110000)
    data.update(changes)
    if 'confirmed_at' in changes and 'delivered_at' not in changes:
        data['delivered_at']=changes['confirmed_at']
    if data['status']=='DA_CHOT':data['receivable']=0
    o=SalesOrder(**data)
    o.lines=[SalesOrderLine(line_no=1,kind='KHAC',description='Dịch vụ',qty=1,unit='lần',unit_price=o.subtotal,
        vat_rate=0,amount=o.subtotal,vat_amount=o.vat_total)]
    db.add(o);db.flush()
    if o.status=='DA_GIAO':
        gh=SalesDelivery(number=f'GH-26-{n:05}',order_id=o.id,delivered_at=o.delivered_at,delivered_by=o.delivered_by,
            subtotal=o.subtotal,vat_total=o.vat_total,total=o.total,status='DA_GIAO',lines=[
                SalesDeliveryLine(order_id=o.id,order_line_no=1,qty=1,amount=o.subtotal,vat_amount=o.vat_total)])
        db.add(gh)
    db.commit()
    return o


def quote(db,p,n,**changes):
    data=dict(number=f'BG-26-{n:05}',customer_code='KTD-000001',created_by=p['a'].id,
              created_at=datetime.fromisoformat('2026-10-05T23:30:00+07:00'),valid_until=date(2026,10,25),status='CHAP_NHAN')
    data.update(changes)
    q=Quote(**data);db.add(q);db.commit();return q


def test_daily_sql_totals_statuses_and_period(db,report_people):
    p=report_people
    order(db,p,1)
    order(db,p,2,subtotal=200000,vat_total=16000,total=216000,receivable=216000)
    order(db,p,3,delivered_at=datetime.fromisoformat('2026-10-02T12:00:00+07:00'),subtotal=300000,vat_total=24000,total=324000)
    order(db,p,4,status='DA_CHOT')
    order(db,p,5,status='DA_HUY')
    order(db,p,6,delivered_at=datetime.fromisoformat('2026-09-30T23:59:59+07:00'))
    order(db,p,7,delivered_at=datetime.fromisoformat('2026-11-01T00:00:00+07:00'))
    rows=svc.daily(db,p['finance'],date(2026,10,1),date(2026,10,31))
    assert rows==[dict(day=date(2026,10,1),orders=2,subtotal=300000,vat_total=26000,total=326000),
                  dict(day=date(2026,10,2),orders=1,subtotal=300000,vat_total=24000,total=324000)]
    assert svc.totals(db,p['finance'])==dict(orders=3,subtotal=600000,vat_total=50000,total=650000)
    assert all(isinstance(r['total'],int) for r in rows)


def test_vietnam_day_late_night_and_early_morning_independent_db_timezone(db,report_people):
    p=report_people
    order(db,p,1,delivered_at=datetime.fromisoformat('2026-10-01T23:30:00+07:00'))
    order(db,p,2,delivered_at=datetime.fromisoformat('2026-10-02T00:30:00+07:00'))
    db.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
    assert [r['day'] for r in svc.daily(db,p['finance'])]==[date(2026,10,1),date(2026,10,2)]
    assert svc.totals(db,p['finance'],date(2026,10,2),date(2026,10,2))['orders']==1


def test_creator_scope_not_delivered_by_and_finance_all(db,report_people,make_user):
    p=report_people
    a=order(db,p,1,created_by=p['a'].id,delivered_by=p['b'].id)
    order(db,p,2,created_by=p['b'].id,customer_code='KTD-000002',subtotal=9000000,vat_total=0,total=9000000,receivable=9000000)
    assert [r['user_id'] for r in svc.by_employee(db,p['a'])]==[p['a'].id]
    assert svc.totals(db,p['a'])['total']==110000
    assert svc.by_customer(db,p['a'])[0]['customer_code']=='KTD-000001'
    assert {r['user_id'] for r in svc.by_employee(db,p['finance'])}=={p['a'].id,p['b'].id}
    assert svc.totals(db,p['finance'])['total']==9110000
    assert [r['id'] for r in svc.aging_orders(db,p['a'],'8_30')]==[a.id]
    assert svc.aging(db,p['a'])[1]['remaining']==110000
    director=make_user('report.director','director')
    assert svc.totals(db,director)['orders']==2
    dual=make_user('report.dual','sales','finance')
    assert svc.totals(db,dual)['orders']==2


def test_aging_b4_balance_pending_and_fully_paid(db,report_people):
    p=report_people
    o=order(db,p,1,status='DA_GIAO',delivered_at=datetime.fromisoformat('2026-10-08T23:30:00+07:00'),subtotal=1000000,vat_total=0,total=1000000,receivable=1000000)
    receipt=sales_receipts.report(db,p['a'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=1000000))
    assert svc.aging(db,p['finance'])[1]['remaining']==sales_receipts.balance(o)['remaining']==1000000
    detail=svc.aging_orders(db,p['finance'],'8_30')[0]
    assert detail['days']==10 and detail['pending']==1000000 and detail['remaining']==1000000
    sales_receipts.confirm(db,p['finance'],receipt.id,receipt.version)
    assert svc.aging(db,p['finance'])[1]['orders']==0 and svc.aging_orders(db,p['finance'],'8_30')==[]


def test_aging_boundaries_zero_to_over_sixty_and_receipt_statuses(db,report_people):
    p=report_people
    for n,days in enumerate([0,7,8,30,31,60,61],1):
        stamp=datetime.combine(date(2026,10,18)-timedelta(days=days),datetime.min.time(),svc.TZ)
        order(db,p,n,confirmed_at=stamp,receivable=100000,status='DA_GIAO')
    groups=svc.aging(db,p['finance'])
    assert [(r['orders'],r['remaining']) for r in groups]==[(2,200000),(2,200000),(2,200000),(1,100000)]
    # Khoảng doanh thu tháng này không làm mất nhóm nợ cũ hơn 60 ngày.
    assert svc.aging_orders(db,p['finance'],'over_60')[0]['days']==61
    for n,status in [(8,'NHAP'),(9,'DA_HUY')]:
        order(db,p,n,status=status,receivable=5000000)
    assert sum(r['orders'] for r in svc.aging(db,p['finance']))==7


def test_conversion_four_quotes_one_active_order_excludes_replaced(db,report_people):
    p=report_people
    q1=quote(db,p,1)
    q2=quote(db,p,2)
    quote(db,p,3,status='DA_GUI')
    q4=quote(db,p,4,status='TU_CHOI')
    quote(db,p,5,status='DA_HUY',replaced_by_id=q4.id)
    order(db,p,1,quote_id=q1.id,status='DA_HUY')
    order(db,p,2,quote_id=q1.id,status='DA_CHOT',created_at=datetime.fromisoformat('2026-11-01T00:00:00+07:00'))
    order(db,p,3,quote_id=q2.id,status='DA_HUY')
    result=svc.quote_conversion(db,p['finance'])
    assert result==dict(quotes=4,converted=1,rate=Decimal('25.00'))
    assert svc.quote_conversion(db,p['finance'],date(2000,1,1),date(2000,1,2))==dict(quotes=0,converted=0,rate=0)


def test_conversion_scopes_quote_and_order_creators(db,report_people):
    p=report_people
    qa=quote(db,p,1)
    qb=quote(db,p,2,created_by=p['b'].id)
    order(db,p,1,quote_id=qa.id,created_by=p['b'].id)
    order(db,p,2,quote_id=qb.id,created_by=p['b'].id)
    assert svc.quote_conversion(db,p['a'])==dict(quotes=1,converted=0,rate=0)
    assert svc.quote_conversion(db,p['b'])==dict(quotes=1,converted=1,rate=100)
    assert svc.quote_conversion(db,p['finance'])==dict(quotes=2,converted=2,rate=100)


def test_top_twenty_customers_sorted_and_exact_large_integer_sum(db,report_people):
    p=report_people
    for n in range(1,23):
        code=f'KTD-{n+2:06}'
        db.add(Customer(code=code,name=f'Khách {n}'));db.commit()
        order(db,p,n,customer_code=code,subtotal=n,vat_total=0,total=n,receivable=n)
    rows=svc.by_customer(db,p['finance'])
    assert len(rows)==20 and rows[0]['total']==22 and rows[-1]['total']==3
    huge=9007199254740993
    order(db,p,23,subtotal=huge,vat_total=0,total=huge,receivable=huge)
    order(db,p,24,subtotal=huge,vat_total=0,total=huge,receivable=huge)
    assert svc.totals(db,p['finance'])['total']==2*huge+253


def test_all_queries_work_in_read_only_transaction_and_no_orm_order_loads(db,engine,report_people):
    p=report_people
    order(db,p,1)
    before=db.query(AuditLog).count()
    loaded=[];sql=[]
    def capture(conn,cursor,statement,parameters,context,executemany):
        sql.append(statement)
    with Session(engine) as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        event.listen(session,'loaded_as_persistent',lambda s,obj:loaded.append(type(obj)))
        event.listen(engine,'before_cursor_execute',capture)
        try:
            svc.daily(session,p['finance']);svc.totals(session,p['finance']);svc.by_employee(session,p['finance'])
            svc.by_customer(session,p['finance']);svc.aging(session,p['finance'])
            svc.aging_orders(session,p['finance'],'8_30');svc.quote_conversion(session,p['finance'])
        finally:
            event.remove(engine,'before_cursor_execute',capture)
    assert not loaded and len(sql)==7 and all(s.lstrip().upper().startswith('SELECT') for s in sql)
    assert sum('GROUP BY' in s for s in sql)>=5
    assert db.query(AuditLog).count()==before


def test_reports_do_not_autoflush_callers_dirty_order(db,engine,report_people):
    p=report_people
    o=order(db,p,1);o.note='Chưa lưu'
    statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany):
        statements.append(statement)
    event.listen(engine,'before_cursor_execute',capture)
    try:
        svc.totals(db,p['finance']);svc.aging(db,p['finance'])
    finally:
        event.remove(engine,'before_cursor_execute',capture)
    assert db.is_modified(o) and all(s.lstrip().upper().startswith('SELECT') for s in statements)
    db.rollback()
    assert o.note==''


def test_forbidden_roles_and_invalid_parameters(db,report_people,make_user):
    tech=make_user('report.tech','technician')
    for fn,args in [(svc.daily,()),(svc.totals,()),(svc.by_employee,()),(svc.by_customer,()),(svc.aging,()),
                    (svc.aging_orders,('8_30',)),(svc.quote_conversion,())]:
        with pytest.raises(BusinessError) as exc:
            fn(db,tech,*args)
        assert exc.value.code=='FORBIDDEN' and exc.value.status==403
    with pytest.raises(BusinessError) as exc:
        svc.daily(db,report_people['finance'],date(2026,10,2),date(2026,10,1))
    assert exc.value.code=='BAD_DATE_RANGE'
    with pytest.raises(BusinessError) as exc:
        svc.aging_orders(db,report_people['finance'],'sai')
    assert exc.value.code=='BAD_BUCKET'

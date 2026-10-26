from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app import (dashboard as svc, finance_reports, purchase_reports, quotes, sales_orders,
                 sales_receipts, service_tickets, supplier_debts)
from app.errors import BusinessError
from app.models import AuditLog, FundLedger, StockLedger
from app.schemas import SalesOrderIn, SalesReceiptReport, ServiceTicketIn
from test_purchase_orders import purchase_people, DAY
from test_supplier_debts import m3_people
from test_purchase_reports import report_people, received, sold, completed, clock


@pytest.fixture
def people(db,report_people,monkeypatch):
    stamp=datetime(2026,10,18,12,tzinfo=svc.TZ)
    clock(monkeypatch,stamp)
    monkeypatch.setattr(svc,'_now',lambda:stamp)
    monkeypatch.setattr(quotes,'_today',lambda:DAY)
    return report_people


def fee(db,p,amount=100000):
    return sold(db,p,[dict(kind='KHAC',description='Công lắp',qty=1,unit_price=amount,vat_rate=10)])


def test_revenue_sales_services_status_free_and_month_to_today(db,people,monkeypatch):
    p=people;received(db,p,qty=30,vat=0)
    fee(db,p,250000);completed(db,p);completed(db,p,billing='MIEN_PHI')
    draft=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='KHAC',description='Chưa giao',qty=1,unit_price=9000000)]))
    sales_orders.confirm(db,p['sales'],draft.id,draft.version)
    cancelled=fee(db,p,5000000);gh=sales_orders.deliveries(db,cancelled.id)[0]
    sales_orders.void_delivery(db,p['director'],gh.id,'Hủy giao',gh.version)
    sales_orders.cancel(db,p['sales'],cancelled.id,'Hủy đơn',cancelled.version)
    clock(monkeypatch,datetime(2026,10,2,9,tzinfo=svc.TZ));fee(db,p,100000);completed(db,p)
    clock(monkeypatch,datetime(2026,10,19,9,tzinfo=svc.TZ));fee(db,p,999999);completed(db,p)
    data=svc.summary(db,p['director'],DAY)
    assert data['revenue_today']==750000
    assert data['revenue_month']==1350000
    assert data['revenue_last_month']==0
    assert data['revenue_daily'][-1]==dict(day=DAY,amount=750000)
    assert data['gross_margin_month']==purchase_reports.gross_margin(db,p['director'],DAY.replace(day=1),DAY)['totals']['profit']


def test_vietnam_day_boundaries_independent_database_timezone(db,people,monkeypatch):
    p=people;received(db,p,qty=5)
    clock(monkeypatch,datetime(2026,10,18,0,30,tzinfo=svc.TZ));fee(db,p,100)
    clock(monkeypatch,datetime(2026,10,18,23,30,tzinfo=svc.TZ));fee(db,p,200);completed(db,p)
    clock(monkeypatch,datetime(2026,10,19,0,30,tzinfo=svc.TZ));fee(db,p,300)
    db.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
    data=svc.summary(db,p['finance'],DAY)
    assert data['revenue_today']==500300
    assert data['revenue_daily'][-1]['amount']==500300
    assert svc.summary(db,p['finance'],DAY+timedelta(days=1))['revenue_today']==300


@pytest.mark.parametrize('anchor,previous_start,previous_end',[
    ('2026-10-01','2026-09-01','2026-09-30'),
    ('2026-01-01','2025-12-01','2025-12-31'),
    ('2024-03-01','2024-02-01','2024-02-29')])
def test_previous_month_first_day_year_boundary_and_leap_year(db,people,monkeypatch,anchor,previous_start,previous_end):
    from datetime import date
    p=people;today=date.fromisoformat(anchor);start=date.fromisoformat(previous_start);end=date.fromisoformat(previous_end)
    for day,amount in [(start-timedelta(days=1),999),(start,100),(end,200),(today,300)]:
        clock(monkeypatch,datetime.combine(day,datetime.min.time(),svc.TZ));fee(db,p,amount)
    data=svc.summary(db,p['finance'],today)
    assert data['last_month_start']==start and data['last_month_end']==end
    assert data['revenue_last_month']==300
    assert data['revenue_today']==data['revenue_month']==300


def test_funds_debts_match_sources_inventory_qty_times_average_cost_and_totals(db,people):
    p=people
    # Giá trị sổ 1 đồng, qty 3 và avg_cost 0: R1 phải theo qty × avg_cost như yêu cầu.
    received(db,p,qty=1,cost=1,vat=0)
    received(db,p,qty=2,cost=0,vat=0)
    received(db,p,qty='1.5',cost=101,product='TD-0002',warehouse='K145',vat=0)
    supplier_debts.set_opening(db,p['director'],'NCC-0001',100000,DAY,'Đầu kỳ',0)
    order=fee(db,p,600000)
    sales_receipts.report(db,p['sales'],order.id,SalesReceiptReport(version=order.version,method='TIEN_MAT',amount=200000,received_at=DAY))
    data=svc.summary(db,p['director'],DAY)
    assert data['funds']==finance_reports.funds(db,p['director'])
    assert data['fund_total']==finance_reports.fund_total(db,p['director'])==5000000
    assert data['customer_debt']==finance_reports.debt_totals(db,p['director'])['remaining']==660000
    assert data['supplier_debt']==purchase_reports.aging_totals(db,p['director'],DAY)['remaining']
    values={r['warehouse_code']:r['value'] for r in data['inventory']}
    assert values=={'K58':0,'K145':152}
    assert data['inventory_total']==sum(values.values())==152
    assert data['pending_receipts']==dict(count=1,amount=200000)


def test_service_open_overdue_pending_receipt_only_waiting(db,people,monkeypatch):
    p=people;now=datetime(2026,10,18,12,tzinfo=svc.TZ)
    for hour in (6,11,12):
        clock(monkeypatch,now.replace(hour=hour))
        service_tickets.create(db,p['sales'],ServiceTicketIn(customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Kẹt giấy'))
    order=fee(db,p,1000000)
    a=sales_receipts.report(db,p['sales'],order.id,SalesReceiptReport(version=order.version,method='TIEN_MAT',amount=100000,received_at=DAY))
    sales_receipts.reject(db,p['finance'],a.id,'Nhầm báo thu',a.version)
    sales_receipts.report(db,p['sales'],order.id,SalesReceiptReport(version=order.version,method='TIEN_MAT',amount=200000,received_at=DAY))
    data=svc.summary(db,p['finance'],DAY)
    assert data['services']['open']==3
    # Đối chiếu trực tiếp hạn do lõi SLA tính, cùng một thời điểm (không tự đặt hạn trong DB).
    rows=service_tickets.search(db,p['director'])
    assert data['services']['overdue']==sum(t.due_at is not None and t.due_at<now for t in rows)
    assert data['services']['overdue']>0
    assert data['pending_receipts']==dict(count=1,amount=200000)


def test_thirty_days_fill_zero_include_first_day_and_exclude_older(db,people,monkeypatch):
    p=people
    clock(monkeypatch,datetime.combine(DAY-timedelta(days=30),datetime.min.time(),svc.TZ));fee(db,p,999999)
    clock(monkeypatch,datetime.combine(DAY-timedelta(days=29),datetime.min.time(),svc.TZ));fee(db,p,100)
    data=svc.summary(db,p['finance'],DAY);series=data['revenue_daily']
    assert len(series)==30 and len({r['day'] for r in series})==30
    assert series[0]==dict(day=DAY-timedelta(days=29),amount=100)
    assert series[-1]==dict(day=DAY,amount=0)
    assert all(r['amount']==0 for r in series[1:])


def test_permissions_finance_omits_sensitive_fields_and_does_not_query_them(db,people,monkeypatch):
    p=people
    for role in ('sales','warehouse','technician'):
        with pytest.raises(BusinessError) as exc:svc.summary(db,p[role])
        assert exc.value.code=='FORBIDDEN' and exc.value.status==403
    director=svc.summary(db,p['director'])
    assert 'gross_margin_month' in director and 'inventory_total' in director and 'inventory' in director
    def forbidden(*args,**kwargs):raise AssertionError('Finance không được chạy truy vấn nhạy cảm')
    monkeypatch.setattr(svc,'_inventory',forbidden)
    monkeypatch.setattr(purchase_reports,'gross_margin',forbidden)
    data=svc.summary(db,p['finance'])
    assert not {'gross_margin_month','inventory','inventory_total'} & data.keys()
    assert data['revenue_today']==0


def test_summary_read_only_no_orm_load_no_autoflush_or_new_ledger_audit(db,engine,people):
    p=people;received(db,p)
    before=[db.query(model).count() for model in (AuditLog,StockLedger,FundLedger)]
    sql=[];loaded=[]
    def capture(conn,cursor,statement,parameters,context,executemany):sql.append(statement)
    with Session(engine) as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        event.listen(session,'loaded_as_persistent',lambda s,obj:loaded.append(type(obj)))
        event.listen(engine,'before_cursor_execute',capture)
        try:svc.summary(session,p['director'],DAY)
        finally:event.remove(engine,'before_cursor_execute',capture)
    assert not loaded and all(q.lstrip().upper().startswith(('SELECT','WITH')) for q in sql)
    from app.models import Supplier
    row=db.get(Supplier,'NCC-0001');db.expire(p['director'],['roles']);row.name='Chưa lưu'
    svc.summary(db,p['director'],DAY)
    assert db.is_modified(row);db.rollback()
    assert [db.query(model).count() for model in (AuditLog,StockLedger,FundLedger)]==before


def test_web_summary_home_redirects_menu_svg_and_role_permissions(client,people):
    from test_web import service_login
    p=people
    for role in ('director','finance','sales','warehouse','technician'):
        service_login(client,p[role])
        root=client.get('/',follow_redirects=False)
        assert root.status_code==303 and root.headers['location']==('/tong-quan' if role in ('director','finance') else '/tho' if role=='technician' else '/viec-cua-toi')
        page=client.get('/tong-quan')
        if role in ('director','finance'):
            assert page.status_code==200 and '<svg' in page.text and page.text.count('<rect ')==30
            assert 'overview-grid' in page.text and '>Tổng quan</a>' in page.text
            assert ('Giá trị tồn kho' in page.text)==(role=='director')
            assert ('Lãi gộp tháng này' in page.text)==(role=='director')
            assert 'href="/bao-thu"' in page.text
            nav=page.text.split('<nav aria-label="Menu chính" class="role-nav">',1)[1].split('</nav>',1)[0]
            assert nav.index('href="/tong-quan"')<nav.index('href="/khach-hang"')
        else:assert page.status_code==403
    client.cookies.clear()
    assert client.get('/',follow_redirects=False).headers['location']=='/dang-nhap'


def test_day_board_technicians_sales_counts_reasons_and_roles(client,db,people,monkeypatch):
    # Tổng quan điều hành (26/10/2026): bảng thợ / kinh doanh trong ngày, chỉ giám đốc; số đếm chung cho kế toán.
    from test_web import service_login
    # Bước phiếu và ngày tạo ghi theo giờ máy chủ thật, nên bảng ngày xem theo ngày Việt Nam thật (không phải DAY đóng băng).
    p=people;received(db,p,qty=10);monkeypatch.setattr(sales_orders,'datetime',datetime)
    today=datetime.now(svc.TZ).date()
    completed(db,p)
    t=service_tickets.create(db,p['sales'],ServiceTicketIn(customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Mờ bản in'))
    service_tickets.assign(db,p['director'],t.id,p['technician'].id,t.version)
    service_tickets.transition(db,p['technician'],t.id,'DA_NHAN',t.version)
    service_tickets.transition(db,p['technician'],t.id,'DA_HEN',t.version,appointment_at=service_tickets._now()+timedelta(hours=2))
    service_tickets.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    service_tickets.transition(db,p['technician'],t.id,'TAM_DUNG',t.version,note='Chờ khách',hold_reason='CHO_KHACH_DUYET')
    sold(db,p)
    board=svc.day_board(db,p['director'],today)
    c=board['counts']
    assert c['received']==2 and c['done']==1 and c['failed']==0 and c['open']==1 and c['unassigned']==0
    assert c['orders']==1 and c['orders_delivered']==1 and c['orders_waiting']==0 and c['orders_amount']==250000
    tech=[r for r in board['technicians'] if r['user_id']==p['technician'].id][0]
    assert tech['assigned']==2 and tech['done']==1 and tech['open']==1 and tech['overdue']==0
    assert [i['reason'] for i in tech['items']]==['Chờ khách duyệt'] and tech['items'][0]['number']==t.number
    sales=board['sales'];assert len(sales)==1 and sales[0]['name']==p['sales'].full_name and sales[0]['orders']==1 and sales[0]['delivered']==1 and sales[0]['amount']==250000
    assert 'technicians' not in svc.day_board(db,p['finance'],today) and svc.day_board(db,p['finance'],today)['counts']['received']==2
    service_login(client,p['director']);page=client.get('/tong-quan').text
    assert 'Thợ hôm nay' in page and 'Chờ khách duyệt' in page and 'Kinh doanh hôm nay' in page
    assert page.index('Cần để mắt ngay')<page.index('Thợ hôm nay')<page.index('Theo dõi · không gấp')<page.index('Tiền · tháng này')<page.index('Doanh thu 30 ngày gần nhất')
    # Khách vượt hạn mức là việc theo dõi, gập sẵn ở cuối, không tính vào việc đỏ (TUẤN 26/10/2026).
    assert page.index('Theo dõi · không gấp')<page.index('data-alert-kind="KHACH_VUOT_HAN_MUC"') and '<details class="alert-group" data-alert-kind="KHACH_VUOT_HAN_MUC" open' not in page
    service_login(client,p['finance']);page=client.get('/tong-quan').text
    assert 'Thợ hôm nay' not in page and 'Kinh doanh hôm nay' not in page and 'Hôm nay nhìn nhanh' in page

"""NS3 projections against PostgreSQL: exact numbers, boundary dates, authorization and batching."""
import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from app import kpi as svc, purchase_reports
from app.errors import BusinessError
from app.models import (AuditLog, AttendanceHoliday, Customer, DayClosing, Employee, KpiTarget,
    PartRequest, PendingItem, Quote, SalesDelivery, SalesOrder, SalesReceipt, ServiceStep, ServiceTicket,
    ServiceVisit, StockBalance, StockDoc, StockLedger, TechnicianReturn, TechnicianReturnLine, Warehouse)
from test_purchase_orders import purchase_people
from test_tk_tai_khoan import web_login

MONTH=date(2026,11,1)


def stamp(s):return datetime.fromisoformat(s).replace(tzinfo=svc.TZ)


@pytest.fixture
def p(db,purchase_people,make_user,monkeypatch):
    p=purchase_people
    for r in ('dispatcher','other','reception','admin'):
        p[r]=make_user('kpi.'+r,'warehouse' if r=='other' else r)
    db.add(Customer(code='KTD-000001',name='Khách KPI',search_text='khach kpi'))
    for r,u in p.items():
        db.add(Employee(user_id=u.id,code='NV-'+str(u.id),position='Khác',start_date=date(2026,1,1),created_by=p['director'].id))
    db.commit()
    monkeypatch.setattr(svc,'_now',lambda:stamp('2026-12-01T00:00'))
    return p


def ticket(db,p,number='DV1',at='2026-11-02T08:00',**kw):
    data=dict(number=number,customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Kẹt giấy',
              assignee_id=p['technician'].id,created_by=p['dispatcher'].id,created_at=stamp(at))
    data.update(kw);row=ServiceTicket(**data);db.add(row);db.flush();return row


def step(db,p,t,status,at,actor='technician',**kw):
    row=ServiceStep(ticket_id=t.id,action='transition',to_status=status,actor_id=p[actor].id,at=stamp(at),**kw)
    db.add(row);db.flush();return row


def receipt(db,p,number='BT1',**kw):
    data=dict(number=number,ticket_id=kw.pop('ticket_id',None),order_id=kw.pop('order_id',None),method='TIEN_MAT',amount=500,
        received_at=date(2026,11,2),reported_by=p['technician'].id,reported_at=stamp('2026-11-02T08:00'),
        decided_by=p['finance'].id,decided_at=stamp('2026-11-02T09:00'),status='DA_XAC_NHAN')
    data.update(kw);row=SalesReceipt(**data);db.add(row);db.flush();return row


def err(code,fn,*args,**kw):
    with pytest.raises(BusinessError) as exc:fn(*args,**kw)
    assert exc.value.code==code
    return exc.value


def test_empty_all_metrics_and_eligibility(db,p,make_user):
    rows=svc.compute(db,MONTH)
    assert len(svc.METRICS)==18 and p['director'].id not in rows
    assert p['admin'].id not in rows and p['reception'].id not in rows
    for m in svc.METRICS:
        role=m['group'];value=rows[p[role].id][m['key']]
        if m['key']=='kt_chot_so_dung_ngay':assert value==0
        else:assert value==(None if m['mode']!='sum' else 0)
    u=make_user('no.profile','sales');assert svc.my_kpi(db,u,MONTH)=={}
    e=db.scalar(select(Employee).where(Employee.user_id==p['sales'].id));e.end_date=date(2026,10,31);db.commit()
    assert p['sales'].id not in svc.compute(db,MONTH)
    e.end_date=date(2026,11,1);db.commit();assert p['sales'].id in svc.compute(db,MONTH)


@pytest.fixture
def sample(db,p):
    t=ticket(db,p,status='HOAN_THANH',appointment_at=stamp('2026-11-02T09:00'),due_at=stamp('2026-11-02T10:00'))
    step(db,p,t,'DA_GIAO','2026-11-02T08:10',actor='dispatcher',assignee_id=p['technician'].id)
    step(db,p,t,'DA_NHAN','2026-11-02T08:30')
    step(db,p,t,'HOAN_THANH','2026-11-02T11:00')
    step(db,p,t,'HOAN_THANH','2026-11-03T11:00')  # repeated completion is one ticket
    db.add(ServiceVisit(ticket_id=t.id,technician_id=p['technician'].id,started_at=stamp('2026-11-02T09:29'),ended_at=stamp('2026-11-02T11:00'),outcome='XONG'))
    warranty=ticket(db,p,'BH1',kind='BAO_HANH',warranty_of_id=t.id,warranty_basis='PHIEU_TRUOC',at='2026-11-04T08:00',status='DA_HUY')
    warranty.status='MOI'
    receipt(db,p,ticket_id=t.id,received_at=date(2026,11,4))
    receipt(db,p,'BT-REJECT',ticket_id=t.id,status='TU_CHOI',reported_at=stamp('2026-11-01T08:00'))
    receipt(db,p,'BT-OUT',ticket_id=t.id,received_at=date(2026,10,31),decided_at=stamp('2026-10-31T09:00'))
    o=SalesOrder(number='DB1',customer_code='KTD-000001',created_by=p['sales'].id,status='DA_GIAO');db.add(o);db.flush()
    receipt(db,p,'BT-SALE',order_id=o.id,amount=700)
    db.add(SalesDelivery(number='GH1',order_id=o.id,delivered_at=stamp('2026-11-30T23:50'),delivered_by=p['warehouse'].id,
                         subtotal=1000,vat_total=0,total=1000,status='DA_GIAO'))
    q=Quote(number='BG1',customer_code='KTD-000001',created_by=p['sales'].id,status='CHAP_NHAN');db.add(q);db.flush()
    db.add(AuditLog(at=stamp('2026-11-02T08:00'),user_id=p['sales'].id,action='quote.send',entity='quote',entity_id=str(q.id)))
    q2=Quote(number='BG2',customer_code='KTD-000001',created_by=p['sales'].id,status='DA_GUI');db.add(q2);db.flush()
    db.add(AuditLog(at=stamp('2026-11-03T08:00'),user_id=p['sales'].id,action='quote.send',entity='quote',entity_id=str(q2.id)))
    ticket(db,p,'OLD',at='2026-10-01T08:00')
    db.add(PartRequest(number='YC1',requester_id=p['technician'].id,product_code='TD-0001',qty=1,reason='Thay trống',status='DA_CAP',created_at=stamp('2026-11-02T08:00'),decided_at=stamp('2026-11-02T10:00'),decided_by=p['warehouse'].id))
    kk=StockDoc(number='KK1',kind='KK',warehouse_code='K58',doc_date=date(2026,11,2),created_by=p['warehouse'].id,status='DA_GHI',posted_at=stamp('2026-11-02T08:00'))
    db.add(kk);db.flush()
    for n,amount in enumerate((100,-200),1):
        db.add(StockLedger(warehouse_code='K58',product_code='TD-000'+str(n),doc_id=kk.id,line_no=n,qty_in=1 if amount>0 else 0,qty_out=0 if amount>0 else 1,unit_cost=abs(amount),amount=amount,balance_qty=1,balance_value=abs(amount)))
    db.add(Warehouse(code='LOI',name='Kho Chờ Xử lý',kind='LOI'))
    db.flush()
    db.add(StockBalance(warehouse_code='LOI',product_code='TD-0001',qty=1,value=100,avg_cost=100))
    db.add(PendingItem(number='CXL1',warehouse_code='LOI',product_code='TD-0001',qty_in=1,qty_left=1,unit_cost=100,group_code='HANG_LOI',source='TON_CU',entered_at=stamp('2026-10-01T08:00'),status='DANG_CHO'))
    r=TechnicianReturn(number='TK1',technician_id=p['technician'].id,warehouse_code='K58',to_warehouse_code='K145',created_by=p['technician'].id,status='DA_NHAN',received_at=stamp('2026-11-02T08:00'))
    db.add(r);db.flush()
    db.add(TechnicianReturnLine(return_id=r.id,line_no=1,product_code='TD-0001',qty=3,condition='HU_HONG',damage_reason='rơi vỡ',received_qty=2,received_condition='HU_HONG',received_damage_reason='rơi vỡ'))
    db.add(DayClosing(day=date(2026,11,2),status='DA_CHOT',closed_by=p['finance'].id,closed_at=stamp('2026-11-03T09:59')))
    db.add(AttendanceHoliday(day=date(2026,11,3),name='Nghỉ lễ'))
    db.commit()
    return p


@pytest.mark.parametrize('key,expected',[
    ('tho_hoan_thanh',1),('tho_dung_hen',100),('tho_nhan_viec',20),('tho_bao_hanh_lai',1),('tho_tien_thu',500),('tho_bao_hu',2),
    ('kd_doanh_thu',1000),('kd_lai_gop',1000),('kd_bao_gia_thanh_don',50),('kd_tien_thu',700),
    ('dp_giao_viec',10),('dp_tre_hen',1),('dp_ton_dong',2),('kho_cap_linh_kien',2),('kho_chenh_kiem_ke',300),
    ('kho_cho_xu_ly_qua_han',1),('kt_xac_nhan_trong_ngay',Decimal(200)/3),('kt_chot_so_dung_ngay',Decimal(100)/24)])
def test_each_metric_exact(db,sample,key,expected):
    p=sample;m=svc.BY_KEY[key]
    actual=svc.compute(db,MONTH)[p[m['group']].id][key]
    assert actual==pytest.approx(expected)
    details=svc.detail(db,p['director'],MONTH,p[m['group']].id,key)
    assert details


def test_arrival_29_31_missing_appointment_and_visit_denominator(db,p):
    for i,delay in enumerate((29,31,None,'no-visit'),1):
        t=ticket(db,p,str(i),status='HOAN_THANH',appointment_at=None if delay is None else stamp('2026-11-02T09:00'))
        step(db,p,t,'HOAN_THANH','2026-11-02T11:00')
        if delay!='no-visit':db.add(ServiceVisit(ticket_id=t.id,technician_id=p['technician'].id,started_at=stamp('2026-11-02T09:00')+timedelta(minutes=delay or 0)))
    db.commit()
    assert svc.compute(db,MONTH)[p['technician'].id]['tho_dung_hen']==50


def change(key,value,user=None,version=None):return dict(metric_key=key,target=value,user_id=user,version=version)


def test_target_priority_achievement_audit_copy_and_atomic_version(db,p):
    gd=p['director'];uid=p['technician'].id;key='tho_hoan_thanh'
    saved=svc.save_targets(db,gd,MONTH,[change(key,10),change(key,5,uid)])
    assert svc.target_for(saved,uid,key)==5
    assert svc.target_for(saved,999,key)==10
    assert svc.achievement(20,5,key)==200
    assert svc.achievement(4,10,'tho_nhan_viec')==100
    assert svc.achievement(20,10,'tho_nhan_viec')==50
    assert svc.achievement(2,None,key) is None and svc.achievement(None,5,key) is None
    assert svc.achievement(0,0,key)==100 and svc.achievement(1,0,key)==200
    assert svc.achievement(0,0,'tho_nhan_viec')==100 and svc.achievement(1,0,'tho_nhan_viec')==0
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='kpi.target'))==2
    svc.save_targets(db,gd,date(2026,10,1),[change(key,100),change('tho_nhan_viec',30)])
    svc.save_targets(db,gd,MONTH,[],copy=True)
    copied=svc.targets(db,MONTH)
    assert copied[(None,key)].target==10 and copied[(uid,key)].target==5
    assert copied[(None,'tho_nhan_viec')].target==30
    common=copied[(None,key)];own=copied[(uid,key)]
    err('VERSION_CONFLICT',svc.save_targets,db,gd,MONTH,[change(key,11,version=common.version),change(key,7,uid,version=0)])
    assert svc.targets(db,MONTH)[(None,key)].target==10
    err('BAD_TARGET',svc.save_targets,db,gd,MONTH,[change(key,-1)])
    err('BAD_TARGET',svc.save_targets,db,gd,MONTH,[change(key,1,p['director'].id)])
    err('BAD_TARGET',svc.save_targets,db,gd,MONTH,[change('kd_doanh_thu',1,uid)])
    err('FORBIDDEN',svc.save_targets,db,p['finance'],MONTH,[])
    with pytest.raises(IntegrityError):
        db.add(KpiTarget(month=MONTH,user_id=None,metric_key=key,target=1,set_by=gd.id,set_at=svc._now()));db.commit()
    db.rollback()


@pytest.mark.parametrize('role',['sales','technician','dispatcher','warehouse','finance','admin','reception'])
def test_director_routes_and_posts_forbidden(client,db,p,role):
    csrf=web_login(client,p[role])
    for url in ('/kpi/tong','/kpi/tong.csv','/kpi/muc-tieu'):
        assert client.get(url+'?thang=2026-11').status_code==403,url
    assert client.post('/kpi/muc-tieu?thang=2026-11',data={'csrf':csrf,'action':'copy'}).status_code==403
    err('FORBIDDEN',svc.board,db,p[role],MONTH)


def test_self_parameter_tampering_sensitive_metric_and_director_views(client,db,sample):
    p=sample;web_login(client,p['sales'])
    page=client.get('/kpi?thang=2026-11&user_id='+str(p['technician'].id))
    assert page.status_code==200 and 'kd_doanh_thu' in page.text and 'tho_hoan_thanh' not in page.text
    assert 'kd_lai_gop' not in page.text and 'Lãi gộp' not in page.text
    assert 'kd_lai_gop' not in svc.my_kpi(db,p['sales'],MONTH)
    assert client.get(f'/kpi/chi-tiet?thang=2026-11&user_id={p["technician"].id}&metric_key=tho_hoan_thanh').status_code==403
    assert client.get(f'/kpi/chi-tiet?thang=2026-11&user_id={p["sales"].id}&metric_key=kd_lai_gop').status_code==403
    detail=client.get(f'/kpi/chi-tiet?thang=2026-11&user_id={p["sales"].id}&metric_key=kd_doanh_thu')
    assert detail.status_code==200 and 'GH1' in detail.text and 'giá vốn' not in detail.text.lower()
    csrf=web_login(client,p['director'])
    page=client.get('/kpi/tong?thang=2026-11');assert page.status_code==200 and 'kd_lai_gop' in page.text
    assert client.get('/kpi/muc-tieu?thang=2026-11').status_code==200
    assert svc.my_kpi(db,p['director'],MONTH)=={}
    response=client.post('/kpi/muc-tieu?thang=2026-11',data={'csrf':csrf,'action':'save','target:common:kd_doanh_thu':'1.234.000'})
    assert response.status_code==200 and svc.targets(db,MONTH)[(None,'kd_doanh_thu')].target==1234000
    downloaded=client.get('/kpi/tong.csv?thang=2026-11')
    rows=list(csv.reader(io.StringIO(downloaded.content.decode('utf-8-sig'))))
    assert rows[0]==['Tháng','Mã NV','Họ tên','Nhóm','Chỉ số','Đơn vị','Thực tế','Mục tiêu','% đạt']
    sale=next(r for r in rows if r[1]=='NV-'+str(p['sales'].id) and r[4]=='Doanh thu chưa VAT')
    assert Decimal(sale[6])==1000 and Decimal(sale[7])==1234000
    # Error preserves raw number and original optimistic version.
    response=client.post('/kpi/muc-tieu?thang=2026-11',data={'csrf':csrf,'action':'save','target:common:tho_nhan_viec':'-1'})
    assert response.status_code==400 and 'value="-1"' in response.text


def test_shared_metrics_equal_and_queries_do_not_grow_with_people(client,db,sample,make_user):
    p=sample;first=svc.compute(db,MONTH)
    for key in ('kho_chenh_kiem_ke','kho_cho_xu_ly_qua_han'):
        assert first[p['warehouse'].id][key]==first[p['other'].id][key]
    web_login(client,p['director'])
    def count():
        statements=[]
        def capture(*args):statements.append(args[2])
        event.listen(db.bind,'before_cursor_execute',capture)
        try:assert client.get('/kpi/tong?thang=2026-11').status_code==200
        finally:event.remove(db.bind,'before_cursor_execute',capture)
        return len(statements)
    baseline=count()
    for i in range(20):
        user=make_user('batch.'+str(i),'warehouse','finance','technician','sales','dispatcher')
        db.add(Employee(code='NV-B'+str(i),user_id=user.id,position='Khác',start_date=date(2026,1,1),created_by=p['director'].id));db.commit()
    assert count()==baseline


def test_cutoff_historical_pending_split_and_dispatch_state(db,p,monkeypatch):
    # Historical dossier consumed AFTER November must still count in November;
    # split child created in December, retaining October entered_at, must not.
    db.add(Warehouse(code='LOI',name='Kho lỗi',kind='LOI'));db.flush()
    def dossier(number,qty,status):
        row=PendingItem(number=number,warehouse_code='LOI',product_code='TD-0001',qty_in=1,qty_left=qty,unit_cost=100,
            group_code='HANG_LOI',source='TON_CU',entered_at=stamp('2026-10-01T08:00'),status=status)
        db.add(row);db.flush();return row
    consumed=dossier('OLD',0,'XONG');child=dossier('CHILD',1,'DANG_CHO')
    before={'status':'DANG_CHO','qty_left':'1','entered_at':stamp('2026-10-01T08:00').isoformat()}
    after={**before,'qty_left':'0','status':'XONG'}
    for row,b,a in ((consumed,before,after),(child,None,before)):
        db.add(AuditLog(at=stamp('2026-12-02T08:00'),entity='pending_item',entity_id=str(row.id),action='pending_item.test',before=b,after=a))
    db.add(StockBalance(warehouse_code='LOI',product_code='TD-0001',qty=1,value=100,avg_cost=100))
    t=ticket(db,p,status='HOAN_THANH',at='2026-11-20T08:00',due_at=stamp('2026-11-30T08:00'))
    step(db,p,t,'DANG_LAM','2026-11-20T09:00')
    step(db,p,t,'HOAN_THANH','2026-12-02T09:00')
    db.commit()
    rows=svc.compute(db,MONTH)
    assert rows[p['warehouse'].id]['kho_cho_xu_ly_qua_han']==1
    detail=svc.detail(db,p['warehouse'],MONTH,p['warehouse'].id,'kho_cho_xu_ly_qua_han');assert [r.number for r in detail]==['OLD']
    assert rows[p['dispatcher'].id]['dp_ton_dong']==1 and rows[p['dispatcher'].id]['dp_tre_hen']==1
    monkeypatch.setattr(svc,'_now',lambda:stamp('2026-11-21T09:00'))
    assert svc.compute(db,MONTH)[p['dispatcher'].id]['dp_tre_hen']==0  # future due date


def test_sales_two_month_shipments_next_month_returns_exact_existing_margin(client,db,p,monkeypatch):
    from app import sales_orders, sales_returns, stock_docs, stock_posting
    from app.schemas import SalesOrderIn, SalesReturnIn, StockDocIn
    stock=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code='K58',supplier_code='NCC-0001',doc_date=date(2026,10,1),lines=[dict(product_code='TD-0001',qty=5,unit_cost=12345)]))
    stock_posting.post(db,p['warehouse'],stock.id,stock.version)
    o=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=5,unit_price=20000,vat_rate=8)]))
    sales_orders.confirm(db,p['sales'],o.id,o.version)
    for qty,when in ((2,'2026-10-31T23:50'),(3,'2026-11-01T00:10')):
        sales_orders.deliver_in_tx(db,p['warehouse'],o.id,o.version,[dict(order_line_no=1,qty=qty)],_stamp=stamp(when));db.commit()
    gh=sales_orders.deliveries(db,o.id)[0]
    monkeypatch.setattr(sales_returns,'_now',lambda:stamp('2026-11-02T08:00'))
    sales_returns.create(db,p['sales'],o.id,SalesReturnIn(version=o.version,reason='Khách trả lại',lines=[dict(delivery_id=gh.id,order_line_no=1,qty=1,condition='TOT')]))
    for month,revenue,cost in ((date(2026,10,1),40000,24690),(MONTH,40000,24690)):
        actual=svc.compute(db,month)[p['sales'].id]
        assert actual['kd_doanh_thu']==revenue and actual['kd_lai_gop']==revenue-cost
        existing=purchase_reports.gross_margin(db,p['director'],month,svc.month_end(month))['totals']
        assert actual['kd_doanh_thu']==existing['revenue'] and actual['kd_lai_gop']==existing['profit']
    web_login_result=svc.my_kpi(db,p['sales'],MONTH)
    assert 'kd_lai_gop' not in web_login_result
    web_login(client,p['sales']);html=client.get('/kpi?thang=2026-11').text
    assert 'kd_lai_gop' not in html and '24.690' not in html and '12.345' not in html and '15.310' not in html
    web_login(client,p['director']);html=client.get('/kpi/tong?thang=2026-11').text
    assert 'kd_lai_gop' in html and '15.310' in html
    assert all('cost' not in r for r in svc.detail(db,p['sales'],MONTH,p['sales'].id,'kd_doanh_thu'))


def test_void_documents_other_month_and_receipt_status_exclusions(db,sample):
    p=sample
    t=ticket(db,p,'VOID',status='DA_HUY');step(db,p,t,'HOAN_THANH','2026-11-02T12:00')
    old=ticket(db,p,'PAST',status='HOAN_THANH');step(db,p,old,'HOAN_THANH','2026-10-31T23:59')
    o=db.scalar(select(SalesOrder));db.add(SalesDelivery(number='VOID-GH',order_id=o.id,delivered_at=stamp('2026-11-04T09:00'),delivered_by=p['warehouse'].id,subtotal=999999,vat_total=0,total=999999,status='DA_HUY'))
    db.add(SalesDelivery(number='FUTURE-GH',order_id=o.id,delivered_at=stamp('2026-12-01T00:00'),delivered_by=p['warehouse'].id,subtotal=999999,vat_total=0,total=999999,status='DA_GIAO'))
    db.commit();actual=svc.compute(db,MONTH)
    assert actual[p['technician'].id]['tho_hoan_thanh']==1
    assert actual[p['technician'].id]['tho_tien_thu']==500
    assert actual[p['sales'].id]['kd_doanh_thu']==1000


def test_current_month_stops_today_and_vietnam_day_close_boundary(db,p,monkeypatch):
    monkeypatch.setattr(svc,'_now',lambda:stamp('2026-11-03T09:00'))
    # 09:59 next morning is on time; exactly 10:00 is late. Sunday excluded.
    db.add_all([DayClosing(day=date(2026,11,2),status='DA_CHOT',closed_by=p['finance'].id,closed_at=stamp('2026-11-03T08:59')),
        DayClosing(day=date(2026,11,3),status='DA_CHOT',closed_by=p['finance'].id,closed_at=stamp('2026-11-04T10:00'))])
    db.commit()
    assert svc.compute(db,MONTH)[p['finance'].id]['kt_chot_so_dung_ngay']==50
    monkeypatch.setattr(svc,'_now',lambda:stamp('2026-11-04T12:00'))
    assert svc.compute(db,MONTH)[p['finance'].id]['kt_chot_so_dung_ngay']==pytest.approx(Decimal(100)/3)


def test_acceptance_uses_own_actor_and_stops_at_reassignment(db,p):
    t=ticket(db,p)
    step(db,p,t,'DA_GIAO','2026-11-02T08:00',actor='dispatcher',assignee_id=p['technician'].id)
    step(db,p,t,'DA_NHAN','2026-11-02T08:05',actor='other')  # somebody else cannot accept for this KPI
    step(db,p,t,'DA_GIAO','2026-11-02T08:10',actor='dispatcher',assignee_id=p['other'].id)
    step(db,p,t,'DA_NHAN','2026-11-02T08:15')
    step(db,p,t,'DA_GIAO','2026-11-02T09:00',actor='dispatcher',assignee_id=p['technician'].id)
    step(db,p,t,'DA_NHAN','2026-11-02T09:30')
    step(db,p,t,'DA_GIAO','2026-11-02T10:00',actor='dispatcher',assignee_id=p['technician'].id)
    step(db,p,t,'DA_NHAN','2026-11-02T10:10')
    db.commit()
    assert svc.compute(db,MONTH)[p['technician'].id]['tho_nhan_viec']==20


def test_receipt_prepaid_then_completed_counts_once_for_latest_completer(db,p):
    t=ticket(db,p,status='HOAN_THANH')
    step(db,p,t,'HOAN_THANH','2026-11-05T11:00',actor='other')
    step(db,p,t,'HOAN_THANH','2026-11-06T11:00')
    receipt(db,p,ticket_id=t.id,received_at=date(2026,11,2))
    db.commit()
    assert svc.compute(db,MONTH)[p['technician'].id]['tho_tien_thu']==500


def test_finance_same_vietnam_day_across_utc_midnight_and_month(db,p):
    t=ticket(db,p)
    receipt(db,p,ticket_id=t.id,reported_at=stamp('2026-11-30T06:50'),decided_at=stamp('2026-11-30T23:50'))
    receipt(db,p,'BT-DEC',ticket_id=t.id,reported_at=stamp('2026-11-30T23:55'),decided_at=stamp('2026-12-01T00:00'))
    db.commit()
    assert svc.compute(db,MONTH)[p['finance'].id]['kt_xac_nhan_trong_ngay']==100


def test_closed_customer_reschedule_is_not_open_backlog_and_reads_never_write(db,p):
    t=ticket(db,p,status='KHACH_HEN_SAU',at='2026-11-01T08:00')
    step(db,p,t,'KHACH_HEN_SAU','2026-11-02T08:00');db.commit()
    statements=[]
    def capture(*args):statements.append(args[2].strip().lower())
    event.listen(db.bind,'before_cursor_execute',capture)
    try:
        rows=svc.board(db,p['director'],MONTH)
        svc.my_kpi(db,p['sales'],MONTH)
        svc.detail(db,p['technician'],MONTH,p['technician'].id,'tho_hoan_thanh')
    finally:event.remove(db.bind,'before_cursor_execute',capture)
    assert rows[p['dispatcher'].id]['dp_ton_dong']==0
    assert not any(q.startswith(('insert','update','delete','alter','create')) for q in statements)


def test_money_aggregate_keeps_numeric_precision(db,p):
    o=SalesOrder(number='BIG',customer_code='KTD-000001',created_by=p['sales'].id,status='DA_GIAO');db.add(o);db.flush()
    amount=9007199254740993  # greater than double precision's exact integer range
    db.add(SalesDelivery(number='BIG-GH',order_id=o.id,delivered_at=stamp('2026-11-02T09:00'),delivered_by=p['warehouse'].id,subtotal=amount,vat_total=0,total=amount,status='DA_GIAO'));db.commit()
    actual=svc.compute(db,MONTH)[p['sales'].id]
    assert actual['kd_doanh_thu']==amount and actual['kd_lai_gop']==amount


def test_reopened_ticket_is_late_at_cutoff_despite_an_earlier_completion(db,p):
    t=ticket(db,p,status='DA_HEN',at='2026-11-01T08:00',due_at=stamp('2026-11-08T08:00'))
    step(db,p,t,'HOAN_THANH','2026-11-07T08:00')
    step(db,p,t,'DA_HEN','2026-11-09T08:00');db.commit()
    actual=svc.compute(db,MONTH)[p['dispatcher'].id]
    assert actual['dp_tre_hen']==1 and actual['dp_ton_dong']==1

"""NS4: cumulative receipts/returns/warranty, immutable snapshots, permissions, and no posting."""
import csv
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import commissions as svc, kpi, purchase_reports, sales_receipts
from app.errors import BusinessError
from app.models import (AuditLog, CommissionRule as Rule, CommissionMonth as Month, CommissionLine as Line,
    CashDoc, FundLedger, SalesOrder, SalesOrderLine, SalesDelivery, SalesDeliveryLine, SalesReceipt, SalesRefund,
    SalesReturn, SalesReturnLine, ServiceLine, ServiceStep, ServiceTicket, StockDoc, StockLedger, User)
from test_ns3_kpi import p, purchase_people, stamp, err
from test_tk_tai_khoan import web_login

JAN=date(2027,1,1);FEB=date(2027,2,1)


@pytest.fixture
def people(p,monkeypatch):
    current={'at':stamp('2027-03-01T12:00')}
    monkeypatch.setattr(svc,'_now',lambda:current['at']);monkeypatch.setattr(kpi,'_now',lambda:current['at'])
    p['clock']=current;return p


def rule(db,p,target='KINH_DOANH',**kw):
    data=dict(target=target,pct=10,fixed_amount=30000 if target=='THO' else 0,effective_from='2027-01-01',reason='Quy tắc thử nghiệm')
    data.update(kw);return svc.add_rule(db,p['director'],data)


def order(db,p,number='DB1',day='2027-01-02T08:00',revenue=2000000,cost=1000000):
    o=SalesOrder(number=number,customer_code='KTD-000001',created_by=p['sales'].id,created_at=stamp(day),status='DA_GIAO',
                 subtotal=revenue,vat_total=revenue*8//100,total=revenue*108//100,receivable=revenue*108//100)
    db.add(o);db.flush()
    db.add(SalesOrderLine(order_id=o.id,line_no=1,kind='HANG',product_code='TD-0001',warehouse_code='K58',description='Máy',qty=10,unit='cái',unit_price=revenue//10,vat_rate=8,amount=revenue,vat_amount=revenue*8//100));db.flush()
    d=StockDoc(number='PX-'+number,kind='PX',warehouse_code='K58',doc_date=stamp(day).date(),created_by=p['warehouse'].id,status='DA_GHI',posted_at=stamp(day))
    db.add(d);db.flush()
    db.add(StockLedger(warehouse_code='K58',product_code='TD-0001',doc_id=d.id,line_no=1,qty_in=0,qty_out=10,unit_cost=cost//10,amount=-cost,balance_qty=0,balance_value=0))
    g=SalesDelivery(number='GH-'+number,order_id=o.id,delivered_by=p['warehouse'].id,delivered_at=stamp(day),subtotal=revenue,vat_total=revenue*8//100,total=o.total,status='DA_GIAO')
    db.add(g);db.flush()
    db.add(SalesDeliveryLine(delivery_id=g.id,order_id=o.id,order_line_no=1,qty=10,amount=revenue,vat_amount=revenue*8//100,warehouse_code='K58',stock_doc_id=d.id));db.commit();return o,g


def receipt(db,p,source,amount,day='2027-01-31T23:50',status='DA_XAC_NHAN',received=None):
    n=db.scalar(select(func.count()).select_from(SalesReceipt))+1
    r=SalesReceipt(number='BT-'+str(n),order_id=source.id if isinstance(source,SalesOrder) else None,
        ticket_id=source.id if isinstance(source,ServiceTicket) else None,amount=amount,method='TIEN_MAT',status=status,
        received_at=received or stamp(day).date(),reported_by=p['sales'].id,reported_at=stamp(day)-timedelta(minutes=5),decided_by=p['finance'].id,decided_at=stamp(day))
    db.add(r);db.commit();return r


def ticket(db,p,number='DV1',day='2027-01-02T08:00',free=False,**kw):
    t=ServiceTicket(number=number,customer_code='KTD-000001',kind=kw.pop('kind','SUA_TAI_CHO'),symptom='Kẹt giấy',
        status='HOAN_THANH',assignee_id=p['technician'].id,created_by=p['dispatcher'].id,created_at=stamp(day)-timedelta(hours=1),
        billing='MIEN_PHI' if free else 'THU_TIEN',free_reason='Miễn phí theo bảo hành' if free else '',
        subtotal=500000,vat_total=40000,total=540000,priced_at=stamp(day),receivable=0 if free else 540000,**kw)
    db.add(t);db.flush()
    db.add(ServiceStep(ticket_id=t.id,action='transition',to_status='HOAN_THANH',actor_id=p['technician'].id,at=stamp(day)))
    db.add(ServiceLine(ticket_id=t.id,line_no=1,kind='PHI',description='Tiền công',qty=1,unit_price=500000,vat_rate=8,amount=500000,vat_amount=40000))
    db.commit();return t


def rows(db,month=JAN):return [r for r in svc.draft(db,month) if r['amount']]


def test_partial_confirmed_receipts_then_next_month_and_immutable_snapshot(db,people):
    p=people;rule(db,p);o,g=order(db,p)
    receipt(db,p,o,1080000)
    receipt(db,p,o,100000,status='CHO_XAC_NHAN');receipt(db,p,o,100000,status='TU_CHOI')
    line=rows(db)[0];assert line['base']==1000000 and line['paid_ratio']==Decimal('.5') and line['amount']==50000
    assert svc.month_total(db,p['sales'].id,JAN)==0
    svc.lock(db,p['director'],JAN);assert svc.month_total(db,p['sales'].id,JAN)==50000
    receipt(db,p,o,1080000,'2027-02-02T08:00')
    assert rows(db,FEB)[0]['amount']==50000
    assert rows(db,JAN)[0]['amount']==50000
    assert db.scalar(select(func.count()).select_from(FundLedger))==0 and db.scalar(select(func.count()).select_from(CashDoc))==0


def test_technician_fees_fixed_and_free(db,people):
    p=people;rule(db,p,'THO');t=ticket(db,p);receipt(db,p,t,540000)
    free=ticket(db,p,'DV-FREE',free=True)
    actual={r['ticket_id']:r for r in rows(db)}
    assert actual[t.id]['amount']==80000 and actual[free.id]['amount']==30000


def returned(db,p,o,g,day='2027-02-10T08:00'):
    d=StockDoc(number='PN-RETURN',kind='PN',warehouse_code='K58',doc_date=stamp(day).date(),created_by=p['warehouse'].id,status='DA_GHI',posted_at=stamp(day));db.add(d);db.flush()
    db.add(StockLedger(warehouse_code='K58',product_code='TD-0001',doc_id=d.id,line_no=1,qty_in=5,qty_out=0,unit_cost=100000,amount=500000,balance_qty=5,balance_value=500000))
    r=SalesReturn(number='TH1',order_id=o.id,returned_at=stamp(day),created_by=p['sales'].id,reason='Khách trả nửa đơn',status='DA_NHAN',subtotal=1000000,vat_total=80000,total=1080000);db.add(r);db.flush()
    db.add(SalesReturnLine(return_id=r.id,line_no=1,order_id=o.id,order_line_no=1,delivery_id=g.id,qty=5,condition='TOT',warehouse_code='K58',amount=1000000,vat_amount=80000,unit_cost=100000,cost_amount=500000,stock_doc_id=d.id));db.commit();return r


def test_return_next_month_and_net_paid_refund_match_existing_sources(db,people):
    p=people;rule(db,p);o,g=order(db,p);receipt(db,p,o,2160000);svc.lock(db,p['director'],JAN)
    assert svc.month_total(db,p['sales'].id,JAN)==100000
    returned(db,p,o,g)
    db.add(SalesRefund(number='HT1',order_id=o.id,amount=1080000,method='TIEN_MAT',reason='Hoàn phần trả',status='DA_CHI',requested_by=p['sales'].id,decided_by=p['finance'].id,decided_at=stamp('2027-02-11T08:00')));db.commit()
    line=rows(db,FEB)[0]
    assert line['amount']==-50000 and line['paid_ratio']==1 and line['base']==500000 and line['note']=='Hàng trả'
    existing=purchase_reports.gross_margin(db,p['director'],JAN,date(2027,2,28))
    assert existing['totals']['profit']==line['base']
    current=sales_receipts.totals();assert db.scalar(select(current.c.paid).where(current.c.parent_id==o.id))==1080000
    assert svc.month_total(db,p['sales'].id,JAN)==100000
    svc.lock(db,p['director'],FEB);assert svc.month_total(db,p['sales'].id,FEB)==-50000


def test_late_confirmation_is_recognized_next_month(db,people):
    p=people;rule(db,p);o,g=order(db,p)
    receipt(db,p,o,2160000,day='2027-02-01T00:01',received=date(2027,1,31))
    assert rows(db,JAN)==[];svc.lock(db,p['director'],JAN)
    assert rows(db,FEB)[0]['amount']==100000


@pytest.mark.parametrize('days,expected',[(89,-80000),(90,-80000),(91,0)])
def test_warranty_90_day_boundary(db,people,days,expected):
    p=people;p['clock']['at']=stamp('2027-06-01T12:00');rule(db,p,'THO');t=ticket(db,p);receipt(db,p,t,540000)
    svc.lock(db,p['director'],JAN)
    at=stamp('2027-01-02T08:00')+timedelta(days=days)
    w=ServiceTicket(number='DV-BH',customer_code=t.customer_code,kind='BAO_HANH',symptom='Lỗi lại',status='MOI',created_by=p['dispatcher'].id,
        created_at=at,warranty_of_id=t.id,warranty_basis='PHIEU_TRUOC',billing='MIEN_PHI',free_reason='Bảo hành phiếu cũ')
    db.add(w);db.commit();month=at.date().replace(day=1)
    original=next((r for r in rows(db,month) if r['ticket_id']==t.id),None)
    assert (original['amount'] if original else 0)==expected
    if expected:assert original['note']=='Bảo hành lại DV-BH'


def test_canceled_warranty_restores_next_open_month_and_warranty_worker_fixed_only(db,people):
    p=people;p['clock']['at']=stamp('2027-06-01T12:00');rule(db,p,'THO');t=ticket(db,p);receipt(db,p,t,540000)
    svc.lock(db,p['director'],JAN)
    w=ticket(db,p,'DV-BH',day='2027-04-01T09:00',free=True,kind='BAO_HANH',warranty_of_id=t.id,warranty_basis='PHIEU_TRUOC')
    actual={r['ticket_id']:r for r in rows(db,date(2027,4,1))}
    assert actual[t.id]['amount']==-80000 and actual[w.id]['amount']==30000
    svc.lock(db,p['director'],FEB);svc.lock(db,p['director'],date(2027,3,1));svc.lock(db,p['director'],date(2027,4,1))
    w.status='DA_HUY';db.commit()
    restored={r['ticket_id']:r for r in rows(db,date(2027,5,1))}
    assert restored[t.id]['amount']==80000 and restored[w.id]['amount']==-30000
    assert svc.month_total(db,p['technician'].id,JAN)==80000


def test_rule_priority_effective_date_missing_and_replacement_delta(db,people):
    p=people;t=ticket(db,p);receipt(db,p,t,540000)
    missing=svc.draft(db,JAN);assert missing[0]['amount']==0 and missing[0]['note']=='Chưa có quy tắc'
    r0=rule(db,p,'THO',pct=0,fixed_amount=10000)
    assert rows(db)[0]['amount']==10000
    r1=rule(db,p,'THO',service_kind='SUA_TAI_CHO',pct=0,fixed_amount=20000)
    assert rows(db)[0]['amount']==20000
    r2=rule(db,p,'THO',user_id=p['technician'].id,pct=0,fixed_amount=30000)
    assert rows(db)[0]['amount']==30000
    r3=rule(db,p,'THO',user_id=p['technician'].id,service_kind='SUA_TAI_CHO',pct=0,fixed_amount=40000)
    assert rows(db)[0]['amount']==40000
    rule(db,p,'THO',user_id=p['technician'].id,service_kind='SUA_TAI_CHO',pct=0,fixed_amount=90000,effective_from='2027-01-03')
    assert rows(db)[0]['amount']==40000  # source completion Jan 2
    svc.lock(db,p['director'],JAN)
    replacement=svc.replace_rule(db,p['director'],r3.id,dict(target='THO',user_id=p['technician'].id,service_kind='SUA_TAI_CHO',pct=0,fixed_amount=50000,effective_from='2027-01-01',reason='Thay mức cố định'))
    assert replacement.replaces_id==r3.id and rows(db,FEB)[0]['amount']==10000
    assert svc.month_total(db,p['technician'].id,JAN)==40000
    err('RULE_REPLACED',svc.replace_rule,db,p['director'],r3.id,dict(target='THO',pct=0,fixed_amount=1,effective_from='2027-01-01',reason='Bản cũ'))
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='commission.rule'))==6


def test_sales_rule_uses_order_creation_day_and_negative_margin_zero(db,people):
    p=people;rule(db,p);rule(db,p,pct=50,effective_from='2027-01-03')
    o,g=order(db,p);receipt(db,p,o,2160000)
    assert rows(db)[0]['amount']==100000
    bad,g=order(db,p,'LOSS',revenue=500000,cost=1000000);receipt(db,p,bad,540000)
    assert len(rows(db))==1
    before,g=order(db,p,'BEFORE',day='2026-12-31T23:50');receipt(db,p,before,2160000)
    assert len(rows(db))==1


def test_lock_guards_and_adjustments_append_only(db,people):
    p=people;p['clock']['at']=stamp('2027-02-05T08:00')
    err('THANG_CHUA_QUA',svc.lock,db,p['director'],FEB)
    p['clock']['at']=stamp('2027-03-05T08:00')
    err('THANG_TRUOC_CHUA_KHOA',svc.lock,db,p['director'],FEB)
    err('REASON_REQUIRED',svc.add_adjustment,db,p['director'],JAN,p['sales'].id,-50000,'')
    svc.add_adjustment(db,p['director'],JAN,p['sales'].id,-50000,'Điều chỉnh thử')
    assert rows(db)[0]['amount']==-50000 and svc.month_total(db,p['sales'].id,JAN)==0
    svc.add_adjustment(db,p['director'],JAN,p['sales'].id,50000,'Đảo điều chỉnh sai')
    locked=svc.lock(db,p['director'],JAN);assert locked.total==0
    assert err('DA_KHOA',svc.lock,db,p['director'],JAN).status==409
    err('DA_KHOA',svc.add_adjustment,db,p['director'],JAN,p['sales'].id,1,'Không được thêm')
    assert db.scalar(select(func.count()).select_from(Line))==2
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='commission.adjust'))==2
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action=='commission.lock'))==1


def test_lock_failure_rolls_back_all_generated_lines(db,people,monkeypatch):
    p=people;rule(db,p);o,g=order(db,p);receipt(db,p,o,2160000)
    original=svc._record
    def fail(session,user,action,row,before=None):
        if action=='lock':raise RuntimeError('Simulated failure after line flush')
        return original(session,user,action,row,before)
    monkeypatch.setattr(svc,'_record',fail)
    with pytest.raises(RuntimeError):svc.lock(db,p['director'],JAN)
    assert db.scalar(select(func.count()).select_from(Line))==0 and db.get(Month,JAN) is None
    assert rows(db)[0]['amount']==100000


@pytest.mark.parametrize('statement',[
    "update commission_rules set pct=20",'delete from commission_rules',
    'update commission_lines set amount=1','delete from commission_lines',
    'update commission_months set total=1','delete from commission_months',
    "insert into commission_lines (month,user_id,source,base,pct,fixed_amount,paid_ratio,entitled,previous,amount,note,created_by) select month,locked_by,'DIEU_CHINH',0,0,0,0,1,0,1,'Thêm sau khóa',locked_by from commission_months",
])
def test_database_guards_reject_raw_mutations(db,people,statement):
    p=people;rule(db,p);svc.add_adjustment(db,p['director'],JAN,p['sales'].id,10,'Thử bất biến');svc.lock(db,p['director'],JAN)
    with pytest.raises(DBAPIError):db.execute(text(statement));db.commit()
    db.rollback();assert db.get(Month,JAN).total==10 and svc.month_total(db,p['sales'].id,JAN)==10


@pytest.mark.parametrize('role',['sales','technician','warehouse','dispatcher','admin','reception'])
def test_private_routes_and_mutations_forbidden(client,db,people,role):
    p=people;csrf=web_login(client,p[role])
    for path in ('/hoa-hong/thang','/hoa-hong/thang.csv','/hoa-hong/quy-tac'):
        assert client.get(path+'?thang=2027-01').status_code==403
    for path in ('/hoa-hong/thang/khoa','/hoa-hong/thang/dieu-chinh','/hoa-hong/quy-tac'):
        assert client.post(path+'?thang=2027-01',data={'csrf':csrf}).status_code==403
    err('FORBIDDEN',svc.lock,db,p[role],JAN)
    err('FORBIDDEN',svc.add_rule,db,p[role],{})
    err('FORBIDDEN',svc.add_adjustment,db,p[role],JAN,p['sales'].id,1,'Thử quyền')


def test_mine_redacts_cost_pct_tampering_and_finance_locked_only(client,db,people):
    p=people;rule(db,p);rule(db,p,'THO');o,g=order(db,p);receipt(db,p,o,2160000);t=ticket(db,p);receipt(db,p,t,540000)
    web_login(client,p['sales']);mine=svc.mine(db,p['sales'],JAN)
    assert 'base' not in mine[0] and 'pct' not in mine[0] and mine[0]['amount']==100000
    html=client.get('/hoa-hong?thang=2027-01&user_id='+str(p['technician'].id)).text
    assert 'DB1' in html and 'DV1' not in html and '1.000.000' not in html and 'pct' not in html and 'lãi gộp' not in html.lower()
    csrf=web_login(client,p['finance']);assert client.get('/hoa-hong/thang?thang=2027-01').status_code==403
    assert client.get('/hoa-hong/thang.csv?thang=2027-01').status_code==403
    assert client.get('/hoa-hong/quy-tac').status_code==403
    svc.lock(db,p['director'],JAN)
    page=client.get('/hoa-hong/thang?thang=2027-01');assert page.status_code==200 and '100.000' in page.text and 'Khóa tháng' not in page.text and '1.000.000' not in page.text
    for path in ('/hoa-hong/thang/khoa','/hoa-hong/thang/dieu-chinh','/hoa-hong/quy-tac'):
        assert client.post(path+'?thang=2027-01',data={'csrf':csrf}).status_code==403
    assert client.get('/hoa-hong/thang').status_code==200  # default most recent locked month
    web_login(client,p['director']);page=client.get('/hoa-hong/thang?thang=2027-01');assert page.status_code==200 and '1.000.000' in page.text
    exported=client.get('/hoa-hong/thang.csv?thang=2027-01');data=list(csv.reader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert data[0]==['Tháng','Nhân viên','Nguồn','Chứng từ','Tỷ lệ đã thu','Hoa hồng','Ghi chú','Cơ sở','%','Cố định','Lũy kế','Đã khóa trước']
    assert int(data[-1][5])==180000
    assert svc.month_total(db,p['sales'].id,JAN)==100000 and svc.month_total(db,p['technician'].id,JAN)==80000
    assert not any(route.path.startswith('/hoa-hong') and any(method in getattr(route,'methods',[]) for method in ('PUT','PATCH','DELETE')) for route in __import__('app.web',fromlist=['router']).router.routes)


def test_rule_replacement_conflict_rolls_back_original_and_validation(db,people):
    p=people;r=rule(db,p);rule(db,p,effective_from='2027-01-02')
    err('RULE_EXISTS',svc.replace_rule,db,p['director'],r.id,dict(target='KINH_DOANH',pct=20,fixed_amount=0,effective_from='2027-01-02',reason='Trùng ngày'))
    assert db.get(Rule,r.id,populate_existing=True).replaced_at is None
    for data in (dict(pct=-1),dict(pct=51),dict(pct='1.001'),dict(fixed_amount=1),dict(service_kind='BAO_HANH')):
        err('BAD_RULE',rule,db,p,**data)
    err('REASON_REQUIRED',rule,db,p,reason=' ')


def test_twenty_people_does_not_increase_board_queries(client,db,people,make_user):
    p=people;rule(db,p);o,g=order(db,p);receipt(db,p,o,2160000);web_login(client,p['director'])
    def count():
        statements=[]
        def capture(*args):statements.append(args[2])
        event.listen(db.bind,'before_cursor_execute',capture)
        try:assert client.get('/hoa-hong/thang?thang=2027-01').status_code==200
        finally:event.remove(db.bind,'before_cursor_execute',capture)
        return len(statements)
    first=count()
    for i in range(20):
        u=make_user('commission.batch.'+str(i),'sales')
        other,g=order(db,p,'DB-BATCH-'+str(i));other.created_by=u.id;db.commit();receipt(db,p,other,2160000)
    assert count()==first


def test_simultaneous_locks_produce_one_snapshot(db,engine,people):
    p=people;rule(db,p);o,g=order(db,p);receipt(db,p,o,2160000);db.commit();barrier=Barrier(2);uid=p['director'].id
    def run():
        with Session(engine,expire_on_commit=False) as s:
            user=s.get(User,uid);barrier.wait()
            try:return ('ok',svc.lock(s,user,JAN).total)
            except BusinessError as exc:return (exc.code,exc.status)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:run(),range(2)))
    assert sorted(results)==[('DA_KHOA',409),('ok',100000)]
    assert db.scalar(select(func.count()).select_from(Month))==1 and db.scalar(select(func.count()).select_from(Line))==1


def test_web_signed_adjustment_rule_forms_and_tech_visibility(client,db,people):
    p=people;csrf=web_login(client,p['director'])
    data=dict(csrf=csrf,target='THO',user_id='',service_kind='',pct='10',fixed_amount='30.000',effective_from='2027-01-01',reason='Tạo bằng form')
    response=client.post('/hoa-hong/quy-tac',data=data)
    assert response.status_code==200 and '30.000' in response.text
    first=db.scalar(select(Rule));assert first.fixed_amount==30000
    response=client.post('/hoa-hong/quy-tac',data={**data,'pct':'51','reason':'Giữ dữ liệu lỗi'})
    assert response.status_code==400 and 'Giữ dữ liệu lỗi' in response.text and 'value="51"' in response.text
    page=client.get('/hoa-hong/quy-tac?thay='+str(first.id));assert page.status_code==200 and f'name="replaces_id" value="{first.id}"' in page.text
    response=client.post('/hoa-hong/quy-tac',data={**data,'replaces_id':str(first.id),'fixed_amount':'40.000'})
    assert response.status_code==200 and db.get(Rule,first.id,populate_existing=True).replaced_at is not None
    response=client.post('/hoa-hong/thang/dieu-chinh?thang=2027-01',data={'csrf':csrf,'user_id':str(p['sales'].id),'amount':'-50.000','note':'Sửa thiếu tháng trước'})
    assert response.status_code==200 and '-50.000' in response.text
    assert rows(db)[0]['amount']==-50000
    t=ticket(db,p);receipt(db,p,t,540000)
    web_login(client,p['technician']);page=client.get('/hoa-hong?thang=2027-01')
    assert page.status_code==200 and '500.000' in page.text and '40.000' in page.text and '90.000' in page.text


def test_liquidation_order_excluded_using_real_fifo_sales_flow(db,people,monkeypatch):
    from test_cxl2a_dispositions import lot as lot_fixture
    from test_cxl2b_liquidation import liquidation as liquidation_fixture,order as liquidation_order
    from test_purchase_reports import clock
    from app import sales_orders
    from app.models import PendingDisposition
    p=people;rule(db,p)
    lots=lot_fixture.__wrapped__(db,p);liquidation_fixture.__wrapped__(db,lots)
    clock(monkeypatch,stamp('2027-01-02T08:00'))
    o=liquidation_order(db,p);sales_orders.deliver(db,p['warehouse'],o.id,o.version)
    assert db.scalar(select(func.count()).select_from(PendingDisposition).where(PendingDisposition.kind=='THANH_LY'))==2
    receipt(db,p,o,o.total)
    assert rows(db)==[]


def test_half_up_rounding_collection_cap_and_no_business_posting(db,people):
    p=people;rule(db,p,pct='12.50');o,g=order(db,p,revenue=8,cost=4);receipt(db,p,o,100)
    before=[db.scalar(select(func.count()).select_from(model)) for model in (StockLedger,StockDoc,FundLedger,CashDoc)]
    line=rows(db)[0]
    assert line['amount']==1 and line['paid_ratio']==1  # 4 * 12.5% = 0.5 -> 1 VND
    svc.lock(db,p['director'],JAN)
    assert before==[db.scalar(select(func.count()).select_from(model)) for model in (StockLedger,StockDoc,FundLedger,CashDoc)]

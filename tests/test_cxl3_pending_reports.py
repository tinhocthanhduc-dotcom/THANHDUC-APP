"""CXL3: read-only reports, exact money and SQL aggregation over real workflows."""
import csv
import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, select

from app import finance_reports, pending_dispositions as dispositions, pending_items, pending_reports as svc, sales_orders, sales_returns
from app.errors import BusinessError
from app.models import PendingItem, Warehouse
from app.schemas import SalesReturnIn, RecoveredPartReceive
from test_cxl2a_dispositions import lot, people, purchase_people, make, child, counts
from test_cxl2b_liquidation import liquidation, order, request
from test_web import service_login

START, END = date(2026, 1, 1), date(2026, 12, 31)
PATH = '/bao-cao/cho-xu-ly'


def report(db, p):
    return svc.monthly(db, p['finance'], START, END)


@pytest.mark.parametrize('role,expected', [('warehouse',403),('dispatcher',403),('sales',403),
    ('technician',403),('finance',200),('director',200)])
def test_page_csv_and_core_permissions(client, db, people, role, expected):
    p=people; service_login(client,p[role])
    for suffix in ('','/tuoi-hang.csv','/thang.csv','/ncc.csv'):
        assert client.get(PATH+suffix).status_code==expected
    for func,args in ((svc.aging,()),(svc.monthly,(START,END)),(svc.supplier_summary,(START,END))):
        if expected==403:
            with pytest.raises(BusinessError) as exc:func(db,p[role],*args)
            assert exc.value.status==403
        else:func(db,p[role],*args)


def test_age_buckets_boundaries_and_dashboard_value(db,lot):
    p,item=lot; now=datetime(2026,10,21,12,tzinfo=UTC)
    samples=[(item,10,'0_30')]
    for group,days,bucket in [('CHO_BH_NCC',45,'31_60'),('THANH_LY',75,'61_90'),('CHUA_PHAN_LOAI',120,'over_90')]:
        row=pending_items.split(db,p['warehouse'],item.id,2,group,item.version)
        samples.append((row,days,bucket))
    for row,days,_ in samples:row.entered_at=now-timedelta(days=days)
    db.commit();data=svc.aging(db,p['finance'],now)
    for row,days,bucket in samples:
        cell=next(r for r in data['rows'] if r['group_code']==row.group_code)['cells'][bucket]
        assert cell==dict(count=1,qty=row.qty_left,value=row.qty_left*100)
    assert data['total']['count']==4 and data['total']['qty']==10
    assert data['total']['value']==sum(r['value'] for r in pending_items.totals(db,p['finance'],now).values())==1000
    for days,bucket in [(0,'0_30'),(30,'0_30'),(31,'31_60'),(60,'31_60'),(61,'61_90'),(90,'61_90'),(91,'over_90')]:
        item.entered_at=now-timedelta(days=days);db.commit()
        assert next(r for r in svc.aging(db,p['finance'],now)['rows'] if r['group_code']=='HANG_LOI')['cells'][bucket]['count']==1


def test_supplier_hold_uses_sent_date_only_and_unknown_date_is_visible(db,lot):
    p,item=lot;now=datetime.now(UTC);item.entered_at=now-timedelta(days=120);db.commit()
    sent=make(db,p,item,'GUI_NCC',4,to_warehouse_code='NCC',supplier_code='NCC-0001')
    held=child(db,sent);held.sent_at=now-timedelta(days=45);db.commit()
    data=svc.aging(db,p['finance'],now)
    assert sum(r['total']['qty'] for r in data['rows'])==6
    assert data['holding'][0]['cells']['31_60']==dict(count=1,qty=4,value=400)
    assert data['total']['value']==sum(r['value'] for r in pending_items.totals(db,p['finance']).values())==1000
    held.sent_at=None;db.commit()
    assert svc.aging(db,p['finance'],now)['holding'][0]['cells']['unknown']['count']==1


def test_zero_value_recovered_stock_still_has_quantities(db,lot):
    from test_recovered_parts import recover
    from test_my_work import ticket
    from app import service_recovered_parts
    p,item=lot
    db.add(Warehouse(code='REC',name='Kho Thu hồi',kind='LOI',recovered_only=True));db.commit()
    t=ticket(db,p,'DANG_LAM');recovered=recover(db,p,t)
    # Use the KT6 receipt path, including its zero-cost warehouse and dossier.
    service_recovered_parts.receive(db,p['warehouse'],recovered.id,RecoveredPartReceive(version=recovered.version,warehouse_code='REC'))
    rows=svc.aging(db,p['finance'])['rows']
    cell=next(r['total'] for r in rows if r['group_code']=='THU_HOI')
    assert cell['count']==1 and cell['qty']>0 and cell['value']==0


def test_monthly_matches_finance_and_excludes_voided_documents(db,lot):
    p,item=lot
    destroyed=make(db,p,item,'HUY',1,actor='director',reason='Vỡ')
    credit=request(db,p,item,qty=2,amount=250);dispositions.approve(db,p['finance'],credit.id,credit.version)
    repaired=make(db,p,item,'SUA_XONG',1,to_warehouse_code='K58')
    total=report(db,p)['total']
    assert total['damage_documents']==1 and total['damage_cost']==finance_reports.damage_cost(db,p['finance'],START,END)==100
    assert (total['return_cost'],total['return_amount'],total['return_vat'],total['return_total'])==(200,250,20,270)
    assert total['return_difference']==finance_reports.pending_return_difference(db,p['finance'],START,END)==50
    assert (total['restored_qty'],total['restored_cost'])==(1,100)
    dispositions.void(db,p['director'],destroyed.id,destroyed.version,'Kiểm tra lại')
    dispositions.void(db,p['finance'],credit.id,credit.version,'NCC chưa nhận')
    total=report(db,p)['total']
    assert total['damage_cost']==total['damage_documents']==total['return_total']==total['return_difference']==0
    assert total['restored_cost']==100


def test_fifo_revenue_once_returns_and_cancelled_delivery(db,liquidation):
    p,item,first,second=liquidation;o=order(db,p)
    sales_orders.deliver(db,p['warehouse'],o.id,o.version);gh=sales_orders.deliveries(db,o.id)[0]
    total=report(db,p)['total']
    assert (total['liquidation_qty'],total['liquidation_cost'],total['liquidation_revenue'],total['liquidation_profit'])==(5,500,1000,500)
    returned=sales_returns.create(db,p['warehouse'],o.id,SalesReturnIn(version=o.version,reason='Khách trả',
        lines=[dict(delivery_id=gh.id,order_line_no=1,qty=1,condition='TOT',warehouse_code='K58')]))
    total=report(db,p)['total']
    assert total['liquidation_revenue']==800 and total['liquidation_profit']==300 and total['liquidation_cost']==500
    sales_returns.void(db,p['director'],returned.id,returned.version,'Nhập sai')
    assert report(db,p)['total']['liquidation_revenue']==1000
    sales_orders.void_delivery(db,p['director'],gh.id,'Khách hẹn giao lại',gh.version)
    total=report(db,p)['total']
    assert total['liquidation_qty']==total['liquidation_cost']==total['liquidation_revenue']==total['liquidation_profit']==0


def test_vietnam_month_boundary_and_partial_period(db,lot):
    p,item=lot;r=make(db,p,item,'HUY',1,actor='director',reason='Hỏng')
    r.decided_at=datetime(2026,1,31,23,30,tzinfo=finance_reports.TZ);db.commit()
    data=svc.monthly(db,p['finance'],date(2026,1,1),date(2026,2,28))
    assert [r['damage_cost'] for r in data['rows']]==[100,0]
    r.decided_at=datetime(2026,2,1,0,30,tzinfo=finance_reports.TZ);db.commit()
    assert [r['damage_cost'] for r in svc.monthly(db,p['finance'],date(2026,1,1),date(2026,2,28))['rows']]==[0,100]
    assert svc.monthly(db,p['finance'],date(2026,2,2),date(2026,2,28))['total']['damage_cost']==0


def test_supplier_current_and_period_flows_sorted(db,lot):
    p,item=lot;sent=make(db,p,item,'GUI_NCC',6,to_warehouse_code='NCC',supplier_code='NCC-0001');held=child(db,sent)
    make(db,p,held,'NCC_TRA_VE',1,to_warehouse_code='K58')
    rejected=make(db,p,held,'NCC_KHONG_NHAN',1,to_warehouse_code='CXL',reason='NCC không nhận')
    credit=request(db,p,item,qty=1,amount=250);dispositions.approve(db,p['finance'],credit.id,credit.version)
    rows=svc.supplier_summary(db,p['finance'],START,END);r=rows[0]
    assert r['supplier_code']=='NCC-0001'
    assert (r['holding_count'],r['holding_qty'],r['holding_value'],r['oldest_sent_at'])==(1,4,400,held.sent_at)
    assert (r['returned_qty'],r['returned_cost'],r['rejected_qty'],r['rejected_cost'],r['credit_total'])==(1,100,1,100,270)
    dispositions.void(db,p['finance'],credit.id,credit.version,'Nhập nhầm')
    assert svc.supplier_summary(db,p['finance'],START,END)[0]['credit_total']==0
    assert svc.supplier_summary(db,p['finance'],date(2025,1,1),date(2025,12,31))[0]['returned_qty']==0


def test_csv_cells_match_screen_and_links_no_writes(client,db,lot):
    p,item=lot;make(db,p,item,'HUY',1,actor='director',reason='Hàng hư')
    sent=make(db,p,item,'GUI_NCC',2,to_warehouse_code='NCC',supplier_code='NCC-0001')
    service_login(client,p['finance']);before=counts(db)
    query='?from=2026-01-01&to=2026-12-31'
    response=client.get(PATH+query);assert response.status_code==200
    assert 'Tồn theo tuổi' in response.text and 'Xử lý theo tháng' in response.text and 'Theo NCC' in response.text
    assert '/cho-xu-ly?group=' in response.text and 'min_days=31' in response.text and '/cho-xu-ly?view=ncc' in response.text
    assert '100' in response.text and '900' in response.text
    tables={}
    for table in ('tuoi-hang','thang','ncc'):
        response=client.get(PATH+'/'+table+'.csv'+query)
        assert response.status_code==200 and response.content.startswith(b'\xef\xbb\xbf')
        assert response.headers['cache-control']=='private, no-store'
        tables[table]=list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert tables['tuoi-hang'][0]==['Vị trí','NCC','Nhóm','Tuổi','Số hồ sơ','Số lượng','Giá trị']
    assert Decimal(tables['tuoi-hang'][-1][-1])==svc.aging(db,p['finance'])['total']['value']==900
    assert tables['thang'][0]==['Tháng',*[label for _,label in svc.MONTH_COLUMNS]]
    assert [Decimal(v) for v in tables['thang'][-1][1:]]==[report(db,p)['total'][key] for key,_ in svc.MONTH_COLUMNS]
    assert tables['ncc'][0]==['Mã NCC','NCC',*[label for _,label in svc.SUPPLIER_COLUMNS]]
    assert tables['ncc'][1][0]=='NCC-0001' and Decimal(tables['ncc'][1][4])==200
    assert counts(db)==before
    assert client.get(PATH+'?from=invalid').status_code==400
    assert client.get(PATH+'/thang.csv?from=2026-12-31&to=2026-01-01').status_code==400


def test_aggregates_do_not_flush_pending_changes_and_query_count_is_bounded(db,lot):
    p,item=lot;before=counts(db);item.reason='Not persisted by reports';statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
    event.listen(db.bind,'before_cursor_execute',capture)
    try:
        svc.aging(db,p['finance']);report(db,p);svc.supplier_summary(db,p['finance'],START,END)
    finally:event.remove(db.bind,'before_cursor_execute',capture)
    assert len(statements)==6 and all(s.lstrip().upper().startswith(('SELECT','WITH')) for s in statements)
    assert item in db.dirty
    db.rollback();assert counts(db)==before

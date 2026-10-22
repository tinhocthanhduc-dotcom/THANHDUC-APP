from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app import (alerts as svc, attachments, cash_docs, cash_posting, config, customers, day_closings,
                 finance_mappings, purchase_orders, sales_receipts, service_charges, service_tickets,
                 stock_docs, stock_min_levels, stock_posting, supplier_debts)
from app.errors import BusinessError
from app.models import AuditLog, Customer, FundLedger, SalesReceipt, StockLedger
from app.schemas import (CashDocIn, CustomerPatch, PurchaseOrderIn, SalesReceiptReport, ServiceCharges,
                         ServiceTicketIn, StockDocIn, StockMinLevelIn)
from test_purchase_orders import purchase_people, DAY
from test_supplier_debts import m3_people
from test_purchase_reports import report_people, clock, received, sold
from test_dashboard import people, fee

NOW=datetime(2026,10,18,12,tzinfo=svc.dashboard.TZ)


def rows(db,p,kind,user='director',**kw):
    return [r for r in svc.collect(db,p[user],today=DAY,now=NOW,**kw) if r['kind']==kind]


def ticket(db,p,monkeypatch,stamp):
    clock(monkeypatch,stamp)
    return service_tickets.create(db,p['sales'],ServiceTicketIn(customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Kẹt giấy'))


def complete(db,p,t):
    tech=p['technician'];u=p['director']
    service_tickets.assign(db,u,t.id,tech.id,t.version)
    service_tickets.transition(db,tech,t.id,'DA_NHAN',t.version)
    service_tickets.transition(db,tech,t.id,'DA_HEN',t.version,appointment_at=service_tickets._now()+timedelta(days=1))
    service_tickets.transition(db,tech,t.id,'DANG_LAM',t.version)
    attachments.add(db,tech,'service_ticket',str(t.id),'SAU','sau.pdf',b'%PDF-1.4')
    service_charges.set_charges(db,tech,t.id,ServiceCharges(version=t.version,billing='THU_TIEN',
        lines=[dict(kind='PHI',description='Công sửa',qty=1,unit_price=1000,vat_rate=0)]))
    service_tickets.transition(db,tech,t.id,'HOAN_THANH',t.version)


def configure_receipts(db,p):
    finance_mappings.set_mapping(db,p['director'],'THU_TIEN_MAT',fund_code='QUY-TM',version=1)
    finance_mappings.set_mapping(db,p['director'],'DANH_MUC_BAN_HANG',category_code='THU-BH',version=1)


def report(db,p,order,amount,stamp):
    r=sales_receipts.report(db,p['sales'],order.id,SalesReceiptReport(version=order.version,method='TIEN_MAT',amount=amount,received_at=DAY))
    # reported_at là server_default, đặt timestamp lịch sử trong dữ liệu test (không sửa sổ).
    r.reported_at=stamp;db.commit()
    return r


def level(db,p,product='TD-0001',warehouse='K58',amount=5,version=0):
    return stock_min_levels.set_level(db,p['warehouse'],warehouse,product,StockMinLevelIn(min_qty=amount,version=version))


def test_service_overdue_red_not_completed_and_now_boundary(db,people,monkeypatch):
    p=people;t=ticket(db,p,monkeypatch,NOW-timedelta(days=2))
    a=rows(db,p,'DV_QUA_HAN');assert len(a)==1 and a[0]['level']=='DO' and a[0]['link']==f'/dich-vu/{t.id}'
    assert t.number in a[0]['title']
    boundary=svc.collect(db,p['director'],today=DAY,now=t.due_at)
    assert not any(r['kind']=='DV_QUA_HAN' for r in boundary)
    complete(db,p,t)
    assert rows(db,p,'DV_QUA_HAN')==[]


def test_minimum_yellow_red_missing_balance_zero_level_and_equal_not_below(db,people):
    p=people
    received(db,p,qty=3,cost=100)
    a=level(db,p)
    found=rows(db,p,'DUOI_TON_TOI_THIEU');assert len(found)==1 and found[0]['level']=='VANG' and 'tồn 3' in found[0]['detail']
    level(db,p,amount=3,version=a.version);assert rows(db,p,'DUOI_TON_TOI_THIEU')==[]
    level(db,p,amount=0,version=a.version);assert rows(db,p,'DUOI_TON_TOI_THIEU')==[]
    # Hai mã chưa từng nhập: không có balance cũng là tồn 0, DO.
    level(db,p,product='TD-0002',amount=2)
    found=rows(db,p,'DUOI_TON_TOI_THIEU');assert len(found)==1 and found[0]['level']=='DO'
    assert found[0]['link']=='/the-kho/K58/TD-0002'
    level(db,p,amount=5,version=a.version)
    out=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PX',warehouse_code='K58',doc_date=DAY,lines=[dict(product_code='TD-0001',qty=3)]))
    stock_posting.post(db,p['warehouse'],out.id,out.version)
    assert all(r['level']=='DO' for r in rows(db,p,'DUOI_TON_TOI_THIEU'))


def test_pending_receipt_age_today_confirmed_and_config(db,people,monkeypatch):
    p=people;configure_receipts(db,p);o=fee(db,p,1000000)
    old=report(db,p,o,100000,NOW-timedelta(days=2))
    report(db,p,o,100000,NOW)
    found=rows(db,p,'BAO_THU_CHO');assert len(found)==1 and old.number in found[0]['title'] and found[0]['level']=='VANG'
    assert found[0]['link']=='/bao-thu'
    monkeypatch.setattr(config,'ALERT_RECEIPT_PENDING_DAYS',3);assert rows(db,p,'BAO_THU_CHO')==[]
    monkeypatch.setattr(config,'ALERT_RECEIPT_PENDING_DAYS',1)
    sales_receipts.confirm(db,p['finance'],old.id,old.version)
    assert rows(db,p,'BAO_THU_CHO')==[]


def test_customer_limit_allowed_debt_and_no_debt_even_high_limit(db,people):
    p=people;c=db.get(Customer,'KTD-000001')
    customers.update(db,p['finance'],c.code,CustomerPatch(version=c.version,phones=['0900000001'],allow_debt=True,credit_limit=200000))
    fee(db,p,100000)
    assert rows(db,p,'KHACH_VUOT_HAN_MUC')==[]
    customers.update(db,p['finance'],c.code,CustomerPatch(version=c.version,credit_limit=100000))
    found=rows(db,p,'KHACH_VUOT_HAN_MUC');assert len(found)==1 and found[0]['level']=='DO' and '110.000' in found[0]['detail']
    customers.update(db,p['finance'],c.code,CustomerPatch(version=c.version,allow_debt=False,credit_limit=999999))
    assert 'không được nợ' in rows(db,p,'KHACH_VUOT_HAN_MUC')[0]['detail']


@pytest.mark.parametrize('days,severity',[(10,None),(30,None),(31,'VANG'),(45,'VANG'),(60,'VANG'),(70,'DO')])
def test_customer_debt_age_reuses_sales_aging_and_paid_disappears(db,people,monkeypatch,days,severity):
    p=people;configure_receipts(db,p)
    clock(monkeypatch,NOW-timedelta(days=days));o=fee(db,p,100000)
    # Khoản cũ có hạn 30 ngày theo migration S3; giữ cùng ngưỡng cảnh báo cũ.
    o.payment_term='CONG_NO';o.due_days=30;db.commit()
    found=rows(db,p,'KHACH_NO_QUA_HAN')
    assert (found[0]['level'] if found else None)==severity
    r=report(db,p,o,o.receivable,NOW)
    sales_receipts.confirm(db,p['finance'],r.id,r.version)
    assert rows(db,p,'KHACH_NO_QUA_HAN')==[] and rows(db,p,'KHACH_VUOT_HAN_MUC')==[]


def test_supplier_debt_fifo_age_paid_disappears_and_configurable_threshold(db,people,monkeypatch):
    p=people;received(db,p,cost=200000,vat=0,day=DAY-timedelta(days=40))
    found=rows(db,p,'NCC_NO_QUA_HAN');assert len(found)==1 and found[0]['level']=='VANG' and '200.000' in found[0]['detail']
    monkeypatch.setattr(config,'ALERT_DEBT_DAYS',45);assert rows(db,p,'NCC_NO_QUA_HAN')==[]
    monkeypatch.setattr(config,'ALERT_DEBT_DAYS',30)
    received(db,p,cost=300000,vat=0,day=DAY-timedelta(days=5))
    supplier_debts.pay(db,p['finance'],'NCC-0001','QUY-TM',200000,DAY)
    assert rows(db,p,'NCC_NO_QUA_HAN')==[] # Tiền trả trừ khoản 40 ngày trước, nợ mới vẫn còn.
    supplier_debts.pay(db,p['finance'],'NCC-0001','QUY-TM',300000,DAY)
    assert rows(db,p,'NCC_NO_QUA_HAN')==[]


def test_late_purchase_order_excludes_fully_received_cancelled_and_null_expected(db,people):
    p=people
    def new(expected):
        o=purchase_orders.create(db,p['warehouse'],PurchaseOrderIn(supplier_code='NCC-0001',order_date=DAY,
            expected_date=expected,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=2,unit_cost=100)]))
        return purchase_orders.order(db,p['warehouse'],o.id,o.version)
    o=new(DAY-timedelta(days=1));found=rows(db,p,'DON_MUA_TRE')
    assert len(found)==1 and found[0]['link']==f'/don-mua/{o.id}'
    purchase_orders.cancel(db,p['warehouse'],o.id,'NCC ngừng giao',o.version)
    assert rows(db,p,'DON_MUA_TRE')==[]
    from app import purchase_receipts
    o=new(DAY-timedelta(days=2));purchase_receipts.receive(db,p['warehouse'],o.id,o.version,DAY,[dict(order_line_id=o.lines[0].id,qty=1)])
    assert len(rows(db,p,'DON_MUA_TRE'))==1
    purchase_receipts.receive(db,p['warehouse'],o.id,o.version,DAY,[dict(order_line_id=o.lines[0].id,qty=1)])
    new(None);new(DAY)
    assert rows(db,p,'DON_MUA_TRE')==[]


def test_unclosed_days_only_posted_prior_days_and_closed_or_unlocked(db,people,monkeypatch):
    p=people;yesterday=DAY-timedelta(days=1)
    for day in (yesterday,DAY,DAY-timedelta(days=8)):
        doc=cash_docs.create(db,p['finance'],CashDocIn(kind='PT',doc_date=day,fund_code='QUY-TM',category_code='THU-BH',amount=100))
        cash_posting.post(db,p['finance'],doc.id,doc.version)
    found=rows(db,p,'CHUA_CHOT_SO');assert len(found)==1 and '17/10/2026' in found[0]['title']
    closing=day_closings.close(db,p['finance'],yesterday,0);assert rows(db,p,'CHUA_CHOT_SO')==[]
    day_closings.unlock(db,p['director'],yesterday,'Kiểm tra lại',closing.version)
    assert len(rows(db,p,'CHUA_CHOT_SO'))==1
    monkeypatch.setattr(config,'ALERT_CLOSING_LOOKBACK_DAYS',1)
    assert len(rows(db,p,'CHUA_CHOT_SO'))==1


def test_slow_stock_old_no_recent_output_new_or_recent_output_not_alert_void_output_alert_again(db,people,monkeypatch):
    p=people;old=DAY-timedelta(days=100)
    received(db,p,qty=3,cost=100,day=old,vat=0)
    found=rows(db,p,'CHAM_LUAN_CHUYEN');assert len(found)==1 and '300 đồng' in found[0]['detail']
    assert found[0]['link']=='/the-kho/K58/TD-0001'
    monkeypatch.setattr(config,'ALERT_SLOW_STOCK_DAYS',120);assert rows(db,p,'CHAM_LUAN_CHUYEN')==[]
    monkeypatch.setattr(config,'ALERT_SLOW_STOCK_DAYS',90)
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PX',warehouse_code='K58',doc_date=DAY-timedelta(days=5),lines=[dict(product_code='TD-0001',qty=1)]))
    stock_posting.post(db,p['warehouse'],doc.id,doc.version);assert rows(db,p,'CHAM_LUAN_CHUYEN')==[]
    stock_posting.void(db,p['warehouse'],doc.id,'Hủy xuất thử',doc.version)
    assert len(rows(db,p,'CHAM_LUAN_CHUYEN'))==1
    received(db,p,product='TD-0002',cost=100,day=DAY-timedelta(days=10))
    assert len(rows(db,p,'CHAM_LUAN_CHUYEN'))==1


def test_per_kind_cap_counts_global_order_and_oldest_twenty(db,people,monkeypatch):
    p=people
    for n in range(25):ticket(db,p,monkeypatch,NOW-timedelta(days=n+2))
    found=svc.collect(db,p['director'],DAY,NOW)
    assert isinstance(found,list) and found.counts['DV_QUA_HAN']==25
    only=[a for a in found if a['kind']=='DV_QUA_HAN'];assert len(only)==20
    assert only[0]['link']=='/dich-vu/25' and only[-1]['link']=='/dich-vu/6'
    assert all(set(r)=={'level','kind','title','detail','link'} for r in found)
    received(db,p,qty=3,cost=100)
    level(db,p,amount=5)
    found=svc.collect(db,p['director'],DAY,NOW)
    assert any(r['level']=='VANG' for r in found)
    assert [(r['level'],r['kind']) for r in found]==sorted((r['level'],r['kind']) for r in found)


def test_permissions_finance_never_queries_or_counts_slow_stock(db,people):
    p=people;received(db,p,day=DAY-timedelta(days=100),cost=100)
    assert len(rows(db,p,'CHAM_LUAN_CHUYEN'))==1
    for role in ('sales','warehouse','technician'):
        with pytest.raises(BusinessError) as exc:svc.collect(db,p[role],DAY,NOW)
        assert exc.value.code=='FORBIDDEN' and exc.value.status==403
    found=svc.collect(db,p['finance'],DAY,NOW)
    assert 'CHAM_LUAN_CHUYEN' not in found.counts and not any(a['kind']=='CHAM_LUAN_CHUYEN' for a in found)


def test_collect_read_only_does_not_autoflush_or_change_audit_or_ledgers(db,engine,people):
    p=people;received(db,p,day=DAY-timedelta(days=100),cost=100)
    before=[db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]
    sql=[];loaded=[]
    def capture(conn,cursor,statement,parameters,context,executemany):sql.append(statement)
    with Session(engine) as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        event.listen(session,'loaded_as_persistent',lambda s,obj:loaded.append(type(obj)))
        event.listen(engine,'before_cursor_execute',capture)
        try:svc.collect(session,p['director'],DAY,NOW)
        finally:event.remove(engine,'before_cursor_execute',capture)
    assert not loaded and all(q.lstrip().upper().startswith(('SELECT','WITH')) for q in sql)
    customer=db.get(Customer,'KTD-000001');db.expire(p['director'],['roles']);customer.name='Chưa lưu'
    svc.collect(db,p['director'],DAY,NOW);assert db.is_modified(customer);db.rollback()
    assert [db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]==before


def test_web_empty_groups_links_overflow_and_finance_hides_slow(client,db,people,monkeypatch):
    from test_web import service_login
    p=people;service_login(client,p['director'])
    page=client.get('/tong-quan');assert page.status_code==200 and 'Không có việc cần xử lý' in page.text
    for n in range(25):ticket(db,p,monkeypatch,NOW-timedelta(days=n+2))
    received(db,p,day=DAY-timedelta(days=100),cost=100)
    page=client.get('/tong-quan')
    assert page.status_code==200 and 'Cần xử lý' in page.text and 'và 5 mục khác' in page.text
    assert 'href="/dich-vu/25"' in page.text and 'data-alert-kind="CHAM_LUAN_CHUYEN"' in page.text
    assert page.text.index('overview-grid')<page.text.index('id="alerts-title"')<page.text.index('Doanh thu 30 ngày gần nhất')
    service_login(client,p['finance']);page=client.get('/tong-quan')
    assert page.status_code==200 and 'data-alert-kind="CHAM_LUAN_CHUYEN"' not in page.text


def test_credit_limit_exact_equality_and_service_debt_are_included(db,people,monkeypatch):
    p=people;c=db.get(Customer,'KTD-000001')
    customers.update(db,p['finance'],c.code,CustomerPatch(version=c.version,phones=['0900000001'],allow_debt=True,credit_limit=110000))
    fee(db,p,100000)
    assert rows(db,p,'KHACH_VUOT_HAN_MUC')==[]
    t=ticket(db,p,monkeypatch,NOW);complete(db,p,t)
    found=rows(db,p,'KHACH_VUOT_HAN_MUC')
    assert len(found)==1 and '111.000' in found[0]['detail']


def test_slow_stock_counts_opening_stock_as_first_input(db,people):
    from app import stock_opening
    p=people
    for doc in stock_opening.create_opening(db,p['director'],'K58',DAY-timedelta(days=100),[dict(MA_HANG='TD-0001',SO_LUONG='2',DON_GIA='150')]):
        stock_posting.post(db,p['director'],doc.id,doc.version)
    found=rows(db,p,'CHAM_LUAN_CHUYEN');assert len(found)==1 and '300 đồng' in found[0]['detail']

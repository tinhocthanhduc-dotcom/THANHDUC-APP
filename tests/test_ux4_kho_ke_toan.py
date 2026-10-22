import pytest
from sqlalchemy import select

from app import sales_orders, sales_receipts, finance_mappings
from app.errors import BusinessError
from app.models import SalesDelivery, StockBalance, StockReservation, CashDoc, FundLedger
from app.schemas import SalesReceiptReport
from test_sales_orders import people, stocked, body, GOOD, SECOND
from test_my_work import people as work_people, purchase_people, ticket, DAY
from test_web import service_login


def warehouse_order(db,p,**changes):
    order=sales_orders.create(db,p['sales'],body(lines=[GOOD,SECOND],contact_name='Chị Hạnh',**changes))
    sales_orders.confirm(db,p['sales'],order.id,order.version)
    return order


def delivery_data(csrf,order,**changes):
    return dict(csrf=csrf,version=order.version,delivery_form='1',return_to='kho',deliver_1='2',deliver_2='1',receiver_name='Chị Hạnh')|changes


def test_warehouse_prefill_permissions_and_work_link(client,db,stocked):
    order=warehouse_order(db,stocked)
    for role in ['sales','technician','finance']:
        service_login(client,stocked[role]);assert client.get(f'/kho/giao/{order.id}').status_code==403
    for role in ['warehouse','director']:
        service_login(client,stocked[role]);r=client.get(f'/kho/giao/{order.id}')
        assert r.status_code==200 and 'name="deliver_1" value="2"' in r.text and 'name="deliver_2" value="1"' in r.text
        assert 'name="receiver_name" value="Chị Hạnh"' in r.text and r.text.count('data-next-primary')==1
    service_login(client,stocked['warehouse'])
    assert f'href="/kho/giao/{order.id}"' in client.get('/viec-cua-toi').text


def test_warehouse_full_delivery_uses_existing_stock_and_print(client,db,stocked):
    order=warehouse_order(db,stocked);csrf=service_login(client,stocked['warehouse'])
    r=client.post(f'/don-ban/{order.id}/deliver',data=delivery_data(csrf,order),follow_redirects=False)
    assert r.status_code==303 and '/in-giao?return_to=kho' in r.headers['location']
    page=client.get(r.headers['location']);assert 'PHIẾU GIAO HÀNG' in page.text and 'Về Việc của tôi' in page.text
    db.expire_all();assert order.status=='DA_GIAO'
    assert db.get(StockBalance,('K58','TD-0001')).qty==3 and db.get(StockBalance,('K145','TD-0002')).qty==2
    assert sum(r.qty for r in db.scalars(select(StockReservation).where(StockReservation.status=='GIU')))==0
    assert db.query(SalesDelivery).count()==1


def test_warehouse_partial_then_excess_keeps_input_and_state(client,db,stocked):
    order=warehouse_order(db,stocked);csrf=service_login(client,stocked['warehouse'])
    assert client.post(f'/don-ban/{order.id}/deliver',data=delivery_data(csrf,order,deliver_1='1'),follow_redirects=False).status_code==303
    db.expire_all();assert order.status=='GIAO_MOT_PHAN' and sales_orders.remaining_lines(db,order)[1]['remaining']==1
    r=client.post(f'/don-ban/{order.id}/deliver',data=delivery_data(csrf,order,deliver_1='5',deliver_2='0',receiver_name='Người nhận vừa nhập'))
    assert r.status_code==409 and 'data-warehouse-deliver' in r.text
    assert 'name="deliver_1" value="5"' in r.text and 'Người nhận vừa nhập' in r.text
    assert db.query(SalesDelivery).count()==1


def test_technician_delivery_handover_no_warehouse_form(client,db,stocked):
    order=warehouse_order(db,stocked,fulfillment='THO_GIAO');service_login(client,stocked['warehouse'])
    html=client.get(f'/kho/giao/{order.id}').text
    # Chưa có thợ: kho thấy đang chờ điều phối, không có nút bàn giao.
    assert 'data-tech-wait' in html and 'data-next-primary' not in html and f'href="/giao-don/{order.id}"' in html and 'id="warehouse-deliver"' not in html
    order.delivery_assignee_id=stocked['technician'].id;db.commit()
    html=client.get(f'/kho/giao/{order.id}').text
    assert 'Bàn giao cho thợ' in html and 'data-next-primary' in html and 'id="warehouse-deliver"' not in html


def reports(db,p):
    rows=[]
    for amount in [100000,200000,300000]:
        t=ticket(db,p,'HOAN_THANH')
        rows.append(sales_receipts.report_for_ticket(db,p['technician'],t.id,SalesReceiptReport(version=t.version,amount=amount,method='TIEN_MAT',received_at=DAY)))
    return rows


def group_data(csrf,rows,p,amount='600.000'):
    return dict(csrf=csrf,reporter_id=p['technician'].id,receipt_id=[r.id for r in rows],counted_amount=amount,
                filter_method='TIEN_MAT',filter_from=DAY.isoformat(),filter_to=DAY.isoformat(),**{'version_'+str(r.id):r.version for r in rows})


def test_cash_queue_compact_totals_group_and_self_exclusion(client,db,work_people):
    p=work_people;rows=reports(db,p);csrf=service_login(client,p['finance'])
    page=client.get('/bao-thu?method=TIEN_MAT');assert page.text.count('data-receipt-row')==3
    assert 'Tiền mặt 600.000' in page.text and 'Chuyển khoản 0' in page.text and 'Xác nhận cả nhóm' in page.text
    for row in rows:assert f'id="receipt-confirm-{row.id}"' in page.text
    assert 'Xác nhận cả nhóm' not in client.get('/bao-thu?method=CHUYEN_KHOAN').text
    # Reporter with finance permission still cannot confirm their own receipts in a group.
    p['technician'].roles=['technician','finance'];db.commit();service_login(client,p['technician'])
    page=client.get('/bao-thu?method=TIEN_MAT')
    assert 'id="cash-confirm-' not in page.text
    for row in rows:assert f'id="receipt-confirm-{row.id}"' not in page.text
    p['technician'].roles=['technician','director'];db.commit();service_login(client,p['technician'])
    page=client.get('/bao-thu?method=TIEN_MAT')
    assert 'id="cash-confirm-' not in page.text
    for row in rows:assert f'id="receipt-confirm-{row.id}"' not in page.text


def test_cash_group_confirm_posts_three_receipts(client,db,work_people,configured_finance):
    p=work_people;rows=reports(db,p);csrf=service_login(client,p['finance'])
    r=client.post('/bao-thu/xac-nhan-nhom',data=group_data(csrf,rows,p),follow_redirects=False)
    assert r.status_code==303 and 'method=TIEN_MAT' in r.headers['location'] and '#cash-group-' in r.headers['location']
    db.expire_all();assert all(r.status=='DA_XAC_NHAN' and r.cash_doc_id for r in rows)
    assert db.query(CashDoc).count()==3 and sum(l.amount for l in db.scalars(select(FundLedger)))==600000


@pytest.mark.parametrize('amount,difference',[('550.000','thiếu 50.000'),('650.000','thừa 50.000')])
def test_count_mismatch_rolls_back_every_receipt(client,db,work_people,configured_finance,amount,difference):
    p=work_people;rows=reports(db,p);csrf=service_login(client,p['finance'])
    r=client.post('/bao-thu/xac-nhan-nhom',data=group_data(csrf,rows,p,amount))
    assert r.status_code==400 and 'CASH_COUNT_MISMATCH' in r.text and difference in r.text
    db.expire_all();assert all(r.status=='CHO_XAC_NHAN' for r in rows) and db.query(CashDoc).count()==0
    assert f'value="{amount}"' in r.text


@pytest.mark.parametrize('action',['confirm','reject'])
def test_stale_group_reports_receipt_and_changes_none(client,db,work_people,configured_finance,action):
    p=work_people;rows=reports(db,p);csrf=service_login(client,p['finance']);raw=group_data(csrf,rows,p)
    target=rows[1]
    if action=='confirm':sales_receipts.confirm(db,p['director'],target.id,target.version)
    else:sales_receipts.reject(db,p['director'],target.id,'Đã xử lý trước',target.version)
    before=db.query(CashDoc).count()
    r=client.post('/bao-thu/xac-nhan-nhom',data=raw)
    assert r.status_code==409 and 'VERSION_CONFLICT' in r.text and target.number in r.text
    db.expire_all();assert rows[0].status==rows[2].status=='CHO_XAC_NHAN' and db.query(CashDoc).count()==before


def test_new_receipt_after_snapshot_not_confirmed(client,db,work_people,configured_finance):
    p=work_people;rows=reports(db,p);csrf=service_login(client,p['finance']);raw=group_data(csrf,rows,p)
    t=ticket(db,p,'HOAN_THANH');fourth=sales_receipts.report_for_ticket(db,p['technician'],t.id,SalesReceiptReport(version=t.version,amount=50000,method='TIEN_MAT'))
    assert client.post('/bao-thu/xac-nhan-nhom',data=raw,follow_redirects=False).status_code==303
    db.expire_all();assert fourth.status=='CHO_XAC_NHAN' and all(r.status=='DA_XAC_NHAN' for r in rows)


def test_late_mapping_error_rolls_back_earlier_posts(client,db,work_people,configured_finance,monkeypatch):
    p=work_people;rows=reports(db,p);csrf=service_login(client,p['finance'])
    original=sales_receipts.confirm_in_tx;calls=[]
    def confirm(session,user,ident,version):
        calls.append(ident)
        if ident==rows[1].id:raise BusinessError('MAPPING_REQUIRED','Thiếu ghép tài chính.')
        return original(session,user,ident,version)
    monkeypatch.setattr(sales_receipts,'confirm_in_tx',confirm)
    r=client.post('/bao-thu/xac-nhan-nhom',data=group_data(csrf,rows,p))
    assert r.status_code==400 and 'MAPPING_REQUIRED' in r.text and rows[1].number in r.text
    db.expire_all();assert all(r.status=='CHO_XAC_NHAN' for r in rows)
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==0 and calls==[rows[0].id,rows[1].id]


def test_group_permission_and_transfer_rejected(client,db,work_people,configured_finance):
    p=work_people;rows=reports(db,p);csrf=service_login(client,p['sales'])
    assert client.post('/bao-thu/xac-nhan-nhom',data=group_data(csrf,rows,p)).status_code==403
    t=ticket(db,p,'HOAN_THANH');transfer=sales_receipts.report_for_ticket(db,p['technician'],t.id,SalesReceiptReport(version=t.version,amount=100000,method='CHUYEN_KHOAN'))
    csrf=service_login(client,p['finance'])
    r=client.post('/bao-thu/xac-nhan-nhom',data=group_data(csrf,[transfer],p,'100.000'))
    assert r.status_code==400 and 'BAD_GROUP' in r.text and db.query(CashDoc).count()==0


def test_actual_missing_mapping_after_first_post_rolls_back(client,db,work_people,configured_finance):
    from app.models import FinanceMapping
    from app.schemas import SalesOrderIn
    p=work_people
    order=sales_orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='KHAC',description='Công lắp',qty=1,unit_price=500000,vat_rate=0)]))
    sales_orders.confirm(db,p['sales'],order.id,order.version);sales_orders.deliver(db,p['sales'],order.id,order.version)
    first=sales_receipts.report(db,p['sales'],order.id,SalesReceiptReport(version=order.version,amount=100000,method='TIEN_MAT'))
    t=ticket(db,p,'HOAN_THANH')
    second=sales_receipts.report_for_ticket(db,p['sales'],t.id,SalesReceiptReport(version=t.version,amount=200000,method='TIEN_MAT'))
    mapping=db.get(FinanceMapping,'DANH_MUC_DICH_VU')
    finance_mappings.set_mapping(db,p['director'],mapping.key,version=mapping.version,category_code=None)
    csrf=service_login(client,p['finance'])
    raw=group_data(csrf,[first,second],p,'300.000')|dict(reporter_id=p['sales'].id)
    r=client.post('/bao-thu/xac-nhan-nhom',data=raw)
    assert r.status_code==409 and 'MAPPING_REQUIRED' in r.text and second.number in r.text
    db.expire_all();assert first.status==second.status=='CHO_XAC_NHAN' and first.cash_doc_id is None
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==0


def test_cash_group_duplicate_ids_and_self_post_rejected(client,db,work_people,configured_finance):
    p=work_people;rows=reports(db,p);csrf=service_login(client,p['finance'])
    raw=group_data(csrf,[rows[0],rows[0]],p,'200.000')
    r=client.post('/bao-thu/xac-nhan-nhom',data=raw)
    assert r.status_code==400 and 'BAD_GROUP' in r.text and db.query(CashDoc).count()==0
    p['technician'].roles=['technician','finance'];db.commit();csrf=service_login(client,p['technician'])
    r=client.post('/bao-thu/xac-nhan-nhom',data=group_data(csrf,rows,p))
    assert r.status_code==403 and 'SELF_CONFIRM' in r.text and db.query(CashDoc).count()==0


def test_purchase_receiving_link_and_remaining_default(client,db,work_people):
    from app import purchase_orders
    from app.schemas import PurchaseOrderIn
    p=work_people
    order=purchase_orders.create(db,p['warehouse'],PurchaseOrderIn(supplier_code='NCC-0001',order_date=DAY,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=3,unit_cost=100000,vat_rate=8)]))
    purchase_orders.order(db,p['warehouse'],order.id,order.version)
    service_login(client,p['warehouse']);html=client.get('/viec-cua-toi').text
    assert 'Đơn mua chờ nhận hàng' in html and f'href="/don-mua/{order.id}/nhan#nhan-hang"' in html
    html=client.get(f'/don-mua/{order.id}/nhan').text
    assert f'name="qty_{order.lines[0].id}" value="3"' in html

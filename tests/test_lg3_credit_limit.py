"""LG3: actual debt + undelivered commitments, on real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import customers, sales_orders as svc, sales_receipts, sales_returns
from app.errors import BusinessError
from app.models import (AuditLog, Customer, FundLedger, SalesOrder, StockDoc,
                        StockLedger, StockReservation, User)
from app.schemas import (CustomerPatch, SalesOrderIn, SalesOrderOptions,
                         SalesOrderUpdate, SalesReceiptReport, SalesReturnIn)
from app.security import issue_token
from app.web import picked
from test_stock_reservations import people, purchase_people, stock, fail
from test_my_work import ticket
from test_web import service_login


@pytest.fixture
def credit_customer(db, people):
    customer=db.get(Customer,'KTD-000001')
    customers.update(db,people['finance'],customer.code,CustomerPatch(
        version=customer.version,phones=['0900000001'],allow_debt=True,credit_limit=100))
    return customer


def body(amount=100,*,goods=False,qty=1,term='CONG_NO',**kw):
    line=dict(kind='HANG' if goods else 'KHAC',description='Hàng / công lắp đặt',
              qty=qty,unit_price=amount//qty,vat_rate=0)
    if goods:line.update(product_code='TD-0001',warehouse_code='K58')
    return SalesOrderIn(customer_code='KTD-000001',lines=[line],payment_term=term,
        **({'deposit_amount':1} if term=='DAT_COC' else {}),**kw)


def draft(db,p,amount=100,**kw):
    return svc.create(db,p['sales'],body(amount,**kw))


def confirmed(db,p,amount=100,**kw):
    row=draft(db,p,amount,**kw)
    return svc.confirm(db,p['sales'],row.id,row.version)


def state(db,customer):
    return svc.credit_state(db,customer,0)


def paid(db,p,order,amount,*,confirm=True):
    row=sales_receipts.report(db,p['sales'],order.id,SalesReceiptReport(
        version=order.version,method='TIEN_MAT',amount=amount))
    if confirm:sales_receipts.confirm(db,p['finance'],row.id,row.version)
    return row


def counts(db):
    return tuple(db.query(model).count() for model in
        (SalesOrder,StockDoc,StockLedger,StockReservation,FundLedger,AuditLog))


def test_second_commitment_blocked_and_director_can_override(db,people,credit_customer):
    p=people;confirmed(db,p)
    b=draft(db,p);before=counts(db)
    exc=fail('CREDIT_LIMIT',svc.confirm,db,p['sales'],b.id,b.version)
    assert exc.status==409 and exc.details==dict(remaining=100,actual_debt=0,
        committed=100,total=100,credit_limit=100,allow_debt=True)
    assert all(label in exc.message for label in ('Nợ thực tế: 0','đơn chốt chưa giao: 100',
        'đơn này: 100','hạn mức: 100'))
    assert counts(db)==before and b.status=='NHAP' and b.receivable is None
    fail('REASON_REQUIRED',svc.confirm,db,p['director'],b.id,b.version,' ')
    assert counts(db)==before
    svc.confirm(db,p['director'],b.id,b.version,'Duyệt thêm cho khách quen')
    assert b.credit_override_by==p['director'].id
    assert b.credit_override_reason=='Duyệt thêm cho khách quen'
    log=db.scalar(select(AuditLog).where(AuditLog.action=='sales_order.confirm',AuditLog.entity_id==str(b.id)))
    assert log.reason==b.credit_override_reason and log.after['credit_override_by']==p['director'].id
    # Delivery honours approval even if exposure subsequently exceeds the limit.
    svc.deliver(db,p['sales'],b.id,b.version)
    assert b.status=='DA_GIAO' and b.receivable==100
    assert state(db,credit_customer)['remaining']==200


def test_partial_delivery_counts_actual_plus_only_unshipped_and_closing_releases_it(db,people,credit_customer):
    p=people;stock(db,p,10);a=confirmed(db,p,goods=True,qty=10)
    svc.deliver(db,p['warehouse'],a.id,a.version,[dict(order_line_no=1,qty=4)])
    assert state(db,credit_customer)==dict(actual_debt=40,committed=60,remaining=100,allowed=True)
    b=draft(db,p,1);fail('CREDIT_LIMIT',svc.confirm,db,p['sales'],b.id,b.version)
    svc.close_remaining(db,p['sales'],a.id,'Khách không lấy thêm',a.version)
    assert state(db,credit_customer)['committed']==0 and a.receivable==40
    svc.confirm(db,p['sales'],b.id,b.version)
    assert state(db,credit_customer)['remaining']==41


def test_cancelled_and_draft_orders_do_not_consume_commitment(db,people,credit_customer):
    p=people;a=confirmed(db,p);draft(db,p,1000)
    svc.cancel(db,p['sales'],a.id,'Khách hủy trước giao',a.version)
    assert state(db,credit_customer)['remaining']==0
    assert confirmed(db,p).status=='DA_CHOT'


@pytest.mark.parametrize('delivered,amount,expected',[(0,100,0),(4,100,0),(4,70,30),(4,20,80)])
def test_confirmed_advance_only_offsets_its_own_order_once(db,people,credit_customer,configured_finance,delivered,amount,expected):
    p=people;stock(db,p,10);a=confirmed(db,p,goods=True,qty=10)
    if delivered:svc.deliver(db,p['warehouse'],a.id,a.version,[dict(order_line_no=1,qty=delivered)])
    paid(db,p,a,amount)
    exposure=state(db,credit_customer)
    assert exposure['actual_debt']==max(0,delivered*10-amount)
    assert exposure['committed']==max(0,100-delivered*10-max(0,amount-delivered*10))
    assert exposure['remaining']==expected
    b=draft(db,p,100-expected)
    svc.confirm(db,p['sales'],b.id,b.version)
    assert state(db,credit_customer)['remaining']==100


def test_pending_or_voided_receipt_does_not_reduce_commitment(db,people,credit_customer,configured_finance):
    p=people;a=confirmed(db,p);r=paid(db,p,a,100,confirm=False)
    b=draft(db,p)
    assert state(db,credit_customer)['committed']==100
    fail('CREDIT_LIMIT',svc.confirm,db,p['sales'],b.id,b.version)
    sales_receipts.confirm(db,p['finance'],r.id,r.version)
    assert state(db,credit_customer)['committed']==0
    sales_receipts.void(db,p['director'],r.id,'Nhầm khoản thu',r.version)
    assert state(db,credit_customer)['committed']==100
    fail('CREDIT_LIMIT',svc.confirm,db,p['sales'],b.id,b.version)


@pytest.mark.parametrize('partial',[False,True])
def test_sales_return_releases_actual_debt_without_readding_returned_delivery(db,people,credit_customer,partial):
    p=people;stock(db,p,10);a=confirmed(db,p,goods=True,qty=10)
    svc.deliver(db,p['warehouse'],a.id,a.version,[dict(order_line_no=1,qty=4 if partial else 10)])
    gh=svc.deliveries(db,a.id)[0]
    sales_returns.create(db,p['sales'],a.id,SalesReturnIn(version=a.version,reason='Khách trả 1 cái',
        lines=[dict(delivery_id=gh.id,order_line_no=1,qty=1,condition='TOT')]))
    exposure=state(db,credit_customer)
    assert exposure['actual_debt']==(30 if partial else 90)
    assert exposure['committed']==(60 if partial else 0)
    assert confirmed(db,p,10).status=='DA_CHOT'
    b=draft(db,p,1);fail('CREDIT_LIMIT',svc.confirm,db,p['sales'],b.id,b.version)


def test_vat_rounding_and_voided_delivery_keep_exact_exposure(db,people,credit_customer):
    p=people;stock(db,p,3)
    credit_customer.credit_limit=108;db.commit()
    a=svc.create(db,p['sales'],SalesOrderIn(customer_code=credit_customer.code,payment_term='CONG_NO',
        lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=3,unit_price=33,vat_rate=8)]))
    svc.confirm(db,p['sales'],a.id,a.version)
    assert a.total==107 and state(db,credit_customer)['committed']==107
    svc.deliver(db,p['warehouse'],a.id,a.version,[dict(order_line_no=1,qty=1)])
    assert state(db,credit_customer)['actual_debt']==36
    assert state(db,credit_customer)['committed']==71
    gh=svc.deliveries(db,a.id)[0]
    svc.void_delivery(db,p['director'],gh.id,'Nhầm lần giao',gh.version)
    assert state(db,credit_customer)['actual_debt']==0
    assert state(db,credit_customer)['committed']==107
    assert confirmed(db,p,1).status=='DA_CHOT'  # inclusive limit, no self double count


def test_closed_quantity_stays_excluded_after_delivery_is_voided(db,people,credit_customer):
    p=people;stock(db,p,10);a=confirmed(db,p,goods=True,qty=10)
    svc.deliver(db,p['warehouse'],a.id,a.version,[dict(order_line_no=1,qty=4)])
    gh=svc.deliveries(db,a.id)[0]
    svc.close_remaining(db,p['sales'],a.id,'Không lấy phần còn lại',a.version)
    svc.void_delivery(db,p['director'],gh.id,'Nhầm giao',gh.version)
    assert a.lines[0].closed_qty==6 and a.status=='DA_CHOT'
    assert state(db,credit_customer)['remaining']==40
    assert confirmed(db,p,60).status=='DA_CHOT'


@pytest.mark.parametrize('term',['TRA_NGAY','DAT_COC'])
def test_noncredit_terms_skip_limit_and_do_not_add_undelivered_commitment(db,people,credit_customer,term):
    p=people;confirmed(db,p)
    a=confirmed(db,p,200,term=term)
    assert a.status=='DA_CHOT' and state(db,credit_customer)['committed']==100


def test_actual_debt_keeps_noncredit_sales_and_service_less_confirmed_receipts(db,people,credit_customer,configured_finance):
    p=people;a=confirmed(db,p,30,term='TRA_NGAY');svc.deliver(db,p['sales'],a.id,a.version)
    paid(db,p,a,10)
    t=ticket(db,p,'HOAN_THANH')  # existing DV debt 500,000
    report=sales_receipts.report_for_ticket(db,p['dispatcher'],t.id,
        SalesReceiptReport(version=t.version,method='TIEN_MAT',amount=499950))
    sales_receipts.confirm(db,p['finance'],report.id,report.version)
    assert state(db,credit_customer)['actual_debt']==70
    assert confirmed(db,p,30).status=='DA_CHOT'
    b=draft(db,p,1);fail('CREDIT_LIMIT',svc.confirm,db,p['sales'],b.id,b.version)


@pytest.mark.parametrize('flow',['confirm','save_new','save_update'])
def test_parallel_confirms_serialize_customer_without_share_lock_upgrade_deadlock(db,engine,people,credit_customer,flow):
    p=people;uid=p['sales'].id
    rows=[] if flow=='save_new' else [draft(db,p),draft(db,p)]
    ids=[row.id for row in rows] if rows else [None,None]
    barrier=Barrier(2)
    def run(oid):
        with Session(engine,expire_on_commit=False) as session:
            session.execute(text("set local lock_timeout = '5s'"))
            user=session.get(User,uid)
            barrier.wait(timeout=10)
            try:
                if flow=='confirm':svc.confirm(session,user,oid,1)
                elif flow=='save_new':svc.save_and_confirm(session,user,body())
                else:svc.save_and_confirm(session,user,SalesOrderUpdate(
                    version=1,lines=body().lines,payment_term='CONG_NO'),oid)
                return 'OK'
            except BusinessError as exc:
                return exc.code
    db.rollback()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,ids))
    assert sorted(results)==['CREDIT_LIMIT','OK']
    assert db.query(SalesOrder).filter_by(status='DA_CHOT').count()==1
    assert state(db,credit_customer)['remaining']==100
    assert db.query(AuditLog).filter_by(action='sales_order.confirm').count()==1


def test_confirmed_prices_and_payment_terms_remain_locked(db,people,credit_customer):
    p=people;a=confirmed(db,p,100,term='TRA_NGAY');before=counts(db)
    fail('PRICE_LOCKED',svc.change_options,db,p['sales'],a.id,
         SalesOrderOptions(version=a.version,payment_term='CONG_NO'))
    fail('PRICE_LOCKED',svc.update_lines,db,p['sales'],a.id,
         SalesOrderUpdate(version=a.version,lines=body(200).lines))
    assert a.total==100 and a.payment_term=='TRA_NGAY' and counts(db)==before


def test_api_and_web_credit_error_show_breakdown_and_preserve_draft(db,client,people,credit_customer):
    p=people;confirmed(db,p);b=draft(db,p,1);before=counts(db)
    h={'Authorization':'Bearer '+issue_token(p['sales'])}
    r=client.post(f'/sales-orders/{b.id}/confirm',headers=h,json=dict(version=b.version))
    assert r.status_code==409 and r.json()['error']=='CREDIT_LIMIT'
    assert r.json()['details']['committed']==100 and r.json()['details']['actual_debt']==0
    csrf=service_login(client,p['sales'])
    r=client.post(f'/don-ban/{b.id}/confirm',data=dict(csrf=csrf,version=b.version))
    assert r.status_code==409 and 'đơn chốt chưa giao: 100' in r.text
    assert counts(db)==before and b.status=='NHAP'


@pytest.mark.parametrize('new',[False,True])
def test_save_and_confirm_credit_failure_rolls_back_the_entire_save(db,people,credit_customer,new):
    p=people;confirmed(db,p)
    b=None if new else draft(db,p,20)
    before=counts(db)
    if new:
        fail('CREDIT_LIMIT',svc.save_and_confirm,db,p['sales'],body())
    else:
        fail('CREDIT_LIMIT',svc.save_and_confirm,db,p['sales'],
             SalesOrderUpdate(version=b.version,lines=body(50).lines),b.id)
        db.refresh(b)
        assert b.status=='NHAP' and b.total==20 and b.lines[0].unit_price==20 and b.version==1
    assert counts(db)==before


def test_commitments_are_scoped_to_each_customer_in_batched_read(db,people,credit_customer):
    p=people;other=Customer(code='KTD-000002',name='Khách khác',search_text='khach khac',
                           allow_debt=True,credit_limit=100)
    db.add(other);db.commit()
    b=svc.create(db,p['sales'],body().model_copy(update=dict(customer_code=other.code)))
    svc.confirm(db,p['sales'],b.id,b.version)
    assert state(db,credit_customer)['committed']==0
    confirmed(db,p)
    assert svc.credit_exposure(db,[credit_customer.code,other.code])=={
        credit_customer.code:dict(actual_debt=0,committed=100),other.code:dict(actual_debt=0,committed=100)}


def test_customer_card_shares_exposure_and_preserves_financial_visibility(db,client,people,credit_customer):
    p=people;confirmed(db,p);b=draft(db,p,1)
    card=picked.customer_cards(db,p['finance'],[credit_customer])[credit_customer.code]
    assert card['debt']==0 and card['committed']==100 and card['limit']==100
    for role in ('finance','director'):
        service_login(client,p[role])
        page=client.get(f'/don-ban/{b.id}')
        assert page.status_code==200 and 'data-card-committed>Đơn chốt chưa giao 100' in page.text
        chooser=client.get('/don-ban/tim-khach?q=Minh')
        assert 'data-committed="100"' in chooser.text
    service_login(client,p['sales'])
    page=client.get(f'/don-ban/{b.id}')
    assert 'data-card-committed' not in page.text and 'data-card-debt' not in page.text

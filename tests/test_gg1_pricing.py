"""GG1: exact allocation, frozen delivery/return amounts and credit exposure."""
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo
import re
from itertools import product
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app import (quotes, sales_orders as orders, sales_returns, sales_receipts,
                 finance_reports, pending_items, sales_reports, purchase_reports)
from app.errors import BusinessError
from app.models import (AuditLog, Customer, FundLedger, Quote, SalesOrder, SalesReceipt,
                        StockDoc, StockLedger, StockReservation, Warehouse)
from app.schemas import (QuoteIn, QuoteUpdate, SalesOrderIn, SalesOrderUpdate,
                         SalesOrderFromQuote, SalesReceiptReport, SalesReturnIn)
from app.security import issue_token
from test_sales_orders import people, stocked
from test_web import service_login
from app.web import steps


def fail(code,fn,*args,**kw):
    with pytest.raises(BusinessError) as exc:fn(*args,**kw)
    assert exc.value.code==code
    return exc.value


def counts(db):
    return tuple(db.query(model).count() for model in (Quote,SalesOrder,StockDoc,StockLedger,StockReservation,SalesReceipt,FundLedger,AuditLog))


def make(db,p,kind='order',qty=1,price=1080000,discount=100000,included=True,rate=8,goods=False,**kw):
    line=dict(description='Hàng / công sửa',qty=qty,unit_price=price,vat_rate=rate)
    if kind=='order':line.update(kind='HANG' if goods else 'KHAC')
    if goods:line.update(product_code='TD-0001',warehouse_code='K58')
    data=dict(customer_code='KTD-000001',price_includes_vat=included,discount_amount=discount,lines=[line],**kw)
    return quotes.create(db,p['sales'],QuoteIn(**data)) if kind=='quote' else orders.create(db,p['sales'],SalesOrderIn(**data))


@pytest.mark.parametrize('kind',['quote','order'])
@pytest.mark.parametrize('included,price,discount,expected',[
    (False,150000,0,(150000,12000,162000)),
    (True,150000,0,(138889,11111,150000)),
    (False,1000000,100000,(900000,72000,972000)),
    (True,1080000,100000,(907407,72593,980000)),
    (True,150000,150000,(0,0,0)),
])
def test_exact_vat_examples_and_entered_price(db,people,kind,included,price,discount,expected):
    row=make(db,people,kind,price=price,discount=discount,included=included)
    assert (row.subtotal,row.vat_total,row.total)==expected
    assert row.lines[0].unit_price==price
    assert row.price_includes_vat is included and row.discount_amount==discount
    event=db.scalar(select(AuditLog).where(AuditLog.entity_id==str(row.id),AuditLog.action==('quote.create' if kind=='quote' else 'sales_order.create')))
    assert event.after['price_includes_vat']==included and event.after['discount_amount']==discount


@pytest.mark.parametrize('values,discount,expected',[
    ([1]*6,2,[1,1,0,0,0,0]),([100000,1,1],2,[2,0,0]),
    ([1,3],2,[0,2]),([1,1],1,[1,0]),([0,1,0],1,[0,1,0]),
    ([0,0],0,[0,0]),([3,7],10,[3,7]),
])
def test_largest_remainder_ties_and_zero_lines(values,discount,expected):
    assert quotes.discount_parts(values,discount)==expected


def test_allocator_conservation_bounds_and_integer_precision():
    for values in product(range(4),repeat=4):
        for discount in range(sum(values)+1):
            shares=quotes.discount_parts(values,discount)
            assert sum(shares)==discount and all(0<=share<=value for share,value in zip(shares,values))
    n=quotes.MAX_MONEY
    assert quotes.discount_parts([n,n-1,1],n)==[n//2+1,n//2,0]


@pytest.mark.parametrize('kind',['quote','order'])
def test_mixed_vat_and_small_line_edge_cases(db,people,kind):
    for prices,rates,discount in [([100000,1,1],[8,10,0],2),([1]*6,[8]*6,2),([100003,70001],[8,10],33333)]:
        lines=[dict(description='Dòng '+str(i),qty=1,unit_price=price,vat_rate=rate) for i,(price,rate) in enumerate(zip(prices,rates))]
        if kind=='order':
            for line in lines:line['kind']='KHAC'
        body=dict(customer_code='KTD-000001',price_includes_vat=True,discount_amount=discount,lines=lines)
        row=quotes.create(db,people['sales'],QuoteIn(**body)) if kind=='quote' else orders.create(db,people['sales'],SalesOrderIn(**body))
        assert row.total==sum(prices)-discount
        shares=[price-line.amount-line.vat_amount for price,line in zip(prices,row.lines)]
        assert shares==quotes.discount_parts(prices,discount)
        assert all(line.amount>=0 and line.vat_amount>=0 for line in row.lines)


@pytest.mark.parametrize('kind',['quote','order'])
def test_over_discount_create_and_update_roll_back(db,people,kind):
    before=counts(db)
    fail('DISCOUNT_TOO_LARGE',make,db,people,kind,price=100,discount=101)
    assert counts(db)==before
    row=make(db,people,kind,price=100,discount=10)
    db.expire_all();db.refresh(row)
    snapshot=quotes.snapshot(row) if kind=='quote' else orders.snapshot(row);before=counts(db)
    cls,update=(QuoteUpdate,quotes.update_lines) if kind=='quote' else (SalesOrderUpdate,orders.update_lines)
    line=dict(description='Dòng sửa',qty=1,unit_price=100)
    if kind=='order':line['kind']='KHAC'
    body=cls(version=row.version,price_includes_vat=False,discount_amount=101,lines=[line])
    fail('DISCOUNT_TOO_LARGE',update,db,people['sales'],row.id,body)
    assert (quotes.snapshot(row) if kind=='quote' else orders.snapshot(row))==snapshot
    assert counts(db)==before


def test_quote_revision_and_conversion_preserve_discount_input_and_totals(db,people):
    p=people;q=make(db,p,'quote',qty=3,price=360000)
    quotes.send(db,p['sales'],q.id,q.version)
    revised=quotes.revise(db,p['sales'],q.id,q.version)
    assert revised.price_includes_vat and revised.discount_amount==100000 and revised.total==q.total
    quotes.send(db,p['sales'],revised.id,revised.version);quotes.accept(db,p['sales'],revised.id,revised.version)
    row=orders.create_from_quote(db,p['sales'],revised.id,SalesOrderFromQuote(version=revised.version))
    assert (row.price_includes_vat,row.discount_amount,row.subtotal,row.vat_total,row.total)==(True,100000,revised.subtotal,revised.vat_total,revised.total)
    assert row.lines[0].unit_price==360000


def test_two_deliveries_reconcile_discount_vat_debt_and_confirmed_receipt(db,stocked,configured_finance):
    p=stocked;row=make(db,p,qty=3,price=360000,goods=True)
    orders.confirm(db,p['sales'],row.id,row.version)
    orders.deliver(db,p['warehouse'],row.id,row.version,[dict(order_line_no=1,qty=1)])
    first=orders.deliveries(db,row.id)[0]
    assert (first.subtotal,first.vat_total,first.total,row.receivable)==(302469,24198,326667,326667)
    orders.deliver(db,p['warehouse'],row.id,row.version)
    shipments=orders.deliveries(db,row.id)
    assert sum(g.subtotal for g in shipments)==row.subtotal==907407
    assert sum(g.vat_total for g in shipments)==row.vat_total==72593
    assert sum(g.total for g in shipments)==row.receivable==row.total==980000
    assert sum(orders.delivery_discount(db,row,g) for g in shipments)==100000
    receipt=sales_receipts.report(db,p['sales'],row.id,SalesReceiptReport(version=row.version,method='TIEN_MAT',amount=980000))
    sales_receipts.confirm(db,p['finance'],receipt.id,receipt.version)
    assert sales_receipts.balance(row)['remaining']==0


def test_s5_return_uses_discounted_delivery_and_exact_last_remainder(db,stocked):
    p=stocked;row=make(db,p,qty=3,price=360000,goods=True)
    orders.confirm(db,p['sales'],row.id,row.version);orders.deliver(db,p['warehouse'],row.id,row.version)
    gh=orders.deliveries(db,row.id)[0]
    def receive(qty):
        return sales_returns.create(db,p['warehouse'],row.id,SalesReturnIn(version=row.version,reason='Khách trả',lines=[dict(delivery_id=gh.id,order_line_no=1,qty=qty,condition='TOT')]))
    first=receive(1)
    assert (first.subtotal,first.vat_total,first.total)==(302469,24198,326667)
    assert row.receivable==653333
    day=gh.delivered_at.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    sales=sales_reports.totals(db,p['finance'],day,day)
    assert (sales['subtotal'],sales['vat_total'],sales['total'])==(604938,48395,653333)
    margin=purchase_reports.gross_margin(db,p['finance'],day,day)['totals']
    assert (margin['revenue'],margin['cost'],margin['profit'])==(604938,200000,404938)
    last=receive(2)
    assert first.total+last.total==980000 and row.receivable==orders.effective_total(row)==0
    assert sum(l.cost_amount for r in (first,last) for l in r.lines)==300000
    sales_returns.void(db,p['director'],last.id,last.version,'Nhập nhầm số lượng')
    assert row.receivable==653333


def test_credit_limit_uses_discounted_total_and_closed_commitments(db,people):
    p=people;c=db.get(Customer,'KTD-000001');c.allow_debt=True;c.credit_limit=980000;db.commit()
    row=make(db,p,qty=3,price=360000,payment_term='CONG_NO')
    checks=steps.order_checks(db,p['sales'],orders.snapshot(row),{},c)
    assert next(check for check in checks if check['key']=='credit')['ok']
    orders.confirm(db,p['sales'],row.id,row.version)
    assert orders.credit_state(db,c,0)['committed']==980000
    other=make(db,p,price=1,discount=0,included=False,rate=0,payment_term='CONG_NO')
    credit=next(check for check in steps.order_checks(db,p['sales'],orders.snapshot(other),{},c) if check['key']=='credit')
    assert not credit['ok'] and not credit['blocking'] and 'đơn chốt chưa giao: 980.000' in credit['label']
    orders.deliver(db,p['sales'],row.id,row.version,[dict(order_line_no=1,qty=1)])
    state=orders.credit_state(db,c,0)
    assert (state['actual_debt'],state['committed'])==(326667,653333)
    orders.close_remaining(db,p['sales'],row.id,'Khách không lấy thêm',row.version)
    state=orders.credit_state(db,c,0)
    assert (state['actual_debt'],state['committed'])==(326667,0)
    assert orders.effective_total(row)==326667
    orders.confirm(db,p['sales'],other.id,other.version)
    gh=orders.deliveries(db,row.id)[0]
    orders.void_delivery(db,p['director'],gh.id,'Nhầm lần giao',gh.version)
    state=orders.credit_state(db,c,0)
    assert (state['actual_debt'],state['committed'])==(0,326668)
    assert orders.effective_total(row)==326667


@pytest.mark.parametrize('kind',['quote','order'])
@pytest.mark.parametrize('new_fields',[False,True])
def test_api_and_form_defaults_and_form_money(client,db,people,kind,new_fields):
    p=people;route='/quotes' if kind=='quote' else '/sales-orders';formroute='/bao-gia/moi' if kind=='quote' else '/don-ban/moi'
    line=dict(description='Dịch vụ',qty=1,unit_price=1080000,vat_rate=8)
    if kind=='order':line['kind']='KHAC'
    data=dict(customer_code='KTD-000001',lines=[line])
    if new_fields:data.update(price_includes_vat=True,discount_amount=100000)
    r=client.post(route,json=data,headers={'Authorization':'Bearer '+issue_token(p['sales'])})
    assert r.status_code==201,r.text
    result=r.json();assert result['price_includes_vat']==new_fields and result['discount_amount']==(100000 if new_fields else 0)
    assert result['total']==(980000 if new_fields else 1166400)
    csrf=service_login(client,p['sales'])
    form=dict(csrf=csrf,customer_code='KTD-000001',description='Dịch vụ',qty='1',unit_price='1.080.000',vat_0='8',kind_0='KHAC',valid_until='2026-12-31')
    if new_fields:form.update(price_includes_vat='1',discount_amount='100.000')
    r=client.post(formroute,data=form,follow_redirects=False);assert r.status_code==303,r.text
    row=(db.get(Quote if kind=='quote' else SalesOrder,int(r.headers['location'].split('?')[0].split('/')[-1])))
    assert (row.price_includes_vat,row.discount_amount,row.total)==(new_fields,100000 if new_fields else 0,980000 if new_fields else 1166400)
    html=client.get(r.headers['location']).text
    # Giao diện: nhóm chọn Giá nhập (radio đang chọn) + ô Giảm giá có dấu chấm nghìn, để trống khi 0.
    assert 'name="price_includes_vat" value="'+('1' if new_fields else '')+'" checked' in html
    assert re.search(r'name="discount_amount"[^>]*value="'+('100.000' if new_fields else '')+'"',html)
    form.update(version=row.version,price_includes_vat=re.search(r'name="price_includes_vat" value="([^"]*)" checked',html)[1],discount_amount=re.search(r'name="discount_amount"[^>]*value="([^"]*)"',html)[1])
    r=client.post(('/bao-gia/' if kind=='quote' else '/don-ban/')+str(row.id),data=form,follow_redirects=False)
    assert r.status_code==303,r.text
    db.refresh(row);assert row.total==result['total'] and row.discount_amount==result['discount_amount']


@pytest.mark.parametrize('kind',['quote','order'])
def test_pricing_changes_are_locked_after_send_or_confirm(client,db,people,kind):
    p=people;row=make(db,p,kind)
    if kind=='quote':quotes.send(db,p['sales'],row.id,row.version)
    else:orders.confirm(db,p['sales'],row.id,row.version)
    before=quotes.snapshot(row) if kind=='quote' else orders.snapshot(row)
    data=dict(version=row.version,price_includes_vat=False,discount_amount=0,lines=[dict(description='Giá mới',qty=1,unit_price=500000)])
    if kind=='order':data['lines'][0]['kind']='KHAC'
    headers={'Authorization':'Bearer '+issue_token(p['sales'])}
    r=client.patch(f'/quotes/{row.id}',json=data,headers=headers) if kind=='quote' else client.put(f'/sales-orders/{row.id}/lines',json=data,headers=headers)
    assert r.status_code==409 and r.json()['error']=='PRICE_LOCKED'
    db.refresh(row);assert (quotes.snapshot(row) if kind=='quote' else orders.snapshot(row))==before


def test_print_quote_and_partial_shipments_show_correct_discount(client,db,stocked):
    p=stocked;q=make(db,p,'quote');service_login(client,p['sales'])
    html=client.get(f'/bao-gia/{q.id}/in').text
    assert 'Cộng tiền hàng (đã gồm VAT)' in html and 'Giảm giá' in html and '−100.000 đồng' in html and '980.000 đồng' in html
    row=make(db,p,qty=3,price=360000,goods=True);orders.confirm(db,p['sales'],row.id,row.version)
    orders.deliver(db,p['warehouse'],row.id,row.version,[dict(order_line_no=1,qty=1)])
    gh=orders.deliveries(db,row.id)[0];html=client.get(f'/don-ban/{row.id}/giao/{gh.id}/in').text
    assert 'Cộng tiền hàng (đã gồm VAT)' in html and '−33.333 đồng' in html and '326.667 đồng' in html
    assert '360.000' in html  # Entered unit price remains available on print.


def test_liquidation_below_cost_cannot_hide_behind_vat_or_discount(db,stocked):
    p=stocked;db.add(Warehouse(code='CXL',name='Chờ xử lý',kind='LOI'));db.commit()
    pending_items.transfer_in(db,p['warehouse'],'K58',[dict(product_code='TD-0001',qty=1)],'THANH_LY','Hàng thanh lý')
    row=orders.create(db,p['sales'],SalesOrderIn(customer_code='KTD-000001',price_includes_vat=True,discount_amount=1,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='CXL',qty=1,unit_price=108000,vat_rate=8)]))
    assert row.lines[0].amount==99999
    checks=steps.order_checks(db,p['sales'],orders.snapshot(row),{('CXL','TD-0001'):dict(available=1)},db.get(Customer,row.customer_code))
    assert not next(check for check in checks if check['key']=='CAN_GIAM_DOC_DUYET_GIA')['ok']
    before=counts(db);exc=fail('CAN_GIAM_DOC_DUYET_GIA',orders.confirm,db,p['sales'],row.id,row.version)
    assert exc.status==403 and counts(db)==before and row.status=='NHAP'
    orders.confirm(db,p['director'],row.id,row.version)


def test_bigint_precision_and_migration_preserve_old_money(db,engine,people):
    p=people;old=make(db,p,discount=0,included=False);oldid=old.id;before=(old.total,old.subtotal,old.vat_total)
    quote=make(db,p,'quote',discount=0,included=False);quoteid=quote.id;quote_before=(quote.total,quote.subtotal,quote.vat_total)
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'));cfg.attributes['database_url']=engine.url.render_as_string(hide_password=False)
    db.commit();command.downgrade(cfg,'0055')
    try:
        assert tuple(db.execute(text('select total,subtotal,vat_total from sales_orders where id=:id'),dict(id=oldid)).one())==before
        assert tuple(db.execute(text('select total,subtotal,vat_total from quotes where id=:id'),dict(id=quoteid)).one())==quote_before
        db.commit();command.upgrade(cfg,'head');db.expire_all();db.refresh(old)
        assert (old.price_includes_vat,old.discount_amount)==(False,0) and (old.total,old.subtotal,old.vat_total)==before
        db.refresh(quote)
        assert (quote.price_includes_vat,quote.discount_amount)==(False,0) and (quote.total,quote.subtotal,quote.vat_total)==quote_before
    finally:
        db.rollback();command.upgrade(cfg,'head')
    with localcontext() as context:
        context.prec=6
        big=make(db,p,'quote',price=quotes.MAX_MONEY,discount=1)
    assert big.total==quotes.MAX_MONEY-1
    assert big.lines[0].amount+big.lines[0].vat_amount==big.total


def test_many_tiny_deliveries_never_go_negative_and_reconcile(client,db,people):
    p=people;row=make(db,p,qty=6,price=1,discount=2)
    orders.confirm(db,p['sales'],row.id,row.version)
    for _ in range(6):orders.deliver(db,p['sales'],row.id,row.version,[dict(order_line_no=1,qty=1)])
    shipments=orders.deliveries(db,row.id)
    assert all(g.subtotal>=0 and g.vat_total>=0 for g in shipments)
    assert sum(g.total for g in shipments)==row.receivable==row.total==4
    assert sum(orders.delivery_discount(db,row,g) for g in shipments)==2
    no_share=next(g for g in shipments if orders.delivery_discount(db,row,g)==0)
    service_login(client,p['sales']);html=client.get(f'/don-ban/{row.id}/giao/{no_share.id}/in').text
    assert '<td class="l">Giảm giá</td><td class="v">0 đồng</td>' in html  # Form in: ô tổng khuôn A4, lần giao không chia giảm giá vẫn in 0.


@pytest.mark.parametrize('changes,code',[(dict(discount_amount='-1'),'BAD_DISCOUNT'),(dict(discount_amount='1.00'),'BAD_DISCOUNT'),(dict(price_includes_vat='2'),'BAD_PRICE_MODE'),(dict(discount_amount='1.080.001'),'DISCOUNT_TOO_LARGE')])
def test_invalid_web_pricing_does_not_save(client,db,people,changes,code):
    csrf=service_login(client,people['sales']);before=counts(db)
    data=dict(csrf=csrf,customer_code='KTD-000001',description='Dịch vụ',qty='1',unit_price='1.080.000',vat_0='8',valid_until='2026-12-31',**changes)
    result=client.post('/bao-gia/moi',data=data)
    assert result.status_code==400 and code in result.text and counts(db)==before


def test_live_credit_check_receives_same_vat_discount_fields(client,db,people):
    p=people;customer=db.get(Customer,'KTD-000001');customer.allow_debt=True;customer.credit_limit=980000;db.commit()
    service_login(client,p['sales'])
    data=dict(customer_code=customer.code,payment_term='CONG_NO',kind_0='KHAC',description='Dịch vụ',qty='1',unit_price='1.080.000',vat_0='8',price_includes_vat='1',discount_amount='100.000')
    page=client.get('/don-ban/dieu-kien',params=data)
    assert page.status_code==200 and 'data-condition="credit" data-ok="1"' in page.text
    page=client.get('/don-ban/dieu-kien',params={**data,'discount_amount':'0'})
    assert page.status_code==200 and 'data-condition="credit" data-ok="0"' in page.text
    page=client.get('/don-ban/dieu-kien',params={**data,'discount_amount':'1.080.001'})
    assert page.status_code==200 and 'data-condition="pricing" data-ok="0"' in page.text

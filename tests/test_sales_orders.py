from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app import quotes, sales_orders as svc, stock_docs, stock_posting
from app.errors import BusinessError
from app.models import (AuditLog, Customer, Product, SalesOrder, StockBalance, StockDoc,
                        StockLedger, Supplier, User, Warehouse)
from app.schemas import (QuoteIn, SalesOrderFromQuote, SalesOrderIn, SalesOrderUpdate, StockDocIn)
from app.security import issue_token

GOOD = dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty='2',unit_price=350000,vat_rate=10)
OTHER = dict(kind='KHAC',description='Công lắp đặt',qty='1',unit='lần',unit_price=200000,vat_rate=8)
SECOND = dict(kind='HANG',product_code='TD-0002',warehouse_code='K145',qty='1',unit_price=900000,vat_rate=10)


@pytest.fixture
def people(db, make_user, monkeypatch):
    monkeypatch.setattr(quotes, '_today', lambda: date(2026,10,17))
    db.add_all([
        Customer(code='KTD-000001',name='Minh Phát',address='58 Mã Lò',search_text='minh phat'),
        Customer(code='KTD-000009',name='Đã ngừng',status='NGUNG'),
        Product(code='TD-0001',name='Mực in',unit='hộp',sale_price=120000,search_text='td-0001 muc in'),
        Product(code='TD-0002',name='Máy in',unit='cái',sale_price=900000,search_text='td-0002 may in'),
        Product(code='TD-0009',name='Đã ngừng',active=False),
        Warehouse(code='K58',name='Kho 58'), Warehouse(code='K145',name='Kho 145'),
        Warehouse(code='K00',name='Kho ngừng',active=False), Supplier(code='NCC-0001',name='Nhà cung cấp'),
    ])
    db.commit()
    return {role: make_user('db.'+role,role) for role in ('sales','warehouse','finance','technician','director')}


@pytest.fixture
def stocked(db, people):
    for wh,code,qty,cost in [('K58','TD-0001','5',100000),('K145','TD-0002','3',400000)]:
        doc = stock_docs.create(db,people['warehouse'],StockDocIn(kind='PN',warehouse_code=wh,
                doc_date=date(2026,10,1),supplier_code='NCC-0001',
                lines=[dict(product_code=code,qty=qty,unit_cost=cost)]))
        stock_posting.post(db,people['warehouse'],doc.id,doc.version)
    return people


def body(**changes):
    data = dict(customer_code='KTD-000001',contact_name='Chị Lan',note='Giao tại văn phòng',lines=[GOOD,OTHER])
    data.update(changes)
    return SalesOrderIn(**data)


def failure(code, fn, *args):
    with pytest.raises(BusinessError) as exc:
        fn(*args)
    assert exc.value.code == code
    return exc.value


def balance(db, wh, code):
    db.expire_all()
    b = db.get(StockBalance,(wh,code))
    return b.qty,b.value


def quote(db, user, accept=True):
    q = quotes.create(db,user,QuoteIn(customer_code='KTD-000001',contact_name='Chị Lan',note='Theo báo giá',lines=[
        dict(product_code='TD-0001',qty='2',unit_price=100,vat_rate=10),
        dict(description='Công lắp đặt',qty='1.5',unit='lần',unit_price=333,vat_rate=10)]))
    quotes.send(db,user,q.id,q.version)
    if accept:
        quotes.accept(db,user,q.id,q.version)
    return q


def test_create_two_kinds_number_totals_half_rounding(db, people):
    o = svc.create(db,people['sales'],body(lines=[{**GOOD,'unit_price':100},
                    {**OTHER,'qty':'1.5','unit_price':333,'vat_rate':10}]))
    assert o.number == 'DB-26-00001' and o.version == 1 and o.status == 'NHAP'
    assert o.receivable is None and o.confirmed_at is None and o.delivered_at is None
    assert o.delivery_address == '58 Mã Lò' and o.contact_name == 'Chị Lan'
    assert [(line.kind,line.amount,line.vat_amount) for line in o.lines] == [('HANG',200,20),('KHAC',500,50)]
    assert (o.subtotal,o.vat_total,o.total) == (700,70,770)
    second = svc.create(db,people['sales'],body(lines=[{**OTHER,'unit_price':5,'vat_rate':10}]))
    assert second.number == 'DB-26-00002' and second.total == 6  # VAT 0.5 làm tròn lên 1.


def test_default_sale_price_and_quote_money_helpers(db, people, monkeypatch):
    used = []
    original_round, original_limit = quotes._round, quotes._money_limit
    def round_money(value):
        used.append('round')
        return original_round(value)
    def limit_money(value):
        used.append('limit')
        return original_limit(value)
    monkeypatch.setattr(quotes,'_round',round_money)
    monkeypatch.setattr(quotes,'_money_limit',limit_money)
    o = svc.create(db,people['sales'],body(lines=[{k:v for k,v in GOOD.items() if k != 'unit_price'}]))
    assert o.lines[0].unit_price == 120000 and o.lines[0].description == 'Mực in' and o.lines[0].unit == 'hộp'
    assert 'round' in used and 'limit' in used
    failure('AMOUNT_TOO_LARGE',svc.create,db,people['sales'],body(lines=[{**OTHER,'qty':'99999999999.999','unit_price':quotes.MAX_MONEY}]))


def test_from_quote_status_copy_used_and_reuse_after_cancel(db, people):
    user = people['sales']
    q = quote(db,user,accept=False)
    failure('BAD_STATUS',svc.create_from_quote,db,user,q.id,SalesOrderFromQuote(version=q.version,warehouses={1:'K58'}))
    quotes.accept(db,user,q.id,q.version)
    args = SalesOrderFromQuote(version=q.version,warehouses={1:'K58'})
    # Giá bán thay đổi không làm đổi giá đã được khách chấp nhận.
    db.get(Product,'TD-0001').sale_price = 999000
    db.commit()
    o = svc.create_from_quote(db,user,q.id,args)
    assert o.quote_id == q.id and o.customer_code == q.customer_code
    assert o.total == q.total == 770 and o.contact_name == q.contact_name and o.note == q.note
    assert [(l.kind,l.product_code,l.warehouse_code,l.qty,l.unit_price,l.unit) for l in o.lines] == [
        ('HANG','TD-0001','K58',Decimal('2'),100,'hộp'),('KHAC',None,None,Decimal('1.5'),333,'lần')]
    assert failure('QUOTE_USED',svc.create_from_quote,db,user,q.id,args).status == 409
    svc.cancel(db,user,o.id,'Khách đổi cách giao',o.version)
    new = svc.create_from_quote(db,user,q.id,args)
    assert new.id != o.id and new.quote_id == q.id and new.status == 'NHAP'
    assert new.number == 'DB-26-00002'


def test_from_quote_requires_source_version_and_warehouse(db, people):
    q = quote(db,people['sales'])
    failure('VERSION_CONFLICT',svc.create_from_quote,db,people['sales'],q.id,SalesOrderFromQuote(version=1,warehouses={1:'K58'}))
    failure('BAD_LINE',svc.create_from_quote,db,people['sales'],q.id,SalesOrderFromQuote(version=q.version,warehouses={2:'K58'}))
    assert db.query(SalesOrder).count() == 0
    draft = svc.create_from_quote(db,people['sales'],q.id,SalesOrderFromQuote(version=q.version))
    assert draft.status == 'NHAP' and draft.lines[0].warehouse_code is None
    failure('WAREHOUSE_REQUIRED',svc.confirm,db,people['sales'],draft.id,draft.version)
    svc.update_lines(db,people['sales'],draft.id,SalesOrderUpdate(version=draft.version,lines=[
        dict(kind='HANG' if l.product_code else 'KHAC',warehouse_code='K58' if l.product_code else None,
             **{k:getattr(l,k) for k in ('product_code','description','qty','unit','unit_price','vat_rate')}) for l in draft.lines]))
    svc.confirm(db,people['sales'],draft.id,draft.version)
    assert draft.status == 'DA_CHOT'
    q2 = quotes.create(db,people['sales'],QuoteIn(customer_code='KTD-000001',lines=[dict(description='Phí',qty=1,unit_price=100)]))
    quotes.send(db,people['sales'],q2.id,q2.version)
    quotes.accept(db,people['sales'],q2.id,q2.version)
    o = svc.create_from_quote(db,people['sales'],q2.id,SalesOrderFromQuote(version=q2.version))
    assert o.lines[0].kind == 'KHAC' and o.lines[0].warehouse_code is None


def test_confirm_locks_prices_sets_receivable_and_empty_rejected(db, people):
    u = people['sales']
    empty = svc.create(db,u,body(lines=[]))
    failure('EMPTY_LINES',svc.confirm,db,u,empty.id,empty.version)
    missing = svc.create(db,u,body(lines=[{**GOOD,'warehouse_code':None}]))
    failure('WAREHOUSE_REQUIRED',svc.confirm,db,u,missing.id,missing.version)
    assert missing.status == 'NHAP' and missing.receivable is None
    o = svc.create(db,u,body())
    v = o.version
    svc.confirm(db,u,o.id,v)
    assert o.status == 'DA_CHOT' and o.receivable == 0 and o.total == 986000
    assert o.confirmed_by == u.id and o.confirmed_at is not None and o.version == v+1
    assert failure('PRICE_LOCKED',svc.update_lines,db,u,o.id,SalesOrderUpdate(version=o.version,lines=[])).status == 409
    failure('BAD_STATUS',svc.confirm,db,u,o.id,o.version)


def test_deliver_two_warehouses_posts_px_and_stock(db, stocked):
    o = svc.create(db,stocked['sales'],body(lines=[GOOD,OTHER,SECOND]))
    svc.confirm(db,stocked['sales'],o.id,o.version)
    svc.deliver(db,stocked['warehouse'],o.id,o.version)
    assert o.status == 'DA_GIAO' and o.delivered_by == stocked['warehouse'].id and o.delivered_at is not None
    assert (o.subtotal,o.vat_total,o.total,o.receivable) == (1800000,176000,1976000,1976000)
    docs = list(db.scalars(select(StockDoc).where(StockDoc.kind=='PX').order_by(StockDoc.warehouse_code)))
    assert [(d.warehouse_code,d.status,d.customer_code) for d in docs] == [('K145','DA_GHI','KTD-000001'),('K58','DA_GHI','KTD-000001')]
    assert {d.reason for d in docs} == {f'Giao {svc.deliveries(db,o.id)[0].number} cho {o.number}'}
    assert [d.lines[0].note for d in docs] == [f'{o.number} dòng 3; {svc.deliveries(db,o.id)[0].number}',f'{o.number} dòng 1; {svc.deliveries(db,o.id)[0].number}']
    assert len({l.stock_doc_id for l in o.lines if l.kind=='HANG'}) == 2 and o.lines[1].stock_doc_id is None
    assert balance(db,'K58','TD-0001') == (Decimal('3'),300000)
    assert balance(db,'K145','TD-0002') == (Decimal('2'),800000)
    ledger = list(db.scalars(select(StockLedger).where(StockLedger.doc_id.in_([d.id for d in docs]))))
    assert len(ledger) == 2 and all(l.qty_out > 0 and not l.is_reversal for l in ledger)


def test_shortage_rolls_back_previously_posted_warehouse_and_numbers(db, stocked):
    o = svc.create(db,stocked['sales'],body(lines=[{**GOOD,'qty':'6'},OTHER,SECOND]))
    svc.confirm(db,stocked['sales'],o.id,o.version)
    version = o.version
    ledger_count = db.query(StockLedger).count()
    audit_count = db.query(AuditLog).count()
    exc = failure('NEGATIVE_STOCK',svc.deliver,db,stocked['sales'],o.id,o.version)
    assert exc.details == {'warehouse':'K58','product':'TD-0001','available':'5.000','requested':'6.000'}
    db.expire_all()
    assert o.status == 'DA_CHOT' and o.version == version and o.delivered_at is None and o.delivered_by is None
    assert all(l.stock_doc_id is None for l in o.lines)
    assert db.query(StockDoc).filter(StockDoc.kind=='PX').count() == 0
    assert db.query(StockLedger).count() == ledger_count and db.query(AuditLog).count() == audit_count
    assert balance(db,'K145','TD-0002') == (Decimal('3'),1200000)
    assert balance(db,'K58','TD-0001') == (Decimal('5'),500000)
    assert db.scalar(text("select last from doc_counters where prefix = 'PX' and year = 2026")) is None
    doc = stock_docs.create(db,stocked['warehouse'],StockDocIn(kind='PN',warehouse_code='K58',doc_date=date(2026,10,17),
            supplier_code='NCC-0001',lines=[dict(product_code='TD-0001',qty='1',unit_cost=100000)]))
    stock_posting.post(db,stocked['warehouse'],doc.id,doc.version)
    svc.deliver(db,stocked['sales'],o.id,o.version)
    assert set(db.scalars(select(StockDoc.number).where(StockDoc.kind=='PX'))) == {'PX-26-00001','PX-26-00002'}


def test_cancel_delivered_restores_stock_and_keeps_history(db, stocked):
    o = svc.create(db,stocked['sales'],body(lines=[GOOD,OTHER,SECOND]))
    svc.confirm(db,stocked['sales'],o.id,o.version)
    svc.deliver(db,stocked['sales'],o.id,o.version)
    doc_ids = {l.stock_doc_id for l in o.lines if l.kind=='HANG'}
    gh=svc.deliveries(db,o.id)[0]
    svc.void_delivery(db,stocked['director'],gh.id,'Hủy lần giao trước',gh.version)
    svc.cancel(db,stocked['sales'],o.id,'Khách trả hàng',o.version)
    assert o.status == 'DA_HUY' and o.receivable == 0 and o.total == 1976000
    assert {l.stock_doc_id for l in o.lines if l.kind=='HANG'} == doc_ids
    assert set(db.scalars(select(StockDoc.status).where(StockDoc.id.in_(doc_ids)))) == {'DA_HUY'}
    assert balance(db,'K58','TD-0001') == (Decimal('5'),500000)
    assert balance(db,'K145','TD-0002') == (Decimal('3'),1200000)
    assert db.query(StockLedger).filter(StockLedger.doc_id.in_(doc_ids),StockLedger.is_reversal.is_(True)).count() == 2


def test_cancel_reason_versions_and_invalid_states(db, people):
    u = people['sales']
    o = svc.create(db,u,body())
    failure('REASON_REQUIRED',svc.cancel,db,u,o.id,' ',o.version)
    failure('VERSION_REQUIRED',svc.confirm,db,u,o.id,None)
    failure('BAD_STATUS',svc.deliver,db,u,o.id,o.version)
    svc.confirm(db,u,o.id,o.version)
    for fn,args in [(svc.update_lines,(SalesOrderUpdate(version=1,lines=[]),)),(svc.confirm,(1,)),
                    (svc.deliver,(1,)),(svc.cancel,('Khách hủy',1))]:
        assert failure('VERSION_CONFLICT',fn,db,u,o.id,*args).status == 409
    svc.cancel(db,u,o.id,'Khách hủy',o.version)
    assert o.receivable == 0
    failure('BAD_STATUS',svc.cancel,db,u,o.id,'Hủy lại',o.version)
    failure('PRICE_LOCKED',svc.update_lines,db,u,o.id,SalesOrderUpdate(version=o.version,lines=[]))


def test_replace_lines_and_search(db, people):
    u = people['sales']
    o = svc.create(db,u,body())
    svc.update_lines(db,u,o.id,SalesOrderUpdate(version=o.version,lines=[{**OTHER,'qty':'1.5','unit_price':333,'vat_rate':10}],
                     contact_name='Chị Hương',delivery_address='145 Mã Lò',note='Giữ dữ liệu'))
    assert len(o.lines)==1 and o.lines[0].line_no==1 and o.lines[0].product_code is None
    assert o.total == 550 and o.version==2 and o.delivery_address=='145 Mã Lò'
    assert svc.search(db,people['finance'],'minh phat')[0].id == o.id
    assert svc.search(db,people['warehouse'],'db-26')[0].id == o.id
    assert svc.search(db,u,status='DA_CHOT') == []
    assert svc.search(db,u,customer_code='KTD-000009') == []
    assert svc.search(db,u,from_date=date(2000,1,1),to_date=date(2000,1,2)) == []
    failure('BAD_STATUS',svc.search,db,u,'','SAI')


@pytest.mark.parametrize('changes,code',[
    ({'product_code':None},'BAD_LINE'),
    ({'warehouse_code':'K99'},'BAD_WAREHOUSE'), ({'warehouse_code':'K00'},'BAD_WAREHOUSE'),
    ({'product_code':'TD-9999'},'BAD_PRODUCT'), ({'product_code':'TD-0009'},'BAD_PRODUCT'),
    ({'kind':'KHAC'},'BAD_LINE'), ({'kind':'KHAC','product_code':None},'BAD_LINE'),
    ({'kind':'KHAC','warehouse_code':None},'BAD_LINE'),
])
def test_invalid_goods_rejected_without_spending_number(db, people, changes, code):
    failure(code,svc.create,db,people['sales'],body(lines=[{**GOOD,**changes}]))
    assert db.query(SalesOrder).count()==0
    assert svc.create(db,people['sales'],body()).number == 'DB-26-00001'


def test_inactive_customer_and_product_revalidated_at_confirm(db, people):
    failure('BAD_CUSTOMER',svc.create,db,people['sales'],body(customer_code='KTD-000009'))
    o = svc.create(db,people['sales'],body())
    db.get(Product,'TD-0001').active=False
    db.commit()
    failure('BAD_PRODUCT',svc.confirm,db,people['sales'],o.id,o.version)
    assert o.status=='NHAP'


def test_audit_every_order_operation(db, stocked):
    user=stocked['director']
    o=svc.create(db,user,body())
    svc.update_lines(db,user,o.id,SalesOrderUpdate(version=o.version,lines=body().lines))
    svc.confirm(db,user,o.id,o.version)
    svc.deliver(db,user,o.id,o.version)
    gh=svc.deliveries(db,o.id)[0]
    svc.void_delivery(db,stocked['director'],gh.id,'Hủy lần giao trước',gh.version)
    svc.cancel(db,user,o.id,'Khách trả hàng',o.version)
    q=quote(db,user)
    svc.create_from_quote(db,user,q.id,SalesOrderFromQuote(version=q.version,warehouses={1:'K58'}))
    logs=list(db.scalars(select(AuditLog).where(AuditLog.entity=='sales_order').order_by(AuditLog.id)))
    assert [l.action for l in logs]==['sales_order.create','sales_order.update_lines','sales_order.confirm',
            'sales_order.deliver','sales_order.void_delivery','sales_order.cancel','sales_order.create_from_quote']
    for log in logs:
        assert 'lines' in log.after and log.after['number'].startswith('DB-26-')
        if log.action in ('sales_order.create','sales_order.create_from_quote'):
            assert log.before is None
        else:
            assert log.after['version']==log.before['version']+1
    assert logs[-2].after['cancel_reason']=='Khách trả hàng' and logs[-2].reason=='Khách trả hàng'


def headers(user):
    return {'Authorization':'Bearer '+issue_token(user)}


def test_api_full_order_flow_and_from_quote(client, db, stocked):
    sales, warehouse = headers(stocked['sales']), headers(stocked['warehouse'])
    r = client.post('/sales-orders',headers=sales,json=body().model_dump(mode='json'))
    assert r.status_code == 201, r.text
    data = r.json()
    assert data['number']=='DB-26-00001' and data['receivable'] is None
    oid=data['id']
    r=client.put(f'/sales-orders/{oid}/lines',headers=sales,json=dict(version=data['version'],lines=[GOOD,OTHER,SECOND]))
    assert r.status_code==200 and len(r.json()['lines'])==3
    r=client.post(f'/sales-orders/{oid}/confirm',headers=sales,json=dict(version=r.json()['version']))
    assert r.status_code==200 and r.json()['receivable']==0
    r=client.post(f'/sales-orders/{oid}/deliver',headers=warehouse,json=dict(version=r.json()['version']))
    assert r.status_code==200 and r.json()['status']=='DA_GIAO'
    assert {l['stock_doc_id'] for l in r.json()['lines'] if l['kind']=='HANG'} != {None}
    gh=svc.deliveries(db,oid)[0]
    r=client.post(f'/sales-orders/deliveries/{gh.id}/void',headers=headers(stocked['director']),json=dict(version=gh.version,reason='Hủy lần giao trước'))
    r=client.post(f'/sales-orders/{oid}/cancel',headers=sales,json=dict(version=r.json()['version'],reason='Khách trả hàng'))
    assert r.status_code==200 and r.json()['status']=='DA_HUY'
    assert balance(db,'K58','TD-0001')==(Decimal('5'),500000)
    assert len(client.get('/sales-orders?q=minh phat&status=DA_HUY',headers=sales).json())==1
    assert client.get(f'/sales-orders/{oid}',headers=sales).json()['number']=='DB-26-00001'
    q=quote(db,stocked['sales'])
    r=client.post(f'/sales-orders/from-quote/{q.id}',headers=sales,json=dict(version=q.version))
    assert r.status_code==201 and r.json()['quote_id']==q.id
    assert r.json()['lines'][0]['warehouse_code'] is None  # Chọn kho sau khi lưu nháp.
    missing=client.post(f"/sales-orders/{r.json()['id']}/confirm",headers=sales,json=dict(version=r.json()['version']))
    assert missing.status_code==400 and missing.json()['error']=='WAREHOUSE_REQUIRED'
    assert client.get('/sales-orders?from=2026-10-18&to=2026-10-17',headers=sales).json()['error']=='BAD_DATE_RANGE'


def test_api_permissions_and_authentication(client, db, stocked):
    o=svc.create(db,stocked['sales'],body())
    q=quote(db,stocked['sales'])
    writes=[('post','/sales-orders',body().model_dump(mode='json')),
            ('post',f'/sales-orders/from-quote/{q.id}',dict(version=q.version,warehouses={'1':'K58'})),
            ('put',f'/sales-orders/{o.id}/lines',dict(version=o.version,lines=[GOOD])),
            ('post',f'/sales-orders/{o.id}/confirm',dict(version=o.version)),
            ('post',f'/sales-orders/{o.id}/cancel',dict(version=o.version,reason='Khách hủy'))]
    for role in ('warehouse','finance','technician'):
        h=headers(stocked[role])
        for method,path,data in writes:
            response=getattr(client,method)(path,headers=h,json=data)
            assert response.status_code==403,(role,path,response.text)
        if role!='warehouse':
            assert client.post(f'/sales-orders/{o.id}/deliver',headers=h,json=dict(version=o.version)).status_code==403
        status=403 if role=='technician' else 200
        assert client.get('/sales-orders',headers=h).status_code==status
        assert client.get(f'/sales-orders/{o.id}',headers=h).status_code==status
    assert client.get('/sales-orders').status_code==401
    assert client.post('/sales-orders',json=body().model_dump(mode='json')).status_code==401
    assert o.status=='NHAP'


def test_api_validates_quantity_price_vat_and_server_owned_fields(client, db, people):
    h=headers(people['sales'])
    for changes in [dict(qty=0),dict(qty=-1),dict(qty='0.0001'),dict(unit_price=-1),dict(unit_price=1.5),
                    dict(vat_rate=7),dict(vat_rate=True),dict(kind='SAI'),dict(stock_doc_id=1),dict(amount=123)]:
        r=client.post('/sales-orders',headers=h,json=body().model_dump(mode='json')|{'lines':[GOOD|changes]})
        assert r.status_code==422,(changes,r.text)
    assert client.post('/sales-orders',headers=h,json=body().model_dump(mode='json')|{'quote_id':1}).status_code==422
    assert db.query(SalesOrder).count()==0
    o=svc.create(db,people['sales'],body())
    assert client.post(f'/sales-orders/{o.id}/confirm',headers=h,json={}).status_code==422
    assert client.post(f'/sales-orders/{o.id}/cancel',headers=h,json=dict(version=o.version)).json()['error']=='REASON_REQUIRED'
    assert client.post(f'/sales-orders/{o.id}/confirm',headers=h,json=dict(version=o.version)).status_code==200
    assert client.post(f'/sales-orders/{o.id}/deliver',headers=h,json=dict(version=1)).json()['error']=='VERSION_CONFLICT'


def test_group_same_warehouse_and_repeat_product_lines(db, stocked):
    o=svc.create(db,stocked['sales'],body(lines=[GOOD,{**GOOD,'qty':'1'},OTHER]))
    svc.confirm(db,stocked['sales'],o.id,o.version)
    svc.deliver(db,stocked['sales'],o.id,o.version)
    docs=list(db.scalars(select(StockDoc).where(StockDoc.kind=='PX')))
    assert len(docs)==1 and len(docs[0].lines)==2
    assert o.lines[0].stock_doc_id==o.lines[1].stock_doc_id==docs[0].id
    assert balance(db,'K58','TD-0001')==(Decimal('2'),200000)
    gh=svc.deliveries(db,o.id)[0]
    svc.void_delivery(db,stocked['director'],gh.id,'Hủy lần giao trước',gh.version)
    svc.cancel(db,stocked['sales'],o.id,'Trả đủ các dòng',o.version)
    assert balance(db,'K58','TD-0001')==(Decimal('5'),500000)


def test_other_only_order_delivers_and_cancels_without_stock(db, people):
    o=svc.create(db,people['sales'],body(lines=[OTHER]))
    svc.confirm(db,people['sales'],o.id,o.version)
    svc.deliver(db,people['sales'],o.id,o.version)
    assert o.status=='DA_GIAO' and db.query(StockDoc).count()==0 and db.query(StockLedger).count()==0
    gh=svc.deliveries(db,o.id)[0]
    svc.void_delivery(db,people['director'],gh.id,'Hủy lần giao trước',gh.version)
    svc.cancel(db,people['sales'],o.id,'Khách hủy dịch vụ',o.version)
    assert o.receivable==0


def test_cancellation_rolls_back_all_reversals_on_failure(db, stocked, monkeypatch):
    o=svc.create(db,stocked['sales'],body(lines=[GOOD,SECOND]))
    svc.confirm(db,stocked['sales'],o.id,o.version)
    svc.deliver(db,stocked['sales'],o.id,o.version)
    version, audits, ledger = o.version,db.query(AuditLog).count(),db.query(StockLedger).count()
    original=stock_posting.void_in_tx
    calls=[]
    def fail_second(*args):
        calls.append(args[2])
        if len(calls)==2:
            raise BusinessError('DOC_NOT_POSTED','Phiếu xuất không còn ở trạng thái đã ghi.',status=409)
        return original(*args)
    monkeypatch.setattr(stock_posting,'void_in_tx',fail_second)
    failure('DOC_NOT_POSTED',svc.void_delivery,db,stocked['director'],svc.deliveries(db,o.id)[0].id,'Khách trả hàng',1)
    assert len(calls)==2
    db.expire_all()
    assert o.status=='DA_GIAO' and o.version==version and o.receivable==o.total
    assert db.query(AuditLog).count()==audits and db.query(StockLedger).count()==ledger
    assert set(db.scalars(select(StockDoc.status).where(StockDoc.kind=='PX')))=={'DA_GHI'}
    assert balance(db,'K58','TD-0001')==(Decimal('3'),300000)
    assert balance(db,'K145','TD-0002')==(Decimal('2'),800000)


def test_partial_unique_quote_index_enforced_by_database(db, people):
    from sqlalchemy.exc import IntegrityError
    q=quote(db,people['sales'])
    o=svc.create_from_quote(db,people['sales'],q.id,SalesOrderFromQuote(version=q.version))
    duplicate=SalesOrder(number='DB-26-99999',customer_code=q.customer_code,quote_id=q.id,created_by=people['sales'].id)
    db.add(duplicate)
    with pytest.raises(IntegrityError) as exc:
        db.commit()
    assert exc.value.orig.diag.constraint_name=='uq_sales_orders_active_quote'
    db.rollback()
    svc.cancel(db,people['sales'],o.id,'Hủy bản cũ',o.version)
    assert svc.create_from_quote(db,people['sales'],q.id,SalesOrderFromQuote(version=q.version)).id!=o.id


def test_line_kind_constraint_rejects_other_with_one_reference(db, people):
    from app.models import SalesOrderLine
    from sqlalchemy.exc import IntegrityError
    o=svc.create(db,people['sales'],body())
    for refs in [dict(product_code='TD-0001'),dict(warehouse_code='K58')]:
        db.add(SalesOrderLine(order_id=o.id,line_no=3,kind='KHAC',description='Sai loại',qty=1,unit='lần',
                             unit_price=1,vat_rate=0,amount=1,vat_amount=0,**refs))
        with pytest.raises(IntegrityError) as exc:
            db.commit()
        assert exc.value.orig.diag.constraint_name=='ck_sales_order_lines_product_warehouse'
        db.rollback()


def test_concurrent_delivery_posts_once(db, engine, stocked):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    o=svc.create(db,stocked['sales'],body(lines=[GOOD,SECOND]))
    svc.confirm(db,stocked['sales'],o.id,o.version)
    oid, uid, version=o.id,stocked['warehouse'].id,o.version
    barrier=Barrier(2)
    def deliver(_):
        with Session(engine,expire_on_commit=False) as s:
            u=s.get(User,uid)
            svc.load(s,u,oid)  # Cả hai session đều đã đọc cùng version trước khi khóa.
            barrier.wait(timeout=10)
            try:
                svc.deliver(s,u,oid,version)
                return 'OK'
            except BusinessError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(deliver,[1,2]))==['OK','VERSION_CONFLICT']
    db.expire_all()
    assert o.status=='DA_GIAO' and o.version==version+1
    assert db.query(StockDoc).filter(StockDoc.kind=='PX').count()==2
    assert balance(db,'K58','TD-0001')==(Decimal('3'),300000)


def test_concurrent_quote_use_and_manual_numbering(db, engine, people):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    q=quote(db,people['sales'])
    qid, qv, uid=q.id,q.version,people['sales'].id
    barrier=Barrier(2)
    def from_quote(_):
        with Session(engine,expire_on_commit=False) as s:
            u=s.get(User,uid)
            barrier.wait(timeout=10)
            try:
                return svc.create_from_quote(s,u,qid,SalesOrderFromQuote(version=qv)).number
            except BusinessError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(from_quote,[1,2]))==['DB-26-00001','QUOTE_USED']
    barrier=Barrier(2)
    def create(_):
        with Session(engine,expire_on_commit=False) as s:
            u=s.get(User,uid)
            barrier.wait(timeout=10)
            return svc.create(s,u,body()).number
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create,[1,2]))==['DB-26-00002','DB-26-00003']

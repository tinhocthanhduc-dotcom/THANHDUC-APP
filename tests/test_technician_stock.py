from decimal import Decimal

import pytest
from sqlalchemy import select,func

from app import (attachments, inventory_catalogs as catalog, service_charges, service_tickets,
                 stock_docs,stock_posting,stock_min_levels,technician_stock,purchase_orders,sales_orders)
from app.errors import BusinessError
from app.models import AuditLog, FundLedger, StockBalance, StockDoc, StockLedger, Warehouse
from app.schemas import WarehouseIn,WarehousePatch,StockDocIn,ServiceCharges,PurchaseOrderIn,SalesOrderIn,StockMinLevelIn
from test_my_work import people,purchase_people,ticket,DAY
from test_web import service_login,d5_image


@pytest.fixture
def held(db,people):
    p=people
    a=catalog.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K501',name='Kho thợ A',kind='THO',holder_id=p['technician'].id))
    b=catalog.create(db,p['director'],Warehouse,WarehouseIn(code='K502',name='Kho thợ B',kind='THO',holder_id=p['other_tech'].id))
    doc=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code='K58',supplier_code='NCC-0001',doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty=10,unit_cost=1000)]))
    stock_posting.post(db,p['warehouse'],doc.id,doc.version)
    return p,a,b


def transfer(db,p,source='K58',dest='K501',qty=3):
    d=stock_docs.create(db,p['warehouse'],StockDocIn(kind='CK',warehouse_code=source,to_warehouse_code=dest,doc_date=DAY,
        lines=[dict(product_code='TD-0001',qty=qty)]))
    return stock_posting.post(db,p['warehouse'],d.id,d.version)


def balance(db,code):
    db.expire_all()
    b=db.get(StockBalance,(code,'TD-0001'))
    return b.qty if b else Decimal(0)


def charges(db,p,t,qty=1,warehouse='K501',who='technician'):
    return service_charges.set_charges(db,p[who],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[
        dict(kind='VAT_TU',product_code='TD-0001',warehouse_code=warehouse,qty=qty,unit_price=2000,vat_rate=0)]))


def error(code,fn,*args,**kwargs):
    with pytest.raises(BusinessError) as e:fn(*args,**kwargs)
    assert e.value.code==code
    return e.value


def test_holder_validation_uniqueness_audit_and_legacy_defaults(db,people):
    p=people
    for kind,holder in [('THO',None),('THO',p['sales'].id),('KHO',p['technician'].id)]:
        error('BAD_HOLDER',catalog.create,db,p['warehouse'],Warehouse,WarehouseIn(code='K501',name='Kho thợ',kind=kind,holder_id=holder))
    a=catalog.create(db,p['warehouse'],Warehouse,WarehouseIn(code='K501',name='Kho A',kind='THO',holder_id=p['technician'].id))
    error('DUPLICATE_HOLDER',catalog.create,db,p['warehouse'],Warehouse,WarehouseIn(code='K502',name='Kho trùng',kind='THO',holder_id=p['technician'].id))
    assert db.get(Warehouse,'K58').kind=='KHO' and db.get(Warehouse,'K58').holder_id is None
    log=db.scalar(select(AuditLog).where(AuditLog.action=='warehouse.create'))
    assert log.after['holder_id']==p['technician'].id and log.after['kind']=='THO'
    error('VERSION_CONFLICT',catalog.update,db,p['warehouse'],Warehouse,a.code,WarehousePatch(version=a.version+1,name='X'))


def test_transfer_service_consumption_no_double_deduction_and_reopen(db,held):
    p,a,b=held;ck=transfer(db,p)
    assert balance(db,'K58')==7 and balance(db,a.code)==3
    assert db.scalar(select(func.sum(StockBalance.qty)))==10
    assert db.scalar(select(func.sum(StockBalance.value)))==10000
    t=ticket(db,p,'DANG_LAM');charges(db,p,t)
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    service_tickets.transition(db,p['technician'],t.id,'HOAN_THANH',t.version)
    assert balance(db,a.code)==2 and balance(db,'K58')==7
    px=db.get(StockDoc,t.lines[0].stock_doc_id);assert px.kind=='PX' and px.status=='DA_GHI' and px.warehouse_code==a.code
    assert db.scalar(select(func.count()).select_from(StockLedger).where(StockLedger.doc_id==px.id,StockLedger.is_reversal.is_(False)))==1
    service_tickets.transition(db,p['dispatcher'],t.id,'DANG_LAM',t.version,'Sửa lại')
    assert balance(db,a.code)==3 and balance(db,'K58')==7 and px.status=='DA_HUY'
    assert db.query(FundLedger).count()==0


def test_own_warehouse_restriction_dispatcher_override_and_old_fallback(db,held):
    p,a,b=held;t=ticket(db,p,'DANG_LAM')
    e=error('TECH_WAREHOUSE_ONLY',charges,db,p,t,warehouse='K58');assert e.status==409
    charges(db,p,t,warehouse='K58',who='dispatcher');assert t.lines[0].warehouse_code=='K58'
    catalog.update(db,p['warehouse'],Warehouse,a.code,WarehousePatch(version=a.version,name=a.name,active=False))
    charges(db,p,t,warehouse='K58');assert t.lines[0].warehouse_code=='K58'


def test_insufficient_completion_atomic_and_reverse_transfer_disable(db,held):
    p,a,b=held;transfer(db,p,qty=2);t=ticket(db,p,'DANG_LAM');charges(db,p,t,qty=5)
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    n=db.query(StockDoc).count()
    error('NEGATIVE_STOCK',service_tickets.transition,db,p['technician'],t.id,'HOAN_THANH',t.version)
    assert balance(db,a.code)==2 and balance(db,'K58')==8 and t.status=='DANG_LAM'
    assert db.query(StockDoc).count()==n
    error('WAREHOUSE_NOT_EMPTY',catalog.update,db,p['warehouse'],Warehouse,a.code,WarehousePatch(version=a.version,name=a.name,active=False))
    transfer(db,p,source=a.code,dest='K58',qty=2)
    assert balance(db,a.code)==0 and balance(db,'K58')==10
    catalog.update(db,p['warehouse'],Warehouse,a.code,WarehousePatch(version=a.version,name=a.name,active=False))
    assert a.active is False


def test_held_scope_latest_ck_and_readonly_web_permissions(client,db,held):
    p,a,b=held;ck=transfer(db,p);transfer(db,p,dest=b.code,qty=1)
    before=[db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]
    data=technician_stock.held(db,p['technician'])
    assert data['warehouse'].code==a.code and data['rows'][0]['qty']==3 and data['rows'][0]['number']==ck.number
    assert [db.query(m).count() for m in (AuditLog,StockLedger,FundLedger)]==before
    a.name='Chưa lưu'
    db.expire(p['technician'],['roles'])
    technician_stock.held(db,p['technician'])
    assert db.is_modified(a)  # Kể cả tải lại vai trò cũng không autoflush thay đổi đang nhập.
    db.rollback()
    error('FORBIDDEN',technician_stock.held,db,p['technician'],p['other_tech'].id)
    csrf=service_login(client,p['technician'])
    page=client.get('/hang-toi-giu');assert page.status_code==200 and 'TD-0001' in page.text and ck.number in page.text and b.code not in page.text
    assert client.get(f'/hang-toi-giu?tho={p["other_tech"].id}').status_code==403
    assert client.get(f'/hang-toi-giu/phieu/{ck.id}').status_code==200
    assert client.get(f'/phieu-kho/{ck.id}').status_code==403
    assert client.post('/phieu-kho/moi',data=dict(csrf=csrf,kind='CK',warehouse_code='K58',to_warehouse_code=a.code)).status_code==403
    service_login(client,p['warehouse']);page=client.get(f'/hang-toi-giu?tho={p["technician"].id}')
    assert page.status_code==200 and 'Trả kho' in page.text and f'kho_nguon={a.code}' in page.text
    page=client.get(f'/phieu-kho/moi?loai=CK&kho_nguon={a.code}')
    assert page.status_code==200 and f'name="warehouse_code" value="{a.code}" checked' in page.text


def test_purchase_sale_minimum_core_and_forms_exclude_tech_warehouses(client,db,held):
    p,a,b=held
    error('BAD_WAREHOUSE',purchase_orders.create,db,p['warehouse'],PurchaseOrderIn(supplier_code='NCC-0001',order_date=DAY,lines=[dict(product_code='TD-0001',warehouse_code=a.code,qty=1,unit_cost=1000)]))
    error('BAD_WAREHOUSE',sales_orders.create,db,p['sales'],SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code=a.code,qty=1,unit_price=2000)]))
    error('BAD_WAREHOUSE',stock_min_levels.set_level,db,p['warehouse'],a.code,'TD-0001',StockMinLevelIn(min_qty=5,version=0))
    service_login(client,p['warehouse']);page=client.get('/don-mua/moi');assert page.status_code==200 and f'value="{a.code}"' not in page.text
    page=client.get('/ton-kho?canh-bao=1');assert page.status_code==200 and a.code not in page.text
    service_login(client,p['sales']);page=client.get('/don-ban/moi');assert page.status_code==200 and f'value="{a.code}"' not in page.text


def test_material_picker_only_held_default_and_grouped_stock(client,db,held):
    p,a,b=held;transfer(db,p);t=ticket(db,p,'DANG_LAM')
    service_login(client,p['technician'])
    page=client.get(f'/dich-vu/{t.id}')
    assert page.status_code==200 and f'name="warehouse_new" value="{a.code}"' in page.text
    picks=client.get('/dich-vu/tim-vat-tu?q=TD-0001');assert 'TD-0001' in picks.text and 'Đang giữ 3.000' in picks.text
    assert 'TD-0002' not in client.get('/dich-vu/tim-vat-tu').text
    service_login(client,p['warehouse']);page=client.get('/ton-kho')
    assert page.status_code==200 and 'Thợ đang giữ' in page.text and '10.000 đồng' in page.text


def test_tech_stocktake_requires_reason_and_uses_existing_posting(db,held):
    p,a,b=held;transfer(db,p)
    body=dict(kind='KK',warehouse_code=a.code,doc_date=DAY,lines=[dict(product_code='TD-0001',qty=2)])
    error('REASON_REQUIRED',stock_docs.create,db,p['warehouse'],StockDocIn(**body))
    d=stock_docs.create(db,p['warehouse'],StockDocIn(**body,reason='Drum hỏng trong khi vận chuyển'))
    stock_posting.post(db,p['warehouse'],d.id,d.version)
    assert balance(db,a.code)==2


def test_web_create_holder_search_csrf_and_destination_picker(client,db,people):
    p=people;p['technician'].full_name='Nguyễn An';db.commit()
    csrf=service_login(client,p['warehouse'])
    assert 'Nguyễn An' in client.get('/kho/tim-tho?q=nguyen an').text
    data=dict(code='K501',name='Kho Nguyễn An',kind='THO',holder_id=p['technician'].id,active='1',csrf=csrf)
    assert client.post('/kho/moi',data={**data,'csrf':''}).status_code==403
    page=client.post('/kho/moi',data=data);assert page.status_code==200 and 'Kho của thợ' in page.text and 'Nguyễn An' in page.text
    assert db.get(Warehouse,'K501').holder_id==p['technician'].id
    assert 'K501' in client.get('/phieu-kho/tim-kho-tho?q=nguyen an').text
    assert client.get('/kho/K501').status_code==200
    page=client.post('/kho/moi',data={**data,'code':'K502'})
    assert page.status_code==409 and 'Thợ đã có' in page.text and 'value="K502"' in page.text


def test_existing_minimum_hidden_when_empty_warehouse_becomes_technician(db,held,make_user):
    from app import alerts
    p,a,b=held;third=make_user('kt2.third','technician')
    stock_min_levels.set_level(db,p['warehouse'],'K145','TD-0002',StockMinLevelIn(min_qty=5,version=0))
    assert list(db.execute(stock_min_levels.low_stock_query()))
    wh=db.get(Warehouse,'K145')
    catalog.update(db,p['warehouse'],Warehouse,wh.code,WarehousePatch(version=wh.version,name='Kho thợ C',kind='THO',holder_id=third.id))
    assert list(db.execute(stock_min_levels.low_stock_query()))==[]
    assert not any(x['kind']=='DUOI_TON_TOI_THIEU' for x in alerts.collect(db,p['director']))


def test_purchase_receiving_rechecks_kind_after_ordering(db,held,make_user):
    from app import purchase_receipts
    p,a,b=held;third=make_user('kt2.third','technician')
    order=purchase_orders.create(db,p['warehouse'],PurchaseOrderIn(supplier_code='NCC-0001',order_date=DAY,lines=[dict(product_code='TD-0001',warehouse_code='K145',qty=1,unit_cost=1000)]))
    purchase_orders.order(db,p['warehouse'],order.id,order.version)
    wh=db.get(Warehouse,'K145')
    catalog.update(db,p['warehouse'],Warehouse,wh.code,WarehousePatch(version=wh.version,name='Kho thợ C',kind='THO',holder_id=third.id))
    before=db.query(StockDoc).count()
    error('BAD_WAREHOUSE',purchase_receipts.receive,db,p['warehouse'],order.id,order.version,DAY,[dict(order_line_id=order.lines[0].id,qty=1)])
    assert db.query(StockDoc).count()==before and balance(db,'K145')==0


@pytest.mark.parametrize('invalid',['missing_holder','duplicate_holder'])
def test_database_constraints_enforce_holder_and_active_uniqueness(db,held,invalid):
    from sqlalchemy.exc import IntegrityError
    p,a,b=held
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(Warehouse(code='K599',name='Kho sai',kind='THO',holder_id=None if invalid=='missing_holder' else p['technician'].id))
            db.flush()


def test_stock_page_paginates_each_section_and_folds_non_company(client,db,held):
    from app.models import Product
    p,a,b=held;transfer(db,p)
    for i in range(30):
        code=f'TD-{9000+i:04d}';db.add(Product(code=code,name=f'Hàng phân trang {i}',search_text=f'hang phan trang {i} {code.lower()}'))
        db.flush();db.add(StockBalance(warehouse_code='K58',product_code=code,qty=Decimal(2),value=200,avg_cost=100))
    db.commit()
    service_login(client,p['warehouse'])
    page=client.get('/ton-kho').text
    assert '<b>32</b>' in page and '32 dòng hàng' in page                       # ô đếm tính trên mọi dòng đã lọc (31 kho công ty + 1 thợ)
    assert 'Hiện <b>1–25</b> / 31 dòng' in page and 'TD-9023' in page and 'TD-9029' not in page
    assert '?trang=2' in page
    assert '<details class="stock-group" data-stock-group="THO">' in page     # thu gọn mặc định
    page2=client.get('/ton-kho?trang=2').text
    assert 'TD-9029' in page2 and 'TD-9000' not in page2 and 'Hiện <b>26–31</b> / 31 dòng' in page2
    assert client.get('/ton-kho?trang=99').text.count('Hiện <b>26–31</b>')==1  # quá trang cuối → trang cuối
    opened=client.get('/ton-kho?trang=2&trang-tho=1').text
    assert 'data-stock-group="THO" open' in opened and 'TD-9029' in opened
    only=client.get(f'/ton-kho?kho={a.code}').text
    assert 'data-stock-group="THO" open' in only                                # không còn dòng Kho công ty thì mở sẵn
    live=client.get('/ton-kho?q=phan+trang&phan=1').text
    assert 'Hiện <b>1–25</b> / 30 dòng' in live and 'trang=2' in live

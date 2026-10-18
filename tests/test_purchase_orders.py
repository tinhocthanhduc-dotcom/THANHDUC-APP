from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import purchase_orders as svc
from app.errors import BusinessError
from app.models import AuditLog, Product, PurchaseOrder, Supplier, Warehouse
from app.schemas import PurchaseOrderIn, PurchaseOrderUpdate, PurchaseOrderLineIn

DAY = date(2026, 10, 18)


@pytest.fixture
def purchase_people(db, make_user):
    db.add_all([Supplier(code='NCC-0001',name='Minh Phát',search_text='minh phat'),
        Warehouse(code='K58',name='Kho 58'), Warehouse(code='K145',name='Kho 145'),
        Product(code='TD-0001',name='Máy in',search_text='may in',sale_price=200000),
        Product(code='TD-0002',name='Mực in',search_text='muc in')])
    db.commit()
    return {r:make_user('m1.'+r,r) for r in ('warehouse','director','finance','sales','technician')}


@pytest.fixture(autouse=True)
def no_m1_ledger_writes(db):
    yield
    db.rollback()
    for table in ('stock_ledger','fund_ledger'):
        assert db.scalar(text('select count(*) from '+table)) == 0


def body(**changes):
    data=dict(supplier_code='NCC-0001',order_date=DAY,lines=[dict(product_code='TD-0001',warehouse_code='K58',qty='1.5',unit_cost=100000)])
    data.update(changes)
    return PurchaseOrderIn(**data)


def fail(code, fn, *args):
    with pytest.raises(BusinessError) as exc:fn(*args)
    assert exc.value.code==code
    return exc.value


def test_number_lifecycle_locked_update_and_audits(db,purchase_people):
    p=purchase_people;u=p['warehouse'];a=svc.create(db,u,body());b=svc.create(db,u,body())
    assert (a.number,b.number)==('DM-26-00001','DM-26-00002')
    a=svc.update(db,u,a.id,PurchaseOrderUpdate(version=a.version,**body(note='Đã sửa').model_dump()))
    assert a.note=='Đã sửa' and len(a.lines)==1 and a.version==2 and a.lines[0].received_qty==0
    svc.order(db,u,a.id,a.version)
    assert a.status=='DA_DAT' and a.ordered_by==u.id and a.ordered_at is not None
    fail('PRICE_LOCKED',svc.update,db,u,a.id,PurchaseOrderUpdate(version=a.version,**body().model_dump()))
    svc.cancel(db,u,a.id,'Không mua nữa',a.version)
    assert a.status=='DA_HUY'
    svc.cancel(db,p['director'],b.id,'Hủy nháp',b.version)
    logs=list(db.scalars(select(AuditLog).where(AuditLog.entity=='purchase_order').order_by(AuditLog.id)))
    assert len(logs)==6
    assert logs[0].before is None and logs[0].after['number']==a.number
    assert logs[2].before['note']=='' and logs[2].after['note']=='Đã sửa'


def test_money_fractional_quantity_round_half_up_all_vat_rates(db,purchase_people):
    lines=[dict(product_code='TD-0001',warehouse_code='K58',qty='0.5',unit_cost=101,vat_rate=rate) for rate in (0,5,8,10)]
    row=svc.create(db,purchase_people['warehouse'],body(lines=lines))
    assert [(l.amount,l.vat_amount) for l in row.lines]==[(51,0),(51,3),(51,4),(51,5)]
    assert (row.subtotal,row.vat_total,row.total)==(204,12,216)
    assert row.lines[0].qty==Decimal('0.500')
    # Amount 5 with VAT 10% => .5 rounds to 1.
    other=svc.create(db,purchase_people['warehouse'],body(lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=1,unit_cost=5,vat_rate=10)]))
    assert other.total==6


def test_cancel_received_and_reason_statuses(db,purchase_people):
    u=purchase_people['warehouse'];row=svc.create(db,u,body());svc.order(db,u,row.id,row.version)
    fail('REASON_REQUIRED',svc.cancel,db,u,row.id,' ',row.version)
    row.lines[0].received_qty=Decimal('0.001');db.commit()
    fail('CANNOT_CANCEL',svc.cancel,db,u,row.id,'Đã nhận',row.version)
    for status in ('NHAN_MOT_PHAN','DA_NHAN','DA_HUY'):
        row.status=status;db.commit()
        fail('CANNOT_CANCEL',svc.cancel,db,u,row.id,'Không được',row.version)


def test_inactive_catalogs_empty_and_revalidation_on_order(db,purchase_people):
    u=purchase_people['warehouse'];row=svc.create(db,u,body())
    for model,key,error in [(Supplier,'NCC-0001','BAD_SUPPLIER'),(Product,'TD-0001','BAD_PRODUCT'),(Warehouse,'K58','BAD_WAREHOUSE')]:
        obj=db.get(model,key);obj.active=False;db.commit()
        fail(error,svc.create,db,u,body())
        fail(error,svc.order,db,u,row.id,row.version)
        obj.active=True;db.commit()
    fail('EMPTY_ORDER',svc.create,db,u,body(lines=[]))
    fail('EMPTY_ORDER',svc.update,db,u,row.id,PurchaseOrderUpdate(version=row.version,**body(lines=[]).model_dump()))
    assert len(svc.load(db,u,row.id).lines)==1
    # Thất bại không tiêu tốn số DM.
    assert svc.create(db,u,body()).number=='DM-26-00002'


def test_suggest_cost_skips_drafts_canceled_and_keeps_latest_ordered(db,purchase_people):
    u=purchase_people['warehouse']
    assert svc.suggest_cost(db,u,'TD-0001') is None
    a=svc.create(db,u,body());svc.order(db,u,a.id,a.version)
    b=svc.create(db,u,body(lines=[dict(product_code='TD-0001',warehouse_code='K145',qty=1,unit_cost=120000)]))
    assert svc.suggest_cost(db,u,'TD-0001')==100000
    svc.order(db,u,b.id,b.version)
    assert svc.suggest_cost(db,u,'TD-0001')==120000
    svc.cancel(db,u,b.id,'Hủy',b.version)
    assert svc.suggest_cost(db,u,'TD-0001')==100000
    a.status='NHAN_MOT_PHAN';db.commit()
    assert svc.suggest_cost(db,u,'TD-0001')==100000
    assert svc.suggest_cost(db,u,'TD-0002') is None


def test_permissions_search_and_version(db,purchase_people):
    p=purchase_people;row=svc.create(db,p['warehouse'],body())
    assert svc.load(db,p['finance'],row.id).number==row.number
    assert svc.search(db,p['finance'],'DM-26', 'NHAP','NCC-0001')[0].id==row.id
    for role in ('sales','technician','finance'):
        assert fail('FORBIDDEN',svc.create,db,p[role],body()).status==403
        fail('FORBIDDEN',svc.update,db,p[role],row.id,PurchaseOrderUpdate(version=row.version,**body().model_dump()))
        fail('FORBIDDEN',svc.order,db,p[role],row.id,row.version)
        fail('FORBIDDEN',svc.cancel,db,p[role],row.id,'Hủy',row.version)
    for role in ('sales','technician'):
        fail('FORBIDDEN',svc.load,db,p[role],row.id)
    fail('VERSION_CONFLICT',svc.order,db,p['warehouse'],row.id,0+row.version+1)
    fail('BAD_STATUS',svc.search,db,p['finance'],'','SAI')


def test_two_editors_one_stale_version_real_postgres(db,engine,purchase_people):
    p=purchase_people;row=svc.create(db,p['warehouse'],body());ident,version=row.id,row.version;barrier=Barrier(2)
    def edit(role,note):
        with Session(engine) as s:
            user=s.get(type(p[role]),p[role].id);barrier.wait()
            try:
                svc.update(s,user,ident,PurchaseOrderUpdate(version=version,**body(note=note).model_dump()))
                return 'OK'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda pair:edit(*pair),[('warehouse','Kho sửa'),('director','Giám đốc sửa')]))
    assert sorted(results)==['OK','VERSION_CONFLICT']
    db.expire_all();assert svc.load(db,p['warehouse'],ident).note in ('Kho sửa','Giám đốc sửa')


def test_default_vat_eight_explicit_zero_and_validation(db,purchase_people):
    u=purchase_people['warehouse'];row=svc.create(db,u,body())
    assert row.lines[0].vat_rate==8 and row.total==162000
    row=svc.create(db,u,body(lines=[dict(product_code='TD-0001',warehouse_code='K58',qty=1,unit_cost=100000,vat_rate=0)]))
    assert row.lines[0].vat_rate==0 and row.total==100000
    for changes in ({'qty':0},{'qty':'0.0001'},{'unit_cost':-1},{'vat_rate':7},{'received_qty':1}):
        with pytest.raises(ValidationError):PurchaseOrderLineIn(**{**body().lines[0].model_dump(),**changes})


def test_api_create_update_order_cancel_and_readonly_finance(client,db,login,purchase_people):
    headers=login('m1.api.warehouse','warehouse')
    payload=body().model_dump(mode='json')
    r=client.post('/purchase-orders',json=payload,headers=headers);assert r.status_code==201,r.text
    data=r.json();ident=data['id'];assert data['number']=='DM-26-00001'
    r=client.put(f'/purchase-orders/{ident}',json={**payload,'version':data['version'],'note':'API'},headers=headers)
    assert r.status_code==200
    r=client.post(f'/purchase-orders/{ident}/order',json={'version':r.json()['version']},headers=headers);assert r.status_code==200
    assert client.get('/purchase-orders/suggest-cost/TD-0001',headers=headers).json()['unit_cost']==100000
    data=r.json()
    finance=login('m1.api.finance','finance')
    assert client.get('/purchase-orders',headers=finance).status_code==200
    assert client.get(f'/purchase-orders/{ident}',headers=finance).status_code==200
    assert client.post('/purchase-orders',json=payload,headers=finance).status_code==403
    r=client.post(f'/purchase-orders/{ident}/cancel',json={'version':data['version'],'reason':'API hủy'},headers=headers)
    assert r.status_code==200 and r.json()['status']=='DA_HUY'


@pytest.mark.parametrize('received',[-1,2])
def test_received_quantity_database_constraint(db,purchase_people,received):
    from sqlalchemy.exc import IntegrityError
    row=svc.create(db,purchase_people['warehouse'],body())
    with pytest.raises(IntegrityError):
        db.execute(text('update purchase_order_lines set received_qty=:qty where order_id=:id'),{'qty':received,'id':row.id})
        db.commit()
    db.rollback()
    assert svc.load(db,purchase_people['warehouse'],row.id).lines[0].received_qty==0

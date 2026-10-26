"""SX1: transactions, inventory valuation, scoped reads and monthly production entitlement."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import production as svc, stock_docs, stock_posting, my_work, commissions, kpi
from app.errors import BusinessError
from app.models import (ProductionOrder, ProductionOrderLine, Product, StockDoc, StockLedger, StockBalance, FundLedger,
                        CommissionLine, Employee, Equipment, User, AuditLog, DocCounter)
from app.schemas import ProductionIn, ProductionComplete, ProductionReceive, StockDocIn
from app.security import issue_token
from test_my_work import people, purchase_people
from test_technician_stock import held
from test_web import service_login

JAN=date(2027,1,1)


@pytest.fixture
def shop(db, held, monkeypatch):
    p,a,b=held
    clock={'now':datetime(2027,1,31,23,30,tzinfo=svc.TZ)}
    monkeypatch.setattr(svc,'_now',lambda:clock['now'])
    monkeypatch.setattr(commissions,'_now',lambda:datetime(2027,2,2,tzinfo=svc.TZ))
    monkeypatch.setattr(kpi,'_now',lambda:datetime(2027,2,2,tzinfo=svc.TZ))
    db.add_all([Product(code='TD-0100',name='Hộp mực tái sinh'),
                Product(code='TD-0101',name='Mực',unit='kg'),Product(code='TD-0102',name='Chip'),Product(code='TD-0103',name='Vỏ')])
    db.add(Employee(user_id=p['technician'].id,code='NV-SX',position='Kỹ thuật',start_date=date(2026,1,1),created_by=p['director'].id))
    db.commit()
    d=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code=a.code,supplier_code='NCC-0001',doc_date=JAN,lines=[
        dict(product_code=c,qty=q,unit_cost=v) for c,q,v in [('TD-0101',2,600000),('TD-0102',2,90000),('TD-0103',5,100000)]]))
    stock_posting.post(db,p['warehouse'],d.id,d.version)
    return p,a,b,clock


def create(db,shop,role='technician',**kw):
    p,a,_,_=shop
    values=dict(kind='DO_MUC',product_code='TD-0100',qty_plan=10,to_warehouse_code='K58',lines=[
        dict(product_code='TD-0101',qty_plan=1),dict(product_code='TD-0102',qty_plan=2),dict(product_code='TD-0103',qty_plan=5)])
    values.update(kw)
    return svc.create(db,p[role],ProductionIn(**values))


def finish(db,shop,row=None,good=9,scrap=1,reason=''):
    p,_,_,_=shop;row=row or create(db,shop)
    svc.start(db,p['technician'],row.id,row.version)
    return svc.complete(db,p['technician'],row.id,ProductionComplete(version=row.version,qty_good=good,qty_scrap=scrap,void_reason=reason,
        lines=[dict(line_no=i,qty_used=q) for i,q in [(1,Decimal('.5')),(2,1),(3,4)]]))


def receive(db,shop,row,qty=9,**kw):
    return svc.receive(db,shop[0]['warehouse'],row.id,ProductionReceive(version=row.version,qty_received=qty,**kw))


def err(code, fn, *args, **kwargs):
    with pytest.raises(BusinessError) as e:fn(*args,**kwargs)
    assert e.value.code==code
    return e.value


def amounts(db):
    db.expire_all()
    return [(x.warehouse_code,x.product_code,x.qty,x.value) for x in db.scalars(select(StockBalance).order_by(StockBalance.warehouse_code,StockBalance.product_code))]


def test_self_create_work_queues_and_minimum_pages(db,client,shop):
    p,a,_,_=shop;r=create(db,shop)
    assert r.number=='SX-27-00001' and r.status=='MOI' and r.technician_id==p['technician'].id and r.from_warehouse_code==a.code
    g=my_work.filtered(db,p['technician'],'production_work')
    assert g['count']==1 and g['rows'][0]['customer']=='Làm hàng: 10 Hộp mực tái sinh' and g['rows'][0]['link']=='/san-xuat/'+r.number
    from app.web.work import _band
    assert _band('production_work')==_band('production_receive')=='day'
    csrf=service_login(client,p['technician'])
    for path in ['/san-xuat','/san-xuat/moi','/san-xuat/'+r.number,'/tho','/viec-cua-toi']:
        response=client.get(path);assert response.status_code==200,response.text
    assert r.number in client.get('/tho').text
    assert len(r.lines)==3 and db.scalar(select(AuditLog.id).where(AuditLog.action=='production.create'))


def test_complete_actual_usage_ledger_cost_and_receive_queue(db,shop):
    p,a,_,_=shop;r=finish(db,shop)
    px=db.get(StockDoc,r.px_doc_id)
    assert px.status=='DA_GHI' and px.source_kind=='SAN_XUAT' and px.warehouse_code==a.code
    assert [l.qty for l in px.lines]==[Decimal('.5'),1,4]
    assert r.material_cost==790000== -db.scalar(select(func.sum(StockLedger.amount)).where(StockLedger.doc_id==px.id))
    assert r.unit_cost==87778 and r.status=='HOAN_THANH'
    group=my_work.filtered(db,p['warehouse'],'production_receive')
    assert group['count']==1 and group['rows'][0]['customer']=='Nhận thành phẩm '+r.number
    assert db.query(FundLedger).count()==0


def test_short_stock_rolls_back_order_lines_docs_audit_and_counter(db,shop):
    p,_,_,_=shop;r=create(db,shop);svc.start(db,p['technician'],r.id,r.version)
    before=(svc.snapshot(r),amounts(db),db.query(StockDoc).count(),db.query(StockLedger).count(),db.query(AuditLog).count(),db.get(DocCounter,('PX',2027)))
    err('NEGATIVE_STOCK',svc.complete,db,p['technician'],r.id,ProductionComplete(version=r.version,qty_good=9,qty_scrap=1,lines=[dict(line_no=1,qty_used=3)]))
    assert (svc.snapshot(svc.load(db,p['technician'],r.id)),amounts(db),db.query(StockDoc).count(),db.query(StockLedger).count(),db.query(AuditLog).count(),db.get(DocCounter,('PX',2027)))==before


@pytest.mark.parametrize('qty,cost,scrap',[(9,87778,1),(8,98750,2)])
def test_receive_rounding_and_existing_average(db,shop,qty,cost,scrap):
    p,_,_,_=shop
    d=stock_docs.create(db,p['warehouse'],StockDocIn(kind='PN',warehouse_code='K58',supplier_code='NCC-0001',doc_date=JAN,lines=[dict(product_code='TD-0100',qty=4,unit_cost=90000)]))
    stock_posting.post(db,p['warehouse'],d.id,d.version)
    r=finish(db,shop);receive(db,shop,r,qty,note='Một hộp không đạt' if qty==8 else '')
    pn=db.get(StockDoc,r.pn_doc_id);b=db.get(StockBalance,('K58','TD-0100'))
    assert pn.kind=='PN' and pn.supplier_code is None and pn.source_kind=='SAN_XUAT' and pn.status=='DA_GHI'
    assert r.qty_good==qty and r.qty_scrap==scrap and r.unit_cost==cost
    assert b.qty==4+qty and b.value==4*90000+qty*cost
    assert b.avg_cost==stock_posting._round(Decimal(b.value)/b.qty)
    assert r.received_by==p['warehouse'].id and r.received_at is not None


def test_serial_count_duplicates_and_enrollment_only_on_sale(db,shop):
    p,_,_,_=shop;product=db.get(Product,'TD-0100');product.warranty_months=3;db.commit()
    r=finish(db,shop);before=amounts(db)
    for serials,code in [([], 'SERIAL_REQUIRED'),(['S']*9,'SERIAL_DUPLICATE'),(['S1']*8,'SERIAL_REQUIRED')]:
        err(code,receive,db,shop,r,serials=serials)
        assert r.status=='HOAN_THANH' and amounts(db)==before and r.pn_doc_id is None
    receive(db,shop,r,serials=['SX-'+str(i) for i in range(9)])
    assert len(db.get(StockDoc,r.pn_doc_id).lines)==9
    assert db.query(Equipment).count()==0


def test_all_scrapped_keeps_px_no_pn_and_reason_required(db,shop):
    p,_,_,_=shop;r=create(db,shop);svc.start(db,p['technician'],r.id,r.version)
    body=ProductionComplete(version=r.version,qty_good=0,qty_scrap=10,lines=[dict(line_no=1,qty_used=1)])
    err('REASON_REQUIRED',svc.complete,db,p['technician'],r.id,body)
    assert r.status=='DANG_LAM' and r.px_doc_id is None
    r=svc.complete(db,p['technician'],r.id,body.model_copy(update={'void_reason':'Hỏng hết'}))
    assert r.status=='DA_HUY' and r.material_cost==600000 and r.unit_cost==0 and r.px_doc_id and not r.pn_doc_id
    assert db.get(StockDoc,r.px_doc_id).status=='DA_GHI' and db.query(FundLedger).count()==0


def test_permissions_api_and_web_do_not_expose_costs(db,client,shop):
    p,a,b,_=shop;r=create(db,shop)
    for role in ('technician','dispatcher','warehouse','director'):
        response=client.get('/production-orders/'+r.number,headers={'Authorization':'Bearer '+issue_token(p[role])})
        assert response.status_code==200,response.text
        assert ('material_cost' in response.json())==(role in ('warehouse','director'))
    for role in ('sales','other_tech'):
        response=client.get('/production-orders/'+r.number,headers={'Authorization':'Bearer '+issue_token(p[role])})
        assert response.status_code==403
    service_login(client,p['sales']);assert client.get('/san-xuat').status_code==403
    service_login(client,p['other_tech']);assert client.get('/san-xuat/'+r.number).status_code==403
    r=finish(db,shop,r)
    response=client.post('/production-orders/'+r.number+'/nhan-kho',json=dict(version=r.version,qty_received=9),headers={'Authorization':'Bearer '+issue_token(p['technician'])})
    assert response.status_code==403 and r.status=='HOAN_THANH'
    p['warehouse'].warehouse_codes=['K145'];db.commit()
    err('FORBIDDEN',receive,db,shop,r)
    assert my_work.filtered(db,p['warehouse'],'production_receive')['count']==0
    err('FORBIDDEN',create,db,shop,technician_id=p['other_tech'].id)
    err('FORBIDDEN',create,db,shop,from_warehouse_code=b.code)


def test_commissions_and_kpi_vietnam_month_no_double_count(db,shop):
    p,_,_,_=shop
    commissions.add_rule(db,p['director'],dict(target='THO',service_kind=None,pct=10,fixed_amount=100,effective_from=JAN,reason='Chung'))
    commissions.add_rule(db,p['director'],dict(target='THO',service_kind='SAN_XUAT',pct=50,fixed_amount=5000,effective_from=JAN,reason='Mỗi thành phẩm'))
    r=finish(db,shop)
    assert not [x for x in commissions.draft(db,JAN) if x['source']=='PHIEU_SX']
    receive(db,shop,r)
    rows=[x for x in commissions.draft(db,JAN) if x['source']=='PHIEU_SX']
    assert len(rows)==1 and rows[0]['amount']==45000 and rows[0]['base']==0 and rows[0]['paid_ratio']==1 and rows[0]['production_id']==r.id and rows[0]['pct']==0
    assert kpi.compute(db,JAN)[p['technician'].id]['tho_san_xuat']==9
    # Month uses received_at in Vietnam; 23:30 Jan 31 remains January.
    commissions.lock(db,p['director'],JAN)
    assert db.scalar(select(CommissionLine.amount).where(CommissionLine.production_id==r.id))==45000
    assert [x for x in commissions.draft(db,date(2027,2,1)) if x['source']=='PHIEU_SX']==[]
    assert kpi.compute(db,date(2027,2,1))[p['technician'].id]['tho_san_xuat']==0
    assert commissions.draft(db,JAN)[0]['number']==r.number


def test_void_before_usage_source_docs_locked_api_and_web(db,client,shop):
    p,_,_,_=shop;r=create(db,shop);svc.void(db,p['technician'],r.id,'Không làm nữa',r.version)
    assert r.status=='DA_HUY' and r.px_doc_id is None
    r=finish(db,shop);receive(db,shop,r)
    err('BAD_STATUS',svc.void,db,p['director'],r.id,'Sai phiếu',r.version)
    csrf=service_login(client,p['warehouse']);before=amounts(db)
    for ident in (r.px_doc_id,r.pn_doc_id):
        doc=db.get(StockDoc,ident)
        response=client.post('/stock-docs/'+str(ident)+'/void',json=dict(version=doc.version,reason='Không hủy rời'),headers={'Authorization':'Bearer '+issue_token(p['warehouse'])})
        assert response.status_code==409 and response.json()['error']=='SOURCE_LOCKED',response.text
        response=client.post('/phieu-kho/'+str(ident)+'/void',data=dict(csrf=csrf,version=doc.version,reason='Không hủy rời'))
        assert response.status_code==409 and 'SOURCE_LOCKED' in response.text
    assert amounts(db)==before and r.status=='DA_NHAP'


def test_production_pn_supplier_exception_does_not_skip_unit_cost(db,shop):
    p,_,_,_=shop
    body=StockDocIn(kind='PN',warehouse_code='K58',doc_date=JAN,lines=[dict(product_code='TD-0100',qty=1,unit_cost=100)])
    err('BAD_SUPPLIER',stock_docs.create,db,p['warehouse'],body)
    doc=stock_docs.create(db,p['warehouse'],body,source_kind='SAN_XUAT')
    assert doc.supplier_code is None
    bad=body.model_copy(update={'lines':[body.lines[0].model_copy(update={'unit_cost':None})]})
    err('BAD_UNIT_COST',stock_docs.create,db,p['warehouse'],bad,source_kind='SAN_XUAT')


def test_assignment_new_materials_and_version_conflicts(db,shop):
    p,a,_,_=shop;r=create(db,shop,role='dispatcher',lines=[])
    assert r.from_warehouse_code is None
    svc.assign(db,p['warehouse'],r.number,p['technician'].id,r.version)
    assert r.from_warehouse_code==a.code
    old=r.version;svc.start(db,p['dispatcher'],r.id,r.version)
    err('VERSION_CONFLICT',svc.complete,db,p['technician'],r.id,ProductionComplete(version=old,qty_good=1,qty_scrap=0))
    svc.complete(db,p['technician'],r.id,ProductionComplete(version=r.version,qty_good=1,qty_scrap=0,lines=[dict(product_code='TD-0101',qty_used='.5'),dict(product_code='TD-0102',qty_used=0)]))
    assert len(db.get(StockDoc,r.px_doc_id).lines)==1 and r.material_cost==300000


def test_material_product_identifier_updates_planned_line_and_skips_new_zero(db,shop):
    p,_,_,_=shop;r=create(db,shop);svc.start(db,p['technician'],r.id,r.version)
    svc.complete(db,p['technician'],r.id,ProductionComplete(version=r.version,qty_good=1,qty_scrap=0,
        lines=[dict(product_code='TD-0101',qty_used='.5'),dict(product_code='TD-0001',qty_used=0)]))
    assert len(r.lines)==3 and r.lines[0].qty_used==Decimal('.5') and r.lines[0].qty_plan==1
    assert r.material_cost==300000 and len(db.get(StockDoc,r.px_doc_id).lines)==1


def test_concurrent_receive_only_one_pn(db,engine,shop):
    p,_,_,_=shop;r=finish(db,shop);version=r.version;uid=p['warehouse'].id;ident=r.id;db.rollback()
    barrier=Barrier(2)
    def worker():
        with Session(engine) as session:
            user=session.get(User,uid);barrier.wait()
            try:svc.receive(session,user,ident,ProductionReceive(version=version,qty_received=9));return 'ok'
            except BusinessError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as ex:out=list(ex.map(lambda _:worker(),range(2)))
    assert sorted(out)==['VERSION_CONFLICT','ok']
    db.expire_all();r=db.get(ProductionOrder,ident)
    assert db.scalar(select(func.count()).select_from(StockDoc).where(StockDoc.source_kind=='SAN_XUAT',StockDoc.kind=='PN'))==1
    assert db.get(StockBalance,('K58','TD-0100')).qty==9


def test_clear_pilot_registration_and_delete_guard(db,shop):
    from scripts.clear_pilot_data import WIPE,KEEP,delete_order,all_tables
    from sqlalchemy.exc import DBAPIError
    assert {'production_orders','production_order_lines'}<=WIPE
    assert not all_tables(db)-KEEP-WIPE
    assert delete_order(db,WIPE).index('production_order_lines')<delete_order(db,WIPE).index('production_orders')
    r=create(db,shop)
    with pytest.raises(DBAPIError):db.execute(text('delete from production_orders where id=:id'),dict(id=r.id))
    db.rollback();assert db.get(ProductionOrder,r.id)


def test_populated_migration_downgrade_preserves_locked_commission(db,engine,shop):
    from alembic import command
    from alembic.config import Config
    p,_,_,_=shop;r=finish(db,shop);receive(db,shop,r)
    commissions.add_rule(db,p['director'],dict(target='THO',service_kind='SAN_XUAT',pct=0,
        fixed_amount=5000,effective_from=JAN,reason='Sản xuất'))
    commissions.lock(db,p['director'],JAN)
    ledger=db.execute(select(StockLedger.id,StockLedger.amount,StockLedger.balance_qty,StockLedger.balance_value).order_by(StockLedger.id)).all()
    db.close()  # No open transaction may hold schema locks while Alembic runs.
    cfg=Config('alembic.ini');cfg.attributes['database_url']=engine.url.render_as_string(hide_password=False)
    command.downgrade(cfg,'0062')
    try:
        with engine.connect() as conn:
            rows=conn.execute(text("select source,amount from commission_lines where month='2027-01-01'")).all()
            assert rows==[('DIEU_CHINH',45000)]
            assert conn.scalar(text("select total from commission_months where month='2027-01-01'"))==45000
            assert conn.execute(select(StockLedger.id,StockLedger.amount,StockLedger.balance_qty,StockLedger.balance_value).order_by(StockLedger.id)).all()==ledger
    finally:
        command.upgrade(cfg,'head')

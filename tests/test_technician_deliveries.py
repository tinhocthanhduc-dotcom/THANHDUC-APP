"""S4: nghiệm thu giao đơn trên PostgreSQL thật, API và form thật."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import (attachments, inventory_catalogs, my_work, sales_orders, sales_receipts,
                 service_charges, service_tickets, stock_reservations, technician_deliveries as svc, technician_stock)
from app.config import get_settings
from app.models import (Attachment, AuditLog, Customer, FundLedger, SalesDelivery, SalesDeliveryAttempt,
                        StockBalance, StockDoc, StockLedger, StockReservation, Warehouse)
from app.schemas import (SalesDeliveryAssign, SalesDeliveryFailed, SalesDeliveryMove, SalesOrderOptions,
                         SalesReceiptReport, ServiceCharges, TechnicianDeliveryIn, WarehouseIn)
from app.security import issue_token
from test_stock_reservations import people, purchase_people, stock, order, holds, fail
from test_my_work import ticket, DAY
from test_web import d5_image, service_login
from web_forms import Forms


@pytest.fixture
def setup(db,people):
    p=people
    for code,who in [('K501','technician'),('K502','other_tech')]:
        inventory_catalogs.create(db,p['warehouse'],Warehouse,WarehouseIn(code=code,name='Kho '+who,kind='THO',holder_id=p[who].id))
    stock(db,p,20)
    return p


def assigned(db,p,qty=4):
    o=order(db,p,qty)
    sales_orders.change_options(db,p['sales'],o.id,SalesOrderOptions(version=o.version,fulfillment='THO_GIAO'))
    svc.assign(db,p['dispatcher'],o.id,SalesDeliveryAssign(version=o.version,assignee_id=p['technician'].id))
    return o


def move(db,p,o,qty,mode='transfer',who='warehouse'):
    return svc.move(db,p[who],o.id,mode,SalesDeliveryMove(version=o.version,lines=[dict(order_line_no=1,qty=qty)]))


def deliver(db,p,o,qty=4,photos=None,**kw):
    body=TechnicianDeliveryIn(version=o.version,lines=[dict(order_line_no=1,qty=qty)],**kw)
    return svc.deliver(db,p['technician'],o.id,body,[('giao.png',d5_image())] if photos is None else photos)


def qty(db,wh):
    db.expire_all()
    b=db.get(StockBalance,(wh,'TD-0001'))
    return b.qty if b else Decimal(0)


def hold_state(db,o):
    return [(r.warehouse_code,r.qty) for r in holds(db,o)]


def counts(db):
    return tuple(db.query(m).count() for m in (SalesDelivery,StockDoc,StockLedger,StockReservation,Attachment,FundLedger,AuditLog))


def test_01_transfer_and_03_delivery_photo_warehouse(db,setup):
    p=setup;o=assigned(db,p);total=db.scalar(select(func.sum(StockBalance.qty)))
    move(db,p,o,4)
    assert qty(db,'K58')==16 and qty(db,'K501')==4
    assert db.scalar(select(func.sum(StockBalance.qty)))==total
    assert hold_state(db,o)==[('K501',4)]
    assert holds(db,o,'DA_NHA')[0].reason.startswith('Chuyển cho thợ CK-')
    deliver(db,p,o)
    gh=sales_orders.deliveries(db,o.id)[0]
    assert gh.lines[0].warehouse_code=='K501'
    assert db.get(StockDoc,gh.lines[0].stock_doc_id).warehouse_code=='K501'
    assert o.lines[0].warehouse_code=='K58' and qty(db,'K501')==0
    assert not holds(db,o) and holds(db,o,'DA_XUAT')[0].qty==4
    assert attachments.list_for(db,p['technician'],'sales_delivery',str(gh.id))[0].tag=='GIAO_HANG'


def test_02_service_cannot_take_reserved_technician_stock(db,setup):
    p=setup;o=assigned(db,p);move(db,p,o,4)
    t=ticket(db,p,'DANG_LAM')
    service_charges.set_charges(db,p['technician'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[
        dict(kind='VAT_TU',product_code='TD-0001',warehouse_code='K501',qty=1,unit_price=200,vat_rate=0)]))
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    fail('RESERVED_STOCK',service_tickets.transition,db,p['technician'],t.id,'HOAN_THANH',t.version)
    assert qty(db,'K501')==4 and hold_state(db,o)==[('K501',4)]


@pytest.mark.parametrize('photos,code', [([], 'PHOTO_REQUIRED'),([('x.png',b'bad')],'BAD_FILE_TYPE'),
    ([('x.pdf',b'%PDF-bad')],'BAD_FILE_TYPE'),([('ok.png',d5_image()),('bad.png',b'bad')],'BAD_FILE_TYPE')])
def test_03_invalid_photo_rolls_back_and_cleans_files(db,setup,photos,code):
    p=setup;o=assigned(db,p);move(db,p,o,4);before=counts(db)
    files=set(Path(get_settings().files_dir).rglob('*.jpg'))
    fail(code,deliver,db,p,o,photos=photos,receipt=SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=500))
    assert counts(db)==before and files==set(Path(get_settings().files_dir).rglob('*.jpg'))
    assert qty(db,'K501')==4 and hold_state(db,o)==[('K501',4)]


def test_04_partial_delivery_return(db,setup):
    p=setup;o=assigned(db,p,10);move(db,p,o,10);deliver(db,p,o,6)
    assert qty(db,'K501')==4 and hold_state(db,o)==[('K501',4)]
    move(db,p,o,4,'return')
    assert qty(db,'K501')==0 and qty(db,'K58')==14 and hold_state(db,o)==[('K58',4)]
    assert o.status=='GIAO_MOT_PHAN' and o.receivable==1200


def test_05_failed_and_immutable_attempts_and_label(db,setup):
    p=setup;o=assigned(db,p);move(db,p,o,4);before=counts(db)
    fail('PROMISED_DATE_REQUIRED',svc.failed,db,p['technician'],o.id,SalesDeliveryFailed(version=o.version,reason='HEN_LAI'))
    svc.failed(db,p['technician'],o.id,SalesDeliveryFailed(version=o.version,reason='HEN_LAI',promised_date=DAY+timedelta(days=1),note='Khách hẹn'))
    assert o.promised_date==DAY+timedelta(days=1) and hold_state(db,o)==[('K501',4)]
    assert qty(db,'K501')==4 and counts(db)[:6]==before[:6]
    assert svc.state(db,p['technician'],o)['label']=='Giao không thành'
    for sql in ('UPDATE sales_delivery_attempts SET note=\'sua\'', 'DELETE FROM sales_delivery_attempts'):
        with pytest.raises(DBAPIError):
            db.execute(text(sql));db.commit()
        db.rollback()
    assert db.query(SalesDeliveryAttempt).count()==1
    deliver(db,p,o)
    assert svc.state(db,p['technician'],o)['label']!='Giao không thành'


@pytest.mark.parametrize('action',['deliver','failed','report','view'])
def test_06_other_technician_forbidden(db,setup,action):
    p=setup;o=assigned(db,p);move(db,p,o,4);u=p['other_tech']
    if action=='deliver':fail('FORBIDDEN',svc.deliver,db,u,o.id,TechnicianDeliveryIn(version=o.version),[])
    elif action=='failed':fail('FORBIDDEN',svc.failed,db,u,o.id,SalesDeliveryFailed(version=o.version,reason='KHACH_VANG'))
    elif action=='report':fail('FORBIDDEN',sales_receipts.report,db,u,o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=1))
    else:fail('FORBIDDEN',svc.load,db,u,o.id)
    assert qty(db,'K501')==4


def test_06_other_technician_cannot_view_photo(db,setup):
    p=setup;o=assigned(db,p);move(db,p,o,4);deliver(db,p,o)
    photo=db.scalar(select(Attachment).where(Attachment.entity=='sales_delivery'))
    fail('FORBIDDEN',attachments.open_file,db,p['other_tech'],photo.id)


def test_07_report_held_self_confirm_and_finance(db,setup,configured_finance):
    p=setup;o=order(db,p,lines=[dict(kind='HANG',product_code='TD-0001',warehouse_code='K58',qty=4,unit_price=125000,vat_rate=0)])
    sales_orders.change_options(db,p['sales'],o.id,SalesOrderOptions(version=o.version,fulfillment='THO_GIAO'))
    svc.assign(db,p['dispatcher'],o.id,SalesDeliveryAssign(version=o.version,assignee_id=p['technician'].id))
    move(db,p,o,4);deliver(db,p,o,receipt=SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=500000))
    held=sales_receipts.held_by(db,p['technician']);assert held['total']==500000
    r=db.get(sales_receipts.SalesReceipt,held['rows'][0]['id'])
    assert fail('SELF_CONFIRM',sales_receipts.confirm,db,p['technician'],r.id,r.version).status==403
    fail('FORBIDDEN',sales_receipts.reject,db,p['technician'],r.id,'Nhầm',r.version)
    fail('FORBIDDEN',sales_receipts.void,db,p['technician'],r.id,'Nhầm',r.version)
    sales_receipts.confirm(db,p['finance'],r.id,r.version)
    assert sales_receipts.held_by(db,p['technician'])['total']==0


def test_08_use_existing_without_ck_and_other_orders_reservations(db,setup):
    p=setup;stock(db,p,2,warehouse='K501');o=assigned(db,p)
    before=counts(db)
    move(db,p,o,2,'use',who='dispatcher')
    assert counts(db)[1:3]==before[1:3]
    assert stock_reservations.available(db,'K58','TD-0001')==18
    assert sorted(hold_state(db,o))==[('K501',2),('K58',2)]
    b=assigned(db,p)
    fail('RESERVED_STOCK',move,db,p,b,1,'use')


def test_09_reassign_requires_return(db,setup):
    p=setup;o=assigned(db,p);move(db,p,o,4)
    body=lambda:SalesDeliveryAssign(version=o.version,assignee_id=p['other_tech'].id)
    fail('HELD_BY_TECH',svc.assign,db,p['dispatcher'],o.id,body())
    move(db,p,o,4,'return');svc.assign(db,p['dispatcher'],o.id,body())
    assert o.delivery_assignee_id==p['other_tech'].id


def test_10_multiwarehouse_hold_deliver_only_technician_keep_total(db,setup):
    p=setup;o=assigned(db,p,10);move(db,p,o,4)
    assert sorted(hold_state(db,o))==[('K501',4),('K58',6)]
    stock_reservations.keep_more(db,p['warehouse'],o.id,o.version)
    assert sum(r.qty for r in holds(db,o))==10
    fail('TECH_DELIVERY_REQUIRED',sales_orders.deliver,db,p['warehouse'],o.id,o.version)
    fail('OVER_DELIVERY',deliver,db,p,o,5)
    deliver(db,p,o,4)
    assert hold_state(db,o)==[('K58',6)] and holds(db,o,'DA_XUAT')[0].warehouse_code=='K501'


def test_11_service_ticket_customer_open_and_links(db,setup,client):
    p=setup;o=assigned(db,p);t=ticket(db,p,'DANG_LAM')
    other=Customer(code='KTD-999999',kind='CA_NHAN',name='Khác',search_text='khac');db.add(other);db.commit()
    t.customer_code=other.code;db.commit()
    body=lambda:SalesDeliveryAssign(version=o.version,assignee_id=p['technician'].id,service_ticket_id=t.id)
    fail('BAD_SERVICE_TICKET',svc.assign,db,p['dispatcher'],o.id,body())
    t.customer_code=o.customer_code;db.commit();svc.assign(db,p['dispatcher'],o.id,body())
    service_login(client,p['dispatcher']);r=client.get(f'/giao-don/{o.id}');assert r.status_code==200 and f'/dich-vu/{t.id}' in r.text
    r=client.get(f'/dich-vu/{t.id}');assert r.status_code==200 and f'/giao-don/{o.id}' in r.text


def test_12_api_multipart_privacy_version_and_web_form_replay(db,setup,client):
    p=setup;o=assigned(db,p);move(db,p,o,4)
    auth={'Authorization':'Bearer '+issue_token(p['technician'])}
    response=client.get(f'/sales-orders/{o.id}/technician-delivery',headers=auth)
    assert response.status_code==200 and 'unit_cost' not in response.text and 'stock_doc_id' not in response.text
    bad=client.post(f'/sales-orders/{o.id}/technician-deliver',headers=auth,data={'body':'bad'},files={'photos':('g.png',d5_image(),'image/png')})
    assert bad.status_code==400
    stale=TechnicianDeliveryIn(version=o.version-1,lines=[dict(order_line_no=1,qty=4)])
    assert client.post(f'/sales-orders/{o.id}/technician-deliver',headers=auth,data={'body':stale.model_dump_json()},files={'photos':('g.png',d5_image(),'image/png')}).status_code==409
    service_login(client,p['technician'])
    page=client.get(f'/giao-don/{o.id}');assert page.status_code==200 and 'capture="environment"' in page.text
    assert client.get('/don-ban').status_code==403
    form=next(f for f in Forms(page.text,page.url).forms if f['path'].endswith('/failed'))['fields']
    assert client.request('POST',f'/giao-don/{o.id}/failed',data={'version':o.version,'reason':'KHACH_VANG'}).status_code==403
    form.update(reason='KHACH_VANG')
    assert client.post(f'/giao-don/{o.id}/failed',data=form,follow_redirects=False).status_code==303
    replay=client.post(f'/giao-don/{o.id}/failed',data=form,follow_redirects=False)
    assert replay.status_code==303 and db.query(SalesDeliveryAttempt).count()==1
    db.refresh(o)
    body=TechnicianDeliveryIn(version=o.version,lines=[dict(order_line_no=1,qty=4)])
    response=client.post(f'/sales-orders/{o.id}/technician-deliver',headers=auth,data={'body':body.model_dump_json()},files={'photos':('g.png',d5_image(),'image/png')})
    assert response.status_code==200, response.text


@pytest.mark.parametrize('who',['sales','warehouse','technician'])
def test_assign_role_and_missing_warehouse(db,setup,who):
    p=setup;o=assigned(db,p)
    fail('FORBIDDEN',svc.assign,db,p[who],o.id,SalesDeliveryAssign(version=o.version,assignee_id=p['technician'].id))
    db.get(Warehouse,'K502').active=False;db.commit()
    fail('NO_TECH_WAREHOUSE',svc.assign,db,p['dispatcher'],o.id,SalesDeliveryAssign(version=o.version,assignee_id=p['other_tech'].id))


def test_ui_work_stock_and_cash_links_and_errors_keep_input(db,setup,client):
    p=setup;o=assigned(db,p);move(db,p,o,2)
    tasks={g['key']:g for g in my_work.collect(db,p['technician'])}
    assert tasks['technician_deliveries']['count']==1 and tasks['technician_deliveries']['rows'][0]['amount']==800
    assert tasks['technician_deliveries']['rows'][0]['link']==f'/giao-don/{o.id}'
    assert {g['key']:g for g in my_work.collect(db,p['other_tech'])}['technician_deliveries']['count']==0
    held=technician_stock.held(db,p['technician']);assert held['reservations']['TD-0001'][0]['qty']==2
    for role,path in [('dispatcher','/dieu-phoi'),('warehouse',f'/don-ban/{o.id}'),('technician','/hang-toi-giu'),('technician','/viec-cua-toi')]:
        service_login(client,p[role]);response=client.get(path)
        assert response.status_code==200, response.text[:300]
        assert f'/giao-don/{o.id}' in response.text
    page=client.get(f'/giao-don/{o.id}')
    form=next(f for f in Forms(page.text,page.url).forms if f['path'].endswith('/deliver'))['fields']
    form.update(qty_1='3',receiver_name='Khách vẫn giữ',amount='',method='TIEN_MAT',received_at='')
    r=client.post(f'/giao-don/{o.id}/deliver',data=form,files={'photos':('anh.png',d5_image(),'image/png')})
    assert r.status_code==409 and 'OVER_DELIVERY' in r.text and 'Khách vẫn giữ' in r.text
    assert 'value="3"' in r.text and db.query(SalesDelivery).count()==0
    form=next(f for f in Forms(r.text,r.url).forms if f['path'].endswith('/report'))['fields']
    form.update(amount='500',method='TIEN_MAT',received_at='',note='Khách ứng')
    assert client.post(f'/giao-don/{o.id}/report',data=form,follow_redirects=False).status_code==303
    response=client.get('/tien-toi-giu');assert response.status_code==200 and f'/giao-don/{o.id}' in response.text
    page=client.get(f'/giao-don/{o.id}')
    empty=next(f for f in Forms(page.text,page.url).forms if f['path'].endswith('/report'))['fields']
    for key in ('amount','method','received_at','note'):empty.pop(key,None)
    assert client.post(f'/giao-don/{o.id}/report',data=empty).status_code==400



def test_receipt_failure_rolls_back_gh_px_images(db,setup):
    p=setup;o=assigned(db,p);move(db,p,o,4);before=counts(db)
    files=set(Path(get_settings().files_dir).rglob('*.jpg'))
    fail('OVER_PAYMENT',deliver,db,p,o,receipt=SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=801))
    assert counts(db)==before and files==set(Path(get_settings().files_dir).rglob('*.jpg'))


def test_permission_compatibility_sales_finance_and_ticket(db,setup,make_user):
    p=setup;o=assigned(db,p)
    receipt=sales_receipts.report(db,p['sales'],o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=1))
    fail('FORBIDDEN',sales_receipts.confirm,db,p['sales'],receipt.id,receipt.version)
    both=make_user('both.sales.finance','sales','finance')
    receipt=sales_receipts.report(db,both,o.id,SalesReceiptReport(version=o.version,method='TIEN_MAT',amount=1))
    fail('SELF_CONFIRM',sales_receipts.confirm,db,both,receipt.id,receipt.version)
    t=ticket(db,p,'DANG_LAM')
    attachments.add(db,p['technician'],'service_ticket',str(t.id),'SAU','sau.png',d5_image())
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[
        dict(kind='PHI',description='Công',qty=1,unit_price=100,vat_rate=0)]))
    service_tickets.transition(db,p['technician'],t.id,'HOAN_THANH',t.version)
    receipt=sales_receipts.report_for_ticket(db,p['technician'],t.id,SalesReceiptReport(version=t.version,method='TIEN_MAT',amount=100))
    fail('FORBIDDEN',sales_receipts.confirm,db,p['technician'],receipt.id,receipt.version)


def test_stale_move_failed_assign_and_over_handover(db,setup):
    p=setup;o=assigned(db,p)
    fail('VERSION_CONFLICT',svc.move,db,p['warehouse'],o.id,'transfer',SalesDeliveryMove(version=o.version-1,lines=[dict(order_line_no=1,qty=1)]))
    fail('VERSION_CONFLICT',svc.assign,db,p['dispatcher'],o.id,SalesDeliveryAssign(version=o.version-1,assignee_id=p['technician'].id))
    fail('VERSION_CONFLICT',svc.failed,db,p['technician'],o.id,SalesDeliveryFailed(version=o.version-1,reason='KHACH_VANG'))
    fail('OVER_HANDOVER',move,db,p,o,5)
    assert hold_state(db,o)==[('K58',4)] and qty(db,'K58')==20


def test_concurrent_technician_deliveries_only_one_posts(db,engine,setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from app.models import User
    p=setup;o=assigned(db,p);move(db,p,o,4)
    oid,version,uid=o.id,o.version,p['technician'].id
    barrier=Barrier(2)
    def run(_):
        with Session(engine,expire_on_commit=False) as session:
            user=session.get(User,uid);svc.load(session,user,oid);barrier.wait(timeout=10)
            try:
                svc.deliver(session,user,oid,TechnicianDeliveryIn(version=version,lines=[dict(order_line_no=1,qty=4)]),[('g.png',d5_image())])
                return 'OK'
            except Exception as exc:
                return getattr(exc,'code',str(exc))
    db.rollback()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
    assert sorted(results)==['OK','VERSION_CONFLICT']
    assert db.query(SalesDelivery).count()==1 and db.query(Attachment).filter_by(entity='sales_delivery').count()==1
    assert qty(db,'K501')==0

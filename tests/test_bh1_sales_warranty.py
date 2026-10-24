"""BH1: enrollment is atomic with all delivery routes, voids and S5 returns."""
from datetime import date, datetime, UTC, timedelta
from decimal import Decimal
import json
from pathlib import Path
import pytest
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from app import equipment, products, sales_orders, sales_returns, service_tickets, service_warranty, technician_deliveries
from app.models import Equipment, Product, SalesDeliverySerial, SalesReturn, StockDoc, StockLedger, StockBalance, CustomerPhone, AuditLog, SalesReceipt
from app.schemas import EquipmentIn, ProductIn, ProductPatch, SalesOrderIn, SalesDeliveryLineIn, SalesCounterSale, SalesReturnIn, ServiceTicketIn, TechnicianDeliveryIn
from app.security import issue_token
from scripts.import_care_warranty import import_rows, read_csv
from test_stock_reservations import people, purchase_people, stock, order, holds, fail
from test_technician_deliveries import setup as tech_setup, assigned, move
from test_cxl2b_liquidation import liquidation
from test_cxl2a_dispositions import lot
from test_web import service_login, d5_image
from web_forms import Forms


@pytest.fixture
def machine(db,people):
    p=people
    products.update(db,p['director'],'TD-0001',ProductPatch(version=db.get(Product,'TD-0001').version,warranty_months=12))
    stock(db,p,10)
    return p


def deliver(db,p,o,serials,qty=None):
    sales_orders.deliver(db,p['warehouse'],o.id,o.version,lines=[SalesDeliveryLineIn(order_line_no=1,qty=qty or o.lines[0].qty,serials=serials)])
    return sales_orders.deliveries(db,o.id)[-1]


def state(db,o):
    db.expire_all()
    return (o.version,o.status,o.receivable,[(h.id,h.qty,h.status) for h in holds(db,o)],db.get(StockBalance,('K58','TD-0001')).qty,
        *(db.query(m).count() for m in (Equipment,SalesDeliverySerial,StockDoc,StockLedger,SalesReceipt,AuditLog)))


def test_delivery_two_machines_dates_stock_and_free_warranty(db,machine):
    p=machine;o=order(db,p,2);before=db.get(StockBalance,('K58','TD-0001')).qty
    gh=deliver(db,p,o,['bh1-a',' BH1-B ']);day=gh.delivered_at.astimezone(sales_returns.TZ).date()
    assert o.status=='DA_GIAO' and o.receivable==400
    assert db.get(StockBalance,('K58','TD-0001')).qty==before-2
    assert db.get(StockDoc,gh.lines[0].stock_doc_id).status=='DA_GHI'
    assert db.scalar(select(func.sum(StockLedger.amount)).where(StockLedger.doc_id==gh.lines[0].stock_doc_id))==-200
    for serial in ('BH1-A','BH1-B'):
        eq=db.get(Equipment,serial)
        assert (eq.product_code,eq.customer_code,eq.condition)==('TD-0001',o.customer_code,'MOI')
        assert eq.warranty_until==service_warranty.add_months(day,12)
        assert eq.support_until==service_warranty.add_months(day,1)
    links=list(db.scalars(select(SalesDeliverySerial)))
    assert len(links)==2 and all(l.created_equipment and l.order_line_id==o.lines[0].line_no for l in links)
    t=service_tickets.create(db,p['dispatcher'],ServiceTicketIn(customer_code=o.customer_code,serial='BH1-A',kind='BAO_HANH',symptom='Máy kẹt giấy'))
    assert t.warranty_basis=='MAY_CON_HAN' and t.billing=='MIEN_PHI' and t.receivable is None


@pytest.mark.parametrize('serials,code',[([], 'SERIAL_REQUIRED'),(['A'],'SERIAL_REQUIRED'),(['A','B','C'],'SERIAL_REQUIRED'),(['a',' A '],'SERIAL_DUPLICATE')])
def test_missing_excess_duplicate_rollback(db,machine,serials,code):
    p=machine;o=order(db,p,2);before=state(db,o)
    fail(code,deliver,db,p,o,serials)
    assert state(db,o)==before


def test_serial_existing_other_customer_or_product_is_blocked(db,machine):
    p=machine;o=order(db,p,1)
    equipment.create(db,p['dispatcher'],EquipmentIn(serial='USED',product_code='TD-0001',customer_code=o.customer_code))
    before=state(db,o);fail('SERIAL_IN_USE',deliver,db,p,o,['USED']);assert state(db,o)==before


def test_unassigned_existing_machine_reused_and_void_restores_before(db,machine):
    p=machine;o=order(db,p,1)
    eq=equipment.create(db,p['dispatcher'],EquipmentIn(serial='REUSE',product_code='TD-0001',condition='CU',warranty_until=date(2020,1,1),active=False))
    gh=deliver(db,p,o,['REUSE']);assert eq.active and eq.customer_code==o.customer_code
    sales_orders.void_delivery(db,p['director'],gh.id,'Nhập sai máy',gh.version)
    assert eq.customer_code is None and eq.warranty_until==date(2020,1,1) and eq.condition=='CU' and not eq.active
    assert db.query(SalesDeliverySerial).count()==1


def test_zero_months_delivery_unchanged_and_no_equipment(db,people):
    p=people;stock(db,p);o=order(db,p,2)
    sales_orders.deliver(db,p['warehouse'],o.id,o.version)
    assert o.status=='DA_GIAO' and db.query(Equipment).count()==0


def test_void_deactivates_new_machine_and_preserves_history(db,machine):
    p=machine;o=order(db,p,1);gh=deliver(db,p,o,['VOID'])
    sales_orders.void_delivery(db,p['director'],gh.id,'Giao nhầm',gh.version)
    eq=db.get(Equipment,'VOID')
    assert not eq.active and eq.customer_code is None and eq.warranty_until is None
    assert o.receivable==0 and db.get(StockBalance,('K58','TD-0001')).qty==10
    assert db.query(SalesDeliverySerial).count()==1
    # A corrected second delivery may reuse the now unassigned serial.
    deliver(db,p,o,['VOID']);assert eq.active and eq.customer_code==o.customer_code


def test_void_blocks_when_any_service_exists_and_rolls_back(db,machine):
    p=machine;o=order(db,p,1);gh=deliver(db,p,o,['SERVICE'])
    service_tickets.create(db,p['dispatcher'],ServiceTicketIn(customer_code=o.customer_code,serial='SERVICE',symptom='Kiểm máy'))
    before=state(db,o)
    fail('SERIAL_HAS_SERVICE',sales_orders.void_delivery,db,p['director'],gh.id,'Giao nhầm',gh.version)
    assert state(db,o)==before and db.get(Equipment,'SERVICE').active


def test_return_serial_required_belongs_to_delivery_and_detaches(db,machine):
    p=machine;o=order(db,p,2);gh=deliver(db,p,o,['RET-A','RET-B'])
    def ret(serials):
        return sales_returns.create(db,p['sales'],o.id,SalesReturnIn(version=o.version,reason='Khách đổi máy',lines=[dict(delivery_id=gh.id,order_line_no=1,qty=1,condition='TOT',serials=serials)]))
    for serials,code in [([], 'SERIAL_REQUIRED'),(['OTHER'],'BAD_SERIAL')]:
        before=state(db,o);fail(code,ret,serials);assert state(db,o)==before
    row=ret(['RET-A']);eq=db.get(Equipment,'RET-A')
    assert eq.customer_code is None and eq.warranty_until is None and eq.support_until is None
    assert db.get(Equipment,'RET-B').customer_code==o.customer_code
    assert row.lines[0].serials==['RET-A'] and o.receivable==200
    before=state(db,o);fail('BAD_SERIAL',ret,['RET-A']);assert state(db,o)==before
    sales_returns.void(db,p['director'],row.id,row.version,'Nhận nhầm')
    assert eq.customer_code==o.customer_code and eq.warranty_until is not None and o.receivable==400


def test_counter_sale_enrolls_and_same_money(db,machine):
    p=machine;o=order(db,p,2,confirm=False)
    sales_orders.counter_sale(db,p['sales'],o.id,SalesCounterSale(version=o.version,method='TIEN_MAT',amount=o.total,
        delivery_lines=[dict(order_line_no=1,qty=2,serials=['COUNTER-A','COUNTER-B'])]))
    assert o.status=='DA_GIAO' and o.receivable==400 and db.query(SalesReceipt).one().amount==400
    assert db.query(SalesDeliverySerial).count()==2


def test_technician_delivery_enrolls_and_uses_technician_stock(db,tech_setup):
    p=tech_setup;row=db.get(Product,'TD-0001');products.update(db,p['director'],row.code,ProductPatch(version=row.version,warranty_months=12))
    o=assigned(db,p,2);move(db,p,o,2)
    technician_deliveries.deliver(db,p['technician'],o.id,TechnicianDeliveryIn(version=o.version,lines=[dict(order_line_no=1,qty=2,serials=['TECH-A','TECH-B'])]),[('photo.png',d5_image())])
    gh=sales_orders.deliveries(db,o.id)[0]
    assert gh.lines[0].warehouse_code=='K501' and db.get(StockBalance,('K501','TD-0001')).qty==0
    assert db.get(Equipment,'TECH-A').customer_code==o.customer_code and db.query(SalesDeliverySerial).count()==2


def test_serial_history_rejects_raw_update_delete(db,machine):
    p=machine;o=order(db,p,1);deliver(db,p,o,['IMMUTABLE'])
    for sql in ("delete from sales_delivery_serials","update sales_delivery_serials set serial='OTHER'"):
        with pytest.raises(DBAPIError),db.begin_nested():db.execute(text(sql))
    assert db.query(SalesDeliverySerial).count()==1


def test_product_warranty_only_director(db,people):
    p=people;row=db.get(Product,'TD-0001')
    fail('FORBIDDEN',products.update,db,p['warehouse'],row.code,ProductPatch(version=row.version,warranty_months=12))
    assert row.warranty_months==0
    products.update(db,p['director'],row.code,ProductPatch(version=row.version,warranty_months=12));assert row.warranty_months==12


def care(db,p):
    products.create(db,p['director'],ProductIn(code='MAY-CU-CARE',name='Máy cũ CARE'))
    db.add(CustomerPhone(customer_code='KTD-000001',phone='0901234567'));db.commit()
    return dict(serial='CARE-A',phone='+84 901234567',model='Không khớp model',warranty_until='31/12/2027',care_ref='SC001')


def test_care_dry_run_apply_idempotent_unmatched_and_expired(db,people,tmp_path):
    p=people;record=care(db,p)
    rows=[record,{**record,'serial':'UNKNOWN','phone':'0902222222'}, {**record,'serial':'EXPIRED','warranty_until':'01/01/2020'},{**record,'serial':''}]
    report=import_rows(db,p['director'],rows,today=date(2026,10,24))
    assert [r['status'] for r in report]==['TAO_MOI','KHONG_KHOP_KHACH','BO_QUA_HET_HAN','BO_QUA_THIEU_SERIAL']
    assert db.query(Equipment).count()==0
    import_rows(db,p['director'],rows,apply=True,today=date(2026,10,24));db.commit()
    eq=db.get(Equipment,'CARE-A');assert eq.customer_code=='KTD-000001' and eq.product_code=='MAY-CU-CARE' and eq.warranty_until==date(2027,12,31)
    assert 'Nhập từ CARE SC001' in eq.note
    again=import_rows(db,p['director'],rows,apply=True,today=date(2026,10,24));db.commit()
    assert again[0]['status']=='DA_CO' and db.query(Equipment).count()==1
    csv=tmp_path/'care.csv';csv.write_text('serial,SĐT,model,ngày hết hạn bảo hành,mã phiếu CARE\nCSV-A,0901234567,ABC,31/12/2027,SC002\n',encoding='utf-8-sig')
    assert read_csv(csv)[0]['care_ref']=='SC002'


def test_care_existing_only_fills_blank_expiry(db,people):
    p=people;record=care(db,p)
    eq=equipment.create(db,p['dispatcher'],EquipmentIn(serial='CARE-A',product_code='TD-0001',customer_code='KTD-000001'))
    import_rows(db,p['director'],[record],apply=True,today=date(2026,10,24));db.commit()
    assert eq.product_code=='TD-0001' and eq.warranty_until==date(2027,12,31)
    import_rows(db,p['director'],[{**record,'warranty_until':'31/12/2028'}],apply=True,today=date(2026,10,24));db.commit()
    assert eq.warranty_until==date(2027,12,31)


def test_care_missing_fallback_stops(db,people):
    with pytest.raises(ValueError,match='MAY-CU-CARE'):import_rows(db,people['director'],[],apply=True)


def test_web_warehouse_serial_paste_error_preserved_and_delivery(db,machine,client):
    p=machine;o=order(db,p,2);csrf=service_login(client,p['warehouse'])
    page=client.get(f'/kho/giao/{o.id}')
    assert page.status_code==200 and 'name="serials_1"' in page.text
    before=state(db,o)
    response=client.post(f'/don-ban/{o.id}/deliver',data=dict(csrf=csrf,version=o.version,delivery_form='1',return_to='kho',deliver_1='2',serials_1='ONLY-ONE'),follow_redirects=False)
    assert response.status_code==400 and 'SERIAL_REQUIRED' in response.text and 'ONLY-ONE' in response.text
    assert state(db,o)==before
    response=client.post(f'/don-ban/{o.id}/deliver',data=dict(csrf=csrf,version=o.version,delivery_form='1',return_to='kho',deliver_1='2',serials_1='WEB-A,\nWEB-B'),follow_redirects=False)
    assert response.status_code==303 and 'in-giao' in response.headers['location']
    db.expire_all();assert db.get(Equipment,'WEB-A').customer_code==o.customer_code
    detail=client.get(f'/don-ban/{o.id}')
    assert detail.status_code==200 and 'WEB-A' in detail.text and 'Bảo hành đến' in detail.text and 'hỗ trợ tận nơi đến' in detail.text
    service_login(client,p['director'])
    device=client.get('/thiet-bi/WEB-A');assert device.status_code==200 and 'Hỗ trợ tận nơi' in device.text


def test_api_missing_then_serial_delivery_and_product_config_permissions(db,machine,client):
    p=machine;o=order(db,p,1)
    auth={'Authorization':'Bearer '+issue_token(p['warehouse'])}
    before=state(db,o)
    response=client.post(f'/sales-orders/{o.id}/deliver',headers=auth,json={'version':o.version})
    assert response.status_code==400 and response.json()['error']=='SERIAL_REQUIRED'
    assert state(db,o)==before
    response=client.post(f'/sales-orders/{o.id}/deliver',headers=auth,json={'version':o.version,'lines':[dict(order_line_no=1,qty=1,serials=['API-A'])]})
    assert response.status_code==200
    db.expire_all();assert db.get(Equipment,'API-A').customer_code==o.customer_code


def test_web_counter_from_draft_and_inline_create(db,machine,client):
    p=machine;o=order(db,p,1,confirm=False);csrf=service_login(client,p['sales'])
    page=client.get(f'/don-ban/{o.id}');assert page.status_code==200 and 'name="counter_serials"' in page.text
    response=client.post(f'/don-ban/{o.id}/counter-sale',data=dict(csrf=csrf,version=o.version,method='TIEN_MAT',amount=o.total,serials_1='WEB-COUNTER'),follow_redirects=False)
    assert response.status_code==303
    db.expire_all();assert db.get(Equipment,'WEB-COUNTER').customer_code==o.customer_code
    # One screen create + counter follows the existing save-then-counter workflow.
    response=client.post('/don-ban/moi',data=dict(csrf=csrf,customer_code=o.customer_code,contact_name='Tuấn',fulfillment='TAI_QUAY',payment_term='TRA_NGAY',
        product_code=['TD-0001'],description=['Máy in'],qty=['1'],unit=['cái'],unit_price=['200'],kind_0='HANG',warehouse_0='K58',vat_0='0',
        then='counter',counter_method='TIEN_MAT',counter_serials=['INLINE-COUNTER']),follow_redirects=False)
    assert response.status_code==303, response.text[:500]
    db.expire_all();assert db.get(Equipment,'INLINE-COUNTER').customer_code==o.customer_code


def test_web_return_serial_selector_and_submit(db,machine,client):
    p=machine;o=order(db,p,2);gh=deliver(db,p,o,['PICK-A','PICK-B']);csrf=service_login(client,p['sales'])
    page=client.get(f'/don-ban/{o.id}/tra-hang')
    assert page.status_code==200 and 'PICK-A' in page.text and 'data-return-serial-choice' in page.text
    suffix=f'{gh.id}_1'
    response=client.post(f'/don-ban/{o.id}/tra-hang',data=dict(csrf=csrf,version=o.version,reason='Khách đổi máy',**{'qty_'+suffix:'1','serials_'+suffix:'PICK-B','condition_'+suffix:'TOT','warehouse_'+suffix:'K58'}),follow_redirects=False)
    assert response.status_code==303
    db.expire_all();assert db.get(Equipment,'PICK-B').customer_code is None


def test_clear_pilot_removes_generated_keeps_care_manual_and_product_config(db,machine,monkeypatch):
    from scripts import clear_pilot_data
    import sys
    p=machine;o=order(db,p,1);deliver(db,p,o,['GENERATED'])
    record=care(db,p);import_rows(db,p['director'],[record],apply=True,today=date(2026,10,24));db.commit()
    equipment.create(db,p['dispatcher'],EquipmentIn(serial='MANUAL',product_code='TD-0001',customer_code=o.customer_code))
    db.close()
    monkeypatch.setattr(sys,'argv',['clear_pilot_data','--yes'])
    clear_pilot_data.main();db.expire_all()
    assert db.get(Equipment,'GENERATED') is None
    assert db.get(Equipment,'CARE-A') is not None and db.get(Equipment,'MANUAL') is not None
    assert db.get(Product,'TD-0001').warranty_months==12 and db.get(Product,'MAY-CU-CARE') is not None
    assert db.query(SalesDeliverySerial).count()==0
    assert db.scalar(text("select count(*) from pg_trigger where not tgisinternal and tgenabled <> 'O'"))==0


def test_calendar_month_ends():
    assert service_warranty.add_months(date(2027,1,31),1)==date(2027,2,28)
    assert service_warranty.add_months(date(2024,2,29),12)==date(2025,2,28)


def test_one_invalid_serial_rolls_back_earlier_enrollment_and_stock(db,machine):
    p=machine;o=order(db,p,2)
    equipment.create(db,p['dispatcher'],EquipmentIn(serial='Z-USED',product_code='TD-0001',customer_code=o.customer_code))
    before=state(db,o)
    fail('SERIAL_IN_USE',deliver,db,p,o,['A-NEW','Z-USED'])
    assert state(db,o)==before and db.get(Equipment,'A-NEW') is None


def test_partial_delivery_distinct_serials_preserves_totals(db,machine):
    p=machine;o=order(db,p,3)
    first=deliver(db,p,o,['PART-A'],1);second=deliver(db,p,o,['PART-B','PART-C'],2)
    assert o.status=='DA_GIAO' and first.total+second.total==o.total==o.receivable==600
    assert db.get(StockBalance,('K58','TD-0001')).qty==7 and not holds(db,o)
    assert db.query(SalesDeliverySerial).count()==3


def test_liquidation_machine_condition_used_without_changing_cost(db,liquidation):
    from test_cxl2b_liquidation import order as liquidation_order
    from app import pending_liquidation
    p,item,first,second=liquidation
    product=db.get(Product,'TD-0001');products.update(db,p['director'],product.code,ProductPatch(version=product.version,warranty_months=3))
    o=liquidation_order(db,p,qty=2);gh=deliver(db,p,o,['USED-A','USED-B'])
    assert db.get(Equipment,'USED-A').condition=='CU'
    assert sum(r.cost_value for r in pending_liquidation.delivery_rows(db,gh.id))==200
    assert o.receivable==gh.total==432

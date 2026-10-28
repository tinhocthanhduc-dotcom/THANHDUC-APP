"""Sửa ở xưởng dùng linh kiện thợ mang về kèm máy: Sửa xong trừ kho thợ (TUẤN 28/10/2026)."""
from datetime import timedelta

from sqlalchemy import select

from app import service_workshop as svc,service_tickets,service_charges,equipment_custody as custody,part_requests,technician_returns,technician_work
from app.models import StockDoc
from app.schemas import CustodyHandover,PartRequestIn,ServiceCharges
from test_my_work import people,purchase_people
from test_technician_stock import held,transfer,balance
from test_mv1_workshop import onsite,photo,intake,snapshot
from test_xg1_workshop import error,approve,repaired,start_return


def to_workshop(db,p,t,eq):
    photo(db,p,t)
    svc.bring(db,p['technician'],t.id,t.version,intake(eq),[],to_workshop=True)
    hand=custody.pending(db,eq.serial)
    if hand:custody.confirm(db,p['warehouse'],hand.id,hand.version)


def quote(db,p,t,warehouse,qty=1):
    service_charges.set_charges(db,p['dispatcher'],t.id,ServiceCharges(version=t.version,billing='THU_TIEN',lines=[
        dict(kind='PHI',description='Công sửa xưởng',qty=1,unit_price=100000,vat_rate=8),
        dict(kind='VAT_TU',description='Drum',product_code='TD-0001',warehouse_code=warehouse,qty=qty,unit_price=50000,vat_rate=8)]))


def test_part_brought_with_machine_is_deducted_from_technician_stock(db,onsite):
    p,a,_,t,eq=onsite
    # Linh kiện kho cấp cho phiếu lúc thợ còn ở chỗ khách, thợ mang về xưởng cùng máy, chưa lắp.
    row=part_requests.create(db,p['technician'],t.id,PartRequestIn(version=t.version,product_code='TD-0001',qty=1,reason='Thiếu drum'))
    part_requests.supply(db,p['warehouse'],row.id,'K58',row.version)
    service_tickets.transition(db,p['technician'],t.id,'DANG_LAM',t.version)
    assert balance(db,a.code)==5
    to_workshop(db,p,t,eq)
    assert t.status=='TAM_DUNG' and t.hold_reason=='CHO_SUA_XUONG' and balance(db,a.code)==5
    k58=balance(db,'K58')
    quote(db,p,t,a.code);approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher');repaired(db,p,t)
    line=next(x for x in t.lines if x.kind=='VAT_TU');doc=db.get(StockDoc,line.stock_doc_id)
    assert doc.kind=='PX' and doc.status=='DA_GHI' and doc.warehouse_code==a.code and doc.lines[0].qty==1
    assert balance(db,a.code)==4 and balance(db,'K58')==k58
    # Linh kiện đã lắp không còn bị đòi trả kho.
    assert technician_returns.proposal(db,t)['rows'][0]['qty']==0
    start_return(db,p,t,eq);photo(db,p,t,'TRA_MAY')
    technician_work.complete(db,p['technician'],t.id,t.version,t.receivable,'FULL','',[],return_machine=intake(eq))
    assert t.status=='DA_DONG' and balance(db,a.code)==4 and db.get(StockDoc,doc.id).status=='DA_GHI'


def test_technician_stock_shortage_blocks_repaired_without_changes(db,onsite):
    p,a,_,t,eq=onsite
    to_workshop(db,p,t,eq)
    quote(db,p,t,a.code,qty=5);approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher')
    before=snapshot(db,t)
    error('NEGATIVE_STOCK',repaired,db,p,t)
    assert snapshot(db,t)==before and balance(db,a.code)==4


def test_reopen_returns_part_to_technician_stock(db,onsite):
    p,a,_,t,eq=onsite
    to_workshop(db,p,t,eq)
    quote(db,p,t,a.code);approve(db,p,t);photo(db,p,t,'SAU',who='dispatcher');repaired(db,p,t)
    assert balance(db,a.code)==3
    start_return(db,p,t,eq);photo(db,p,t,'TRA_MAY')
    technician_work.complete(db,p['technician'],t.id,t.version,t.receivable,'',None,[],return_machine=intake(eq))
    t.status='HOAN_THANH';db.commit()
    service_tickets.transition(db,p['dispatcher'],t.id,'DANG_LAM',t.version,'Mở lại kiểm tra')
    assert balance(db,a.code)==4

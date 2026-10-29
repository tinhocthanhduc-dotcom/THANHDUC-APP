"""Đổi thợ: hàng đã cấp cho phiếu đi theo thợ mới (TUẤN 29/10/2026)."""
from sqlalchemy import select

from app import part_requests, service_tickets
from app.errors import BusinessError
from app.models import ServiceTicket, StockDoc, Warehouse
from app.schemas import PartRequestIn
from test_my_work import people, purchase_people, ticket
from test_technician_stock import held, transfer, balance, charges

import pytest


def supplied(db, p, t, qty=2):
    r = part_requests.create(db, p['technician'], t.id, PartRequestIn(version=t.version, product_code='TD-0001', qty=qty, reason='Cần drum'))
    part_requests.supply(db, p['warehouse'], r.id, 'K58', r.version)
    db.refresh(t)
    return r


def reassign(db, p, t, who):
    db.refresh(t)
    return service_tickets.assign(db, p['dispatcher'], t.id, p[who].id, t.version, 'Đổi lịch')


def test_dispatcher_reassign_moves_supplied_parts_and_lines(db, held):
    p, a, b = held
    transfer(db, p)  # thợ A có sẵn 3 cái riêng, không thuộc phiếu
    t = ticket(db, p, 'DANG_LAM')
    supplied(db, p, t)
    charges(db, p, t, qty=2)
    reassign(db, p, t, 'other_tech')
    assert balance(db, 'K501') == 3 and balance(db, 'K502') == 2
    db.expire_all(); t = db.get(ServiceTicket, t.id)
    assert [l.warehouse_code for l in t.lines if l.kind == 'VAT_TU'] == ['K502']
    doc = db.scalar(select(StockDoc).where(StockDoc.source_kind == 'DOI_THO'))
    assert doc.status == 'DA_GHI' and doc.number in t.steps[-1].note and 'Đổi lịch' in t.steps[-1].note
    # Đổi tiếp về thợ A: hàng của phiếu quay lại theo, không đụng hàng riêng của thợ B.
    reassign(db, p, t, 'technician')
    assert balance(db, 'K501') == 5 and balance(db, 'K502') == 0


def test_reassign_short_stock_moves_what_is_left_and_notes_shortage(db, held):
    p, a, b = held
    t = ticket(db, p, 'DANG_LAM')
    supplied(db, p, t, qty=2)
    transfer(db, p, source='K501', dest='K58', qty=1)  # thợ A lỡ đưa đi 1 cái
    reassign(db, p, t, 'other_tech')
    assert balance(db, 'K501') == 0 and balance(db, 'K502') == 1
    db.expire_all(); note = db.get(ServiceTicket, t.id).steps[-1].note
    assert 'Kho thợ cũ thiếu, chưa chuyển' in note and '(TD-0001) × 1' in note


def test_reassign_without_new_technician_warehouse_rolls_back(db, held):
    p, a, b = held
    t = ticket(db, p, 'DANG_LAM')
    supplied(db, p, t)
    db.get(Warehouse, 'K502').active = False; db.commit()
    with pytest.raises(BusinessError) as e:
        reassign(db, p, t, 'other_tech')
    assert e.value.code == 'NO_TECH_WAREHOUSE'
    db.rollback(); db.expire_all()
    assert db.get(ServiceTicket, t.id).assignee_id == p['technician'].id and balance(db, 'K501') == 2


def test_reassign_without_ticket_stock_needs_no_new_warehouse(db, held):
    p, a, b = held
    t = ticket(db, p, 'DA_NHAN')
    db.get(Warehouse, 'K502').active = False; db.commit()
    reassign(db, p, t, 'other_tech')
    assert db.scalar(select(StockDoc).where(StockDoc.source_kind == 'DOI_THO')) is None

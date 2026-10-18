"""Ghi sổ và hủy phiếu kho (K4). Nơi duy nhất thay đổi tồn kho.

Giá vốn bình quân gia quyền theo từng kho. Xuất kho lấy giá trị theo tỷ lệ số lượng trên tồn
(xuất hết thì lấy hết giá trị), nên giá trị tồn không bao giờ lệch vì làm tròn.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from . import audit
from .errors import BusinessError
from .inventory_catalogs import check_writer
from .models import PurchaseReturn, PurchaseReceipt, StockBalance, StockDoc, StockLedger, User
from .stock_docs import _transaction, snapshot

ZERO = Decimal(0)


def _round(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass
class Move:
    warehouse: str
    product: str
    line_no: int
    qty: Decimal  # dương là nhập, âm là xuất
    # Giá trị nhập cố định (đồng). None: xuất theo bình quân, hoặc kiểm kê tăng theo bình quân.
    amount: int | None = None
    unit_cost: int | None = None


def _load_for_update(session: Session, doc_id: int) -> StockDoc:
    # Khóa phiếu để hai người bấm ghi sổ cùng lúc không ghi hai lần.
    doc = session.scalars(
        select(StockDoc).where(StockDoc.id == doc_id).options(selectinload(StockDoc.lines)).with_for_update()
    ).first()
    if doc is None:
        raise BusinessError("NOT_FOUND", "Không tìm thấy phiếu kho.", status=404)
    return doc


def _check_version(doc: StockDoc, version: int | None) -> None:
    if version is not None and doc.version != version:
        raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa phiếu, hãy tải lại.", {"current": doc.version}, status=409)


def _lock_balances(session: Session, keys: set[tuple[str, str]]) -> dict[tuple[str, str], StockBalance]:
    """Tạo dòng tồn còn thiếu rồi khóa tất cả theo thứ tự cố định để tránh deadlock."""
    ordered = sorted(keys)
    if not ordered:
        return {}
    session.execute(
        insert(StockBalance)
        .values([{"warehouse_code": w, "product_code": p, "qty": ZERO, "value": 0, "avg_cost": 0} for w, p in ordered])
        .on_conflict_do_nothing()
    )
    rows = {}
    for w, p in ordered:
        rows[(w, p)] = session.scalars(
            select(StockBalance)
            .where(StockBalance.warehouse_code == w, StockBalance.product_code == p)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
    return rows


def _apply(session: Session, doc: StockDoc, bal: StockBalance, move: Move, reversal: bool) -> int:
    """Áp một lần nhập hoặc xuất vào tồn, ghi một dòng sổ. Trả về giá trị đã nhập (+) hoặc xuất (-)."""
    if move.qty > 0:
        amount = move.amount if move.amount is not None else _round(move.qty * bal.avg_cost)
        new_qty, new_value = bal.qty + move.qty, bal.value + amount
    else:
        out = -move.qty
        if out > bal.qty:
            raise BusinessError(
                "NEGATIVE_STOCK",
                "Không đủ tồn để xuất.",
                {"warehouse": bal.warehouse_code, "product": bal.product_code, "available": str(bal.qty), "requested": str(out)},
                status=409,
            )
        if move.amount is not None:
            amount = move.amount  # hủy phiếu: trả đúng giá trị đã nhập
        else:
            amount = -(bal.value if out == bal.qty else _round(Decimal(bal.value) * out / bal.qty))
        new_qty, new_value = bal.qty - out, bal.value + amount
    if new_value < 0 or (new_qty == 0 and new_value != 0):
        # Chỉ xảy ra khi hủy phiếu nhập mà hàng đó đã xuất đi một phần theo giá khác.
        raise BusinessError(
            "VOID_COST_CONFLICT",
            "Hủy phiếu này làm lệch giá trị tồn. Hãy lập phiếu điều chỉnh thay vì hủy.",
            {"warehouse": bal.warehouse_code, "product": bal.product_code},
            status=409,
        )
    qty = abs(move.qty)
    session.add(
        StockLedger(
            warehouse_code=bal.warehouse_code,
            product_code=bal.product_code,
            doc_id=doc.id,
            line_no=move.line_no,
            is_reversal=reversal,
            qty_in=qty if move.qty > 0 else ZERO,
            qty_out=qty if move.qty < 0 else ZERO,
            unit_cost=move.unit_cost if move.unit_cost is not None else _round(abs(Decimal(amount)) / qty),
            amount=amount,
            balance_qty=new_qty,
            balance_value=new_value,
        )
    )
    bal.qty, bal.value = new_qty, new_value
    if new_qty > 0:
        bal.avg_cost = _round(Decimal(new_value) / new_qty)
    return amount


def _post_moves(session: Session, doc: StockDoc, balances, pn_amounts=None) -> None:
    for line in doc.lines:
        src = balances[(doc.warehouse_code, line.product_code)]
        if doc.kind == "PN":
            _apply(session, doc, src, Move(doc.warehouse_code, line.product_code, line.line_no, line.qty,
                                           (pn_amounts[line.line_no] if pn_amounts is not None else _round(line.qty * line.unit_cost)), line.unit_cost), False)
        elif doc.kind == "PX":
            _apply(session, doc, src, Move(doc.warehouse_code, line.product_code, line.line_no, -line.qty), False)
        elif doc.kind == "CK":
            moved = _apply(session, doc, src, Move(doc.warehouse_code, line.product_code, line.line_no, -line.qty), False)
            dst = balances[(doc.to_warehouse_code, line.product_code)]
            _apply(session, doc, dst, Move(doc.to_warehouse_code, line.product_code, line.line_no, line.qty, -moved), False)
        else:  # KK: dòng lưu số đếm thực tế, sổ ghi phần chênh lệch
            delta = line.qty - src.qty
            if delta > 0:
                cost = line.unit_cost
                if cost is None and src.avg_cost == 0:
                    raise BusinessError(
                        "BAD_UNIT_COST",
                        "Mã hàng chưa có giá vốn, dòng kiểm kê tăng tồn phải nhập đơn giá.",
                        {"line_no": line.line_no, "product": line.product_code},
                    )
                amount = _round(delta * cost) if cost is not None else None
                _apply(session, doc, src, Move(doc.warehouse_code, line.product_code, line.line_no, delta, amount, cost), False)
            elif delta < 0:
                _apply(session, doc, src, Move(doc.warehouse_code, line.product_code, line.line_no, delta), False)


def post(session: Session, user: User, doc_id: int, version: int | None) -> StockDoc:
    check_writer(user)
    with _transaction(session):
        doc = post_in_tx(session, user, doc_id, version)
    return doc


def post_in_tx(session: Session, user: User, doc_id: int, version: int | None, *, pn_amounts: dict[int, int] | None = None) -> StockDoc:
    """Ghi sổ trong transaction của nơi gọi (phiếu sinh tự động từ chứng từ khác), không kiểm vai trò."""
    doc = _load_for_update(session, doc_id)
    if doc.status != "NHAP":
        raise BusinessError("DOC_NOT_DRAFT", "Chỉ ghi sổ được phiếu nháp.", {"status": doc.status}, status=409)
    _check_version(doc, version)
    if not doc.lines:
        raise BusinessError("EMPTY_DOC", "Phiếu chưa có dòng hàng.")
    if doc.kind == "KK":
        codes = [line.product_code for line in doc.lines]
        dup = sorted({c for c in codes if codes.count(c) > 1})
        if dup:
            raise BusinessError("DUPLICATE_LINE", "Phiếu kiểm kê có mã hàng lặp lại.", {"products": dup})
    # M2 truyền tiền nhập đã bù làm tròn, không sửa đơn giá gốc hoặc ghi tồn ngoài lõi.
    if pn_amounts is not None:
        if (doc.kind != 'PN' or set(pn_amounts) != {line.line_no for line in doc.lines}
                or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 9_223_372_036_854_775_807 for v in pn_amounts.values())):
            raise BusinessError('BAD_RECEIPT_AMOUNT', 'Giá trị nhập đã chốt không hợp lệ.')
    before = snapshot(doc)
    keys = {(doc.warehouse_code, line.product_code) for line in doc.lines}
    if doc.kind == "CK":
        keys |= {(doc.to_warehouse_code, line.product_code) for line in doc.lines}
    _post_moves(session, doc, _lock_balances(session, keys), pn_amounts)
    doc.status = "DA_GHI"
    doc.posted_by = user.id
    doc.posted_at = datetime.now(UTC)
    session.flush()
    audit.record(session, user, "stock_doc.post", "stock_doc", str(doc.id), before, snapshot(doc))
    return doc


def void(session: Session, user: User, doc_id: int, reason: str, version: int | None) -> StockDoc:
    """Hủy phiếu đã ghi sổ: ghi dòng đảo ngược đúng số lượng và giá trị đã ghi, theo thứ tự ngược."""
    check_writer(user)
    with _transaction(session):
        _load_for_update(session, doc_id)
        if session.scalar(select(PurchaseReceipt.id).where(PurchaseReceipt.stock_doc_id == doc_id, PurchaseReceipt.status == 'DA_GHI')) is not None:
            raise BusinessError('SOURCE_LOCKED', 'Phiếu nhập từ đơn mua phải hủy lần nhận trên đơn mua.', status=409)
        if session.scalar(select(PurchaseReturn.id).where(PurchaseReturn.stock_doc_id == doc_id, PurchaseReturn.status == 'DA_GHI')) is not None:
            raise BusinessError('SOURCE_LOCKED', 'Phiếu xuất trả NCC phải hủy phiếu trả hàng trên đơn mua.', status=409)
        doc = void_in_tx(session, user, doc_id, reason, version)
    return doc


def void_in_tx(session: Session, user: User, doc_id: int, reason: str, version: int | None) -> StockDoc:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise BusinessError("REASON_REQUIRED", "Hủy phiếu đã ghi sổ phải ghi lý do.")
    doc = _load_for_update(session, doc_id)
    if doc.status != "DA_GHI":
        raise BusinessError("DOC_NOT_POSTED", "Chỉ hủy theo cách này với phiếu đã ghi sổ.", {"status": doc.status}, status=409)
    _check_version(doc, version)
    before = snapshot(doc)
    rows = list(session.scalars(select(StockLedger).where(StockLedger.doc_id == doc.id).order_by(StockLedger.id.desc())))
    balances = _lock_balances(session, {(r.warehouse_code, r.product_code) for r in rows})
    for r in rows:
        qty = r.qty_out - r.qty_in
        _apply(session, doc, balances[(r.warehouse_code, r.product_code)],
               Move(r.warehouse_code, r.product_code, r.line_no, qty, -r.amount, r.unit_cost), True)
    doc.status = "DA_HUY"
    doc.void_reason = reason
    session.flush()
    audit.record(session, user, "stock_doc.void", "stock_doc", str(doc.id), before, snapshot(doc), reason)
    return doc

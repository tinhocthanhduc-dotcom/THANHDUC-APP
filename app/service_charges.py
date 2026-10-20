"""Tiền và vật tư của phiếu dịch vụ (D6).

Dòng phí và dòng vật tư sửa tự do khi phiếu còn mở. Khi phiếu sang HOAN_THANH, giá bị khóa (ghi tổng và số phải thu)
và vật tư được xuất kho bằng phiếu PX tự lập và ghi sổ trong cùng transaction: đây là đường duy nhất Dịch vụ trừ kho.
Mở lại hoặc hủy phiếu đã hoàn thành thì hủy các phiếu PX đó (sổ kho ghi đảo) và mở khóa giá.
Báo thu do sales_receipts quản lý; danh sách cần thu nối tổng đã thu/chờ bằng SQL.
"""
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit, service_sla, service_tickets, stock_docs, stock_posting, technician_stock
from .errors import BusinessError
from .models import Product, SalesReceipt, ServiceLine, ServiceTicket, User, Warehouse, StockBalance, StockDocLine, StockLedger, StockDoc
from .schemas import ServiceCharges, ServiceLineOut, StockDocIn, StockDocLineIn
from .security import has_role
from .stock_docs import _transaction

VIEWERS = ("dispatcher", "finance")


def _round(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _snapshot(t: ServiceTicket) -> dict:
    data = service_tickets.snapshot(t)
    data["lines"] = [ServiceLineOut.model_validate(x).model_dump(mode="json") for x in t.lines]
    return data


def set_charges(session: Session, user: User, ticket_id: int, body: ServiceCharges) -> ServiceTicket:
    """Thay toàn bộ dòng phí, vật tư và cách thu của phiếu đang mở."""
    with _transaction(session):
        t = service_tickets._get(session, user, ticket_id, lock=True)
        service_tickets._check_version(t, body.version)
        return set_charges_in_tx(session, user, t, body)


def set_charges_in_tx(session: Session, user: User, t: ServiceTicket, body: ServiceCharges) -> ServiceTicket:
    """Caller đã khóa phiếu, kiểm phiên bản và quản lý transaction."""
    if not has_role(user, *service_tickets.DISPATCHERS) and t.assignee_id != user.id:
        raise BusinessError("FORBIDDEN", "Chỉ thợ được giao hoặc điều phối được sửa tiền của phiếu này.", status=403)
    if t.status not in service_tickets.OPEN:
        code = "PRICE_LOCKED" if t.priced_at is not None else "BAD_STATUS"
        raise BusinessError(code, "Phiếu đã hoàn thành hoặc đóng, không sửa tiền được.", {"status": t.status}, status=409)
    free_reason = body.free_reason.strip()
    if body.billing == "MIEN_PHI" and len(free_reason) < 3:
        raise BusinessError("REASON_REQUIRED", "Miễn phí phải ghi lý do.")
    warranty_override = (t.warranty_basis in ('PHIEU_TRUOC', 'MAY_CON_HAN')
                         and t.billing == 'MIEN_PHI' and body.billing == 'THU_TIEN')
    if warranty_override:
        if not has_role(user, 'dispatcher'):
            raise BusinessError('FORBIDDEN', 'Chỉ điều phối hoặc giám đốc được đổi bảo hành sang Có thu tiền.', status=403)
        if not body.billing_reason.strip():
            raise BusinessError('REASON_REQUIRED', 'Phải ghi lý do thu tiền phiếu còn bảo hành.')
    own = technician_stock.restricted_warehouse(session, user, lock=True)
    if own:
        # Mỗi dòng cũ chỉ miễn kiểm một lần; không cho nhân bản dòng kho công ty.
        unchanged = Counter((x.product_code, x.warehouse_code, x.qty) for x in t.lines if x.kind == 'VAT_TU')
        for x in body.lines:
            if x.kind != 'VAT_TU' or x.warehouse_code == own.code:
                continue
            key = (x.product_code, x.warehouse_code, x.qty)
            if not unchanged[key]:
                raise BusinessError('TECH_WAREHOUSE_ONLY','Vật tư thêm hoặc sửa phải lấy từ kho của chính bạn.',{'warehouse':own.code},status=409)
            unchanged[key] -= 1
    _validate_lines(session, body)
    before = _snapshot(t)
    lines = []
    for i, x in enumerate(body.lines, 1):
        amount = _round(x.qty * x.unit_price)
        lines.append(ServiceLine(line_no=i, kind=x.kind, product_code=x.product_code, warehouse_code=x.warehouse_code,
                                 description=x.description, qty=x.qty, unit_price=x.unit_price, vat_rate=x.vat_rate,
                                 amount=amount, vat_amount=_round(Decimal(amount) * x.vat_rate / 100)))
    t.lines.clear()
    session.flush()  # xóa dòng cũ trước để số dòng mới không trùng
    t.lines = lines
    t.billing = body.billing
    t.free_reason = free_reason if body.billing == "MIEN_PHI" else ""
    # Thay dòng cũng là sửa phiếu, kể cả khi cách thu không thay đổi.
    t.version += 1
    session.flush()
    audit.record(session, user, "service_ticket.charges", "service_ticket", str(t.id), before, _snapshot(t), body.billing_reason.strip() if warranty_override else "")
    return t


def _validate_lines(session: Session, body: ServiceCharges):
    for i, x in enumerate(body.lines, 1):
        if x.kind == "PHI":
            if x.product_code or x.warehouse_code:
                raise BusinessError("BAD_LINE", "Dòng phí không có mã hàng và kho.", {"line_no": i})
            if not x.description:
                raise BusinessError("BAD_LINE", "Dòng phí phải ghi nội dung.", {"line_no": i})
        elif not x.product_code or not x.warehouse_code:
            raise BusinessError("BAD_LINE", "Dòng vật tư phải có mã hàng và kho xuất.", {"line_no": i})
    codes = sorted({x.product_code for x in body.lines if x.kind == "VAT_TU"})
    valid = set(session.scalars(select(Product.code).where(Product.code.in_(codes), Product.active.is_(True))))
    if set(codes) - valid:
        raise BusinessError("BAD_PRODUCT", "Mã hàng không tồn tại hoặc đã ngừng bán.", {"invalid_codes": sorted(set(codes) - valid)})
    whs = sorted({x.warehouse_code for x in body.lines if x.kind == "VAT_TU"})
    valid = set(session.scalars(select(Warehouse.code).where(Warehouse.code.in_(whs), Warehouse.active.is_(True), Warehouse.kind.in_(('KHO','THO')))))
    if set(whs) - valid:
        raise BusinessError("BAD_WAREHOUSE", "Kho không tồn tại hoặc đã ngừng sử dụng.", {"codes": sorted(set(whs) - valid)})


def complete(session: Session, user: User, t: ServiceTicket) -> None:
    """Gọi trong transaction chuyển sang HOAN_THANH: khóa giá và xuất kho vật tư."""
    if t.billing is None:
        raise BusinessError("BILLING_REQUIRED", "Chọn Có thu tiền hoặc Miễn phí trước khi hoàn thành.")
    subtotal = sum(x.amount for x in t.lines)
    vat_total = sum(x.vat_amount for x in t.lines)
    if t.billing == "THU_TIEN" and subtotal + vat_total == 0:
        raise BusinessError("EMPTY_CHARGES", "Có thu tiền nhưng chưa có dòng phí hoặc vật tư nào có giá.")
    today = datetime.now(UTC).astimezone(service_sla.TZ).date()
    by_wh: dict[str, list[ServiceLine]] = {}
    for x in t.lines:
        if x.kind == "VAT_TU":
            by_wh.setdefault(x.warehouse_code, []).append(x)
    for wh in sorted(by_wh):
        parts = by_wh[wh]
        body = StockDocIn(kind="PX", warehouse_code=wh, doc_date=today, customer_code=t.customer_code,
                          reason=f"Vật tư phiếu dịch vụ {t.number}",
                          lines=[StockDocLineIn(product_code=x.product_code, qty=x.qty, note=f"{t.number} dòng {x.line_no}") for x in parts])
        doc = stock_docs.insert(session, user, body)
        costs = {}
        for code in sorted({x.product_code for x in parts}):
            bal=session.scalar(select(StockBalance).where(StockBalance.warehouse_code==wh,StockBalance.product_code==code).with_for_update().execution_options(populate_existing=True))
            costs[code]=bal.avg_cost if bal else 0
        for line in doc.lines:line.unit_cost=costs[line.product_code]
        session.flush()
        stock_posting.post_in_tx(session, user, doc.id, None)
        for x in parts:
            x.stock_doc_id = doc.id
    t.subtotal, t.vat_total, t.total = subtotal, vat_total, subtotal + vat_total
    t.receivable = t.total if t.billing == "THU_TIEN" else 0
    t.priced_at = datetime.now(UTC)


def release(session: Session, user: User, t: ServiceTicket, reason: str) -> None:
    """Gọi trong transaction mở lại hoặc hủy phiếu đã hoàn thành: hủy phiếu PX và mở khóa giá."""
    for doc_id in sorted({x.stock_doc_id for x in t.lines if x.stock_doc_id is not None}):
        stock_posting.void_in_tx(session, user, doc_id, f"{t.number}: {reason}", None)
    for x in t.lines:
        x.stock_doc_id = None
    t.subtotal = t.vat_total = t.total = t.receivable = t.priced_at = None


def receivables(session: Session, user: User, from_date: date | None = None, to_date: date | None = None) -> list[ServiceTicket]:
    """T3: còn nợ thực tế; giữ interface list phiếu, thêm paid/pending/remaining chỉ đọc."""
    if not has_role(user, *VIEWERS):
        raise BusinessError("FORBIDDEN", "Chỉ điều phối, kế toán hoặc giám đốc xem danh sách cần thu.", status=403)
    from .sales_receipts import _sums, _balance
    paid,pending = _sums()
    totals = select(SalesReceipt.ticket_id,paid.label('paid'),pending.label('pending')).where(
        SalesReceipt.ticket_id.is_not(None)).group_by(SalesReceipt.ticket_id).subquery()
    stmt = (select(ServiceTicket,func.coalesce(totals.c.paid,0),func.coalesce(totals.c.pending,0))
            .outerjoin(totals,totals.c.ticket_id==ServiceTicket.id)
            .where(ServiceTicket.status.in_(("HOAN_THANH", "DA_DONG")),ServiceTicket.receivable>func.coalesce(totals.c.paid,0)))
    if from_date is not None:
        stmt = stmt.where(ServiceTicket.priced_at >= datetime.combine(from_date, time.min, service_sla.TZ))
    if to_date is not None:
        stmt = stmt.where(ServiceTicket.priced_at < datetime.combine(to_date + timedelta(days=1), time.min, service_sla.TZ))
    rows = []
    for ticket,pay,wait in session.execute(stmt.order_by(ServiceTicket.priced_at,ServiceTicket.id)):
        current = _balance(ticket.receivable,pay,wait)
        # Thuộc tính không mapped, không thay số tiền đã chốt trong DB.
        ticket.paid,ticket.pending,ticket.remaining = current['paid'],current['pending'],current['remaining']
        rows.append(ticket)
    return rows


def material_costs(session, ticket_ids):
    """Read each posted PX line once, even when a product occurs on several service lines."""
    links=select(ServiceLine.ticket_id,ServiceLine.stock_doc_id).where(ServiceLine.ticket_id.in_(ticket_ids),ServiceLine.stock_doc_id.is_not(None)).distinct().subquery()
    posted_cost=select(StockLedger.unit_cost).where(StockLedger.doc_id==StockDocLine.doc_id,StockLedger.line_no==StockDocLine.line_no,StockLedger.qty_out>0,StockLedger.is_reversal.is_(False)).limit(1).correlate(StockDocLine).scalar_subquery()
    values=session.execute(select(links.c.ticket_id,StockDocLine.qty,func.coalesce(StockDocLine.unit_cost,posted_cost,0)).select_from(links).join(StockDoc,StockDoc.id==links.c.stock_doc_id).join(StockDocLine,StockDocLine.doc_id==StockDoc.id).where(StockDoc.kind=='PX',StockDoc.status=='DA_GHI'))
    result={}
    for ident,qty,cost in values:result[ident]=result.get(ident,Decimal(0))+qty*cost
    return result

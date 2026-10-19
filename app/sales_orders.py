"""B2: đơn bán; giao/hủy PX qua lõi kho trong transaction của đơn."""
from contextlib import contextmanager
from zoneinfo import ZoneInfo
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import or_, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from . import audit, quotes, stock_docs, stock_posting, stock_reservations
from .config import get_settings
from .customers import like_pattern
from .doc_numbers import next_number
from .errors import BusinessError
from decimal import Decimal

from .models import SalesDelivery, SalesDeliveryLine, Customer, SalesOrder, SalesOrderLine, SalesReceipt, SALES_ORDER_STATUSES, Warehouse
from .schemas import (QuoteLineIn, SalesOrderFromQuote, SalesOrderIn, SalesOrderLineIn,
                      SalesOrderOut, SalesOrderUpdate, StockDocIn, StockDocLineIn)
from .security import has_role
from .text import fold

WRITERS = ('sales',)
DELIVERERS = ('sales', 'warehouse')
VIEWERS = ('sales', 'warehouse', 'finance')


def _today():
    return quotes._today()


def check_reader(user):
    if not has_role(user, *VIEWERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh, kho, kế toán hoặc giám đốc được xem đơn bán.', status=403)


def check_writer(user):
    if not has_role(user, *WRITERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh hoặc giám đốc được sửa đơn bán.', status=403)


def _quote_used():
    return BusinessError('QUOTE_USED', 'Báo giá đã có đơn bán chưa hủy.', status=409)


@contextmanager
def _transaction(session):
    try:
        yield
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa đơn bán, hãy tải lại.', status=409)
    except IntegrityError as exc:
        session.rollback()
        if getattr(getattr(exc.orig, 'diag', None), 'constraint_name', '') == 'uq_sales_orders_active_quote':
            raise _quote_used() from exc
        raise
    except Exception:
        session.rollback()
        raise


def snapshot(order):
    return SalesOrderOut.model_validate(order).model_dump(mode='json')


def _load(session, order_id, lock=False):
    stmt = select(SalesOrder).where(SalesOrder.id == order_id).options(selectinload(SalesOrder.lines))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy đơn bán.', status=404)
    return row


def load(session: Session, user, order_id):
    check_reader(user)
    return _load(session, order_id)


def search(session: Session, user, q='', status=None, customer_code=None, from_date=None, to_date=None, holding=False):
    check_reader(user)
    if status and status not in SALES_ORDER_STATUSES:
        raise BusinessError('BAD_STATUS', 'Trạng thái đơn bán không hợp lệ.')
    stmt = select(SalesOrder).options(selectinload(SalesOrder.lines)).join(Customer, SalesOrder.customer_code == Customer.code)
    if holding:
        from .models import StockReservation
        stmt = stmt.where(select(StockReservation.id).where(StockReservation.order_id == SalesOrder.id, StockReservation.status == 'GIU').exists())
    needle = fold(q)
    if needle:
        stmt = stmt.where(or_(SalesOrder.number.ilike(like_pattern(needle)), Customer.search_text.like(like_pattern(needle))))
    for column, value in ((SalesOrder.status, status), (SalesOrder.customer_code, customer_code)):
        if value:
            stmt = stmt.where(column == value)
    # Khoảng ngày theo giờ Việt Nam, cùng cách cấp số DB.
    if from_date is not None or to_date is not None:
        tz = ZoneInfo('Asia/Ho_Chi_Minh')
        if from_date is not None and to_date is not None and from_date > to_date:
            raise BusinessError('BAD_DATE_RANGE', 'Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
        if from_date is not None:
            stmt = stmt.where(SalesOrder.created_at >= datetime.combine(from_date, time.min, tz))
        if to_date is not None and to_date < date.max:
            stmt = stmt.where(SalesOrder.created_at < datetime.combine(to_date + timedelta(days=1), time.min, tz))
    return list(session.scalars(stmt.order_by(SalesOrder.id.desc()).limit(get_settings().search_limit * 5)))


def _version(order, version):
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BusinessError('VERSION_REQUIRED', 'Phải gửi phiên bản đơn bán, hãy tải lại.')
    if order.version != version:
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa đơn bán, hãy tải lại.', {'current': order.version}, status=409)


def _status(order, allowed):
    if order.status not in allowed:
        raise BusinessError('BAD_STATUS', 'Không thực hiện được ở trạng thái đơn bán hiện tại.', {'status': order.status}, status=409)


def _lines(session, lines, require_warehouse=False):
    for i, line in enumerate(lines, 1):
        if line.kind == 'HANG':
            if not line.product_code:
                raise BusinessError('BAD_LINE', 'Dòng hàng phải có mã hàng.', {'line_no': i})
            if require_warehouse and not line.warehouse_code:
                raise BusinessError('WAREHOUSE_REQUIRED', 'Dòng hàng phải chọn kho xuất.', {'line_no': i})
        elif line.product_code or line.warehouse_code:
            raise BusinessError('BAD_LINE', 'Dòng khác không có mã hàng hoặc kho.', {'line_no': i})
    codes = sorted({line.warehouse_code for line in lines if line.kind == 'HANG' and line.warehouse_code})
    valid = set(session.scalars(select(Warehouse.code).where(Warehouse.code.in_(codes), Warehouse.active.is_(True), Warehouse.kind == 'KHO')
                               .order_by(Warehouse.code).with_for_update(read=True)))
    if set(codes) - valid:
        raise BusinessError('BAD_WAREHOUSE', 'Kho không tồn tại hoặc đã ngừng sử dụng.', {'codes': sorted(set(codes) - valid)})
    # Dùng nguyên phần tính dòng B1 (_round/_money_limit, VAT, giá bán và kiểm mã hàng).
    priced = quotes._lines(session, [QuoteLineIn(**line.model_dump(exclude={'kind', 'warehouse_code'})) for line in lines])
    return [SalesOrderLine(kind=source.kind, warehouse_code=source.warehouse_code,
                          **{field: getattr(line, field) for field in ('line_no', 'product_code', 'description', 'qty',
                             'unit', 'unit_price', 'vat_rate', 'amount', 'vat_amount')})
            for source, line in zip(lines, priced)]


def _record(session, user, order, action, before, reason=''):
    session.flush()
    audit.record(session, user, 'sales_order.' + action, 'sales_order', str(order.id), before, snapshot(order), reason)


def _new(session, user, body, quote_id=None, action='create'):
    customer = quotes._customer(session, body.customer_code)
    lines = _lines(session, body.lines)
    order = SalesOrder(number=next_number(session, 'DB', _today()), customer_code=body.customer_code,
                       quote_id=quote_id, contact_name=body.contact_name, promised_date=body.promised_date,
                       delivery_address=customer.address if body.delivery_address is None else body.delivery_address,
                       note=body.note, status='NHAP', cancel_reason='', created_by=user.id, lines=lines)
    quotes._totals(order)
    session.add(order)
    _record(session, user, order, action, None)
    return order


def create(session: Session, user, body: SalesOrderIn):
    check_writer(user)
    with _transaction(session):
        order = _new(session, user, body)
    return order


def create_from_quote(session: Session, user, quote_id, body: SalesOrderFromQuote):
    check_writer(user)
    with _transaction(session):
        # Khóa nguồn để hai yêu cầu cùng lúc không dùng cùng báo giá.
        quote = quotes._load(session, quote_id, lock=True)
        quotes._version(quote, body.version)
        if quote.status != 'CHAP_NHAN':
            raise BusinessError('BAD_STATUS', 'Chỉ lập đơn bán từ báo giá đã được khách chấp nhận.', status=409)
        if session.scalar(select(SalesOrder.id).where(SalesOrder.quote_id == quote_id, SalesOrder.status != 'DA_HUY')) is not None:
            raise _quote_used()
        goods = {line.line_no for line in quote.lines if line.product_code}
        if set(body.warehouses) - goods:
            raise BusinessError('BAD_LINE', 'Chỉ chọn kho cho các dòng hàng trong báo giá.', {'line_nos': sorted(set(body.warehouses)-goods)})
        lines = [SalesOrderLineIn(kind='HANG' if line.product_code else 'KHAC',
                 warehouse_code=body.warehouses.get(line.line_no),
                 **{field: getattr(line, field) for field in ('product_code','description','qty','unit','unit_price','vat_rate')})
                 for line in quote.lines]
        order = _new(session, user, SalesOrderIn(customer_code=quote.customer_code,
                     contact_name=quote.contact_name if body.contact_name is None else body.contact_name,
                     delivery_address=body.delivery_address, note=quote.note if body.note is None else body.note,
                     lines=lines, promised_date=body.promised_date), quote_id, 'create_from_quote')
    return order


def update_lines(session: Session, user, order_id, body: SalesOrderUpdate):
    check_writer(user)
    with _transaction(session):
        order = _load(session, order_id, lock=True)
        _version(order, body.version)
        if order.status != 'NHAP':
            raise BusinessError('PRICE_LOCKED', 'Đơn bán đã chốt hoặc đã hủy, không sửa dòng được.', status=409)
        quotes._customer(session, order.customer_code)
        lines = _lines(session, body.lines)
        before = snapshot(order)
        order.lines.clear()
        session.flush()
        order.lines = lines
        for field in ('contact_name', 'delivery_address', 'note'):
            value = getattr(body, field)
            if value is not None:
                setattr(order, field, value)
        if 'promised_date' in body.model_fields_set:
            order.promised_date = body.promised_date
        order.version += 1
        quotes._totals(order)
        _record(session, user, order, 'update_lines', before)
    return order


def confirm(session: Session, user, order_id, version):
    check_writer(user)
    with _transaction(session):
        order = _load(session, order_id, lock=True)
        _version(order, version)
        _status(order, ('NHAP',))
        if not order.lines:
            raise BusinessError('EMPTY_LINES', 'Đơn bán phải có ít nhất một dòng trước khi chốt.')
        quotes._customer(session, order.customer_code)
        _lines(session, [SalesOrderLineIn.model_validate(line, from_attributes=True) for line in order.lines], require_warehouse=True)
        before = snapshot(order)
        quotes._totals(order)
        order.status, order.receivable = 'DA_CHOT', 0
        order.confirmed_at, order.confirmed_by = datetime.now(UTC), user.id
        stock_reservations.keep_in_tx(session, user, order)
        _record(session, user, order, 'confirm', before)
    return order


def delivery_sums(session, order_id):
    return {r.order_line_no: dict(qty=r.qty, amount=int(r.amount), vat_amount=int(r.vat_amount))
            for r in session.execute(select(SalesDeliveryLine.order_line_no,
                func.sum(SalesDeliveryLine.qty).label('qty'), func.sum(SalesDeliveryLine.amount).label('amount'),
                func.sum(SalesDeliveryLine.vat_amount).label('vat_amount')).join(SalesDelivery)
                .where(SalesDelivery.order_id == order_id, SalesDelivery.status == 'DA_GIAO')
                .group_by(SalesDeliveryLine.order_line_no))}


def first_delivery():
    return select(func.min(SalesDelivery.delivered_at)).where(
        SalesDelivery.order_id == SalesOrder.id, SalesDelivery.status == 'DA_GIAO').correlate(SalesOrder).scalar_subquery()


def remaining_lines(session, order):
    sums = delivery_sums(session, order.id)
    return {line.line_no: dict(delivered=sums.get(line.line_no, {}).get('qty', Decimal(0)),
            remaining=line.qty-line.closed_qty-sums.get(line.line_no, {}).get('qty', Decimal(0))) for line in order.lines}


def effective_total(order):
    """Trần còn hiệu lực: dòng nguyên giữ giá gốc; dòng đóng giữ đúng tiền các GH.

    Bù làm tròn có thể lệch một đồng so với nhân tỷ lệ. Khi đóng không
    tính lại tiền đã giao, kể cả đã chia thành nhiều GH.
    """
    from sqlalchemy.orm import object_session
    if not any(line.closed_qty for line in order.lines):
        return order.total
    sums = delivery_sums(object_session(order), order.id)
    total = 0
    for line in order.lines:
        if not line.closed_qty:
            total += line.amount+line.vat_amount
            continue
        old = sums.get(line.line_no,dict(qty=0,amount=0,vat_amount=0))
        remaining = line.qty-line.closed_qty-old['qty']
        amount = quotes._round(remaining*line.unit_price)
        total += old['amount']+old['vat_amount']+amount+quotes._round(Decimal(amount)*line.vat_rate/100)
    return total


def _recompute(session, order):
    sums = delivery_sums(session, order.id)
    done = all(sums.get(l.line_no, {}).get('qty', Decimal(0)) + l.closed_qty == l.qty for l in order.lines)
    last = session.execute(select(SalesDelivery.delivered_at, SalesDelivery.delivered_by)
        .where(SalesDelivery.order_id == order.id, SalesDelivery.status == 'DA_GIAO')
        .order_by(SalesDelivery.delivered_at.desc(), SalesDelivery.id.desc()).limit(1)).first()
    order.status = 'DA_GIAO' if done else 'GIAO_MOT_PHAN' if sums else 'DA_CHOT'
    order.delivered_at, order.delivered_by = last if last else (None, None)
    order.version += 1


def delivery_snapshot(delivery):
    fields = ('id','number','order_id','delivered_at','delivered_by','receiver_name','note','subtotal','vat_total',
              'total','status','void_reason','voided_by','voided_at','version')
    data = {}
    for key in fields:
        value = getattr(delivery,key)
        data[key] = value.isoformat() if isinstance(value,date) else value
    data['lines'] = [dict(order_line_no=l.order_line_no,qty=str(l.qty),amount=l.amount,vat_amount=l.vat_amount,
                          warehouse_code=l.warehouse_code,stock_doc_id=l.stock_doc_id) for l in delivery.lines]
    return data


def deliveries(session, order_id):
    return list(session.scalars(select(SalesDelivery).options(selectinload(SalesDelivery.lines))
        .where(SalesDelivery.order_id == order_id).order_by(SalesDelivery.delivered_at, SalesDelivery.id)))


def deliver(session: Session, user, order_id, version, lines=None, receiver_name='', note=''):
    if not has_role(user, *DELIVERERS):
        raise BusinessError('FORBIDDEN', 'Chỉ kinh doanh, kho hoặc giám đốc được giao đơn bán.', status=403)
    from .schemas import SalesDeliveryIn
    with _transaction(session):
        order = _load(session, order_id, lock=True)
        _version(order, version)
        _status(order, ('DA_CHOT', 'GIAO_MOT_PHAN'))
        body = SalesDeliveryIn(version=version, lines=lines, receiver_name=receiver_name, note=note)
        sums = delivery_sums(session, order.id)
        remaining = remaining_lines(session, order)
        quantities = {n: state['remaining'] for n,state in remaining.items()} if body.lines is None else {}
        if body.lines is not None:
            for part in body.lines:
                if part.order_line_no in quantities or part.order_line_no not in remaining:
                    raise BusinessError('OVER_DELIVERY', 'Dòng giao hàng không thuộc đơn hoặc bị trùng.', status=409)
                quantities[part.order_line_no] = part.qty
        if any(q < 0 or q > remaining[n]['remaining'] for n,q in quantities.items()):
            raise BusinessError('OVER_DELIVERY', 'Số giao vượt số lượng còn lại của đơn.', status=409)
        parts = [l for l in order.lines if quantities.get(l.line_no, 0) > 0]
        if not parts:
            raise BusinessError('EMPTY_DELIVERY', 'Phải có ít nhất một dòng giao lớn hơn 0.')
        _lines(session, [SalesOrderLineIn.model_validate(l, from_attributes=True) for l in parts], require_warehouse=True)
        before = snapshot(order)
        stamp = datetime.now(UTC)
        gh = SalesDelivery(number=next_number(session,'GH',stamp.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date()),
            order_id=order.id, delivered_at=stamp, delivered_by=user.id, receiver_name=body.receiver_name, note=body.note,
            subtotal=0, vat_total=0, total=0, status='DA_GIAO', void_reason='', lines=[])
        by_wh = {}
        for line in parts:
            qty = quantities[line.line_no]
            old = sums.get(line.line_no, dict(qty=0, amount=0, vat_amount=0))
            # Chỉ bù tiền khi giao đủ toàn bộ dòng, không thu phần đã đóng.
            final = old['qty'] + qty == line.qty
            amount = line.amount-old['amount'] if final else quotes._round(qty*line.unit_price)
            vat = line.vat_amount-old['vat_amount'] if final else quotes._round(Decimal(amount)*line.vat_rate/100)
            piece = SalesDeliveryLine(order_id=order.id, order_line_no=line.line_no, qty=qty,
                amount=amount, vat_amount=vat, warehouse_code=line.warehouse_code)
            gh.lines.append(piece)
            gh.subtotal += amount
            gh.vat_total += vat
            if line.kind == 'HANG':
                by_wh.setdefault(line.warehouse_code, []).append((line,piece))
        gh.total = gh.subtotal+gh.vat_total
        session.add(gh)
        session.flush()
        # Lấy số PX trước khóa tồn, cùng thứ tự với các luồng xuất kho khác.
        # Nếu khóa tồn rồi chờ doc_counters.PX, có thể deadlock với xuất DV.
        docs = []
        for wh in sorted(by_wh):
            pairs = by_wh[wh]
            doc = stock_docs.insert(session,user,StockDocIn(kind='PX', warehouse_code=wh,
                doc_date=stamp.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date(), customer_code=order.customer_code,
                reason=f'Giao {gh.number} cho {order.number}', lines=[StockDocLineIn(product_code=l.product_code,
                    qty=p.qty, note=f'{order.number} dòng {l.line_no}; {gh.number}') for l,p in pairs]))
            docs.append((doc,pairs))
        stock_posting._lock_balances(session, {(l.warehouse_code,l.product_code) for l in parts if l.kind=='HANG'})
        for doc,pairs in docs:
            stock_posting.post_in_tx(session,user,doc.id,None,for_order_id=order.id)
            for line,piece in pairs:
                piece.stock_doc_id = doc.id
                line.stock_doc_id = doc.id  # liên kết gần nhất để giữ tương thích API cũ
                stock_reservations.split_in_tx(session,user,order,line,piece.qty,gh.number)
        session.flush()
        _recompute(session,order)
        order.receivable = (order.receivable or 0)+gh.total
        audit.record(session,user,'sales_delivery.create','sales_delivery',str(gh.id),None,delivery_snapshot(gh))
        _record(session,user,order,'deliver',before)
    return order


def close_remaining(session, user, order_id, reason, version):
    with _transaction(session):
        order = _load(session,order_id,lock=True)
        if not (has_role(user,'director') or (has_role(user,'sales') and order.created_by==user.id)):
            raise BusinessError('FORBIDDEN','Chỉ người lập đơn hoặc giám đốc được đóng phần còn lại.',status=403)
        _version(order,version)
        _status(order,('GIAO_MOT_PHAN',))
        reason = (reason or '').strip()
        if not reason:
            raise BusinessError('REASON_REQUIRED','Phải nhập lý do đóng phần còn lại.')
        paid = session.scalar(select(func.coalesce(func.sum(SalesReceipt.amount),0)).where(SalesReceipt.order_id==order.id,
            SalesReceipt.status.in_(('DA_XAC_NHAN','CHO_XAC_NHAN'))))
        if paid > order.receivable:
            raise BusinessError('OVER_PAID','Khách đã trả nhiều hơn phần giao; xử lý hoàn tiền trước',status=409)
        before = snapshot(order)
        state = remaining_lines(session,order)
        for line in order.lines:
            line.closed_qty += state[line.line_no]['remaining']
        stock_reservations.close_in_tx(session,user,order,reason=reason)
        _recompute(session,order)
        _record(session,user,order,'close_remaining',before,reason)
    return order


def void_delivery(session, user, delivery_id, reason, version):
    if not has_role(user,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được hủy lần giao.',status=403)
    with _transaction(session):
        order_id = session.scalar(select(SalesDelivery.order_id).where(SalesDelivery.id==delivery_id))
        if order_id is None:
            raise BusinessError('NOT_FOUND','Không tìm thấy lần giao.',status=404)
        order = _load(session,order_id,lock=True)
        gh = session.scalar(select(SalesDelivery).options(selectinload(SalesDelivery.lines)).where(
            SalesDelivery.id==delivery_id).with_for_update().execution_options(populate_existing=True))
        _version(gh,version)
        _status(gh,('DA_GIAO',))
        reason=(reason or '').strip()
        if not reason:
            raise BusinessError('REASON_REQUIRED','Phải nhập lý do hủy lần giao.')
        before, old = snapshot(order), delivery_snapshot(gh)
        for doc_id in sorted({l.stock_doc_id for l in gh.lines if l.stock_doc_id}):
            stock_posting.void_in_tx(session,user,doc_id,f'{gh.number}: {reason}',None)
        gh.status, gh.void_reason = 'DA_HUY', reason
        gh.voided_by, gh.voided_at = user.id, datetime.now(UTC)
        session.flush()
        _recompute(session,order)
        order.receivable -= gh.total
        audit.record(session,user,'sales_delivery.void','sales_delivery',str(gh.id),old,delivery_snapshot(gh),reason)
        _record(session,user,order,'void_delivery',before,reason)
    return order


def cancel(session: Session, user, order_id, reason, version):
    check_writer(user)
    with _transaction(session):
        order = _load(session, order_id, lock=True)
        _version(order, version)
        _status(order, ('NHAP', 'DA_CHOT'))
        reason = (reason or '').strip()
        if not reason:
            raise BusinessError('REASON_REQUIRED', 'Phải nhập lý do hủy đơn bán.')
        if session.scalar(select(SalesReceipt.id).where(SalesReceipt.order_id == order.id,
                          SalesReceipt.status.in_(('CHO_XAC_NHAN', 'DA_XAC_NHAN'))).limit(1)) is not None:
            raise BusinessError('HAS_RECEIPTS', 'Tài chính phải từ chối hoặc hủy báo thu trước', status=409)
        before = snapshot(order)
        stock_reservations.close_in_tx(session, user, order, reason=reason)
        order.status, order.cancel_reason = 'DA_HUY', reason
        if order.receivable is not None:
            order.receivable = 0
        # Giữ tổng và liên kết PX để xem lại lịch sử sau hủy; không còn số phải thu.
        _record(session, user, order, 'cancel', before, reason)
    return order


def set_promised_date(session, user, order_id, promised_date, version):
    check_writer(user)
    with _transaction(session):
        order = _load(session, order_id, lock=True)
        _version(order, version)
        _status(order, ('NHAP','DA_CHOT','GIAO_MOT_PHAN'))
        before = snapshot(order)
        order.promised_date = promised_date
        order.version += 1
        _record(session, user, order, 'promise', before)
    return order

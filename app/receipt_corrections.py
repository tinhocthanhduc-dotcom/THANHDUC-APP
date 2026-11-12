"""Sửa báo thu đã xác nhận một bước (TUẤN duyệt 12/11/2026).

Giám đốc sửa số tiền, đơn/phiếu (kể cả sang khách khác), ngày thu, hình thức của một BT đã xác nhận.
Không sửa đè: BT cũ thành DA_HUY (giữ lại, nối replaced_by_id sang BT mới), phiếu thu cũ ghi đảo
hôm nay, BT mới xác nhận luôn và ghi phiếu thu mới. Công nợ khách vẫn chỉ giảm qua BT.
Ngày gốc đã chốt sổ thì không mở khóa: phần đảo và phiếu thu mới ghi vào hôm nay (như duyệt điều chỉnh
phiếu thu chi), BT mới vẫn giữ ngày thu thật.
"""
from datetime import UTC, date, datetime

from sqlalchemy import select

from . import cash_docs, cash_posting, day_closings, finance_mappings, sales_orders
from . import sales_receipts as svc
from .doc_numbers import next_number
from .errors import BusinessError
from .models import CustomerOpeningDebt, SalesOrder, SalesReceipt, ServiceTicket, User, SALES_RECEIPT_METHODS
from .security import has_role

KINDS = ('DB', 'DV', 'DK')
METHOD_LABELS = {'TIEN_MAT': 'Tiền mặt', 'CHUYEN_KHOAN': 'Chuyển khoản'}


def can_correct(user, receipt) -> bool:
    return bool(user) and has_role(user, 'director') and receipt.status == 'DA_XAC_NHAN'


def key_of(receipt):
    if receipt.order_id is not None:
        return ('DB', receipt.order_id)
    if receipt.ticket_id is not None:
        return ('DV', receipt.ticket_id)
    return ('DK', receipt.opening_id)


def _parent(session, kind, ident):
    if kind == 'DB':
        return sales_orders._load(session, ident, lock=True)
    if kind == 'DV':
        return svc._ticket(session, ident)
    return svc._opening(session, ident)


def _limit(kind, parent):
    """Số tối đa được thu của chứng từ, cùng luật chặn vượt khi báo thu."""
    if kind == 'DB':
        if parent.status not in ('DA_CHOT', 'GIAO_MOT_PHAN', 'DA_GIAO'):
            raise BusinessError('BAD_STATUS', f'{parent.number}: chỉ gán báo thu vào đơn đã chốt hoặc đã giao.', status=409)
        return sales_orders.effective_total(parent)
    if kind == 'DV':
        if parent.status not in ('HOAN_THANH', 'DA_DONG') or (parent.receivable or 0) <= 0 or parent.billing == 'MIEN_PHI':
            raise BusinessError('BAD_STATUS', f'{parent.number}: chỉ gán vào phiếu dịch vụ đã hoàn thành hoặc đã đóng còn số phải thu.', status=409)
        return parent.receivable
    return parent.amount


def _money(value):
    return f'{value:,}'.replace(',', '.')


def correct(session, user, receipt_id, version, *, kind, target_id, amount, received_at, method, reason):
    if not has_role(user, 'director'):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được sửa báo thu đã xác nhận.', status=403)
    if kind not in KINDS or not isinstance(target_id, int) or target_id <= 0:
        raise BusinessError('BAD_TARGET', 'Chọn đơn, phiếu hoặc nợ đầu kỳ để gán báo thu.')
    if method not in SALES_RECEIPT_METHODS:
        raise BusinessError('BAD_METHOD', 'Hình thức thu chưa hợp lệ.')
    if not isinstance(amount, int) or isinstance(amount, bool) or not 0 < amount <= 9_223_372_036_854_775_807:
        raise BusinessError('BAD_AMOUNT', 'Số tiền đúng phải là số nguyên đồng lớn hơn 0.')
    if not isinstance(received_at, date) or received_at > cash_docs.today():
        raise BusinessError('BAD_DATE', 'Ngày thu không được sau hôm nay.')
    reason = (reason or '').strip()
    if not reason:
        raise BusinessError('REASON_REQUIRED', 'Phải chọn hoặc nhập lý do sửa báo thu.')
    with svc._transaction(session):
        return correct_in_tx(session, user, receipt_id, version, kind=kind, target_id=target_id, amount=amount,
                             received_at=received_at, method=method, reason=reason)


def correct_in_tx(session, user, receipt_id, version, *, kind, target_id, amount, received_at, method, reason):
    source = session.execute(select(SalesReceipt.order_id, SalesReceipt.ticket_id, SalesReceipt.opening_id)
                             .where(SalesReceipt.id == receipt_id)).first()
    if source is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy báo thu.', status=404)
    old_key = ('DB', source.order_id) if source.order_id is not None else \
        ('DV', source.ticket_id) if source.ticket_id is not None else ('DK', source.opening_id)
    new_key = (kind, target_id)
    # Khóa chứng từ nguồn trước BT như mọi luồng báo thu; hai chứng từ thì theo thứ tự (loại, id) cố định.
    parents = {key: _parent(session, *key) for key in sorted({old_key, new_key})}
    old = session.scalar(select(SalesReceipt).where(SalesReceipt.id == receipt_id).with_for_update()
                         .execution_options(populate_existing=True))
    svc._version(old, version)
    if old.status != 'DA_XAC_NHAN':
        raise BusinessError('BAD_STATUS', 'Chỉ sửa báo thu đã xác nhận. Báo thu đang chờ thì kế toán từ chối và báo lại.',
                            {'status': old.status}, status=409)
    if (old_key, old.amount, old.received_at, old.method) == (new_key, amount, received_at, method):
        raise BusinessError('NO_CHANGE', 'Chưa đổi gì so với báo thu cũ.', status=409)
    old_parent, new_parent = parents[old_key], parents[new_key]
    if old_key[0] == 'DB' and svc.balance(old_parent)['paid'] < old.amount:
        raise BusinessError('REFUND_PAID', 'Đơn đã hoàn tiền, không thể sửa làm số thu thuần âm.', status=409)
    limit = _limit(kind, new_parent)

    today = cash_docs.today()
    new_fund = finance_mappings.require(session, 'THU_TIEN_MAT' if method == 'TIEN_MAT' else 'THU_CHUYEN_KHOAN')
    old_doc = cash_docs._load(session, old.cash_doc_id) if old.cash_doc_id is not None else None
    cash_posting._lock_balances(session, {new_fund} | ({old_doc.fund_code} if old_doc else set()))

    number = next_number(session, 'BT', received_at)
    changes = []
    if old_key != new_key:
        changes.append(f'{old_parent.number} → {new_parent.number}')
    if old.amount != amount:
        changes.append(f'{_money(old.amount)} → {_money(amount)}')
    if old.received_at != received_at:
        changes.append(f'ngày {old.received_at:%d/%m/%Y} → {received_at:%d/%m/%Y}')
    if old.method != method:
        changes.append(f'{METHOD_LABELS[old.method]} → {METHOD_LABELS[method]}')
    text = f'Sửa báo thu → {number} ({"; ".join(changes)}): {reason}'[:2000]

    before = svc.snapshot(old)
    if old_doc is not None:
        # Không mở khóa ngày gốc: chỉ ngày đảo (hôm nay) phải còn mở, giống duyệt điều chỉnh phiếu thu chi.
        cash_posting.void_in_tx(session, user, old_doc.id, text, old_doc.version,
                                _correction_permit=cash_posting._CORRECTION_PERMIT)
    old.status, old.reason = 'DA_HUY', text
    old.decided_by, old.decided_at = user.id, datetime.now(UTC)
    session.flush()

    current = svc.balance(new_parent)
    if current['paid'] + current['pending'] + amount > limit:
        raise BusinessError('OVER_PAYMENT', f'{new_parent.number}: số tiền vượt số còn phải thu '
                            f'({_money(max(0, limit - current["paid"] - current["pending"]))} đ).',
                            {**current, 'limit': limit}, status=409)

    new = SalesReceipt(number=number, order_id=new_key[1] if kind == 'DB' else None,
                       ticket_id=new_key[1] if kind == 'DV' else None, opening_id=new_key[1] if kind == 'DK' else None,
                       method=method, amount=amount, received_at=received_at, note=old.note,
                       status='CHO_XAC_NHAN', reason='', reported_by=old.reported_by)
    session.add(new)
    session.flush()
    old.replaced_by_id = new.id
    svc._record(session, user, old, 'correct', before, text)
    svc._record(session, user, new, 'report', None, text)
    new_before = svc.snapshot(new)
    doc_date = today if day_closings.is_closed(session, received_at) else received_at
    svc._post_cash(session, user, new_parent, new, doc_date=doc_date)
    new.status, new.decided_by, new.decided_at = 'DA_XAC_NHAN', user.id, datetime.now(UTC)
    svc._record(session, user, new, 'confirm', new_before, text)
    return new


def is_correction(receipt) -> bool:
    return (receipt.reason or '').startswith('Sửa báo thu')


# ---- Màn sửa: chọn chứng từ ----

MODELS = {'DB': SalesOrder, 'DV': ServiceTicket, 'DK': CustomerOpeningDebt}


def _room(kind, parent):
    b = svc.balance(parent)
    limit = sales_orders.effective_total(parent) if kind == 'DB' else (parent.receivable or 0)
    return max(0, limit - b['paid'] - b['pending'])


def options(session, receipt, customer_code, limit=100):
    """Chứng từ của khách còn gán được báo thu (cùng điều kiện báo thu: đơn đã chốt/giao, phiếu DV đã xong còn phải thu,
    nợ đầu kỳ), mới trước; room = số tối đa BT sửa được gán vào. Chứng từ đang gán luôn có mặt."""
    old_key = key_of(receipt)
    found = [('DB', o) for o in session.scalars(select(SalesOrder).where(
        SalesOrder.customer_code == customer_code, SalesOrder.status.in_(('DA_CHOT', 'GIAO_MOT_PHAN', 'DA_GIAO')))
        .order_by(SalesOrder.id.desc()).limit(limit))]
    found += [('DV', t) for t in session.scalars(select(ServiceTicket).where(
        ServiceTicket.customer_code == customer_code, ServiceTicket.status.in_(('HOAN_THANH', 'DA_DONG')),
        ServiceTicket.receivable > 0, ServiceTicket.billing != 'MIEN_PHI').order_by(ServiceTicket.id.desc()).limit(limit))]
    found += [('DK', d) for d in session.scalars(select(CustomerOpeningDebt).where(CustomerOpeningDebt.customer_code == customer_code))]
    keys = {(k, row.id) for k, row in found}
    if old_key not in keys:
        parent = session.get(MODELS[old_key[0]], old_key[1])
        if parent is not None and parent.customer_code == customer_code:
            found.append((old_key[0], parent))
    rows = []
    for kind, parent in found:
        current = (kind, parent.id) == old_key
        room = _room(kind, parent) + (receipt.amount if current else 0)
        if room > 0 or current:
            rows.append(dict(kind=kind, id=parent.id, number=parent.number, current=current, room=room))
    rows.sort(key=lambda r: (not r['current'], r['kind'] != 'DK', r['number']))
    return rows


def parent_info(session, receipt):
    kind, ident = key_of(receipt)
    parent = session.get(MODELS[kind], ident)
    return dict(kind=kind, id=ident, number=parent.number, customer_code=parent.customer_code)


# ---- Zalo báo giám đốc + kế toán ----

def notice_text(session, old, new):
    from .dispatch_alerts import base_url
    who = session.get(User, new.decided_by)
    rest = old.reason.removeprefix(f'Sửa báo thu → {new.number} (')
    changes, _, why = rest.partition('): ')
    return '\n'.join([f'Sửa báo thu {old.number} → {new.number} ({who.full_name if who else "giám đốc"})',
                      changes, f'Lý do: {why}', f'{base_url()}/bao-thu/{new.id}'])


def notify(receipt_id: int) -> int:
    """Chạy nền sau khi sửa xong; Zalo lỗi vẫn ghi ở Tin nhắn Zalo, không làm hỏng việc sửa."""
    from sqlalchemy.orm import Session
    from . import zalo_bot
    from .config import get_settings
    from .db import engine
    from .models import ZaloChat
    if not get_settings().zalo_bot_token.strip():
        return 0
    count = 0
    with Session(engine()) as session:
        old = session.get(SalesReceipt, receipt_id)
        new = session.get(SalesReceipt, old.replaced_by_id) if old and old.replaced_by_id else None
        if new is None:
            return 0
        body = notice_text(session, old, new)
        people = session.execute(select(User, ZaloChat).join(ZaloChat, ZaloChat.user_id == User.id)
                                 .where(User.active.is_(True), ZaloChat.chat_type == 'PRIVATE').order_by(User.id, ZaloChat.id)).all()
        done = set()
        for user, chat in people:
            if user.id in done or not has_role(user, 'finance'):
                continue
            done.add(user.id)
            zalo_bot.send_text(session, chat, body, None, 'thong_bao')
            count += 1
    return count

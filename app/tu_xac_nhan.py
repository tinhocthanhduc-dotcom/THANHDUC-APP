"""Giám đốc soát báo thu kế toán tự xác nhận (TUẤN duyệt mẫu 17/11/2026).

Chỉ đọc + nhắn Zalo: danh sách báo thu người lập tự xác nhận trong hạn mức (sales_receipts.self_confirmed), dùng cho
tab "Tự xác nhận" ở trang Báo thu và một đoạn trong tin gộp cuối ngày gửi giám đốc. Không đổi tiền, quỹ, công nợ.
"""
from datetime import datetime, time, timedelta

from sqlalchemy import select

from . import sales_receipts
from .models import Customer, CustomerOpeningDebt, DispatchZaloSent, SalesOrder, ServiceTicket, User

LAST_DEFAULT = '17:45'
METHODS = {'TIEN_MAT': 'Tiền mặt', 'CHUYEN_KHOAN': 'CK'}


def _money(value) -> str:
    return f'{int(value):,}'.replace(',', '.')


def describe(session, receipts) -> list[dict]:
    """Thêm số chứng từ nguồn, tên khách, tên người lập cho từng báo thu."""
    orders = dict(session.execute(select(SalesOrder.id, SalesOrder).where(SalesOrder.id.in_({r.order_id for r in receipts if r.order_id}))).all())
    tickets = dict(session.execute(select(ServiceTicket.id, ServiceTicket).where(ServiceTicket.id.in_({r.ticket_id for r in receipts if r.ticket_id}))).all())
    openings = dict(session.execute(select(CustomerOpeningDebt.id, CustomerOpeningDebt)
                                    .where(CustomerOpeningDebt.id.in_({r.opening_id for r in receipts if r.opening_id}))).all())
    parents = {r.id: orders.get(r.order_id) or tickets.get(r.ticket_id) or openings.get(r.opening_id) for r in receipts}
    names = dict(session.execute(select(Customer.code, Customer.name)
                                 .where(Customer.code.in_({p.customer_code for p in parents.values() if p is not None}))).all())
    people = dict(session.execute(select(User.id, User.full_name).where(User.id.in_({r.reported_by for r in receipts}))).all())
    out = []
    for r in receipts:
        p = parents[r.id]
        out.append(dict(receipt=r, source=p.number if p is not None else '', customer=names.get(p.customer_code, '') if p is not None else '',
                        who=people.get(r.reported_by, str(r.reported_by))))
    return out


def day_rows(session, day) -> list[dict]:
    """Báo thu tự xác nhận trong một ngày (giờ Việt Nam)."""
    from .nhan_vien_ao import TZ
    start = datetime.combine(day, time.min, TZ)
    return describe(session, sales_receipts.self_confirmed(session, start, start + timedelta(days=1)))


def totals(rows) -> dict:
    cash = sum(x['receipt'].amount for x in rows if x['receipt'].method == 'TIEN_MAT')
    total = sum(x['receipt'].amount for x in rows)
    return dict(n=len(rows), total=total, cash=cash, bank=total - cash)


def zalo_text(rows, day) -> str:
    t = totals(rows)
    by_person = {}
    for x in rows:
        by_person.setdefault(x['who'], []).append(x)
    lines = [f'KẾ TOÁN TỰ XÁC NHẬN BÁO THU · {day.strftime("%d/%m")}', f'{t["n"]} phiếu · {_money(t["total"])} đ']
    for who, items in by_person.items():
        lines.append(f'{who}:')
        for x in items[:15]:
            r = x['receipt']
            lines.append(f'• {r.number} · {x["customer"][:40] or x["source"]} · {METHODS.get(r.method, r.method)} · {_money(r.amount)}')
        if len(items) > 15:
            lines.append(f'• … và {len(items) - 15} phiếu nữa')
    lines.append(f'Tiền mặt {_money(t["cash"])} · Chuyển khoản {_money(t["bank"])}')
    lines.append('Soát ở trang Báo thu, tab Tự xác nhận. Phiếu sai: mở phiếu, bấm Sửa báo thu hoặc Hủy báo thu.')
    return '\n'.join(lines)


def run_once(session, now=None, sender=None) -> int:
    """Gọi mỗi phút trước khi gửi tin gộp: tới mốc cuối ngày thì nhắn giám đốc danh sách phiếu tự xác nhận từ mốc hôm trước.
    Không có phiếu thì không nhắn. Tin gộp đang bật thì tin này được giữ và đi chung tin gộp cùng mốc."""
    from . import nhan_vien_ao as nva, tin_gop
    from .zalo_bot import send_text
    if sender is None and not nva._zalo_ready():
        return 0
    now = now or tin_gop._now()
    row = tin_gop._active(session)
    last = max(nva.cfg(row)['times']) if row is not None and nva.cfg(row)['times'] else LAST_DEFAULT
    if tin_gop.slot(now, [last]) != last:
        return 0
    local = now.astimezone(nva.TZ)
    day = local.date()
    mark = f'txn:{day.isoformat()}'
    chats = nva.chats(session)
    directors = [u for u in session.scalars(select(User).where(User.active.is_(True)).order_by(User.id))
                 if 'director' in (u.roles or []) and u.id in chats]
    todo = [u for u in directors if session.get(DispatchZaloSent, (mark, u.id)) is None]
    if not todo:
        return 0
    end = datetime.combine(day, datetime.strptime(last, '%H:%M').time(), nva.TZ) + tin_gop.GRACE
    rows = describe(session, sales_receipts.self_confirmed(session, end - timedelta(days=1), end))
    if not rows:
        return 0
    body = zalo_text(rows, day)
    return sum(1 for u in todo if nva._send(session, chats[u.id], u.id, mark, body, sender or send_text))

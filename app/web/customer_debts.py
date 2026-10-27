"""Công nợ khách hàng: danh sách khuôn 3 cho kế toán, giám đốc. Chỉ đọc, cùng nguồn với thẻ Công nợ trang khách và Báo cáo tài chính."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import customers as customer_svc, finance_reports
from ..db import get_session
from ..models import Customer, CustomerPhone, SalesOrder, SalesReceipt, ServiceTicket, User
from ..text import fold
from . import money, nav, paging, render, web_user
from .lists import KINDS, OLD_DEBT_DAYS, _today
from .. import search as tim

router = APIRouter()
TABS = {'no': 'Còn nợ', 'chua-tra': 'Khách chưa trả', 'o-nv': 'Tiền ở nhân viên', 'qua-han': f'Quá {OLD_DEBT_DAYS} ngày', 'vuot': 'Vượt hạn mức'}
OLD_TABS = {'cho': 'o-nv'}  # link cũ trước khi tách tiền ở nhân viên
TRANSFER = 'ck'  # khóa "người giữ" của chuyển khoản: tiền đã vào tài khoản công ty, không ở người
SORTS = {'no': 'Nợ nhiều nhất', 'lau': 'Nợ lâu nhất', 'ten': 'Tên khách'}
DOC_LIMIT = 3


def debt_user(user: User = Depends(web_user)):
    finance_reports.check_reader(user)
    return user


def _holders(session, codes, today):
    """Báo thu chờ xác nhận gom theo khách và người giữ: tiền mặt ở người báo thu, chuyển khoản chờ đối soát. Chỉ đọc."""
    customer = func.coalesce(SalesOrder.customer_code, ServiceTicket.customer_code)
    stmt = (select(customer.label('code'), SalesReceipt.method, SalesReceipt.reported_by, User.full_name,
                   func.sum(SalesReceipt.amount).label('amount'), func.count().label('n'), func.min(SalesReceipt.reported_at).label('since'))
            .outerjoin(SalesOrder, SalesOrder.id == SalesReceipt.order_id)
            .outerjoin(ServiceTicket, ServiceTicket.id == SalesReceipt.ticket_id)
            .join(User, User.id == SalesReceipt.reported_by)
            .where(SalesReceipt.status == 'CHO_XAC_NHAN', customer.in_(codes))
            .group_by(customer, SalesReceipt.method, SalesReceipt.reported_by, User.full_name))
    out = {}
    for r in session.execute(stmt):
        cash = r.method == 'TIEN_MAT'
        since = r.since.astimezone(finance_reports.TZ).date() if r.since else today
        out.setdefault(r.code, []).append(dict(key=str(r.reported_by) if cash else TRANSFER, cash=cash, name=r.full_name if cash else '',
                                               label=f'Tiền mặt ở {r.full_name}' if cash else 'Chuyển khoản chờ đối soát',
                                               amount=int(r.amount), n=int(r.n), days=max(0, (today - since).days)))
    for items in out.values():
        items.sort(key=lambda h: (not h['cash'], -h['amount']))
    return out


def _rows(session, user, today):
    """Gom từng chứng từ còn nợ theo khách; không có luật tiền mới, chỉ cộng lại các dòng của finance_reports._debts().
    Còn nợ (sổ) = khách chưa trả + đang ở nhân viên (báo thu chờ xác nhận)."""
    d = finance_reports._debts()
    by_code = {}
    for r in session.execute(select(d).order_by(d.c.day, d.c.kind, d.c.id)):
        x = by_code.setdefault(r.customer_code, dict(code=r.customer_code, remaining=0, pending=0, since=None, docs=[]))
        x['remaining'] += int(r.remaining); x['pending'] += int(r.pending)
        unpaid = max(0, int(r.remaining) - int(r.pending))
        day = r.day or today
        if unpaid:  # Nợ từ / quá hạn chỉ tính chứng từ khách còn chưa trả; trả hết cho thợ thì không còn là khách nợ lâu
            x['since'] = day if x['since'] is None or day < x['since'] else x['since']
        href = f'/don-ban/{r.id}#thu-tien' if r.kind == 'DB' else nav.ticket_url(user, r.id, '#thu-tien')
        x['docs'].append(dict(kind=r.kind, number=r.number, href=href, remaining=int(r.remaining), unpaid=unpaid, day=day))
    if not by_code:
        return []
    customers = {c.code: c for c in session.scalars(select(Customer).where(Customer.code.in_(list(by_code))))}
    phones = {}
    for code, phone in session.execute(select(CustomerPhone.customer_code, CustomerPhone.phone)
                                       .where(CustomerPhone.customer_code.in_(list(by_code)), CustomerPhone.is_contact.is_(False)).order_by(CustomerPhone.phone)):
        phones.setdefault(code, []).append(phone)
    last = finance_reports.last_receipts(session, user)
    holders = _holders(session, list(by_code), today)
    rows = []
    for code, x in by_code.items():
        c = customers.get(code)
        if c is None:
            continue
        limit = c.credit_limit if c.allow_debt else 0
        unpaid = x['remaining'] - x['pending']
        pay = next((doc['href'] for doc in x['docs'] if doc['href'] and doc['unpaid']), '')  # chứng từ cũ nhất khách còn chưa trả: Báo thu ngay trên đó
        since = x['since'] or min(doc['day'] for doc in x['docs'])
        days = (today - since).days
        rows.append(dict(x, c=c, name=c.name, kind=c.kind, phones=phones.get(code, []), since=since, days=days, pay_href=pay,
                         unpaid=unpaid, holders=holders.get(code, []),
                         old=unpaid > 0 and days > OLD_DEBT_DAYS, limit=limit, allow_debt=c.allow_debt,
                         over=x['remaining'] > limit, left=limit - x['remaining'], last=last.get(code),
                         orders=sum(1 for doc in x['docs'] if doc['kind'] == 'DB'), tickets=sum(1 for doc in x['docs'] if doc['kind'] != 'DB'),
                         search=fold(' '.join([c.search_text or '', c.code, c.name, *phones.get(code, []), *(doc['number'] for doc in x['docs'])]))))
    return rows


def _match(row, q):
    needle = fold(q)
    if not needle:
        return True
    digits = ''.join(ch for ch in q if ch.isdigit())
    return tim.text_matches(q, row['search']) or (len(digits) >= 3 and any(digits in p for p in row['phones']))


@router.get('/cong-no-khach-hang')
def listing(request: Request, q: str = '', tab: str = '', loai: str = '', xep: str = '', giu: str = '', trang: str = '', so: str = '',
            user: User = Depends(debt_user), session: Session = Depends(get_session)):
    today = _today()
    q, loai = q[:200], loai if loai in KINDS else ''
    tab = OLD_TABS.get(tab, tab)
    tab, xep = (tab if tab in TABS else 'no'), (xep if xep in SORTS else 'no')
    all_rows = _rows(session, user, today)
    # Người giữ tiền: gom trên toàn bộ khách còn nợ để làm ô số và ô chọn.
    people = {}
    for r in all_rows:
        for h in r['holders']:
            g = people.setdefault(h['key'], dict(key=h['key'], label=h['label'], cash=h['cash'], amount=0, n=0, days=0))
            g['amount'] += h['amount']; g['n'] += h['n']; g['days'] = max(g['days'], h['days'])
    people = sorted(people.values(), key=lambda g: (not g['cash'], -g['amount']))
    giu = giu if giu in {g['key'] for g in people} else ''
    found = [r for r in all_rows if _match(r, q) and (not loai or r['kind'] == loai) and (not giu or any(h['key'] == giu for h in r['holders']))]
    pick = {'no': lambda r: True, 'chua-tra': lambda r: r['unpaid'] > 0, 'o-nv': lambda r: r['pending'] > 0,
            'qua-han': lambda r: r['old'], 'vuot': lambda r: r['over']}
    counts = {k: sum(1 for r in found if pick[k](r)) for k in TABS}
    rows = [r for r in found if pick[tab](r)]
    amount = {'chua-tra': 'unpaid', 'o-nv': 'pending'}.get(tab, 'remaining')  # tab tiền ở NV xếp theo tiền đang giữ
    rows.sort(key={'no': lambda r: (-r[amount], r['code']), 'lau': lambda r: (r['since'], r['code']), 'ten': lambda r: (fold(r['name']), r['code'])}[xep])
    query = dict(q=q, tab='' if tab == 'no' else tab, loai=loai, xep='' if xep == 'no' else xep, giu=giu)
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/cong-no-khach-hang', query)
    # Ô số đếm trên toàn bộ khách còn nợ (không theo ô tìm); bấm ô là chuyển tab.
    groups = {k: [r for r in all_rows if pick[k](r)] for k in TABS}
    total = lambda k, key='remaining': sum(r[key] for r in groups[k])
    oldest = max((r['days'] for r in groups['qua-han']), default=0)
    short = lambda g: (g['label'].replace('Tiền mặt ở ', '') if g['cash'] else 'CK chờ đối soát') + ' ' + money(g['amount'])
    held = ' · '.join(short(g) for g in people[:2]) + (f' · +{len(people) - 2} người' if len(people) > 2 else '')
    tiles = [dict(label='Khách chưa trả', n=f"{len(groups['chua-tra'])} khách", sub=(money(total('chua-tra', 'unpaid')) + ' đ · phải đi thu') if groups['chua-tra'] else 'Không có',
                  href='/cong-no-khach-hang?tab=chua-tra', on=tab == 'chua-tra', tone='huy' if groups['chua-tra'] else ''),
             dict(label='Đã thu · đang ở nhân viên', n=money(total('o-nv', 'pending')), sub=held if people else 'Không có tiền chờ xác nhận',
                  href='/cong-no-khach-hang?tab=o-nv', on=tab == 'o-nv', tone='cho' if groups['o-nv'] else ''),
             dict(label=f'Khách nợ quá {OLD_DEBT_DAYS} ngày', n=f"{len(groups['qua-han'])} khách", sub=(money(total('qua-han', 'unpaid')) + f' đ · cũ nhất {oldest} ngày') if groups['qua-han'] else 'Không có',
                  href='/cong-no-khach-hang?tab=qua-han', on=tab == 'qua-han', tone='huy' if groups['qua-han'] else ''),
             dict(label='Vượt hạn mức · không cho nợ', n=f"{len(groups['vuot'])} khách", sub=(money(total('vuot')) + ' đ') if groups['vuot'] else 'Không có',
                  href='/cong-no-khach-hang?tab=vuot', on=tab == 'vuot', tone='huy' if groups['vuot'] else '')]
    tabs = [dict(label=label, n=counts[k], href=link(tab='' if k == 'no' else k), on=tab == k) for k, label in TABS.items()]
    chips = ([dict(label=KINDS[loai], href=link(loai=''))] if loai else []) + \
            [dict(label=g['label'], href=link(giu='')) for g in people if g['key'] == giu]
    return render(request, 'cong_no_khach_ds.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, q=q, tab=tab, loai=loai, xep=xep,
                  giu=giu, people=people, kinds=KINDS, sorts=SORTS, doc_limit=DOC_LIMIT, old_days=OLD_DEBT_DAYS,
                  debt_sum=sum(r['remaining'] for r in rows), unpaid_sum=sum(r['unpaid'] for r in rows), pending_sum=sum(r['pending'] for r in rows))

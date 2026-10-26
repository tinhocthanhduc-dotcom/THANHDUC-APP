"""Công nợ khách hàng: danh sách khuôn 3 cho kế toán, giám đốc. Chỉ đọc, cùng nguồn với thẻ Công nợ trang khách và Báo cáo tài chính."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import customers as customer_svc, finance_reports
from ..db import get_session
from ..models import Customer, CustomerPhone, User
from ..text import fold
from . import money, nav, paging, render, web_user
from .lists import KINDS, OLD_DEBT_DAYS, _today

router = APIRouter()
TABS = {'no': 'Còn nợ', 'qua-han': f'Quá {OLD_DEBT_DAYS} ngày', 'vuot': 'Vượt hạn mức', 'cho': 'Có chờ xác nhận'}
SORTS = {'no': 'Nợ nhiều nhất', 'lau': 'Nợ lâu nhất', 'ten': 'Tên khách'}
DOC_LIMIT = 3


def debt_user(user: User = Depends(web_user)):
    finance_reports.check_reader(user)
    return user


def _rows(session, user, today):
    """Gom từng chứng từ còn nợ theo khách; không có luật tiền mới, chỉ cộng lại các dòng của finance_reports._debts()."""
    d = finance_reports._debts()
    by_code = {}
    for r in session.execute(select(d).order_by(d.c.day, d.c.kind, d.c.id)):
        x = by_code.setdefault(r.customer_code, dict(code=r.customer_code, remaining=0, pending=0, since=None, docs=[]))
        x['remaining'] += int(r.remaining); x['pending'] += int(r.pending)
        day = r.day or today
        x['since'] = day if x['since'] is None or day < x['since'] else x['since']
        href = f'/don-ban/{r.id}#thu-tien' if r.kind == 'DB' else nav.ticket_url(user, r.id, '#thu-tien')
        x['docs'].append(dict(kind=r.kind, number=r.number, href=href, remaining=int(r.remaining)))
    if not by_code:
        return []
    customers = {c.code: c for c in session.scalars(select(Customer).where(Customer.code.in_(list(by_code))))}
    phones = {}
    for code, phone in session.execute(select(CustomerPhone.customer_code, CustomerPhone.phone)
                                       .where(CustomerPhone.customer_code.in_(list(by_code)), CustomerPhone.is_contact.is_(False)).order_by(CustomerPhone.phone)):
        phones.setdefault(code, []).append(phone)
    last = finance_reports.last_receipts(session, user)
    rows = []
    for code, x in by_code.items():
        c = customers.get(code)
        if c is None:
            continue
        limit = c.credit_limit if c.allow_debt else 0
        pay = next((doc['href'] for doc in x['docs'] if doc['href']), '')  # chứng từ cũ nhất còn nợ: Báo thu ngay trên đó
        rows.append(dict(x, c=c, name=c.name, kind=c.kind, phones=phones.get(code, []), days=(today - x['since']).days, pay_href=pay,
                         old=(today - x['since']).days > OLD_DEBT_DAYS, limit=limit, allow_debt=c.allow_debt,
                         over=x['remaining'] > limit, left=limit - x['remaining'], last=last.get(code),
                         orders=sum(1 for doc in x['docs'] if doc['kind'] == 'DB'), tickets=sum(1 for doc in x['docs'] if doc['kind'] != 'DB'),
                         search=fold(' '.join([c.search_text or '', c.code, c.name, *phones.get(code, []), *(doc['number'] for doc in x['docs'])]))))
    return rows


def _match(row, q):
    needle = fold(q)
    if not needle:
        return True
    digits = ''.join(ch for ch in q if ch.isdigit())
    return needle in row['search'] or (len(digits) >= 3 and any(digits in p for p in row['phones']))


@router.get('/cong-no-khach-hang')
def listing(request: Request, q: str = '', tab: str = '', loai: str = '', xep: str = '', trang: str = '', so: str = '',
            user: User = Depends(debt_user), session: Session = Depends(get_session)):
    today = _today()
    q, loai = q[:200], loai if loai in KINDS else ''
    tab, xep = (tab if tab in TABS else 'no'), (xep if xep in SORTS else 'no')
    all_rows = _rows(session, user, today)
    found = [r for r in all_rows if _match(r, q) and (not loai or r['kind'] == loai)]
    pick = {'no': lambda r: True, 'qua-han': lambda r: r['old'], 'vuot': lambda r: r['over'], 'cho': lambda r: r['pending'] > 0}
    counts = {k: sum(1 for r in found if pick[k](r)) for k in TABS}
    rows = [r for r in found if pick[tab](r)]
    rows.sort(key={'no': lambda r: (-r['remaining'], r['code']), 'lau': lambda r: (r['since'], r['code']), 'ten': lambda r: (fold(r['name']), r['code'])}[xep])
    query = dict(q=q, tab='' if tab == 'no' else tab, loai=loai, xep='' if xep == 'no' else xep)
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/cong-no-khach-hang', query)
    # Ô số đếm trên toàn bộ khách còn nợ (không theo ô tìm); bấm ô là chuyển tab.
    groups = {k: [r for r in all_rows if pick[k](r)] for k in TABS}
    total = lambda k, key='remaining': sum(r[key] for r in groups[k])
    oldest = max((r['days'] for r in groups['qua-han']), default=0)
    tiles = [dict(label='Đang còn nợ', n=f"{len(groups['no'])} khách", sub=money(total('no')) + ' đ', href='/cong-no-khach-hang', on=tab == 'no', tone='huy' if groups['no'] else ''),
             dict(label=f'Nợ quá {OLD_DEBT_DAYS} ngày', n=f"{len(groups['qua-han'])} khách", sub=(money(total('qua-han')) + f' đ · cũ nhất {oldest} ngày') if groups['qua-han'] else 'Không có',
                  href='/cong-no-khach-hang?tab=qua-han', on=tab == 'qua-han', tone='huy' if groups['qua-han'] else ''),
             dict(label='Vượt hạn mức · không cho nợ', n=f"{len(groups['vuot'])} khách", sub=(money(total('vuot')) + ' đ') if groups['vuot'] else 'Không có',
                  href='/cong-no-khach-hang?tab=vuot', on=tab == 'vuot', tone='huy' if groups['vuot'] else ''),
             dict(label='Báo thu chờ xác nhận', n=money(total('cho', 'pending')), sub=(f"{len(groups['cho'])} khách · trừ nợ khi xác nhận") if groups['cho'] else 'Không có báo thu chờ',
                  href='/bao-thu', on=False, tone='cho' if groups['cho'] else '')]
    tabs = [dict(label=label, n=counts[k], href=link(tab='' if k == 'no' else k), on=tab == k) for k, label in TABS.items()]
    chips = [dict(label=KINDS[loai], href=link(loai=''))] if loai else []
    return render(request, 'cong_no_khach_ds.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, q=q, tab=tab, loai=loai, xep=xep,
                  kinds=KINDS, sorts=SORTS, doc_limit=DOC_LIMIT, old_days=OLD_DEBT_DAYS,
                  debt_sum=sum(r['remaining'] for r in rows), pending_sum=sum(r['pending'] for r in rows))

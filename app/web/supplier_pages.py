"""Đợt 7: danh sách Nhà cung cấp theo khuôn 3 và trang tổng quan một NCC (giống trang khách Đợt 4). Chỉ đọc."""
from datetime import timedelta

from sqlalchemy import func, select

from .. import inventory_catalogs as catalog_svc, supplier_debts
from ..customers import like_pattern
from ..models import Product, PurchaseOrder, PurchaseOrderLine, PurchaseReceipt, Supplier
from ..security import has_role
from ..text import fold
from . import money, nav, paging, render
from .lists import _today
from .purchases import OPEN, STATUSES as PO_STATUSES, TONES as PO_TONES

TABS = {'dang-dung': 'Đang dùng', 'ngung': 'Ngừng sử dụng', 'tat-ca': 'Tất cả'}
PO_TABS = {'mo': 'Đang mở', 'DA_NHAN': 'Đã nhận đủ', 'DA_HUY': 'Đã hủy', 'tat-ca': 'Tất cả'}
PO_OPEN = ('NHAP', *OPEN)


def _debts(session, user):
    """Còn nợ theo NCC, cùng nguồn với Công nợ NCC. Chỉ kế toán, giám đốc."""
    if not has_role(user, 'finance'):
        return None
    b = supplier_debts._balances().subquery()
    return {code: int(rem) for code, rem in session.execute(select(b.c.supplier_code, b.c.remaining).where(b.c.remaining != 0))}


def list_page(request, session, user, q='', tt='', ve='', no=''):
    q = q[:200]
    tab = tt if tt in TABS else 'dang-dung'
    debts = _debts(session, user)
    ve, no = ('1' if ve else ''), ('1' if no and debts is not None else '')
    stmt = select(Supplier)
    if fold(q):
        stmt = stmt.where(Supplier.search_text.like(like_pattern(fold(q))))
    found = list(session.scalars(stmt.order_by(Supplier.code)))
    orders = list(session.scalars(select(PurchaseOrder).where(PurchaseOrder.status != 'DA_HUY')))
    last, coming = {}, {}
    for o in orders:
        if o.status != 'NHAP' and (o.supplier_code not in last or (o.order_date, o.id) > (last[o.supplier_code].order_date, last[o.supplier_code].id)):
            last[o.supplier_code] = o
        if o.status in OPEN:
            coming.setdefault(o.supplier_code, []).append(o)
    counts = {'dang-dung': sum(1 for s in found if s.active), 'ngung': sum(1 for s in found if not s.active), 'tat-ca': len(found)}
    rows = [s for s in found if (tab == 'tat-ca' or s.active == (tab == 'dang-dung'))
            and (not ve or s.code in coming) and (not no or (debts or {}).get(s.code, 0) > 0)]
    query = dict(q=q, tt='' if tab == 'dang-dung' else tab, ve=ve, no=no)
    page, shown = paging.slice_rows(rows, request.query_params.get('trang'), request.query_params.get('so'), query)
    link = paging.linker('/nha-cung-cap', query)
    # Ô số đếm toàn bộ NCC (không theo ô tìm), bấm là lọc.
    alls = list(session.execute(select(Supplier.code, Supplier.active)))
    tiles = [dict(label='Đang dùng', n=sum(1 for _, a in alls if a), sub='Chọn được khi lập đơn mua', href='/nha-cung-cap', on=tab == 'dang-dung' and not ve and not no),
             dict(label='Có hàng đang về', n=len(coming), sub=f'{sum(len(v) for v in coming.values())} đơn chưa nhận đủ' if coming else 'Không có đơn chờ nhận',
                  href='/nha-cung-cap?ve=1', on=bool(ve), tone='cho' if coming else '')]
    if debts is not None:
        owing = {c: v for c, v in debts.items() if v > 0}
        tiles.append(dict(label='Đang còn nợ', n=len(owing), sub=money(sum(owing.values())) + ' đ', href='/nha-cung-cap?no=1', on=bool(no), tone='huy' if owing else ''))
    tiles.append(dict(label='Ngừng sử dụng', n=sum(1 for _, a in alls if not a), sub='Không chọn được khi mua', href='/nha-cung-cap?tt=ngung', on=tab == 'ngung'))
    tabs = [dict(label=label, n=counts[k], href=link(tt='' if k == 'dang-dung' else k), on=tab == k) for k, label in TABS.items()]
    chips = [dict(label=label, href=link(**{key: ''})) for key, label in (('ve', 'Có hàng đang về'), ('no', 'Đang còn nợ')) if query.get(key)]
    left = {}
    if coming:
        for order_id, n in session.execute(select(PurchaseOrderLine.order_id, func.count()).where(
                PurchaseOrderLine.order_id.in_([o.id for v in coming.values() for o in v]),
                PurchaseOrderLine.received_qty < PurchaseOrderLine.qty).group_by(PurchaseOrderLine.order_id)):
            left[order_id] = n
    left = {code: sum(left.get(o.id, 0) for o in v) for code, v in coming.items()}
    return render(request, 'ncc_ds.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, q=q, tab=tab, ve=ve, no=no,
                  last=last, coming=coming, left=left, debts=debts, can_write=has_role(user, *catalog_svc.WRITERS),
                  debt_sum=sum((debts or {}).get(s.code, 0) for s in rows))


def _po_next(o, today):
    """Bước tiếp của đơn mua: (ai, việc, trễ). Chỉ để nhắc, không thay luật chuyển trạng thái."""
    late = o.status in OPEN and o.expected_date is not None and o.expected_date < today
    if o.status == 'NHAP':
        return 'kho', 'Kiểm tra rồi Đặt hàng', False
    if o.status == 'DA_DAT':
        when = o.expected_date.strftime(' · hẹn %d/%m') if o.expected_date else ''
        return 'kho', ('Nhận hàng' + when) + (', quá hẹn' if late else ''), late
    if o.status == 'NHAN_MOT_PHAN':
        return 'kho', 'Nhận nốt hàng' + (o.expected_date.strftime(' · quá hẹn %d/%m') if late else ''), late
    if o.status == 'DA_NHAN':
        return 'không ai', 'Đã nhận đủ', False
    return 'không ai', 'Đã hủy', False


def overview(request, session, user, s, tab='mo', saved=False):
    code, today = s.code, _today()
    see_orders = nav.allowed(user, '/don-mua')
    tab = tab if tab in PO_TABS else 'mo'
    orders = list(session.scalars(select(PurchaseOrder).where(PurchaseOrder.supplier_code == code)
                                  .order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.id.desc()))) if see_orders else []
    product_names = {}
    if orders:
        codes = {l.product_code for o in orders for l in o.lines}
        product_names = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_(codes))).all()) if codes else {}
    docs = []
    for o in orders:
        who, title, late = _po_next(o, today)
        names = [f'{product_names.get(l.product_code, l.product_code)} × {l.qty:g}' for l in o.lines]
        what = ', '.join(names[:2]) + (f', {len(names) - 2} dòng khác' if len(names) > 2 else '')
        kho = sorted({l.warehouse_code for l in o.lines})
        docs.append(dict(o=o, no=o.number, href=f'/don-mua/{o.id}', what=what or '—', kho=('Kho nhận ' + ', '.join(kho)) if kho else '',
                         status=PO_STATUSES.get(o.status, o.status), tone=PO_TONES.get(o.status, 'nhap'), who=who, next=title, late=late,
                         open=o.status in PO_OPEN, amount=o.total))
    tab_rows = {'mo': [d for d in docs if d['open']], 'DA_NHAN': [d for d in docs if d['o'].status == 'DA_NHAN'],
                'DA_HUY': [d for d in docs if d['o'].status == 'DA_HUY'], 'tat-ca': docs}
    link = paging.linker(f'/nha-cung-cap/{code}', {'tab': '' if tab == 'mo' else tab})
    page, rows = paging.slice_rows(tab_rows[tab], request.query_params.get('trang'), request.query_params.get('so'), {'tab': '' if tab == 'mo' else tab})
    tabs = [dict(label=label, n=len(tab_rows[k]), href=link(tab='' if k == 'mo' else k, trang=''), on=k == tab) for k, label in PO_TABS.items()]
    coming = [d['o'] for d in docs if d['o'].status in OPEN]
    left_lines = session.scalar(select(func.count()).select_from(PurchaseOrderLine).where(
        PurchaseOrderLine.order_id.in_([o.id for o in coming]), PurchaseOrderLine.received_qty < PurchaseOrderLine.qty)) if coming else 0
    year_ago = today - timedelta(days=365)
    received = session.execute(select(func.count(), func.coalesce(func.sum(PurchaseReceipt.total), 0)).join(PurchaseOrder, PurchaseOrder.id == PurchaseReceipt.order_id)
                               .where(PurchaseOrder.supplier_code == code, PurchaseReceipt.status == 'DA_GHI', PurchaseReceipt.receipt_date >= year_ago)).one() if see_orders else (0, 0)
    open_docs = tab_rows['mo']
    tiles = []
    if see_orders:
        tiles += [dict(label='Đơn đang mở', n=len(open_docs), sub=f"{sum(1 for d in open_docs if d['o'].status == 'NHAP')} nháp · {len(coming)} đã đặt", href=link(tab='', trang=''), on=tab == 'mo'),
                  dict(label='Hàng đang về', n=f'{len(coming)} đơn', sub=f'{left_lines} dòng chưa nhận đủ' if coming else 'Không có hàng chờ nhận',
                       href=link(tab='', trang=''), on=False, tone='cho' if coming else '')]
    debt = None
    if has_role(user, 'finance'):
        debt = supplier_debts.balance(session, user, code)
        tiles.append(dict(label='Còn nợ', n=money(debt['remaining']), sub=('Đã trả ' + money(debt['paid'])) if debt['paid'] else 'Chưa trả lần nào',
                          href='#cong-no', on=False, tone='huy' if debt['remaining'] > 0 else ''))
    if see_orders:
        tiles.append(dict(label='Đã nhận 12 tháng', n=f'{received[0]} lần', sub=money(int(received[1])) + ' đ', href=link(tab='DA_NHAN', trang=''), on=tab == 'DA_NHAN', tone='xong'))
    # Hàng hay mua 12 tháng: đếm số đơn đã đặt có mã đó, giá = lần đặt gần nhất (chưa VAT).
    often = []
    if see_orders:
        stats = {}
        for o in sorted((o for o in orders if o.status not in ('NHAP', 'DA_HUY') and o.order_date >= year_ago), key=lambda o: (o.order_date, o.id)):
            for l in o.lines:
                x = stats.setdefault(l.product_code, dict(code=l.product_code, name=product_names.get(l.product_code, l.product_code), n=0, cost=0))
                x['n'] += 1
                x['cost'] = l.unit_cost
        often = sorted(stats.values(), key=lambda x: (-x['n'], x['name']))[:5]
    quick = []
    if s.active and has_role(user, 'warehouse'):
        quick.append(('+ Đơn mua', f'/don-mua/moi?supplier_code={code}'))
    if has_role(user, 'finance'):
        quick.append(('+ Trả tiền NCC', f'/cong-no-ncc/{code}#tra-tien'))
    return render(request, 'ncc_xem.jinja', s=s, tiles=tiles, tabs=tabs, tab=tab, rows=rows, page=page, debt=debt, often=often,
                  quick=quick, see_orders=see_orders, can_write=has_role(user, *catalog_svc.WRITERS), saved=saved)

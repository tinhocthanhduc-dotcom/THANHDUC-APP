"""Đợt 2b: danh sách Khách hàng, Hàng hóa theo khuôn 3 (ô đếm, tab, lọc tức thì, 25 dòng/trang). Chỉ đọc."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .. import customers as customer_svc, finance_reports, products as product_svc
from ..models import Customer, Product, ProductGroup, SalesOrder, ServiceTicket, StockBalance, StockMinLevel, Warehouse
from ..security import has_role
from ..text import fold
from . import money, nav, paging, render

VN = ZoneInfo('Asia/Ho_Chi_Minh')
CUSTOMER_TABS = {'DANG_DUNG': 'Đang dùng', 'NGUNG': 'Ngừng giao dịch', 'DA_GOP': 'Đã gộp'}
KINDS = {'CONG_TY': 'Công ty', 'CA_NHAN': 'Cá nhân'}
OLD_DEBT_DAYS = 30


def _today():
    return datetime.now(VN).date()


def _day(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def customers_page(request, session, user, q='', loai='', trang_thai='', no='', tu='', den='', trang='', so='', can_write=False):
    can_debt = has_role(user, *customer_svc.CREDIT_ROLES)
    q, loai = q[:200], loai if loai in KINDS else ''
    status = trang_thai if trang_thai in (*CUSTOMER_TABS, 'tat-ca') else 'DANG_DUNG'
    no = '1' if no and can_debt else ''
    start, end = _day(tu), _day(den)
    tu, den = (start.isoformat() if start else ''), (end.isoformat() if end else '')
    stmt = select(Customer).where(*customer_svc.matches(q))
    if loai:
        stmt = stmt.where(Customer.kind == loai)
    if start:
        stmt = stmt.where(Customer.created_at >= datetime.combine(start, datetime.min.time(), VN))
    if end:
        stmt = stmt.where(Customer.created_at < datetime.combine(end + timedelta(days=1), datetime.min.time(), VN))
    found = list(session.scalars(stmt.order_by(Customer.updated_at.desc(), Customer.code)))
    debts, today = {}, _today()
    if can_debt:
        d = finance_reports._debts()
        debts = {code: dict(remaining=int(rem), since=since or today) for code, rem, since in session.execute(
            select(d.c.customer_code, func.sum(d.c.remaining), func.min(d.c.day)).group_by(d.c.customer_code)) if rem > 0}

    def alert(c):
        x = debts.get(c.code)
        return bool(x) and (x['remaining'] > (c.credit_limit if c.allow_debt else 0) or (today - x['since']).days > OLD_DEBT_DAYS)
    counts = {code: sum(1 for c in found if c.status == code) for code in CUSTOMER_TABS}
    rows = [c for c in found if (status == 'tat-ca' or c.status == status) and (not no or c.code in debts)]
    query = dict(q=q, loai=loai, trang_thai='' if status == 'DANG_DUNG' else status, no=no, tu=tu, den=den)
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/khach-hang', query)
    codes = [c.code for c in shown]
    last = {}
    if codes:
        for model, label, path in ((SalesOrder, 'Đơn bán', '/don-ban/'), (ServiceTicket, 'Dịch vụ', '/dich-vu/')):
            for code, number, at in session.execute(select(model.customer_code, model.number, model.created_at)
                    .where(model.customer_code.in_(codes), model.status != 'DA_HUY')
                    .distinct(model.customer_code).order_by(model.customer_code, model.created_at.desc())):
                if code not in last or at > last[code]['at']:
                    last[code] = dict(number=number, label=label, at=at)
    # Ô số đếm khách đang dùng toàn hệ thống, không theo bộ lọc; bấm là lọc.
    alls = list(session.execute(select(Customer.code, Customer.kind, Customer.created_at).where(Customer.status == 'DANG_DUNG')))
    month = today.replace(day=1)
    new_month = sum(1 for _, _, at in alls if at.astimezone(VN).date() >= month)
    tiles = [dict(label='Khách công ty', n=sum(1 for _, k, _ in alls if k == 'CONG_TY'), sub='Có MST, xuất hóa đơn', href='/khach-hang?loai=CONG_TY', on=loai == 'CONG_TY'),
             dict(label='Khách cá nhân', n=sum(1 for _, k, _ in alls if k == 'CA_NHAN'), sub='Khách lẻ, sửa máy', href='/khach-hang?loai=CA_NHAN', on=loai == 'CA_NHAN')]
    if can_debt:
        owing = [debts[c] for c, _, _ in alls if c in debts]
        tiles.append(dict(label='Đang còn nợ', n=len(owing), sub=money(sum(x['remaining'] for x in owing)) + ' đ', href='/khach-hang?no=1', on=bool(no), tone='huy' if owing else ''))
    else:
        tiles.append(dict(label='Ngừng giao dịch', n=session.scalar(select(func.count()).where(Customer.status == 'NGUNG')) or 0, sub='Không chọn được khi bán', href='/khach-hang?trang_thai=NGUNG', on=status == 'NGUNG'))
    tiles.append(dict(label='Mới tháng này', n=new_month, sub='Tạo từ ' + month.strftime('%d/%m'), href=f'/khach-hang?tu={month.isoformat()}', on=False, tone='xong'))
    tabs = [dict(label=label, n=counts[code], href=link(trang_thai='' if code == 'DANG_DUNG' else code), on=status == code) for code, label in CUSTOMER_TABS.items()]
    tabs.append(dict(label='Tất cả', n=len(found), href=link(trang_thai='tat-ca'), on=status == 'tat-ca'))
    chips = [dict(label=label, href=link(**{key: ''})) for key, label in (('loai', KINDS.get(loai, '')), ('no', 'Đang còn nợ')) if query.get(key)]
    if tu or den:
        chips.append(dict(label='Ngày tạo', href=link(tu='', den='')))
    return render(request, 'khach_ds.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, q=q, loai=loai, no=no,
                  tu=tu, den=den, selected_status=status, debts=debts, last=last, alert=alert, can_debt=can_debt, can_write=can_write,
                  debt_sum=sum(debts[c.code]['remaining'] for c in rows if c.code in debts))


STOCK_STATES = {'con': 'Còn hàng', 'sap': 'Sắp hết', 'het': 'Hết hàng'}


def products_page(request, session, user, q='', nhom='', tt='', ton='', trang='', so='', can_write=False):
    q, tt = q[:200], tt if tt in ('ngung', 'tat-ca') else ''
    can_stock = nav.allowed(user, '/ton-kho')
    can_alert = nav.allowed(user, '/ton-kho', {'canh-bao': '1'})
    ton = ton if ton in STOCK_STATES and can_stock and (ton != 'sap' or can_alert) else ''
    groups = list(session.scalars(select(ProductGroup.name).order_by(ProductGroup.name)))
    nhom = nhom[:100]
    found = product_svc.search(session, q, active_only=False, limit=None, group=nhom)
    stock, low = {}, set()
    if can_stock:
        # Tồn bán được: chỉ kho thường (KHO), không tính kho lỗi, kho thợ.
        for wh, code, qty in session.execute(select(StockBalance.warehouse_code, StockBalance.product_code, StockBalance.qty)
                .join(Warehouse, Warehouse.code == StockBalance.warehouse_code).where(Warehouse.kind == 'KHO').order_by(StockBalance.warehouse_code)):
            stock.setdefault(code, {})[wh] = qty
        if can_alert:
            low = {code for (code,) in session.execute(select(StockMinLevel.product_code).outerjoin(StockBalance,
                (StockBalance.warehouse_code == StockMinLevel.warehouse_code) & (StockBalance.product_code == StockMinLevel.product_code))
                .join(Warehouse, Warehouse.code == StockMinLevel.warehouse_code).where(
                    Warehouse.kind == 'KHO', StockMinLevel.min_qty > 0, func.coalesce(StockBalance.qty, 0) <= StockMinLevel.min_qty))}

    def state(p):
        if p.code not in stock:
            return ''
        total = sum(stock[p.code].values())
        return 'het' if total <= 0 else 'sap' if p.code in low else 'con'
    states = {p.code: state(p) for p in found}
    counts = dict(ban=sum(1 for p in found if p.active), ngung=sum(1 for p in found if not p.active))
    rows = [p for p in found if (tt == 'tat-ca' or p.active == (tt != 'ngung')) and (not ton or states[p.code] == ton)]
    query = dict(q=q, nhom=nhom, tt=tt, ton=ton)
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/hang-hoa', query)
    active = [p for p in found if p.active] if not (q or nhom) else [p for p in session.scalars(select(Product).where(Product.active.is_(True)))]
    all_states = states if not (q or nhom) else {p.code: state(p) for p in active}
    tiles = [dict(label='Đang bán', n=len(active), sub=f'{len({p.group_name for p in active if p.group_name})} nhóm hàng', href='/hang-hoa', on=False)]
    if can_stock:
        if can_alert:
            n = sum(1 for p in active if all_states.get(p.code) == 'sap')
            tiles.append(dict(label='Sắp hết', n=n, sub='Dưới mức tối thiểu', href='/hang-hoa?ton=sap', on=ton == 'sap', tone='cho' if n else ''))
        n = sum(1 for p in active if all_states.get(p.code) == 'het')
        tiles.append(dict(label='Hết hàng', n=n, sub='Các kho thường bằng 0', href='/hang-hoa?ton=het', on=ton == 'het', tone='huy' if n else ''))
    stopped = session.scalar(select(func.count()).where(Product.active.is_(False))) or 0
    tiles.append(dict(label='Ngừng bán', n=stopped, sub='Ẩn khỏi ô chọn hàng', href='/hang-hoa?tt=ngung', on=tt == 'ngung'))
    tabs = [dict(label='Đang bán', n=counts['ban'], href=link(tt=''), on=not tt), dict(label='Ngừng bán', n=counts['ngung'], href=link(tt='ngung'), on=tt == 'ngung'),
            dict(label='Tất cả', n=len(found), href=link(tt='tat-ca'), on=tt == 'tat-ca')]
    chips = [dict(label=label, href=link(**{key: ''})) for key, label in (('nhom', 'Nhóm: ' + nhom), ('ton', STOCK_STATES.get(ton, ''))) if query.get(key)]
    warehouses = list(session.scalars(select(Warehouse.code).where(Warehouse.kind == 'KHO', Warehouse.active.is_(True)).order_by(Warehouse.code))) if can_stock else []
    return render(request, 'hang_ds.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, q=q, nhom=nhom, tt=tt, ton=ton,
                  groups=groups, stock=stock, states=states, warehouses=warehouses, can_stock=can_stock, can_alert=can_alert, can_write=can_write,
                  stock_states=STOCK_STATES)


WARRANTY = {'con': 'Còn bảo hành', 'sap': 'Sắp hết bảo hành', 'het': 'Hết bảo hành', 'khong': 'Chưa có bảo hành'}
SOON_DAYS = 30


def devices_page(request, session, user, q='', tt='', bh='', o='', trang='', so='', can_write=False):
    from .. import equipment
    from ..equipment_custody import LOCATIONS
    q, tt = q[:200], tt if tt in ('ngung', 'tat-ca') else ''
    bh, o = (bh if bh in WARRANTY else ''), (o if o in LOCATIONS or o == 'CHUA_GAN' else '')
    today = _today()

    def warranty(e):
        if not e.warranty_until:
            return 'khong'
        return 'het' if e.warranty_until < today else 'sap' if (e.warranty_until - today).days <= SOON_DAYS else 'con'

    def place(e):
        # Máy chưa gắn khách thì không có "Tại khách" (cột mặc định là TAI_KHACH).
        loc = e.custody_location or 'TAI_KHACH'
        return '' if loc == 'TAI_KHACH' and not e.customer_code else loc
    found = equipment.search(session, q, limit=None)
    counts = dict(dung=sum(1 for e in found if e.active), ngung=sum(1 for e in found if not e.active))
    rows = [e for e in found if (tt == 'tat-ca' or e.active == (tt != 'ngung')) and (not bh or warranty(e) == bh or (bh == 'con' and warranty(e) == 'sap')) and (not o or place(e) == ('' if o == 'CHUA_GAN' else o))]
    query = dict(q=q, tt=tt, bh=bh, o=o)
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/thiet-bi', query)
    serials = [e.serial for e in shown]
    tickets = {s: dict(n=n, last=at) for s, n, at in session.execute(select(ServiceTicket.serial, func.count(), func.max(ServiceTicket.created_at))
               .where(ServiceTicket.serial.in_(serials), ServiceTicket.status != 'DA_HUY').group_by(ServiceTicket.serial))} if serials else {}
    products = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_({e.product_code for e in shown}))).all()) if shown else {}
    customers = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_({e.customer_code for e in shown if e.customer_code}))).all()) if shown else {}
    from ..models import CustomerSite, User
    sites = dict(session.execute(select(CustomerSite.id, CustomerSite.name).where(CustomerSite.id.in_({e.site_id for e in shown if e.site_id}))).all()) if shown else {}
    holders = dict(session.execute(select(User.id, User.full_name).where(User.id.in_({e.custody_holder_id for e in shown if e.custody_holder_id}))).all()) if shown else {}
    held_tickets = dict(session.execute(select(ServiceTicket.id, ServiceTicket.number).where(ServiceTicket.id.in_({e.custody_ticket_id for e in shown if e.custody_ticket_id}))).all()) if shown else {}
    # Ô số đếm thiết bị đang dùng toàn hệ thống; bấm là lọc.
    alls = [e for e in (found if not q else equipment.search(session, '', limit=None)) if e.active]
    n_soon = sum(1 for e in alls if warranty(e) == 'sap')
    tiles = [dict(label='Còn bảo hành', n=sum(1 for e in alls if warranty(e) in ('con', 'sap')), sub='Máy bán ra và máy sửa có bảo hành', href='/thiet-bi?bh=con', on=bh == 'con', tone='xong'),
             dict(label='Sắp hết bảo hành', n=n_soon, sub=f'Trong {SOON_DAYS} ngày tới', href='/thiet-bi?bh=sap', on=bh == 'sap', tone='cho' if n_soon else ''),
             dict(label='Đang ở công ty', n=sum(1 for e in alls if place(e) in ('THO_GIU', 'KHO_KY_THUAT', 'DOI_TAC')), sub='Thợ giữ, kho kỹ thuật, đối tác', href='/may-dang-giu', on=False),
             dict(label='Chưa gắn khách', n=sum(1 for e in alls if not e.customer_code), sub='Cần bổ sung khách', href='/thiet-bi?o=CHUA_GAN', on=o == 'CHUA_GAN')]
    tabs = [dict(label='Đang dùng', n=counts['dung'], href=link(tt=''), on=not tt), dict(label='Ngừng dùng', n=counts['ngung'], href=link(tt='ngung'), on=tt == 'ngung'),
            dict(label='Tất cả', n=len(found), href=link(tt='tat-ca'), on=tt == 'tat-ca')]
    chips = [dict(label=label, href=link(**{key: ''})) for key, label in (('bh', WARRANTY.get(bh, '')), ('o', 'Chưa gắn khách' if o == 'CHUA_GAN' else 'Đang ở: ' + LOCATIONS.get(o, ''))) if query.get(key)]
    return render(request, 'thiet_bi_ds.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, q=q, tt=tt, bh=bh, o=o,
                  warranty=warranty, place=place, warranties=WARRANTY, locations=LOCATIONS, tickets=tickets, product_names=products,
                  customer_names=customers, sites=sites, holders=holders, held_tickets=held_tickets, today=today, can_device_write=can_write)

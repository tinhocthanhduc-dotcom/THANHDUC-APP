"""Trợ lý Zalo đợt 1 (TUẤN duyệt 06/11/2026): tra cứu nhanh và báo cáo theo câu hỏi. CHỈ ĐỌC.

- Nợ khách: chỉ kế toán, giám đốc (Q1). Số lấy từ finance_reports.customer_docs, cùng nguồn màn Công nợ khách hàng.
- Doanh thu, đã thu: kế toán, giám đốc thấy cả công ty (cùng công thức Tổng quan); kinh doanh chỉ doanh số đơn của mình (Q2).
- Trạng thái đơn: ai mở được Đơn bán trên app. Trạng thái phiếu, phiếu quá hẹn: điều phối, lễ tân, kế toán, kho, giám đốc;
  thợ chỉ phiếu đang giao cho mình.
- Việc của thợ: điều phối, giám đốc hỏi được mọi thợ; thợ chỉ hỏi chính mình.
- Bảo hành theo serial, mã khách hay đặt: mọi nhân viên đã gắn.
- Không bao giờ đưa giá vốn, lãi, lương, hoa hồng vào kết quả. Mọi lần gọi đều vào nhật ký Trợ lý Zalo như công cụ cũ.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from . import finance_reports, sales_reports, service_tickets
from .equipment import normalize_serial
from .models import Customer, Equipment, Product, SalesOrder, SalesReceipt, ServiceStep, ServiceTicket, User
from .security import has_role
from .text import fold

TZ = finance_reports.TZ
MAX_ROWS = 10
ORDER_STATUSES = {'NHAP': 'Nháp', 'DA_CHOT': 'Đã chốt', 'GIAO_MOT_PHAN': 'Giao một phần', 'DA_GIAO': 'Đã giao', 'DA_HUY': 'Đã hủy'}
TICKET_STATUSES = {'MOI': 'Mới, chưa giao thợ', 'DA_GIAO': 'Đã giao thợ', 'DA_NHAN': 'Thợ đã nhận', 'DA_HEN': 'Đã hẹn khách',
                   'DANG_LAM': 'Đang làm', 'CHO_LINH_KIEN': 'Chờ linh kiện', 'TAM_DUNG': 'Tạm dừng', 'HOAN_THANH': 'Hoàn thành',
                   'DA_DONG': 'Đã đóng', 'KHONG_LAM_DUOC': 'Không làm được', 'DA_HUY': 'Đã hủy'}
KINDS = {'SUA_TAI_CHO': 'Sửa tại chỗ', 'MANG_VE': 'Mang về xưởng', 'BAO_HANH': 'Bảo hành', 'MUC_DINH_KY': 'Mực định kỳ'}
PERIODS = {'hom_nay': 'hôm nay', 'hom_qua': 'hôm qua', 'tuan_nay': 'tuần này', 'thang_nay': 'tháng này', 'thang_truoc': 'tháng trước'}


def _now():
    return datetime.now(TZ)


def _url(path: str) -> str:
    """Link đầy đủ để bấm từ Zalo, lấy tên miền từ địa chỉ webhook đã cấu hình."""
    from .config import get_settings
    base = get_settings().zalo_webhook_url.split('/zalo/')[0].rstrip('/')
    return base + path


def _d(value) -> str:
    return value.strftime('%d/%m/%Y') if value else ''


def _dt(value) -> str:
    return value.astimezone(TZ).strftime('%H:%M %d/%m/%Y') if value else ''


def rights(user: User) -> dict:
    """Quyền tra cứu, theo đúng vai trò tài khoản app (giám đốc có mọi quyền)."""
    office = has_role(user, 'dispatcher', 'reception', 'finance', 'warehouse')
    return dict(debt=has_role(user, 'finance'), revenue_all=has_role(user, 'finance'),
                revenue_own=has_role(user, 'sales') and not has_role(user, 'finance'),
                orders=has_role(user, 'sales', 'warehouse', 'finance'), tickets_all=office,
                tickets_own=not office and 'technician' in user.roles, all_technicians=has_role(user, 'dispatcher'))


def labels(r: dict) -> list[str]:
    out = ['bảo hành theo serial', 'mã khách hay đặt']
    if r['orders'] or r['tickets_all'] or r['tickets_own']:
        out.append('trạng thái ' + ('đơn, phiếu' if r['orders'] and (r['tickets_all'] or r['tickets_own']) else 'đơn' if r['orders'] else 'phiếu'))
    if r['tickets_all'] or r['tickets_own']:
        out.append('phiếu quá hẹn')
    out.append('việc của thợ' if r['all_technicians'] else 'phiếu của tôi' if r['tickets_own'] else '')
    if r['debt']:
        out.append('nợ khách')
    if r['revenue_all']:
        out.append('doanh thu, đã thu')
    elif r['revenue_own']:
        out.append('doanh số của tôi')
    return [x for x in out if x]


# ---------- Công cụ ----------

def tool_debt(session: Session, user: User, customer_code: str) -> dict:
    customer = session.get(Customer, str(customer_code or '').strip())
    if customer is None:
        return dict(loi='Không có mã khách này. Tìm bằng tim_khach trước.')
    docs = finance_reports.customer_docs(session, user, customer.code)
    total = sum(int(d['remaining']) for d in docs)
    pending = sum(int(d['pending']) for d in docs)
    kinds = {'DB': 'Đơn bán', 'DV': 'Phiếu dịch vụ', 'DK': 'Nợ đầu kỳ'}
    return dict(khach=customer.name, con_no_gom_vat=total, trong_do_dang_o_nhan_vien=pending, khach_chua_tra=total - pending,
                so_chung_tu=len(docs), hang_muc=[dict(so=d['number'], loai=kinds.get(d['kind'], d['kind']), con_no=int(d['remaining']),
                                                      so_ngay=int(d['days'] or 0)) for d in docs[:8]],
                link=_url(f'/khach-hang/{customer.code}'))


def _order_row(o: SalesOrder) -> dict:
    return dict(so=o.number, loai='Đơn bán', trang_thai=ORDER_STATUSES.get(o.status, o.status), ngay_tao=_d(o.created_at.astimezone(TZ)),
                hen_giao=_d(o.promised_date), da_giao_luc=_dt(o.delivered_at), link=_url(f'/don-ban/{o.id}'))


def _ticket_row(t: ServiceTicket, names: dict) -> dict:
    return dict(so=t.number, loai=KINDS.get(t.kind or '', 'Phiếu dịch vụ'), trang_thai=TICKET_STATUSES.get(t.status, t.status),
                tho=names.get(t.assignee_id, 'chưa giao'), hen=_dt(t.appointment_at), han=_dt(t.due_at),
                loi=(t.symptom or '')[:120], ngay_nhan=_d(t.created_at.astimezone(TZ)))


def _names(session, ids) -> dict:
    ids = {i for i in ids if i}
    return dict(session.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all()) if ids else {}


def _tickets_scope(stmt, user, r):
    return stmt if r['tickets_all'] else stmt.where(ServiceTicket.assignee_id == user.id)


def tool_status(session: Session, user: User, r: dict, key: str) -> dict:
    key = str(key or '').strip()[:60]
    if not key:
        return dict(loi='Thiếu số đơn, số phiếu hoặc mã khách.')
    see_tickets = r['tickets_all'] or r['tickets_own']
    order = session.scalars(select(SalesOrder).where(func.upper(SalesOrder.number) == key.upper())).first()
    if order is not None:
        return dict(ket_qua=[_order_row(order)]) if r['orders'] else dict(loi='Anh/chị không có quyền xem đơn bán.')
    ticket = session.scalars(select(ServiceTicket).where(func.upper(ServiceTicket.number) == key.upper())).first()
    if ticket is not None:
        if not see_tickets or (not r['tickets_all'] and ticket.assignee_id != user.id):
            return dict(loi='Anh/chị không có quyền xem phiếu này.')
        return dict(ket_qua=[_ticket_row(ticket, _names(session, [ticket.assignee_id]))])
    customer = session.get(Customer, key)
    if customer is None:
        return dict(loi='Không thấy đơn, phiếu hay mã khách này. Tên khách thì tìm mã bằng tim_khach trước.')
    out = []
    if r['orders']:
        out += [_order_row(o) for o in session.scalars(select(SalesOrder).where(SalesOrder.customer_code == customer.code)
                                                       .order_by(SalesOrder.created_at.desc()).limit(5))]
    if see_tickets:
        rows = list(session.scalars(_tickets_scope(select(ServiceTicket).where(ServiceTicket.customer_code == customer.code), user, r)
                                    .order_by(ServiceTicket.created_at.desc()).limit(5)))
        names = _names(session, [t.assignee_id for t in rows])
        out += [_ticket_row(t, names) for t in rows]
    return dict(khach=customer.name, ket_qua=out, ghi_chu='' if out else 'Khách chưa có đơn hay phiếu nào anh/chị xem được.')


def tool_warranty(session: Session, serial: str) -> dict:
    serial = normalize_serial(str(serial or ''))[:64]
    if not serial:
        return dict(loi='Thiếu số serial.')
    from . import service_warranty
    row = session.get(Equipment, serial)
    if row is None:
        near = list(session.scalars(select(Equipment.serial).where(Equipment.serial.ilike(f'%{serial}%')).limit(5)))
        return dict(loi='Không có máy nào đúng serial này.', serial_gan_giong=near)
    today = _now().date()
    product = session.get(Product, row.product_code) if row.product_code else None
    customer = session.get(Customer, row.customer_code) if row.customer_code else None
    out = dict(serial=row.serial, may=product.name if product else row.model_name, khach=customer.name if customer else 'chưa gắn khách',
               bao_hanh_may_den=_d(row.warranty_until), con_bao_hanh_may=bool(row.warranty_until and row.warranty_until >= today))
    last = session.scalars(select(ServiceTicket).where(ServiceTicket.serial == row.serial, ServiceTicket.status.in_(('HOAN_THANH', 'DA_DONG')))
                           .order_by(ServiceTicket.priced_at.desc().nulls_last(), ServiceTicket.id.desc()).limit(1)).first()
    if last is not None:
        end = service_warranty.warranty_end(session, last)
        out.update(lan_sua_gan_nhat=last.number, bao_hanh_sua_den=_d(end), con_bao_hanh_sua=bool(end and end >= today))
    return out


def period(key: str, today: date | None = None) -> tuple[date, date, str]:
    today = today or _now().date()
    key = key if key in PERIODS else 'hom_nay'
    if key == 'hom_qua':
        start = end = today - timedelta(days=1)
    elif key == 'tuan_nay':
        start, end = today - timedelta(days=today.weekday()), today
    elif key == 'thang_nay':
        start, end = today.replace(day=1), today
    elif key == 'thang_truoc':
        end = today.replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
    else:
        start = end = today
    return start, end, PERIODS[key]


def tool_revenue(session: Session, user: User, r: dict, key: str) -> dict:
    start, end, name = period(str(key or ''))
    span = dict(ky=name, tu=_d(start), den=_d(end))
    if not r['revenue_all']:
        mine = sales_reports.totals(session, user, start, end)  # kinh doanh: chỉ đơn của mình
        return dict(**span, doanh_so_cua_toi_chua_vat=mine['subtotal'], so_don=mine['orders'])
    from .dashboard import _service_revenue
    sales = sales_reports.totals(session, user, start, end)
    service = int(session.scalar(select(func.coalesce(func.sum(ServiceTicket.subtotal), 0))
                                 .where(*_service_revenue(), *sales_reports._window(ServiceTicket.priced_at, start, end))) or 0)
    got = session.execute(select(func.coalesce(func.sum(SalesReceipt.amount), 0), func.count())
                          .where(SalesReceipt.status == 'DA_XAC_NHAN', SalesReceipt.received_at.between(start, end))).one()
    wait = session.execute(select(func.coalesce(func.sum(SalesReceipt.amount), 0), func.count())
                           .where(SalesReceipt.status == 'CHO_XAC_NHAN')).one()
    return dict(**span, doanh_thu_ban_hang_chua_vat=sales['subtotal'], doanh_thu_dich_vu_chua_vat=service,
                tong_doanh_thu_chua_vat=sales['subtotal'] + service, so_don_giao=sales['orders'],
                da_thu_da_xac_nhan=int(got[0]), so_lan_thu=int(got[1]),
                bao_thu_cho_xac_nhan_hien_tai=dict(so_tien=int(wait[0]), so_lan=int(wait[1])), link=_url('/tong-quan'))


def tool_overdue(session: Session, user: User, r: dict) -> dict:
    now = _now()
    where = (ServiceTicket.status.in_(service_tickets.OPEN), ServiceTicket.due_at < now)
    total = session.scalar(_tickets_scope(select(func.count()).select_from(ServiceTicket).where(*where), user, r)) or 0
    rows = list(session.scalars(_tickets_scope(select(ServiceTicket).where(*where), user, r).order_by(ServiceTicket.due_at).limit(MAX_ROWS)))
    names = _names(session, [t.assignee_id for t in rows])
    custs = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_({t.customer_code for t in rows}))).all()) if rows else {}
    return dict(tong_qua_hen=int(total), ket_qua=[dict(_ticket_row(t, names), khach=custs.get(t.customer_code, t.customer_code),
                                                    tre=f'{int((now - t.due_at).total_seconds() // 3600)} giờ') for t in rows],
                link=_url('/dieu-phoi?view=overdue' if r['all_technicians'] else '/tho' if r['tickets_own'] else '') if r['all_technicians'] or r['tickets_own'] else '')


def _technician(session, name: str):
    techs = [u for u in session.scalars(select(User).where(User.active.is_(True))) if 'technician' in u.roles]
    key = fold(str(name or '').strip())
    hit = [u for u in techs if key and key in fold(u.full_name)]
    return hit, techs


def tool_technician(session: Session, user: User, r: dict, name: str) -> dict:
    if r['all_technicians']:
        hit, techs = _technician(session, name)
        if len(hit) != 1:
            return dict(loi='Không rõ thợ nào.' if not hit else 'Có nhiều thợ trùng tên, hỏi lại tên đầy đủ.',
                        cac_tho=[u.full_name for u in (hit or techs)])
        tech = hit[0]
    elif r['tickets_own']:
        tech = user  # thợ chỉ hỏi được việc của chính mình
    else:
        return dict(loi='Anh/chị không có quyền xem việc của thợ.')
    today = _now().date()
    open_rows = list(session.scalars(select(ServiceTicket).where(ServiceTicket.assignee_id == tech.id, ServiceTicket.status.in_(service_tickets.OPEN))
                                     .order_by(ServiceTicket.appointment_at.asc().nulls_last(), ServiceTicket.due_at.asc().nulls_last(),
                                               ServiceTicket.id).limit(MAX_ROWS)))
    open_total = session.scalar(select(func.count()).select_from(ServiceTicket).where(
        ServiceTicket.assignee_id == tech.id, ServiceTicket.status.in_(service_tickets.OPEN))) or 0
    done = list(session.scalars(select(ServiceTicket.number).join(ServiceStep, ServiceStep.ticket_id == ServiceTicket.id).where(
        ServiceStep.action == 'transition', ServiceStep.to_status == 'HOAN_THANH', ServiceTicket.assignee_id == tech.id,
        ServiceTicket.status.in_(('HOAN_THANH', 'DA_DONG')), *sales_reports._window(ServiceStep.at, today, today)).distinct()))
    custs = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_({t.customer_code for t in open_rows}))).all()) if open_rows else {}
    names = {tech.id: tech.full_name}
    return dict(tho=tech.full_name, hoan_thanh_hom_nay=len(done), phieu_hoan_thanh_hom_nay=done[:MAX_ROWS],
                dang_mo=int(open_total), phieu_dang_mo_theo_thu_tu=[dict(_ticket_row(t, names), khach=custs.get(t.customer_code, ''))
                                                                     for t in open_rows])


# ---------- Định nghĩa cho AI ----------

def _fn(name, desc, props, required):
    return dict(type='function', function=dict(name=name, description=desc,
                                               parameters=dict(type='object', properties=props, required=required, additionalProperties=False)))


T_DEBT = _fn('no_khach', 'Công nợ hiện tại của một khách (đã gồm VAT): tổng còn nợ, phần đang ở nhân viên, từng chứng từ còn nợ.',
             dict(ma_khach=dict(type='string', description='Mã KTD lấy từ tim_khach')), ['ma_khach'])
T_STATUS = _fn('trang_thai', 'Trạng thái đơn bán hoặc phiếu dịch vụ theo số (DH-..., DV-...), hoặc 5 đơn và 5 phiếu gần nhất của một mã khách.',
               dict(so_hoac_ma_khach=dict(type='string')), ['so_hoac_ma_khach'])
T_WARRANTY = _fn('bao_hanh', 'Tra máy theo số serial: máy gì, của khách nào, bảo hành máy và bảo hành lần sửa gần nhất còn hạn không.',
                 dict(serial=dict(type='string')), ['serial'])
T_REVENUE = _fn('doanh_thu', 'Doanh thu (chưa VAT) và tiền đã thu trong một kỳ. Kinh doanh chỉ nhận doanh số đơn của mình.',
                dict(ky=dict(type='string', enum=list(PERIODS))), ['ky'])
T_OVERDUE = _fn('phieu_qua_hen', 'Các phiếu dịch vụ đang mở đã quá hạn xử lý, trễ nhất trước (tối đa 10).', {}, [])
T_TECH = _fn('viec_tho', 'Việc của một thợ: số phiếu hoàn thành hôm nay, phiếu đang mở theo thứ tự hẹn (phiếu đầu là phiếu tiếp theo).',
             dict(ten_tho=dict(type='string', description='Tên thợ. Thợ tự hỏi việc của mình thì để trống.')), ['ten_tho'])


def build(session: Session, user: User) -> tuple[list, dict]:
    """Công cụ đợt 1 theo quyền người hỏi. Công cụ không có quyền thì AI không thấy."""
    r = rights(user)
    tools = [T_WARRANTY]
    handlers = dict(bao_hanh=lambda a: tool_warranty(session, a.get('serial')))
    if r['orders'] or r['tickets_all'] or r['tickets_own']:
        tools.append(T_STATUS)
        handlers['trang_thai'] = lambda a: tool_status(session, user, r, a.get('so_hoac_ma_khach'))
    if r['tickets_all'] or r['tickets_own']:
        tools.append(T_OVERDUE)
        handlers['phieu_qua_hen'] = lambda a: tool_overdue(session, user, r)
    if r['all_technicians'] or r['tickets_own']:
        tools.append(T_TECH)
        handlers['viec_tho'] = lambda a: tool_technician(session, user, r, a.get('ten_tho'))
    if r['debt']:
        tools.append(T_DEBT)
        handlers['no_khach'] = lambda a: tool_debt(session, user, a.get('ma_khach'))
    if r['revenue_all'] or r['revenue_own']:
        tools.append(T_REVENUE)
        handlers['doanh_thu'] = lambda a: tool_revenue(session, user, r, a.get('ky'))
    return tools, handlers

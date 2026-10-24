"""U2: chỉ đọc nhóm việc và dẫn tới chứng từ có sẵn."""
from fastapi import Request

from .. import payroll, attendance, alerts, my_work, quotes, service_tickets, work_watch
from ..security import has_role
from ..errors import BusinessError
from . import render, nav
from .services import STATUSES as SERVICE_STATUSES
from .orders import STATUSES as ORDER_STATUSES
from .quotes import STATUSES as QUOTE_STATUSES
from .purchases import STATUSES as PURCHASE_STATUSES
from .receipts import STATUSES as RECEIPT_STATUSES

LABELS = dict(supplier_debt={'CON_NO':'Còn nợ'},pending_supplier_return={'CHO_DUYET':'Chờ duyệt'},pending_disposition={'CHO_DUYET':'Chờ duyệt'},technician_return={'CHO_NHAN':'Chờ nhận','DA_NHAN':'Đã nhận','DA_HUY':'Đã hủy'},refund={'CHO_DUYET':'Chờ duyệt','DA_CHI':'Đã chi','TU_CHOI':'Từ chối'}, delivery=ORDER_STATUSES, service=SERVICE_STATUSES, order=ORDER_STATUSES, quote=QUOTE_STATUSES,
              purchase=PURCHASE_STATUSES, receipt=RECEIPT_STATUSES, stock={'SAP_HET':'Sắp hết'}, part={'CHO_CAP':'Chờ cấp'}, custody={'THO_GIU':'Thợ giữ','CHO_NHAN':'Chờ nhận'})
# Khuôn 1: nhãn nút hành động trên thẻ việc, theo nhóm. Chỉ là chữ; link vẫn do my_work quyết định.
VERBS = dict(service_workshop='Xử lý tại xưởng',supplier_debts_due='Trả tiền', parts_pending='Cấp linh kiện', delivery_handover='Bàn giao cho thợ', orders_waiting='Giao hàng', orders_pickup_late='Giao hàng',
    technician_returns='Nhận trả', purchases_waiting='Nhận hàng', machines_warehouse='Nhận máy', machines_pending='Nhận máy', machines_held='Xem máy',
    stock_low='Lập đơn mua', receipts_pending='Xác nhận', refunds_pending='Duyệt hoàn tiền', pending_destruction='Duyệt hủy',
    pending_supplier_returns='Duyệt trả NCC', receipts_rejected='Làm lại báo thu', quotes_open='Mở báo giá', quotes_accepted='Tạo đơn', orders_draft='Mở đơn',
    orders_unreported='Báo thu', orders_deposit='Thu cọc', service_return_ready='Hẹn trả máy', service_unassigned='Giao thợ', service_close='Xem', service_unreported='Báo thu')
# Biểu tượng đầu khung nhóm việc: theo phân hệ của nhóm (chỉ trang trí).
ICON_BY = (('supplier_debt','Tài chính'),('receipt','Tài chính'),('refund','Tài chính'),('quote','Bán hàng'),('order','Bán hàng'),('delivery','Bán hàng'),
    ('service','Dịch vụ'),('machine','Dịch vụ'),('purchase','Mua hàng'))
# Dòng nhắc dưới tên nhóm; nhóm không có ở đây thì để trống.
HINTS = dict(supplier_debts_due='NCC còn nợ, cũ nhất lên đầu', receipts_pending='Kiểm tra tiền đã về rồi xác nhận', parts_pending='Thợ đang chờ linh kiện', quotes_open='Gọi lại khách trước khi hết hạn', quotes_accepted='Khách đã chốt, tạo đơn để giữ hàng',
    orders_unreported='Thu tiền và báo thu', orders_waiting='Soạn hàng và giao', orders_draft='Hoàn tất rồi xác nhận đơn', service_unassigned='Chọn thợ cho phiếu', service_return_ready='Chọn thợ và giờ trả máy',
    purchases_waiting='Nhận hàng khi NCC giao', receipts_rejected='Sửa và gửi lại báo thu', stock_low='Cân nhắc đặt thêm hàng')
# Đợt 6: gom nhóm việc theo phân hệ, mỗi phân hệ một khung có vạch màu riêng (chỉ trình bày).
AREAS = (('sv', 'Dịch vụ', 'Phiếu sửa máy, giao thợ, máy khách'), ('sa', 'Bán hàng', 'Báo giá, đơn bán, tiền khách'),
    ('kh', 'Kho · mua hàng', 'Giao hàng, nhận hàng, linh kiện, tồn thấp'), ('tc', 'Tài chính', 'Xác nhận tiền, hoàn tiền, trả NCC'))
AREA_OF = dict(supplier_debts_due='tc', refunds_pending='tc', receipts_pending='tc',
    quotes_open='sa', quotes_accepted='sa', orders_cancel_requested='sa', orders_draft='sa', orders_unreported='sa', orders_deposit='sa',
    receipts_rejected='sa', orders_pickup_late='sa', technician_deliveries='sa',
    delivery_handover='kh', parts_pending='kh', technician_returns='kh', purchases_waiting='kh', stock_low='kh', machines_warehouse='kh',
    pending_destruction='kh', pending_supplier_returns='kh')


def _area(user, key):
    if key == 'orders_waiting':return 'kh' if has_role(user, 'warehouse') else 'sa'
    return AREA_OF.get(key, 'sv' if key.startswith(('service_', 'machines_')) else 'kh')


NOTICES = {'done':'Đã hoàn tất bước này.', 'confirmed':'Đã xác nhận báo thu.', 'rejected':'Đã từ chối báo thu.'}


def page(request: Request, user, session):
    key = request.query_params.get('nhom', '')
    try:
        number = int(request.query_params.get('trang', '1'))
        if number < 1:raise ValueError
    except ValueError:
        raise BusinessError('BAD_PAGE', 'Số trang chưa hợp lệ.')
    groups = [my_work.filtered(session, user, key, number)] if key else my_work.collect(session, user)
    # Việc đang chờ luôn ở đầu; không bắt người dùng lướt qua các nhóm rỗng.
    groups.sort(key=lambda group: (group['count'] == 0, not any(row.get('overdue') for row in group['rows'])))
    icons = {g['key']: nav.NAV_ICONS[next((label for part, label in ICON_BY if part in g['key']), 'Kho')] for g in groups}
    area_key = request.query_params.get('khu', '')
    busy = [g for g in groups if g['count']]
    # Việc quá hạn gom lên khung "Làm trước"; nhóm bên dưới không lặp lại dòng đó.
    first = [] if key else [dict(row, group_title=g['title'], verb=row.get('next_label') or VERBS.get(g['key'], 'Mở'))
        for g in busy for row in g['rows'] if row.get('overdue')]
    areas = []
    for code, name, hint in AREAS:
        mine = [g for g in busy if _area(user, g['key']) == code]
        if mine or any(_area(user, g['key']) == code for g in groups):
            areas.append(dict(key=code, name=name, hint=hint, groups=mine, count=sum(g['count'] for g in mine)))
    if area_key not in {a['key'] for a in areas} | {'late'}:area_key = ''
    total = sum(g['count'] for g in busy)
    tiles = [dict(label='Tất cả', n=total, href='/viec-cua-toi', on=not area_key),
        dict(label='Quá hạn · làm trước', n=len(first), href='/viec-cua-toi?khu=late', on=area_key == 'late', tone='huy' if first else '')] + [
        dict(label=a['name'], n=a['count'], href='/viec-cua-toi?khu=' + a['key'], on=area_key == a['key'], tone='area-' + a['key']) for a in areas]
    shortcuts = [(label, href) for label, href, roles in (
        ('Tạo báo giá','/bao-gia/moi',quotes.WRITERS),
        ('Nhận đơn dịch vụ','/dich-vu/moi',service_tickets.CREATORS),
        ('Lập đơn mua','/don-mua/moi',('warehouse',))) if has_role(user,*roles) and nav.allowed(user,href)]
    return render(request, 'viec_cua_toi.jinja', groups=groups, areas=areas, first=first, tiles=tiles, area_key=area_key, total=total, labels=LABELS, verbs=VERBS, icons=icons, hints=HINTS, shortcuts=shortcuts,
        advance_pending=payroll.pending_count(session,user),attendance_pending=attendance.pending_count(session,user) if has_role(user,'director') else 0,pending_notices=alerts.pending_notices(session,user),selected_group=key, page=number,
        watch=None if key else dict(url='/viec-cua-toi/moi', stamp=work_watch.my_queue(session, user, groups)), notice=NOTICES.get(request.query_params.get('xong', ''), ''))

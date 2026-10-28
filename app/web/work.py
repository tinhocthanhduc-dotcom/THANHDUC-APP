"""U2: chỉ đọc nhóm việc và dẫn tới chứng từ có sẵn."""
from urllib.parse import urlencode

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

LABELS = dict(production={'MOI':'Mới','DANG_LAM':'Đang làm','HOAN_THANH':'Chờ nhập kho','DA_NHAP':'Đã nhập kho','DA_HUY':'Đã hủy'},supplier_debt={'CON_NO':'Còn nợ'},pending_supplier_return={'CHO_DUYET':'Chờ duyệt'},pending_disposition={'CHO_DUYET':'Chờ duyệt'},stock_doc={'NHAP':'Chờ duyệt'},technician_return={'CHO_NHAN':'Chờ nhận','DA_NHAN':'Đã nhận','DA_HUY':'Đã hủy'},refund={'CHO_DUYET':'Chờ duyệt','DA_CHI':'Đã chi','TU_CHOI':'Từ chối'}, delivery=ORDER_STATUSES, service=SERVICE_STATUSES, order=ORDER_STATUSES, quote=QUOTE_STATUSES,
              purchase=PURCHASE_STATUSES, receipt=RECEIPT_STATUSES, stock={'SAP_HET':'Sắp hết'}, part={'CHO_CAP':'Chờ cấp'}, custody={'THO_GIU':'Thợ giữ','CHO_NHAN':'Chờ nhận'})
# Khuôn 1: nhãn nút hành động trên thẻ việc, theo nhóm. Chỉ là chữ; link vẫn do my_work quyết định.
VERBS = dict(production_work='Làm hàng',production_receive='Nhận vào kho',service_workshop='Xử lý tại xưởng',supplier_debts_due='Trả tiền', parts_pending='Cấp linh kiện', delivery_handover='Bàn giao cho thợ', orders_waiting='Giao hàng', orders_pickup_late='Giao hàng',
    technician_returns='Nhận trả', purchases_waiting='Nhận hàng', machines_warehouse='Nhận máy', machines_pending='Nhận máy', machines_held='Xem máy',
    stock_low='Lập đơn mua', receipts_pending='Xác nhận', refunds_pending='Duyệt hoàn tiền', pending_destruction='Duyệt hủy', stock_counts_pending='Duyệt kiểm kê',
    pending_supplier_returns='Duyệt trả NCC', receipts_rejected='Làm lại báo thu', quotes_open='Mở báo giá', quotes_accepted='Tạo đơn', orders_draft='Mở đơn',
    orders_unreported='Báo thu', orders_deposit='Thu cọc', service_return_ready='Hẹn trả máy', service_unassigned='Giao thợ', service_close='Xem', service_unreported='Báo thu')
# Biểu tượng đầu khung nhóm việc: theo phân hệ của nhóm (chỉ trang trí).
ICON_BY = (('supplier_debt','Tài chính'),('receipt','Tài chính'),('refund','Tài chính'),('quote','Bán hàng'),('order','Bán hàng'),('delivery','Bán hàng'),
    ('service','Dịch vụ'),('machine','Dịch vụ'),('purchase','Mua hàng'))
# Dòng nhắc dưới tên nhóm; nhóm không có ở đây thì để trống.
HINTS = dict(supplier_debts_due='NCC còn nợ, cũ nhất lên đầu', receipts_pending='Kiểm tra tiền đã về rồi xác nhận', parts_pending='Thợ đang chờ linh kiện', quotes_open='Gọi lại khách trước khi hết hạn', quotes_accepted='Khách đã chốt, tạo đơn để giữ hàng',
    orders_unreported='Thu tiền và báo thu', orders_waiting='Soạn hàng và giao', orders_draft='Hoàn tất rồi xác nhận đơn', service_unassigned='Chọn thợ cho phiếu', service_return_ready='Chọn thợ và giờ trả máy',
    purchases_waiting='Nhận hàng khi NCC giao', receipts_rejected='Sửa và gửi lại báo thu', stock_low='Cân nhắc đặt thêm hàng', stock_counts_pending='Xem số đếm rồi duyệt ghi sổ')
# Đợt 6: gom nhóm việc theo phân hệ, mỗi phân hệ một khung có vạch màu riêng (chỉ trình bày).
AREAS = (('sv', 'Dịch vụ', 'Phiếu sửa máy, giao thợ, máy khách'), ('sa', 'Bán hàng', 'Báo giá, đơn bán, tiền khách'),
    ('kh', 'Kho · mua hàng', 'Giao hàng, nhận hàng, linh kiện, tồn thấp'), ('tc', 'Tài chính', 'Xác nhận tiền, hoàn tiền, trả NCC'))
AREA_OF = dict(supplier_debts_due='tc', refunds_pending='tc', receipts_pending='tc',
    quotes_open='sa', quotes_accepted='sa', orders_cancel_requested='sa', orders_draft='sa', orders_unreported='sa', orders_deposit='sa',
    receipts_rejected='sa', orders_pickup_late='sa', technician_deliveries='sa',
    delivery_handover='kh', parts_pending='kh', technician_returns='kh', purchases_waiting='kh', stock_low='kh', machines_warehouse='kh',
    pending_destruction='kh', pending_supplier_returns='kh', stock_counts_pending='kh')


def _area(user, key):
    if key == 'orders_waiting':return 'kh' if has_role(user, 'warehouse') else 'sa'
    return AREA_OF.get(key, 'sv' if key.startswith(('service_', 'machines_')) else 'kh')


NOTICES = {'done':'Đã hoàn tất bước này.', 'confirmed':'Đã xác nhận báo thu.', 'rejected':'Đã từ chối báo thu.'}


# Mẫu ưu tiên (2026-10-25): ba khung theo độ gấp thay cho khung theo phân hệ.
# Làm ngay: việc có khách/thợ đang chờ hoặc có hẹn; mọi dòng quá hạn của nhóm khác cũng kéo lên đây.
URGENT = ('service_appointment_overdue', 'service_today', 'service_unassigned', 'service_assigned', 'service_return_ready',
    'orders_pickup_late', 'orders_cancel_requested', 'technician_deliveries', 'delivery_handover', 'orders_waiting',
    'parts_pending', 'machines_pending', 'machines_warehouse')
# Tiền · làm sau: báo thu, xác nhận thu, trả NCC; gập sẵn khi còn việc gấp.
MONEY = ('orders_unreported', 'service_unreported', 'receipts_pending', 'supplier_debts_due')
WHY = dict(service_appointment_overdue='Hẹn ngày trước', service_today='Hẹn hôm nay', service_unassigned='Chưa giao thợ',
    service_assigned='Chờ nhận việc', service_return_ready='Khách chờ máy', orders_pickup_late='Hẹn lấy quá ngày',
    orders_cancel_requested='Đề nghị hủy', technician_deliveries='Cần giao', delivery_handover='Bàn giao thợ',
    orders_waiting='Chờ giao', parts_pending='Thợ chờ linh kiện', machines_pending='Chờ nhận máy', machines_warehouse='Chờ nhận máy')
# Mua hàng chưa phải gấp (TUẤN 2026-10-26): dòng "quá hạn" của các nhóm này (vd. tồn bằng 0) không kéo lên Làm ngay,
# nhóm nằm cuối khung Trong ngày.
CALM = ('stock_low', 'purchases_waiting', 'pending_supplier_returns')
AREA_NAMES = {code: name for code, name, _ in AREAS}
BANDS = ('now', 'day', 'money')
SHOWN = 5
NOW_SHOWN = 15


def _band(key):
    return 'now' if key in URGENT else 'money' if key in MONEY else 'day'


def _when(row, key, now):
    """Chữ đậm đầu dòng Làm ngay: trễ bao lâu, hẹn mấy giờ, hoặc lý do gấp."""
    due, appt = row.get('due_at'), row.get('appointment_at')
    if row.get('overdue') and due:
        hours = int((now - due).total_seconds() // 3600)
        return 'Trễ %d giờ' % max(hours, 1) if hours < 24 else 'Trễ %d ngày' % (hours // 24)
    if appt and key != 'service_appointment_overdue':
        local = appt.astimezone(now.tzinfo)
        return 'Hẹn ' + local.strftime('%H:%M' if local.date() == now.date() else '%d/%m %H:%M')
    if row.get('overdue') and key not in WHY:return 'Quá hạn'
    return WHY.get(key, 'Quá hạn')


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
    view = request.query_params.get('xem', '')
    if area_key == 'late':area_key, view = '', 'now'
    known = {_area(user, g['key']) for g in groups}
    if area_key not in known:area_key = ''
    if view not in BANDS:view = ''
    busy = [g for g in groups if g['count']]
    total = sum(g['count'] for g in busy)
    shown = [g for g in busy if not area_key or _area(user, g['key']) == area_key]
    clock = my_work._now()
    hot = []
    for g in shown:
        urgent = g['key'] in URGENT
        for row in g['rows']:
            if urgent or (row.get('overdue') and g['key'] not in CALM):
                red = bool(row.get('overdue')) or g['key'] == 'service_appointment_overdue'
                hot.append(dict(row, group_key=g['key'], group_title=g['title'], verb=row.get('next_label') or VERBS.get(g['key'], 'Mở'),
                    when=_when(row, g['key'], clock), red=red, area=_area(user, g['key']), area_name=AREA_NAMES[_area(user, g['key'])]))
    order = {k: i for i, k in enumerate(URGENT)}
    hot.sort(key=lambda r: (not r['red'], order.get(r['group_key'], -1), r.get('appointment_at') or r.get('due_at') or clock))
    # Nhóm gấp có nhiều hơn số dòng đang hiện thì có link Xem tất cả.
    shown_hot = hot[:NOW_SHOWN]
    now_more = [g for g in shown if g['key'] in URGENT and g['count'] > sum(1 for r in shown_hot if r['group_key'] == g['key'])]
    late_left = lambda g: 0 if g['key'] in CALM else sum(1 for r in g['rows'] if r.get('overdue'))
    day = [g for g in shown if _band(g['key']) == 'day' and g['count'] > late_left(g) and g['key'] not in CALM]
    # TUẤN 2026-10-27: nhóm mua hàng gập thành một dòng như phần tiền, nằm dưới Trong ngày.
    buy = [g for g in shown if g['key'] in CALM and g['count']]
    money = [g for g in shown if _band(g['key']) == 'money' and g['count'] > late_left(g)]
    counts = dict(now=sum(g['count'] for g in shown if g['key'] in URGENT) + sum(late_left(g) for g in shown if g['key'] not in URGENT),
        day=sum(g['count'] - late_left(g) for g in day), buy=sum(g['count'] for g in buy), money=sum(g['count'] - late_left(g) for g in money))
    link = lambda xem, khu: '/viec-cua-toi' + ('?' + urlencode({k: v for k, v in (('xem', xem), ('khu', khu)) if v}) if xem or khu else '')
    tiles = [dict(label=label, n=counts[band], sub=sub, href=link('' if view == band else band, area_key),
        on=view == band, tone='hot' if band == 'now' and counts['now'] else band)
        for band, label, sub in (('now', 'Làm ngay', 'Quá hạn, có hẹn, khách chờ'), ('day', 'Trong ngày', 'Cần làm, chưa trễ'),
            ('money', 'Tiền · làm sau', 'Báo thu, xác nhận, trả NCC'))]
    chips = [dict(label='Tất cả', href=link(view, ''), on=not area_key, key='')] + [
        dict(label=name, href=link(view, code), on=area_key == code, key=code)
        for code, name, _ in AREAS if code in known]
    money_open = view == 'money' or not (counts['now'] or counts['day'])
    shortcuts = [(label, href) for label, href, roles in (
        ('Tạo báo giá','/bao-gia/moi',quotes.WRITERS),
        ('Nhận đơn dịch vụ','/dich-vu/moi',service_tickets.CREATORS),
        ('Lập đơn mua','/don-mua/moi',('warehouse',))) if has_role(user,*roles) and nav.allowed(user,href)]
    return render(request, 'viec_cua_toi.jinja', groups=groups, hot=shown_hot, now_more=now_more, day=day, buy=buy, money=money, counts=counts, view=view,
        chips=chips, money_open=money_open, calm=CALM, tiles=tiles, area_key=area_key, total=total, labels=LABELS, verbs=VERBS, icons=icons, hints=HINTS, shortcuts=shortcuts,
        advance_pending=payroll.pending_count(session,user),attendance_pending=attendance.pending_count(session,user) if has_role(user,'director') else 0,pending_notices=alerts.pending_notices(session,user),selected_group=key, page=number,
        watch=None if key else dict(url='/viec-cua-toi/moi', stamp=work_watch.my_queue(session, user, groups)), notice=NOTICES.get(request.query_params.get('xong', ''), ''))

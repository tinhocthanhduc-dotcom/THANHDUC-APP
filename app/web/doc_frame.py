"""Đợt 1b: một khung chi tiết cho báo giá và đơn bán (chỉ đọc), dòng tiến độ và thông báo có số chứng từ.

Chỉ dựng chữ cho giao diện từ số liệu đã có; không tính lại tiền hay tồn.
"""


def money(value):
    from . import money as fmt
    return fmt(value)


def _qty(value):
    from . import quantity
    return quantity(value)

ORDER_ACTION_SAVED = ('promise', 'options', 'reserve', 'release', 'close-remaining', 'void-delivery')


def _kho(order):
    codes = sorted({l.warehouse_code for l in order.lines if l.kind == 'HANG' and l.warehouse_code})
    return ', '.join(codes)


def _receiver(order):
    if order.fulfillment == 'THO_GIAO':
        return 'thợ giao'
    kho = _kho(order)
    return 'thủ kho ' + kho if kho else 'kinh doanh'


def line_progress(order, reservations, delivery_state):
    """Nhãn tiến độ cho từng dòng: dict line_no -> [(chữ, tông)]; tông theo nhãn Đợt 0."""
    if order.status not in ('DA_CHOT', 'GIAO_MOT_PHAN', 'DA_GIAO'):
        return {}
    out = {}
    for line in order.lines:
        state = delivery_state.get(line.line_no) or {}
        delivered, remaining = state.get('delivered', 0), state.get('remaining', 0)
        wanted = line.qty - (line.closed_qty or 0)
        chips = []
        if remaining <= 0:
            chips.append((f'Đã giao {_qty(delivered)}/{_qty(wanted)}', 'xong'))
        else:
            if delivered:
                chips.append((f'Giao {_qty(delivered)}/{_qty(wanted)}', 'lam'))
            held = reservations.get(line.line_no) if line.kind == 'HANG' else None
            if held is not None:
                if held['missing'] > 0:
                    chips.append((f'Thiếu {_qty(held["missing"])} ở {line.warehouse_code}', 'huy'))
                else:
                    chips.append((f'Đã giữ {_qty(held["held"])}/{_qty(remaining)}', 'lam'))
            elif not delivered:
                chips.append(('Chưa giao', 'nhap'))
        if line.closed_qty:
            chips.append((f'Đóng {_qty(line.closed_qty)}', 'nhap'))
        out[line.line_no] = chips
    return out


def order_next(order, collection, available):
    """Hộp Bước tiếp của đơn đã chốt trở đi: (ai, việc, dòng nhắc)."""
    balance = collection['balance'] if collection else {}
    pending = balance.get('pending', 0)
    if order.status == 'DA_HUY':
        return dict(who='không ai', title='Đơn đã hủy', warn=order.cancel_reason or '')
    if order.status in ('DA_CHOT', 'GIAO_MOT_PHAN'):
        count = sum(1 for l in order.lines if l.kind == 'HANG')
        when = f', hẹn {order.promised_date.strftime("%d/%m")}' if order.promised_date else ''
        warn = ''
        if order.payment_term == 'TRA_NGAY':
            due = max(0, (order.total or 0) - balance.get('paid', 0) - pending)
            if due:
                warn = f'Trả ngay: khách còn phải trả {money(due)} đ khi nhận hàng.'
        verb = 'Giao tiếp phần còn lại' if order.status == 'GIAO_MOT_PHAN' else f'Soạn và giao {count} dòng hàng' if count else 'Giao đơn'
        return dict(who=_receiver(order), title=verb + when, warn=warn)
    if available > 0:
        return dict(who='kinh doanh', title=f'Thu {money(available)} đ và báo thu', warn='')
    if pending > 0:
        return dict(who='kế toán', title=f'Kiểm tra tiền đã về rồi xác nhận {money(pending)} đ', warn='')
    return dict(who='không ai', title='Đã giao đủ, đã thu đủ', warn='')


def order_toast(order, collection, available, action=''):
    n = order.number
    if action in ORDER_ACTION_SAVED:
        return f'Đã lưu thay đổi {n}.'
    pending = (collection or {}).get('balance', {}).get('pending', 0) if collection else 0
    if order.status == 'NHAP':
        return f'Đã lưu đơn nháp {n} · chưa chốt, chưa giữ hàng.'
    if order.status == 'DA_HUY':
        return f'Đã hủy {n}.'
    if order.status == 'DA_CHOT':
        if action == 'deposit' or (action == '' and pending and order.payment_term == 'DAT_COC'):
            return f'Đã báo thu cọc {n} · chờ kế toán xác nhận.'
        return f'Đã chốt {n} · đã giữ hàng · chờ {_receiver(order)} giao.'
    if order.status == 'GIAO_MOT_PHAN':
        return f'Đã giao một phần {n} · phần còn lại chờ {_receiver(order)} giao.'
    if available > 0:
        return f'Đã giao đủ {n} · chờ báo thu {money(available)} đ.'
    if pending > 0:
        return f'Đã báo thu {n} · {money(pending)} đ chờ kế toán xác nhận.'
    return f'{n} đã giao đủ và thu đủ.'


QUOTE_NEXT = {
    'DA_GUI': ('kinh doanh', 'Chờ khách trả lời'),
    'CHAP_NHAN': ('kinh doanh', 'Tạo đơn để giữ hàng'),
    'TU_CHOI': ('không ai', 'Khách đã từ chối'),
    'DA_HUY': ('không ai', 'Báo giá đã hủy'),
}


def quote_next(quote, linked=None):
    who, title = QUOTE_NEXT.get(quote.status, ('kinh doanh', 'Kiểm tra giá rồi gửi khách'))
    if quote.status == 'DA_GUI' and quote.valid_until:
        title += f', hết hạn {quote.valid_until.strftime("%d/%m")}'
    if quote.status == 'CHAP_NHAN' and linked is not None:
        who, title = 'không ai', f'Đã có đơn {linked.number}'
    return dict(who=who, title=title, warn='')


def quote_toast(quote, linked=None):
    n = quote.number
    if quote.status == 'NHAP':
        return f'Đã lưu báo giá nháp {n}.'
    if quote.status == 'DA_GUI':
        until = f', hết hạn {quote.valid_until.strftime("%d/%m")}' if quote.valid_until else ''
        return f'Đã gửi {n} · chờ khách trả lời{until}.'
    if quote.status == 'CHAP_NHAN':
        return f'Khách đồng ý {n} · đã có đơn {linked.number}.' if linked else f'Khách đồng ý {n} · bấm Tạo đơn bán để giữ hàng.'
    if quote.status == 'TU_CHOI':
        return f'Đã ghi khách từ chối {n}.'
    return f'Đã hủy {n}.'

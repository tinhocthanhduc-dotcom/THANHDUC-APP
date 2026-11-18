"""U2: chọn nút điều hướng; POST vẫn do form/route cũ và lõi kiểm quyền."""
from .. import attachments, quotes, sales_orders, sales_receipts, service_tickets
from ..errors import BusinessError
from ..security import has_role


def permitted(check, user, *args):
    try:
        check(user, *args)
        return True
    except BusinessError:
        return False


def form(label, form_id, route, hint='', return_work=False):
    return dict(label=label, form=form_id, route=route, hint=hint, return_work=return_work)


def link(label, href, hint=''):
    return dict(label=label, href=href, route=href, hint=hint)


def block(primary, secondary=()):
    return dict(primary=primary, secondary=list(secondary)) if primary else None


def ticket_report_label(user, ticket):
    return 'Báo đã thu' if has_role(user,'technician') and ticket.assignee_id == user.id else 'Báo thu'


def quote(user, q, linked):
    if not permitted(quotes.check_writer, user):return None
    if q is None:return block(dict(form('Lưu và gửi', 'quote-edit', '/bao-gia/moi'), then='send'))
    base=f'/bao-gia/{q.id}'
    if q.status=='NHAP':return block(dict(form('Lưu và gửi', 'quote-edit', base), then='send'))
    # Đợt 5 (TUẤN duyệt 18/11/2026): một chạm ghi khách đồng ý và mở trang tạo đơn điền sẵn; "Chỉ ghi khách đồng ý" + "Tạo bản sửa" ở Việc khác.
    if q.status=='DA_GUI':return block(dict(form('Khách đồng ý → tạo đơn', 'quote-accept', base+'/accept'), then='order'),[form('Khách từ chối','quote-reject',base+'/reject')])
    if q.status=='CHAP_NHAN':
        return block(link('Xem đơn bán '+linked.number if linked else 'Tạo đơn bán',
            f'/don-ban/{linked.id}' if linked else f'/don-ban/tu-bao-gia/{q.id}'))
    return None


def order(user, o, available=0, deposit_remaining=0):
    # counter: màn đơn đổi nút chính thành Bán tại quầy khi đang chọn Tại quầy + Trả ngay.
    if o is None:
        return block(dict(form('Lưu và chốt', 'order-edit', '/don-ban/moi'), then='confirm', counter=True)) if permitted(sales_orders.check_writer,user) else None
    base=f'/don-ban/{o.id}'
    if o.status=='NHAP' and permitted(sales_orders.check_writer,user):
        return block(dict(form('Lưu và chốt', 'order-edit', base), then='confirm', counter=True))
    if o.status in ('DA_CHOT','GIAO_MOT_PHAN') and has_role(user,*sales_orders.DELIVERERS):
        if o.fulfillment=='THO_GIAO':return block(link('Giao cho thợ',f'/giao-don/{o.id}'))
        # Đợt 1b: một màn giao dùng chung cho kinh doanh và kho.
        return block(link('Giao tiếp' if o.status=='GIAO_MOT_PHAN' else 'Giao hàng', f'/kho/giao/{o.id}'))
    if o.status in ('GIAO_MOT_PHAN','DA_GIAO') and available>0 and permitted(sales_orders.check_writer,user):
        return block(form('Báo thu', 'receipt-report', base+'/bao-thu', 'Kiểm tra số tiền và cách thu trong mục Thu tiền trước khi báo.'))
    return None


def ready_message_links(session, user, t):
    """TUẤN duyệt 16/11/2026: nút phụ Soạn tin báo khách nhận máy; đã có tin chờ gửi thì mở lại, đã gửi thì ghi giờ gửi."""
    from .. import tin_gui_khach
    if not tin_gui_khach.can_write(user,'SUA_XONG') or t.priced_at is None:return []
    row=tin_gui_khach.ready_message(session,t.id)
    href=f'/tin-gui-khach/{row.id}' if row is not None else f'/tin-gui-khach/moi?loai=SUA_XONG&phieu={t.id}'
    if row is None:return [link('Soạn tin báo khách',href)]
    if row.status=='CHO':return [link('Mở tin báo khách (chờ gửi)',href)]
    when=row.decided_at.astimezone(tin_gui_khach.TZ).strftime('%H:%M %d/%m') if row.decided_at else ''
    return [link(f'Đã báo khách {when} · {tin_gui_khach.VIA.get(row.sent_via,"")}',href)]


def service(session, user, t):
    dispatcher=has_role(user,*service_tickets.DISPATCHERS)
    own='technician' in user.roles and t.assignee_id==user.id
    can_step=dispatcher or own
    base=f'/dich-vu/{t.id}'
    def transition(label,target):
        return form(label,'service-'+target,base+'/transition')
    if t.status=='MOI' and dispatcher:return block(form('Giao thợ','service-assign',base+'/assign','Chọn thợ trong form Giao thợ.'))
    # Điều phối vừa giao thợ: không đẩy nút Nhận việc của thợ lên làm nút chính; vẫn làm thay được trong Việc khác.
    if t.status=='DA_GIAO' and own:return block(transition('Nhận việc','DA_NHAN'))
    if t.status=='DA_NHAN' and can_step:return block(transition('Đã đến khách','DANG_LAM') if t.appointment_at else transition('Hẹn khách để bắt đầu','DA_HEN'))
    if t.status=='DA_HEN' and can_step:return block(transition('Đã đến khách','DANG_LAM'),
        [form('Không gặp khách','service-no-show',base+'/no-show','Ghi người liên hệ, đã gọi chưa và giờ hẹn mới.')])
    if t.status=='TAM_DUNG' and t.hold_reason=='CHO_SUA_XUONG':
        if not dispatcher:return None
        from ..service_workshop import warranty_free
        approved=t.quote_approved_at is not None or warranty_free(t)
        from ..service_tickets import can_quote
        if not approved and not can_quote(user):return None  # kho/điều phối không duyệt giá: kinh doanh hoặc giám đốc làm
        return block(form('Sửa xong' if approved else 'Khách đồng ý','workshop-repaired' if approved else 'workshop-approve',base+'/xuong/'+('sua-xong' if approved else 'dong-y')))  # TUẤN 15/11: Khách không sửa chỉ một chỗ (khung xưởng / tab Không sửa), không lặp ở Bước tiếp
    if t.status=='TAM_DUNG' and t.hold_reason=='SUA_XONG_CHO_TRA':
        if not dispatcher:return None
        return block(form('Giao thợ trả máy','service-schedule-return',base+'/hen-tra-may'),ready_message_links(session,user,t))
    if t.status in ('CHO_LINH_KIEN','TAM_DUNG') and can_step:return block(transition('Tiếp tục làm','DANG_LAM'))
    if t.status=='DANG_LAM' and can_step:
        secondary=[transition('Hẹn quay lại','DA_HEN'),form('Xin linh kiện từ kho','part-create',base+'/yeu-cau-linh-kien'),transition('Tạm dừng','TAM_DUNG')]
        if t.billing is None:
            return block(form('Lưu tiền','service-charges',base+'/tien/luu',
                'Chọn Có thu tiền hoặc Miễn phí; giá sẽ được khóa khi hoàn thành.'),secondary)
        if not attachments.has_visible(session,'service_ticket',t.id,'SAU'):
            return block(form('Tải ảnh sau sửa','photo-SAU-form',base+'/anh/tai','Chọn ảnh sau sửa trong mục Ảnh.'),secondary)
        return block(transition('Hoàn thành','HOAN_THANH'),secondary)
    # TUẤN 27/10/2026 (DV-26-00014): thợ chọn Chưa thu, phiếu tự đóng = công nợ khách, văn phòng thu.
    # Không đẩy thợ sang màn Báo đã thu; khách trả sau thì thợ vẫn mở được trong mục gập ở phiếu.
    if t.status=='DA_DONG' and own and not dispatcher:return None
    if t.status in ('HOAN_THANH','DA_DONG'):
        balance=sales_receipts.balance(t) if t.receivable else None
        available=balance['remaining']-balance['pending'] if balance else 0
        if t.priced_at and available>0 and permitted(sales_receipts.check_ticket_reporter,user,t):
            return block(form(ticket_report_label(user,t),'receipt-report',base+'/bao-thu','Kiểm tra số tiền và cách thu trong mục Thu tiền trước khi báo.'))
    return None


def receipt(user, r):
    if r.status!='CHO_XAC_NHAN' or not permitted(sales_receipts.check_finance,user):return None
    reject=form('Từ chối',f'receipt-reject-{r.id}',f'/bao-thu/{r.id}/reject', 'Từ chối phải nhập lý do.',return_work=True)
    if r.reported_by==user.id and not has_role(user,'director'):
        return block(reject)
    return block(form('Xác nhận',f'receipt-confirm-{r.id}',f'/bao-thu/{r.id}/confirm',return_work=True),[reject])


def delivery(session, user, order):
    from .. import technician_deliveries
    state=technician_deliveries.state(session,user,order)
    label='Đã đề nghị hủy · chờ bán hàng' if state['cancel_requested'] else 'Giao hàng / Ghi không giao được' if state['held'] else 'Chờ kho bàn giao'
    return block(link(label,f'/giao-don/{order.id}'))

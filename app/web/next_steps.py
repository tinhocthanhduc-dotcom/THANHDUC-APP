"""U2: chọn nút điều hướng; POST vẫn do form/route cũ và lõi kiểm quyền."""
from .. import attachments, quotes, sales_orders, sales_receipts, service_tickets
from ..errors import BusinessError
from ..security import has_role


def permitted(check, user):
    try:
        check(user)
        return True
    except BusinessError:
        return False


def form(label, form_id, route, hint='', return_work=False):
    return dict(label=label, form=form_id, route=route, hint=hint, return_work=return_work)


def link(label, href, hint=''):
    return dict(label=label, href=href, route=href, hint=hint)


def block(primary, secondary=()):
    return dict(primary=primary, secondary=list(secondary)) if primary else None


def quote(user, q, linked):
    if not permitted(quotes.check_writer, user):return None
    if q is None:return block(form('Lưu báo giá', 'quote-edit', '/bao-gia/moi'))
    base=f'/bao-gia/{q.id}'
    if q.status=='NHAP':return block(form('Gửi báo giá', 'quote-send', base+'/send'))
    if q.status=='DA_GUI':return block(form('Khách đồng ý', 'quote-accept', base+'/accept'))
    if q.status=='CHAP_NHAN':
        return block(link('Xem đơn bán' if linked else 'Tạo đơn bán từ báo giá',
            f'/don-ban/{linked.id}' if linked else f'/don-ban/tu-bao-gia/{q.id}'))
    return None


def order(user, o, available=0):
    if o is None:
        return block(form('Lưu đơn bán', 'order-edit', '/don-ban/moi')) if permitted(sales_orders.check_writer,user) else None
    base=f'/don-ban/{o.id}'
    if o.status=='NHAP' and permitted(sales_orders.check_writer,user):
        return block(form('Chốt đơn', 'order-confirm', base+'/confirm', 'Lưu thay đổi và chọn đủ kho trước khi chốt.'))
    if o.status=='DA_CHOT' and has_role(user,*sales_orders.DELIVERERS):
        return block(form('Giao hàng', 'order-deliver', base+'/deliver'))
    if o.status=='DA_GIAO' and available>0 and permitted(sales_orders.check_writer,user):
        return block(form('Báo thu', 'receipt-report', base+'/bao-thu', 'Kiểm tra số tiền và cách thu trong mục Thu tiền trước khi báo.'))
    return None


def service(session, user, t):
    dispatcher=has_role(user,*service_tickets.DISPATCHERS)
    own='technician' in user.roles and t.assignee_id==user.id
    can_step=dispatcher or own
    base=f'/dich-vu/{t.id}'
    def transition(label,target):
        return form(label,'service-'+target,base+'/transition')
    if t.status=='MOI' and dispatcher:return block(form('Giao thợ','service-assign',base+'/assign','Chọn thợ trong form Giao thợ.'))
    if t.status=='DA_GIAO' and can_step:return block(transition('Nhận việc','DA_NHAN'))
    if t.status=='DA_NHAN' and can_step:return block(transition('Hẹn khách để bắt đầu','DA_HEN'))
    if t.status=='DA_HEN' and can_step:return block(transition('Bắt đầu làm','DANG_LAM'))
    if t.status=='CHO_LINH_KIEN' and can_step:return block(transition('Làm tiếp','DANG_LAM'))
    if t.status=='DANG_LAM' and can_step:
        secondary=[transition('Chờ linh kiện','CHO_LINH_KIEN')]
        if t.billing is None:
            return block(form('Lưu tiền trước khi hoàn thành','service-charges',base+'/tien/luu',
                'Chọn Có thu tiền hoặc Miễn phí; giá sẽ được khóa khi hoàn thành.'),secondary)
        if not attachments.has_visible(session,'service_ticket',t.id,'SAU'):
            return block(form('Tải ảnh sau sửa','photo-SAU-form',base+'/anh/tai','Chọn ảnh sau sửa trong mục Ảnh.'),secondary)
        return block(transition('Hoàn thành','HOAN_THANH'),secondary)
    if t.status in ('HOAN_THANH','DA_DONG'):
        balance=sales_receipts.balance(t) if t.receivable else None
        available=balance['remaining']-balance['pending'] if balance else 0
        if t.priced_at and available>0 and permitted(sales_receipts.check_ticket_reporter,user):
            return block(form('Báo thu','receipt-report',base+'/bao-thu','Kiểm tra số tiền và cách thu trong mục Thu tiền trước khi báo.'))
        if dispatcher and t.status=='HOAN_THANH':return block(transition('Đóng phiếu','DA_DONG'))
    return None


def receipt(user, r):
    if r.status!='CHO_XAC_NHAN' or not permitted(sales_receipts.check_finance,user):return None
    reject=form('Từ chối',f'receipt-reject-{r.id}',f'/bao-thu/{r.id}/reject', 'Từ chối phải nhập lý do.',return_work=True)
    if r.reported_by==user.id and not has_role(user,'director'):
        return block(reject)
    return block(form('Xác nhận',f'receipt-confirm-{r.id}',f'/bao-thu/{r.id}/confirm',return_work=True),[reject])

"""U2: chỉ đọc nhóm việc và dẫn tới chứng từ có sẵn."""
from fastapi import Request

from .. import my_work, quotes, service_tickets
from ..security import has_role
from ..errors import BusinessError
from . import render, nav
from .services import STATUSES as SERVICE_STATUSES
from .orders import STATUSES as ORDER_STATUSES
from .quotes import STATUSES as QUOTE_STATUSES
from .purchases import STATUSES as PURCHASE_STATUSES
from .receipts import STATUSES as RECEIPT_STATUSES

LABELS = dict(service=SERVICE_STATUSES, order=ORDER_STATUSES, quote=QUOTE_STATUSES,
              purchase=PURCHASE_STATUSES, receipt=RECEIPT_STATUSES, stock={'SAP_HET':'Sắp hết'}, part={'CHO_CAP':'Chờ cấp'}, custody={'THO_GIU':'Thợ giữ','CHO_NHAN':'Chờ nhận'})
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
    groups.sort(key=lambda group: group['count'] == 0)
    shortcuts = [(label, href) for label, href, roles in (
        ('Tạo báo giá','/bao-gia/moi',quotes.WRITERS),
        ('Nhận đơn dịch vụ','/dich-vu/moi',service_tickets.CREATORS),
        ('Lập đơn mua','/don-mua/moi',('warehouse',))) if has_role(user,*roles) and nav.allowed(user,href)]
    return render(request, 'viec_cua_toi.jinja', groups=groups, labels=LABELS, shortcuts=shortcuts,
        selected_group=key, page=number, notice=NOTICES.get(request.query_params.get('xong', ''), ''))

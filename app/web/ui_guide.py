"""UI0: trang mẫu quy chuẩn giao diện, chỉ để xem và bấm thử, không ghi dữ liệu."""
from fastapi import APIRouter, Depends, Request

from ..errors import BusinessError
from ..models import User
from ..security import has_role
from . import render, web_user
from .money_input import parse_money

router = APIRouter()

SWATCHES = (('ok-solid', 'Xác nhận · Hoàn thành'), ('bad-solid', 'Hủy · Đóng · Hư'),
            ('info-solid', 'Gọi · Chuyển'), ('warn-solid', 'Cần chú ý'))


def director(user: User = Depends(web_user)):
    if not has_role(user, 'director'):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc xem được trang quy chuẩn giao diện.', status=403)
    return user


@router.get('/ui/quy-chuan')
def page(request: Request, thu: str = '', ten: str = '', so_tien: str = '', user: User = Depends(director)):
    # Form mẫu gửi bằng GET để không ghi gì; lỗi hiện đúng ô và giữ nguyên giá trị đã nhập.
    errors, ok = {}, False
    if thu:
        if not ten.strip():
            errors['ten'] = 'Hãy nhập tên khách.'
        try:
            parse_money(so_tien, 'Tiền thu khách', allow_zero=False)
        except BusinessError as exc:
            errors['so_tien'] = exc.message
        ok = not errors
    return render(request, 'ui_quy_chuan.jinja', swatches=SWATCHES, errors=errors, ok=ok,
                  d=dict(ten=ten[:200], so_tien=so_tien[:30]))

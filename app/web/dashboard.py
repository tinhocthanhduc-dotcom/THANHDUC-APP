"""R1: tổng quan và biểu đồ SVG, không có thao tác ghi."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from . import nav
from .. import payroll, attendance, alerts, dashboard as svc
from ..db import get_session
from ..models import User
from ..security import has_role
from . import render, web_user

from .services import STATUSES as SERVICE_STATUSES
from .orders import STATUSES as ORDER_STATUSES, FULFILLMENTS
from . import paging

router=APIRouter()
# Cảnh báo kho · mua hàng: không gấp, đứng sau việc cần để mắt (TUẤN 26/10/2026: mua hàng chưa phải gấp).
CALM_KINDS=('DUOI_TON_TOI_THIEU','CHAM_LUAN_CHUYEN','DON_MUA_TRE','CHO_XU_LY_QUA_HAN','CHO_NCC_QUA_HAN','THU_HOI_CHUA_NOP')
# Công nợ theo dõi: TUẤN 26/10/2026 "những việc này không cần ưu tiên" → gập gọn, cuối trang cùng kho · mua hàng.
DEBT_KINDS=('KHACH_VUOT_HAN_MUC','KHACH_NO_QUA_HAN','NCC_NO_QUA_HAN')


@router.get('/tong-quan')
def page(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    data=svc.summary(session,user)
    notices=alerts.collect(session,user,today=data['today'])
    groups={}
    for notice in notices:
        # R2: cảnh báo chỉ dẫn tới trang người xem mở được (kế toán xem phiếu dịch vụ qua bản chỉ đọc).
        notice=dict(notice,link=nav.visible_link(user,notice.get('link')))
        groups.setdefault(notice['kind'],[]).append(notice)
    def period(start,end):return urlencode({'from':start.isoformat(),'to':end.isoformat()})
    # SVG chỉ dùng tọa độ, số tiền vẫn là int ở tầng dữ liệu và nhãn.
    maximum=max((r['amount'] for r in data['revenue_daily']),default=0)
    bars=[dict(**r,x=40+n*28,height=(r['amount']*170/maximum if maximum else 0))
        for n,r in enumerate(data['revenue_daily'])]
    board=svc.day_board(session,user,today=data['today'])
    # Hai khung cảnh báo: việc cần để mắt (đỏ nhiều lên đầu) và kho · mua hàng (không gấp, cuối trang).
    calm={k:v for k,v in groups.items() if k in CALM_KINDS}
    debt={k:v for k,v in groups.items() if k in DEBT_KINDS}
    eye=dict(sorted(((k,v) for k,v in groups.items() if k not in CALM_KINDS+DEBT_KINDS),key=lambda kv:(-sum(1 for a in kv[1] if a['level']=='DO'),-notices.counts[kv[0]])))
    avg30=sum(r['amount'] for r in data['revenue_daily'])//max(1,len(data['revenue_daily']))
    advance_pending=payroll.pending_count(session,user);attendance_pending=attendance.pending_count(session,user) if has_role(user,'director') else 0
    # Bản 2 (TUẤN duyệt 28/10/2026): ai đang làm gì · việc cần làm tiếp · việc đang diễn ra; bảng thợ cũ gập xuống dưới.
    live=svc.live_board(session,user,today=data['today'],approvals=(attendance_pending,advance_pending),technicians=board.get('technicians'))
    orders=_day_orders(request,session,user,data['today'])
    return render(request,'tong_quan.jinja',s=data,orders=orders,order_statuses=ORDER_STATUSES,fulfillments=FULFILLMENTS,bars=bars,board=board,live=live,eye_groups=eye,calm_groups=calm,debt_groups=debt,avg30=avg30,
        service_labels=SERVICE_STATUSES,advance_pending=advance_pending,attendance_pending=attendance_pending,can_save=has_role(user,'director'),alert_groups=groups,alert_counts=notices.counts,alert_labels=alerts.LABELS,
        today_query=period(data['today'],data['today']),month_query=period(data['month_start'],data['today']),
        last_query=period(data['last_month_start'],data['last_month_end']))


ORDER_TABS=(('','Tất cả'),('ban','Đơn bán'),('dv','Dịch vụ'),('mo','Chưa xong'))


def _day_orders(request,session,user,today):
    """Đơn trong ngày (TUẤN 30/10/2026): đơn bán + phiếu dịch vụ, lọc theo tab, 25 dòng/trang, số đếm trên cả ngày."""
    rows=svc.day_orders(session,user,today=today)
    tab=request.query_params.get('don','')
    tab=tab if tab in dict(ORDER_TABS) else ''
    counts={'':len(rows),'ban':sum(r['type']=='ban' for r in rows),'dv':sum(r['type']=='dv' for r in rows),'mo':sum(not r['done'] for r in rows)}
    shown=[r for r in rows if not tab or (r['type']==tab if tab in ('ban','dv') else not r['done'])]
    q=request.query_params
    page=paging.build(q.get('trang-don'),q.get('so'),len(shown),{'don':tab},key='trang-don')
    base=page['href'];page['href']=lambda n:base(n)+'#don-trong-ngay'
    tabs=[dict(key=k,label=label,n=counts[k],href='/tong-quan'+('?'+urlencode({'don':k}) if k else '')+'#don-trong-ngay') for k,label in ORDER_TABS]
    return dict(rows=shown[page['offset']:page['offset']+page['size']],page=page,tabs=tabs,tab=tab,
        total=len(rows),open=counts['mo'],ban=counts['ban'],dv=counts['dv'])

"""R1: tổng quan và biểu đồ SVG, không có thao tác ghi."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import attendance, alerts, dashboard as svc
from ..db import get_session
from ..models import User
from ..security import has_role
from . import render, web_user

router=APIRouter()


@router.get('/tong-quan')
def page(request:Request,user:User=Depends(web_user),session:Session=Depends(get_session)):
    data=svc.summary(session,user)
    notices=alerts.collect(session,user,today=data['today'])
    groups={}
    for notice in notices:
        groups.setdefault(notice['kind'],[]).append(notice)
    def period(start,end):return urlencode({'from':start.isoformat(),'to':end.isoformat()})
    # SVG chỉ dùng tọa độ, số tiền vẫn là int ở tầng dữ liệu và nhãn.
    maximum=max((r['amount'] for r in data['revenue_daily']),default=0)
    bars=[dict(**r,x=40+n*28,height=(r['amount']*170/maximum if maximum else 0))
        for n,r in enumerate(data['revenue_daily'])]
    return render(request,'tong_quan.jinja',s=data,bars=bars,attendance_pending=attendance.pending_count(session,user) if has_role(user,'director') else 0,can_save=has_role(user,'director'),alert_groups=groups,alert_counts=notices.counts,alert_labels=alerts.LABELS,
        today_query=period(data['today'],data['today']),month_query=period(data['month_start'],data['today']),
        last_query=period(data['last_month_start'],data['last_month_end']))

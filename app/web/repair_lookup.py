"""Tra giá sửa cho thợ (TUẤN duyệt mẫu T1–T3, 15/11/2026): chọn model, chọn lỗi, bấm Tra giá.

Thay bảng giá kiểu máy tính trên màn thợ. Kết quả chỉ các gói sửa gắn với lỗi đã chọn (cùng luật Hỏi giá sửa:
quick_quotes.compute), giá đã gồm VAT, ô sáng là cấp hay gặp với lỗi. Model chưa có thì không đoán giá, gợi ý model gần giống.
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import price_book as pb
from .. import quick_quotes as qq
from ..db import get_session
from ..models import Equipment, Product, RepairGroup, RepairInspection, ServiceTicket, User
from ..service_tickets import CLOSED
from . import render, web_user

router = APIRouter(include_in_schema=False)
MAX_MINE = 6


def my_models(session: Session, user: User) -> list[dict]:
    """Máy thợ đang sửa (phiếu của mình chưa đóng): tên model từ kiểm tra máy, rồi thiết bị."""
    rows = session.execute(
        select(ServiceTicket.number, RepairInspection.model_name, Equipment.model_name, Product.name)
        .outerjoin(RepairInspection, RepairInspection.ticket_id == ServiceTicket.id)
        .outerjoin(Equipment, Equipment.serial == ServiceTicket.serial)
        .outerjoin(Product, Product.code == Equipment.product_code)
        .where(ServiceTicket.assignee_id == user.id, ServiceTicket.status.not_in(CLOSED))
        .order_by(ServiceTicket.id.desc()).limit(30)).all()
    out, seen = [], set()
    for number, inspected, eq_model, product in rows:
        name = ' '.join(str(inspected or eq_model or product or '').split())
        if not name:
            continue
        m = pb.find_model(session, name)
        label = m.name if m and m.active else name
        if label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append(dict(name=label, ticket=number))
        if len(out) >= MAX_MINE:
            break
    return out


def _matches(session, q: str) -> list:
    q = ' '.join(str(q or '').split())[:60]
    return [m for m in pb.models(session, q) if m.active][:8] if len(q) >= 2 else []


@router.get('/tra-gia')
def lookup(request: Request, model: str = '', q: str = '', xem: str = '', user: User = Depends(web_user),
           session: Session = Depends(get_session)):
    pb.require_view(user)
    ht = [str(v)[:200] for v in request.query_params.getlist('ht')][:qq.MAX_SYMPTOMS]
    model = ' '.join(str(model or '').split())[:160]
    m = pb.find_model(session, model) if model else None
    m = m if m and m.active else None
    ctx = dict(group=None, model=model, m=m, ht=ht, q=q[:60], mine=my_models(session, user), matches=_matches(session, q or (model if not m else '')),
               chips=[], result=None, error='', money=qq.money, level_keys=('1', '2', '3'), level_hint=qq.LEVEL_HINT)
    if m:
        ctx['group'] = session.get(RepairGroup, m.group_id)
        ctx['chips'] = qq.symptom_names(session, m)
        if xem:
            if not ht:
                ctx['error'] = 'Chọn ít nhất một lỗi của máy.'
            else:
                ctx['result'] = qq.compute(session, m.name, ht, 'CUA_HANG')
    ctx['group_names'] = {g.id: g.name for g in pb.groups(session, active_only=False)}
    return render(request, 'tra_gia_tho.jinja', **ctx)


@router.get('/tra-gia/tim')
def find(request: Request, q: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    pb.require_view(user)
    return render(request, 'tra_gia_tho_tim.jinja', matches=_matches(session, q), q=q[:60],
                  group_names={g.id: g.name for g in pb.groups(session, active_only=False)})

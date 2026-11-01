"""Cửa /ai cho Trợ lý AI trên máy chủ (TUẤN 01/11/2026). Chỉ các lệnh dưới đây, không lệnh nào đụng tiền hay kho."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import ai_agent as svc
from .. import ai_dang_web as web_svc
from ..db import get_session
from ..models import AiKey

router = APIRouter(prefix='/ai', tags=['Trợ lý AI'])


def _bearer(request: Request) -> str:
    raw = request.headers.get('authorization', '')
    return raw[7:].strip() if raw.lower().startswith('bearer ') else ''


def ai_key(request: Request, session: Session = Depends(get_session)) -> AiKey:
    return svc.authenticate(session, _bearer(request), request.headers, 'dieu_phoi')


def web_key(request: Request, session: Session = Depends(get_session)) -> AiKey:
    """Khóa riêng của Codex đăng web (phạm vi dang_web): chỉ lấy câu lệnh và trả câu trả lời nháp."""
    return svc.authenticate(session, _bearer(request), request.headers, 'dang_web')


class RemindIn(BaseModel):
    nguoi_id: int
    noi_dung: str = Field(max_length=300)
    phieu: str | None = None


class NoteIn(BaseModel):
    loai: str = 'GHI_CHU'
    tieu_de: str = Field(max_length=200)
    noi_dung: str = Field('', max_length=8000)
    phieu: str | None = None


class ProposeIn(BaseModel):
    loai: str
    ly_do: str = Field(max_length=500)
    phieu: str | None = None
    tho_id: int | None = None
    hen_luc: str | None = None
    khung_hen: str | None = None
    ma_hang: str | None = None
    so_luong: int | None = None


def _out(row):
    return dict(id=row.id, loai=row.kind, trang_thai=row.status, tieu_de=row.title, ket_qua=row.result)


@router.get('/tong-quan')
def overview(_: AiKey = Depends(ai_key), session: Session = Depends(get_session)):
    return svc.snapshot(session)


@router.post('/nhac-viec', status_code=201)
def remind(body: RemindIn, key: AiKey = Depends(ai_key), session: Session = Depends(get_session)):
    return _out(svc.remind(session, key, body.nguoi_id, body.noi_dung, body.phieu))


@router.post('/ghi-chu', status_code=201)
def note(body: NoteIn, key: AiKey = Depends(ai_key), session: Session = Depends(get_session)):
    return _out(svc.note(session, key, body.loai, body.tieu_de, body.noi_dung, body.phieu))


@router.post('/de-xuat', status_code=201)
def propose(body: ProposeIn, key: AiKey = Depends(ai_key), session: Session = Depends(get_session)):
    return _out(svc.propose(session, key, body.loai, body.ly_do, ticket_number=body.phieu, assignee_id=body.tho_id,
                            appointment_at=body.hen_luc, appointment_slot=body.khung_hen, product_code=body.ma_hang, qty=body.so_luong))


@router.get('/de-xuat')
def my_proposals(_: AiKey = Depends(ai_key), session: Session = Depends(get_session)):
    """AI xem lại các đề xuất gần đây đã được duyệt hay từ chối, để không đề xuất lặp."""
    from sqlalchemy import select
    from ..models import AiAction
    svc.expire(session)
    rows = session.scalars(select(AiAction).where(AiAction.kind.in_(svc.PROPOSALS)).order_by(AiAction.id.desc()).limit(50))
    return [dict(_out(r), ly_do_tu_choi=r.result if r.status == 'TU_CHOI' else '') for r in rows]


# ---------- Codex đăng web (phạm vi dang_web) ----------

class AnswerIn(BaseModel):
    tra_loi: str = Field(max_length=web_svc.TOI_DA_TRA_LOI)


class FailIn(BaseModel):
    ly_do: str = Field('', max_length=300)


@router.get('/dang-web/viec')
def web_jobs(key: AiKey = Depends(web_key), session: Session = Depends(get_session)):
    """Các câu lệnh đang chờ Codex trả lời (gọi đều đặn: app dựa vào đây để biết Codex còn chạy)."""
    return web_svc.lay_viec(session, key)


@router.post('/dang-web/viec/{viec_id}/tra-loi')
def web_answer(viec_id: str, body: AnswerIn, key: AiKey = Depends(web_key), session: Session = Depends(get_session)):
    return web_svc.tra_loi(session, key, viec_id, body.tra_loi)


@router.post('/dang-web/viec/{viec_id}/khong-lam-duoc')
def web_fail(viec_id: str, body: FailIn, key: AiKey = Depends(web_key), session: Session = Depends(get_session)):
    return web_svc.khong_lam_duoc(session, key, viec_id, body.ly_do)

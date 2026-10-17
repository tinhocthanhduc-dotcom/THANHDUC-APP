from fastapi import APIRouter, Depends, File, Form, UploadFile, Request
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .. import attachments as svc
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import User
from ..schemas import AttachmentOut, ServiceReason
from ..security import current_user
from ..web import COOKIE, web_user

router = APIRouter(prefix="/attachments", tags=["Tệp đính kèm"])


@router.get("", response_model=list[AttachmentOut])
def list_for(entity: str, entity_id: str, include_hidden: bool = False,
             user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.list_for(session, user, entity, entity_id, include_hidden)


@router.post("", response_model=AttachmentOut, status_code=201)
async def add(entity: str = Form(), entity_id: str = Form(), tag: str = Form("KHAC"), file: UploadFile = File(),
              user: User = Depends(current_user), session: Session = Depends(get_session)):
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise BusinessError("FILE_TOO_LARGE", "Tệp quá lớn.", {"max_mb": get_settings().max_upload_mb})
    return svc.add(session, user, entity, entity_id, tag, file.filename or "", data)


def file_user(request: Request, token=Depends(OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)),
              session: Session = Depends(get_session)):
    # Ảnh nhúng / biên bản dùng cookie; API tiếp tục nhận Bearer và trả 401 nếu thiếu cả hai.
    if token is not None:
        return current_user(token, session)
    if request.cookies.get(COOKIE):
        return web_user(request, session)
    return current_user(None, session)


@router.get("/{attachment_id}/file")
def download(attachment_id: int, user: User = Depends(file_user), session: Session = Depends(get_session)):
    path, a = svc.open_file(session, user, attachment_id)
    return FileResponse(path, media_type=a.content_type, filename=a.filename, content_disposition_type="inline",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/{attachment_id}/hide", response_model=AttachmentOut)
def hide(attachment_id: int, body: ServiceReason, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return svc.hide(session, user, attachment_id, body.reason, body.version)

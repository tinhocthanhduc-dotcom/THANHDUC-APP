"""Tệp đính kèm (D4): ảnh trước/sau sửa, biên bản, gắn vào phiếu dịch vụ hoặc phiếu kho.

Ảnh được xoay đúng chiều, bỏ thông tin EXIF (vị trí GPS) và thu về cạnh dài 1600px, lưu JPEG. PDF giữ nguyên.
Loại tệp nhận theo nội dung, không tin đuôi file hay khai báo của trình duyệt. Không xóa, chỉ ẩn kèm lý do.
"""
from contextlib import nullcontext

import re
import hashlib
import io
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, service_tickets
from .config import get_settings
from .errors import BusinessError
from .models import Attachment, StockDoc, User
from .schemas import AttachmentOut
from .security import has_role
from .stock_docs import _transaction

TAGS = ("TRUOC", "SAU", "BIEN_BAN", "KHAC", "NHAN_MAY", "TRA_MAY", "GIAO_HANG")
MAX_SIDE = 1600
MAX_PIXELS = 60_000_000  # chặn ảnh "bom" giải nén
HIDERS = ("dispatcher",)


def _service_ticket(session: Session, user: User, entity_id: str, write: bool):
    t = service_tickets._get(session, user, int(entity_id))
    if write and t.assignee_id != user.id and not has_role(user, *service_tickets.CREATORS):
        raise BusinessError("FORBIDDEN", "Chỉ thợ được giao hoặc điều phối được thêm tệp cho phiếu này.", status=403)


def _stock_doc(session: Session, user: User, entity_id: str, write: bool):
    if session.get(StockDoc, int(entity_id)) is None:
        raise BusinessError("NOT_FOUND", "Không tìm thấy phiếu kho.", status=404)
    if write and not has_role(user, "warehouse"):
        raise BusinessError("FORBIDDEN", "Chỉ Kho được thêm tệp cho phiếu kho.", status=403)


def _custody(session: Session, user: User, entity_id: str, write: bool):
    from .equipment_custody import load
    load(session,user,int(entity_id))


def _sales_delivery(session, user, entity_id, write):
    from .models import SalesDelivery
    from .technician_deliveries import load, check_assignee
    delivery=session.get(SalesDelivery,int(entity_id))
    if delivery is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy lần giao.',status=404)
    order=load(session,user,delivery.order_id)
    if write:
        check_assignee(user,order)


def _technician_return_line(session, user, entity_id, write):
    from . import technician_returns
    from .models import TechnicianReturnLine
    ident, line_no = (int(value) for value in entity_id.split(':'))
    row = technician_returns.load(session,user,ident)
    line = session.get(TechnicianReturnLine,(ident,line_no))
    if line is None: raise BusinessError('NOT_FOUND','Không tìm thấy dòng trả kho.',status=404)
    if write and row.status!='CHO_NHAN':
        raise BusinessError('BAD_STATUS','Chỉ thêm ảnh khi yêu cầu đang chờ kho nhận.',status=409)


# Loại chứng từ được đính kèm và cách kiểm quyền xem/ghi của từng loại.
TARGETS = {"technician_return_line": _technician_return_line, "sales_delivery": _sales_delivery, "service_ticket": _service_ticket, "stock_doc": _stock_doc, "equipment_custody": _custody}


def _check(session: Session, user: User, entity: str, entity_id: str, write: bool):
    check = TARGETS.get(entity)
    valid_id = bool(re.fullmatch(r'[0-9]+:[0-9]+',str(entity_id))) if entity=='technician_return_line' else str(entity_id).isdigit()
    if check is None or not valid_id:
        raise BusinessError("BAD_ENTITY", "Không đính kèm được vào loại chứng từ này.", {"entity": entity})
    check(session, user, str(entity_id), write)


def snapshot(a: Attachment) -> dict:
    return AttachmentOut.model_validate(a).model_dump(mode="json")


def _root() -> Path:
    return Path(get_settings().files_dir)


def _prepare(data: bytes) -> tuple[bytes, str, str, int | None, int | None]:
    """Trả (nội dung lưu, content_type, đuôi file, rộng, cao)."""
    if data.startswith(b"%PDF-"):
        return data, "application/pdf", "pdf", None, None
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("JPEG", "PNG", "WEBP"):
                raise BusinessError("BAD_FILE_TYPE", "Chỉ nhận ảnh JPEG, PNG, WEBP hoặc tệp PDF.")
            if im.width * im.height > MAX_PIXELS:
                raise BusinessError("FILE_TOO_LARGE", "Ảnh quá lớn.")
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=82, optimize=True)
            return out.getvalue(), "image/jpeg", "jpg", im.width, im.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise BusinessError("BAD_FILE_TYPE", "Chỉ nhận ảnh JPEG, PNG, WEBP hoặc tệp PDF.")


def add(session: Session, user: User, entity: str, entity_id: str, tag: str, filename: str, data: bytes, *, _in_tx=False) -> Attachment:
    if tag not in TAGS:
        raise BusinessError("BAD_TAG", "Loại tệp phải là ảnh trước, ảnh sau, biên bản hoặc khác.", {"allowed": list(TAGS)})
    if not data:
        raise BusinessError("EMPTY_FILE", "Tệp rỗng.")
    if len(data) > get_settings().max_upload_mb * 1024 * 1024:
        raise BusinessError("FILE_TOO_LARGE", "Tệp quá lớn.", {"max_mb": get_settings().max_upload_mb})
    if (tag=='GIAO_HANG') != (entity=='sales_delivery'):
        raise BusinessError('BAD_TAG','Ảnh giao hàng chỉ gắn vào lần giao đơn bán.')
    if entity == 'equipment_custody' or tag in ('NHAN_MAY','TRA_MAY'):
        from .equipment_custody import check_photo
        if not str(entity_id).isdigit():
            raise BusinessError('BAD_ENTITY','Mã phiếu giữ máy chưa hợp lệ.')
        if entity not in ('equipment_custody','service_ticket') or tag not in ('NHAN_MAY','TRA_MAY'):
            raise BusinessError('BAD_TAG','Mục giữ máy chỉ nhận ảnh nhận máy hoặc trả máy.')
        check_photo(session,user,int(entity_id),write=True,tag=tag)
    _check(session, user, entity, entity_id, write=True)
    content, content_type, ext, width, height = _prepare(data)
    if (tag in ('NHAN_MAY','TRA_MAY','GIAO_HANG') or entity=='technician_return_line') and not content_type.startswith('image/'):
        raise BusinessError('BAD_FILE_TYPE','Ảnh nhận và trả máy phải là ảnh JPEG, PNG hoặc WEBP.')
    now = datetime.now(UTC)
    rel = f"{entity}/{now:%Y/%m}/{uuid.uuid4().hex}.{ext}"
    target = _root() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, target)
    try:
        with (nullcontext() if _in_tx else _transaction(session)):
            a = Attachment(entity=entity, entity_id=str(entity_id), tag=tag, filename=(filename or "tep")[:255],
                           content_type=content_type, size=len(content), sha256=hashlib.sha256(content).hexdigest(),
                           path=rel, width=width, height=height, created_by=user.id)
            session.add(a)
            session.flush()
            audit.record(session, user, "attachment.add", "attachment", str(a.id), None, snapshot(a))
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return a


def list_for(session: Session, user: User, entity: str, entity_id: str, include_hidden: bool = False) -> list[Attachment]:
    _check(session, user, entity, entity_id, write=False)
    stmt = select(Attachment).where(Attachment.entity == entity, Attachment.entity_id == str(entity_id))
    if not (include_hidden and has_role(user, "director")):
        stmt = stmt.where(Attachment.hidden.is_(False))
    return list(session.scalars(stmt.order_by(Attachment.id)))


def has_visible(session: Session, entity: str, entity_id, tag: str) -> bool:
    return session.scalar(select(Attachment.id).where(
        Attachment.entity == entity, Attachment.entity_id == str(entity_id), Attachment.tag == tag, Attachment.hidden.is_(False)
    ).limit(1)) is not None


def _get(session: Session, user: User, attachment_id: int, lock: bool = False) -> Attachment:
    stmt = select(Attachment).where(Attachment.id == attachment_id)
    a = session.scalars(stmt.with_for_update() if lock else stmt).first()
    if a is None or (a.hidden and not has_role(user, "director")):
        raise BusinessError("NOT_FOUND", "Không tìm thấy tệp.", status=404)
    _check(session, user, a.entity, a.entity_id, write=False)
    return a


def open_file(session: Session, user: User, attachment_id: int) -> tuple[Path, Attachment]:
    a = _get(session, user, attachment_id)
    path = _root() / a.path
    if not path.is_file():
        raise BusinessError("FILE_MISSING", "Tệp không còn trên máy chủ, hãy khôi phục từ bản sao lưu.", status=410)
    return path, a


def hide(session: Session, user: User, attachment_id: int, reason: str, version: int | None) -> Attachment:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise BusinessError("REASON_REQUIRED", "Ẩn tệp phải ghi lý do.")
    with _transaction(session):
        a = _get(session, user, attachment_id, lock=True)
        if a.hidden:
            raise BusinessError("ALREADY_HIDDEN", "Tệp đã ẩn.", status=409)
        if a.created_by != user.id and not has_role(user, *HIDERS):
            raise BusinessError("FORBIDDEN", "Chỉ người tải lên hoặc điều phối được ẩn tệp.", status=403)
        if version is not None and a.version != version:
            raise BusinessError("VERSION_CONFLICT", "Người khác vừa sửa tệp, hãy tải lại.", {"current": a.version}, status=409)
        before = snapshot(a)
        a.hidden, a.hidden_reason, a.hidden_by, a.hidden_at = True, reason, user.id, datetime.now(UTC)
        session.flush()
        audit.record(session, user, "attachment.hide", "attachment", str(a.id), before, snapshot(a), reason)
    return a

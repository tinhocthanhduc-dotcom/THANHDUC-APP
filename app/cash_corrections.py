"""T4: yêu cầu điều chỉnh, duyệt bằng đảo phiếu gốc và lập phiếu dương mới."""
from datetime import UTC,datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import audit,cash_docs as docs,cash_posting,day_closings
from .errors import BusinessError
from .finance_catalogs import check_reader,check_writer,check_version
from .models import CashCorrection
from .schemas import CashDocIn
from .security import has_role


def _director(user):
    if not has_role(user,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được duyệt hoặc từ chối điều chỉnh.',status=403)


def snapshot(row):
    return {c.name:v.isoformat() if isinstance(v:=getattr(row,c.name),datetime) else v for c in CashCorrection.__table__.columns}


def _record(session,user,row,action,before,reason=''):
    session.flush()
    audit.record(session,user,'cash_correction.'+action,'cash_correction',str(row.id),before,snapshot(row),reason)


def _eligible(doc):
    if doc.source_type:
        raise BusinessError('SOURCE_LOCKED','Phiếu có nguồn tự động không điều chỉnh trực tiếp; phiếu từ báo thu phải hủy báo thu.',status=409)
    if doc.kind not in ('PT','PC') or doc.is_opening or doc.status!='DA_GHI':
        raise BusinessError('BAD_STATUS','Chỉ điều chỉnh PT hoặc PC đã ghi, không phải số dư đầu kỳ.',status=409)


def _body(doc,row):
    return CashDocIn(kind=doc.kind,doc_date=docs.today(),fund_code=row.new_fund_code,category_code=row.new_category_code,
        amount=row.new_amount,customer_code=doc.customer_code,supplier_code=doc.supplier_code,partner_name=doc.partner_name,
        description=f'Điều chỉnh cho {doc.number}'+(f': {row.new_description}' if row.new_description else ''))


def request(session,user,doc_id,body):
    check_writer(user)
    try:
        with docs._transaction(session):
            reason=(body.reason or '').strip()
            if not reason:
                raise BusinessError('REASON_REQUIRED','Yêu cầu điều chỉnh phải có lý do.')
            doc=docs._load(session,doc_id,True)
            check_version(doc,body.version);_eligible(doc)
            if session.scalar(select(CashCorrection.id).where(CashCorrection.doc_id==doc_id,CashCorrection.status=='CHO_DUYET')):
                raise BusinessError('CORRECTION_PENDING','Phiếu đang có yêu cầu điều chỉnh chờ duyệt.',status=409)
            row=CashCorrection(doc_id=doc_id,reason=reason,new_amount=body.new_amount,
                new_fund_code=body.new_fund_code or doc.fund_code,new_category_code=body.new_category_code or doc.category_code,
                new_description=body.new_description if body.new_description is not None else doc.description,requested_by=user.id)
            docs._validate(session,user,_body(doc,row))
            session.add(row);_record(session,user,row,'request',None,body.reason)
    except IntegrityError as exc:
        if getattr(getattr(exc.orig,'diag',None),'constraint_name','')=='uq_cash_corrections_pending':
            raise BusinessError('CORRECTION_PENDING','Phiếu đang có yêu cầu điều chỉnh chờ duyệt.',status=409) from exc
        raise
    return row


def _load(session,id):
    row=session.scalar(select(CashCorrection).where(CashCorrection.id==id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy yêu cầu điều chỉnh.',status=404)
    return row


def _pending(row,version):
    check_version(row,version)
    if row.status!='CHO_DUYET':
        raise BusinessError('BAD_STATUS','Yêu cầu đã được xử lý.',status=409)


def approve(session,user,id,version):
    _director(user)
    with docs._transaction(session):
        row=_load(session,id);_pending(row,version)
        doc=docs._load(session,row.doc_id,True);_eligible(doc)
        # Chỉ kiểm ngày hôm nay. Ngày gốc vẫn DA_CHOT, không tạm mở khóa ngày cũ.
        day_closings.check_open(session,docs.today())
        candidate=_body(doc,row)
        docs._validate(session,user,candidate)
        # Khóa hợp của quỹ cũ/mới trước khi đảo, không khóa chéo khi đổi quỹ.
        cash_posting._lock_balances(session,{doc.fund_code,candidate.fund_code})
        before=snapshot(row)
        cash_posting.void_in_tx(session,user,doc.id,('Điều chỉnh: '+row.reason)[:2000],doc.version,
                               _correction_permit=cash_posting._CORRECTION_PERMIT)
        new_doc=docs.insert(session,user,candidate)
        cash_posting.post_in_tx(session,user,new_doc.id,new_doc.version)
        row.status,row.new_doc_id,row.decided_by,row.decided_at='DA_DUYET',new_doc.id,user.id,datetime.now(UTC)
        _record(session,user,row,'approve',before,row.reason)
    return row


def reject(session,user,id,reason,version):
    _director(user)
    with docs._transaction(session):
        row=_load(session,id);_pending(row,version)
        reason=(reason or '').strip()
        if not reason or len(reason)>2000:
            raise BusinessError('REASON_REQUIRED','Từ chối phải nhập lý do (tối đa 2000 ký tự).')
        before=snapshot(row)
        row.status,row.decide_reason,row.decided_by,row.decided_at='TU_CHOI',reason,user.id,datetime.now(UTC)
        _record(session,user,row,'reject',before,reason)
    return row


def search(session,user):
    check_reader(user)
    return list(session.scalars(select(CashCorrection).where(CashCorrection.status=='CHO_DUYET').order_by(CashCorrection.id)))

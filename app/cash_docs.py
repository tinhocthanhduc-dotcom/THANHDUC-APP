"""T2: phiếu thu/chi/chuyển quỹ. Không sửa số dư tại tầng phiếu nháp."""
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from . import audit, day_closings
from .doc_numbers import next_number
from .errors import BusinessError
from .finance_catalogs import check_reader, check_writer, check_version
from .models import CashDoc, CashCategory, Customer, Fund, Supplier
from .schemas import CashDocIn, CashDocPatch
from .security import has_role

KINDS = {'PT':'Phiếu thu','PC':'Phiếu chi','CQ':'Chuyển quỹ'}
STATES = {'NHAP':'Nháp','DA_GHI':'Đã ghi','DA_HUY':'Đã hủy'}
HEADER_FIELDS = ('kind','doc_date','fund_code','to_fund_code','category_code','amount','customer_code',
                 'supplier_code','partner_name','description','is_opening')
MAX_MONEY = 9_223_372_036_854_775_807
TZ = ZoneInfo('Asia/Ho_Chi_Minh')


def today():
    return datetime.now(TZ).date()


def snapshot(doc):
    data = {c.name:getattr(doc,c.name) for c in CashDoc.__table__.columns}
    return {k:v.isoformat() if isinstance(v,(date,datetime)) else v for k,v in data.items()}


@contextmanager
def _transaction(session):
    try:
        yield
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT','Người khác vừa sửa phiếu, hãy tải lại.',status=409)
    except IntegrityError as exc:
        session.rollback()
        if getattr(getattr(exc.orig,'diag',None),'constraint_name','')=='uq_cash_docs_active_source':
            raise BusinessError('SOURCE_USED','Nguồn này đã có phiếu thu chi chưa hủy.',status=409) from exc
        raise
    except Exception:
        session.rollback()
        raise


def _load(session,doc_id,lock=False):
    stmt = select(CashDoc).where(CashDoc.id==doc_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    row = session.scalar(stmt)
    if row is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy phiếu thu chi.',status=404)
    return row


def load(session,user,doc_id):
    check_reader(user)
    return _load(session,doc_id)


def search(session,user,kind=None,fund=None,status=None,from_date=None,to_date=None):
    check_reader(user)
    if kind and kind not in KINDS:
        raise BusinessError('BAD_KIND','Loại phiếu chưa hợp lệ.')
    if status and status not in STATES:
        raise BusinessError('BAD_STATUS','Trạng thái phiếu chưa hợp lệ.')
    if from_date and to_date and from_date>to_date:
        raise BusinessError('BAD_DATE_RANGE','Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
    stmt = select(CashDoc)
    for column,value in ((CashDoc.kind,kind),(CashDoc.status,status)):
        if value:
            stmt = stmt.where(column==value)
    if fund:
        stmt = stmt.where(or_(CashDoc.fund_code==fund,CashDoc.to_fund_code==fund))
    if from_date:
        stmt = stmt.where(CashDoc.doc_date>=from_date)
    if to_date:
        stmt = stmt.where(CashDoc.doc_date<=to_date)
    return list(session.scalars(stmt.order_by(CashDoc.doc_date.desc(),CashDoc.id.desc())))


def _opening(user,is_opening):
    if is_opening and not has_role(user,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được thao tác phiếu số dư đầu kỳ.',status=403)


def _draft(doc):
    if doc.status=='DA_GHI':
        raise BusinessError('DOC_POSTED','Phiếu đã ghi sổ, không được sửa hoặc hủy nháp.',status=409)
    if doc.status=='DA_HUY':
        raise BusinessError('DOC_VOID','Phiếu đã hủy, không được sửa hoặc hủy lại.',status=409)


def check_source(doc):
    if doc.source_type in ('PAYROLL_LINE','SALARY_ADVANCE'):
        raise BusinessError('SOURCE_LOCKED','Phiếu chi lương/tạm ứng gắn hồ sơ bất biến, không sửa hoặc hủy riêng. Điều chỉnh ở kỳ sau.',status=409)
    if doc.source_type=='SALES_REFUND':
        raise BusinessError('SOURCE_LOCKED','Phiếu chi hoàn tiền gắn đề nghị hoàn, không được sửa hoặc hủy riêng.',status=409)
    if doc.source_type=='SUPPLIER_PAYMENT':
        raise BusinessError('SOURCE_LOCKED','Phiếu chi này được tạo từ trả NCC. Phải hủy trả tiền tại trang công nợ NCC.',status=409)
    if doc.source_type=='SALES_RECEIPT':
        raise BusinessError('SOURCE_LOCKED','Phiếu thu này được tạo từ báo thu. Phải hủy báo thu để ghi đảo phiếu thu.',status=409)


def _validate(session,user,body):
    _opening(user,body.is_opening)
    if body.kind=='CQ':
        if not body.to_fund_code or body.to_fund_code==body.fund_code:
            raise BusinessError('BAD_TRANSFER','Chuyển quỹ phải chọn quỹ đích khác quỹ nguồn.')
        if body.is_opening:
            raise BusinessError('BAD_OPENING','Số dư đầu kỳ dùng phiếu thu hoặc phiếu chi, không dùng chuyển quỹ.')
    elif body.to_fund_code is not None:
        raise BusinessError('BAD_TRANSFER','Chỉ chuyển quỹ được có quỹ đích.')
    for code in sorted({body.fund_code,body.to_fund_code}-{None}):
        row = session.scalar(select(Fund).where(Fund.code==code).with_for_update(read=True).execution_options(populate_existing=True))
        if row is None or not row.active:
            raise BusinessError('BAD_FUND','Quỹ không tồn tại hoặc đã ngừng sử dụng.',{'fund':code})
    if body.kind=='CQ' or body.is_opening:
        if body.category_code is not None:
            raise BusinessError('BAD_CATEGORY','Chuyển quỹ và số dư đầu kỳ không có danh mục thu chi.')
    else:
        cat = session.scalar(select(CashCategory).where(CashCategory.code==body.category_code).with_for_update(read=True)
                             .execution_options(populate_existing=True))
        if cat is None or not cat.active or cat.direction!=('THU' if body.kind=='PT' else 'CHI'):
            raise BusinessError('BAD_CATEGORY','Phiếu thu cần danh mục Thu; phiếu chi cần danh mục Chi đang sử dụng.')
    if body.customer_code and body.supplier_code:
        raise BusinessError('BAD_PARTNER','Chỉ chọn khách hàng hoặc nhà cung cấp, không chọn cả hai.')
    if body.customer_code:
        customer = session.scalar(select(Customer).where(Customer.code==body.customer_code).with_for_update(read=True)
                                  .execution_options(populate_existing=True))
        if customer is None or customer.status!='DANG_DUNG':
            raise BusinessError('BAD_PARTNER','Khách hàng không tồn tại hoặc đã ngừng sử dụng.')
    if body.supplier_code:
        supplier = session.scalar(select(Supplier).where(Supplier.code==body.supplier_code).with_for_update(read=True)
                                  .execution_options(populate_existing=True))
        if supplier is None or not supplier.active:
            raise BusinessError('BAD_PARTNER','Nhà cung cấp không tồn tại hoặc đã ngừng sử dụng.')
    if bool(body.source_type)!=bool(body.source_id):
        raise BusinessError('BAD_SOURCE','Nguồn phiếu phải có đủ loại và mã nguồn.')


def _record(session,user,doc,action,before,reason=''):
    session.flush()
    audit.record(session,user,'cash_doc.'+action,'cash_doc',str(doc.id),before,snapshot(doc),reason)


def insert(session,user,body: CashDocIn):
    """Trong transaction nơi gọi, không commit; caller kiểm quyền cho phiếu tự động. Đầu kỳ luôn cần director."""
    _opening(user,body.is_opening)
    day_closings.check_open(session,body.doc_date)
    _validate(session,user,body)
    doc = CashDoc(**body.model_dump(),created_by=user.id,number=next_number(session,body.kind,body.doc_date))
    session.add(doc)
    _record(session,user,doc,'create',None)
    return doc


def create(session,user,body: CashDocIn):
    check_writer(user)
    if body.source_type in ('PAYROLL_LINE','SALARY_ADVANCE'):
        raise BusinessError('SOURCE_LOCKED','Phiếu chi lương/tạm ứng phải tạo từ hồ sơ nguồn.',status=409)
    if body.source_type=='SALES_REFUND':
        raise BusinessError('SOURCE_LOCKED','Hoàn tiền phải được duyệt từ đề nghị hoàn.',status=409)
    with _transaction(session):
        doc = insert(session,user,body)
    return doc


def update(session,user,doc_id,body: CashDocPatch):
    check_writer(user)
    with _transaction(session):
        doc = _load(session,doc_id,True)
        _opening(user,doc.is_opening)
        check_version(doc,body.version)
        day_closings.check_days(session,[doc.doc_date,body.doc_date or doc.doc_date])
        _draft(doc)
        before = snapshot(doc)
        data = {k:getattr(doc,k) for k in (*HEADER_FIELDS,'source_type','source_id')}
        data.update(body.model_dump(exclude_unset=True,exclude={'version'}))
        try:
            candidate = CashDocIn(**data)
        except ValidationError as exc:
            raise BusinessError('BAD_FORM','Dữ liệu phiếu chưa hợp lệ, hãy kiểm tra lại.') from exc
        if doc.kind!=candidate.kind or doc.doc_date.year!=candidate.doc_date.year:
            raise BusinessError('NUMBER_LOCKED','Đổi loại phiếu hoặc sang năm khác thì hủy nháp và lập phiếu mới.',status=409)
        _validate(session,user,candidate)
        for field in HEADER_FIELDS:
            setattr(doc,field,getattr(candidate,field))
        doc.version += 1
        _record(session,user,doc,'update',before)
    return doc


def cancel(session,user,doc_id,body):
    check_writer(user)
    with _transaction(session):
        doc = _load(session,doc_id,True)
        check_source(doc)
        _opening(user,doc.is_opening)
        check_version(doc,body.version)
        day_closings.check_open(session,doc.doc_date)
        _draft(doc)
        reason = (body.reason or '').strip()
        if not reason:
            raise BusinessError('REASON_REQUIRED','Hủy phiếu phải nhập lý do.')
        before = snapshot(doc)
        doc.status,doc.void_reason = 'DA_HUY',reason
        _record(session,user,doc,'cancel',before,reason)
    return doc

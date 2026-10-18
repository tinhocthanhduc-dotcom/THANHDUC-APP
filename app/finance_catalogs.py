"""T1: quỹ và danh mục thu chi; không xóa, dùng chung cho các màn hình."""
from contextlib import contextmanager
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .config import get_settings
from .customers import like_pattern
from .errors import BusinessError
from .models import Fund, CashCategory, FinanceMapping
from .security import has_role
from .text import fold

WRITERS = ('finance',)


def check_reader(user):
    if not has_role(user, 'finance'):
        raise BusinessError('FORBIDDEN', 'Chỉ kế toán hoặc giám đốc được xem tài chính.', status=403)


def check_writer(user):
    check_reader(user)


def normalize_code(value):
    code = ''.join(str(value or '').split()).upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9_-]{0,31}', code):
        raise BusinessError('BAD_CODE', 'Mã chỉ gồm chữ không dấu, số, dấu gạch ngang hoặc gạch dưới (tối đa 32 ký tự).')
    return code


def snapshot(row):
    fields = ('code','name','kind','bank_name','account_no','note','active','search_text','version') if isinstance(row,Fund) else (
        'code','name','direction','note','active','search_text','version')
    return {key:getattr(row,key) for key in fields}


@contextmanager
def transaction(session):
    try:
        yield
        session.commit()
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT','Người khác vừa sửa, hãy tải lại.',status=409)
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(getattr(exc.orig,'diag',None),'constraint_name','')
        if constraint in ('funds_pkey','cash_categories_pkey'):
            raise BusinessError('DUPLICATE_CODE','Mã này đã có, hãy dùng mã khác.',status=409) from exc
        raise
    except Exception:
        session.rollback()
        raise


def check_version(row,version):
    if not isinstance(version,int) or isinstance(version,bool) or version < 1:
        raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản, hãy tải lại trang.')
    if row.version != version:
        raise BusinessError('VERSION_CONFLICT','Người khác vừa sửa, hãy tải lại.',{'current':row.version},status=409)


def _load(session,model,code,lock=False):
    stmt = select(model).where(model.code==normalize_code(code))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(stmt)
    if row is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy quỹ hoặc danh mục thu chi.',status=404)
    return row


def load(session,user,model,code):
    check_reader(user)
    return _load(session,model,code)


def search(session,user,model,q='',*,active=None,kind=None,direction=None):
    check_reader(user)
    stmt = select(model)
    if fold(q):
        stmt = stmt.where(model.search_text.like(like_pattern(fold(q))))
    if active is not None:
        stmt = stmt.where(model.active==active)
    if model is Fund and kind:
        stmt = stmt.where(Fund.kind==kind)
    if model is CashCategory and direction:
        stmt = stmt.where(CashCategory.direction==direction)
    return list(session.scalars(stmt.order_by(model.code).limit(get_settings().search_limit)))


def _validate(model,data):
    if model is Fund:
        if data['kind'] not in ('TIEN_MAT','NGAN_HANG'):
            raise BusinessError('BAD_KIND','Loại quỹ chưa hợp lệ.')
        if data['kind']=='NGAN_HANG' and not data['account_no'].strip():
            raise BusinessError('ACCOUNT_REQUIRED','Quỹ ngân hàng phải có số tài khoản.')
    elif data['direction'] not in ('THU','CHI'):
        raise BusinessError('BAD_DIRECTION','Chiều thu chi chưa hợp lệ.')


def _record(session,user,row,before):
    row.search_text = fold(' '.join(str(getattr(row,k,'')) for k in ('code','name','bank_name','account_no','note')))
    session.flush()
    entity = 'fund' if isinstance(row,Fund) else 'cash_category'
    audit.record(session,user,entity+('.create' if before is None else '.update'),entity,row.code,before,snapshot(row))


def create(session,user,model,body):
    check_writer(user)
    with transaction(session):
        data = body.model_dump()
        data['code'] = normalize_code(data['code'])
        _validate(model,data)
        if session.get(model,data['code']) is not None:
            raise BusinessError('DUPLICATE_CODE','Mã này đã có, hãy dùng mã khác.',status=409)
        row = model(**data)
        session.add(row)
        _record(session,user,row,None)
    return row


def update(session,user,model,code,body):
    check_writer(user)
    with transaction(session):
        # Cùng thứ tự khóa đích -> bảng ghép với set_mapping, tránh ngừng dùng khi đang ghép.
        row = _load(session,model,code,lock=True)
        check_version(row,body.version)
        before = snapshot(row)
        changes = body.model_dump(exclude_unset=True,exclude={'version'})
        if any(v is None for v in changes.values()):
            raise BusinessError('BAD_FORM','Không được để rỗng trường bắt buộc.')
        data = {**before,**changes}
        _validate(model,data)
        target = FinanceMapping.fund_code if model is Fund else FinanceMapping.category_code
        keys = list(session.scalars(select(FinanceMapping.key).where(target==row.code)))
        if keys and not data['active']:
            raise BusinessError('IN_USE','Quỹ hoặc danh mục đang được bảng ghép sử dụng. Hãy bỏ ghép trước.',status=409)
        invalid_kind = model is Fund and any(data['kind'] != ('TIEN_MAT' if key=='THU_TIEN_MAT' else 'NGAN_HANG') for key in keys)
        if invalid_kind or (model is CashCategory and keys and data['direction']!='THU'):
            raise BusinessError('BAD_MAPPING','Không thể đổi loại hoặc chiều khi đang được bảng ghép sử dụng.',status=409)
        for key,value in changes.items():
            setattr(row,key,value)
        row.version += 1
        _record(session,user,row,before)
    return row

"""Các ghép tài chính cố định; các bước thu tiền sau dùng require()."""
from sqlalchemy import select

from . import audit, finance_catalogs as catalogs
from .errors import BusinessError
from .models import Fund, CashCategory, FinanceMapping
from .security import has_role

KEYS = {
    'DANH_MUC_LUONG':'Chi lương, tạm ứng (Chi)',
    'THU_TIEN_MAT':'Thu tiền mặt',
    'THU_CHUYEN_KHOAN':'Thu chuyển khoản',
    'DANH_MUC_BAN_HANG':'Danh mục bán hàng',
    'DANH_MUC_DICH_VU':'Danh mục dịch vụ',
    'DANH_MUC_HOAN_TIEN_BAN':'Hoàn tiền bán hàng (Chi)',
    'DANH_MUC_MUA_HANG':'Danh mục mua hàng (Chi)',
    'DANH_MUC_CHI_PHI_THO':'Hoàn chi phí phát sinh cho thợ (Chi)',
}


def category_direction(key):
    return 'CHI' if key in ('DANH_MUC_MUA_HANG','DANH_MUC_HOAN_TIEN_BAN','DANH_MUC_LUONG','DANH_MUC_CHI_PHI_THO') else 'THU'


def check_writer(user):
    if not has_role(user,'director'):
        raise BusinessError('FORBIDDEN','Chỉ giám đốc được sửa bảng ghép tài chính.',status=403)


def _key(key):
    if key not in KEYS:
        raise BusinessError('BAD_MAPPING','Khóa ghép tài chính không hợp lệ.')


def snapshot(row):
    return {k:getattr(row,k) for k in ('key','fund_code','category_code','updated_by','version')}


def search(session,user):
    catalogs.check_reader(user)
    return list(session.scalars(select(FinanceMapping).order_by(FinanceMapping.key)))


def valid_options(session,user,key,q=''):
    _key(key)
    if key.startswith('THU_'):
        return catalogs.search(session,user,Fund,q,active=True,kind='TIEN_MAT' if key=='THU_TIEN_MAT' else 'NGAN_HANG')
    return catalogs.search(session,user,CashCategory,q,active=True,direction=category_direction(key))


def set_mapping(session,user,key,fund_code=None,category_code=None,version=None):
    check_writer(user)
    _key(key)
    with catalogs.transaction(session):
        is_fund = key.startswith('THU_')
        if (is_fund and category_code is not None) or (not is_fund and fund_code is not None):
            raise BusinessError('BAD_MAPPING','Loại quỹ hoặc danh mục không đúng với khóa ghép.')
        code = fund_code if is_fund else category_code
        if code is not None:
            try:
                code = catalogs.normalize_code(code)
            except BusinessError as exc:
                raise BusinessError('BAD_MAPPING','Mã quỹ hoặc danh mục ghép chưa hợp lệ.') from exc
            model = Fund if is_fund else CashCategory
            target = session.scalar(select(model).where(model.code==code).with_for_update().execution_options(populate_existing=True))
            expected = 'TIEN_MAT' if key=='THU_TIEN_MAT' else 'NGAN_HANG'
            if target is None or not target.active or (is_fund and target.kind!=expected) or (not is_fund and target.direction!=category_direction(key)):
                raise BusinessError('BAD_MAPPING','Phải chọn quỹ đúng loại hoặc danh mục đúng chiều Thu/Chi đang sử dụng.')
        row = session.scalar(select(FinanceMapping).where(FinanceMapping.key==key).with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise BusinessError('MAPPING_REQUIRED','Chưa có cấu hình ghép tài chính, hãy kiểm tra migration.',status=409)
        catalogs.check_version(row,version)
        before = snapshot(row)
        row.fund_code = code if is_fund else None
        row.category_code = None if is_fund else code
        row.updated_by = user.id
        row.version += 1
        session.flush()
        audit.record(session,user,'finance_mapping.update','finance_mapping',key,before,snapshot(row))
    return row


def require(session,key):
    _key(key)
    # Không dùng identity-map cache: bước thu tiền phải đọc cấu hình mới nhất.
    column = FinanceMapping.fund_code if key.startswith('THU_') else FinanceMapping.category_code
    with session.no_autoflush:
        code = session.scalar(select(column).where(FinanceMapping.key==key))
    if not code:
        raise BusinessError('MAPPING_REQUIRED',f'Chưa ghép {KEYS[key].lower()}. Giám đốc cần thiết lập bảng ghép tài chính.',{'key':key},status=409)
    return code

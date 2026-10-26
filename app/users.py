"""Employee accounts. Passwords and hashes never enter audit snapshots."""
import re
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from . import audit, inventory_catalogs, sales_receipts, equipment_custody, service_tickets
from .errors import BusinessError
from .models import ROLES, User, Warehouse, StockBalance, Product, ServiceTicket
from .security import hash_password, verify_password, issue_token
from .stock_docs import _transaction
from .text import fold
from . import search as tim

_ALPHABET='ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
# Serializes lifecycle changes, including concurrent attempts to remove the last active admin.
_LOCK_KEY=834036


def temporary_password():
    return ''.join(secrets.choice(_ALPHABET) for _ in range(12))


def snapshot(user):
    return dict(username=user.username,full_name=user.full_name,phone=user.phone,roles=list(user.roles),warehouse_codes=list(user.warehouse_codes or []),active=user.active,
        must_change_password=user.must_change_password,token_version=user.token_version,
        updated_at=user.updated_at.isoformat() if user.updated_at else None)


def check_admin(user):
    if not user.active or 'admin' not in user.roles:
        raise BusinessError('FORBIDDEN','Chỉ quản trị được quản lý tài khoản nhân viên.',status=403)


def marker(user):
    return (user.updated_at or user.created_at).isoformat()


def _version(user,version):
    try:
        expected=version if isinstance(version,datetime) else datetime.fromisoformat(str(version))
    except (TypeError,ValueError):
        raise BusinessError('VERSION_REQUIRED','Hãy tải lại tài khoản trước khi lưu.')
    if expected!=(user.updated_at or user.created_at):
        raise BusinessError('VERSION_CONFLICT','Người khác vừa sửa tài khoản, hãy tải lại.',status=409)


def _touch(user):
    now=datetime.now(UTC)
    user.updated_at=max(now,user.updated_at+timedelta(microseconds=1)) if user.updated_at else now


def _roles(roles):
    if not isinstance(roles,(list,tuple)) or not roles:
        raise BusinessError('ROLES_REQUIRED','Chọn ít nhất một vai trò.')
    bad=[role for role in roles if role not in ROLES]
    if bad:raise BusinessError('UNKNOWN_ROLE','Vai trò không tồn tại.',{'roles':bad})
    return [role for role in ROLES if role in roles]


def warehouse_choices(session):
    return list(session.scalars(select(Warehouse).where(Warehouse.kind=='KHO',Warehouse.active.is_(True)).order_by(Warehouse.code)))


def _warehouse_codes(session,codes):
    codes=[str(code) for code in (codes or [])]
    known={w.code for w in warehouse_choices(session)}
    bad=[code for code in codes if code not in known]
    if bad:raise BusinessError('UNKNOWN_WAREHOUSE','Kho phụ trách không tồn tại hoặc đã ngừng.',{'warehouse_codes':bad})
    return sorted(set(codes))


def _profile(full_name,phone):
    name=str(full_name or '').strip();phone=str(phone or '').strip()
    if not name or len(name)>200:raise BusinessError('BAD_FULL_NAME','Họ tên bắt buộc, tối đa 200 ký tự.')
    if len(phone)>200:raise BusinessError('BAD_PHONE','Số điện thoại tối đa 200 ký tự.')
    return name,phone


def _admin(session,actor):
    fresh=session.get(User,actor.id,populate_existing=True)
    if fresh is None:raise BusinessError('FORBIDDEN','Không có quyền quản trị.',status=403)
    check_admin(fresh)
    return fresh


def _load(session,ident):
    # Match inventory catalog lock order: warehouse, then its holder.
    warehouses=list(session.scalars(select(Warehouse).where(Warehouse.kind=='THO',Warehouse.holder_id==ident).order_by(Warehouse.code).with_for_update().execution_options(populate_existing=True)))
    row=session.scalar(select(User).where(User.id==ident).with_for_update().execution_options(populate_existing=True))
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy tài khoản.',status=404)
    return row,warehouses


def _last_admin(session,user,roles=None,active=None):
    losing=user.active and 'admin' in user.roles and (roles is not None and 'admin' not in roles or active is False)
    if losing and session.scalar(select(func.count()).select_from(User).where(User.active.is_(True),User.roles.contains(['admin'])))<=1:
        raise BusinessError('LAST_ADMIN','Phải giữ ít nhất một quản trị đang hoạt động.',status=409)


def _record(session,actor,user,action,before,reason=''):
    _touch(user);session.flush()
    audit.record(session,actor,'user.'+action,'user',str(user.id),before,snapshot(user),reason)


@contextmanager
def _admin_tx(session,actor):
    with _transaction(session):
        session.execute(text('select pg_advisory_xact_lock(:key)'),dict(key=_LOCK_KEY))
        yield _admin(session,actor)


def create(session,admin,username,full_name,phone,roles,*,password=None,warehouse_codes=None):
    """API may supply its existing initial-password field; web creation generates a temporary one."""
    try:
        with _admin_tx(session,admin) as actor:
            username=str(username or '').strip().lower()
            if not re.fullmatch(r'[a-z0-9._-]{3,32}',username):raise BusinessError('BAD_USERNAME','Tên đăng nhập gồm 3–32 chữ không dấu, số và . _ - .')
            roles=_roles(roles);name,phone=_profile(full_name,phone)
            if session.scalar(select(User.id).where(User.username==username)):
                raise BusinessError('USERNAME_TAKEN','Tên đăng nhập đã có.',status=409)
            temp=temporary_password() if password is None else password
            user=User(username=username,full_name=name,phone=phone,roles=roles,warehouse_codes=_warehouse_codes(session,warehouse_codes),password_hash=hash_password(temp),must_change_password=True)
            session.add(user);session.flush()
            if 'technician' in roles:inventory_catalogs.technician_warehouse_in_tx(session,actor,user)
            _record(session,actor,user,'create',None)
        return user,temp
    except IntegrityError as exc:
        if getattr(getattr(exc.orig,'diag',None),'constraint_name',None)=='users_username_key':
            raise BusinessError('USERNAME_TAKEN','Tên đăng nhập đã có.',status=409) from exc
        raise


def update_roles(session,admin,user_id,roles,version):
    return update(session,admin,user_id,roles,version)


def update(session,admin,user_id,roles,version,full_name=None,phone=None,warehouse_codes=None):
    with _admin_tx(session,admin) as actor:
        user,warehouses=_load(session,user_id);_version(user,version)
        roles=_roles(roles);_last_admin(session,user,roles=roles)
        before=snapshot(user)
        if 'technician' not in roles:
            codes=[w.code for w in warehouses]
            stocks=list(session.execute(select(StockBalance.warehouse_code,StockBalance.product_code,StockBalance.qty).where(StockBalance.warehouse_code.in_(codes),StockBalance.qty!=0).order_by(StockBalance.warehouse_code,StockBalance.product_code).with_for_update()))
            if stocks:
                products=sorted({row.product_code for row in stocks})
                raise BusinessError('TECH_STOCK_NOT_EMPTY','Kho thợ còn hàng: '+', '.join(products),{'products':products},status=409)
            for warehouse in warehouses:
                if warehouse.active:
                    old=inventory_catalogs.snapshot(warehouse);warehouse.active=False;warehouse.version+=1
                    session.flush();audit.record(session,actor,'warehouse.update','warehouse',warehouse.code,old,inventory_catalogs.snapshot(warehouse))
        user.roles=roles
        if warehouse_codes is not None:user.warehouse_codes=_warehouse_codes(session,warehouse_codes)
        if full_name is not None or phone is not None:
            user.full_name,user.phone=_profile(full_name if full_name is not None else user.full_name,phone if phone is not None else user.phone)
        session.flush()
        if 'technician' in roles:inventory_catalogs.technician_warehouse_in_tx(session,actor,user)
        _record(session,actor,user,'roles',before)
    return user


def lock(session,admin,user_id,reason):
    with _admin_tx(session,admin) as actor:
        user,_=_load(session,user_id)
        if user.id==actor.id:raise BusinessError('SELF_LOCK','Không tự khóa tài khoản của mình.',status=409)
        reason=str(reason or '').strip()
        if not reason:raise BusinessError('REASON_REQUIRED','Phải ghi lý do khóa tài khoản.')
        _last_admin(session,user,active=False)
        if not user.active:raise BusinessError('BAD_STATUS','Tài khoản đã khóa.',status=409)
        before=snapshot(user);user.active=False;user.token_version+=1
        _record(session,actor,user,'lock',before,reason)
    return user


def unlock(session,admin,user_id):
    with _admin_tx(session,admin) as actor:
        user,_=_load(session,user_id)
        if user.active:raise BusinessError('BAD_STATUS','Tài khoản đang dùng.',status=409)
        before=snapshot(user);user.active=True
        _record(session,actor,user,'unlock',before)
    return user


def reset_password(session,admin,user_id):
    with _admin_tx(session,admin) as actor:
        user,_=_load(session,user_id);before=snapshot(user)
        temp=temporary_password();user.password_hash=hash_password(temp);user.must_change_password=True;user.token_version+=1
        _record(session,actor,user,'reset_password',before)
    return temp


def change_own_password(session,user,old,new):
    """Return the refreshed token minted while the user row is still locked."""
    with _transaction(session):
        session.execute(text('select pg_advisory_xact_lock(:key)'),dict(key=_LOCK_KEY))
        current=session.scalar(select(User).where(User.id==user.id).with_for_update().execution_options(populate_existing=True))
        if current is None or not current.active:raise BusinessError('UNAUTHENTICATED','Phiên đăng nhập đã hết hạn.',status=401)
        if not verify_password(old,current.password_hash):raise BusinessError('BAD_PASSWORD','Mật khẩu cũ chưa đúng.',status=400)
        if old==new:raise BusinessError('PASSWORD_UNCHANGED','Mật khẩu mới phải khác mật khẩu cũ.')
        hashed=hash_password(new);before=snapshot(current)
        current.password_hash=hashed;current.must_change_password=False;current.token_version+=1
        _record(session,current,current,'change_password',before)
        token=issue_token(current)
    return token


def authenticate(session,username,password):
    with _transaction(session):
        user=session.scalar(select(User).where(User.username==str(username).strip().lower()).with_for_update().execution_options(populate_existing=True))
        if not user or not user.active or not verify_password(password,user.password_hash):
            raise BusinessError('BAD_LOGIN','Sai tên đăng nhập hoặc mật khẩu.',status=401)
        user.last_login_at=datetime.now(UTC);session.flush()
        token=issue_token(user)
    return user,token


def handover_summary(session,user):
    with session.no_autoflush:
        stock=list(session.execute(select(StockBalance.warehouse_code,StockBalance.product_code,StockBalance.qty,Product.name).join(Warehouse,Warehouse.code==StockBalance.warehouse_code).join(Product,Product.code==StockBalance.product_code).where(Warehouse.kind=='THO',Warehouse.holder_id==user.id,StockBalance.qty!=0).order_by(StockBalance.warehouse_code,StockBalance.product_code)).mappings())
        cash=sales_receipts.held_by(session,user)
        machines=equipment_custody.held_rows(session,location='THO_GIU',holder_id=user.id)
        tickets=list(session.scalars(select(ServiceTicket).where(ServiceTicket.assignee_id==user.id,ServiceTicket.status.in_(service_tickets.OPEN)).order_by(ServiceTicket.id)))
        return dict(stock_count=len(stock),stock_rows=stock,cash_total=cash['total'],cash_rows=cash['rows'],machine_count=len(machines),machine_rows=machines,ticket_count=len(tickets),ticket_rows=tickets)


def search(session,admin,q='',status=''):
    check_admin(admin)
    stmt=select(User).order_by(User.full_name,User.username)
    if status=='active':stmt=stmt.where(User.active.is_(True))
    elif status=='locked':stmt=stmt.where(User.active.is_(False))
    elif status:raise BusinessError('BAD_STATUS','Trạng thái tài khoản chưa hợp lệ.')
    needle=fold(q)
    return [row for row in session.scalars(stmt) if not needle or tim.text_matches(q,' '.join([row.full_name,row.username,row.phone]))]

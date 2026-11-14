"""Bảng giá chung toàn công ty (TUẤN duyệt 14/11/2026, đợt 1 quy trình báo giá sửa chữa).

Một trang cho mọi giá:
- 3 tab hàng hóa (Máy in, Mực & hộp mực, Linh kiện & VPP) đọc thẳng products.sale_price, không nhập lại.
- Dịch vụ & phụ phí (PHI): một giá, ví dụ phí kiểm tra, phụ phí tận nơi.
- Gói sửa chữa (GOI): giá trọn gói theo nhóm máy × dịch vụ × cấp 1 Nhẹ / 2 TB / 3 Nặng, model khác nhóm thì ghi đè.
- Hiện tượng: khách tả lỗi → dịch vụ + khoảng cấp có thể cần (Báo giá nhanh dùng ở đợt 2).

Quy tắc: giá lưu chưa VAT; chỉ giám đốc sửa giá; mọi lần đổi ghi audit_log (Lịch sử đổi giá); giá vốn, lãi chỉ
giám đốc và kế toán thấy; tra giá theo thứ tự model ghi đè → giá nhóm của model → không có thì "Cần xác minh",
không bao giờ đoán. Phiếu đã lập giữ giá đã ghi trên phiếu (bảng này không sửa phiếu cũ).
"""
import csv
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from . import audit
from .config import DEFAULT_VAT_RATE
from .errors import BusinessError
from .models import (AuditLog, PriceService, Product, RepairGroup, RepairModel, RepairOption, RepairPrice,
                     RepairSymptom, StockBalance, User, Warehouse)
from .security import has_role
from .text import fold

TABS = {'may-in': 'Máy in', 'muc': 'Mực & hộp mực', 'linh-kien': 'Linh kiện & VPP',
        'dich-vu': 'Dịch vụ & phụ phí', 'goi': 'Gói sửa chữa', 'hien-tuong': 'Hiện tượng'}
PRODUCT_TABS = ('may-in', 'muc', 'linh-kien')
LEVELS = {1: 'Nhẹ', 2: 'Trung bình', 3: 'Nặng'}
VIEWERS = ('sales', 'dispatcher', 'reception', 'finance', 'warehouse', 'technician')
MAX_PRICE = 1_000_000_000
PRICE_ACTIONS = ('price_service.create', 'price_service.update', 'repair_price.save', 'repair_model.create',
                 'repair_model.update', 'repair_symptom.save', 'repair_symptom.delete', 'product.price')
INK_WORDS = ('muc', 'drum', 'trong', 'toner', 'ribbon', 'ruy bang', 'cartridge')
PRINTER_WORDS = ('may in', 'may photo', 'may scan', 'may da nang')


# ---------- quyền ----------

def can_view(user: User) -> bool:
    return bool(user and user.active and has_role(user, *VIEWERS))


def can_edit(user: User) -> bool:
    return bool(user and user.active and has_role(user, 'director'))


def can_cost(user: User) -> bool:
    """Giá vốn và % lãi: giám đốc và kế toán."""
    return bool(user and user.active and has_role(user, 'finance'))


def require_view(user):
    if not can_view(user):
        raise BusinessError('FORBIDDEN', 'Tài khoản không xem được bảng giá.', status=403)


def require_edit(user):
    if not can_edit(user):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được sửa bảng giá.', status=403)


# ---------- số ----------

def with_vat(price: int | None) -> int:
    return (int(price or 0) * (100 + DEFAULT_VAT_RATE) + 50) // 100


def parse_money(raw, label: str, required=False) -> int | None:
    text = str(raw if raw is not None else '').strip()
    if not text:
        if required:
            raise BusinessError('BAD_PRICE', f'{label}: chưa nhập giá.')
        return None
    digits = text.replace('.', '').replace(',', '').replace(' ', '').replace('đ', '')
    if not digits.isdigit():
        raise BusinessError('BAD_PRICE', f'{label}: giá chỉ gồm số (ví dụ 450.000).')
    value = int(digits)
    if value > MAX_PRICE:
        raise BusinessError('BAD_PRICE', f'{label}: giá quá lớn.')
    return value


def parse_months(raw, label: str) -> int:
    text = str(raw if raw is not None else '').strip() or '0'
    if not text.isdigit() or int(text) > 60:
        raise BusinessError('BAD_WARRANTY', f'{label}: bảo hành nhập số tháng từ 0 đến 60.')
    return int(text)


def parse_hours(raw, label: str) -> Decimal:
    text = str(raw if raw is not None else '').strip().replace(',', '.') or '0'
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise BusinessError('BAD_HOURS', f'{label}: giờ chuẩn nhập số, ví dụ 1,5.')
    if value < 0 or value > 99:
        raise BusinessError('BAD_HOURS', f'{label}: giờ chuẩn từ 0 đến 99.')
    return value.quantize(Decimal('0.1'))


def _text(raw, limit: int) -> str:
    return ' '.join(str(raw or '').split())[:limit]


def _note(raw, limit: int = 2000) -> str:
    return str(raw or '').strip()[:limit]


# ---------- hàng hóa ----------

def product_tab(p: Product) -> str:
    group = fold(p.group_name)
    if p.track_serial or any(w in group for w in PRINTER_WORDS):
        return 'may-in'
    probe = group or fold(p.name)
    if any(w in probe for w in INK_WORDS):
        return 'muc'
    return 'linh-kien'


def _last_price_changes(session) -> dict:
    """Mã hàng → (lúc, user_id) lần đổi giá bán gần nhất (tạo mã, sửa ở Hàng hóa, hoặc nhập Excel ở Bảng giá)."""
    rows = session.execute(select(AuditLog.entity_id, AuditLog.at, AuditLog.user_id)
                           .where(AuditLog.entity == 'product', AuditLog.action.in_(('product.create', 'product.update', 'product.price')),
                                  (AuditLog.before.is_(None)) | (AuditLog.before['sale_price'] != AuditLog.after['sale_price']))
                           .order_by(AuditLog.entity_id, AuditLog.at.desc()).distinct(AuditLog.entity_id))
    return {code: (at, uid) for code, at, uid in rows}


def product_rows(session, tab: str, q: str = '', nhom: str = '', chip: str = '', with_cost: bool = False) -> dict:
    products = list(session.scalars(select(Product).where(Product.active.is_(True), Product.is_test.is_(False),
                                                          Product.code != 'MAY-CU-CARE').order_by(Product.code)))
    counts = {t: 0 for t in PRODUCT_TABS}
    for p in products:
        counts[product_tab(p)] += 1
    mine = [p for p in products if product_tab(p) == tab]
    stock, cost = {}, {}
    for code, qty, value, avg in session.execute(
            select(StockBalance.product_code, func.sum(StockBalance.qty), func.sum(StockBalance.value), func.max(StockBalance.avg_cost))
            .join(Warehouse, Warehouse.code == StockBalance.warehouse_code).where(Warehouse.kind == 'KHO')
            .group_by(StockBalance.product_code)):
        stock[code] = qty or Decimal(0)
        cost[code] = int(value / qty) if qty else int(avg or 0)
    changes = _last_price_changes(session)
    names = dict(session.execute(select(User.id, User.full_name)).all()) if changes else {}
    since = datetime.now(UTC) - timedelta(days=30)
    rows = []
    for p in mine:
        c = cost.get(p.code) if with_cost else None
        margin = round((p.sale_price - c) * 100 / p.sale_price) if c and p.sale_price else None
        at, uid = changes.get(p.code, (p.created_at, None))
        rows.append(dict(code=p.code, name=p.name, group=p.group_name, unit=p.unit, warranty=p.warranty_months,
                         price=p.sale_price, vat=with_vat(p.sale_price), stock=stock.get(p.code), cost=c, margin=margin,
                         below=bool(c and p.sale_price < c), changed_at=at, changed_by=names.get(uid, ''),
                         recent=bool(at and at >= since), search=fold(f'{p.code} {p.name} {p.group_name}')))
    groups = sorted({r['group'] for r in rows if r['group']})
    chips = dict(duoi_von=sum(1 for r in rows if r['below']), doi_gia=sum(1 for r in rows if r['recent']))
    needle = fold(q)
    shown = [r for r in rows if (not nhom or r['group'] == nhom) and all(w in r['search'] for w in needle.split())
             and (chip != 'duoi-von' or r['below']) and (chip != 'doi-gia' or r['recent'])]
    return dict(rows=shown, counts=counts, groups=groups, chips=chips)


# ---------- nhóm máy, model ----------

def groups(session, active_only=True) -> list[RepairGroup]:
    stmt = select(RepairGroup).order_by(RepairGroup.sort, RepairGroup.id)
    if active_only:
        stmt = stmt.where(RepairGroup.active.is_(True))
    return list(session.scalars(stmt))


def load_group(session, group_id) -> RepairGroup:
    try:
        g = session.get(RepairGroup, int(group_id))
    except (TypeError, ValueError):
        g = None
    if not g:
        raise BusinessError('GROUP_REQUIRED', 'Chọn nhóm máy.')
    return g


def models(session, q: str = '', group_id: int | None = None) -> list[RepairModel]:
    stmt = select(RepairModel).order_by(RepairModel.name)
    if group_id:
        stmt = stmt.where(RepairModel.group_id == group_id)
    rows = list(session.scalars(stmt))
    needle = fold(q).split()
    return [m for m in rows if all(w in m.search_text for w in needle)]


def find_model(session, name: str) -> RepairModel | None:
    name = _text(name, 160)
    if not name:
        return None
    return session.scalar(select(RepairModel).where(func.lower(RepairModel.name) == name.lower()))


def save_model(session, user, name: str, group_id, note: str = '', model_id: int | None = None, active: bool = True) -> RepairModel:
    require_edit(user)
    name = _text(name, 160)
    if len(name) < 2:
        raise BusinessError('MODEL_NAME', 'Tên model cần ít nhất 2 ký tự, ví dụ Canon LBP 2900.')
    group = load_group(session, group_id)
    other = find_model(session, name)
    if other and other.id != model_id:
        raise BusinessError('DUPLICATE_MODEL', f'Đã có model "{other.name}".', status=409)
    if model_id:
        m = session.get(RepairModel, model_id)
        if not m:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy model.', status=404)
        before = dict(name=m.name, group=m.group_id, note=m.note, active=m.active)
        m.name, m.group_id, m.note, m.active = name, group.id, _note(note, 500), bool(active)
        action = 'repair_model.update'
    else:
        before = None
        m = RepairModel(name=name, group_id=group.id, note=_note(note, 500), active=True, created_by=user.id)
        session.add(m)
        action = 'repair_model.create'
    m.search_text = fold(f'{name} {group.name}')
    session.flush()
    audit.record(session, user, action, 'repair_model', str(m.id), before, dict(name=m.name, group=m.group_id, note=m.note, active=m.active))
    session.commit()
    return m


def known_model_names(session) -> list[str]:
    """Gợi ý tên model khi thêm: máy đang bán (theo dõi serial) và tên máy khách đã ghi trên Thiết bị."""
    from .models import Equipment
    names = set(session.scalars(select(Product.name).where(Product.track_serial.is_(True), Product.active.is_(True))))
    names |= set(session.scalars(select(Equipment.model_name).where(Equipment.model_name != '').distinct()))
    taken = {n.lower() for n in session.scalars(select(RepairModel.name))}
    return sorted(n for n in names if n and n.lower() not in taken)[:500]


# ---------- dịch vụ ----------

def services(session, kind: str, active_only: bool = False) -> list[PriceService]:
    stmt = select(PriceService).where(PriceService.kind == kind).order_by(PriceService.sort, PriceService.name)
    if active_only:
        stmt = stmt.where(PriceService.active.is_(True))
    return list(session.scalars(stmt))


def load_service(session, service_id) -> PriceService:
    try:
        s = session.get(PriceService, int(service_id))
    except (TypeError, ValueError):
        s = None
    if not s:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy dịch vụ.', status=404)
    return s


def customer_name(service: PriceService, model: str = '') -> str:
    """Tên in cho khách. Gói sửa: mặc định "<tên dịch vụ> máy <model>"; {model} trong mẫu được thay bằng model."""
    template = service.customer_name or (service.name + (' máy {model}' if service.kind == 'GOI' else ''))
    text = template.replace('{model}', model.strip()) if model.strip() else template.replace(' máy {model}', '').replace('{model}', '')
    return ' '.join(text.split())


def _service_snapshot(s: PriceService) -> dict:
    return dict(kind=s.kind, name=s.name, customer_name=s.customer_name, description=s.description, unit=s.unit, price=s.price,
                warranty_months=s.warranty_months, scope=[s.scope_1, s.scope_2, s.scope_3],
                hours=[str(s.hours_1), str(s.hours_2), str(s.hours_3)], active=s.active)


def _price_snapshot(rows) -> dict:
    return {str(r.level): dict(price=r.price, genuine=r.price_genuine, warranty=r.warranty_months) for r in rows}


def save_service(session, user, form: dict) -> PriceService:
    """Lưu dịch vụ (mới hoặc sửa). Gói sửa: kèm phạm vi/giờ chuẩn từng cấp, phương án kỹ thuật và giá các nhóm máy."""
    require_edit(user)
    kind = str(form.get('kind', '')).upper()
    sid = str(form.get('id', '')).strip()
    if sid:
        s = load_service(session, sid)
        kind = s.kind
        if str(form.get('version', '')) != str(s.version):
            raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa dịch vụ này, hãy tải lại trang.', status=409)
        before = _service_snapshot(s)
    elif kind in ('GOI', 'PHI'):
        s, before = PriceService(kind=kind), None
    else:
        raise BusinessError('BAD_KIND', 'Loại dịch vụ không hợp lệ.')
    name = _text(form.get('name'), 160)
    if len(name) < 2:
        raise BusinessError('SERVICE_NAME', 'Tên dịch vụ cần ít nhất 2 ký tự.')
    s.name = name
    s.customer_name = _text(form.get('customer_name'), 200)
    s.description = _note(form.get('description'))
    s.technical_note = _note(form.get('technical_note'))
    s.active = str(form.get('active', '1')) == '1'
    s.sort = int(str(form.get('sort', '0')).strip() or 0) if str(form.get('sort', '0')).strip().lstrip('-').isdigit() else 0
    if kind == 'PHI':
        s.unit = _text(form.get('unit'), 32) or 'lần'
        s.price = parse_money(form.get('price'), 'Giá', required=True)
        s.warranty_months = parse_months(form.get('warranty_months'), 'Bảo hành')
    else:
        for lv in LEVELS:
            setattr(s, f'scope_{lv}', _note(form.get(f'scope_{lv}'), 500))
            setattr(s, f'hours_{lv}', parse_hours(form.get(f'hours_{lv}'), f'Cấp {lv}'))
    s.search_text = fold(f'{s.name} {s.customer_name}')
    s.updated_by, s.updated_at = user.id, datetime.now(UTC)
    if before is None:
        session.add(s)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise BusinessError('DUPLICATE_SERVICE', f'Đã có dịch vụ "{name}".', status=409)
    except StaleDataError:
        session.rollback()
        raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa dịch vụ này, hãy tải lại trang.', status=409)
    if kind == 'GOI':
        _save_options(session, s, form)
        _save_group_prices(session, user, s, form)
    audit.record(session, user, 'price_service.update' if before else 'price_service.create', 'price_service', str(s.id),
                 before, _service_snapshot(s))
    session.commit()
    return s


def _save_options(session, s: PriceService, form: dict):
    rows = []
    for i in range(int(form.get('option_rows', 0) or 0) if str(form.get('option_rows', '0')).isdigit() else 0):
        name = _text(form.get(f'opt_name_{i}'), 160)
        if name:
            rows.append(RepairOption(service_id=s.id, name=name, part_hint=_text(form.get(f'opt_hint_{i}'), 200),
                                     is_main=str(form.get(f'opt_main_{i}', '')) == '1', sort=len(rows)))
    for old in session.scalars(select(RepairOption).where(RepairOption.service_id == s.id)):
        session.delete(old)
    session.flush()
    session.add_all(rows)


def _level_inputs(form: dict, prefix: str, label: str) -> dict:
    """Đọc 3 cấp từ form: {cấp: (giá, giá chính hãng, bảo hành)}; giá trống = cấp không áp dụng."""
    out = {}
    for lv in LEVELS:
        price = parse_money(form.get(f'{prefix}_p{lv}'), f'{label} cấp {lv}')
        genuine = parse_money(form.get(f'{prefix}_ch{lv}'), f'{label} cấp {lv} chính hãng')
        months = parse_months(form.get(f'{prefix}_bh{lv}'), f'{label} cấp {lv}')
        if price:
            if genuine is not None and genuine and genuine < price:
                raise BusinessError('BAD_PRICE', f'{label} cấp {lv}: giá chính hãng không thấp hơn giá thường.')
            out[lv] = (price, genuine or None, months)
        elif genuine:
            raise BusinessError('BAD_PRICE', f'{label} cấp {lv}: có giá chính hãng thì cần giá thường.')
    prices = [out[lv][0] for lv in sorted(out)]
    if prices != sorted(prices):
        raise BusinessError('BAD_PRICE', f'{label}: giá cấp sau không thấp hơn cấp trước.')
    return out


def _apply_levels(session, user, s: PriceService, levels: dict, group_id=None, model_id=None, label=''):
    where = (RepairPrice.service_id == s.id, RepairPrice.group_id == group_id) if group_id else \
        (RepairPrice.service_id == s.id, RepairPrice.model_id == model_id)
    current = list(session.scalars(select(RepairPrice).where(*where)))
    before = _price_snapshot(current)
    by_level = {r.level: r for r in current}
    now = datetime.now(UTC)
    for lv, row in by_level.items():
        if lv not in levels:
            session.delete(row)
    for lv, (price, genuine, months) in levels.items():
        row = by_level.get(lv)
        if row is None:
            session.add(RepairPrice(service_id=s.id, group_id=group_id, model_id=model_id, level=lv, price=price,
                                    price_genuine=genuine, warranty_months=months, updated_by=user.id, updated_at=now))
        elif (row.price, row.price_genuine, row.warranty_months) != (price, genuine, months):
            row.price, row.price_genuine, row.warranty_months, row.updated_by, row.updated_at = price, genuine, months, user.id, now
    session.flush()
    after = {str(lv): dict(price=p, genuine=g, warranty=m) for lv, (p, g, m) in sorted(levels.items())}
    if before != after:
        target = f'nhom:{group_id}' if group_id else f'model:{model_id}'
        audit.record(session, user, 'repair_price.save', 'price_service', str(s.id), dict(target=target, label=label, levels=before),
                     dict(target=target, label=label, levels=after))


def _save_group_prices(session, user, s: PriceService, form: dict):
    for g in groups(session, active_only=False):
        if f'g{g.id}_p1' not in form and f'g{g.id}_p2' not in form and f'g{g.id}_p3' not in form:
            continue
        _apply_levels(session, user, s, _level_inputs(form, f'g{g.id}', g.name), group_id=g.id, label=g.name)


def save_model_price(session, user, service_id, model_name: str, form: dict) -> RepairModel:
    require_edit(user)
    s = load_service(session, service_id)
    if s.kind != 'GOI':
        raise BusinessError('BAD_KIND', 'Giá riêng theo model chỉ dùng cho gói sửa chữa.')
    m = find_model(session, model_name)
    if not m:
        raise BusinessError('MODEL_NOT_FOUND', f'Model "{_text(model_name, 160)}" chưa có trong danh mục Model máy, thêm model trước.')
    levels = _level_inputs(form, 'm', m.name)
    if not levels:
        raise BusinessError('BAD_PRICE', 'Nhập giá ít nhất một cấp cho model.')
    _apply_levels(session, user, s, levels, model_id=m.id, label=m.name)
    session.commit()
    return m


def delete_model_price(session, user, service_id, model_id) -> None:
    require_edit(user)
    s = load_service(session, service_id)
    _apply_levels(session, user, s, {}, model_id=int(model_id), label=getattr(session.get(RepairModel, int(model_id)), 'name', ''))
    session.commit()


def options(session, service_id) -> list[RepairOption]:
    return list(session.scalars(select(RepairOption).where(RepairOption.service_id == service_id).order_by(RepairOption.sort, RepairOption.id)))


def level_by_options(main_count: int) -> int:
    """Phạm vi cấp theo số phương án chính đã chọn: 0 → cấp 1, 1 → cấp 2, từ 2 → cấp 3."""
    return 1 if main_count <= 0 else 2 if main_count == 1 else 3


def price_table(session, service_id) -> dict:
    """{'group': {group_id: {cấp: RepairPrice}}, 'model': {model_id: {cấp: RepairPrice}}}"""
    out = {'group': {}, 'model': {}}
    for r in session.scalars(select(RepairPrice).where(RepairPrice.service_id == service_id)):
        key, ref = ('group', r.group_id) if r.group_id else ('model', r.model_id)
        out[key].setdefault(ref, {})[r.level] = r
    return out


def package_rows(session, group_id: int, q: str = '') -> list[dict]:
    """Tab Gói sửa chữa: dịch vụ có giá cho nhóm máy đang chọn (và dịch vụ chưa có giá nhóm nào, để giám đốc điền)."""
    svcs = services(session, 'GOI')
    prices = {}
    overrides = {}
    for r in session.scalars(select(RepairPrice).where(RepairPrice.service_id.in_([s.id for s in svcs]) if svcs else False)):
        if r.group_id:
            prices.setdefault(r.service_id, {}).setdefault(r.group_id, {})[r.level] = r
        else:
            overrides.setdefault(r.service_id, set()).add(r.model_id)
    model_names = dict(session.execute(select(RepairModel.id, RepairModel.name).where(RepairModel.group_id == group_id)).all())
    needle = fold(q).split()
    rows = []
    for s in svcs:
        if not all(w in s.search_text for w in needle):
            continue
        by_group = prices.get(s.id, {})
        if by_group and group_id not in by_group:
            continue
        mine = by_group.get(group_id, {})
        rows.append(dict(service=s, levels=mine, overrides=[model_names[m] for m in overrides.get(s.id, ()) if m in model_names],
                         empty=not by_group))
    return rows


def lookup(session, service_id, model: str, level: int, genuine: bool = False) -> dict:
    """Tra giá gói: model ghi đè → giá nhóm của model → Cần xác minh. Không bao giờ đoán giá.

    Trả dict(ok, price, price_vat, warranty, source, reason, name). ok=False kèm reason khi cần xác minh.
    """
    s = load_service(session, service_id)
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 0
    base = dict(ok=False, price=None, price_vat=None, warranty=None, source='', service=s, level=level,
                name=customer_name(s, model or ''))
    if s.kind != 'GOI' or not s.active:
        return {**base, 'reason': 'Dịch vụ không phải gói sửa đang dùng.'}
    if level not in LEVELS:
        return {**base, 'reason': 'Chọn cấp 1, 2 hoặc 3.'}
    m = find_model(session, model or '')
    if not m or not m.active:
        return {**base, 'reason': 'Model chưa có trong danh mục Model máy, cần kỹ thuật xác minh.'}
    base['name'] = customer_name(s, m.name)
    base['model'] = m
    for source, cond in (('model', RepairPrice.model_id == m.id), ('nhom', RepairPrice.group_id == m.group_id)):
        row = session.scalar(select(RepairPrice).where(RepairPrice.service_id == s.id, cond, RepairPrice.level == level))
        if row is None:
            continue
        price = row.price_genuine if genuine else row.price
        if price is None:
            return {**base, 'reason': 'Chưa có giá linh kiện chính hãng cho cấp này, cần xác minh.', 'source': source}
        return {**base, 'ok': True, 'price': price, 'price_vat': with_vat(price), 'warranty': row.warranty_months, 'source': source}
    return {**base, 'reason': 'Nhóm máy của model chưa có giá cấp này, cần kỹ thuật xác minh.'}


# ---------- hiện tượng ----------

def symptoms(session, q: str = '') -> list[RepairSymptom]:
    rows = list(session.scalars(select(RepairSymptom).order_by(RepairSymptom.name)))
    needle = fold(q).split()
    return [r for r in rows if all(w in r.search_text for w in needle)]


def save_symptom(session, user, form: dict) -> RepairSymptom | None:
    require_edit(user)
    sid = str(form.get('id', '')).strip()
    row = session.get(RepairSymptom, int(sid)) if sid.isdigit() else None
    if sid and not row:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy hiện tượng.', status=404)
    before = _symptom_snapshot(row) if row else None
    if form.get('xoa') == '1' and row:
        audit.record(session, user, 'repair_symptom.delete', 'repair_symptom', str(row.id), before, None)
        session.delete(row)
        session.commit()
        return None
    name = _text(form.get('name'), 200)
    if len(name) < 3:
        raise BusinessError('SYMPTOM_NAME', 'Hiện tượng cần ít nhất 3 ký tự, ví dụ "In bị nhăn giấy".')
    service = load_service(session, form.get('service_id'))
    if service.kind != 'GOI':
        raise BusinessError('BAD_KIND', 'Hiện tượng chỉ gắn với gói sửa chữa.')
    lo, hi = str(form.get('level_min', '1')), str(form.get('level_max', '3'))
    if lo not in ('1', '2', '3') or hi not in ('1', '2', '3') or int(hi) < int(lo):
        raise BusinessError('BAD_LEVEL', 'Khoảng cấp không hợp lệ (cấp đến phải từ cấp bắt đầu trở lên).')
    gid = str(form.get('group_id', '')).strip()
    group = load_group(session, gid) if gid else None
    if row is None:
        row = RepairSymptom()
        session.add(row)
    row.name, row.service_id, row.group_id = name, service.id, group.id if group else None
    row.level_min, row.level_max, row.note = int(lo), int(hi), _note(form.get('note'), 500)
    row.active = str(form.get('active', '1')) == '1'
    row.search_text = fold(f'{name} {service.name}')
    session.flush()
    audit.record(session, user, 'repair_symptom.save', 'repair_symptom', str(row.id), before, _symptom_snapshot(row))
    session.commit()
    return row


def _symptom_snapshot(r: RepairSymptom) -> dict:
    return dict(name=r.name, service=r.service_id, group=r.group_id, levels=[r.level_min, r.level_max], active=r.active)


# ---------- lịch sử ----------

def history(session, offset: int = 0, size: int = 25) -> tuple[list[dict], int]:
    cond = (AuditLog.action.in_(PRICE_ACTIONS)) | (
        (AuditLog.action == 'product.update') & (AuditLog.before['sale_price'] != AuditLog.after['sale_price']))
    total = session.scalar(select(func.count()).select_from(AuditLog).where(cond)) or 0
    rows = list(session.scalars(select(AuditLog).where(cond).order_by(AuditLog.at.desc(), AuditLog.id.desc()).offset(offset).limit(size)))
    users = dict(session.execute(select(User.id, User.full_name)).all())
    svc_names = dict(session.execute(select(PriceService.id, PriceService.name)).all())
    out = []
    for r in rows:
        out.append(dict(at=r.at, who=users.get(r.user_id, ''), what=_describe(r, svc_names), reason=r.reason))
    return out, total


def _fmt(v) -> str:
    return '—' if v in (None, '') else f'{int(v):,}'.replace(',', '.')


def _describe(r: AuditLog, svc_names: dict) -> str:
    b, a = r.before or {}, r.after or {}
    if r.action in ('product.update', 'product.price'):
        return f'{r.entity_id} {a.get("name", "")}: giá bán {_fmt(b.get("sale_price"))} → {_fmt(a.get("sale_price"))}'
    if r.action == 'repair_price.save':
        name = svc_names.get(int(r.entity_id), r.entity_id)
        parts = []
        for lv in ('1', '2', '3'):
            old, new = (b.get('levels') or {}).get(lv), (a.get('levels') or {}).get(lv)
            if old != new:
                parts.append(f'C{lv} {_fmt((old or {}).get("price"))} → {_fmt((new or {}).get("price"))}')
        where = a.get('label') or b.get('label') or ''
        return f'{name} · {"model " if str(a.get("target", "")).startswith("model") else ""}{where}: ' + ('; '.join(parts) or 'đổi giá chính hãng / bảo hành')
    if r.action.startswith('price_service'):
        if not b:
            return f'Thêm dịch vụ "{a.get("name")}"' + (f' giá {_fmt(a.get("price"))}' if a.get('kind') == 'PHI' else '')
        if a.get('kind') == 'PHI' and b.get('price') != a.get('price'):
            return f'{a.get("name")}: giá {_fmt(b.get("price"))} → {_fmt(a.get("price"))}'
        return f'Sửa thông tin dịch vụ "{a.get("name")}"'
    if r.action.startswith('repair_model'):
        return ('Thêm model ' if not b else 'Sửa model ') + str(a.get('name', ''))
    if r.action == 'repair_symptom.delete':
        return f'Xóa hiện tượng "{b.get("name")}"'
    if r.action == 'repair_symptom.save':
        return ('Thêm hiện tượng "' if not b else 'Sửa hiện tượng "') + str(a.get('name', '')) + '"'
    return r.action


# ---------- Excel (CSV UTF-8) ----------

CSV_HEAD = {
    'hang': ['Mã hàng', 'Tên hàng', 'Nhóm', 'Giá chưa VAT'],
    'dich-vu': ['Dịch vụ', 'Đơn vị', 'Giá chưa VAT', 'Bảo hành (tháng)'],
    'goi': ['Dịch vụ', 'Nhóm máy hoặc Model', 'Cấp', 'Giá chưa VAT', 'Giá chính hãng', 'Bảo hành (tháng)'],
}


def csv_kind(tab: str) -> str:
    return 'hang' if tab in PRODUCT_TABS else tab if tab in ('dich-vu', 'goi') else ''


def export_csv(session, tab: str) -> str:
    kind = csv_kind(tab)
    if not kind:
        raise BusinessError('BAD_TAB', 'Tab này không xuất Excel.')
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEAD[kind])
    if kind == 'hang':
        for r in product_rows(session, tab)['rows']:
            w.writerow([r['code'], r['name'], r['group'], r['price']])
    elif kind == 'dich-vu':
        for s in services(session, 'PHI'):
            w.writerow([s.name, s.unit, s.price, s.warranty_months])
    else:
        gnames = {g.id: g.name for g in groups(session, active_only=False)}
        mnames = dict(session.execute(select(RepairModel.id, RepairModel.name)).all())
        for s in services(session, 'GOI'):
            table = price_table(session, s.id)
            for key, names in (('group', gnames), ('model', mnames)):
                for ref, levels in sorted(table[key].items(), key=lambda kv: names.get(kv[0], '')):
                    for lv, r in sorted(levels.items()):
                        w.writerow([s.name, names.get(ref, ''), lv, r.price, r.price_genuine or '', r.warranty_months])
    return '﻿' + buf.getvalue()


def _read_csv(raw: bytes) -> list[list[str]]:
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise BusinessError('BAD_FILE', 'File cần lưu dạng "CSV UTF-8" (Excel: Lưu thành → CSV UTF-8).')
    first = text.split('\n', 1)[0]
    delim = ';' if first.count(';') > first.count(',') else ','
    rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]
    return [r for r in rows if any(r)]


def plan_import(session, tab: str, raw: bytes) -> dict:
    """Đọc file, so với giá hiện tại, trả danh sách thay đổi để giám đốc xem trước. Chưa ghi gì."""
    kind = csv_kind(tab)
    if not kind:
        raise BusinessError('BAD_TAB', 'Tab này không nhập Excel.')
    if len(raw) > 2_000_000:
        raise BusinessError('BAD_FILE', 'File quá lớn (tối đa 2 MB).')
    rows = _read_csv(raw)
    if not rows or [c.lower() for c in rows[0][:len(CSV_HEAD[kind])]] != [c.lower() for c in CSV_HEAD[kind]]:
        raise BusinessError('BAD_FILE', 'Dòng đầu file phải đúng tiêu đề: ' + ' | '.join(CSV_HEAD[kind]) + '. Hãy tải file mẫu ở nút Tải Excel.')
    changes, errors = [], []
    for n, row in enumerate(rows[1:], start=2):
        row = row + [''] * (len(CSV_HEAD[kind]) - len(row))
        try:
            change = _plan_row(session, kind, row)
        except BusinessError as exc:
            errors.append(f'Dòng {n}: {exc.message}')
            continue
        if change:
            changes.append(change)
    return dict(kind=kind, changes=changes, errors=errors)


def _plan_row(session, kind: str, row: list[str]) -> dict | None:
    if kind == 'hang':
        p = session.get(Product, row[0].upper())
        if not p:
            raise BusinessError('NOT_FOUND', f'không có mã hàng {row[0]}.')
        price = parse_money(row[3], 'Giá', required=True)
        return None if price == p.sale_price else dict(key=p.code, label=f'{p.code} {p.name}', old=p.sale_price, new=price)
    if kind == 'dich-vu':
        s = session.scalar(select(PriceService).where(PriceService.kind == 'PHI', func.lower(PriceService.name) == row[0].lower()))
        price = parse_money(row[2], 'Giá', required=True)
        months = parse_months(row[3], 'Bảo hành')
        old = (s.price, s.warranty_months) if s else None
        return None if old == (price, months) else dict(key=row[0], label=row[0] + ('' if s else ' (mới)'), old=s.price if s else None,
                                                        new=price, unit=row[1] or 'lần', months=months)
    s = session.scalar(select(PriceService).where(PriceService.kind == 'GOI', func.lower(PriceService.name) == row[0].lower()))
    if not s:
        raise BusinessError('NOT_FOUND', f'chưa có gói "{row[0]}", thêm dịch vụ trên app trước.')
    g = session.scalar(select(RepairGroup).where(func.lower(RepairGroup.name) == row[1].lower()))
    m = None if g else find_model(session, row[1])
    if not g and not m:
        raise BusinessError('NOT_FOUND', f'"{row[1]}" không phải nhóm máy hay model trong danh mục.')
    if row[2] not in ('1', '2', '3'):
        raise BusinessError('BAD_LEVEL', 'cấp là 1, 2 hoặc 3.')
    lv = int(row[2])
    price = parse_money(row[3], 'Giá', required=True)
    genuine = parse_money(row[4], 'Giá chính hãng')
    months = parse_months(row[5], 'Bảo hành')
    cond = RepairPrice.group_id == g.id if g else RepairPrice.model_id == m.id
    cur = session.scalar(select(RepairPrice).where(RepairPrice.service_id == s.id, cond, RepairPrice.level == lv))
    if cur and (cur.price, cur.price_genuine, cur.warranty_months) == (price, genuine or None, months):
        return None
    return dict(key=f'{s.id}|{"g" if g else "m"}{(g or m).id}|{lv}', label=f'{s.name} · {(g or m).name} · cấp {lv}',
                old=cur.price if cur else None, new=price, genuine=genuine or None, months=months)


def apply_import(session, user, tab: str, raw: bytes) -> int:
    """Ghi các thay đổi đã xem trước. File có dòng lỗi thì không ghi gì."""
    require_edit(user)
    plan = plan_import(session, tab, raw)
    if plan['errors']:
        raise BusinessError('IMPORT_ERRORS', 'File còn dòng lỗi, sửa file rồi tải lại: ' + '; '.join(plan['errors'][:5]))
    kind = plan['kind']
    for c in plan['changes']:
        if kind == 'hang':
            p = session.get(Product, c['key'])
            before = dict(name=p.name, sale_price=p.sale_price)
            p.sale_price = c['new']
            audit.record(session, user, 'product.price', 'product', p.code, before, dict(name=p.name, sale_price=p.sale_price), reason='Nhập Excel bảng giá')
        elif kind == 'dich-vu':
            s = session.scalar(select(PriceService).where(PriceService.kind == 'PHI', func.lower(PriceService.name) == c['key'].lower()))
            before = _service_snapshot(s) if s else None
            if not s:
                s = PriceService(kind='PHI', name=_text(c['key'], 160), search_text=fold(c['key']))
                session.add(s)
            s.price, s.unit, s.warranty_months = c['new'], _text(c['unit'], 32) or 'lần', c['months']
            s.updated_by, s.updated_at = user.id, datetime.now(UTC)
            session.flush()
            audit.record(session, user, 'price_service.update' if before else 'price_service.create', 'price_service', str(s.id),
                         before, _service_snapshot(s), reason='Nhập Excel bảng giá')
        else:
            sid, target, lv = c['key'].split('|')
            s = session.get(PriceService, int(sid))
            gid = int(target[1:]) if target[0] == 'g' else None
            mid = int(target[1:]) if target[0] == 'm' else None
            cond = RepairPrice.group_id == gid if gid else RepairPrice.model_id == mid
            levels = {r.level: (r.price, r.price_genuine, r.warranty_months)
                      for r in session.scalars(select(RepairPrice).where(RepairPrice.service_id == s.id, cond))}
            levels[int(lv)] = (c['new'], c['genuine'], c['months'])
            label = session.get(RepairGroup, gid).name if gid else session.get(RepairModel, mid).name
            _apply_levels(session, user, s, levels, group_id=gid, model_id=mid, label=label)
    session.commit()
    return len(plan['changes'])

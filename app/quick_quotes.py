"""Báo giá nhanh HG (TUẤN duyệt mẫu B1 14/11/2026, đợt 2 quy trình báo giá sửa chữa).

Khách hỏi giá qua Zalo/điện thoại khi chưa gửi máy. Nhân viên chọn model, hiện tượng, sửa ở đâu:
- App chỉ hiện các dịch vụ + cấp đã khai báo ở tab Hiện tượng (không tự lấy cấp 1 đến cấp 3), giá gồm VAT lấy thẳng
  từ bảng giá chung (price_book.lookup: model ghi đè → giá nhóm). Không có giá thì "Cần kỹ thuật xác nhận", không đoán.
- Các dịch vụ thay thế nhau tùy nguyên nhân, không cộng lại. Tin gửi khách không có linh kiện.
- Tận nơi: thêm phụ phí có chữ "tận nơi". Phí kiểm tra (dịch vụ có chữ "kiểm tra") luôn ghi trong tin.
- Lưu ảnh chụp giá lúc báo (lines, text); bảng giá đổi sau không làm sai tin đã gửi.
- Khách đồng ý: Chuyển thành phiếu nhận máy (quầy) hoặc Hẹn thợ tới (nhận đơn dịch vụ), điền sẵn, không nhập lại.
- Không trừ kho, không tạo nợ, không gửi tự động: người làm bấm Sao chép hoặc Gửi Zalo công ty.
Bản 2 (TUẤN duyệt mẫu HG1–HG3 14/11/2026): lưới ô cấp 1/2/3/chính hãng bật/tắt; giám đốc sửa giá ô (giữ giá bảng);
hạng mục ngoài bảng giá; tin ghi khoảng hoặc từng cấp + link /hg/{mã}; sửa phiếu giữ số HG; bản in A4.
"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import audit, doc_numbers
from . import price_book as pb
from .errors import BusinessError
from .models import Customer, PriceService, QuickQuote, RepairGroup, RepairPrice, RepairSymptom, ServiceTicket, User
from .security import has_role
from .service_sla import TZ
from .text import fold

ROLES = ('sales', 'dispatcher', 'reception')
PREFIX = 'HG'
PLACES = {'CUA_HANG': 'Tại cửa hàng', 'TAN_NOI': 'Tận nơi'}
STATUSES = {'MOI': 'Chờ khách trả lời', 'DA_GUI': 'Đã gửi khách', 'CHUYEN_PHIEU': 'Đã chuyển phiếu', 'KHONG_SUA': 'Khách không sửa'}
VIEWS = {'cho': ('Chờ khách', ('MOI', 'DA_GUI')), 'chuyen': ('Đã chuyển phiếu', ('CHUYEN_PHIEU',)), 'khong': ('Không sửa', ('KHONG_SUA',))}
OPEN = ('MOI', 'DA_GUI')
MAX_TEXT = 2000
MAX_SYMPTOMS = 8
VERIFY = 'Cần kỹ thuật xác nhận'


def _now():
    return datetime.now(TZ)


def money(value) -> str:
    return f'{int(value or 0):,}'.replace(',', '.')


def can_use(user: User) -> bool:
    return bool(user and user.active and has_role(user, *ROLES))


def require(user: User):
    if not can_use(user):
        raise BusinessError('FORBIDDEN', 'Báo giá nhanh dành cho kinh doanh, điều phối, lễ tân và giám đốc.', status=403)


# ---------- tính giá ----------

def symptom_names(session: Session, model=None) -> list[str]:
    """Các hiện tượng đang dùng, áp được cho nhóm máy của model (hiện tượng không gắn nhóm áp mọi nhóm)."""
    stmt = select(RepairSymptom.name, RepairSymptom.group_id).join(PriceService, PriceService.id == RepairSymptom.service_id) \
        .where(RepairSymptom.active.is_(True), PriceService.active.is_(True))
    names = {}
    for name, gid in session.execute(stmt).all():
        if model is None or gid is None or gid == model.group_id:
            names.setdefault(name.lower(), name)
    return sorted(names.values(), key=lambda n: fold(n))


def _fee(session: Session, *words) -> list[PriceService]:
    return [s for s in pb.services(session, 'PHI', active_only=True) if any(w in fold(s.name) for w in words)]


LEVEL_KEYS = ('1', '2', '3', 'CH')
LEVEL_NAMES = {'1': 'cấp 1', '2': 'cấp 2', '3': 'cấp 3', 'CH': 'chính hãng'}
LEVEL_SHORT = {'1': 'Cấp 1', '2': 'Cấp 2', '3': 'Cấp 3', 'CH': 'Chính hãng'}
LEVEL_HINT = {'1': 'Nhẹ', '2': 'Trung bình', '3': 'Nặng', 'CH': 'LK hãng · cấp 2'}
MODES = {'KHOANG': 'Khoảng giá (từ – đến)', 'CAP': 'Từng cấp'}
MAX_CUSTOM = 5
MAX_PRICE = 500_000_000
VALID_DAYS = 7


def _int(text) -> int | None:
    digits = ''.join(ch for ch in str(text or '') if ch.isdigit())
    return int(digits[:12]) if digits else None


def parse_options(form) -> dict:
    """Lựa chọn trên lưới cấp (mẫu HG2 duyệt 14/11/2026): ô cấp bật/tắt, giá giám đốc sửa, hạng mục ngoài bảng giá."""
    getlist = form.getlist if hasattr(form, 'getlist') else (lambda k: form.get(k) if isinstance(form.get(k), list) else ([form[k]] if k in form else []))
    seen = {_int(v) for v in getlist('cap_sid')} - {None}
    picks = {sid: set() for sid in seen}
    for v in getlist('cap'):
        sid, _, lv = str(v).partition(':')
        sid = _int(sid)
        if sid in picks and lv in LEVEL_KEYS:
            picks[sid].add(lv)
    overrides = {}
    for key in (form.keys() if hasattr(form, 'keys') else []):
        if str(key).startswith('gia_'):
            parts = str(key).split('_')
            if len(parts) == 3 and _int(parts[1]) is not None and parts[2] in LEVEL_KEYS:
                val = _int(form.get(key))
                if val is not None:
                    overrides[(_int(parts[1]), parts[2])] = val
    names, prices, months = getlist('hm_ten'), getlist('hm_gia'), getlist('hm_bh')
    customs = []
    for i, name in enumerate(names[:20]):
        name = ' '.join(str(name or '').split())[:200]
        price = _int(prices[i]) if i < len(prices) else None
        warranty = _int(months[i]) if i < len(months) else None
        if name or price:
            customs.append(dict(name=name, price=price, warranty=warranty or 0))
    mode = str(form.get('cach', '') or 'KHOANG')
    return dict(picks=picks if seen else None, overrides=overrides, customs=customs, mode=mode if mode in MODES else 'KHOANG')


def check_customs(customs: list[dict]) -> list[dict]:
    if len(customs) > MAX_CUSTOM:
        raise BusinessError('BAD_ITEM', f'Tối đa {MAX_CUSTOM} hạng mục ngoài bảng giá.')
    out = []
    for c in customs:
        if len(c['name']) < 2:
            raise BusinessError('BAD_ITEM', 'Hạng mục ngoài bảng giá: nhập tên việc sửa.')
        if not c['price'] or c['price'] < 1000 or c['price'] > MAX_PRICE:
            raise BusinessError('BAD_ITEM', f'Hạng mục "{c["name"]}": nhập giá gồm VAT (từ 1.000đ).')
        if not 0 <= (c['warranty'] or 0) <= 36:
            raise BusinessError('BAD_ITEM', f'Hạng mục "{c["name"]}": bảo hành 0–36 tháng.')
        out.append(dict(custom=True, name=c['name'], price=c['price'], warranty=c['warranty'] or 0, ok=True,
                        low=c['price'], high=c['price'], levels=[], cells={}, reason=''))
    return out


def compute(session: Session, model_name: str, symptoms: list[str], place: str, picks: dict | None = None,
            overrides: dict | None = None, customs: list[dict] | None = None) -> dict:
    """Giá tham khảo cho các hiện tượng đã chọn. Mỗi dịch vụ một dòng với đủ ô cấp 1/2/3/chính hãng có giá;
    mặc định bật đúng cấp đã khai (level_min..level_max). picks {service_id: {'1','CH'..}} thay mặc định cho dịch vụ đã thấy,
    overrides {(service_id, cấp): giá gồm VAT} là giá giám đốc sửa riêng cho phiếu."""
    model_name = ' '.join(str(model_name or '').split())[:160]
    place = place if place in PLACES else 'CUA_HANG'
    picks, overrides = picks or {}, overrides or {}
    chosen = []
    for s in symptoms[:MAX_SYMPTOMS]:
        s = ' '.join(str(s or '').split())[:200]
        if s and s.lower() not in [c.lower() for c in chosen]:
            chosen.append(s)
    model = pb.find_model(session, model_name)
    model = model if model and model.active else None
    group = session.get(RepairGroup, model.group_id) if model else None
    ranges = {}
    if chosen:
        rows = session.scalars(select(RepairSymptom).where(RepairSymptom.active.is_(True),
                                                           func.lower(RepairSymptom.name).in_([c.lower() for c in chosen]))
                               .order_by(RepairSymptom.id))
        for r in rows:
            if model is not None and r.group_id is not None and r.group_id != model.group_id:
                continue
            lo, hi = ranges.get(r.service_id, (r.level_min, r.level_max))
            ranges[r.service_id] = (min(lo, r.level_min), max(hi, r.level_max))
    lines, overridden = [], []
    for sid, (lo, hi) in ranges.items():
        s = session.get(PriceService, sid)
        if s is None or s.kind != 'GOI' or not s.active:
            continue
        if model is not None and not session.scalar(select(RepairPrice.id).where(
                RepairPrice.service_id == sid, or_(RepairPrice.group_id == model.group_id, RepairPrice.model_id == model.id)).limit(1)):
            continue  # nhóm máy này không làm dịch vụ (cả 3 cấp trống, không giá riêng model): không hiện
        want = picks.get(sid)
        if want is None:
            want = {str(lv) for lv in range(lo, hi + 1)}
        cells, reason = {}, ''
        for key in LEVEL_KEYS:
            r = pb.lookup(session, sid, model.name if model else model_name, 2 if key == 'CH' else int(key), genuine=key == 'CH')
            if not r['ok']:
                if key != 'CH' and lo <= int(key) <= hi:
                    reason = reason or r['reason']
                cells[key] = None
                continue
            base = r['price_vat']
            price = overrides.get((sid, key), base)
            if not 1000 <= price <= MAX_PRICE:
                raise BusinessError('BAD_PRICE', f'{s.name} {LEVEL_NAMES[key]}: giá phải từ 1.000đ.')
            if price != base:
                overridden.append((sid, key))
            cells[key] = dict(price=price, base=base, warranty=r['warranty'] or 0, on=key in want)
        on = [k for k in LEVEL_KEYS if cells[k] and cells[k]['on']]
        prices = [cells[k]['price'] for k in on]
        line = dict(service_id=sid, name=s.name, sort=s.sort, cells=cells, levels=[int(k) for k in on if k != 'CH'],
                    genuine='CH' in on, declared=[lo, hi], ok=bool(prices), low=min(prices) if prices else None,
                    high=max(prices) if prices else None, reason='' if prices else (reason or VERIFY))
        lines.append(line)
    lines.sort(key=lambda x: (not x['ok'], x['sort'], fold(x['name'])))
    fees = [dict(name=f.name, price=pb.with_vat(f.price), note=(f.description or '').strip()[:160]) for f in _fee(session, 'kiem tra')]
    trip = sorted((dict(name=f.name, price=pb.with_vat(f.price), note='') for f in _fee(session, 'tan noi')), key=lambda x: x['price']) if place == 'TAN_NOI' else []
    extra = check_customs(customs or [])
    return dict(model_name=model.name if model else model_name, model=model, group=group, symptoms=chosen, place=place,
                lines=lines, customs=extra, check_fees=fees, trip_fees=trip, overridden=overridden,
                unknown_model=bool(model_name) and model is None)


def range_text(line: dict) -> str:
    if not line['ok']:
        return VERIFY
    if line['low'] == line['high']:
        return money(line['low'])
    return f"{money(line['low'])} – {money(line['high'])}"


def levels_text(line: dict) -> str:
    if line.get('custom'):
        return 'ngoài bảng giá'
    lv = line['levels'] or ([] if line.get('genuine') else list(range(line['declared'][0], line['declared'][1] + 1)))
    text = '' if not lv else (f'C{lv[0]}' if len(lv) == 1 else f'C{lv[0]}–C{lv[-1]}')
    if line.get('genuine'):
        text = (text + ' + ' if text else '') + 'CH'
    return text


def levels_list_text(line: dict) -> str:
    """Từng cấp: "cấp 1 486.000đ · cấp 2 675.000đ"."""
    cells = line.get('cells') or {}
    return ' · '.join(f"{LEVEL_NAMES[k]} {money(cells[k]['price'])}đ" for k in LEVEL_KEYS if cells.get(k) and cells[k]['on'])


def zalo_text(result: dict, mode: str = 'KHOANG', link: str = '') -> str:
    model = result['model_name'] or 'của anh/chị'
    sym = ', '.join(s[:1].lower() + s[1:] for s in result['symptoms'])
    head = f'Dạ, máy {model}' + (f' bị {sym}' if sym else '')
    priced = [ln for ln in result['lines'] if ln['ok']]
    extra = result.get('customs') or []
    out = []
    if priced or extra:
        out.append(head + ' thì chi phí tùy nguyên nhân và tình trạng máy. Giá tham khảo bên em (đã gồm VAT):')
        for ln in priced:
            if mode == 'CAP' and ln.get('cells'):
                out.append(f"• {ln['name']}: {levels_list_text(ln)}")
            else:
                out.append(f"• {ln['name']}: " + (f"khoảng {money(ln['low'])}đ" if ln['low'] == ln['high'] else f"{money(ln['low'])} – {money(ln['high'])}đ"))
        for ln in extra:
            out.append(f"• {ln['name']}: {money(ln['price'])}đ")
    else:
        out.append(head + ' thì bên em cần kỹ thuật kiểm tra máy mới báo được chi phí chính xác.')
    for f in result['trip_fees']:
        out.append(f"• {f['name']}: {money(f['price'])}đ")
    for f in result['check_fees']:
        note = f['note'].rstrip('.')
        out.append(f"{f['name']} {money(f['price'])}đ" + (f", {note[:1].lower() + note[1:]}." if note else '.'))
    out.append('Sau khi kiểm tra, Thành Đức sẽ báo chi phí và thời gian cụ thể, chỉ sửa khi anh/chị đồng ý ạ.')
    if link:
        out.append(f'Xem báo giá chi tiết: {link}')
    return '\n'.join(out)[:MAX_TEXT]


# ---------- lưu và xử lý ----------

def _customer(session: Session, code: str) -> Customer | None:
    code = str(code or '').strip()[:16]
    if not code:
        return None
    c = session.get(Customer, code)
    if c is None or c.status != 'DANG_DUNG':
        raise BusinessError('BAD_CUSTOMER', 'Khách hàng không có hoặc đã ngừng.')
    return c


def _clean_text(text) -> str:
    text = str(text or '').replace('\r\n', '\n').strip()
    if len(text) > MAX_TEXT:
        raise BusinessError('BAD_TEXT', f'Tin dài quá {MAX_TEXT} ký tự, Zalo không gửi được. Hãy rút ngắn.')
    return text


def is_director(user: User) -> bool:
    return bool(user and has_role(user, 'director'))


def link(row: QuickQuote) -> str:
    from .dispatch_alerts import base_url
    return f'{base_url()}/hg/{row.token}' if row.token else ''


def valid_until(row: QuickQuote):
    return ((row.updated_at or row.created_at).astimezone(TZ) + timedelta(days=VALID_DAYS)).date()


def _with_link(body: str, url: str) -> str:
    if not url or url in body:
        return body
    return _clean_text(body.rstrip() + f'\nXem báo giá chi tiết: {url}')


def _build(session: Session, user: User, model_name, symptoms, place, options: dict | None):
    if len(' '.join(str(model_name or '').split())) < 2:
        raise BusinessError('BAD_MODEL', 'Nhập model máy khách hỏi, ví dụ Canon LBP 2900.')
    options = options or {}
    result = compute(session, model_name, symptoms, place, options.get('picks'), options.get('overrides'), options.get('customs'))
    if not result['symptoms']:
        raise BusinessError('BAD_SYMPTOM', 'Chọn ít nhất một hiện tượng khách kể.')
    if result['overridden'] and not is_director(user):
        raise BusinessError('DIRECTOR_ONLY', 'Chỉ giám đốc sửa giá từng ô. Hãy để giá theo bảng giá chung.', status=403)
    lines = [{k: v for k, v in ln.items() if k != 'sort'} for ln in result['lines']]
    lines += result['customs'] + [dict(fee=True, **f) for f in result['trip_fees'] + result['check_fees']]
    return result, lines


def _summary(lines):
    return [[ln['name'], ln.get('low'), ln.get('high'), levels_text(ln) if not ln.get('fee') else ''] for ln in lines if not ln.get('fee')]


def create(session: Session, user: User, *, model_name: str, symptoms: list[str], place: str, customer_code: str = '',
           contact: str = '', note: str = '', text: str | None = None, options: dict | None = None) -> QuickQuote:
    require(user)
    result, lines = _build(session, user, model_name, symptoms, place, options)
    customer = _customer(session, customer_code)
    mode = (options or {}).get('mode', 'KHOANG')
    row = QuickQuote(number=doc_numbers.next_number(session, PREFIX, _now().date()), customer_code=customer.code if customer else None,
                     contact=' '.join(str(contact or '').split())[:200], model_name=result['model_name'],
                     model_id=result['model'].id if result['model'] else None, place=result['place'], symptoms=result['symptoms'],
                     note=str(note or '').strip()[:2000], lines=lines, price_mode=mode, token=secrets.token_urlsafe(18),
                     text='', created_by=user.id)
    url = link(row)
    row.text = _with_link(_clean_text(text), url) if text is not None and str(text).strip() else zalo_text(result, mode, url)
    session.add(row)
    session.flush()
    audit.record(session, user, 'quick_quote.create', 'quick_quote', str(row.id), None,
                 dict(number=row.number, model=row.model_name, symptoms=row.symptoms, place=row.place, customer=row.customer_code or '',
                      mode=mode, lines=_summary(lines), overridden=[f'{sid}:{lv}' for sid, lv in result['overridden']]))
    session.commit()
    return row


def update(session: Session, user: User, qid: int, *, model_name: str, symptoms: list[str], place: str, customer_code: str = '',
           contact: str = '', note: str = '', text: str | None = None, options: dict | None = None) -> QuickQuote:
    """Sửa phiếu (mẫu HG2): giữ số HG, chỉ khi chờ khách. Tin đã gửi thì về Chờ khách trả lời để gửi lại tin mới."""
    row = _open(session, user, qid)
    result, lines = _build(session, user, model_name, symptoms, place, options)
    customer = _customer(session, customer_code)
    mode = (options or {}).get('mode', 'KHOANG')
    before = dict(model=row.model_name, symptoms=row.symptoms, place=row.place, customer=row.customer_code or '', mode=row.price_mode,
                  status=row.status, lines=_summary(row.lines))
    row.customer_code = customer.code if customer else None
    row.contact = ' '.join(str(contact or '').split())[:200]
    row.model_name, row.model_id = result['model_name'], result['model'].id if result['model'] else None
    row.place, row.symptoms, row.note = result['place'], result['symptoms'], str(note or '').strip()[:2000]
    row.lines, row.price_mode = lines, mode
    row.token = row.token or secrets.token_urlsafe(18)
    url = link(row)
    row.text = _with_link(_clean_text(text), url) if text is not None and str(text).strip() else zalo_text(result, mode, url)
    if row.status == 'DA_GUI':
        row.status = 'MOI'
    row.updated_at, row.updated_by, row.edits = _now(), user.id, (row.edits or 0) + 1
    audit.record(session, user, 'quick_quote.update', 'quick_quote', str(row.id), before,
                 dict(model=row.model_name, symptoms=row.symptoms, place=row.place, customer=row.customer_code or '', mode=mode,
                      status=row.status, lines=_summary(lines), overridden=[f'{sid}:{lv}' for sid, lv in result['overridden']]))
    session.commit()
    return row


def from_row(row: QuickQuote) -> dict:
    """Điền sẵn form sửa từ phiếu đã lưu: cấp đã bật, giá giám đốc đã sửa, hạng mục ngoài bảng giá."""
    picks, overrides, customs = {}, {}, []
    for ln in row.lines:
        if ln.get('fee'):
            continue
        if ln.get('custom'):
            customs.append(dict(name=ln['name'], price=ln['price'], warranty=ln.get('warranty') or 0))
            continue
        cells = ln.get('cells')
        if cells is None:  # phiếu bản 1: chỉ có danh sách cấp
            picks[ln['service_id']] = {str(lv) for lv in ln.get('levels') or []}
            continue
        picks[ln['service_id']] = {k for k, c in cells.items() if c and c.get('on')}
        overrides.update({(ln['service_id'], k): c['price'] for k, c in cells.items() if c and c['price'] != c['base']})
    return dict(model=row.model_name, ht=list(row.symptoms), noi=row.place, khach=row.customer_code or '', lien_he=row.contact,
                ghi_chu=row.note, text_edited='', text='', options=dict(picks=picks, overrides=overrides, customs=customs, mode=row.price_mode))


def public(session: Session, token: str) -> tuple[QuickQuote | None, str]:
    """Link khách: ok | het_han | khong_co. Phiếu khách không sửa hoặc quá hạn không hiện giá."""
    token = str(token or '')[:40]
    if len(token) < 16:
        return None, 'khong_co'
    row = session.scalars(select(QuickQuote).where(QuickQuote.token == token)).first()
    if row is None:
        return None, 'khong_co'
    if row.status == 'KHONG_SUA' or valid_until(row) < _now().date():
        return row, 'het_han'
    return row, 'ok'


def load(session: Session, user: User, qid: int, lock: bool = False) -> QuickQuote:
    require(user)
    row = session.get(QuickQuote, qid, with_for_update=lock)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không có báo giá nhanh này.', status=404)
    return row


def load_open(session: Session, user: User, qid) -> QuickQuote:
    try:
        qid = int(str(qid or '').strip())
    except ValueError:
        raise BusinessError('NOT_FOUND', 'Không có báo giá nhanh này.', status=404)
    row = load(session, user, qid)
    if row.status not in OPEN:
        raise BusinessError('BAD_STATUS', f'Báo giá {row.number} đã {STATUSES[row.status].lower()}.', status=409)
    return row


def _open(session, user, qid) -> QuickQuote:
    row = load(session, user, qid, lock=True)
    if row.status not in OPEN:
        raise BusinessError('BAD_STATUS', f'Báo giá {row.number} đã {STATUSES[row.status].lower()}.', status=409)
    return row


def save_text(session: Session, user: User, qid: int, text) -> QuickQuote:
    row = _open(session, user, qid)
    text = _clean_text(text)
    if len(text) < 5:
        raise BusinessError('BAD_TEXT', 'Nội dung tin còn trống.')
    if text != row.text:
        row.text = text
        audit.record(session, user, 'quick_quote.edit', 'quick_quote', str(row.id), None, dict(length=len(text)))
    session.commit()
    return row


def mark_sent(session: Session, user: User, qid: int, text=None) -> QuickQuote:
    """Đã sao chép gửi bằng Zalo cá nhân / gọi điện báo."""
    row = _open(session, user, qid)
    if text is not None and str(text).strip():
        row.text = _clean_text(text)
    before = row.status
    row.status, row.sent_via = 'DA_GUI', row.sent_via or 'CA_NHAN'
    row.decided_by, row.decided_at = user.id, _now()
    audit.record(session, user, 'quick_quote.send', 'quick_quote', str(row.id), dict(status=before), dict(status='DA_GUI', via=row.sent_via))
    session.commit()
    return row


def linked_chat(session: Session, customer_code: str | None):
    if not customer_code:
        return None
    from .tin_gui_khach import linked_chat as chat
    return chat(session, customer_code)


def send_bot(session: Session, user: User, qid: int, text=None) -> QuickQuote:
    from . import zalo_bot
    row = _open(session, user, qid)
    chat = linked_chat(session, row.customer_code)
    if chat is None:
        raise BusinessError('NOT_LINKED', 'Khách chưa chọn hoặc chưa gắn Zalo công ty. Hãy Sao chép rồi gửi bằng Zalo cá nhân.', status=409)
    if text is not None and str(text).strip():
        row.text = _clean_text(text)
    if len(row.text) < 5:
        raise BusinessError('BAD_TEXT', 'Nội dung tin còn trống.')
    body = row.text
    session.commit()
    sent = zalo_bot.send_text(session, chat, body, user, 'tin_khach')
    if sent.status != 'DA_GUI':
        raise BusinessError('SEND_FAILED', f'Zalo chưa gửi được: {(sent.error or "không rõ lý do")[:200]}. Hãy thử lại hoặc Sao chép.', status=502)
    row = session.get(QuickQuote, qid, with_for_update=True)
    before = row.status
    row.status, row.sent_via, row.decided_by, row.decided_at = 'DA_GUI', 'BOT', user.id, _now()
    audit.record(session, user, 'quick_quote.send', 'quick_quote', str(row.id), dict(status=before),
                 dict(status='DA_GUI', via='BOT', chat=chat.id, length=len(body)))
    session.commit()
    return row


def decline(session: Session, user: User, qid: int, note: str = '') -> QuickQuote:
    row = _open(session, user, qid)
    before = row.status
    row.status, row.decided_by, row.decided_at = 'KHONG_SUA', user.id, _now()
    if str(note or '').strip():
        row.note = (row.note + '\n' if row.note else '') + 'Khách không sửa: ' + str(note).strip()[:300]
    audit.record(session, user, 'quick_quote.decline', 'quick_quote', str(row.id), dict(status=before), dict(status='KHONG_SUA'))
    session.commit()
    return row


def reopen(session: Session, user: User, qid: int) -> QuickQuote:
    row = load(session, user, qid, lock=True)
    if row.status != 'KHONG_SUA':
        raise BusinessError('BAD_STATUS', 'Chỉ mở lại báo giá khách đã báo không sửa.', status=409)
    row.status = 'DA_GUI' if row.sent_via else 'MOI'
    audit.record(session, user, 'quick_quote.reopen', 'quick_quote', str(row.id), dict(status='KHONG_SUA'), dict(status=row.status))
    session.commit()
    return row


def ticket_symptom(row: QuickQuote) -> str:
    """Mô tả lỗi điền sẵn cho phiếu khi chuyển: model + hiện tượng khách kể + số HG để tra lại."""
    text = f"{row.model_name}: {'; '.join(row.symptoms)}."
    if row.note.strip():
        text += ' ' + row.note.strip()
    return (text + f' (Báo giá nhanh {row.number})')[:4000]


def mark_converted(session: Session, user: User, qid, ticket: ServiceTicket) -> QuickQuote | None:
    """Phiếu đã tạo từ báo giá nhanh: ghi ticket_id. Báo giá đã đóng thì bỏ qua, phiếu vẫn giữ."""
    try:
        qid = int(str(qid or '').strip())
    except ValueError:
        return None
    row = session.get(QuickQuote, qid, with_for_update=True)
    if row is None or row.status not in OPEN:
        session.rollback()
        return None
    before = row.status
    row.status, row.ticket_id, row.decided_by, row.decided_at = 'CHUYEN_PHIEU', ticket.id, user.id, _now()
    if not row.customer_code:
        row.customer_code = ticket.customer_code
    audit.record(session, user, 'quick_quote.convert', 'quick_quote', str(row.id), dict(status=before),
                 dict(status='CHUYEN_PHIEU', ticket=ticket.number))
    session.commit()
    return row


# ---------- danh sách ----------

def rows(session: Session, user: User, view: str, q: str = '') -> list[QuickQuote]:
    require(user)
    statuses = VIEWS.get(view, VIEWS['cho'])[1]
    found = list(session.scalars(select(QuickQuote).where(QuickQuote.status.in_(statuses))
                                 .order_by(QuickQuote.created_at.desc(), QuickQuote.id.desc()).limit(1000)))
    needle = fold(q).split()
    if needle:
        names = customer_names(session, found)
        found = [r for r in found if all(w in fold(' '.join((r.number, r.model_name, r.contact, names.get(r.customer_code, ''),
                                                              ' '.join(r.symptoms)))) for w in needle)]
    return found


def counts(session: Session) -> dict:
    by = dict(session.execute(select(QuickQuote.status, func.count()).group_by(QuickQuote.status)).all())
    return {k: sum(by.get(s, 0) for s in st) for k, (_, st) in VIEWS.items()}


def customer_names(session: Session, rows_) -> dict:
    codes = {r.customer_code for r in rows_ if r.customer_code}
    return dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_(codes))).all()) if codes else {}

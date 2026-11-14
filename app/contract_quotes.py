"""Báo giá hợp đồng sửa chữa (TUẤN duyệt mẫu H1–H3 bản 3, 14/11/2026).

- Khách cần báo giá trước để ký hợp đồng: chọn khách, thêm các model và số máy; app lấy mọi gói sửa của nhóm máy từng
  model từ Bảng giá chung (giá riêng model nếu có). Chọn chung các cấp báo (Cấp 1·2·3, Chính hãng = giá linh kiện hãng
  ở Cấp 2), từng gói chọn cấp riêng được. Giám đốc giảm % cả bảng hoặc sửa từng ô.
- Bản chụp (snapshot) chỉ phần cho khách: tên gói, mô tả, giá chưa VAT, VAT, bảo hành. Không giá vốn, không phương án.
- Link /bgh/<mã ngẫu nhiên>, tin Zalo, phiếu A4. Còn chờ ký thì sửa được (giá lấy lại theo bảng giá hiện tại).
- Khách đã ký: từ ngày ký đến hết thời hạn áp giá, máy của khách có model trong hợp đồng vào xưởng thì khung Báo giá
  sửa chữa lấy đúng ô giá hợp đồng thay bảng giá chung (cấp/PA không có trên bảng ký thì vẫn theo bảng giá chung).
  Giám đốc đổi bảng giá chung không đổi giá hợp đồng đã ký. Ngừng áp thì thôi áp ngay.
- Không tạo nợ, không trừ kho; tiền thật vẫn là dòng phí của phiếu dịch vụ.
"""
import secrets
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, doc_numbers
from . import price_book as pb
from .errors import BusinessError
from .models import ContractQuote, Customer, PriceService, RepairGroup, User
from .security import has_role
from .service_sla import TZ

PREFIX = 'HD'
LEVELS = ('1', '2', '3', 'CH')
LEVEL_LABELS = {'1': 'Cấp 1 · Nhẹ', '2': 'Cấp 2 · Trung bình', '3': 'Cấp 3 · Nặng', 'CH': 'Chính hãng'}
LEVEL_SHORT = {'1': 'Cấp 1', '2': 'Cấp 2', '3': 'Cấp 3', 'CH': 'Chính hãng'}
LEVEL_HINT = {'1': 'Nhẹ', '2': 'Trung bình', '3': 'Nặng', 'CH': 'LK hãng · Cấp 2'}
VALID_CHOICES = (15, 30, 60)
VALID_DAYS = 30
APPLY_MONTHS = 12
MAX_MODELS = 10
MAX_OPENS = 200
MAX_DISCOUNT = 50
DEFAULT_PAYMENT = 'chuyển khoản trong 7 ngày kể từ ngày nghiệm thu, theo hóa đơn GTGT.'
STATUSES = {'CHO': 'Chờ khách ký', 'DA_KY': 'Đã ký · đang áp giá', 'NGUNG': 'Ngừng áp', 'HET_HAN': 'Hết hiệu lực', 'HET_AP': 'Hết thời hạn áp giá'}
SENT_VIA = {'BOT': 'Zalo công ty', 'COPY': 'Zalo cá nhân / gọi điện', 'IN': 'In A4'}
VIEWS = {'cho': 'Chờ khách ký', 'da-ky': 'Đã ký', 'khac': 'Hết hạn · ngừng'}


def _now():
    from . import service_tickets  # cùng đồng hồ với phiếu dịch vụ
    return service_tickets._now().astimezone(TZ)


def _today() -> date:
    return _now().date()


def require(user: User):
    from .service_tickets import DISPATCHERS
    if not has_role(user, *DISPATCHERS):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối hoặc giám đốc lập báo giá hợp đồng.', status=403)


def is_director(user: User) -> bool:
    return bool(user and has_role(user, 'director'))


def link(q: ContractQuote) -> str:
    from .dispatch_alerts import base_url
    return f'{base_url()}/bgh/{q.token}'


def state(q: ContractQuote, today: date | None = None) -> str:
    today = today or _today()
    if q.status == 'CHO' and q.valid_until < today:
        return 'HET_HAN'
    if q.status == 'DA_KY' and q.apply_until and q.apply_until < today:
        return 'HET_AP'
    return q.status


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in range(d.day, 27, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, min(d.day, 28))


def discounted(price: int, pct: int) -> int:
    """Giá chưa VAT sau giảm, làm tròn nghìn đồng (không dưới 1.000đ)."""
    if not pct:
        return int(price)
    out = (Decimal(price) * (100 - pct) / 100 / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 1000
    return max(1000, int(out))


def ex_vat_for(total: int) -> int:
    """Giá chưa VAT sao cho giá gồm VAT đúng số đã nhập; không tách tròn được thì lấy số gần nhất."""
    guess = int((Decimal(total) * 100 / 108).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    for p in sorted(range(guess - 3, guess + 4), key=lambda x: abs(x - guess)):
        if p > 0 and pb.with_vat(p) == total:
            return p
    return max(1, guess)


def _cell(price: int, warranty: int, base: int) -> dict:
    return dict(price=price, vat=pb.with_vat(price) - price, base=base, warranty=warranty or 0)


# ---------- đọc form ----------

def _int(raw, default=0) -> int:
    try:
        return int(str(raw or '').replace('.', '').replace(',', '').strip())
    except ValueError:
        return default


def parse(form) -> dict:
    """Form lập/sửa: khách, model_<i>/qty_<i>, cấp chung lv, cấp từng gói pick_<i>_<sid>, giảm %, hiệu lực, phụ phí."""
    get_list = form.getlist if hasattr(form, 'getlist') else (lambda k: [form[k]] if k in form else [])
    models = []
    for i in range(MAX_MODELS):
        name = ' '.join(str(form.get(f'model_{i}', '') or '').split())[:160]
        if not name:
            continue
        picks = {}
        for key in form.keys():
            if key.startswith(f'pick_{i}_'):
                sid = _int(key.rsplit('_', 1)[1], 0)
                if sid:
                    picks[sid] = [lv for lv in get_list(key) if lv in LEVELS]
        models.append(dict(i=i, name=name, qty=_int(form.get(f'qty_{i}'), 1), seen=str(form.get(f'seen_{i}', '') or ''),
                           picks=picks))
    levels = [lv for lv in get_list('lv') if lv in LEVELS]
    return dict(khach=str(form.get('khach', '') or '').strip()[:16], lien_he=str(form.get('lien_he', '') or '').strip()[:200],
                dien_thoai=str(form.get('dien_thoai', '') or '').strip()[:40], models=models,
                levels=levels if 'lv_seen' in form else list(LEVELS),
                giam=_int(form.get('giam'), 0), hieu_luc=_int(form.get('hieu_luc'), VALID_DAYS),
                ap_thang=_int(form.get('ap_thang'), APPLY_MONTHS), free_check=str(form.get('kiem_tra', '')) == '1' if 'kt_seen' in form else True,
                phi=[_int(x) for x in get_list('phi') if _int(x)], thanh_toan=str(form.get('thanh_toan', '') or '').strip()[:500])


def from_quote(q: ContractQuote) -> dict:
    """Dữ liệu form để sửa một báo giá đang chờ ký."""
    models = []
    for i, m in enumerate(q.snapshot['models']):
        picks = {r['service_id']: [lv for lv in LEVELS if r['cells'].get(lv)] for r in m['rows']}
        models.append(dict(i=i, name=m['name'], qty=m['qty'], seen=m['name'], picks=picks))
    return dict(khach=q.customer_code, lien_he=q.contact_name, dien_thoai=q.contact_phone, models=models, levels=list(q.levels),
                giam=q.discount_pct, hieu_luc=VALID_DAYS, ap_thang=q.apply_months, free_check=q.free_check,
                phi=[x['service_id'] for x in q.snapshot.get('extras', []) if x.get('service_id')], thanh_toan=q.payment_terms)


# ---------- gói theo model ----------

def _available(session, m, s: PriceService) -> dict:
    """Ô giá có trong bảng giá chung cho model: {'1': lookup, ...}; ô không có giá thì None."""
    out = {}
    for lv in LEVELS:
        r = pb.lookup(session, s.id, m.name, 2 if lv == 'CH' else int(lv), genuine=lv == 'CH')
        out[lv] = r if r['ok'] else None
    return out


def catalog(session: Session, d: dict) -> list[dict]:
    """Mỗi model: tìm trong danh mục, nhóm máy, mọi gói sửa của nhóm (có ít nhất một ô giá) và cấp đang chọn."""
    out = []
    for md in d['models']:
        m = pb.find_model(session, md['name'])
        row = dict(i=md['i'], name=md['name'], qty=md['qty'], model=m, group=None, services=[], error='')
        if m is None or not m.active:
            row['error'] = 'Model chưa có trong danh mục Model máy. Giám đốc thêm ở Bảng giá › Model máy.'
            out.append(row)
            continue
        row['name'] = m.name
        row['group'] = session.get(RepairGroup, m.group_id)
        fresh = md['seen'] != m.name  # model mới thêm hoặc vừa đổi: chọn sẵn theo cấp chung
        for pr in pb.package_rows(session, m.group_id):
            s = pr['service']
            if pr['empty'] or not s.active:
                continue
            cells = _available(session, m, s)
            if not any(cells.values()):
                continue
            want = d['levels'] if fresh else md['picks'].get(s.id, [])
            row['services'].append(dict(service=s, name=pb.customer_name(s), cells=cells,
                                        picked=[lv for lv in want if cells.get(lv)]))
        out.append(row)
    return out


def _extras(session, d) -> list[dict]:
    out = []
    if d['free_check']:
        out.append(dict(service_id=None, name='Kiểm tra, chẩn đoán máy', price=0, vat=0, note='Miễn phí cho khách hợp đồng'))
    for sid in d['phi'][:10]:
        s = session.get(PriceService, sid)
        if s is None or s.kind != 'PHI' or not s.active:
            continue
        out.append(dict(service_id=s.id, name=pb.customer_name(s), price=s.price, vat=pb.with_vat(s.price) - s.price,
                        note=f'Mỗi {s.unit}' if s.unit else ''))
    return out


def fee_services(session) -> list[PriceService]:
    return pb.services(session, 'PHI', active_only=True)


def build(session: Session, user: User, d: dict, old: ContractQuote | None = None) -> dict:
    """Kiểm tra form và dựng bản chụp. Giá lấy theo bảng giá hiện tại, giảm % do giám đốc."""
    if d['giam'] and not is_director(user):
        if not (old and old.discount_pct == d['giam']):
            raise BusinessError('DIRECTOR_ONLY', 'Chỉ giám đốc nhập giảm giá hợp đồng.', status=403)
    if not 0 <= d['giam'] <= MAX_DISCOUNT:
        raise BusinessError('BAD_DISCOUNT', f'Giảm giá hợp đồng từ 0 đến {MAX_DISCOUNT}%.')
    customer = session.get(Customer, d['khach']) if d['khach'] else None
    if customer is None or customer.status != 'DANG_DUNG':
        raise BusinessError('CUSTOMER_REQUIRED', 'Chọn khách hàng đang giao dịch.')
    if not d['models']:
        raise BusinessError('MODEL_REQUIRED', 'Thêm ít nhất một model máy của khách.')
    if not d['levels']:
        raise BusinessError('LEVEL_REQUIRED', 'Chọn ít nhất một cấp sửa chữa báo cho khách.')
    if d['hieu_luc'] not in VALID_CHOICES:
        raise BusinessError('BAD_VALID', 'Hiệu lực báo giá chọn 15, 30 hoặc 60 ngày.')
    if not 1 <= d['ap_thang'] <= 36:
        raise BusinessError('BAD_MONTHS', 'Thời hạn áp giá từ 1 đến 36 tháng.')
    models, seen = [], set()
    for row in catalog(session, d):
        if row['error']:
            raise BusinessError('UNKNOWN_MODEL', f'{row["name"]}: {row["error"]}')
        if row['model'].id in seen:
            raise BusinessError('DUP_MODEL', f'Model {row["name"]} bị nhập hai lần.')
        seen.add(row['model'].id)
        if not 1 <= row['qty'] <= 999:
            raise BusinessError('BAD_QTY', f'Số máy {row["name"]} từ 1 đến 999.')
        rows = []
        for sv in row['services']:
            if not sv['picked']:
                continue
            s = sv['service']
            cells = {}
            for lv in LEVELS:
                r = sv['cells'].get(lv) if lv in sv['picked'] else None
                cells[lv] = _cell(discounted(r['price'], d['giam']), r['warranty'], r['price']) if r else None
            rows.append(dict(service_id=s.id, name=sv['name'], note=' '.join((s.description or '').split())[:200], cells=cells))
        if not rows:
            raise BusinessError('SERVICE_REQUIRED', f'{row["name"]}: chọn ít nhất một gói sửa có giá.')
        models.append(dict(model_id=row['model'].id, name=row['name'], group=row['group'].name if row['group'] else '',
                           qty=row['qty'], rows=rows))
    shown = [lv for lv in LEVELS if any(r['cells'][lv] for m in models for r in m['rows'])]
    return dict(customer=customer, models=models, extras=_extras(session, d), shown=shown)


def _payment(d) -> str:
    return d['thanh_toan'] or DEFAULT_PAYMENT


def create(session: Session, user: User, d: dict) -> ContractQuote:
    require(user)
    b = build(session, user, d)
    today = _today()
    q = ContractQuote(number=doc_numbers.next_number(session, PREFIX, today), customer_code=b['customer'].code,
                      contact_name=d['lien_he'], contact_phone=d['dien_thoai'], status='CHO', levels=b['shown'],
                      discount_pct=d['giam'], valid_until=today + timedelta(days=d['hieu_luc']), apply_months=d['ap_thang'],
                      free_check=d['free_check'], payment_terms=_payment(d),
                      snapshot=dict(models=b['models'], extras=b['extras']), token=secrets.token_urlsafe(18),
                      created_by=user.id, created_at=_now(), updated_at=_now())
    q.text = zalo_text(q, b['customer'])
    session.add(q)
    session.flush()
    audit.record(session, user, 'contract_quote.create', 'contract_quote', q.number, None, _summary(q))
    session.commit()
    return q


def load(session: Session, user: User, qid: int, lock: bool = False) -> ContractQuote:
    require(user)
    q = session.get(ContractQuote, qid, with_for_update=lock)
    if q is None:
        raise BusinessError('NOT_FOUND', 'Không có báo giá hợp đồng này.', status=404)
    return q


def _check_version(q, version):
    if version is not None and q.version != version:
        raise BusinessError('STALE', 'Báo giá vừa được người khác sửa. Tải lại trang rồi làm lại.', status=409)


def update(session: Session, user: User, qid: int, version, d: dict) -> ContractQuote:
    """Sửa báo giá đang chờ ký: dựng lại bản chụp theo bảng giá hiện tại, hiệu lực tính lại từ hôm nay. Link giữ nguyên."""
    q = load(session, user, qid, lock=True)
    _check_version(q, version)
    if q.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Chỉ sửa báo giá chưa ký.', status=409)
    if d['khach'] != q.customer_code:
        raise BusinessError('CUSTOMER_LOCKED', 'Không đổi khách của báo giá đã lập. Lập báo giá mới cho khách khác.')
    before = _summary(q)
    b = build(session, user, d, old=q)
    q.contact_name, q.contact_phone = d['lien_he'], d['dien_thoai']
    q.levels, q.discount_pct, q.apply_months, q.free_check = b['shown'], d['giam'], d['ap_thang'], d['free_check']
    q.valid_until = _today() + timedelta(days=d['hieu_luc'])
    q.payment_terms = _payment(d)
    q.snapshot = dict(models=b['models'], extras=b['extras'])
    q.text = zalo_text(q, b['customer'])
    q.updated_at = _now()
    audit.record(session, user, 'contract_quote.update', 'contract_quote', q.number, before, _summary(q))
    session.commit()
    return q


def set_cells(session: Session, user: User, qid: int, version, form) -> ContractQuote:
    """Giám đốc sửa từng ô giá (nhập giá gồm VAT). Ô trống giữ nguyên; không thêm ô mới."""
    if not is_director(user):
        raise BusinessError('DIRECTOR_ONLY', 'Chỉ giám đốc sửa giá từng ô.', status=403)
    q = load(session, user, qid, lock=True)
    _check_version(q, version)
    if q.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Chỉ sửa giá báo giá chưa ký.', status=409)
    snap = {'models': [dict(m, rows=[dict(r, cells=dict(r['cells'])) for r in m['rows']]) for m in q.snapshot['models']],
            'extras': q.snapshot.get('extras', [])}
    changed = []
    for mi, m in enumerate(snap['models']):
        for r in m['rows']:
            for lv in LEVELS:
                c = r['cells'].get(lv)
                raw = form.get(f'o_{mi}_{r["service_id"]}_{lv}')
                if c is None or raw is None or not str(raw).strip():
                    continue
                total = pb.parse_money(raw, f'Giá {r["name"]} {LEVEL_SHORT[lv]}')
                if not total or total <= 0:
                    raise BusinessError('BAD_PRICE', f'Giá {r["name"]} {LEVEL_SHORT[lv]} phải lớn hơn 0.')
                if total == c['price'] + c['vat']:
                    continue
                price = ex_vat_for(total)
                r['cells'][lv] = dict(c, price=price, vat=total - price if pb.with_vat(price) == total else pb.with_vat(price) - price)
                changed.append(f'{m["name"]} · {r["name"]} · {LEVEL_SHORT[lv]}: {c["price"] + c["vat"]} → {total}')
    if changed:
        q.snapshot = snap
        q.text = zalo_text(q, session.get(Customer, q.customer_code))
        q.updated_at = _now()
        audit.record(session, user, 'contract_quote.cells', 'contract_quote', q.number, None, dict(changes=changed[:60]))
    session.commit()
    return q


def _summary(q: ContractQuote) -> dict:
    return dict(customer=q.customer_code, models=[f'{m["name"]} × {m["qty"]}' for m in q.snapshot['models']],
                rows=sum(len(m['rows']) for m in q.snapshot['models']), levels=list(q.levels), discount=q.discount_pct,
                valid_until=q.valid_until.isoformat(), apply_months=q.apply_months)


def _m(n) -> str:
    return f'{int(n or 0):,}'.replace(',', '.')


def zalo_text(q: ContractQuote, customer: Customer | None) -> str:
    models = q.snapshot['models']
    n_rows = sum(len(m['rows']) for m in models)
    out = [f'Dạ, Thành Đức gửi {customer.name if customer else "anh/chị"} báo giá dịch vụ sửa chữa máy in theo hợp đồng ({q.number}):']
    for m in models:
        out.append(f'• {m["name"]} ({m["qty"]} máy): {len(m["rows"])} gói sửa')
    out.append(f'Tổng {n_rows} gói, giá trọn gói công + linh kiện, đã gồm VAT 8%' + (f', đã giảm {q.discount_pct}%.' if q.discount_pct else '.'))
    out.append(f'Báo giá có hiệu lực đến {q.valid_until.strftime("%d/%m/%Y")}.')
    out.append(f'Xem chi tiết: {link(q)}')
    out.append('Anh/chị xem giúp em, đồng ý thì bên em gửi bản in để hai bên ký làm phụ lục hợp đồng ạ.')
    return '\n'.join(out)


# ---------- gửi, ký, ngừng ----------

def mark_sent(session: Session, user: User, qid: int, via: str = 'COPY', text=None) -> ContractQuote:
    q = load(session, user, qid, lock=True)
    if q.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Báo giá đã ký hoặc đã ngừng.', status=409)
    if text is not None and str(text).strip():
        q.text = _clean_text(q, text)
    q.sent_via, q.sent_at, q.sent_by = via if via in SENT_VIA else 'COPY', _now(), user.id
    audit.record(session, user, 'contract_quote.send', 'contract_quote', q.number, None, dict(via=q.sent_via))
    session.commit()
    return q


def _clean_text(q: ContractQuote, text) -> str:
    text = str(text).strip()[:2000]
    if link(q) not in text:
        raise BusinessError('TEXT_LINK', 'Tin gửi khách phải giữ nguyên link xem báo giá.')
    return text


def send_bot(session: Session, user: User, qid: int, text=None) -> ContractQuote:
    from . import tin_gui_khach as tg
    from . import zalo_bot
    q = load(session, user, qid, lock=True)
    if q.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Báo giá đã ký hoặc đã ngừng.', status=409)
    chat = tg.linked_chat(session, q.customer_code)
    if chat is None:
        raise BusinessError('NOT_LINKED', 'Khách chưa gắn Zalo công ty. Hãy Sao chép tin rồi gửi bằng Zalo cá nhân.', status=409)
    if text is not None and str(text).strip():
        q.text = _clean_text(q, text)
    body = q.text
    session.commit()
    sent = zalo_bot.send_text(session, chat, body, user, 'tin_khach')
    if sent.status != 'DA_GUI':
        raise BusinessError('SEND_FAILED', f'Zalo chưa gửi được: {(sent.error or "không rõ lý do")[:200]}. Hãy thử lại hoặc Sao chép.', status=502)
    q = session.get(ContractQuote, qid, with_for_update=True)
    q.sent_via, q.sent_at, q.sent_by = 'BOT', _now(), user.id
    audit.record(session, user, 'contract_quote.send', 'contract_quote', q.number, None, dict(via='BOT', chat=chat.id))
    session.commit()
    return q


def sign(session: Session, user: User, qid: int, version, signed_on: date | None, note: str = '') -> ContractQuote:
    """Khách đã ký: từ ngày ký áp giá hợp đồng trong apply_months tháng."""
    q = load(session, user, qid, lock=True)
    _check_version(q, version)
    if q.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Báo giá đã ký hoặc đã ngừng.', status=409)
    today = _today()
    if signed_on is None:
        raise BusinessError('DATE_REQUIRED', 'Ghi ngày khách ký.')
    if signed_on > today:
        raise BusinessError('BAD_DATE', 'Ngày ký không được sau hôm nay.')
    if signed_on < q.created_at.astimezone(TZ).date():
        raise BusinessError('BAD_DATE', 'Ngày ký không được trước ngày lập báo giá.')
    if q.valid_until < signed_on:
        raise BusinessError('EXPIRED', 'Báo giá đã hết hiệu lực trước ngày ký. Bấm Sửa để cập nhật giá, gửi lại khách rồi ghi ký.', status=409)
    q.status, q.signed_on, q.signed_by = 'DA_KY', signed_on, user.id
    q.apply_until = add_months(signed_on, q.apply_months) - timedelta(days=1)
    q.signed_note = ' '.join(str(note or '').split())[:500]
    q.updated_at = _now()
    audit.record(session, user, 'contract_quote.sign', 'contract_quote', q.number, None,
                 dict(signed_on=signed_on.isoformat(), apply_until=q.apply_until.isoformat(), note=q.signed_note))
    session.commit()
    return q


def stop(session: Session, user: User, qid: int, version, reason: str) -> ContractQuote:
    q = load(session, user, qid, lock=True)
    _check_version(q, version)
    if q.status == 'NGUNG':
        raise BusinessError('BAD_STATUS', 'Báo giá đã ngừng.', status=409)
    reason = ' '.join(str(reason or '').split())[:500]
    if len(reason) < 3:
        raise BusinessError('REASON_REQUIRED', 'Ghi lý do ngừng áp giá.')
    before = q.status
    q.status, q.stopped_at, q.stopped_by, q.stop_reason = 'NGUNG', _now(), user.id, reason
    q.updated_at = _now()
    audit.record(session, user, 'contract_quote.stop', 'contract_quote', q.number, dict(status=before), dict(reason=reason))
    session.commit()
    return q


def public(session: Session, token: str) -> tuple[ContractQuote | None, str]:
    """Trang khách: ok | da_ky | het_han | ngung | qua_lan | khong_co. Chỉ ok/da_ky hiện giá."""
    token = str(token or '')[:40]
    if len(token) < 16:
        return None, 'khong_co'
    q = session.scalars(select(ContractQuote).where(ContractQuote.token == token).with_for_update()).first()
    if q is None:
        return None, 'khong_co'
    st = state(q)
    if st in ('NGUNG', 'HET_AP'):
        out = 'ngung'
    elif st == 'HET_HAN':
        out = 'het_han'
    elif q.opened_count >= MAX_OPENS:
        out = 'qua_lan'
    else:
        out = 'da_ky' if st == 'DA_KY' else 'ok'
    if out in ('ok', 'da_ky'):
        q.opened_count += 1
        q.last_opened_at = _now()
    session.commit()
    return q, out


# ---------- danh sách ----------

def rows(session: Session, view: str, q: str = '') -> list[ContractQuote]:
    from .search import fold
    items = list(session.scalars(select(ContractQuote).order_by(ContractQuote.id.desc())))
    today = _today()
    keep = {'cho': ('CHO',), 'da-ky': ('DA_KY',), 'khac': ('HET_HAN', 'NGUNG', 'HET_AP')}[view]
    items = [x for x in items if state(x, today) in keep]
    needle = fold(q).split()
    if needle:
        names = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_({x.customer_code for x in items}))).all()) if items else {}
        items = [x for x in items if all(w in fold(f'{x.number} {x.customer_code} {names.get(x.customer_code, "")} '
                                                    + ' '.join(m['name'] for m in x.snapshot['models'])) for w in needle)]
    return items


def counts(session: Session) -> dict:
    today = _today()
    out = dict.fromkeys(VIEWS, 0)
    for x in session.scalars(select(ContractQuote)):
        st = state(x, today)
        out['cho' if st == 'CHO' else 'da-ky' if st == 'DA_KY' else 'khac'] += 1
    return out


# ---------- áp giá hợp đồng khi máy vào xưởng ----------

def active_for(session: Session, customer_code: str, model_id: int) -> ContractQuote | None:
    """Hợp đồng đã ký mới nhất của khách có model này, còn trong thời hạn áp giá hôm nay."""
    if not customer_code or not model_id:
        return None
    today = _today()
    found = session.scalars(select(ContractQuote).where(
        ContractQuote.customer_code == customer_code, ContractQuote.status == 'DA_KY',
        ContractQuote.signed_on <= today, ContractQuote.apply_until >= today).order_by(ContractQuote.signed_on.desc(), ContractQuote.id.desc()))
    for q in found:
        if any(m['model_id'] == model_id for m in q.snapshot['models']):
            return q
    return None


def contract_cell(q: ContractQuote, model_id: int, service_id: int, level: int, genuine: bool) -> dict | None:
    key = 'CH' if genuine and level == 2 else (None if genuine else str(level))
    if key is None:
        return None
    for m in q.snapshot['models']:
        if m['model_id'] != model_id:
            continue
        for r in m['rows']:
            if r['service_id'] == service_id:
                return r['cells'].get(key)
    return None


def lookup(session: Session, customer_code: str, service_id, model: str, level: int, genuine: bool = False) -> dict:
    """Như price_book.lookup, nhưng ô có trên hợp đồng đã ký của khách thì lấy giá hợp đồng (source='hop_dong')."""
    base = pb.lookup(session, service_id, model, level, genuine=genuine)
    m = base.get('model') or pb.find_model(session, model or '')
    if m is None:
        return base
    q = active_for(session, customer_code, m.id)
    if q is None:
        return base
    try:
        lv = int(level)
    except (TypeError, ValueError):
        return base
    c = contract_cell(q, m.id, int(service_id), lv, genuine)
    if c is None:
        return base
    return {**base, 'ok': True, 'price': c['price'], 'price_vat': c['price'] + c['vat'], 'warranty': c['warranty'],
            'source': 'hop_dong', 'reason': '', 'contract': dict(id=q.id, number=q.number, signed_on=q.signed_on,
                                                                  discount=q.discount_pct, base=c.get('base'))}

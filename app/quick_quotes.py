"""Báo giá nhanh HG (TUẤN duyệt mẫu B1 14/11/2026, đợt 2 quy trình báo giá sửa chữa).

Khách hỏi giá qua Zalo/điện thoại khi chưa gửi máy. Nhân viên chọn model, hiện tượng, sửa ở đâu:
- App chỉ hiện các dịch vụ + cấp đã khai báo ở tab Hiện tượng (không tự lấy cấp 1 đến cấp 3), giá gồm VAT lấy thẳng
  từ bảng giá chung (price_book.lookup: model ghi đè → giá nhóm). Không có giá thì "Cần kỹ thuật xác nhận", không đoán.
- Các dịch vụ thay thế nhau tùy nguyên nhân, không cộng lại. Tin gửi khách không có linh kiện.
- Tận nơi: thêm phụ phí có chữ "tận nơi". Phí kiểm tra (dịch vụ có chữ "kiểm tra") luôn ghi trong tin.
- Lưu ảnh chụp giá lúc báo (lines, text); bảng giá đổi sau không làm sai tin đã gửi.
- Khách đồng ý: Chuyển thành phiếu nhận máy (quầy) hoặc Hẹn thợ tới (nhận đơn dịch vụ), điền sẵn, không nhập lại.
- Không trừ kho, không tạo nợ, không gửi tự động: người làm bấm Sao chép hoặc Gửi Zalo công ty.
"""
from datetime import datetime

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


def compute(session: Session, model_name: str, symptoms: list[str], place: str) -> dict:
    """Khoảng giá tham khảo cho các hiện tượng đã chọn. Mỗi dịch vụ một dòng; cấp lấy đúng level_min..level_max đã khai."""
    model_name = ' '.join(str(model_name or '').split())[:160]
    place = place if place in PLACES else 'CUA_HANG'
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
    lines = []
    for sid, (lo, hi) in ranges.items():
        s = session.get(PriceService, sid)
        if s is None or s.kind != 'GOI' or not s.active:
            continue
        if model is not None and not session.scalar(select(RepairPrice.id).where(
                RepairPrice.service_id == sid, or_(RepairPrice.group_id == model.group_id, RepairPrice.model_id == model.id)).limit(1)):
            continue  # nhóm máy này không làm dịch vụ (cả 3 cấp trống, không giá riêng model): không hiện

        prices, levels, reason = [], [], ''
        for lv in range(lo, hi + 1):
            r = pb.lookup(session, sid, model.name if model else model_name, lv)
            if r['ok']:
                prices.append(r['price_vat'])
                levels.append(lv)
            else:
                reason = reason or r['reason']
        line = dict(service_id=sid, name=s.name, sort=s.sort, levels=levels, declared=[lo, hi], ok=bool(prices),
                    low=min(prices) if prices else None, high=max(prices) if prices else None, reason='' if prices else reason)
        lines.append(line)
    lines.sort(key=lambda x: (not x['ok'], x['sort'], fold(x['name'])))
    fees = [dict(name=f.name, price=pb.with_vat(f.price), note=(f.description or '').strip()[:160]) for f in _fee(session, 'kiem tra')]
    trip = sorted((dict(name=f.name, price=pb.with_vat(f.price), note='') for f in _fee(session, 'tan noi')), key=lambda x: x['price']) if place == 'TAN_NOI' else []
    return dict(model_name=model.name if model else model_name, model=model, group=group, symptoms=chosen, place=place,
                lines=lines, check_fees=fees, trip_fees=trip, unknown_model=bool(model_name) and model is None)


def range_text(line: dict) -> str:
    if not line['ok']:
        return VERIFY
    if line['low'] == line['high']:
        return money(line['low'])
    return f"{money(line['low'])} – {money(line['high'])}"


def levels_text(line: dict) -> str:
    lv = line['levels'] or list(range(line['declared'][0], line['declared'][1] + 1))
    return f'C{lv[0]}' if len(lv) == 1 else f'C{lv[0]}–C{lv[-1]}'


def zalo_text(result: dict) -> str:
    model = result['model_name'] or 'của anh/chị'
    sym = ', '.join(s[:1].lower() + s[1:] for s in result['symptoms'])
    head = f'Dạ, máy {model}' + (f' bị {sym}' if sym else '')
    priced = [ln for ln in result['lines'] if ln['ok']]
    out = []
    if priced:
        out.append(head + ' thì chi phí tùy nguyên nhân và tình trạng máy. Giá tham khảo bên em (đã gồm VAT):')
        for ln in priced:
            out.append(f"• {ln['name']}: " + (f"khoảng {money(ln['low'])}đ" if ln['low'] == ln['high'] else f"{money(ln['low'])} – {money(ln['high'])}đ"))
    else:
        out.append(head + ' thì bên em cần kỹ thuật kiểm tra máy mới báo được chi phí chính xác.')
    for f in result['trip_fees']:
        out.append(f"• {f['name']}: {money(f['price'])}đ")
    for f in result['check_fees']:
        note = f['note'].rstrip('.')
        out.append(f"{f['name']} {money(f['price'])}đ" + (f", {note[:1].lower() + note[1:]}." if note else '.'))
    out.append('Sau khi kiểm tra, Thành Đức sẽ báo chi phí và thời gian cụ thể, chỉ sửa khi anh/chị đồng ý ạ.')
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


def create(session: Session, user: User, *, model_name: str, symptoms: list[str], place: str, customer_code: str = '',
           contact: str = '', note: str = '', text: str | None = None) -> QuickQuote:
    require(user)
    if len(' '.join(str(model_name or '').split())) < 2:
        raise BusinessError('BAD_MODEL', 'Nhập model máy khách hỏi, ví dụ Canon LBP 2900.')
    result = compute(session, model_name, symptoms, place)
    if not result['symptoms']:
        raise BusinessError('BAD_SYMPTOM', 'Chọn ít nhất một hiện tượng khách kể.')
    customer = _customer(session, customer_code)
    body = _clean_text(text) if text is not None and str(text).strip() else zalo_text(result)
    lines = [{k: v for k, v in ln.items() if k != 'sort'} for ln in result['lines']]
    row = QuickQuote(number=doc_numbers.next_number(session, PREFIX, _now().date()), customer_code=customer.code if customer else None,
                     contact=' '.join(str(contact or '').split())[:200], model_name=result['model_name'],
                     model_id=result['model'].id if result['model'] else None, place=result['place'], symptoms=result['symptoms'],
                     note=str(note or '').strip()[:2000],
                     lines=lines + [dict(fee=True, **f) for f in result['trip_fees'] + result['check_fees']],
                     text=body, created_by=user.id)
    session.add(row)
    session.flush()
    audit.record(session, user, 'quick_quote.create', 'quick_quote', str(row.id), None,
                 dict(number=row.number, model=row.model_name, symptoms=row.symptoms, place=row.place, customer=row.customer_code or '',
                      lines=[[ln['name'], ln.get('low'), ln.get('high')] for ln in lines]))
    session.commit()
    return row


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

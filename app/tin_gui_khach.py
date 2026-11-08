"""Tin gửi khách (Trợ lý Zalo đợt 4, TUẤN duyệt mẫu 06/11/2026).

- Ba loại tin: nhắc nợ, báo giá, hỏi thăm sau sửa. App soạn sẵn từ SỐ LIỆU THẬT (công nợ, báo giá, phiếu dịch vụ);
  AI không đặt giá hay số nợ. Người làm xem lại, sửa chữ rồi tự bấm gửi. Không bao giờ tự gửi.
- Khách đã gắn chat bot Zalo công ty: Gửi qua Zalo công ty. Chưa gắn: Sao chép, gửi bằng Zalo cá nhân, rồi bấm Đã gửi.
- "Viết lại tự nhiên hơn": nhờ Codex/Claude trên Z2. App so số trong bản mới với bản cũ; lệch số thì không cho dùng.
- Quyền (Q8): nhắc nợ giám đốc + kế toán; báo giá kinh doanh + giám đốc; hỏi thăm điều phối, kinh doanh + giám đốc.
- Q9: mỗi khách tối đa 1 tin nhắc nợ đã gửi trong 7 ngày (giám đốc vẫn soạn được, có cảnh báo).
- Mọi tin ghi lại: ai soạn, ai sửa, ai gửi, lúc nào, gửi bằng gì, nội dung cuối (bảng customer_messages + audit).
"""
import re
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit, doc_numbers
from .config import get_settings
from .errors import BusinessError
from .models import (Customer, CustomerMessage, Equipment, Product, Quote, ServiceTicket, User, ZaloAiJob, ZaloChat, ZaloMessage,
                     ZaloTroLy)
from .security import has_role
from .service_sla import TZ

KINDS = {'NHAC_NO': 'Nhắc nợ', 'BAO_GIA': 'Báo giá', 'HOI_THAM': 'Hỏi thăm sau sửa'}
SHORT = {'NHAC_NO': 'Nhắc nợ', 'BAO_GIA': 'Báo giá', 'HOI_THAM': 'Hỏi thăm'}
WRITERS = {'NHAC_NO': ('finance',), 'BAO_GIA': ('sales',), 'HOI_THAM': ('dispatcher', 'sales')}
VIEWS = {'cho': ('Chờ gửi', 'CHO'), 'da-gui': ('Đã gửi', 'DA_GUI'), 'da-bo': ('Đã bỏ', 'DA_BO')}
VIA = {'BOT': 'Zalo công ty', 'CA_NHAN': 'Zalo cá nhân'}
DEBT_GAP_DAYS = 7
REWRITE_WAIT = 240  # giây: quá lâu máy Z2 chưa trả lời thì coi như lỗi, người làm bấm lại được
MAX_TEXT = 2000  # giới hạn một tin Zalo
DOC_KINDS = {'DB': 'Đơn', 'DV': 'Phiếu', 'DK': 'Nợ đầu kỳ'}
PREFIX = 'TG'


def _now():
    return datetime.now(TZ)


def _money(value) -> str:
    return f'{int(value or 0):,}'.replace(',', '.') + ' đ'


def _d(value) -> str:
    return value.strftime('%d/%m/%Y') if value else ''


def _qty(value) -> str:
    from decimal import Decimal
    text = format(Decimal(str(value or 0)), 'f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text).replace('.', ',')


def is_director(user: User) -> bool:
    return user.active and 'director' in user.roles


def can_write(user: User, kind: str) -> bool:
    return bool(user.active and kind in WRITERS and has_role(user, *WRITERS[kind]))


def kinds_for(user: User) -> list[str]:
    return [k for k in KINDS if can_write(user, k)]


def check_any(user: User) -> None:
    if not kinds_for(user):
        raise BusinessError('FORBIDDEN', 'Tài khoản này không soạn tin gửi khách.', status=403)


def check_kind(user: User, kind: str) -> None:
    if kind not in KINDS:
        raise BusinessError('BAD_KIND', 'Không có loại tin này.')
    if not can_write(user, kind):
        who = {'NHAC_NO': 'giám đốc và kế toán', 'BAO_GIA': 'kinh doanh và giám đốc', 'HOI_THAM': 'điều phối, kinh doanh và giám đốc'}[kind]
        raise BusinessError('FORBIDDEN', f'Tin {KINDS[kind].lower()} chỉ {who} soạn được.', status=403)


def linked_chat(session: Session, customer_code: str) -> ZaloChat | None:
    """Chat riêng của khách đã gắn với bot (giám đốc gắn ở Tin nhắn Zalo). Zalo Bot chỉ nhắn được người đã nhắn bot."""
    return session.scalars(select(ZaloChat).where(ZaloChat.customer_code == customer_code, ZaloChat.chat_type == 'PRIVATE')
                           .order_by(ZaloChat.last_message_at.desc().nulls_last(), ZaloChat.id.desc()).limit(1)).first()


def bank_line(session: Session) -> str:
    row = session.get(ZaloTroLy, 1)
    line = (row.bank_line or '').strip() if row else ''
    if line:
        return line
    # 0088 VietQR: để trống thì lấy từ quỹ ngân hàng đang ghép Thu chuyển khoản (đủ BIN, số TK, chủ TK).
    from . import vietqr
    return vietqr.bank_line(vietqr.company_account(session))


# ---------- Soạn từ số liệu ----------

def _customer(session: Session, code: str) -> Customer:
    row = session.get(Customer, str(code or '').strip())
    if row is None:
        raise BusinessError('BAD_CUSTOMER', 'Không có mã khách này.')
    return row


def debt_facts(session: Session, user: User, code: str, today: date) -> dict:
    """Chứng từ khách còn chưa trả, lấy đúng như trang Công nợ khách hàng (số gồm VAT). Tiền đã nộp thợ chờ xác nhận không nhắc."""
    from .web.customer_debts import _rows
    with session.no_autoflush:
        rows = [r for r in _rows(session, user, today) if r['code'] == code]
    docs = [dict(kind=doc['kind'], number=doc['number'] or DOC_KINDS.get(doc['kind'], ''), day=doc['day'].isoformat(), unpaid=doc['unpaid'])
            for r in rows for doc in r['docs'] if doc['unpaid'] > 0]
    total = sum(doc['unpaid'] for doc in docs)
    if total <= 0:
        raise BusinessError('NO_DEBT', 'Khách này hiện không còn nợ chưa trả, không cần nhắc.')
    return dict(as_of=today.isoformat(), docs=docs, total=total, days=max(r['days'] for r in rows))


def debt_text(name: str, code: str, f: dict, bank: str) -> str:
    lines = [f'Kính gửi {name},', f"Thành Đức xin gửi thông tin công nợ đến ngày {_d(date.fromisoformat(f['as_of']))}:"]
    for doc in f['docs']:
        label = 'Nợ đầu kỳ' if doc['kind'] == 'DK' else f"{DOC_KINDS.get(doc['kind'], '')} {doc['number']}".strip()
        lines.append(f"• {label} ngày {date.fromisoformat(doc['day']).strftime('%d/%m')}: còn {_money(doc['unpaid'])}")
    lines.append(f"Tổng còn: {_money(f['total'])}.")
    if bank:
        lines.append(f'Anh/chị vui lòng chuyển khoản vào TK {bank}, nội dung: {code}.')
    lines.append('Nếu đã thanh toán, anh/chị bỏ qua tin này giúp em. Cảm ơn anh/chị!')
    return '\n'.join(lines)


def quote_facts(session: Session, user: User, quote_id: int) -> tuple[Quote, dict]:
    from . import quotes
    quotes.check_reader(user)
    q = session.get(Quote, quote_id)
    if q is None:
        raise BusinessError('NOT_FOUND', 'Không có báo giá này.', status=404)
    if q.status in ('DA_HUY', 'TU_CHOI'):
        raise BusinessError('BAD_STATUS', 'Báo giá đã hủy hoặc khách đã từ chối, không soạn tin được.', status=409)
    if not q.lines:
        raise BusinessError('BAD_STATUS', 'Báo giá chưa có dòng hàng.', status=409)
    names = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_([ln.product_code for ln in q.lines if ln.product_code])))
                 .all())
    lines = [dict(name=(ln.description or names.get(ln.product_code, ln.product_code or '')).strip(), qty=_qty(ln.qty), unit=ln.unit,
                  amount=int(ln.amount)) for ln in q.lines]
    return q, dict(number=q.number, lines=lines, subtotal=int(q.subtotal), vat=int(q.vat_total), discount=int(q.discount_amount or 0),
                   total=int(q.total), valid_until=q.valid_until.isoformat() if q.valid_until else '')


def quote_text(f: dict) -> str:
    lines = [f"Chào anh/chị, Thành Đức gửi báo giá {f['number']}:"]
    for ln in f['lines']:
        lines.append(f"• {ln['name']} × {ln['qty']} {ln['unit']}: {_money(ln['amount'])}".replace('  ', ' '))
    lines.append(f"Tiền hàng chưa VAT: {_money(f['subtotal'])}, VAT: {_money(f['vat'])}"
                 + (f", giảm giá: {_money(f['discount'])}" if f['discount'] else '') + '.')
    lines.append(f"Tổng cộng: {_money(f['total'])}" + (f", hiệu lực đến {_d(date.fromisoformat(f['valid_until']))}" if f['valid_until'] else '') + '.')
    lines.append('Anh/chị đồng ý thì nhắn lại, em lên đơn và hẹn giao ạ.')
    return '\n'.join(lines)


def ticket_facts(session: Session, user: User, ticket_id: int) -> tuple[ServiceTicket, dict]:
    from . import service_warranty
    t = session.get(ServiceTicket, ticket_id)
    if t is None:
        raise BusinessError('NOT_FOUND', 'Không có phiếu dịch vụ này.', status=404)
    if t.status not in ('HOAN_THANH', 'DA_DONG'):
        raise BusinessError('BAD_STATUS', 'Phiếu chưa hoàn thành, chưa hỏi thăm sau sửa được.', status=409)
    stamp, _ = service_warranty.completion(session, t)
    machine = ''
    if t.serial:
        eq = session.get(Equipment, t.serial)
        product = session.get(Product, eq.product_code) if eq is not None and eq.product_code else None
        machine = (product.name if product else (eq.model_name if eq is not None else '')) or ''
    tech_id = t.repaired_by or t.assignee_id
    tech = session.get(User, tech_id) if tech_id else None
    end = service_warranty.warranty_end(session, t) if t.kind != 'MUC_DINH_KY' else None
    return t, dict(number=t.number, machine=machine, serial=t.serial or '', done=_d(stamp.astimezone(TZ).date()) if stamp else '',
                   tech=tech.full_name if tech else '', warranty_end=end.isoformat() if end else '', ink=t.kind == 'MUC_DINH_KY')


def ticket_text(f: dict) -> str:
    what = 'giao mực' if f['ink'] else 'sửa'
    machine = f"máy {f['machine']}" if f['machine'] else 'máy'
    if f['serial']:
        machine += f" (serial {f['serial']})"
    parts = [f"phiếu {f['number']}"] + ([f"thợ {f['tech'].split()[-1]}"] if f['tech'] else [])
    lines = [f"Chào anh/chị, Thành Đức hỏi thăm {machine} sau lần {what}"
             + (f" ngày {f['done'][:5]}" if f['done'] else '') + f" ({', '.join(parts)})."]
    tail = 'Máy chạy ổn chưa ạ?' if not f['ink'] else 'Mực in có đều và đậm không ạ?'
    if f['warranty_end']:
        tail += f" Máy được bảo hành sửa chữa đến {_d(date.fromisoformat(f['warranty_end']))}."
    lines.append(tail + f' Có gì anh/chị nhắn lại hoặc gọi {get_settings().company_hotline} để em hỗ trợ ngay.')
    return '\n'.join(lines)


def last_debt_sent(session: Session, code: str) -> datetime | None:
    return session.scalar(select(func.max(CustomerMessage.decided_at)).where(
        CustomerMessage.customer_code == code, CustomerMessage.kind == 'NHAC_NO', CustomerMessage.status == 'DA_GUI'))


def create(session: Session, user: User, kind: str, *, customer_code: str = '', ref_id: int | None = None,
           source: str = 'APP') -> CustomerMessage:
    """Soạn một tin. Đã có tin chờ gửi cùng loại cho cùng khách / chứng từ thì trả lại tin đó, không soạn trùng."""
    check_kind(user, kind)
    today = _now().date()
    ref_type = ''
    if kind == 'NHAC_NO':
        customer = _customer(session, customer_code)
        last = last_debt_sent(session, customer.code)
        if last is not None and _now() - last < timedelta(days=DEBT_GAP_DAYS) and not is_director(user):
            raise BusinessError('TOO_SOON', f'Đã nhắc nợ khách này ngày {last.astimezone(TZ):%d/%m}. '
                                            f'Mỗi khách tối đa 1 tin nhắc nợ trong {DEBT_GAP_DAYS} ngày (giám đốc vẫn gửi được).', status=409)
        facts = debt_facts(session, user, customer.code, today)
        if last is not None:
            facts['last_sent'] = last.astimezone(TZ).date().isoformat()
        text = debt_text(customer.name, customer.code, facts, bank_line(session))
    elif kind == 'BAO_GIA':
        q, facts = quote_facts(session, user, int(ref_id or 0))
        customer, ref_type, ref_id = _customer(session, q.customer_code), 'quote', q.id
        text = quote_text(facts)
    else:
        t, facts = ticket_facts(session, user, int(ref_id or 0))
        customer, ref_type, ref_id = _customer(session, t.customer_code), 'service_ticket', t.id
        text = ticket_text(facts)
    same = select(CustomerMessage).where(CustomerMessage.status == 'CHO', CustomerMessage.kind == kind,
                                         CustomerMessage.customer_code == customer.code)
    same = same.where(CustomerMessage.ref_id == ref_id) if ref_id else same.where(CustomerMessage.ref_id.is_(None))
    old = session.scalars(same.order_by(CustomerMessage.id.desc()).limit(1)).first()
    if old is not None:
        return old
    row = CustomerMessage(number=doc_numbers.next_number(session, PREFIX, today), kind=kind, customer_code=customer.code,
                          ref_type=ref_type, ref_id=ref_id, facts=facts, draft_text=text[:MAX_TEXT], text=text[:MAX_TEXT],
                          source=source if source in ('APP', 'ZALO') else 'APP', created_by=user.id)
    session.add(row)
    session.flush()
    audit.record(session, user, 'customer_message.create', 'customer_message', str(row.id), None,
                 dict(number=row.number, kind=kind, customer_code=customer.code, ref=f'{ref_type}:{ref_id}' if ref_id else '', source=row.source))
    session.commit()
    return row


# ---------- Xem, sửa, gửi ----------

def load(session: Session, user: User, msg_id: int, *, lock: bool = False) -> CustomerMessage:
    check_any(user)
    row = session.get(CustomerMessage, msg_id, with_for_update=lock)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không có tin này.', status=404)
    if not is_director(user) and row.created_by != user.id:
        raise BusinessError('FORBIDDEN', 'Tin này do người khác soạn.', status=403)
    return row


def _open(session: Session, user: User, msg_id: int, version) -> CustomerMessage:
    row = load(session, user, msg_id, lock=True)
    check_kind(user, row.kind)
    if row.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Tin này đã gửi hoặc đã bỏ.', status=409)
    if int(version or 0) != row.version:
        raise BusinessError('STALE', 'Tin vừa được sửa ở nơi khác, hãy tải lại trang.', status=409)
    return row


def _set_text(row: CustomerMessage, user: User, text) -> bool:
    text = str(text if text is not None else row.text).replace('\r\n', '\n').strip()
    if len(text) < 5:
        raise BusinessError('BAD_TEXT', 'Nội dung tin còn trống.')
    if len(text) > MAX_TEXT:
        raise BusinessError('BAD_TEXT', f'Tin dài quá {MAX_TEXT} ký tự, Zalo không gửi được. Hãy rút ngắn.')
    if text == row.text:
        return False
    row.text, row.edited_by, row.edited_at = text, user.id, _now()
    return True


def save(session: Session, user: User, msg_id: int, version, text) -> CustomerMessage:
    row = _open(session, user, msg_id, version)
    if _set_text(row, user, text):
        row.version += 1
        audit.record(session, user, 'customer_message.edit', 'customer_message', str(row.id), None, dict(length=len(row.text)))
    session.commit()
    return row


def send_bot(session: Session, user: User, msg_id: int, version, text) -> CustomerMessage:
    """Gửi qua bot Zalo công ty. Ghi tin gửi trước (zalo_bot.send_text), Zalo lỗi thì tin vẫn ở Chờ gửi để gửi lại hoặc sao chép."""
    from . import zalo_bot
    row = _open(session, user, msg_id, version)
    chat = linked_chat(session, row.customer_code)
    if chat is None:
        raise BusinessError('NOT_LINKED', 'Khách chưa gắn Zalo công ty. Hãy Sao chép rồi gửi bằng Zalo cá nhân.', status=409)
    _set_text(row, user, text)
    row.version += 1
    session.commit()
    sent = zalo_bot.send_text(session, chat, row.text, user, 'tin_khach')
    if sent.status != 'DA_GUI':
        raise BusinessError('SEND_FAILED', f'Zalo chưa gửi được: {(sent.error or "không rõ lý do")[:200]}. Tin vẫn ở Chờ gửi.', status=502)
    row = session.get(CustomerMessage, msg_id, with_for_update=True)
    row.status, row.sent_via, row.chat_pk, row.zalo_message_id = 'DA_GUI', 'BOT', chat.id, sent.id
    row.decided_by, row.decided_at, row.ai_job_id = user.id, _now(), None
    row.version += 1
    audit.record(session, user, 'customer_message.send', 'customer_message', str(row.id), dict(status='CHO'),
                 dict(status='DA_GUI', via='BOT', chat=chat.id, length=len(row.text)))
    session.commit()
    return row


def mark_personal(session: Session, user: User, msg_id: int, version, text) -> CustomerMessage:
    row = _open(session, user, msg_id, version)
    _set_text(row, user, text)
    row.status, row.sent_via, row.decided_by, row.decided_at, row.ai_job_id = 'DA_GUI', 'CA_NHAN', user.id, _now(), None
    row.version += 1
    audit.record(session, user, 'customer_message.send', 'customer_message', str(row.id), dict(status='CHO'),
                 dict(status='DA_GUI', via='CA_NHAN', length=len(row.text)))
    session.commit()
    return row


def drop(session: Session, user: User, msg_id: int, version, note: str = '') -> CustomerMessage:
    row = _open(session, user, msg_id, version)
    row.status, row.decided_by, row.decided_at, row.drop_note, row.ai_job_id = 'DA_BO', user.id, _now(), str(note or '').strip()[:300], None
    row.version += 1
    audit.record(session, user, 'customer_message.drop', 'customer_message', str(row.id), dict(status='CHO'), dict(status='DA_BO'),
                 reason=row.drop_note)
    session.commit()
    return row


# ---------- Viết lại bằng AI trên Z2 ----------

REWRITE_PROMPT = """Bạn viết lại tin nhắn Zalo của Công ty Tin Học Thành Đức gửi khách hàng cho tự nhiên, lịch sự, ngắn gọn hơn.
Quy tắc bắt buộc:
- Giữ NGUYÊN mọi con số, số tiền, ngày, mã đơn/phiếu/báo giá, serial, số tài khoản, số điện thoại, đúng như bản gốc. Không thêm số mới.
- Không thêm thông tin không có trong bản gốc. Không hứa giảm giá, không đổi hạn.
- Xưng "em", gọi "anh/chị". Văn bản thường, không markdown. Dưới 1500 ký tự.
- Chỉ trả về nội dung tin đã viết lại, không giải thích, không bọc trong dấu ngoặc.
Bản gốc ({kind}):
<<<
{text}
>>>"""

_TOKEN = re.compile(r'[A-Z]{2,4}-[0-9A-Z-]*[0-9]|\d+(?:[./]\d+)*')


def tokens(text: str) -> set[str]:
    """Các con số và mã trong tin. Tiền 1.250.000 và 1250000 coi như nhau."""
    out = set()
    for tok in _TOKEN.findall(text or ''):
        if re.fullmatch(r'\d{1,3}(?:\.\d{3})+', tok):
            tok = tok.replace('.', '')
        out.add(tok.rstrip('.'))
    return out


def compare(before: str, after: str) -> tuple[list[str], list[str]]:
    a, b = tokens(before), tokens(after)
    return sorted(a - b), sorted(b - a)


def clean_answer(text: str) -> str:
    text = (text or '').strip()
    if text.startswith('<<<'):
        text = text[3:]
    if text.endswith('>>>'):
        text = text[:-3]
    return text.strip().strip('`').strip()


def request_rewrite(session: Session, user: User, msg_id: int, version, text) -> CustomerMessage:
    from . import zalo_agent
    row = _open(session, user, msg_id, version)
    cfg = zalo_agent.config(session)
    if cfg.staff_engine not in ('codex', 'claude') or not zalo_agent.z2_alive(session):
        raise BusinessError('NO_Z2', 'Máy Z2 (Codex/Claude) đang không chạy nên chưa viết lại được. Hãy sửa tay.', status=409)
    _set_text(row, user, text)
    job = ZaloAiJob(log_id=None, engine=cfg.staff_engine, prompt=REWRITE_PROMPT.format(kind=KINDS[row.kind], text=row.text))
    session.add(job)
    session.flush()
    row.ai_job_id = job.id
    row.version += 1
    session.commit()
    return row


def rewrite_state(session: Session, row: CustomerMessage) -> dict | None:
    """Tình trạng lượt viết lại đang gắn với tin: DANG (chờ), LOI, hoặc XONG kèm bản mới và số lệch nếu có."""
    if not row.ai_job_id or row.status != 'CHO':
        return None
    job = session.get(ZaloAiJob, row.ai_job_id)
    if job is None:
        return None
    if job.status in ('CHO', 'DANG'):
        if _now() - job.created_at > timedelta(seconds=REWRITE_WAIT):
            job.status, job.error, job.done_at = 'LOI', f'Quá {REWRITE_WAIT} giây máy Z2 chưa trả lời', _now()
            session.commit()
        else:
            return dict(status='DANG')
    if job.status == 'LOI':
        return dict(status='LOI', error=job.error[:200])
    text = clean_answer(job.answer)
    missing, extra = compare(row.text, text)
    ok = bool(text) and len(text) <= MAX_TEXT and not missing and not extra
    return dict(status='XONG', text=text, ok=ok, missing=missing, extra=extra)


def finish_rewrite(session: Session, user: User, msg_id: int, version, use: bool) -> CustomerMessage:
    row = _open(session, user, msg_id, version)
    state = rewrite_state(session, row)
    if use:
        if not state or state['status'] != 'XONG' or not state['ok']:
            raise BusinessError('BAD_REWRITE', 'Bản viết lại không dùng được (sai số hoặc chưa xong). Giữ bản cũ.', status=409)
        _set_text(row, user, state['text'])
        audit.record(session, user, 'customer_message.ai', 'customer_message', str(row.id), None, dict(job=row.ai_job_id))
    row.ai_job_id = None
    row.version += 1
    session.commit()
    return row


# ---------- Danh sách, trả lời của khách ----------

def _scope(stmt, user: User):
    return stmt if is_director(user) else stmt.where(CustomerMessage.created_by == user.id)


def counts(session: Session, user: User) -> dict:
    got = dict(session.execute(_scope(select(CustomerMessage.status, func.count()).group_by(CustomerMessage.status), user)).all())
    return {key: int(got.get(status, 0)) for key, (_, status) in VIEWS.items()}


def kind_counts(session: Session, user: User, view: str) -> dict:
    status = VIEWS.get(view, VIEWS['cho'])[1]
    got = dict(session.execute(_scope(select(CustomerMessage.kind, func.count()).where(CustomerMessage.status == status)
                                      .group_by(CustomerMessage.kind), user)).all())
    return {k: int(got.get(k, 0)) for k in KINDS}


def rows(session: Session, user: User, view: str, kind: str, offset: int, size: int) -> list[CustomerMessage]:
    status = VIEWS.get(view, VIEWS['cho'])[1]
    stmt = _scope(select(CustomerMessage).where(CustomerMessage.status == status), user)
    if kind in KINDS:
        stmt = stmt.where(CustomerMessage.kind == kind)
    order = (CustomerMessage.created_at.desc(), CustomerMessage.id.desc()) if status == 'CHO' else (CustomerMessage.decided_at.desc(), CustomerMessage.id.desc())
    return list(session.scalars(stmt.order_by(*order).offset(offset).limit(size)))


def total(session: Session, user: User, view: str, kind: str) -> int:
    status = VIEWS.get(view, VIEWS['cho'])[1]
    stmt = _scope(select(func.count()).select_from(CustomerMessage).where(CustomerMessage.status == status), user)
    if kind in KINDS:
        stmt = stmt.where(CustomerMessage.kind == kind)
    return int(session.scalar(stmt) or 0)


def replies(session: Session, row: CustomerMessage, limit: int = 5) -> list[ZaloMessage]:
    """Tin khách nhắn lại vào chat bot sau khi gửi qua Zalo công ty."""
    if row.sent_via != 'BOT' or not row.chat_pk or row.decided_at is None:
        return []
    return list(session.scalars(select(ZaloMessage).where(ZaloMessage.chat_pk == row.chat_pk, ZaloMessage.direction == 'IN',
                                                          ZaloMessage.message_at > row.decided_at)
                                .order_by(ZaloMessage.message_at, ZaloMessage.id).limit(limit)))


def mark_seen(session: Session, user: User, row: CustomerMessage) -> None:
    if row.decided_by == user.id and row.reply_seen_at is None and replies(session, row, 1):
        row.reply_seen_at = _now()
        session.commit()


def unseen_replies(session: Session, user: User, days: int = 7) -> list[CustomerMessage]:
    """Tin mình gửi qua bot mà khách đã nhắn lại, mình chưa mở xem (chuông + Việc của tôi)."""
    has_reply = select(ZaloMessage.id).where(ZaloMessage.chat_pk == CustomerMessage.chat_pk, ZaloMessage.direction == 'IN',
                                             ZaloMessage.message_at > CustomerMessage.decided_at).exists()
    return list(session.scalars(select(CustomerMessage).where(
        CustomerMessage.decided_by == user.id, CustomerMessage.sent_via == 'BOT', CustomerMessage.reply_seen_at.is_(None),
        CustomerMessage.decided_at >= _now() - timedelta(days=days), has_reply).order_by(CustomerMessage.decided_at.desc()).limit(20)))


def waiting(session: Session, user: User) -> list[CustomerMessage]:
    return list(session.scalars(select(CustomerMessage).where(CustomerMessage.created_by == user.id, CustomerMessage.status == 'CHO')
                                .order_by(CustomerMessage.created_at).limit(50)))


def names(session: Session, rows_: list[CustomerMessage]) -> dict:
    codes = {r.customer_code for r in rows_}
    ids = {r.created_by for r in rows_} | {r.decided_by for r in rows_ if r.decided_by} | {r.edited_by for r in rows_ if r.edited_by}
    return dict(custs=dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_(codes))).all()) if codes else {},
                users=dict(session.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all()) if ids else {})


def ref_link(row: CustomerMessage) -> tuple[str, str] | None:
    if row.ref_type == 'quote' and row.ref_id:
        return f'/bao-gia/{row.ref_id}', row.facts.get('number', 'Báo giá')
    if row.ref_type == 'service_ticket' and row.ref_id:
        return f'/dich-vu/{row.ref_id}', row.facts.get('number', 'Phiếu')
    if row.kind == 'NHAC_NO':
        return f'/cong-no-khach-hang?q={row.customer_code}', 'Công nợ khách'
    return None

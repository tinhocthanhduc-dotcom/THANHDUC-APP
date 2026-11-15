"""Báo giá gộp nhiều máy một khách (TUẤN duyệt mẫu 15/11/2026, ví dụ Hasaki gửi 3 máy Canon 6030).

- Mỗi máy vẫn là một phiếu dịch vụ và một "lần báo giá" riêng (repair_quotes.create_in_tx), tiền vẫn ở dòng của từng phiếu.
- Nhóm BGG-yy-nnnnn chỉ gom các lần đó: một link /bgg/<mã>, một tin Zalo, một bản A4, tổng cả nhóm.
- Khách trả lời từng máy hoặc tất cả; app gọi đúng repair_quotes.agree / not_yet của từng phiếu (Khách đồng ý của xưởng),
  nên Sửa xong, công nợ, kho, hoa hồng chạy như cũ. Khách không sửa vẫn làm trên từng phiếu (thu phí kiểm tra / miễn phí).
"""
import secrets
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, doc_numbers
from . import repair_inspection as ri
from . import repair_quotes as rqs
from .errors import BusinessError
from .models import Customer, RepairInspection, RepairQuoteGroup, RepairQuoteVersion, ServiceTicket, User

PREFIX = 'BGG'
MAX_MACHINES = 30
ANSWERS = ('PA1', 'PA2', 'CHUA', '')


def link(g: RepairQuoteGroup) -> str:
    return f'{rqs.base_url()}/bgg/{g.token}'


def _m(n) -> str:
    return rqs._m(n)


# ---------- máy của một khách ----------

def candidates(session: Session, customer_code: str) -> list[dict]:
    """Máy của khách đang chờ sửa ở xưởng, khách chưa đồng ý. ready = đã Áp vào báo giá (gửi gộp được)."""
    rows = list(session.scalars(select(ServiceTicket).where(ServiceTicket.customer_code == customer_code, ServiceTicket.status == 'TAM_DUNG',
                                                           ServiceTicket.hold_reason == ri.WAITING, ServiceTicket.declined_at.is_(None),
                                                           ServiceTicket.quote_approved_at.is_(None)).order_by(ServiceTicket.id)))
    out = []
    for t in rows:
        row = session.get(RepairInspection, t.id)
        q = ri.quote(session, t, row) if row is not None and row.items and row.applied_at else None
        ready = bool(q and q['ok'])
        why = '' if ready else ('Chưa kiểm tra' if row is None else
                                'Khách chưa đồng ý · thợ kiểm tra lại' if row.recheck_reason and not row.applied_at else
                                'Chưa áp giá' if not row.applied_at else 'Còn dịch vụ chưa có giá')
        out.append(dict(ticket=t, machine=ri.machine_name(session, t) or (row.model_name if row else ''), ready=ready, why=why,
                        quote=q, names=[ln['customer_name'] for ln in q['lines']] if q else [], open=rqs.open_version(session, t.id)))
    return out


def by_customer(rows: list[dict]) -> dict[str, list[dict]]:
    """Gom các dòng Máy chờ kiểm tra theo khách (giữ thứ tự xuất hiện đầu tiên)."""
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r['ticket'].customer_code, []).append(r)
    return out


# ---------- tạo nhóm ----------

def _totals(snaps: list[dict]) -> dict:
    """Tổng nhóm: theo phương án đề xuất từng máy + tổng nếu chọn tiêu chuẩn / chính hãng cho mọi máy có."""
    chosen = dict(subtotal=0, vat=0, total=0)
    pa1 = pa2 = 0
    for s in snaps:
        c = s['chosen']
        for k in chosen:
            chosen[k] += c[k]
        alt = s.get('alt')
        pa1 += c['total'] if s['pick'] == 'PA1' or not alt else alt['total']
        pa2 += c['total'] if s['pick'] == 'PA2' or not alt else alt['total']
    return dict(chosen=chosen, pa1=pa1 if pa1 != chosen['total'] else None, pa2=pa2 if pa2 != chosen['total'] else None)


def zalo_text(session: Session, g: RepairQuoteGroup, members: list[RepairQuoteVersion]) -> str:
    snaps = [v.snapshot for v in members]
    out = [f'Dạ, Thành Đức gửi báo giá sửa {len(members)} máy ({g.number}):']
    for i, s in enumerate(snaps, 1):
        serial = f' – {s["serial"]}' if s.get('serial') else ''
        out.append(f'{i}) {s["model"]}{serial} ({s["number"]}): {_m(s["chosen"]["total"])}đ')
    tot = _totals(snaps)
    out.append(f'Tổng cộng đã gồm VAT 8%: {_m(tot["chosen"]["total"])}đ.')
    if tot['pa2']:
        out.append(f'Nếu chọn linh kiện chính hãng (máy có): {_m(tot["pa2"])}đ.')
    if tot['pa1']:
        out.append(f'Nếu chọn phương án tiêu chuẩn: {_m(tot["pa1"])}đ.')
    out.append(f'Báo giá có hiệu lực đến {g.valid_until.strftime("%d/%m/%Y")}.')
    out.append(f'Xem chi tiết từng máy: {link(g)}')
    out.append('Anh/chị đồng ý cả nhóm hoặc từng máy thì nhắn lại giúp em, bên em sửa và hẹn ngày trả máy ạ.')
    return '\n'.join(out)


def create(session: Session, user: User, customer_code: str, picks: dict[int, int | None]) -> RepairQuoteGroup:
    """picks = {ticket_id: version phiếu}. Tạo lần báo giá mới cho từng phiếu (cùng một transaction) + nhóm có link chung."""
    from .stock_docs import _transaction
    rqs.require(user)
    if len(picks) < 2:
        raise BusinessError('PICK_TWO', 'Chọn ít nhất 2 máy để gộp báo giá.')
    if len(picks) > MAX_MACHINES:
        raise BusinessError('TOO_MANY', f'Gộp tối đa {MAX_MACHINES} máy một lần.')
    with _transaction(session):
        customer = session.get(Customer, customer_code)
        if customer is None:
            raise BusinessError('NOT_FOUND', 'Không có khách này.', status=404)
        doc_numbers.lock_counter(session, PREFIX, rqs._today())
        for tid in sorted(picks):
            t = session.get(ServiceTicket, tid)
            if t is None:
                raise BusinessError('NOT_FOUND', 'Không có phiếu này.', status=404)
            if t.customer_code != customer.code:
                raise BusinessError('OTHER_CUSTOMER', f'{t.number} không phải máy của khách {customer.name}.', status=409)
        g = RepairQuoteGroup(number=doc_numbers.next_number(session, PREFIX, rqs._today()), customer_code=customer.code,
                             token=secrets.token_urlsafe(18), created_by=user.id, valid_until=rqs._today() + timedelta(days=rqs.VALID_DAYS))
        session.add(g)
        session.flush()
        members = [rqs.create_in_tx(session, user, tid, picks[tid], group_id=g.id) for tid in sorted(picks)]
        g.text = zalo_text(session, g, members)
        audit.record(session, user, 'repair_quote_group.create', 'repair_quote_group', str(g.id), None,
                     dict(number=g.number, tickets=sorted(picks), total=_totals([v.snapshot for v in members])['chosen']['total']))
    return g


# ---------- xem ----------

def members(session: Session, g: RepairQuoteGroup) -> list[RepairQuoteVersion]:
    return list(session.scalars(select(RepairQuoteVersion).where(RepairQuoteVersion.group_id == g.id).order_by(RepairQuoteVersion.ticket_id)))


def load(session: Session, user: User, gid: int, lock=False) -> RepairQuoteGroup:
    rqs.require(user)
    g = session.get(RepairQuoteGroup, gid, with_for_update=lock)
    if g is None:
        raise BusinessError('NOT_FOUND', 'Không có báo giá gộp này.', status=404)
    return g


def view(session: Session, g: RepairQuoteGroup) -> dict:
    rows = members(session, g)
    waiting = [v for v in rows if v.status == 'CHO']
    return dict(members=rows, waiting=waiting, totals=_totals([v.snapshot for v in rows]),
                open_totals=_totals([v.snapshot for v in waiting]) if waiting else None,
                tickets={v.ticket_id: session.get(ServiceTicket, v.ticket_id) for v in rows})


def for_ticket(session: Session, ticket_id: int) -> RepairQuoteGroup | None:
    """Nhóm của lần báo giá đang chờ của phiếu (hiện chú thích trên phiếu dịch vụ)."""
    v = rqs.open_version(session, ticket_id)
    return session.get(RepairQuoteGroup, v.group_id) if v is not None and v.group_id else None


# ---------- gửi ----------

def _clean_text(g: RepairQuoteGroup, text) -> str:
    from . import tin_gui_khach as tg
    text = str(text or '').strip()[:3000]
    if len(text) < 10:
        raise BusinessError('BAD_TEXT', 'Nội dung tin còn trống.')
    missing, _ = tg.compare(g.text, text)
    money = [m for m in missing if len(m) >= 4]
    if money:
        raise BusinessError('TEXT_NUMBERS', f'Tin thiếu hoặc sai số so với báo giá: {", ".join(money[:5])}. Sửa lại cho khớp.')
    return text


def _mark_members_sent(session: Session, user: User, g: RepairQuoteGroup, via: str):
    for v in members(session, g):
        if v.status == 'CHO' and v.sent_at is None:
            v.sent_via, v.sent_at, v.sent_by = via, rqs._now(), user.id


def _check_open(session: Session, g: RepairQuoteGroup):
    if g.status == 'THU_HOI':
        raise BusinessError('BAD_STATUS', 'Báo giá gộp đã thu hồi.', status=409)
    if not any(v.status == 'CHO' for v in members(session, g)):
        raise BusinessError('BAD_STATUS', 'Không còn máy nào chờ khách trả lời trong báo giá gộp này.', status=409)


def mark_sent(session: Session, user: User, gid: int, text=None) -> RepairQuoteGroup:
    g = load(session, user, gid, lock=True)
    _check_open(session, g)
    if text is not None and str(text).strip():
        g.text = _clean_text(g, text)
    g.sent_via = g.sent_via or 'COPY'
    g.sent_at, g.sent_by = (g.sent_at or rqs._now()), (g.sent_by or user.id)
    _mark_members_sent(session, user, g, 'COPY')
    audit.record(session, user, 'repair_quote_group.send', 'repair_quote_group', str(g.id), None, dict(via='COPY'))
    session.commit()
    return g


def send_bot(session: Session, user: User, gid: int, text=None) -> RepairQuoteGroup:
    from . import tin_gui_khach as tg
    from . import zalo_bot
    g = load(session, user, gid, lock=True)
    _check_open(session, g)
    chat = tg.linked_chat(session, g.customer_code)
    if chat is None:
        raise BusinessError('NOT_LINKED', 'Khách chưa gắn Zalo công ty. Hãy Sao chép tin rồi gửi bằng Zalo cá nhân.', status=409)
    if text is not None and str(text).strip():
        g.text = _clean_text(g, text)
    body = g.text
    session.commit()
    sent = zalo_bot.send_text(session, chat, body, user, 'tin_khach')
    if sent.status != 'DA_GUI':
        raise BusinessError('SEND_FAILED', f'Zalo chưa gửi được: {(sent.error or "không rõ lý do")[:200]}. Hãy thử lại hoặc Sao chép.', status=502)
    g = session.get(RepairQuoteGroup, gid, with_for_update=True)
    g.sent_via, g.sent_at, g.sent_by = 'BOT', rqs._now(), user.id
    _mark_members_sent(session, user, g, 'BOT')
    audit.record(session, user, 'repair_quote_group.send', 'repair_quote_group', str(g.id), None, dict(via='BOT', chat=chat.id))
    session.commit()
    return g


def revoke(session: Session, user: User, gid: int) -> RepairQuoteGroup:
    """Thu hồi link chung và mọi lần báo giá còn chờ trong nhóm."""
    g = load(session, user, gid, lock=True)
    if g.status == 'THU_HOI':
        raise BusinessError('BAD_STATUS', 'Báo giá gộp đã thu hồi.', status=409)
    for v in members(session, g):
        if v.status == 'CHO':
            v.status = 'THU_HOI'
    g.status = 'THU_HOI'
    audit.record(session, user, 'repair_quote_group.revoke', 'repair_quote_group', str(g.id), None, dict(number=g.number))
    session.commit()
    return g


# ---------- khách mở link ----------

def public(session: Session, token: str) -> tuple[RepairQuoteGroup | None, str, list[RepairQuoteVersion]]:
    """ok | het_han | thu_hoi | lan_moi | qua_lan | khong_co. 'ok' khi còn ít nhất một máy chờ khách hoặc đã đồng ý."""
    token = str(token or '')[:40]
    if len(token) < 16:
        return None, 'khong_co', []
    g = session.scalars(select(RepairQuoteGroup).where(RepairQuoteGroup.token == token).with_for_update()).first()
    if g is None:
        return None, 'khong_co', []
    rows = members(session, g)
    live = [v for v in rows if v.status in ('CHO', 'DONG_Y')]
    if g.status == 'THU_HOI':
        state = 'thu_hoi'
    elif not live:
        state = 'lan_moi'
    elif any(v.status == 'CHO' for v in live) and g.valid_until < rqs._today():
        state = 'het_han'
    elif g.opened_count >= rqs.MAX_OPENS:
        state = 'qua_lan'
    else:
        state = 'ok'
    if state == 'ok':
        g.opened_count += 1
        g.last_opened_at = rqs._now()
        for v in rows:
            if v.status == 'CHO':
                v.opened_count += 1
                v.last_opened_at = g.last_opened_at
    session.commit()
    return g, state, rows


# ---------- khách trả lời ----------

def reply(session: Session, user: User, gid: int, answers: dict[int, str], versions: dict[int, int | None], via: str, person: str,
          promised_return_on, reasons: dict[int, str] | None = None, note: str = '') -> dict:
    """answers = {ticket_id: PA1 | PA2 | CHUA | ''}. Kiểm tra hết trước, rồi ghi từng phiếu bằng đúng hàm của báo giá từng phiếu.

    Trả dict(agreed=[số phiếu], not_yet=[số phiếu]). Mỗi phiếu tự commit (như nút trên phiếu); lỗi giữa chừng báo phiếu nào chưa ghi.
    """
    reasons = reasons or {}
    g = load(session, user, gid)
    if g.status == 'THU_HOI':
        raise BusinessError('BAD_STATUS', 'Báo giá gộp đã thu hồi.', status=409)
    rows = {v.ticket_id: v for v in members(session, g) if v.status == 'CHO'}
    picked = {tid: a for tid, a in answers.items() if a}
    if not picked:
        raise BusinessError('NO_ANSWER', 'Chọn khách trả lời cho ít nhất một máy.')
    for tid, a in picked.items():
        v = rows.get(tid)
        if v is None:
            raise BusinessError('BAD_STATUS', 'Có máy không còn chờ khách trả lời, hãy tải lại trang.', status=409)
        if a not in ANSWERS:
            raise BusinessError('BAD_CHOICE', 'Chọn phương án có trong báo giá đã gửi.')
        number = v.snapshot.get('number', '')
        if a in ('PA1', 'PA2') and a != v.snapshot['pick'] and a != v.snapshot.get('alt_key'):
            raise BusinessError('BAD_CHOICE', f'{number}: không có phương án {a} trong báo giá đã gửi.')
        if a == 'CHUA' and reasons.get(tid) not in rqs.REASONS:
            raise BusinessError('REASON_REQUIRED', f'{number}: chọn lý do khách chưa đồng ý.')
        if a == 'CHUA' and reasons.get(tid) == 'Khác' and len(' '.join(str(note or '').split())) < 3:
            raise BusinessError('REASON_REQUIRED', 'Lý do Khác: ghi rõ khách nói gì ở ô ghi chú.')
    agrees = [tid for tid, a in picked.items() if a in ('PA1', 'PA2')]
    if agrees:
        if via not in rqs.VIA:
            raise BusinessError('BAD_VIA', 'Chọn khách trả lời qua đâu.')
        if len(' '.join(str(person or '').split())) < 2:
            raise BusinessError('PERSON_REQUIRED', 'Ghi tên người xác nhận (vd: Anh Nam kế toán).')
        if not isinstance(promised_return_on, date) or promised_return_on < rqs._today():
            raise BusinessError('BAD_APPOINTMENT', 'Chọn ngày hẹn trả từ hôm nay trở đi.')
        if g.valid_until < rqs._today() or any(rows[tid].valid_until < rqs._today() for tid in agrees):
            raise BusinessError('EXPIRED', 'Báo giá đã hết hiệu lực. Gửi lại báo giá mới cho khách.', status=409)
    done = dict(agreed=[], not_yet=[])
    for tid in sorted(picked):
        v = rows[tid]
        number = v.snapshot.get('number', '')
        try:
            if picked[tid] == 'CHUA':
                rqs.not_yet(session, user, v.id, reasons[tid], None, via, f'{g.number}{" · " + note.strip() if note and note.strip() else ""}')
                done['not_yet'].append(number)
            else:
                rqs.agree(session, user, v.id, versions.get(tid), picked[tid], via, person, promised_return_on,
                          f'{g.number}{" · " + note.strip() if note and note.strip() else ""}')
                done['agreed'].append(number)
        except BusinessError as exc:
            session.rollback()
            ok = ', '.join(done['agreed'] + done['not_yet'])
            exc.message = f'{number}: {exc.message}' + (f' (Đã ghi xong: {ok}.)' if ok else '')
            raise
    g = session.get(RepairQuoteGroup, gid, with_for_update=True)
    if not any(v.status == 'CHO' for v in members(session, g)):
        g.status = 'XONG'
    audit.record(session, user, 'repair_quote_group.reply', 'repair_quote_group', str(g.id), None,
                 dict(answers={str(k): v for k, v in picked.items()}, via=via, person=person))
    session.commit()
    return done

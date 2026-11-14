"""Gửi báo giá sửa chữa cho khách và ghi khách trả lời (đợt 4, mẫu M5–M7 14/11/2026).

- "Lưu và gửi báo giá" chụp bản khách hiện tại của phiếu (sau khi điều phối Áp vào báo giá) thành lần 1, 2…: chỉ tên
  dịch vụ cho khách, mô tả kết quả, giá chưa VAT, VAT, tổng, bảo hành, hiệu lực. Không linh kiện, giá vốn, lãi.
- Link /bg/<mã>: mã ngẫu nhiên dài, không dùng số phiếu; hết hạn theo hiệu lực, thu hồi được, tối đa MAX_OPENS lần mở;
  có lần mới thì link cũ báo không còn hiệu lực. Trang chỉ đọc bản chụp, ghi số lần khách mở.
- Tin Zalo: mẫu có sẵn; Codex/Claude trên Z2 viết lại cho tự nhiên, mọi con số và mã phải khớp mẫu, sai thì giữ mẫu.
- Khách trả lời (nhân viên ghi): Đồng ý (PA, qua đâu, ai, hẹn trả) dùng đúng nút "Khách đồng ý" của xưởng;
  Chưa đồng ý → báo lại kỹ thuật (bỏ trạng thái đã báo giá, thợ thấy lý do); Không sửa dùng nút "Khách không sửa" như cũ.
- Không tạo nợ, không trừ kho: tiền thật vẫn là dòng của phiếu, nợ chỉ phát sinh lúc Sửa xong.
"""
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audit
from . import repair_inspection as ri
from .config import get_settings
from .errors import BusinessError
from .models import Customer, RepairInspection, RepairQuoteVersion, ServiceTicket, User, ZaloAiJob
from .security import has_role
from .service_sla import TZ

VALID_DAYS = 7      # hiệu lực báo giá (mẫu đợt 4, chờ anh chốt)
WAIT_DAYS = 2       # chờ khách trả lời quá 2 ngày thì báo đỏ
MAX_OPENS = 50      # link mở tối đa
SHOW_BOTH = True    # có giá chính hãng thì bản khách hiện cả 2 phương án
AI_WAIT = 180
VIA = {'ZALO': 'Zalo', 'DIEN_THOAI': 'Điện thoại', 'TAI_QUAY': 'Tại quầy'}
REASONS = ('Giá cao', 'Muốn linh kiện chính hãng', 'Muốn rẻ hơn', 'Chờ hỏi lại', 'Khác')
STATUSES = {'CHO': 'Chờ khách trả lời', 'DONG_Y': 'Khách đồng ý', 'CHUA_DONG_Y': 'Chưa đồng ý', 'KHONG_SUA': 'Không sửa',
            'THAY_THE': 'Đã có lần mới', 'THU_HOI': 'Đã thu hồi link'}
SENT_VIA = {'BOT': 'Zalo công ty', 'COPY': 'Zalo cá nhân / gọi điện', 'IN': 'In A4'}


def _now():
    from . import service_tickets  # cùng đồng hồ với phiếu dịch vụ / Khách đồng ý
    return service_tickets._now().astimezone(TZ)


def _today():
    return _now().date()


def base_url() -> str:
    from .dispatch_alerts import base_url as url
    return url()


def link(row: RepairQuoteVersion) -> str:
    return f'{base_url()}/bg/{row.token}'


def require(user: User):
    from .service_tickets import DISPATCHERS
    if not has_role(user, *DISPATCHERS):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối hoặc giám đốc gửi báo giá và ghi khách trả lời.', status=403)


def versions(session: Session, ticket_id: int) -> list[RepairQuoteVersion]:
    return list(session.scalars(select(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == ticket_id)
                                .order_by(RepairQuoteVersion.no.desc())))


def open_version(session: Session, ticket_id: int) -> RepairQuoteVersion | None:
    return session.scalars(select(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == ticket_id, RepairQuoteVersion.status == 'CHO')
                           .order_by(RepairQuoteVersion.no.desc()).limit(1)).first()


def overdue(row: RepairQuoteVersion | None) -> bool:
    return bool(row and row.status == 'CHO' and row.sent_at and _now() - row.sent_at > timedelta(days=WAIT_DAYS))


def waiting_days(row: RepairQuoteVersion | None) -> int:
    return (_now() - row.sent_at).days if row and row.sent_at else 0


# ---------- bản chụp phần cho khách ----------

def _pa(p: dict) -> dict | None:
    if not p['ok']:
        return None
    return dict(price=p['price'], vat=p['price_vat'] - p['price'], warranty=p['warranty'])


def _totals(lines, key):
    picked = [ln[key] or ln['PA1'] for ln in lines]
    if any(p is None for p in picked):
        return None
    sub = sum(p['price'] for p in picked)
    vat = sum(p['vat'] for p in picked)
    return dict(subtotal=sub, vat=vat, total=sub + vat, warranty=max((p['warranty'] or 0 for p in picked), default=0))


def snapshot(session: Session, t: ServiceTicket, row: RepairInspection) -> dict:
    """Bản khách: chỉ phần được phép ra ngoài. Giá lấy từ bảng giá như lúc áp, đối chiếu với dòng tiền của phiếu."""
    q = ri.quote(session, t, row)
    if not q['ok']:
        raise BusinessError('NO_PRICE', 'Báo giá còn dịch vụ chưa có giá, chưa gửi được.', status=409)
    lines = []
    for ln in q['lines']:
        pa1, pa2 = _pa(ln['std']), _pa(ln['gen'])
        lines.append(dict(name=ln['customer_name'], description=ln['description'], PA1=pa1, PA2=pa2 if SHOW_BOTH or ln['genuine'] else None,
                          pick='PA2' if ln['genuine'] else 'PA1'))
    customer = session.get(Customer, t.customer_code)
    pick = 'PA2' if any(ln['pick'] == 'PA2' for ln in lines) else 'PA1'
    chosen = dict(subtotal=q['subtotal'], vat=q['vat'], total=q['total'], warranty=q['warranty'] or 0)
    other = 'PA1' if pick == 'PA2' else 'PA2'
    alt = _totals(lines, other) if any(ln[other] for ln in lines) else None
    if alt and alt['total'] == chosen['total']:
        alt = None
    return dict(number=t.number, customer=customer.name if customer else t.customer_code, model=row.model_name, serial=t.serial or '',
                symptom=' '.join((t.symptom or '').split())[:300], lines=lines, pick=pick, chosen=chosen, alt=alt,
                alt_key=other if alt else '')


def _matches_ticket(t: ServiceTicket, row: RepairInspection, snap: dict) -> bool:
    """Dòng phí đã áp trên phiếu phải đúng bằng giá trong bản chụp (không gửi giá khác với tiền thật của phiếu)."""
    applied = {x.description: x.unit_price for x in t.lines if x.kind == 'PHI' and x.stock_doc_id is None and x.description in (row.applied_lines or [])}
    want = {ln['name'][:500]: (ln[ln['pick']] or {}).get('price') for ln in snap['lines']}
    return applied == want


def zalo_text(snap: dict, row: RepairQuoteVersion) -> str:
    out = [f'Dạ, Thành Đức gửi báo giá sửa máy {snap["model"]} ({snap["number"]}, lần {row.no}):']
    for ln in snap['lines']:
        p = ln[ln['pick']]
        out.append(f'• {ln["name"]}{" (linh kiện chính hãng)" if ln["pick"] == "PA2" else ""}: {_m(p["price"])}đ')
    c = snap['chosen']
    out.append(f'VAT 8%: {_m(c["vat"])}đ. Tổng cộng: {_m(c["total"])}đ.')
    if c['warranty']:
        out.append(f'Bảo hành {c["warranty"]} tháng cho hạng mục đã sửa.')
    if snap.get('alt'):
        label = 'linh kiện chính hãng' if snap['alt_key'] == 'PA2' else 'tiêu chuẩn'
        out.append(f'Phương án {label}: tổng {_m(snap["alt"]["total"])}đ.')
    out.append(f'Báo giá có hiệu lực đến {row.valid_until.strftime("%d/%m/%Y")}.')
    out.append(f'Xem chi tiết: {link(row)}')
    out.append('Anh/chị đồng ý thì nhắn lại giúp em, bên em sửa và hẹn ngày trả máy ạ.')
    return '\n'.join(out)


def _m(n) -> str:
    return f'{int(n or 0):,}'.replace(',', '.')


def create(session: Session, user: User, ticket_id: int, version) -> RepairQuoteVersion:
    """Lưu bản khách thành lần mới (lần cũ đang chờ chuyển Đã có lần mới) và soạn tin Zalo."""
    from . import service_tickets
    from .stock_docs import _transaction
    require(user)
    with _transaction(session):
        t = ri.load_ticket(session, user, ticket_id, lock=True)
        service_tickets._check_version(t, version)
        if not ri.is_waiting(t):
            raise BusinessError('BAD_STATUS', 'Phiếu không ở bước chờ sửa tại xưởng.', status=409)
        if t.quote_approved_at:
            raise BusinessError('ALREADY_APPROVED', 'Khách đã đồng ý báo giá này, không cần gửi lại.', status=409)
        row = session.get(RepairInspection, t.id)
        if row is None or row.applied_at is None:
            raise BusinessError('NOT_APPLIED', 'Bấm Áp vào báo giá trước, rồi mới gửi khách.', status=409)
        snap = snapshot(session, t, row)
        if not _matches_ticket(t, row, snap):
            raise BusinessError('STALE_QUOTE', 'Giá trên phiếu khác bảng giá hiện tại (vừa đổi giá?). Bấm Áp lại báo giá rồi gửi.', status=409)
        for old in session.scalars(select(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == t.id,
                                                                     RepairQuoteVersion.status == 'CHO').with_for_update()):
            old.status = 'THAY_THE'
        no = (session.scalar(select(func.max(RepairQuoteVersion.no)).where(RepairQuoteVersion.ticket_id == t.id)) or 0) + 1
        v = RepairQuoteVersion(ticket_id=t.id, no=no, token=secrets.token_urlsafe(18), snapshot=snap, created_by=user.id,
                               valid_until=_today() + timedelta(days=VALID_DAYS))
        snap['valid_until'] = v.valid_until.isoformat()
        session.add(v)
        session.flush()
        v.text = zalo_text(snap, v)
        v.ai_job_id = _request_ai(session, v.text)
        audit.record(session, user, 'repair_quote.create', 'service_ticket', str(t.id), None,
                     dict(no=no, total=snap['chosen']['total'], pick=snap['pick'], valid_until=v.valid_until.isoformat()))
    return v


def _request_ai(session: Session, text: str) -> int | None:
    from . import tin_gui_khach as tg
    from . import zalo_agent
    cfg = zalo_agent.config(session)
    if cfg.staff_engine not in ('codex', 'claude') or not zalo_agent.z2_alive(session):
        return None
    job = ZaloAiJob(log_id=None, engine=cfg.staff_engine, prompt=tg.REWRITE_PROMPT.format(kind='Báo giá sửa chữa', text=text))
    session.add(job)
    session.flush()
    return job.id


def ai_text(session: Session, v: RepairQuoteVersion) -> dict:
    """Bản Codex viết lại: DANG / LOI / XONG (ok khi mọi số, mã, link khớp tin mẫu). Không có thì status None."""
    from . import tin_gui_khach as tg
    job = session.get(ZaloAiJob, v.ai_job_id) if v.ai_job_id else None
    if job is None:
        return dict(status=None)
    if job.status in ('CHO', 'DANG'):
        if _now() - job.created_at > timedelta(seconds=AI_WAIT):
            return dict(status='LOI', error='Quá 3 phút máy Z2 chưa soạn xong')
        return dict(status='DANG')
    if job.status == 'LOI':
        return dict(status='LOI', error=(job.error or '')[:200])
    text = tg.clean_answer(job.answer)
    missing, extra = tg.compare(v.text, text)
    ok = bool(text) and len(text) <= 2000 and not missing and not extra and link(v) in text
    return dict(status='XONG', text=text, ok=ok, missing=missing, extra=extra)


def suggested_text(session: Session, v: RepairQuoteVersion) -> tuple[str, str]:
    """Tin hiện trong ô: đã gửi/đã sửa > Codex (đạt kiểm tra số) > mẫu. Trả (text, nguồn)."""
    if v.sent_at or v.ai_job_id is None:
        return v.text, 'mau' if not v.sent_at else 'gui'
    state = ai_text(session, v)
    if state['status'] == 'XONG' and state['ok']:
        return state['text'], 'ai'
    return v.text, 'mau'


def _load(session: Session, user: User, vid: int, lock=False) -> RepairQuoteVersion:
    require(user)
    v = session.get(RepairQuoteVersion, vid, with_for_update=lock)
    if v is None:
        raise BusinessError('NOT_FOUND', 'Không có lần báo giá này.', status=404)
    return v


def _clean_text(v: RepairQuoteVersion, text) -> str:
    from . import tin_gui_khach as tg
    text = str(text or '').strip()[:2000]
    if len(text) < 10:
        raise BusinessError('BAD_TEXT', 'Nội dung tin còn trống.')
    missing, _ = tg.compare(v.text, text)
    money = [m for m in missing if len(m) >= 4]
    if money:
        raise BusinessError('TEXT_NUMBERS', f'Tin thiếu hoặc sai số so với báo giá: {", ".join(money[:5])}. Sửa lại cho khớp.')
    return text


def mark_sent(session: Session, user: User, vid: int, via: str, text=None) -> RepairQuoteVersion:
    """Ghi đã gửi: Zalo cá nhân / gọi điện (COPY) hoặc In A4 (IN). Gửi lại không đổi bản chụp."""
    v = _load(session, user, vid, lock=True)
    if v.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Lần báo giá này không còn chờ khách trả lời.', status=409)
    if via not in ('COPY', 'IN'):
        raise BusinessError('BAD_VIA', 'Chọn cách gửi.')
    if text is not None and str(text).strip():
        v.text = _clean_text(v, text)
    first = v.sent_at is None
    v.sent_via = v.sent_via if (via == 'IN' and v.sent_via) else via
    v.sent_at, v.sent_by = (v.sent_at or _now()), (v.sent_by or user.id)
    audit.record(session, user, 'repair_quote.send', 'service_ticket', str(v.ticket_id), None, dict(no=v.no, via=via, first=first))
    session.commit()
    return v


def send_bot(session: Session, user: User, vid: int, text=None) -> RepairQuoteVersion:
    from . import tin_gui_khach as tg
    from . import zalo_bot
    v = _load(session, user, vid, lock=True)
    if v.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Lần báo giá này không còn chờ khách trả lời.', status=409)
    t = session.get(ServiceTicket, v.ticket_id)
    chat = tg.linked_chat(session, t.customer_code)
    if chat is None:
        raise BusinessError('NOT_LINKED', 'Khách chưa gắn Zalo công ty. Hãy Sao chép tin rồi gửi bằng Zalo cá nhân.', status=409)
    if text is not None and str(text).strip():
        v.text = _clean_text(v, text)
    body = v.text
    session.commit()
    sent = zalo_bot.send_text(session, chat, body, user, 'tin_khach')
    if sent.status != 'DA_GUI':
        raise BusinessError('SEND_FAILED', f'Zalo chưa gửi được: {(sent.error or "không rõ lý do")[:200]}. Hãy thử lại hoặc Sao chép.', status=502)
    v = session.get(RepairQuoteVersion, vid, with_for_update=True)
    v.sent_via, v.sent_at, v.sent_by = 'BOT', _now(), user.id
    audit.record(session, user, 'repair_quote.send', 'service_ticket', str(v.ticket_id), None, dict(no=v.no, via='BOT', chat=chat.id))
    session.commit()
    return v


def revoke(session: Session, user: User, vid: int) -> RepairQuoteVersion:
    v = _load(session, user, vid, lock=True)
    if v.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Chỉ thu hồi link báo giá đang chờ khách trả lời.', status=409)
    v.status = 'THU_HOI'
    audit.record(session, user, 'repair_quote.revoke', 'service_ticket', str(v.ticket_id), None, dict(no=v.no))
    session.commit()
    return v


# ---------- khách mở link ----------

def public(session: Session, token: str) -> tuple[RepairQuoteVersion | None, str]:
    """Trang công khai: trả (bản, tình trạng). ok | het_han | thu_hoi | lan_moi | bao_lai | qua_lan | khong_co. Chỉ 'ok' mới hiện giá."""
    token = str(token or '')[:40]
    if len(token) < 16:
        return None, 'khong_co'
    v = session.scalars(select(RepairQuoteVersion).where(RepairQuoteVersion.token == token).with_for_update()).first()
    if v is None:
        return None, 'khong_co'
    if v.status == 'THU_HOI':
        state = 'thu_hoi'
    elif v.status == 'THAY_THE':
        state = 'lan_moi'
    elif v.status == 'CHUA_DONG_Y':
        state = 'bao_lai'
    elif v.status == 'CHO' and v.valid_until < _today():
        state = 'het_han'
    elif v.opened_count >= MAX_OPENS:
        state = 'qua_lan'
    else:
        state = 'ok'
    if state == 'ok':
        v.opened_count += 1
        v.last_opened_at = _now()
    session.commit()
    return v, state


# ---------- khách trả lời ----------

def agree(session: Session, user: User, vid: int, ticket_version, choice: str, via: str, person: str, promised_return_on,
          note: str = '') -> RepairQuoteVersion:
    """Khách đồng ý: khách chọn phương án khác bản đang áp thì áp lại theo phương án đó (giá phải đúng bản đã gửi),
    rồi bấm đúng nút "Khách đồng ý" của xưởng (ngày hẹn trả). Ghi PA, qua đâu, ai xác nhận lên lần báo giá."""
    from . import service_workshop
    v = _load(session, user, vid)
    if v.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Lần báo giá này không còn chờ khách trả lời.', status=409)
    if v.valid_until < _today():
        raise BusinessError('EXPIRED', 'Báo giá đã hết hiệu lực. Gửi lại báo giá mới cho khách.', status=409)
    if choice not in ('PA1', 'PA2') or (choice != v.snapshot['pick'] and choice != v.snapshot.get('alt_key')):
        raise BusinessError('BAD_CHOICE', 'Chọn phương án có trong báo giá đã gửi.')
    if via not in VIA:
        raise BusinessError('BAD_VIA', 'Chọn khách trả lời qua đâu.')
    person = ' '.join(str(person or '').split())[:200]
    if len(person) < 2:
        raise BusinessError('PERSON_REQUIRED', 'Ghi tên người xác nhận (vd: Anh Nam kế toán).')
    from datetime import date
    if not isinstance(promised_return_on, date) or promised_return_on < _today():
        raise BusinessError('BAD_APPOINTMENT', 'Chọn ngày hẹn trả từ hôm nay trở đi.')
    t = session.get(ServiceTicket, v.ticket_id)
    version = ticket_version
    if choice != v.snapshot['pick']:
        row = session.get(RepairInspection, t.id)
        choices = {i.service_id: choice == 'PA2' for i in row.items}
        if ri.quote(session, t, row, choices)['total'] != (v.snapshot.get('alt') or {}).get('total'):
            raise BusinessError('PRICE_CHANGED', 'Giá phương án khách chọn đã đổi so với lúc gửi. Gửi lại báo giá mới.', status=409)
        ri.apply(session, user, t.id, version, choices)
        session.refresh(t)
        version = t.version
    service_workshop.quote_approve(session, user, t.id, version, promised_return_on,
                                   f'Báo giá lần {v.no} · {choice} · {VIA[via]} · {person}{" · " + note.strip()[:500] if note.strip() else ""}')
    v = session.get(RepairQuoteVersion, vid, with_for_update=True)
    v.status, v.reply_choice, v.reply_via, v.reply_person = 'DONG_Y', choice, via, person
    v.reply_note, v.replied_by, v.replied_at = str(note or '').strip()[:2000], user.id, _now()
    audit.record(session, user, 'repair_quote.agree', 'service_ticket', str(t.id), None, dict(no=v.no, choice=choice, via=via, person=person))
    session.commit()
    return v


def not_yet(session: Session, user: User, vid: int, reason: str, wanted_price=None, via: str = '', note: str = '') -> RepairQuoteVersion:
    """Khách chưa đồng ý → báo lại kỹ thuật: máy về Chờ kiểm tra lại, thợ thấy lý do; điều phối áp và gửi lần sau."""
    v = _load(session, user, vid, lock=True)
    if v.status != 'CHO':
        raise BusinessError('BAD_STATUS', 'Lần báo giá này không còn chờ khách trả lời.', status=409)
    if reason not in REASONS:
        raise BusinessError('REASON_REQUIRED', 'Chọn lý do khách chưa đồng ý.')
    note = ' '.join(str(note or '').split())[:500]
    if reason == 'Khác' and len(note) < 3:
        raise BusinessError('REASON_REQUIRED', 'Lý do Khác: ghi rõ khách nói gì.')
    row = session.get(RepairInspection, v.ticket_id, with_for_update=True)
    v.status, v.reply_reason, v.reply_via, v.reply_note = 'CHUA_DONG_Y', reason, via if via in VIA else '', note
    v.wanted_price, v.replied_by, v.replied_at = wanted_price, user.id, _now()
    wish = f' · khách muốn khoảng {_m(wanted_price)}đ' if wanted_price else ''
    row.recheck_reason = f'Báo giá lần {v.no}: {reason}{wish}{" · " + note if note else ""}'[:1000]
    row.applied_at = row.applied_by = None
    audit.record(session, user, 'repair_quote.not_yet', 'service_ticket', str(v.ticket_id), None,
                 dict(no=v.no, reason=reason, wanted=wanted_price, note=note))
    session.commit()
    return v


def close_open(session: Session, ticket_id: int, status: str) -> None:
    """Gọi từ nút xưởng (Khách đồng ý / Khách không sửa) trong cùng transaction: lần đang chờ được đóng theo."""
    v = open_version(session, ticket_id)
    if v is not None:
        v.status = status
        if v.replied_at is None:
            v.replied_at = _now()


def link_settings() -> dict:
    s = get_settings()
    return dict(name=s.company_name, phone=s.company_phone, hotline=s.company_hotline, address=s.company_address,
                website=s.company_website)

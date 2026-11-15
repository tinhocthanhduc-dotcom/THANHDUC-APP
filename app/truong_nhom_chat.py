"""Trưởng nhóm AI chat Zalo với giám đốc (Đội AI đợt 3, TUẤN duyệt 15/11/2026, mẫu FWswiMz2fUiAB5e32mighf).

Giám đốc nhắn bot Zalo công ty như nhắn một nhân viên. Trợ lý Zalo (zalo_agent._staff) thêm các công cụ dưới đây khi:
người hỏi là giám đốc (Q9), Trưởng nhóm AI đang bật và cho phép chat, máy trả lời là Claude/Codex trên Z2 (không API).

- tinh_hinh_doi: ai đang bao nhiêu việc, việc nào trễ, tin khách chờ.
- giao_phieu: giao hoặc đổi thợ phiếu dịch vụ, làm ngay (Q8), hoàn tác trong undo_minutes phút. Phiếu đang cầm hàng
  của thợ cũ thì không đổi (không đụng kho).
- nhac_nhan_vien: nhắn Zalo nhân viên + đặt việc có hạn (team_tasks gd_nhac); tới hạn chưa xong thì báo giám đốc.
- doi_han: dời hạn một việc trong bảng Đội AI (giữ hạn đó, không tính lại).
- hoan_tac: trả phiếu vừa đổi thợ về thợ cũ.
Không có công cụ nào đụng tiền, giá, kho, công nợ, lương, hủy hay đóng chứng từ, nhắn khách.
"""
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select

from . import nhan_vien_ao as nva
from . import service_tickets
from . import truong_nhom as tn
from .errors import BusinessError
from .models import ServiceStep, ServiceTicket, TeamTask, User, VirtualAgentLog, ZaloMessage, ZaloTriage
from .service_sla import TZ
from .text import fold

HISTORY = 20
HISTORY_CHARS = 1800


def _fn(name, desc, props, required):
    return dict(type='function', function=dict(name=name, description=desc,
                                               parameters=dict(type='object', properties=props, required=required, additionalProperties=False)))


_S = dict(type='string')
T_STATUS = _fn('tinh_hinh_doi', 'Tình hình cả đội lúc này: mỗi người bao nhiêu việc, bao nhiêu việc trễ, từng việc trễ kèm link, tin khách Zalo chưa trả lời. '
               'Có tên người thì chỉ trả việc của người đó.', dict(nguoi=dict(type='string', description='Tên nhân viên, để trống là cả đội')), ['nguoi'])
T_ASSIGN = _fn('giao_phieu', 'Giao hoặc chuyển một PHIẾU DỊCH VỤ cho thợ khác theo lệnh giám đốc. Làm ngay. Chỉ phiếu dịch vụ, không phải đơn bán.',
               dict(so_phieu=dict(type='string', description='Số phiếu, viết tắt như 0408 cũng được'), nguoi=dict(type='string', description='Tên thợ nhận')),
               ['so_phieu', 'nguoi'])
T_REMIND = _fn('nhac_nhan_vien', 'Nhắn Zalo nhắc một nhân viên làm một việc, đặt hạn; tới hạn chưa xong thì báo giám đốc.',
               dict(nguoi=dict(type='string'), noi_dung=dict(type='string', description='Việc cần làm, ngắn gọn, viết như lời giám đốc dặn'),
                    han=dict(type='string', description='YYYY-MM-DD HH:MM, chuỗi rỗng nếu giám đốc không nói hạn')), ['nguoi', 'noi_dung', 'han'])
T_DUE = _fn('doi_han', 'Dời hạn một việc đang theo dõi (số chứng từ hoặc vài chữ trong tên việc).',
            dict(viec=dict(type='string'), han=dict(type='string', description='YYYY-MM-DD HH:MM'), ly_do=dict(type='string')), ['viec', 'han', 'ly_do'])
T_UNDO = _fn('hoan_tac', 'Hoàn tác lần giao/đổi thợ gần nhất giám đốc vừa nhắn (trả phiếu về thợ cũ).', {}, [])

RULE = """- Bạn cũng là Trưởng nhóm AI, theo dõi việc cả đội cho giám đốc. Hỏi ai trễ, ai bao nhiêu việc, tin khách nào chờ thì dùng tinh_hinh_doi.
  Giám đốc ra lệnh giao/chuyển phiếu dịch vụ thì giao_phieu; nhắc nhân viên làm việc gì thì nhac_nhan_vien; dời hạn thì doi_han; "hoàn tác" thì hoan_tac.
  Lệnh rõ thì làm ngay rồi báo lại đúng kết quả công cụ. Không chắc phiếu nào, người nào thì hỏi lại, không đoán. Số phiếu viết tắt (0408) đưa nguyên cho công cụ.
  Đọc đoạn chat trước để hiểu "nó", "phiếu đó", "ừ làm đi". Giờ "3h chiều" là 15:00 hôm nay ({today}).
- Không bao giờ tự đổi tiền, giá, giảm giá, kho, công nợ, lương; không chốt, hủy, đóng phiếu; không nhắn khách. Giám đốc nhờ những việc đó thì nói
  em không tự làm được và gửi link chứng từ để anh mở app bấm; nhắn khách thì soạn tin nháp bằng soan_tin_khach nếu có.
"""
ABILITIES = ['tình hình cả đội', 'giao hoặc đổi thợ phiếu dịch vụ', 'nhắc nhân viên', 'dời hạn việc']


def enabled(session, user, engine: str) -> bool:
    if user is None or not tn._is_director(user) or engine == 'api':
        return False
    row = tn.active(session)
    return row is not None and bool(nva.cfg(row).get('chat', True))


def history(session, chat_pk: int, before_id: int) -> str:
    """Đoạn chat gần nhất (cũ → mới) để AI hiểu "phiếu đó", "ừ làm đi"."""
    from .zalo_agent import Z2_WAIT_TEXT
    rows = list(session.scalars(select(ZaloMessage).where(ZaloMessage.chat_pk == chat_pk, ZaloMessage.id < before_id,
                                                          or_(ZaloMessage.direction == 'OUT', ZaloMessage.kind == 'text'),
                                                          ZaloMessage.text != '').order_by(ZaloMessage.id.desc()).limit(HISTORY)))
    lines, size = [], 0
    for m in rows:
        if m.text.strip() == Z2_WAIT_TEXT:
            continue
        line = ('Giám đốc: ' if m.direction == 'IN' else 'Em: ') + ' '.join(m.text.split())[:300]
        if size + len(line) > HISTORY_CHARS:
            break
        lines.append(line)
        size += len(line) + 1
    return '\n'.join(reversed(lines))


def question(session, chat, msg) -> str:
    past = history(session, chat.id, msg.id)
    return f'Đoạn chat trước (cũ → mới):\n{past}\n\nTin mới của giám đốc: {msg.text}' if past else msg.text


# ---------- tìm người, phiếu, giờ ----------

def _people(session, roles=None):
    out = [u for u in tn._people(session) if not tn._is_director(u)]
    return [u for u in out if roles is None or set(u.roles or []) & set(roles)]


def find_user(session, name: str, roles=None):
    """Khớp tên có dấu trước (Thạnh khác Thanh), không ra thì khớp không dấu. Trả (user, lỗi)."""
    raw = ' '.join(str(name or '').lower().split())
    if not raw:
        return None, 'Chưa nói tên người.'
    people = _people(session, roles)
    for key in (lambda s: ' '.join(s.lower().split()), fold):
        q = key(raw)
        exact = [u for u in people if key(u.full_name) == q or key(u.full_name).split()[-1] == q]
        hits = exact or [u for u in people if q in key(u.full_name)]
        if len(hits) == 1:
            return hits[0], ''
        if len(hits) > 1:
            return None, 'Có nhiều người khớp: ' + ', '.join(u.full_name for u in hits) + '. Hỏi lại giám đốc là ai.'
    return None, f'Không thấy nhân viên "{name}"' + (' là thợ' if roles else '') + '.'


def find_ticket(session, number: str):
    raw = str(number or '').strip().upper()
    if not raw:
        return None, 'Chưa nói số phiếu.'
    t = session.scalar(select(ServiceTicket).where(ServiceTicket.number == raw))
    if t is not None:
        return t, ''
    digits = ''.join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None, f'Không thấy phiếu {number}.'
    hits = list(session.scalars(select(ServiceTicket).where(ServiceTicket.number.like(f'%{digits}'),
                                                            ServiceTicket.status.in_(service_tickets.ASSIGNABLE)).order_by(ServiceTicket.id.desc()).limit(4)))
    if len(hits) == 1:
        return hits[0], ''
    if hits:
        return None, 'Có nhiều phiếu khớp: ' + ', '.join(h.number for h in hits) + '. Hỏi lại giám đốc.'
    return None, f'Không thấy phiếu dịch vụ đang mở số {number}.'


def parse_when(raw: str, now: datetime):
    text = str(raw or '').strip()
    if not text:
        return None
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            at = datetime.strptime(text[:16], fmt)
        except ValueError:
            continue
        if fmt == '%Y-%m-%d':
            at = at.replace(hour=17, minute=30)
        return at.replace(tzinfo=TZ)
    raise BusinessError('VALIDATION', f'Giờ "{raw}" chưa đúng dạng YYYY-MM-DD HH:MM.')


def _when_text(at, now):
    local = at.astimezone(TZ)
    return local.strftime('%H:%M hôm nay' if local.date() == now.astimezone(TZ).date() else '%H:%M %d/%m')


# ---------- công cụ ----------

def tool_status(session, name: str, now=None) -> dict:
    now = now or tn._now()
    if tn._last['sync'] is None or now - tn._last['sync'] > tn.EVERY:
        tn.sync(session, now)
        tn._last['sync'] = now
    base = nva._base()
    only = None
    if str(name or '').strip():
        only, err = find_user(session, name)
        if only is None:
            return dict(loi=err)
    names = dict(session.execute(select(User.id, User.full_name)).all())
    tasks = [t for t in tn.open_tasks(session, only.id if only else None) if t.item_key not in tn.NO_DUE]
    per = {}
    for t in tasks:
        p = per.setdefault(t.owner_id, dict(nguoi=names.get(t.owner_id, ''), so_viec=0, tre=0))
        p['so_viec'] += 1
        p['tre'] += int(t.due_at is not None and t.due_at < now)
    late = sorted((t for t in tasks if t.due_at is not None and t.due_at < now), key=lambda t: tn._rank(t, now))
    show = late[:8] if not only else sorted(tasks, key=lambda t: tn._rank(t, now))[:8]
    rows = [dict(so=t.number, viec=t.title, nguoi=names.get(t.owner_id, ''), han=tn.when(t, now), link=base + t.link if t.link.startswith('/') else t.link,
                 da_nhac=t.reminded_at is not None) for t in show]
    out = dict(moi_nguoi=sorted(per.values(), key=lambda p: -p['so_viec']), qua_tai_tu=int(tn.cfg(session)['overload']),
               **({'viec_cua_nguoi': rows} if only else {'viec_tre': rows, 'tong_tre': len(late)}))
    if not only:
        out['tin_khach_chua_tra_loi'] = session.scalar(select(func.count()).select_from(ZaloTriage).where(
            ZaloTriage.done_at.is_(None), ZaloTriage.status.in_(('XONG', 'LOI')), ZaloTriage.owner_id.is_not(None))) or 0
    return out


def _log(session, **kw):
    entry = VirtualAgentLog(agent=tn.CODE, **kw)
    session.add(entry)
    return entry


def tool_assign(session, boss: User, number: str, name: str, quote: str, sender=None, now=None) -> dict:
    now = now or tn._now()
    ticket, err = find_ticket(session, number)
    if ticket is None:
        return dict(loi=err)
    tech, err = find_user(session, name, roles=('technician',))
    if tech is None:
        return dict(loi=err)
    if ticket.status not in service_tickets.ASSIGNABLE:
        return dict(loi=f'Phiếu {ticket.number} đã đóng, không giao được.')
    if ticket.assignee_id == tech.id:
        return dict(loi=f'Phiếu {ticket.number} đang giao cho {tech.full_name} rồi.')
    old_id = ticket.assignee_id
    if old_id is not None and tn._has_stock(session, ticket):
        return dict(loi=f'Phiếu {ticket.number} đang cầm hàng của thợ cũ, em không tự đổi để không đụng kho. Anh mở phiếu bấm Giao thợ khác.',
                    link=nva._base() + f'/dich-vu/{ticket.id}')
    names = dict(session.execute(select(User.id, User.full_name)).all())
    why = f'Giám đốc nhắn Zalo: "{quote[:200]}"'
    session.commit()
    try:
        with service_tickets._transaction(session):
            service_tickets.assign_in_tx(session, boss, ticket.id, tech.id, ticket.version, 'Giám đốc nhắn Zalo · Trưởng nhóm AI')
            if old_id is None:
                entry = _log(session, action='LENH', ticket_id=ticket.id, target_id=tech.id, text=f'Giao {ticket.number} cho {tech.full_name}', reason=why)
            else:
                entry = _log(session, action='DOI_NGUOI', ticket_id=ticket.id, target_id=tech.id,
                             text=f'Chuyển {ticket.number} từ {names.get(old_id, "")} sang {tech.full_name}', reason=why)
    except BusinessError as exc:
        session.rollback()
        return dict(loi=exc.message)
    undo = int(tn.cfg(session)['undo_minutes'])
    if old_id is not None:
        tn._tell(session, entry, ticket, old_id, dict(id=tech.id, name=tech.full_name), 'giám đốc nhắn', undo, sender)
    return dict(da_giao=ticket.number, cho=tech.full_name, tho_cu=names.get(old_id, '') if old_id else '',
                hoan_tac=f'nhắn "hoàn tác" trong {undo} phút' if old_id is not None and undo else '', link=nva._base() + f'/dich-vu/{ticket.id}')


def tool_remind(session, boss: User, name: str, what: str, due_raw: str, sender=None, now=None) -> dict:
    now = now or tn._now()
    user, err = find_user(session, name)
    if user is None:
        return dict(loi=err)
    what = ' '.join(str(what or '').split())[:300]
    if not what:
        return dict(loi='Chưa nói nhắc việc gì.')
    try:
        due = parse_when(due_raw, now) or now + timedelta(hours=2)
    except BusinessError as exc:
        return dict(loi=exc.message)
    if due <= now:
        return dict(loi='Hạn đã qua, hỏi lại giám đốc hạn mới.')
    t = TeamTask(item_key=tn.GD_NHAC, ref=f'{now.strftime("%Y%m%d%H%M%S")}-{user.id}', number='Việc GĐ', title=what, link='/viec-cua-toi',
                 owner_id=user.id, urgent=True, due_at=due, due_override=due, first_seen_at=now, last_seen_at=now, note=f'Giám đốc {boss.full_name} nhắn Zalo')
    session.add(t)
    session.flush()
    t.link = f'/viec-cua-toi#tn-{t.id}'
    _log(session, action='LENH', target_id=user.id, text=f'Nhắc {user.full_name}: {what}'[:400], reason=f'Hạn {_when_text(due, now)}')
    session.commit()
    sent = False
    chat = nva.chats(session).get(user.id)
    if chat is not None and nva._zalo_ready():
        body = (f'Giám đốc nhờ bạn: {what}\nHạn: {_when_text(due, now)}.\n'
                f'Làm xong bấm Xong ở Việc của tôi: {nva._base()}/viec-cua-toi#tn-{t.id}')
        sent = nva._send(session, chat, user.id, f'tn-lenh:{t.id}', body, nva._sender(sender))
    return dict(da_nhac=user.full_name, viec=what, han=_when_text(due, now),
                da_nhan_zalo=sent, ghi_chu='' if sent else 'Nhân viên chưa gắn Zalo, việc chỉ hiện trong Việc của tôi.')


def tool_due(session, boss: User, what: str, due_raw: str, why: str, now=None) -> dict:
    now = now or tn._now()
    try:
        due = parse_when(due_raw, now)
    except BusinessError as exc:
        return dict(loi=exc.message)
    if due is None:
        return dict(loi='Chưa nói hạn mới.')
    q = ' '.join(str(what or '').split())
    if not q:
        return dict(loi='Chưa nói việc nào.')
    tasks = tn.open_tasks(session)
    up = q.upper()
    digits = ''.join(ch for ch in q if ch.isdigit())
    hits = [t for t in tasks if t.number and t.number.upper() == up]
    if not hits and digits and len(digits) >= 3:
        hits = [t for t in tasks if t.number and ''.join(ch for ch in t.number if ch.isdigit()).endswith(digits)]
    if not hits:
        hits = [t for t in tasks if fold(q) in fold(t.title)]
    if not hits:
        return dict(loi=f'Không thấy việc "{what}" đang theo dõi.')
    if len({t.number or t.id for t in hits}) > 1:
        return dict(loi='Có nhiều việc khớp: ' + '; '.join(f'{t.number} {t.title}' for t in hits[:5]) + '. Hỏi lại giám đốc.')
    note = ' '.join(str(why or '').split())[:200]
    for t in hits:
        t.due_override = t.due_at = due
        t.reminded_at = t.dispatch_at = t.director_at = None
        t.note = (f'Giám đốc dời hạn: {note}' if note else 'Giám đốc dời hạn')[:400]
    t = hits[0]
    _log(session, action='LENH', target_id=t.owner_id, text=f'Dời hạn {t.number} {t.title}'[:400] + f' sang {_when_text(due, now)}', reason=note)
    session.commit()
    owner = session.get(User, t.owner_id)
    return dict(da_doi=t.number, viec=t.title, han_moi=_when_text(due, now), nguoi=owner.full_name if owner else '', ly_do=note)


def tool_undo(session, boss: User, now=None) -> dict:
    now = now or tn._now()
    undo = int(tn.cfg(session)['undo_minutes'])
    entry = session.scalar(select(VirtualAgentLog).where(
        VirtualAgentLog.agent == tn.CODE, VirtualAgentLog.action == 'DOI_NGUOI', VirtualAgentLog.undone_at.is_(None),
        VirtualAgentLog.at >= now - timedelta(minutes=undo)).order_by(VirtualAgentLog.id.desc()).limit(1))
    if entry is None:
        return dict(loi=f'Không có lần đổi thợ nào trong {undo} phút qua để hoàn tác.')
    try:
        t = tn.undo(session, boss, entry.id)
    except BusinessError as exc:
        session.rollback()
        return dict(loi=exc.message)
    step = session.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id).order_by(ServiceStep.id.desc()).limit(1))
    who = session.get(User, step.assignee_id) if step and step.assignee_id else None
    return dict(da_hoan_tac=t.number, ve=who.full_name if who else '')


def build(session, boss: User, msg_text: str, sender=None) -> tuple[list, dict]:
    handlers = dict(
        tinh_hinh_doi=lambda a: tool_status(session, a.get('nguoi')),
        giao_phieu=lambda a: tool_assign(session, boss, a.get('so_phieu'), a.get('nguoi'), msg_text, sender),
        nhac_nhan_vien=lambda a: tool_remind(session, boss, a.get('nguoi'), a.get('noi_dung'), a.get('han'), sender),
        doi_han=lambda a: tool_due(session, boss, a.get('viec'), a.get('han'), a.get('ly_do')),
        hoan_tac=lambda a: tool_undo(session, boss))
    return [T_STATUS, T_ASSIGN, T_REMIND, T_DUE, T_UNDO], handlers


# ---------- nhân viên bấm Xong việc giám đốc nhắc ----------

def finish(session, user: User, task_id: int, sender=None) -> TeamTask:
    t = session.get(TeamTask, task_id, with_for_update=True)
    if t is None or t.item_key != tn.GD_NHAC:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy việc này.', status=404)
    if t.owner_id != user.id and not tn._is_director(user):
        raise BusinessError('FORBIDDEN', 'Việc này giao cho người khác.', status=403)
    if t.done_at is not None:
        return t
    now = tn._now()
    t.done_at = now
    _log(session, action='LENH', target_id=user.id, text=f'{user.full_name} xong việc giám đốc nhắc: {t.title}'[:400])
    session.commit()
    if nva._zalo_ready() and not tn._is_director(user):
        late = t.due_at is not None and now > t.due_at
        body = f'{user.full_name} đã xong việc anh nhắc: {t.title}' + (' (trễ hạn)' if late else '') + '.'
        for boss, chat in tn._directors(session):
            nva._send(session, chat, boss.id, f'tn-xong:{t.id}', body, nva._sender(sender))
    return t


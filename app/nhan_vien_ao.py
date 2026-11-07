"""Nhân viên AI đợt 1 (TUẤN duyệt phương án 07/11/2026): app tự làm một phần việc theo quy tắc giám đốc đặt.

Chạy bằng code trên máy chủ, không gọi API. Mỗi nhân viên AI có công tắc riêng, TAT_CA là công tắc tổng.
Mọi việc đều ghi vào virtual_agent_log kèm lý do; trang /nhan-vien-ao (chỉ giám đốc) xem nhật ký và sửa cài đặt.

Ranh giới cứng: không đụng tiền, kho, công nợ; không hủy, không đóng phiếu; không nhắn khách. Chỉ nhắn nội bộ.

- Điều phối AI (DIEU_PHOI): phiếu dễ hết giờ thợ tự nhận mà chưa ai nhận thì giao luôn cho thợ đứng đầu gợi ý,
  thợ phải đã vào ca và không nghỉ phép. Giao qua đúng hàm service_tickets.assign_in_tx bằng tài khoản hệ thống
  "Nhân viên AI" (không đăng nhập được), nên nhật ký phiếu, KPI, hoa hồng thợ như điều phối giao tay.
  Điều phối có undo_minutes phút để Hoàn tác (phiếu về Cần giao thợ) khi thợ chưa bấm Nhận; Đổi thợ lúc nào cũng được.
  TUẤN 07/11/2026: loại phiếu Mực định kỳ + Sửa tại chỗ mức Thường, hoàn tác 10 phút, không giới hạn số phiếu, chạy thật ngay.
- Nhắc hẹn AI (NHAC_HEN): nhắn Zalo cho thợ khi được giao quá accept_minutes phút chưa bấm Nhận, khi tới mốc nhắc
  sắp trễ hẹn; trễ hẹn quá director_late_minutes phút mà thợ chưa đi thì nhắn giám đốc. Điều phối đã có báo riêng.
- Thư ký phiếu (THU_KY): lúc times (mặc định 11:15, 17:15) gom việc còn treo của từng nhân viên (đúng nhóm ở
  Việc của tôi) nhắn Zalo riêng. Giám đốc không nhận (đã có báo cáo sáng).
Tin Zalo chỉ gửi trong giờ làm việc, ngày làm việc, cho người đã được gắn Zalo riêng. Mốc chống gửi lặp ở dispatch_zalo_sent.
"""
import logging
import secrets
from datetime import datetime, time, timedelta

from sqlalchemy import and_, exists, func, or_, select

from . import service_sla, service_suggest, service_tickets
from .config import get_settings
from .errors import BusinessError
from .models import Customer, DispatchZaloSent, ServiceStep, ServiceTicket, User, VirtualAgent, VirtualAgentLog, ZaloChat
from .service_sla import TZ

log = logging.getLogger(__name__)

SYSTEM_USERNAME = 'nhan-vien-ao'
SYSTEM_NAME = 'Nhân viên AI'
NOTE = 'Điều phối AI'
MASTER = 'TAT_CA'
KIND_LABELS = {'MUC_DINH_KY': 'Mực định kỳ', 'SUA_TAI_CHO': 'Sửa tại chỗ', 'MANG_VE': 'Mang về xưởng', 'GIAO_HANG': 'Giao hàng'}
DISPATCH_KINDS = ('MUC_DINH_KY', 'SUA_TAI_CHO', 'MANG_VE')   # bảo hành không bao giờ tự giao
PRIORITY_LABELS = {'THUONG': 'Thường', 'GAP': 'Gấp'}       # Khẩn không bao giờ tự giao
AGENTS = {
    'DIEU_PHOI': dict(name='Điều phối AI', what='Giao phiếu dễ khi hết giờ thợ tự nhận mà chưa thợ nào nhận',
                      defaults=dict(kinds=['MUC_DINH_KY', 'SUA_TAI_CHO'], priorities=['THUONG'], wait_minutes=30,
                                    undo_minutes=10, max_per_day=0, max_per_tech=0)),
    'NHAC_HEN': dict(name='Nhắc hẹn AI', what='Nhắn Zalo cho thợ khi chưa nhận việc hoặc sắp trễ hẹn; trễ lâu thì nhắn giám đốc',
                     defaults=dict(accept_minutes=15, director_late_minutes=30)),
    'THU_KY': dict(name='Thư ký phiếu', what='Gom việc còn treo của từng người, nhắn Zalo vào cuối buổi',
                   defaults=dict(times=['11:15', '17:15'])),
}
MODES = {'TU_LAM': 'Tự làm', 'CHAY_THU': 'Chạy thử'}
ACTIONS = {'GIAO': 'Đã giao', 'CHAY_THU': 'Chạy thử', 'BO_QUA': 'Bỏ qua', 'NHAC': 'Đã nhắc', 'BAO': 'Đã báo',
           'HOAN_TAC': 'Hoàn tác', 'CAI_DAT': 'Đổi cài đặt'}
# Thư ký phiếu: các nhóm Việc của tôi là "việc treo" cần làm nốt trong ngày.
CLERK_KEYS = ('service_unreported', 'orders_unreported', 'receipts_rejected', 'receipts_pending', 'service_paused',
              'service_return_ready', 'technician_deliveries', 'zalo_ticket_drafts', 'customer_messages_waiting', 'service_goods_short')
FRESH = timedelta(hours=4)       # nhắc chưa nhận: chỉ xét việc giao trong 4 giờ gần nhất, không dồn việc cũ khi mới bật
CATCH_UP = timedelta(hours=2)    # thư ký: app khởi động lại trễ quá 2 giờ sau mốc thì bỏ mốc đó
MAX_TEXT = 1800


def _now():
    return service_tickets._now()


# ---------- cấu hình ----------

def rows(session) -> dict[str, VirtualAgent]:
    out = {r.code: r for r in session.scalars(select(VirtualAgent))}
    missing = [c for c in (MASTER, *AGENTS) if c not in out]
    for code in missing:
        out[code] = VirtualAgent(code=code, enabled=True, mode='TU_LAM', config={})
        session.add(out[code])
    if missing:
        session.commit()
    return out


def cfg(row: VirtualAgent) -> dict:
    return {**AGENTS[row.code]['defaults'], **(row.config or {})}


def active(session, code: str):
    """Dòng cấu hình nếu công tắc tổng và công tắc riêng đều bật, không thì None."""
    all_rows = rows(session)
    row = all_rows[code]
    return row if all_rows[MASTER].enabled and row.enabled else None


def _director(user):
    if not user.active or 'director' not in user.roles:
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được cài đặt nhân viên AI.', status=403)


def _int(raw, lo, hi, label):
    raw = str(raw if raw is not None else '').strip()
    if not raw.isdecimal() or not lo <= int(raw) <= hi:
        raise BusinessError('VALIDATION', f'{label}: nhập số từ {lo} đến {hi}.')
    return int(raw)


def _times(raw) -> list[str]:
    out = []
    for part in str(raw or '').replace(';', ',').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(datetime.strptime(part, '%H:%M').strftime('%H:%M'))
        except ValueError as exc:
            raise BusinessError('VALIDATION', f'Giờ "{part}" chưa đúng, nhập dạng 11:15.') from exc
    if not out or len(out) > 4:
        raise BusinessError('VALIDATION', 'Nhập 1 đến 4 mốc giờ, cách nhau bằng dấu phẩy.')
    return sorted(set(out))


def save(session, user, code: str, form) -> VirtualAgent:
    """form: dict-like từ trang cài đặt. Chỉ giám đốc. Ghi nhật ký CAI_DAT."""
    _director(user)
    all_rows = rows(session)
    if code not in all_rows:
        raise BusinessError('NOT_FOUND', 'Không có nhân viên AI này.', status=404)
    row = all_rows[code]
    get = form.get
    enabled = str(get('bat') or '') == '1'
    old = (row.enabled, row.mode, dict(row.config or {}))
    if code == MASTER:
        row.enabled = enabled
        text = 'Bật tất cả nhân viên AI' if enabled else 'Tắt tất cả nhân viên AI'
    else:
        row.enabled = enabled
        config = dict(cfg(row))
        if get('cai_dat') == '1':
            if code == 'DIEU_PHOI':
                kinds = [k for k in form.getlist('kinds') if k in DISPATCH_KINDS]
                priorities = [p for p in form.getlist('priorities') if p in PRIORITY_LABELS]
                if not kinds or not priorities:
                    raise BusinessError('VALIDATION', 'Chọn ít nhất một loại phiếu và một mức ưu tiên.')
                mode = str(get('mode') or 'TU_LAM')
                row.mode = mode if mode in MODES else 'TU_LAM'
                config.update(kinds=kinds, priorities=priorities,
                              wait_minutes=_int(get('wait_minutes'), 5, 240, 'Giao sau khi thợ không tự nhận'),
                              undo_minutes=_int(get('undo_minutes'), 0, 120, 'Điều phối được hoàn tác trong'),
                              max_per_day=_int(get('max_per_day'), 0, 500, 'Tối đa phiếu mỗi ngày'),
                              max_per_tech=_int(get('max_per_tech'), 0, 100, 'Tối đa mỗi thợ mỗi ngày'))
            elif code == 'NHAC_HEN':
                config.update(accept_minutes=_int(get('accept_minutes'), 5, 240, 'Nhắc thợ chưa nhận sau'),
                              director_late_minutes=_int(get('director_late_minutes'), 5, 120, 'Báo giám đốc khi trễ hẹn quá'))
            elif code == 'THU_KY':
                config.update(times=_times(get('times')))
        row.config = config
        text = f"{AGENTS[code]['name']}: {'bật' if enabled else 'tắt'}" + (f" · {MODES[row.mode]}" if code == 'DIEU_PHOI' else '')
    if (row.enabled, row.mode, dict(row.config or {})) == old:
        return row
    row.updated_by, row.updated_at = user.id, _now()
    session.add(VirtualAgentLog(agent=code, action='CAI_DAT', text=text, target_id=user.id))
    session.commit()
    return row


# ---------- tài khoản hệ thống ----------

def system_user(session) -> User:
    """Tài khoản đứng tên việc của nhân viên AI. Không đăng nhập được (active=False, mật khẩu ngẫu nhiên không ai biết)."""
    user = session.scalar(select(User).where(User.username == SYSTEM_USERNAME))
    if user is None:
        from .security import hash_password
        user = User(username=SYSTEM_USERNAME, full_name=SYSTEM_NAME, password_hash=hash_password(secrets.token_urlsafe(32)),
                    roles=['dispatcher'], active=False)
        session.add(user)
        session.commit()
    return user


# ---------- Zalo ----------

def _zalo_ready() -> bool:
    return bool(get_settings().zalo_bot_token.strip())


def chats(session) -> dict[int, ZaloChat]:
    out = {}
    for user, chat in session.execute(select(User, ZaloChat).join(ZaloChat, ZaloChat.user_id == User.id)
                                      .where(User.active.is_(True), ZaloChat.chat_type == 'PRIVATE').order_by(User.id, ZaloChat.id)):
        out.setdefault(user.id, chat)
    return out


def _send(session, chat, user_id: int, mark: str, body: str, sender) -> bool:
    if session.get(DispatchZaloSent, (mark, user_id)) is not None:
        return False
    # Ghi mốc trước rồi mới gửi: gửi lỗi vẫn không gửi lại (tránh 2 tin), lỗi hiện ở Tin nhắn Zalo.
    row = DispatchZaloSent(mark=mark, user_id=user_id)
    session.add(row)
    session.commit()
    msg = sender(session, chat, body, None, 'nhan_vien_ao')
    row.message_id = getattr(msg, 'id', None)
    session.commit()
    return True


def _sender(sender):
    if sender is not None:
        return sender
    from . import zalo_bot
    return zalo_bot.send_text


def _base():
    from .dispatch_alerts import base_url
    return base_url()


def _working(session, now) -> bool:
    from .dispatch_alerts import working_now
    return working_now(session, now)


def _day_start(now):
    local = now.astimezone(TZ)
    return datetime.combine(local.date(), time(0), TZ)


# ---------- Điều phối AI ----------

def _candidates(session, c: dict, now):
    t = ServiceTicket
    kinds = list(c['kinds'])
    kind_ok = t.kind.in_(kinds)
    if 'SUA_TAI_CHO' in kinds:
        kind_ok = kind_ok | t.kind.is_(None)   # phiếu chưa chọn loại = thợ đến chỗ khách, tính như Sửa tại chỗ
    done = exists().where(VirtualAgentLog.ticket_id == t.id, VirtualAgentLog.agent == 'DIEU_PHOI',
                          VirtualAgentLog.action.in_(('GIAO', 'HOAN_TAC', 'CHAY_THU')))
    wait = timedelta(minutes=int(c['wait_minutes']))
    return list(session.scalars(select(t).where(
        t.status == 'MOI', t.assignee_id.is_(None), t.counter_intake.is_(False), t.warranty_of_id.is_(None),
        t.priority.in_(list(c['priorities'])), t.priority != 'KHAN', kind_ok, ~done,
        or_(and_(t.self_claim_until.is_not(None), t.self_claim_until <= now),
            and_(t.self_claim_until.is_(None), t.created_at <= now - wait))).order_by(t.id).limit(20)))


def _today_counts(session, now):
    q = select(VirtualAgentLog.target_id, func.count()).where(
        VirtualAgentLog.agent == 'DIEU_PHOI', VirtualAgentLog.action == 'GIAO', VirtualAgentLog.undone_at.is_(None),
        VirtualAgentLog.at >= _day_start(now)).group_by(VirtualAgentLog.target_id)
    return dict(session.execute(q).all())


def _pick(rows_, by_id, per_tech, max_per_tech):
    for r in rows_:
        x = by_id.get(r['id'])
        if x is None or r['blocked'] or x.on_leave or not x.clocked_in:
            continue
        if max_per_tech and per_tech.get(r['id'], 0) >= max_per_tech:
            continue
        return r
    return None


def _skip_once(session, t, text):
    if session.scalar(select(VirtualAgentLog.id).where(VirtualAgentLog.ticket_id == t.id, VirtualAgentLog.agent == 'DIEU_PHOI',
                                                       VirtualAgentLog.action == 'BO_QUA').limit(1)) is None:
        session.add(VirtualAgentLog(agent='DIEU_PHOI', action='BO_QUA', ticket_id=t.id, text=f'{t.number}: {text}'))
        session.commit()


def dispatch_once(session, now=None, sender=None) -> int:
    row = active(session, 'DIEU_PHOI')
    now = now or _now()
    if row is None or not _working(session, now):
        return 0
    c = cfg(row)
    tickets = _candidates(session, c, now)
    if not tickets:
        return 0
    techs = service_suggest.load_techs(session, now)
    by_id = {x.user.id: x for x in techs}
    ranks = service_suggest.rank_many(session, tickets, now, techs)
    per_tech = _today_counts(session, now)
    max_day, max_tech = int(c['max_per_day']), int(c['max_per_tech'])
    actor = system_user(session)
    names = dict(session.execute(select(Customer.code, Customer.name).where(Customer.code.in_({t.customer_code for t in tickets}))).all())
    count = 0
    for t in tickets:
        if max_day and sum(per_tech.values()) >= max_day:
            break
        pick = _pick(ranks.get(t.id, []), by_id, per_tech, max_tech)
        if pick is None:
            _skip_once(session, t, 'không có thợ đã vào ca để giao, để điều phối giao')
            continue
        why = ', '.join(pick['why']) or 'điểm gợi ý cao nhất'
        kind = KIND_LABELS.get(t.kind or 'SUA_TAI_CHO', t.kind)
        customer = names.get(t.customer_code, t.customer_code)
        if row.mode == 'CHAY_THU':
            session.add(VirtualAgentLog(agent='DIEU_PHOI', action='CHAY_THU', ticket_id=t.id, target_id=pick['id'],
                                        text=f"Sẽ giao {t.number} ({kind}, {customer}) cho {pick['name']}", reason=why))
            session.commit()
            continue
        try:
            with service_tickets._transaction(session):
                service_tickets.assign_in_tx(session, actor, t.id, pick['id'], t.version, f'{NOTE} · {why}')
                entry = VirtualAgentLog(agent='DIEU_PHOI', action='GIAO', ticket_id=t.id, target_id=pick['id'],
                                        text=f"Giao {t.number} ({kind}, {customer}) cho {pick['name']}", reason=why)
                session.add(entry)
        except BusinessError as exc:  # phiếu vừa được người khác giao / đổi: bỏ qua lượt này
            session.rollback()
            log.info('dieu phoi ao bo qua %s: %s', t.id, exc)
            continue
        per_tech[pick['id']] = per_tech.get(pick['id'], 0) + 1
        count += 1
        _tell_dispatchers(session, entry, t, kind, customer, pick, why, int(c['undo_minutes']), sender)
    return count


def _tell_dispatchers(session, entry, t, kind, customer, pick, why, undo, sender):
    from .dispatch_alerts import recipients
    if not _zalo_ready() or not get_settings().dispatch_zalo:
        return
    tail = f'Muốn đổi thợ hoặc hoàn tác, mở phiếu trong {undo} phút' if undo else 'Muốn đổi thợ, mở phiếu'
    body = f"Điều phối AI vừa giao {t.number} ({kind}, {customer}) cho {pick['name']}. Lý do: {why}.\n{tail}: {_base()}/dich-vu/{t.id}"
    send = _sender(sender)
    for user, chat in recipients(session):
        if user.id != pick['id']:
            _send(session, chat, user.id, f'nva-giao:{entry.id}', body, send)


def ticket_note(session, t: ServiceTicket, now=None) -> dict | None:
    """Khung "Điều phối AI đã giao" trên phiếu: lần giao gần nhất của nhân viên AI, còn hoàn tác được không."""
    entry = session.scalar(select(VirtualAgentLog).where(VirtualAgentLog.ticket_id == t.id, VirtualAgentLog.agent == 'DIEU_PHOI',
                                                         VirtualAgentLog.action == 'GIAO').order_by(VirtualAgentLog.id.desc()).limit(1))
    if entry is None:
        return None
    now = now or _now()
    undo = int(cfg(rows(session)['DIEU_PHOI'])['undo_minutes'])
    left = undo * 60 - int((now - entry.at).total_seconds())
    can_undo = (entry.undone_at is None and left > 0 and t.status == 'DA_GIAO' and t.assignee_id == entry.target_id)
    name = session.scalar(select(User.full_name).where(User.id == entry.target_id)) or ''
    return dict(id=entry.id, name=name, at=entry.at, reason=entry.reason, undone=entry.undone_at is not None,
                can_undo=can_undo, minutes_left=max(0, -(-left // 60)))


def undo(session, user, log_id: int) -> ServiceTicket:
    """Điều phối/giám đốc rút việc Điều phối AI vừa giao: phiếu về Cần giao thợ, nhân viên AI không giao lại phiếu này."""
    from .security import has_role
    if not has_role(user, *service_tickets.DISPATCHERS):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối hoặc giám đốc được hoàn tác.', status=403)
    entry = session.get(VirtualAgentLog, log_id)
    if entry is None or entry.action != 'GIAO' or entry.ticket_id is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy lần giao của Điều phối AI.', status=404)
    with service_tickets._transaction(session):
        t = service_tickets._get(session, user, entry.ticket_id, lock=True)
        note = ticket_note(session, t)
        if note is None or note['id'] != entry.id or not note['can_undo']:
            raise BusinessError('UNDO_CLOSED', 'Hết giờ hoàn tác hoặc thợ đã nhận việc. Dùng Giao thợ khác để đổi thợ.', status=409)
        before, from_status = service_tickets.snapshot(t), t.status
        tech = session.scalar(select(User.full_name).where(User.id == t.assignee_id)) or ''
        t.assignee_id, t.status, t.transfer_request, t.hold_reason = None, 'MOI', '', ''
        t.self_claim_until = None
        t.due_at = service_sla.due_at('MOI', t.priority, _now(), None)
        service_tickets._record(session, user, t, 'unassign', before, from_status, f'Hoàn tác Điều phối AI (đã giao {tech})')
        entry.undone_by, entry.undone_at = user.id, _now()
        session.add(VirtualAgentLog(agent='DIEU_PHOI', action='HOAN_TAC', ticket_id=t.id, target_id=user.id,
                                    text=f'{user.full_name} hoàn tác {t.number} (đã giao {tech})'))
    return t


# ---------- Nhắc hẹn AI ----------

def remind_once(session, now=None, sender=None) -> int:
    row = active(session, 'NHAC_HEN')
    now = now or _now()
    if row is None or not _zalo_ready() or not _working(session, now):
        return 0
    c = cfg(row)
    people = chats(session)
    if not people:
        return 0
    send = _sender(sender)
    base = _base()
    count = 0
    # 1) Được giao quá accept_minutes phút chưa bấm Nhận.
    t, s = ServiceTicket, ServiceStep
    last = select(s.ticket_id, func.max(s.id).label('sid')).where(s.action.in_(('assign', 'reassign'))).group_by(s.ticket_id).subquery()
    accept = timedelta(minutes=int(c['accept_minutes']))
    for ticket, step in session.execute(select(t, s).join(last, last.c.ticket_id == t.id).join(s, s.id == last.c.sid).where(
            t.status == 'DA_GIAO', t.assignee_id.is_not(None), s.assignee_id == t.assignee_id,
            s.at <= now - accept, s.at > now - FRESH).order_by(t.id)):
        chat = people.get(ticket.assignee_id)
        if chat is None:
            continue
        mins = int((now - step.at).total_seconds() // 60)
        body = f"Nhắc việc: phiếu {ticket.number} giao cho bạn {mins} phút rồi, bạn chưa bấm Nhận.\nMở app: {base}/tho/phieu/{ticket.id}"
        if _send(session, chat, ticket.assignee_id, f'nva-nhan:{ticket.id}:{step.id}', body, send):
            session.add(VirtualAgentLog(agent='NHAC_HEN', action='NHAC', ticket_id=ticket.id, target_id=ticket.assignee_id,
                                        text=f'Nhắc thợ: {ticket.number} giao {mins} phút chưa nhận'))
            session.commit()
            count += 1
    # 2) Sắp trễ hẹn: nhắn thợ đúng mốc app đã nhắc trong chuông.
    for item in service_suggest.at_risk(session, now, level='tech'):
        ticket = item['ticket']
        chat = people.get(ticket.assignee_id)
        if chat is None:
            continue
        label = service_suggest.risk_label(item)
        body = f"Nhắc hẹn: {ticket.number} {label.lower()}, bạn chưa bấm Đi. Nếu trễ, bấm Báo trễ trong app.\nMở app: {base}/tho/phieu/{ticket.id}"
        if _send(session, chat, ticket.assignee_id, f"nva-hen:{item['mark']}", body, send):
            session.add(VirtualAgentLog(agent='NHAC_HEN', action='NHAC', ticket_id=ticket.id, target_id=ticket.assignee_id,
                                        text=f'Nhắc thợ: {ticket.number} {label.lower()}'))
            session.commit()
            count += 1
    # 3) Trễ hẹn quá director_late_minutes phút mà thợ chưa đi: báo giám đốc.
    late = int(c['director_late_minutes'])
    bosses = [(uid, chat) for uid, chat in people.items() if 'director' in (session.get(User, uid).roles or [])]
    if bosses:
        names = None
        for item in service_suggest.at_risk(session, now, level='dispatch'):
            if item['minutes'] > -late:
                continue
            ticket = item['ticket']
            if names is None:
                names = dict(session.execute(select(User.id, User.full_name)).all())
            tech = names.get(ticket.assignee_id, 'thợ')
            body = (f"Báo giám đốc: {ticket.number} trễ hẹn {-item['minutes']} phút, {tech} chưa đi. Điều phối đã được báo trước đó."
                    f"\nMở app: {base}/dich-vu/{ticket.id}")
            for uid, chat in bosses:
                if _send(session, chat, uid, f"nva-gd:{item['mark']}", body, send):
                    session.add(VirtualAgentLog(agent='NHAC_HEN', action='BAO', ticket_id=ticket.id, target_id=uid,
                                                text=f'Báo giám đốc: {ticket.number} trễ {-item["minutes"]} phút, {tech} chưa đi'))
                    session.commit()
                    count += 1
    return count


# ---------- Thư ký phiếu ----------

def clerk_slot(session, now, times: list[str]):
    from .morning_report import is_workday
    local = now.astimezone(TZ)
    if not is_workday(session, local.date()):
        return None
    for raw in sorted(times, reverse=True):
        at = datetime.combine(local.date(), datetime.strptime(raw, '%H:%M').time(), TZ)
        if at <= local < at + CATCH_UP:
            return raw
    return None


def clerk_text(groups, at: str, base: str) -> str:
    head = f'Việc còn treo lúc {at} (Thư ký phiếu):'
    tail = f'Mở Việc của tôi: {base}/viec-cua-toi'
    body = []
    for g in groups:
        numbers = ', '.join(str(r.get('number')) for r in g['rows'][:3] if r.get('number'))
        more = f' +{g["count"] - 3}' if g['count'] > 3 and numbers else ''
        line = f"• {g['title']}: {g['count']}" + (f' ({numbers}{more})' if numbers else '')
        if len('\n'.join([head, *body, line, tail])) > MAX_TEXT:
            body.append('• … xem thêm trong app')
            break
        body.append(line)
    return '\n'.join([head, *body, tail])


def clerk_once(session, now=None, sender=None) -> int:
    from . import my_work
    row = active(session, 'THU_KY')
    now = now or _now()
    if row is None or not _zalo_ready():
        return 0
    at = clerk_slot(session, now, cfg(row)['times'])
    if at is None:
        return 0
    day = now.astimezone(TZ).date()
    mark = f"nva-thu-ky:{day.isoformat()}:{at.replace(':', '')}"
    send = _sender(sender)
    base = _base()
    count = 0
    for uid, chat in chats(session).items():
        if session.get(DispatchZaloSent, (mark, uid)) is not None:
            continue
        user = session.get(User, uid)
        if user is None or 'director' in user.roles:
            continue
        groups = [g for g in my_work.collect(session, user, keys=CLERK_KEYS) if g['count']]
        if not groups:
            session.add(DispatchZaloSent(mark=mark, user_id=uid))   # không có việc treo: không nhắn, không xét lại mốc này
            session.commit()
            continue
        if _send(session, chat, uid, mark, clerk_text(groups, at, base), send):
            total = sum(g['count'] for g in groups)
            session.add(VirtualAgentLog(agent='THU_KY', action='BAO', target_id=uid,
                                        text=f'Gửi {user.full_name} {total} việc treo lúc {at}', reason=', '.join(g['title'] for g in groups)[:500]))
            session.commit()
            count += 1
    return count


# ---------- vòng nền ----------

def run_once(session, now=None, sender=None) -> int:
    """Gọi mỗi phút trong vòng nền báo điều phối, trước khi báo phiếu cần giao (phiếu đã tự giao thì không báo nữa)."""
    now = now or _now()
    total = 0
    for job in (dispatch_once, remind_once, clerk_once):
        try:
            total += job(session, now, sender)
        except Exception:  # noqa: BLE001 - lỗi một nhân viên AI không chặn các việc khác
            session.rollback()
            log.exception('nhan vien ao %s', job.__name__)
    return total


# ---------- trang /nhan-vien-ao ----------

def today_stats(session, now=None) -> dict:
    now = now or _now()
    q = select(VirtualAgentLog.agent, VirtualAgentLog.action, func.count()).where(
        VirtualAgentLog.at >= _day_start(now)).group_by(VirtualAgentLog.agent, VirtualAgentLog.action)
    out = {}
    for agent, action, n in session.execute(q):
        out.setdefault(agent, {})[action] = n
    return out


def log_query(day, agent=''):
    start = datetime.combine(day, time(0), TZ)
    q = select(VirtualAgentLog).where(VirtualAgentLog.at >= start, VirtualAgentLog.at < start + timedelta(days=1))
    if agent:
        q = q.where(VirtualAgentLog.agent == agent)
    return q


def log_counts(session, day) -> dict:
    start = datetime.combine(day, time(0), TZ)
    return dict(session.execute(select(VirtualAgentLog.agent, func.count()).where(
        VirtualAgentLog.at >= start, VirtualAgentLog.at < start + timedelta(days=1)).group_by(VirtualAgentLog.agent)).all())


def trial_results(session, entries) -> dict:
    """Chạy thử: điều phối thật đã giao ai cho phiếu đó → {log_id: True khớp / False khác / None chưa giao}."""
    ids = {e.ticket_id for e in entries if e.action == 'CHAY_THU' and e.ticket_id}
    if not ids:
        return {}
    s = ServiceStep
    first = dict(session.execute(select(s.ticket_id, s.assignee_id).where(s.ticket_id.in_(ids), s.action == 'assign')
                                 .order_by(s.ticket_id, s.id.desc())).all())
    return {e.id: (None if first.get(e.ticket_id) is None else first[e.ticket_id] == e.target_id)
            for e in entries if e.action == 'CHAY_THU'}


def recent_assigned(session, since):
    """Cho chuông điều phối: các phiếu Điều phối AI đã giao từ `since`, chưa hoàn tác."""
    return list(session.execute(select(VirtualAgentLog.id, VirtualAgentLog.ticket_id, VirtualAgentLog.text).where(
        VirtualAgentLog.agent == 'DIEU_PHOI', VirtualAgentLog.action == 'GIAO', VirtualAgentLog.undone_at.is_(None),
        VirtualAgentLog.at >= since).order_by(VirtualAgentLog.id.desc()).limit(20)))

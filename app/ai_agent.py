"""Trợ lý AI (TUẤN 01/11/2026): cửa riêng cho Claude trên máy chủ Z2.

Ba mức, theo bản mẫu đã duyệt:
- Được xem: phiếu dịch vụ đang mở, việc của từng người, chấm công hôm nay. Không có tiền, giá vốn, lương.
- Tự làm: nhắc việc (tối đa 3 lần/người/ngày), ghi chú trễ hạn, báo cáo. Không đổi chứng từ nào.
- Chờ giám đốc duyệt: giao/đổi thợ, đổi giờ hẹn, đề xuất nhập hàng. Khi duyệt, việc chạy bằng
  tài khoản giám đốc qua đúng hàm nghiệp vụ sẵn có (kiểm quyền, phiên bản phiếu, nhật ký như bấm tay).
AI không có tài khoản người dùng, không đọc thẳng database, không có quyền ghi nghiệp vụ.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, time, timedelta

from sqlalchemy import func, select, update

from . import audit, my_work, service_sla, service_tickets
from .errors import BusinessError
from .models import AiAction, AiKey, AttendancePunch, AuditLog, Customer, Product, ServiceTicket, User
from .security import has_role

TZ = service_sla.TZ
PREFIX = 'tdai_'
REMIND_PER_DAY = 3
MAX_PENDING = 20
EXPIRE = {'GIAO_VIEC': timedelta(hours=2), 'DOI_HEN': timedelta(hours=2), 'DE_XUAT_NHAP': timedelta(hours=24)}
AUTO = ('NHAC_VIEC', 'GHI_CHU', 'BAO_CAO')
PROPOSALS = tuple(EXPIRE)
KIND_LABELS = {'NHAC_VIEC': 'Nhắc việc', 'GHI_CHU': 'Ghi chú', 'BAO_CAO': 'Báo cáo', 'GIAO_VIEC': 'Giao việc',
               'DOI_HEN': 'Đổi giờ hẹn', 'DE_XUAT_NHAP': 'Đề xuất nhập hàng'}
STATUS_LABELS = {'DA_GUI': 'Đã gửi', 'CHO_DUYET': 'Chờ duyệt', 'DA_DUYET': 'Đã duyệt', 'TU_CHOI': 'Từ chối',
                 'HET_HAN': 'Hết hạn', 'LOI': 'Không chạy được'}
# Chỉ các trường này của hàng việc (Việc của tôi) được đưa cho AI; bỏ số tiền.
ROW_FIELDS = ('id', 'number', 'status', 'customer', 'overdue', 'due_at', 'appointment_at', 'qty')
TICKET_STATUS = {'MOI': 'Mới', 'DA_GIAO': 'Đã giao', 'DA_NHAN': 'Đã nhận', 'DA_HEN': 'Đã hẹn', 'DANG_LAM': 'Đang làm',
                 'CHO_LINH_KIEN': 'Chờ linh kiện', 'TAM_DUNG': 'Tạm dừng'}


def _now():
    return datetime.now(TZ)


def _day_start(now=None):
    return datetime.combine((now or _now()).astimezone(TZ).date(), time.min, TZ)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _director(user):
    # Tài khoản chỉ có vai Quản trị không được cấp hay duyệt việc của AI.
    if not user.active or 'director' not in user.roles:
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được quản lý Trợ lý AI.', status=403)


# ---------- Khóa truy cập ----------

# Mỗi AI một khóa với phạm vi riêng. ready=False: chưa có lệnh nào cho phạm vi này, chưa cho tạo khóa.
AGENTS = {
    'dieu-phoi': dict(name='Claude · Điều phối', scopes=('dieu_phoi',), ready=True,
                      hint='Xem việc, nhắc việc, đề xuất giao thợ / dời hẹn / nhập hàng.'),
    'dang-web': dict(name='Codex · Đăng web', scopes=('dang_web',), ready=False,
                     hint='Soạn nháp, sửa ảnh, xếp danh mục trên web. Chỉ lưu nháp, không đụng giá.'),
}


def _agent(agent):
    if agent not in AGENTS or not AGENTS[agent]['ready']:
        raise BusinessError('BAD_AGENT', 'Trợ lý này chưa có.', status=404)
    return AGENTS[agent]


def active_key(session, agent='dieu-phoi'):
    return session.scalar(select(AiKey).where(AiKey.agent == agent, AiKey.revoked_at.is_(None)))


def active_keys(session) -> dict:
    return {k.agent: k for k in session.scalars(select(AiKey).where(AiKey.revoked_at.is_(None)))}


def _locked(session, agent):
    return session.scalar(select(AiKey).where(AiKey.agent == agent, AiKey.revoked_at.is_(None)).with_for_update())


def create_key(session, user, agent='dieu-phoi') -> str:
    """Tạo khóa mới cho một AI (khóa cũ của AI đó bị thu hồi). Trả chuỗi khóa đầy đủ, chỉ hiện một lần."""
    _director(user)
    spec = _agent(agent)
    now = _now()
    old = _locked(session, agent)
    paused = False
    if old is not None:
        old.revoked_at, old.revoked_by, paused = now, user.id, old.paused
        session.flush()
    raw = PREFIX + secrets.token_urlsafe(32)
    key = AiKey(agent=agent, scopes=list(spec['scopes']), prefix=raw[:len(PREFIX) + 4], key_hash=_hash(raw),
                paused=paused, created_by=user.id)
    session.add(key)
    session.flush()
    audit.record(session, user, 'ai.key_create', 'ai_key', str(key.id), None, dict(agent=agent, prefix=key.prefix, scopes=key.scopes))
    session.commit()
    return raw


def revoke_key(session, user, agent='dieu-phoi'):
    _director(user)
    key = _locked(session, agent)
    if key is None:
        raise BusinessError('NO_KEY', 'Trợ lý này chưa có khóa nào đang dùng.', status=409)
    key.revoked_at, key.revoked_by = _now(), user.id
    audit.record(session, user, 'ai.key_revoke', 'ai_key', str(key.id), dict(agent=agent, prefix=key.prefix), None)
    session.commit()


def set_paused(session, user, paused: bool, agent='dieu-phoi'):
    _director(user)
    key = _locked(session, agent)
    if key is None:
        raise BusinessError('NO_KEY', 'Trợ lý này chưa có khóa nào đang dùng.', status=409)
    before = key.paused
    key.paused = bool(paused)
    audit.record(session, user, 'ai.pause' if paused else 'ai.resume', 'ai_key', str(key.id),
                 dict(agent=agent, paused=before), dict(agent=agent, paused=key.paused))
    session.commit()


def authenticate(session, raw: str | None, headers, scope: str) -> AiKey:
    """Khóa chỉ dùng được từ trong máy chủ (yêu cầu đi qua Cloudflare bị chặn) và chỉ trong phạm vi của nó."""
    if headers.get('cf-connecting-ip') or headers.get('cf-ray'):
        raise BusinessError('AI_LOCAL_ONLY', 'Khóa Trợ lý AI chỉ dùng được từ trong máy chủ.', status=403)
    if not raw or not raw.startswith(PREFIX):
        raise BusinessError('UNAUTHENTICATED', 'Thiếu khóa Trợ lý AI.', status=401)
    key = session.scalar(select(AiKey).where(AiKey.key_hash == _hash(raw), AiKey.revoked_at.is_(None)))
    if key is None or not hmac.compare_digest(key.key_hash, _hash(raw)):
        raise BusinessError('UNAUTHENTICATED', 'Khóa Trợ lý AI không đúng hoặc đã bị thu hồi.', status=401)
    if scope not in (key.scopes or []):
        raise BusinessError('AI_SCOPE', 'Khóa này không được dùng cho việc này.', status=403)
    if key.paused:
        raise BusinessError('AI_PAUSED', 'Giám đốc đang tạm dừng Trợ lý AI này.', status=423)
    now = _now()
    if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
        session.execute(update(AiKey).where(AiKey.id == key.id).values(last_used_at=now))
        session.commit()
    return key


# ---------- Được xem ----------

def _iso(value):
    return value.astimezone(TZ).isoformat(timespec='minutes') if isinstance(value, datetime) else value


def _staff(session):
    rows = session.scalars(select(User).where(User.active.is_(True)).order_by(User.id))
    return [u for u in rows if set(u.roles) - {'admin'}]


def _clock(session, staff, now):
    start = _day_start(now)
    rows = session.execute(select(AttendancePunch.user_id, AttendancePunch.kind, AttendancePunch.at)
                           .where(AttendancePunch.at >= start, AttendancePunch.at < start + timedelta(days=1))
                           .order_by(AttendancePunch.at)).all()
    out = {u.id: dict(vao=None, ra=None) for u in staff}
    for uid, kind, at in rows:
        if uid in out:
            out[uid]['vao' if kind == 'IN' else 'ra'] = _iso(at)
    return out


def _work(session, user):
    groups = []
    for g in my_work.collect(session, user):
        if not g['count']:
            continue
        rows = [{k: _iso(r.get(k)) for k in ROW_FIELDS if k in r} for r in g['rows'][:10]]
        groups.append(dict(nhom=g['key'], ten=g['title'], so=g['count'], dong=rows))
    return groups


def snapshot(session) -> dict:
    """Ảnh chụp để AI điều phối. Chỉ SELECT; không có số tiền, giá vốn, lương."""
    now = _now()
    expire(session)
    staff = _staff(session)
    names = {u.id: u.full_name for u in session.scalars(select(User))}
    clock = _clock(session, staff, now)
    open_counts = service_tickets.open_counts(session)
    reminded = dict(session.execute(select(AiAction.target_user_id, func.count()).where(
        AiAction.kind == 'NHAC_VIEC', AiAction.created_at >= _day_start(now)).group_by(AiAction.target_user_id)).all())
    people = []
    for u in staff:
        people.append(dict(id=u.id, ten=u.full_name, vai=list(u.roles), cham_cong=clock[u.id],
                           phieu_dang_mo=open_counts.get(u.id, 0), da_nhac_hom_nay=reminded.get(u.id, 0),
                           viec=_work(session, u)))
    t = ServiceTicket
    overdue = t.due_at.is_not(None) & (t.due_at < now)
    tickets = []
    for row, customer in session.execute(select(t, Customer.name).join(Customer, Customer.code == t.customer_code)
                                         .where(t.status.in_(service_tickets.OPEN))
                                         .order_by(overdue.desc(), t.due_at.asc().nulls_last(), t.id).limit(200)):
        tickets.append(dict(so=row.number, trang_thai=row.status, trang_thai_ten=TICKET_STATUS.get(row.status, row.status),
                            uu_tien=row.priority, loai=row.kind, khach=customer, tho_id=row.assignee_id,
                            tho=names.get(row.assignee_id), hen=_iso(row.appointment_at), khung_hen=row.appointment_slot,
                            han=_iso(row.due_at), tre_han=bool(row.due_at and row.due_at < now),
                            tinh_trang=(row.symptom or '')[:200], chuyen_tho=row.transfer_request or ''))
    pending = session.scalar(select(func.count()).select_from(AiAction).where(AiAction.status == 'CHO_DUYET'))
    return dict(luc=_iso(now), nguoi=people, phieu_dich_vu=tickets, cho_giam_doc_duyet=pending,
                gioi_han=dict(nhac_moi_nguoi_moi_ngay=REMIND_PER_DAY, de_xuat_cho_duyet_toi_da=MAX_PENDING))


# ---------- Tự làm ----------

def _text(value, label, limit):
    value = str(value or '').strip()
    if not value:
        raise BusinessError('REQUIRED', f'Thiếu {label}.')
    if len(value) > limit:
        raise BusinessError('TOO_LONG', f'{label.capitalize()} dài quá {limit} ký tự.')
    return value


def _ticket(session, number):
    number = str(number or '').strip()
    t = session.scalar(select(ServiceTicket).where(ServiceTicket.number == number)) if number else None
    if t is None:
        raise BusinessError('NOT_FOUND', f'Không tìm thấy phiếu {number}.', status=404)
    return t


def _log(session, action, row):
    audit.record(session, None, 'ai.' + action, 'ai_action', str(row.id), None,
                 dict(kind=row.kind, status=row.status, title=row.title, target_user_id=row.target_user_id,
                      ticket_id=row.ticket_id, payload=row.payload))


def remind(session, key, user_id, text, ticket_number=None) -> AiAction:
    target = session.get(User, int(user_id)) if str(user_id or '').isdigit() else None
    if target is None or not target.active or not set(target.roles) - {'admin'}:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy nhân viên đang làm việc.', status=404)
    text = _text(text, 'nội dung nhắc', 300)
    ticket = _ticket(session, ticket_number) if ticket_number else None
    # Khóa theo người nhận để hai lần gọi cùng lúc không vượt giới hạn.
    session.execute(select(User.id).where(User.id == target.id).with_for_update())
    sent = session.scalar(select(func.count()).select_from(AiAction).where(
        AiAction.kind == 'NHAC_VIEC', AiAction.target_user_id == target.id, AiAction.created_at >= _day_start()))
    if sent >= REMIND_PER_DAY:
        session.rollback()
        raise BusinessError('AI_REMIND_LIMIT', f'Hôm nay đã nhắc {target.full_name} {REMIND_PER_DAY} lần.', status=429)
    row = AiAction(kind='NHAC_VIEC', status='DA_GUI', title=text, target_user_id=target.id,
                   ticket_id=ticket.id if ticket else None, payload=dict(ticket=ticket.number if ticket else None), key_id=key.id)
    session.add(row)
    session.flush()
    _log(session, 'remind', row)
    session.commit()
    return row


def note(session, key, kind, title, text='', ticket_number=None) -> AiAction:
    if kind not in ('GHI_CHU', 'BAO_CAO'):
        raise BusinessError('BAD_KIND', 'Chỉ ghi chú hoặc báo cáo.')
    title = _text(title, 'tiêu đề', 200)
    body = str(text or '').strip()
    if len(body) > 8000:
        raise BusinessError('TOO_LONG', 'Nội dung dài quá 8000 ký tự.')
    ticket = _ticket(session, ticket_number) if ticket_number else None
    row = AiAction(kind=kind, status='DA_GUI', title=title, reason=body, ticket_id=ticket.id if ticket else None,
                   payload=dict(ticket=ticket.number if ticket else None), key_id=key.id)
    session.add(row)
    session.flush()
    _log(session, 'note' if kind == 'GHI_CHU' else 'report', row)
    session.commit()
    return row


# ---------- Đề xuất chờ duyệt ----------

def _technician(session, user_id):
    try:
        return service_tickets._technician(session, int(user_id))
    except (TypeError, ValueError):
        raise BusinessError('NOT_FOUND', 'Không tìm thấy thợ.', status=404)


def propose(session, key, kind, reason, ticket_number=None, assignee_id=None, appointment_at=None,
            appointment_slot=None, product_code=None, qty=None) -> AiAction:
    if kind not in PROPOSALS:
        raise BusinessError('BAD_KIND', 'Loại đề xuất không hợp lệ.')
    reason = _text(reason, 'lý do', 500)
    expire(session)
    pending = session.scalar(select(func.count()).select_from(AiAction).where(AiAction.status == 'CHO_DUYET'))
    if pending >= MAX_PENDING:
        raise BusinessError('AI_TOO_MANY', f'Đang có {pending} đề xuất chờ giám đốc duyệt, chờ bớt rồi gửi tiếp.', status=429)
    names = {}
    ticket = None
    if kind in ('GIAO_VIEC', 'DOI_HEN'):
        ticket = _ticket(session, ticket_number)
        dup = session.scalar(select(AiAction.id).where(AiAction.status == 'CHO_DUYET', AiAction.kind == kind, AiAction.ticket_id == ticket.id))
        if dup:
            raise BusinessError('AI_DUPLICATE', f'Phiếu {ticket.number} đã có đề xuất này đang chờ duyệt.', status=409)
    if kind == 'GIAO_VIEC':
        if ticket.status not in service_tickets.ASSIGNABLE:
            raise BusinessError('BAD_STATUS', f'Phiếu {ticket.number} đã đóng, không giao được.', status=409)
        tech = _technician(session, assignee_id)
        if ticket.assignee_id == tech.id and ticket.status == 'DA_GIAO':
            raise BusinessError('SAME_ASSIGNEE', 'Phiếu đang giao cho thợ này rồi.', status=409)
        old = session.get(User, ticket.assignee_id) if ticket.assignee_id else None
        title = f'Đổi thợ {ticket.number}: {old.full_name} → {tech.full_name}' if old else f'Giao {ticket.number} cho {tech.full_name}'
        payload = dict(ticket=ticket.number, version=ticket.version, assignee_id=tech.id, assignee=tech.full_name,
                       old_assignee=old.full_name if old else None)
    elif kind == 'DOI_HEN':
        from . import service_appointments
        if ticket.status not in ('MOI', 'DA_GIAO', 'DA_NHAN', 'DA_HEN'):
            raise BusinessError('BAD_STATUS', 'Chỉ đổi hẹn trước khi thợ bắt đầu làm.', status=409)
        try:
            at = datetime.fromisoformat(str(appointment_at or ''))
        except ValueError:
            raise BusinessError('BAD_APPOINTMENT', 'Giờ hẹn mới chưa đúng dạng 2026-11-02T14:00.')
        if at.tzinfo is None:
            at = at.replace(tzinfo=TZ)
        slot = appointment_slot or 'GIO'
        service_appointments.validate(at, slot, _now())
        old = service_appointments.label(ticket.appointment_at, ticket.appointment_slot) if ticket.appointment_at else 'chưa hẹn'
        new = service_appointments.label(at, slot)
        if slot == 'GIO':
            new = at.astimezone(TZ).strftime('%H:%M %d/%m')
        title = f'Dời hẹn {ticket.number}: {old} → {new}'
        payload = dict(ticket=ticket.number, version=ticket.version, appointment_at=at.isoformat(), appointment_slot=slot,
                       old=old, new=new)
    else:
        product = session.get(Product, str(product_code or '').strip())
        if product is None:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy mã hàng.', status=404)
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0 or qty > 10000:
            raise BusinessError('BAD_QTY', 'Số lượng đề xuất phải từ 1 đến 10000.')
        title = f'Nhập thêm {qty} × {product.code} {product.name}'
        payload = dict(product_code=product.code, product=product.name, qty=qty)
    row = AiAction(kind=kind, status='CHO_DUYET', title=title, reason=reason, ticket_id=ticket.id if ticket else None,
                   payload=payload, key_id=key.id, expires_at=_now() + EXPIRE[kind])
    session.add(row)
    session.flush()
    _log(session, 'propose', row)
    session.commit()
    return row


def expire(session) -> int:
    rows = list(session.scalars(select(AiAction).where(AiAction.status == 'CHO_DUYET', AiAction.expires_at < _now()).with_for_update(skip_locked=True)))
    for row in rows:
        row.status, row.result = 'HET_HAN', 'Quá hạn chờ duyệt, không chạy.'
        audit.record(session, None, 'ai.expire', 'ai_action', str(row.id), dict(status='CHO_DUYET'), dict(status='HET_HAN'))
    if rows:
        session.commit()
    return len(rows)


def _claim(session, user, action_id):
    _director(user)
    expire(session)
    row = session.scalar(select(AiAction).where(AiAction.id == action_id).with_for_update())
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy đề xuất.', status=404)
    if row.status != 'CHO_DUYET':
        session.rollback()
        raise BusinessError('ALREADY_DECIDED', f'Đề xuất này đã {STATUS_LABELS[row.status].lower()}.', status=409)
    return row


def approve(session, user, action_id) -> AiAction:
    """Chạy việc bằng tài khoản giám đốc qua hàm nghiệp vụ sẵn có. Lỗi nghiệp vụ thì ghi 'Không chạy được'."""
    row = _claim(session, user, action_id)
    p, note_text = dict(row.payload), f'Duyệt đề xuất Trợ lý AI #{row.id}'
    row.status, row.decided_by, row.decided_at = 'DA_DUYET', user.id, _now()
    audit.record(session, user, 'ai_action.approve', 'ai_action', str(row.id), dict(status='CHO_DUYET'), dict(status='DA_DUYET'))
    try:
        if row.kind == 'GIAO_VIEC':
            # assign/reschedule tự commit cả dòng đề xuất; lỗi thì rollback cả hai.
            service_tickets.assign(session, user, row.ticket_id, p['assignee_id'], p.get('version'), note_text)
            row.result = f"Đã giao {p['ticket']} cho {p['assignee']}."
        elif row.kind == 'DOI_HEN':
            service_tickets.reschedule(session, user, row.ticket_id, p.get('version'), datetime.fromisoformat(p['appointment_at']),
                                       p.get('appointment_slot'), note_text)
            row.result = f"Đã dời hẹn {p['ticket']} sang {p['new']}."
        else:
            row.result = 'Đã báo thủ kho.'
        session.commit()
    except BusinessError as exc:
        session.rollback()
        row = session.scalar(select(AiAction).where(AiAction.id == action_id).with_for_update())
        if row.status == 'CHO_DUYET':
            row.status, row.decided_by, row.decided_at = 'LOI', user.id, _now()
            row.result = exc.message if exc.code != 'VERSION_CONFLICT' else 'Phiếu đã thay đổi sau khi AI đề xuất, không chạy.'
            audit.record(session, user, 'ai_action.failed', 'ai_action', str(row.id), dict(status='CHO_DUYET'),
                         dict(status='LOI', error=exc.code))
            session.commit()
    return row


def reject(session, user, action_id, reason='') -> AiAction:
    row = _claim(session, user, action_id)
    row.status, row.decided_by, row.decided_at = 'TU_CHOI', user.id, _now()
    row.result = str(reason or '').strip()[:300]
    audit.record(session, user, 'ai_action.reject', 'ai_action', str(row.id), dict(status='CHO_DUYET'), dict(status='TU_CHOI'), row.result)
    session.commit()
    return row


# ---------- Màn giám đốc ----------

TABS = {'cho-duyet': ('Chờ duyệt', ('CHO_DUYET',)), 'da-lam': ('AI đã làm', ('DA_GUI',)),
        'da-duyet': ('Đã duyệt', ('DA_DUYET',)), 'tu-choi': ('Từ chối · hết hạn', ('TU_CHOI', 'HET_HAN', 'LOI'))}


def counts(session) -> dict:
    expire(session)
    raw = dict(session.execute(select(AiAction.status, func.count()).group_by(AiAction.status)).all())
    out = {tab: sum(raw.get(s, 0) for s in statuses) for tab, (_, statuses) in TABS.items()}
    out['nhat-ky'] = session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.like('ai%')))
    return out


def listing(session, tab, offset, size):
    names = {u.id: u.full_name for u in session.scalars(select(User))}
    numbers = {}
    if tab == 'nhat-ky':
        rows = []
        for a in session.scalars(select(AuditLog).where(AuditLog.action.like('ai%')).order_by(AuditLog.id.desc()).offset(offset).limit(size)):
            rows.append(dict(at=a.at, who=names.get(a.user_id, 'Trợ lý AI') if a.user_id else 'Trợ lý AI', action=a.action,
                             text=(a.after or {}).get('title') or LOG_LABELS.get(a.action, a.action), reason=a.reason, entity_id=a.entity_id))
        return rows
    statuses = TABS[tab][1]
    rows = list(session.scalars(select(AiAction).where(AiAction.status.in_(statuses)).order_by(AiAction.id.desc()).offset(offset).limit(size)))
    for r in rows:
        if r.ticket_id:
            numbers[r.ticket_id] = None
    if numbers:
        numbers = dict(session.execute(select(ServiceTicket.id, ServiceTicket.number).where(ServiceTicket.id.in_(list(numbers)))).all())
    return [dict(row=r, kind=KIND_LABELS[r.kind], status=STATUS_LABELS[r.status], target=names.get(r.target_user_id),
                 decided_by=names.get(r.decided_by), ticket=numbers.get(r.ticket_id)) for r in rows]


LOG_LABELS = {'ai.key_create': 'Tạo khóa mới', 'ai.key_revoke': 'Khóa truy cập', 'ai.pause': 'Tạm dừng trợ lý',
              'ai.resume': 'Cho trợ lý chạy tiếp', 'ai_action.approve': 'Duyệt đề xuất', 'ai_action.reject': 'Từ chối đề xuất',
              'ai_action.failed': 'Duyệt nhưng không chạy được', 'ai.expire': 'Đề xuất hết hạn'}


def pending_brief(session, limit=10):
    expire(session)
    rows = list(session.scalars(select(AiAction).where(AiAction.status == 'CHO_DUYET').order_by(AiAction.id).limit(limit)))
    total = session.scalar(select(func.count()).select_from(AiAction).where(AiAction.status == 'CHO_DUYET'))
    return total, [dict(id=r.id, kind=KIND_LABELS[r.kind], title=r.title, reason=r.reason) for r in rows]


def reminders_for(session, user, since):
    """Nhắc việc AI gửi tới người này (cho chuông), và đề xuất nhập hàng đã duyệt (cho thủ kho)."""
    rows = list(session.scalars(select(AiAction).where(AiAction.kind == 'NHAC_VIEC', AiAction.target_user_id == user.id,
                                                       AiAction.created_at >= since).order_by(AiAction.id.desc()).limit(my_work.LIMIT)))
    if 'warehouse' in user.roles:
        rows += list(session.scalars(select(AiAction).where(AiAction.kind == 'DE_XUAT_NHAP', AiAction.status == 'DA_DUYET',
                                                            AiAction.decided_at >= since).order_by(AiAction.id.desc()).limit(my_work.LIMIT)))
    return rows

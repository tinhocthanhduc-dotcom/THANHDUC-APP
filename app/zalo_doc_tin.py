"""Trưởng nhóm AI đọc tin Zalo (Đội AI, TUẤN duyệt mẫu MBakJ8u4e77Fszx9hrtz7w ngày 15/11/2026).

Tin khách nhắn vào bot (chat riêng đã gắn hoặc chưa gắn khách, cả nhóm có bot - Q5) được đưa cho Claude/Codex
trên máy Z2 đọc (Q7: chỉ Z2, không API; Z2 tắt thì tin chờ). Z2 trả về loại tin:
- SUA / MUC: có khách → phiếu nháp (zalo_ticket_drafts) cho điều phối, kèm thợ gợi ý; điều phối bấm một lần
  "Tạo phiếu và giao" (TUẤN chọn: soạn nháp, người bấm 1 lần). Chưa rõ khách → việc "gắn khách" cho điều phối.
- HOI_GIA → bán hàng ít việc nhất; CONG_NO → kế toán. Hiện ở Việc của tôi nhóm "Tin Zalo cần trả lời".
- KHAC → bỏ qua.
Hạn 30 phút trong giờ làm; tin ngoài giờ tính từ đầu giờ làm hôm sau (Q6).
AI không trả lời khách, không đụng tiền, kho, công nợ. Tin trợ lý Zalo đã tạo nháp thì không tạo nháp thứ hai.
"""
import json
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select

from .config import get_settings
from .errors import BusinessError
from .models import (AttendanceHoliday, Customer, User, VirtualAgentLog, ZaloAiJob, ZaloChat, ZaloMessage, ZaloTicketDraft,
                     ZaloTriage, ZaloTroLy)
from .service_sla import TZ
from .text import fold, phone as norm_phone

log = logging.getLogger(__name__)

INTENTS = {'SUA': 'Sửa máy', 'MUC': 'Cần mực', 'HOI_GIA': 'Hỏi giá', 'CONG_NO': 'Công nợ, thanh toán', 'KHAC': 'Khác'}
KIND_OF = {'SUA': 'SUA_TAI_CHO', 'MUC': 'MUC_DINH_KY'}
DUE = timedelta(minutes=30)
LOOK_BACK = timedelta(hours=24)       # chỉ đọc tin trong 24 giờ gần nhất (bật lần đầu không dồn tin cũ)
PER_RUN = 5
SMALL_TALK = {'ok', 'oke', 'okie', 'okay', 'vang', 'da', 'dạ', 'cam on', 'thanks', 'thank you', 'tks', 'ty', 'u', 'uh', 'um', 'vâng', 'được',
              'duoc', 'roi', 'rồi', 'ok em', 'ok anh', 'ok chi', 'da vang', 'da em', 'cam on em', 'cam on anh', 'cam on chi', 'hi', 'alo'}
PHONE = re.compile(r'(?:\+?84|0)[\s.-]?\d(?:[\s.-]?\d){8,9}')

PROMPT = """Bạn là trưởng nhóm điều phối của Công ty Tin Học Thành Đức (bán và sửa máy in, nạp/giao mực, TP.HCM).
Đọc một tin khách nhắn vào Zalo công ty và CHỈ trả về một khối JSON, không viết gì khác:
{"loai": "SUA|MUC|HOI_GIA|CONG_NO|KHAC", "tom_tat": "một câu ngắn", "mo_ta": "lỗi/yêu cầu để ghi phiếu",
 "muc_do": "THUONG|GAP|KHAN", "ngay": "YYYY-MM-DD hoặc rỗng", "buoi": "SANG|CHIEU hoặc rỗng",
 "ten_khach": "tên công ty/khách nếu tin có nói", "so_dien_thoai": "số trong tin nếu có", "ma_muc": "mã hộp mực nếu có"}
- SUA: báo máy hỏng, kẹt giấy, in mờ, cần thợ tới sửa hoặc mang máy đi sửa.
- MUC: cần giao/nạp mực, hết mực, đặt hộp mực.
- HOI_GIA: hỏi giá, xin báo giá máy, mực, linh kiện, dịch vụ.
- CONG_NO: hỏi số nợ, xin hóa đơn, báo đã chuyển khoản, hỏi số tài khoản.
- KHAC: chào hỏi, cảm ơn, tin không cần ai xử lý.
GAP khi khách cần trong ngày hoặc nói gấp; KHAN khi cả văn phòng không in được. "mai" là ngày sau hôm nay.
Hôm nay: <<TODAY>>. Nhóm/chat: <<CHAT>>. Người nhắn: <<SENDER>>. Khách đã gắn với chat: <<LINKED>>.
Các tin trước trong chat (cũ → mới): <<BEFORE>>
Tin cần đọc: <<TEXT>>"""


def _now():
    from . import service_tickets
    return service_tickets._now()


# ---------- hạn (Q6) ----------

def work_start_after(session, at):
    """Thời điểm sớm nhất ≥ at nằm trong giờ làm việc."""
    s = get_settings()
    local = at.astimezone(TZ)
    for _ in range(15):
        day = local.date()
        workday = day.weekday() in s.work_days and session.get(AttendanceHoliday, day) is None
        start, end = datetime.combine(day, s.work_start, TZ), datetime.combine(day, s.work_end, TZ)
        if workday and local < end:
            return max(local, start)
        local = datetime.combine(day + timedelta(days=1), s.work_start, TZ)
    return local


def due_after(session, at):
    return work_start_after(session, at) + DUE


# ---------- đưa tin cho Z2 ----------

def _engine(session) -> str:
    row = session.scalar(select(ZaloTroLy).limit(1))
    engine = (row.staff_engine if row else '') or ''
    return engine if engine in ('codex', 'claude') else 'claude'


def _small_talk(text: str) -> bool:
    t = re.sub(r'[^\w\s]', ' ', fold(text)).strip()
    return len(t) < 4 or t in {fold(x) for x in SMALL_TALK}


def candidates(session, now):
    """Tin khách mới chưa đọc: tin đến (IN), dạng chữ, không phải nhân viên, không phải bot."""
    m, c = ZaloMessage, ZaloChat
    staff_chat = c.user_id.is_not(None)
    q = (select(m, c).join(c, c.id == m.chat_pk)
         .where(m.direction == 'IN', m.kind == 'text', m.created_at >= now - LOOK_BACK, ~staff_chat,
                ~select(ZaloTriage.id).where(ZaloTriage.message_id == m.id).exists())
         .order_by(m.id).limit(50))
    return list(session.execute(q))


def _before(session, msg) -> str:
    rows = list(session.scalars(select(ZaloMessage).where(ZaloMessage.chat_pk == msg.chat_pk, ZaloMessage.id < msg.id,
                                                          ZaloMessage.kind == 'text').order_by(ZaloMessage.id.desc()).limit(3)))
    return json.dumps([f"{'Khách' if r.direction == 'IN' else 'Công ty'}: {r.text[:300]}" for r in reversed(rows)], ensure_ascii=False)


def prompt_for(session, msg, chat, now) -> str:
    linked = session.get(Customer, chat.customer_code) if chat.customer_code else None
    return (PROMPT.replace('<<TODAY>>', now.astimezone(TZ).strftime('%Y-%m-%d (%A)'))
            .replace('<<CHAT>>', f"{'nhóm' if chat.chat_type == 'GROUP' else 'chat riêng'} \"{chat.display_name}\"")
            .replace('<<SENDER>>', msg.sender_name or chat.display_name or 'không rõ')
            .replace('<<LINKED>>', f'{linked.name} ({linked.code})' if linked else 'chưa gắn')
            .replace('<<BEFORE>>', _before(session, msg)).replace('<<TEXT>>', msg.text[:2000]))


def enqueue(session, now=None) -> int:
    now = now or _now()
    count = 0
    for msg, chat in candidates(session, now):
        if isinstance(msg.raw, dict) and ((msg.raw.get('message') or {}).get('from') or {}).get('is_bot'):
            session.add(ZaloTriage(message_id=msg.id, status='BO_QUA', note='Tin của bot'))
        elif session.scalar(select(ZaloTicketDraft.id).where(ZaloTicketDraft.message_id == msg.id)) is not None:
            session.add(ZaloTriage(message_id=msg.id, status='BO_QUA', note='Trợ lý Zalo đã tạo phiếu nháp'))
        elif _small_talk(msg.text):
            session.add(ZaloTriage(message_id=msg.id, status='BO_QUA', intent='KHAC', note='Tin xã giao'))
        elif count < PER_RUN:
            job = ZaloAiJob(engine=_engine(session), prompt=prompt_for(session, msg, chat, now))
            session.add(job)
            session.flush()
            session.add(ZaloTriage(message_id=msg.id, job_id=job.id))
            count += 1
        else:
            break
        session.commit()
    return count


# ---------- nhận kết quả Z2 ----------

def parse(text: str) -> dict:
    raw = (text or '').strip()
    try:
        obj = json.loads(raw[raw.find('{'):raw.rfind('}') + 1]) if '{' in raw else None
    except ValueError:
        obj = None
    if not isinstance(obj, dict) or obj.get('loai') not in INTENTS:
        raise ValueError('Z2 không trả đúng khuôn JSON')
    return {k: str(v or '').strip() for k, v in obj.items()}


def find_customer(session, chat, data, msg_text):
    """(mã khách, chắc chắn?). Ưu tiên khách đã gắn chat; rồi số điện thoại; rồi tên chỉ khi khớp đúng một khách."""
    if chat.customer_code:
        return chat.customer_code, True
    active = Customer.status == 'DANG_DUNG'
    numbers = [data.get('so_dien_thoai', '')] + PHONE.findall(msg_text or '')
    for raw in numbers:
        try:
            value = norm_phone(raw) if raw else ''
        except BusinessError:
            continue
        if value:
            hits = list(session.scalars(select(Customer.code).where(active, Customer.search_text.contains(value)).limit(2)))
            if len(hits) == 1:
                return hits[0], False
    for name in (data.get('ten_khach', ''), chat.display_name if chat.chat_type == 'GROUP' else ''):
        key = fold(name)
        if len(key) >= 4:
            hits = list(session.scalars(select(Customer.code).where(active, Customer.search_text.contains(key)).limit(2)))
            if len(hits) == 1:
                return hits[0], False
    return None, False


def _pick(session, role, intent_load):
    users = [u for u in session.scalars(select(User).where(User.active.is_(True)).order_by(User.id)) if role in (u.roles or [])]
    staff = [u for u in users if 'director' not in u.roles] or users
    return min(staff, key=lambda u: (intent_load.get(u.id, 0), u.id)) if staff else None


def _open_load(session):
    return dict(session.execute(select(ZaloTriage.owner_id, func.count()).where(ZaloTriage.done_at.is_(None), ZaloTriage.owner_id.is_not(None))
                                .group_by(ZaloTriage.owner_id)).all())


def apply(session, row: ZaloTriage, data: dict, now) -> None:
    from . import zalo_phieu_nhap
    msg = session.get(ZaloMessage, row.message_id)
    chat = session.get(ZaloChat, msg.chat_pk)
    intent = data['loai']
    row.intent, row.summary = intent, (data.get('tom_tat') or msg.text)[:500]
    row.status = 'XONG'
    if intent == 'KHAC':
        row.status, row.done_at = 'BO_QUA', now
        return
    code, sure = find_customer(session, chat, data, msg.text)
    row.customer_code, row.customer_sure = code, sure
    row.due_at = due_after(session, msg.created_at or now)
    load = _open_load(session)
    if intent in KIND_OF and code:
        try:
            draft = zalo_phieu_nhap.create(
                session, message_id=msg.id, log_id=None, source='KHACH', sender=None, customer_code=code, kind=KIND_OF[intent],
                symptom=data.get('mo_ta') or msg.text, want_date=data.get('ngay'), slot=data.get('buoi'),
                priority=data.get('muc_do') or 'THUONG', ink_hint=data.get('ma_muc', ''), question=msg.text)
        except BusinessError as exc:
            row.note = exc.message[:300]
        else:
            draft.customer_sure, draft.from_triage = sure, True
            row.draft_id, row.done_at = draft.id, now   # việc nằm ở Phiếu nháp từ Zalo của điều phối
            return
    role = {'HOI_GIA': 'sales', 'CONG_NO': 'finance'}.get(intent, 'dispatcher')
    owner = _pick(session, role, load)
    row.owner_id = owner.id if owner else None
    if intent in KIND_OF:
        row.note = row.note or 'Chưa rõ khách: gắn khách rồi tạo phiếu'


def collect(session, now=None) -> int:
    """Nhận kết quả các lượt Z2 đã xong và giao việc."""
    now = now or _now()
    count = 0
    rows = list(session.execute(select(ZaloTriage, ZaloAiJob).join(ZaloAiJob, ZaloAiJob.id == ZaloTriage.job_id).where(
        ZaloTriage.status == 'CHO_AI', ZaloAiJob.status.in_(('XONG', 'LOI'))).order_by(ZaloTriage.id).limit(20)))
    for row, job in rows:
        try:
            if job.status == 'LOI':
                raise ValueError(job.error or 'Z2 không đọc được')
            apply(session, row, parse(job.answer), now)
            if row.status == 'XONG':
                session.commit()
                from . import zalo_tu_tra_loi
                try:  # Trả lời tự động (19/11/2026): hỏi giá → AI soạn chờ duyệt; báo hỏng → báo đã nhận. Lỗi không chặn chia việc.
                    zalo_tu_tra_loi.on_triage(session, row)
                except Exception:  # noqa: BLE001
                    session.rollback()
                    log.exception('Trả lời tự động lỗi ở tin %s', row.message_id)
                row = session.get(ZaloTriage, row.id)
                session.add(VirtualAgentLog(agent='TRUONG_NHOM', action='DOC_TIN', target_id=row.owner_id,
                                            text=f'Đọc tin Zalo: {INTENTS[row.intent].lower()} · {row.summary[:160]}',
                                            reason=_reason(session, row)))
            count += 1
        except Exception as exc:  # noqa: BLE001 - một tin lỗi không chặn tin khác
            session.rollback()
            row = session.get(ZaloTriage, row.id)
            row.status, row.note = 'LOI', f'{exc}'[:300]
            msg = session.get(ZaloMessage, row.message_id)
            owner = _pick(session, 'dispatcher', _open_load(session))
            row.owner_id, row.due_at = (owner.id if owner else None), due_after(session, msg.created_at or now)
        session.commit()
    return count


def _reason(session, row) -> str:
    if row.draft_id:
        draft = session.get(ZaloTicketDraft, row.draft_id)
        return f'Tạo phiếu nháp {draft.number} cho điều phối'
    name = session.scalar(select(User.full_name).where(User.id == row.owner_id)) if row.owner_id else ''
    return f'Giao {name}' + (f' · {row.note}' if row.note else '')


def run_once(session, now=None) -> int:
    """Gọi mỗi phút từ Trưởng nhóm AI. Không cần giờ làm việc: tin ngoài giờ vẫn đọc, hạn tính từ giờ làm hôm sau."""
    now = now or _now()
    return collect(session, now) + enqueue(session, now)


# ---------- Việc của tôi: Tin Zalo cần trả lời ----------

def label(row: ZaloTriage) -> str:
    if row.status == 'LOI':
        return 'AI không đọc được, xem tin'
    if row.intent in KIND_OF and not row.customer_code:
        return 'Chưa rõ khách: gắn khách rồi tạo phiếu'
    return INTENTS.get(row.intent, '')


def mark_done(session, user, triage_id: int) -> ZaloTriage:
    from .security import has_role
    row = session.get(ZaloTriage, triage_id, with_for_update=True)
    if row is None:
        raise BusinessError('NOT_FOUND', 'Không có tin này.', status=404)
    if row.owner_id != user.id and not has_role(user, 'director', 'dispatcher'):
        raise BusinessError('FORBIDDEN', 'Tin này giao người khác.', status=403)
    if row.done_at is None:
        row.done_at, row.done_by = _now(), user.id
    session.commit()
    return row


def mine(session, user, include_all=False) -> list[dict]:
    q = (select(ZaloTriage, ZaloMessage, ZaloChat).join(ZaloMessage, ZaloMessage.id == ZaloTriage.message_id)
         .join(ZaloChat, ZaloChat.id == ZaloMessage.chat_pk)
         .where(ZaloTriage.done_at.is_(None), ZaloTriage.status.in_(('XONG', 'LOI'))).order_by(ZaloTriage.due_at, ZaloTriage.id))
    if not include_all:
        q = q.where(ZaloTriage.owner_id == user.id)
    out = []
    names = None
    for row, msg, chat in session.execute(q.limit(100)):
        if names is None:
            names = dict(session.execute(select(User.id, User.full_name)).all())
        customer = session.get(Customer, row.customer_code) if row.customer_code else None
        out.append(dict(row=row, msg=msg, chat=chat, customer=customer, label=label(row), owner=names.get(row.owner_id, '')))
    return out

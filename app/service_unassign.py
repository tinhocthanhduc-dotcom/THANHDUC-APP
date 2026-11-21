"""Hủy giao thợ trên phiếu dịch vụ (TUẤN duyệt mẫu 21/11/2026).

Điều phối/giám đốc rút phiếu khỏi thợ khi thợ chưa bắt đầu làm:
- phiếu thường về Mới · chưa giao thợ;
- phiếu mang về đang ở bước trả máy về Sửa xong, chờ trả máy (máy vẫn ở Kho kỹ thuật, bàn giao máy chờ thợ nhận bị hủy).
Chặn khi thợ đã bắt đầu, kho thợ còn vật tư của phiếu, còn yêu cầu vật tư chờ cấp cho thợ, hoặc thợ đang giữ máy khách.
Không đụng tiền, kho, công nợ. Điều phối AI không tự giao lại phiếu đã hủy giao (xem nhan_vien_ao._candidates).
"""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from . import service_sla
from . import service_tickets as tickets
from .errors import BusinessError
from .models import Equipment, EquipmentCustody, PartRequest, ServiceStep, ServiceTicket, User, Warehouse

ACTION = 'unassign'
ALLOWED = ('DA_GIAO', 'DA_NHAN', 'DA_HEN')
REASONS = {'GIAO_NHAM': 'Giao nhầm thợ', 'KHACH_DOI': 'Khách dời, chưa có lịch', 'THO_BAN': 'Thợ nghỉ / bận',
           'CHO_KHACH': 'Chờ khách xác nhận', 'KHAC': 'Khác'}
READY = 'SUA_XONG_CHO_TRA'


def _qty(value) -> str:
    value = Decimal(value)
    return str(int(value)) if value == value.to_integral_value() else format(value.normalize(), 'f')


def returning(t) -> bool:
    """Phiếu mang về đã sửa xong (hoặc khách không sửa) và đang ở bước trả máy."""
    from .service_charges import return_locked
    return return_locked(t)


def blocked(session, t) -> str:
    """Lý do không hủy giao được ('' = hủy được). Dùng cho cả màn hình và lõi."""
    if t.assignee_id is None or t.status == 'MOI':
        return 'Phiếu chưa giao thợ.'
    name = session.scalar(select(User.full_name).where(User.id == t.assignee_id)) or 'Thợ'
    if t.status == 'DANG_LAM':
        return f'{name} đã bắt đầu làm phiếu này (Đang làm). Không hủy giao được, dùng Giao thợ khác.'
    if t.status in ('CHO_LINH_KIEN', 'TAM_DUNG'):
        state = 'chờ linh kiện' if t.status == 'CHO_LINH_KIEN' else 'tạm dừng'
        return f'Phiếu đang {state} sau khi thợ đã làm. Không hủy giao được, dùng Giao thợ khác.'
    if t.status not in ALLOWED:
        return 'Phiếu đã xong hoặc đã đóng, không hủy giao được.'
    from .technician_reassign import ticket_need
    from .technician_stock import own_warehouse
    old = own_warehouse(session, session.get(User, t.assignee_id))
    if old is not None:
        need = ticket_need(session, t, old.code)
        if need:
            items = ', '.join(f'{code} ×{_qty(qty)}' for code, qty in sorted(need.items()))
            return (f'Kho thợ {name} còn vật tư cấp cho phiếu: {items}. Thợ trả vật tư về kho trước, '
                    'hoặc dùng Giao thợ khác (vật tư đi theo thợ mới).')
    waiting = session.scalar(select(PartRequest.id).join(Warehouse, Warehouse.code == PartRequest.to_warehouse_code)
                             .where(PartRequest.ticket_id == t.id, PartRequest.status == 'CHO_CAP', Warehouse.kind == 'THO').limit(1))
    if waiting:
        return f'Phiếu còn yêu cầu vật tư đang chờ kho cấp cho {name}. Hủy yêu cầu đó trước, hoặc dùng Giao thợ khác.'
    held = session.scalar(select(Equipment.serial).where(Equipment.custody_ticket_id == t.id, Equipment.custody_location == 'THO_GIU',
                                                         Equipment.custody_holder_id == t.assignee_id).limit(1))
    if held:
        return f'Máy khách {held} đang ở chỗ {name}. Thợ trả máy về Kho kỹ thuật trước.'
    return ''


def unassign(session, user, ticket_id: int, version: int | None, reason: str, note: str = ''):
    """Trả (phiếu, id thợ cũ, câu lý do). Caller không cần mở transaction."""
    tickets._require(user, tickets.DISPATCHERS, 'Chỉ điều phối hoặc giám đốc được hủy giao thợ.')
    note = (note or '').strip()
    if reason not in REASONS:
        raise BusinessError('REASON_REQUIRED', 'Hãy chọn lý do hủy giao thợ.')
    if reason == 'KHAC' and len(note) < 3:
        raise BusinessError('NOTE_REQUIRED', 'Chọn Khác thì ghi rõ lý do.')
    with tickets._transaction(session):
        t = tickets._get(session, user, ticket_id, lock=True)
        tickets._check_version(t, version)
        why = blocked(session, t)
        if why:
            raise BusinessError('CANNOT_UNASSIGN', why, status=409)
        old_id = t.assignee_id
        old_name = session.scalar(select(User.full_name).where(User.id == old_id)) or ''
        before, from_status = tickets.snapshot(t), t.status
        # Bàn giao máy đang chờ thợ cũ nhận (lúc hẹn trả máy) thì hủy: máy vẫn ở Kho kỹ thuật.
        for row in session.scalars(select(EquipmentCustody).where(EquipmentCustody.ticket_id == t.id, EquipmentCustody.status == 'CHO_NHAN',
                                                                  EquipmentCustody.location == 'THO_GIU', EquipmentCustody.holder_id == old_id).with_for_update()):
            from . import equipment_custody
            from .schemas import CustodyOut
            prev = CustodyOut.model_validate(row).model_dump(mode='json')
            row.status, row.confirmed_by, row.confirmed_at = 'DA_HUY', user.id, tickets._now()
            equipment_custody._record(session, user, row, 'cancel', prev)
        label = REASONS[reason]
        text = f'Hủy giao thợ: {old_name} · {label}' + (f'. {note}' if note else '')
        t.transfer_request, t.self_claim_until = '', None
        if returning(t):
            # Ràng buộc phiếu: Tạm dừng phải có người phụ trách, nên giữ thợ cũ trên phiếu; điều phối hẹn trả lại sẽ chọn thợ.
            t.status, t.hold_reason = 'TAM_DUNG', READY
            t.due_at = service_sla.due_at('TAM_DUNG', t.priority, tickets._now(), t.appointment_at, t.appointment_slot)
        else:
            t.assignee_id, t.status, t.hold_reason = None, 'MOI', ''
            t.due_at = service_sla.due_at('MOI', t.priority, tickets._now(), None)
        tickets._record(session, user, t, ACTION, before, from_status, text, label)
    return t, old_id, label


def previous_assignee(session, step) -> int | None:
    """Thợ giữ phiếu ngay trước bước hủy giao."""
    return session.scalar(select(ServiceStep.assignee_id).where(ServiceStep.ticket_id == step.ticket_id, ServiceStep.id < step.id,
                                                                ServiceStep.assignee_id.is_not(None)).order_by(ServiceStep.id.desc()).limit(1))


def recent_for(session, user_id: int, since):
    """Phiếu vừa bị rút khỏi thợ này (cho chuông màn thợ)."""
    out = []
    for step, number in session.execute(select(ServiceStep, ServiceTicket.number).join(ServiceTicket, ServiceTicket.id == ServiceStep.ticket_id)
                                        .where(ServiceStep.action == ACTION, ServiceStep.at >= since, ServiceStep.actor_id != user_id)
                                        .order_by(ServiceStep.id.desc()).limit(30)):
        if previous_assignee(session, step) == user_id:
            out.append((step.id, number, step.note))
    return out


def notify(ticket_id: int, old_id: int, reason: str):
    """Chạy nền: Zalo báo thợ cũ. Zalo lỗi vẫn ghi ở Tin nhắn Zalo, không làm hỏng việc hủy giao."""
    from sqlalchemy.orm import Session

    from . import zalo_bot
    from .config import get_settings
    from .db import engine
    from .models import Customer, ZaloChat
    if not get_settings().zalo_bot_token.strip():
        return 0
    with Session(engine()) as session:
        t = session.get(ServiceTicket, ticket_id)
        if t is None:
            return 0
        customer = session.get(Customer, t.customer_code)
        when = ''
        if t.appointment_at:
            when = f' (lịch {t.appointment_at.astimezone(service_sla.TZ).strftime("%d/%m %H:%M")})'
        body = '\n'.join((f'{t.number} · {customer.name if customer else t.customer_code}',
                          f'Phiếu đã được rút khỏi anh/chị{when}.', f'Lý do: {reason}.', 'Anh/chị không cần làm phiếu này nữa.'))
        chat = session.scalar(select(ZaloChat).join(User, User.id == ZaloChat.user_id).where(
            ZaloChat.user_id == old_id, User.active.is_(True), ZaloChat.chat_type == 'PRIVATE').order_by(ZaloChat.id).limit(1))
        if chat is None:
            return 0
        zalo_bot.send_text(session, chat, body, None, 'thong_bao')
        return 1


RECENT = timedelta(days=3)

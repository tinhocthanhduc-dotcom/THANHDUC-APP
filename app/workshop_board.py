"""Bảng xưởng (TUẤN duyệt mẫu KuE6LqYr5PAakwcosnTbGK 17/11/2026).

Mỗi máy ở xưởng một hàng: máy, khách, bước (6 màu), thời gian, việc cần làm + ai làm. Chỉ đọc: nút trên hàng mở
đúng màn đang có (kiểm tra máy, phiếu dịch vụ, trả tại quầy); lõi nghiệp vụ vẫn kiểm tra quyền và trạng thái.

Bước:
  s1 Mới về       máy chờ sửa ở xưởng, chưa có kết quả kiểm tra (hoặc khách chưa đồng ý, kiểm tra lại) · Kho
  s2 Chờ duyệt giá đã kiểm tra, chưa gửi khách · Kinh doanh / giám đốc
  s3 Chờ khách    báo giá đã gửi, chờ khách trả lời · người đã gửi
  s4 Đang sửa     khách đã đồng ý (hoặc bảo hành miễn phí), chưa sửa xong · thợ, kho chốt
  s5 Chờ chốt     thợ đã báo sửa xong (ảnh + đã sửa gì), kho chốt tiền/vật tư/bảo hành rồi giao trả
  s6 Chờ trả      sửa xong hoặc khách không sửa, máy chưa về tay khách
"""
from datetime import timedelta

from sqlalchemy import select

from . import repair_quotes as rq
from .models import Customer, Equipment, EquipmentCustody, RepairInspection, ServiceStep, ServiceTicket, User
from .security import has_role
from .service_sla import TZ
from .service_tickets import DISPATCHERS, can_quote

STAGES = {'s1': 'Mới về', 's2': 'Chờ duyệt giá', 's3': 'Chờ khách', 's4': 'Đang sửa', 's5': 'Chờ chốt', 's6': 'Chờ trả'}
INSPECT_HOURS = 4      # mới về quá 4 giờ chưa kiểm tra: đỏ
PRICE_HOURS = 2        # kiểm tra xong quá 2 giờ chưa gửi khách: đỏ
CUSTODY_HOURS = 2      # kho chưa bấm nhận máy quá 2 giờ: đỏ


def _now():
    from .service_tickets import _now as now  # cùng đồng hồ với phiếu dịch vụ (test đặt giờ được)
    return now().astimezone(TZ)


def _ago(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 1:
        return 'vừa xong'
    if minutes < 60:
        return f'{minutes} phút'
    if minutes < 24 * 60:
        return f'{minutes // 60} giờ'
    return f'{minutes // (24 * 60)} ngày'


def _day(d) -> str:
    return d.strftime('%d/%m') if d else ''


def _left(today, d) -> tuple[str, str]:
    """Nhãn còn bao lâu tới ngày hẹn và độ gấp ('' | warn | bad)."""
    if d is None:
        return '', ''
    n = (d - today).days
    if n < 0:
        return f'Trễ hẹn {-n} ngày', 'bad'
    if n == 0:
        return 'Hẹn hôm nay', 'warn'
    return f'Còn {n} ngày', 'warn' if n == 1 else ''


def tickets(session):
    """Phiếu đang ở xưởng hoặc đã chốt ở xưởng mà máy chưa về tay khách."""
    t = ServiceTicket
    held = select(Equipment.custody_ticket_id).where(Equipment.custody_ticket_id.is_not(None),
                                                     Equipment.custody_location != 'TAI_KHACH')
    stmt = select(t).where(
        ((t.status == 'TAM_DUNG') & t.hold_reason.in_(('CHO_SUA_XUONG', 'SUA_XONG_CHO_TRA')))
        | (t.status.in_(('DA_GIAO', 'DA_NHAN', 'DA_HEN', 'DANG_LAM')) & t.priced_at.is_not(None)
           & (t.repaired_by.is_not(None) | t.declined_at.is_not(None)) & t.id.in_(held))
    ).order_by(t.id)
    return list(session.scalars(stmt))


def _since(session, ids):
    """Lúc máy về xưởng (bước bàn giao gần nhất) theo phiếu."""
    if not ids:
        return {}
    rows = session.execute(select(ServiceStep.ticket_id, ServiceStep.at).where(
        ServiceStep.ticket_id.in_(ids), ServiceStep.action.in_(('workshop_handover', 'counter_intake'))).order_by(ServiceStep.id))
    return {tid: at for tid, at in rows}


def waiting(session) -> dict:
    """{ticket_id: (bước, hạn, thợ được giao)} cho các bước có người phải làm: s1 kho kiểm tra (4 giờ), s2 duyệt giá gửi khách
    (2 giờ), s3 gọi lại khách (2 ngày), s4 thợ sửa (trước ngày hẹn trả 1 ngày), s5 kho chốt (4 giờ).
    Việc của tôi + chuông + Trưởng nhóm AI dùng (đợt 1d, đợt 2)."""
    from datetime import datetime, time as dtime
    from .service_workshop import reported, warranty_free
    t = ServiceTicket
    items = list(session.scalars(select(t).where(t.status == 'TAM_DUNG', t.hold_reason == 'CHO_SUA_XUONG',
                                                 t.declined_at.is_(None)).order_by(t.id)))
    if not items:
        return {}
    ids = [x.id for x in items]
    since = _since(session, ids)
    done = reported(session, ids)
    inspections = {r.ticket_id: r for r in session.scalars(select(RepairInspection).where(RepairInspection.ticket_id.in_(ids)))}
    out = {}
    for x in items:
        if x.quote_approved_at is not None or warranty_free(x):
            if x.id in done:
                out[x.id] = ('s5', (done[x.id].at or x.created_at) + timedelta(hours=INSPECT_HOURS), x.assignee_id)
            else:
                day = (x.promised_return_on - timedelta(days=1)) if x.promised_return_on else None
                out[x.id] = ('s4', datetime.combine(day, dtime(17, 0), TZ) if day else None, x.assignee_id)
            continue
        sent = rq.open_version(session, x.id)
        r = inspections.get(x.id)
        if sent is not None and sent.sent_at:
            out[x.id] = ('s3', sent.sent_at + timedelta(days=2), x.assignee_id)
        elif r is not None and r.items and (r.applied_at is not None or not r.recheck_reason):
            out[x.id] = ('s2', (r.inspected_at or x.created_at) + timedelta(hours=PRICE_HOURS), x.assignee_id)
        else:
            out[x.id] = ('s1', (since.get(x.id) or x.created_at) + timedelta(hours=INSPECT_HOURS), x.assignee_id)
    return out


def build(session, user) -> dict:
    from . import repair_inspection as ri
    from .service_workshop import warranty_free
    from .text import fold
    now = _now()
    today = now.date()
    items = tickets(session)
    ids = [t.id for t in items]
    customers = {c.code: c for c in session.scalars(select(Customer).where(Customer.code.in_({t.customer_code for t in items})))} if items else {}
    names = dict(session.execute(select(User.id, User.full_name)).all())
    inspections = {r.ticket_id: r for r in session.scalars(select(RepairInspection).where(RepairInspection.ticket_id.in_(ids)))} if ids else {}
    since = _since(session, ids)
    machines = {e.serial: e for e in session.scalars(select(Equipment).where(Equipment.serial.in_({t.serial for t in items if t.serial})))} if items else {}
    pending = {r.serial: r for r in session.scalars(select(EquipmentCustody).where(
        EquipmentCustody.serial.in_(list(machines)), EquipmentCustody.status == 'CHO_NHAN'))} if machines else {}
    from .service_workshop import reported
    done_map = reported(session, ids)
    quoter = can_quote(user)
    dispatcher = has_role(user, *DISPATCHERS)
    inspector = ri.can_inspect(user)
    sales = has_role(user, 'sales')
    rows = []
    for t in items:
        c = customers.get(t.customer_code)
        r = inspections.get(t.id)
        eq = machines.get(t.serial) if t.serial else None
        machine = (r.model_name if r and r.model_name else '') or ri.machine_name(session, t) or 'Máy chưa ghi tên'
        came = since.get(t.id) or t.created_at
        sub_bits, step_note, chips = [t.number], '', []
        late, time_label, time_sub, tone = False, '', '', ''
        todo, who, action, mine = '', '', None, False
        href = f'/dich-vu/{t.id}'
        if r and r.items:
            from .models import PriceService
            first = session.get(PriceService, r.items[0].service_id)
            sub_bits.append(f'{first.name if first else "Gói sửa"} · Cấp {r.items[0].level}' + (f' +{len(r.items) - 1}' if len(r.items) > 1 else ''))
        elif t.symptom:
            sub_bits.append(' '.join(t.symptom.split())[:60])
        wait = pending.get(t.serial) if t.serial else None
        if t.status == 'TAM_DUNG' and t.hold_reason == 'CHO_SUA_XUONG' and t.declined_at is None:
            sent = rq.open_version(session, t.id)
            if (t.quote_approved_at or warranty_free(t)) and t.id in done_map:
                rep = done_map[t.id]
                stage = 's5'
                time_label, tone = _left(today, t.promised_return_on)
                late = tone == 'bad'
                at = rep.at.astimezone(TZ) if rep.at else None
                time_sub = f'Thợ báo {at.strftime("%H:%M") if at and at.date() == today else _day(at)}' if at else ''
                step_note = (rep.note or '')[:80]
                todo, who = 'Chốt sửa xong, giao trả', 'Kho · ' + (names.get(rep.actor_id) or 'thợ') + ' đã sửa'
                if dispatcher:
                    action, mine = dict(label='Chốt · giao trả', href=f'{href}#workshop-repaired', tone='ok'), True
            elif t.quote_approved_at or warranty_free(t):
                stage = 's4'
                time_label, tone = _left(today, t.promised_return_on)
                late = tone == 'bad'
                time_sub = f'Hẹn trả {_day(t.promised_return_on)}' if t.promised_return_on else ''
                step_note = 'Bảo hành miễn phí' if not t.quote_approved_at else ''
                if eq is not None and eq.custody_location == 'DOI_TAC':
                    chips.append(('Ở đối tác', 'warn'))
                todo, who = 'Sửa máy, báo sửa xong', 'Thợ ' + (names.get(t.assignee_id) or 'xưởng')
                if t.assignee_id == user.id and 'technician' in user.roles:
                    action, mine = dict(label='Báo sửa xong', href=f'/tho/phieu/{t.id}#sua-xuong', tone='ok'), True
                elif dispatcher:  # thợ không dùng app: kho chốt thẳng như cũ
                    action = dict(label='Sửa xong', href=f'{href}#workshop-repaired', tone='')
            elif sent is not None and sent.sent_at:
                stage = 's3'
                days = rq.waiting_days(sent)
                late = rq.overdue(sent) or days >= 2
                time_label, tone = (f'Chờ {days} ngày' if days else 'Gửi hôm nay'), ('bad' if late else 'warn' if days else '')
                if late:
                    time_sub = 'Quá hạn gọi lại'
                total = (sent.snapshot.get('chosen') or {}).get('total')
                step_note = (f'{total:,.0f} đ'.replace(',', '.') if total else '') + (f' · mở link {sent.opened_count} lần' if sent.opened_count else ' · chưa mở link')
                todo = 'Gọi lại khách, ghi trả lời' if late else 'Chờ khách trả lời'
                who = (names.get(sent.sent_by) or names.get(sent.created_by) or '') + ' đã gửi'
                if quoter:
                    action, mine = dict(label='Ghi trả lời', href=f'{href}#gui-khach', tone='ok' if late else ''), True
            elif r is not None and r.items and (r.applied_at is not None or not r.recheck_reason):
                stage = 's2'
                done = r.inspected_at
                gone = now - done if done else timedelta(0)
                late = gone > timedelta(hours=PRICE_HOURS)
                time_label, tone = f'Kiểm tra xong {done.astimezone(TZ).strftime("%H:%M") if done and done.astimezone(TZ).date() == today else _day(done.astimezone(TZ)) if done else ""}', 'bad' if late else ''
                time_sub = 'Vừa kiểm tra' if gone < timedelta(minutes=1) else f'Chờ {_ago(gone)}'
                todo = 'Duyệt báo giá, gửi khách' if r.applied_at is None else 'Gửi báo giá cho khách'
                pl = plan(session, r)
                if pl and pl['missing']:
                    chips.append((f'Thiếu {len(pl["missing"])} LK', 'warn'))
                if pl:
                    step_note = f'Công {pl["hours_text"]} · xong dự kiến {_day(pl["eta"])}'
                who = 'Kinh doanh hoặc giám đốc'
                if quoter:
                    action, mine = dict(label='Duyệt · gửi', href=f'{href}#gui-khach', tone='ok'), True
            else:
                stage = 's1'
                gone = now - came
                late = gone > timedelta(hours=INSPECT_HOURS)
                time_label, tone = ('Vừa về' if gone < timedelta(minutes=1) else f'Về {_ago(gone)}'), 'bad' if late else ''
                time_sub = f'Hạn kiểm tra {INSPECT_HOURS} giờ'
                if r is not None and r.recheck_reason:
                    step_note = 'Kiểm tra lại: ' + r.recheck_reason[:80]
                    todo = 'Kiểm tra lại theo ý khách'
                else:
                    todo = 'Nhận máy, kiểm tra'
                if wait is not None:
                    chips.append(('Kho chưa nhận máy', 'warn'))
                who = 'Kho'
                if inspector:
                    action, mine = dict(label='Nhận máy · kiểm tra' if wait is not None else 'Kiểm tra máy', href=f'/kiem-tra-may/{t.id}', tone='new'), True
        else:
            stage = 's6'
            back_with = None
            if t.status == 'TAM_DUNG':
                time_label = 'Chờ hẹn trả'
                if t.declined_at:
                    step_note = 'Khách không sửa'
                if t.counter_intake or not t.assignee_id:
                    todo, who = 'Chờ khách tới lấy, trả tại quầy', 'Thanh hoặc điều phối'
                    if sales or dispatcher:
                        action, mine = dict(label='Trả tại quầy', href=f'/quay-sua/{t.id}', tone=''), True
                else:
                    todo, who = 'Hẹn ngày, giao thợ trả máy', 'Điều phối'
                    if dispatcher:
                        action, mine = dict(label='Giao thợ trả', href=f'{href}#service-schedule-return', tone='ok'), True
                done_at = t.priced_at
                if done_at is not None and now - done_at > timedelta(days=1):
                    late, tone = True, 'bad'
                    time_sub = f'Sửa xong {_ago(now - done_at)}'
            else:
                back_with = names.get(t.assignee_id, 'thợ')
                when = t.appointment_at.astimezone(TZ) if t.appointment_at else None
                time_label = f'Trả {_day(when)}' if when else 'Thợ đang giữ'
                if when and when.date() < today:
                    late, tone = True, 'bad'
                step_note = f'{back_with} mang trả'
                todo, who = 'Mang trả máy, thu tiền', 'Thợ · ' + back_with
                mine = t.assignee_id == user.id
                if mine:
                    action = dict(label='Mở màn thợ', href=f'/tho/phieu/{t.id}', tone='')
        rows.append(dict(t=t, stage=stage, stage_label=STAGES[stage], machine=machine, sub=' · '.join(b for b in sub_bits if b),
                         customer=c.name if c else t.customer_code, contact=(c.address or '')[:60] if c else '',
                         note=step_note, chips=chips, late=late, time=time_label, time_sub=time_sub, tone=tone,
                         todo=todo, who=who, action=action, mine=mine, href=href,
                         search=fold(' '.join((t.number, machine, c.name if c else '', t.serial or '', t.customer_code)))))
    order = {k: i for i, k in enumerate(STAGES)}
    rows.sort(key=lambda x: (not x['late'], not x['mine'], order[x['stage']], x['t'].id))
    counts = dict(mine=sum(1 for x in rows if x['mine']), all=len(rows), late=sum(1 for x in rows if x['late']),
                  **{k: sum(1 for x in rows if x['stage'] == k) for k in STAGES})
    waiting_total = 0
    for x in rows:
        if x['stage'] == 's3':
            v = rq.open_version(session, x['t'].id)
            waiting_total += int((v.snapshot.get('chosen') or {}).get('total') or 0) if v else 0
    return dict(rows=rows, counts=counts, waiting_total=waiting_total, stages=STAGES)


def pick(rows: list[dict], view: str, q: str = '') -> list[dict]:
    from .text import fold
    if view == 'cua-toi':
        rows = [x for x in rows if x['mine']]
    elif view == 'qua-han':
        rows = [x for x in rows if x['late']]
    elif view in STAGES:
        rows = [x for x in rows if x['stage'] == view]
    if q:
        key = fold(q)
        rows = [x for x in rows if key in x['search']]
    return rows


# ---------- dự trù khi kiểm tra (TUẤN 17/11: kho chọn gói + linh kiện, app tự hiện còn/thiếu, giờ công, ngày xong) ----------

def part_stock(session, codes=None) -> dict:
    """Số còn dùng được của linh kiện, cộng mọi kho hàng (loại KHO, đang dùng); không tính kho thợ, kho lỗi."""
    from .models import Warehouse
    from .stock_reservations import quantities
    kho = set(session.scalars(select(Warehouse.code).where(Warehouse.kind == 'KHO', Warehouse.active.is_(True))))
    out = {}
    for (wh, code), q in quantities(session).items():
        if wh in kho and (codes is None or code in codes):
            out[code] = out.get(code, 0) + max(0, int(q['available'] or 0))
    return out


def add_work_days(day, n: int):
    """Cộng n ngày làm việc (bỏ Chủ nhật)."""
    while n > 0:
        day += timedelta(days=1)
        if day.weekday() != 6:
            n -= 1
    return day


def plan(session, row) -> dict | None:
    """Dự trù từ kết quả kiểm tra: giờ công theo cấp (bảng giá), linh kiện thiếu, ngày xong dự kiến."""
    import math
    from decimal import Decimal
    from .models import PriceService, Product
    if row is None or not row.items:
        return None
    hours = Decimal(0)
    need = {}
    for i in row.items:
        s = session.get(PriceService, i.service_id)
        hours += Decimal(getattr(s, f'hours_{i.level}', 0) or 0) if s else 0
        for p in i.parts or []:
            need[p['code']] = need.get(p['code'], 0) + int(p.get('qty') or 1)
    have = part_stock(session, set(need)) if need else {}
    names = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_(list(need)))).all()) if need else {}
    missing = [dict(code=c, name=names.get(c, c), need=n, have=have.get(c, 0)) for c, n in need.items() if have.get(c, 0) < n]
    days = max(1, math.ceil(float(hours) / 8)) + (1 if missing else 0)
    return dict(hours=hours, hours_text=(f'{hours:.1f}'.rstrip('0').rstrip('.').replace('.', ',') + ' giờ') if hours else 'chưa có giờ chuẩn',
                parts=len(need), missing=missing, eta=add_work_days(_now().date(), days))

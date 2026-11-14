"""Thợ kiểm tra máy + báo giá sửa chữa chính thức (TUẤN duyệt mẫu M3/M4 14/11/2026, đợt 3).

- Máy ở xưởng (phiếu Tạm dừng · Chờ sửa ở xưởng): thợ chọn dịch vụ gói, tick phương án kỹ thuật. App tự ra cấp
  theo số phương án "chính" (0 → C1, 1 → C2, từ 2 → C3). Chọn cấp khác phải ghi lý do; điều phối thấy lý do khi áp giá.
  Thợ ghi linh kiện dự kiến (nội bộ). Thợ không thấy giá.
- Điều phối: giá lấy từ bảng giá chung (model ghi đè → giá nhóm), PA1 tiêu chuẩn hoặc PA2 chính hãng. "Áp vào báo giá"
  ghi mỗi dịch vụ thành MỘT dòng PHI trọn gói trên phiếu (tên cho khách, giá chưa VAT, VAT 8%), giữ các dòng khác.
  Ghi qua service_charges.set_charges_in_tx: đổi tiền thì "khách đồng ý" cũ tự bỏ như sửa tay. Thiếu giá: không áp.
- Lãi trực tiếp (chỉ giám đốc, kế toán thấy) = gói chưa VAT − giá vốn linh kiện dự kiến − công kỹ thuật
  (giờ chuẩn × lương/ngày công/8 + hoa hồng thợ) − chi phí phiếu đã duyệt. Dưới sàn 30%: chỉ giám đốc áp được.
- Mô tả kết quả cho khách (TUẤN 14/11): lưu kết quả kiểm tra thì nhờ Codex/Claude trên Z2 soạn sẵn mỗi dịch vụ 1–2 câu.
  4 chốt: chỉ ghi vào ô cho khách (không tên/mã linh kiện, không giá); có số lạ (ngoài tên model) thì bỏ; Z2 tắt hoặc quá
  3 phút thì dùng mẫu mô tả trong bảng giá; điều phối sửa được trước khi áp. Bản đã duyệt lưu ở customer_note.
- Không trừ kho, không tạo nợ: linh kiện thực dùng vẫn nhập ở Tiền và vật tư như cũ (đợt 5 làm tiếp).
"""
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import audit
from . import price_book as pb
from .config import DEFAULT_VAT_RATE
from .errors import BusinessError
from .models import (CommissionRule, Employee, EmployeeSalaryVersion, Equipment, PriceService, Product, RepairInspection,
                     RepairInspectionItem, RepairModel, ServiceTicket, User)
from .security import has_role
from .service_sla import TZ
from .text import fold

INSPECTORS = ('technician', 'dispatcher')
WAITING = 'CHO_SUA_XUONG'
FLOOR_PCT = 30
MAX_SERVICES = 6
MAX_PARTS = 10
AI_WAIT = 180  # giây: quá 3 phút máy Z2 chưa soạn xong thì dùng mẫu trong bảng giá
MAX_NOTE = 500
AI_PROMPT = """Bạn soạn phần "Mô tả kết quả" trên báo giá sửa chữa máy in của Công ty Tin Học Thành Đức gửi khách hàng.
Máy: {model}. Khách báo: {symptom}
Thợ ghi (nội bộ): {note}
Các hạng mục (số thứ tự. tên in cho khách | việc kỹ thuật nội bộ | mẫu mô tả):
{items}
Quy tắc bắt buộc:
- Mỗi hạng mục 1–2 câu, nói khách nhận được gì sau khi sửa (máy in lại bình thường, hết lỗi gì), lịch sự, dễ hiểu.
- KHÔNG nêu tên linh kiện cụ thể, mã hàng, giá, số lượng, phần trăm, thời gian. Không có chữ số nào ngoài tên model.
- Không hứa bảo hành, giảm giá, ngày xong. Văn bản thường, không markdown, mỗi mô tả dưới 300 ký tự.
- Chỉ trả về một đối tượng JSON, khóa là số thứ tự dạng chuỗi, giá trị là mô tả. Ví dụ: {{"1": "...", "2": "..."}}"""


def _now():
    return datetime.now(TZ)


def can_inspect(user: User) -> bool:
    return bool(user and user.active and has_role(user, *INSPECTORS))


def require_inspect(user: User):
    if not can_inspect(user):
        raise BusinessError('FORBIDDEN', 'Chỉ thợ hoặc điều phối được ghi kết quả kiểm tra máy.', status=403)


def is_waiting(t: ServiceTicket) -> bool:
    return t.status == 'TAM_DUNG' and t.hold_reason == WAITING and t.declined_at is None


def load_ticket(session: Session, user: User, ticket_id: int, lock=False) -> ServiceTicket:
    require_inspect(user)
    stmt = select(ServiceTicket).where(ServiceTicket.id == ticket_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    t = session.scalars(stmt).first()
    if t is None:
        raise BusinessError('NOT_FOUND', 'Không có phiếu này.', status=404)
    return t


def machine_name(session: Session, t: ServiceTicket) -> str:
    if not t.serial:
        return ''
    row = session.execute(select(Equipment.model_name, Product.name).outerjoin(Product, Product.code == Equipment.product_code)
                          .where(Equipment.serial == t.serial)).first()
    return (row[1] or row[0] or '') if row else ''


def guess_model(session: Session, name: str) -> RepairModel | None:
    """Model trong bảng giá khớp tên máy: trùng tên, hoặc mọi chữ của model nằm trong tên máy (lấy tên dài nhất)."""
    exact = pb.find_model(session, name)
    if exact and exact.active:
        return exact
    text = fold(name).replace('-', ' ')
    if not text:
        return None
    best = None
    for m in session.scalars(select(RepairModel).where(RepairModel.active.is_(True))):
        words = fold(m.name).replace('-', ' ').split()
        if words and all(w in text.split() for w in words) and (best is None or len(m.name) > len(best.name)):
            best = m
    return best


def inspection(session: Session, ticket_id: int) -> RepairInspection | None:
    return session.get(RepairInspection, ticket_id)


def waiting_rows(session: Session) -> list[dict]:
    """Máy ở xưởng chờ kiểm tra / chờ báo giá / chờ khách: cho thợ và điều phối."""
    tickets = list(session.scalars(select(ServiceTicket).where(ServiceTicket.status == 'TAM_DUNG', ServiceTicket.hold_reason == WAITING,
                                                             ServiceTicket.declined_at.is_(None)).order_by(ServiceTicket.id)))
    found = {r.ticket_id: r for r in session.scalars(select(RepairInspection).where(RepairInspection.ticket_id.in_([t.id for t in tickets])))} \
        if tickets else {}
    out = []
    for t in tickets:
        r = found.get(t.id)
        stage = ('Khách đồng ý' if t.quote_approved_at else 'Đã báo giá' if r and r.applied_at else
                 'Chờ điều phối báo giá' if r else 'Chờ kiểm tra')
        out.append(dict(ticket=t, inspection=r, stage=stage, machine=machine_name(session, t), todo=r is None))
    out.sort(key=lambda x: (not x['todo'], x['ticket'].id))
    return out


# ---------- thợ ghi kết quả ----------

def _ids(values, limit) -> list[int]:
    out = []
    for v in values:
        v = str(v or '').strip()
        if v.isdigit() and len(v) < 10 and int(v) not in out:
            out.append(int(v))
    return out[:limit]


def _parts(codes, qtys) -> list[dict]:
    out = []
    for code, qty in zip(codes, qtys):
        code = str(code or '').strip().upper()[:16]
        if not code:
            continue
        raw = str(qty or '1').strip().replace(',', '.')
        try:
            q = Decimal(raw)
        except ArithmeticError:
            q = Decimal(0)
        if q <= 0 or q > 1000 or q != q.to_integral_value():
            raise BusinessError('BAD_QTY', f'Số lượng linh kiện {code} phải là số nguyên dương.')
        out.append(dict(code=code, qty=int(q)))
    return out[:MAX_PARTS]


def save(session: Session, user: User, ticket_id: int, form) -> RepairInspection:
    """form: model, dv (list mã dịch vụ), opt_<dv> (list mã phương án), cap_<dv>, ly_do_<dv>, ch_<dv>, lk_<dv>/sl_<dv>, note, version."""
    t = load_ticket(session, user, ticket_id, lock=True)
    if not is_waiting(t):
        raise BusinessError('BAD_STATUS', 'Chỉ kiểm tra máy đang chờ sửa ở xưởng (chưa sửa xong, khách chưa từ chối).', status=409)
    row = session.get(RepairInspection, t.id, with_for_update=True)
    version = str(form.get('version', '') or '').strip()
    if row is not None and version and version != str(row.version):
        raise BusinessError('STALE', 'Kết quả kiểm tra vừa được người khác sửa, hãy tải lại trang.', status=409)
    model_name = ' '.join(str(form.get('model', '') or '').split())[:160]
    if len(model_name) < 2:
        raise BusinessError('BAD_MODEL', 'Ghi model máy (vd Canon LBP 2900) để điều phối tra giá.')
    model = pb.find_model(session, model_name)
    service_ids = _ids(form.getlist('dv'), MAX_SERVICES)
    if not service_ids:
        raise BusinessError('NO_SERVICE', 'Chọn ít nhất một dịch vụ cần làm.')
    items = []
    for sort, sid in enumerate(service_ids, 1):
        s = session.get(PriceService, sid)
        if s is None or s.kind != 'GOI' or not s.active:
            raise BusinessError('BAD_SERVICE', 'Dịch vụ đã chọn không còn trong bảng giá, hãy tải lại trang.')
        opts = {o.id: o for o in pb.options(session, sid)}
        chosen = [opts[i] for i in _ids(form.getlist(f'opt_{sid}'), 20) if i in opts]
        auto = pb.level_by_options(sum(1 for o in chosen if o.is_main))
        raw = str(form.get(f'cap_{sid}', '') or '').strip()
        level = int(raw) if raw in ('1', '2', '3') else auto
        reason = ' '.join(str(form.get(f'ly_do_{sid}', '') or '').split())[:500]
        if level != auto and len(reason) < 3:
            raise BusinessError('LEVEL_REASON', f'{s.name}: chọn cấp {level} khác cấp app tính (cấp {auto}) thì ghi lý do.')
        items.append(RepairInspectionItem(service_id=sid, options=[o.name for o in chosen], level_auto=auto, level=level,
                                          level_reason=reason if level != auto else '', sort=sort,
                                          genuine=str(form.get(f'ch_{sid}', '')) == '1',
                                          parts=_parts(form.getlist(f'lk_{sid}'), form.getlist(f'sl_{sid}'))))
    codes = {p['code'] for i in items for p in i.parts}
    if codes:
        known = set(session.scalars(select(Product.code).where(Product.code.in_(codes))))
        missing = sorted(codes - known)
        if missing:
            raise BusinessError('BAD_PART', f'Không có mã linh kiện: {", ".join(missing)}.')
    before = snapshot(row) if row else None
    now = _now()
    if row is None:
        row = RepairInspection(ticket_id=t.id, inspected_by=user.id, inspected_at=now)
        session.add(row)
    row.model_name, row.model_id = (model.name, model.id) if model else (model_name, None)
    row.note = str(form.get('note', '') or '').strip()[:2000]
    row.inspected_by, row.inspected_at = user.id, now
    old_items = {i.service_id: (_item_snap(i), i.customer_note) for i in row.items}
    for i in items:
        snap, note = old_items.get(i.service_id, (None, ''))
        if snap == _item_snap(i) and before and before['model'] == row.model_name:
            i.customer_note = note  # dịch vụ không đổi: giữ mô tả điều phối đã duyệt
    row.items[:] = []
    session.flush()
    row.items.extend(items)
    changed = before is None or before['items'] != [_item_snap(i) for i in items] or before['model'] != row.model_name
    if changed or before['note'] != row.note:
        session.flush()
        row.ai_job_id = _request_ai(session, t, row)
    if row.applied_at is not None and changed:
        # Kết quả đổi sau khi đã báo giá: điều phối phải áp lại (phiếu vẫn giữ dòng tiền cũ tới lúc đó).
        row.applied_at = row.applied_by = None
    session.flush()
    audit.record(session, user, 'repair_inspection.save', 'service_ticket', str(t.id), before, snapshot(row))
    session.commit()
    return row


def _item_snap(i: RepairInspectionItem) -> dict:
    return dict(service=i.service_id, options=list(i.options), level=i.level, auto=i.level_auto, reason=i.level_reason,
                genuine=bool(i.genuine), parts=list(i.parts))


def snapshot(row: RepairInspection) -> dict:
    return dict(model=row.model_name, note=row.note, items=[_item_snap(i) for i in row.items],
                applied=row.applied_at.isoformat() if row.applied_at else None)


# ---------- mô tả kết quả cho khách (Codex/Claude trên Z2) ----------

def _request_ai(session: Session, t: ServiceTicket, row: RepairInspection) -> int | None:
    """Gửi lượt soạn mô tả cho máy Z2; Z2 tắt hoặc chưa cấu hình thì thôi (dùng mẫu bảng giá), không chặn lưu."""
    from . import zalo_agent
    from .models import ZaloAiJob
    cfg = zalo_agent.config(session)
    if cfg.staff_engine not in ('codex', 'claude') or not zalo_agent.z2_alive(session):
        return None
    lines = []
    for n, i in enumerate(row.items, 1):
        s = session.get(PriceService, i.service_id)
        lines.append(f"{n}. {pb.customer_name(s, row.model_name)} | {', '.join(i.options) or 'theo thực tế'} | {s.description or ''}")
    prompt = AI_PROMPT.format(model=row.model_name, symptom=' '.join((t.symptom or '').split())[:300],
                              note=' '.join((row.note or '').split())[:300] or 'không', items='\n'.join(lines))
    job = ZaloAiJob(log_id=None, engine=cfg.staff_engine, prompt=prompt)
    session.add(job)
    session.flush()
    return job.id


_DIGITS = re.compile(r'\d+')


def _clean_note(text, model: str) -> str | None:
    """Chốt an toàn cho mô tả Codex soạn: không số lạ, không mã hàng, độ dài vừa phải. Không đạt: None."""
    text = ' '.join(str(text or '').replace('**', '').split()).strip('"“” ')
    if not 10 <= len(text) <= 300 or 'TD-' in text.upper():
        return None
    allowed = set(_DIGITS.findall(model or ''))
    if any(d not in allowed for d in _DIGITS.findall(text)):
        return None
    return text


def ai_state(session: Session, row: RepairInspection) -> dict:
    """Tình trạng lượt soạn: None (không có), DANG, LOI, XONG kèm texts {service_id: mô tả đạt chốt}."""
    from .models import ZaloAiJob
    job = session.get(ZaloAiJob, row.ai_job_id) if row.ai_job_id else None
    if job is None:
        return dict(status=None, texts={})
    if job.status in ('CHO', 'DANG'):
        if _now() - job.created_at > timedelta(seconds=AI_WAIT):
            return dict(status='LOI', texts={}, error=f'Quá {AI_WAIT // 60} phút máy Z2 chưa soạn xong')
        return dict(status='DANG', texts={})
    if job.status == 'LOI':
        return dict(status='LOI', texts={}, error=(job.error or '')[:200])
    raw = (job.answer or '').strip().strip('`')
    raw = raw[raw.find('{'):raw.rfind('}') + 1] if '{' in raw else ''
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    texts = {}
    for n, i in enumerate(row.items, 1):
        text = _clean_note(data.get(str(n)) if isinstance(data, dict) else None, row.model_name)
        if text:
            texts[i.service_id] = text
    return dict(status='XONG', texts=texts)


def descriptions(session: Session, row: RepairInspection) -> dict:
    """Mô tả cho khách từng dịch vụ: đã duyệt > Codex soạn (đạt chốt) > mẫu trong bảng giá. {service_id: (text, nguồn)}."""
    state = ai_state(session, row)
    out = {}
    for i in row.items:
        s = session.get(PriceService, i.service_id)
        if i.customer_note:
            out[i.service_id] = (i.customer_note, 'duyet')
        elif i.service_id in state['texts']:
            out[i.service_id] = (state['texts'][i.service_id], 'ai')
        else:
            out[i.service_id] = (s.description or '', 'mau')
    return dict(texts=out, state=state)


# ---------- báo giá + lãi ----------

def hourly_rate(session: Session, user_id: int | None) -> int | None:
    """Lương / ngày công chuẩn / 8 giờ của thợ (bản lương đang hiệu lực). Không có hồ sơ lương: None."""
    if not user_id:
        return None
    today = _now().date()
    row = session.scalars(select(EmployeeSalaryVersion).join(Employee, Employee.id == EmployeeSalaryVersion.employee_id)
                          .where(Employee.user_id == user_id, EmployeeSalaryVersion.replaced_at.is_(None),
                                 EmployeeSalaryVersion.effective_from <= today)
                          .order_by(EmployeeSalaryVersion.effective_from.desc()).limit(1)).first()
    if row is None or not row.standard_days:
        return None
    return int((Decimal(row.base_salary) / row.standard_days / 8).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def tech_rule(session: Session, kind: str | None, user_id: int | None, day) -> CommissionRule | None:
    """Quy tắc hoa hồng thợ áp cho phiếu (giống bảng hoa hồng: riêng người > riêng loại phiếu > chung)."""
    stmt = select(CommissionRule).where(CommissionRule.target == 'THO', CommissionRule.replaced_at.is_(None),
                                        CommissionRule.effective_from <= day,
                                        or_(CommissionRule.user_id.is_(None), CommissionRule.user_id == user_id),
                                        or_(CommissionRule.service_kind.is_(None), CommissionRule.service_kind == kind),
                                        or_(CommissionRule.service_kind.is_(None), CommissionRule.service_kind.notin_(('SAN_XUAT', 'GIAO_DON'))))
    rows = list(session.scalars(stmt))
    rows.sort(key=lambda r: (r.user_id is not None, r.service_kind is not None, r.effective_from, r.id), reverse=True)
    return rows[0] if rows else None


def quote(session: Session, t: ServiceTicket, row: RepairInspection, choices: dict | None = None, with_cost: bool = False) -> dict:
    """Giá từng dịch vụ (PA1 tiêu chuẩn / PA2 chính hãng), bản khách, và lãi trực tiếp nếu with_cost."""
    from .production_bom import unit_costs
    choices = choices or {}
    lines, ok = [], True
    codes = {p['code'] for i in row.items for p in i.parts}
    costs = unit_costs(session, codes) if with_cost else {}
    names = dict(session.execute(select(Product.code, Product.name).where(Product.code.in_(codes))).all()) if codes else {}
    hours = Decimal(0)
    desc = descriptions(session, row)
    for i in row.items:
        s = session.get(PriceService, i.service_id)
        std = pb.lookup(session, i.service_id, row.model_name, i.level)
        gen = pb.lookup(session, i.service_id, row.model_name, i.level, genuine=True)
        genuine = choices.get(i.service_id, i.genuine) and gen['ok']
        pick = gen if genuine else std
        ok = ok and pick['ok']
        part_rows = [dict(code=p['code'], name=names.get(p['code'], p['code']), qty=p['qty'],
                          cost=costs.get(p['code'], 0) * p['qty'] if with_cost else None) for p in i.parts]
        h = getattr(s, f'hours_{i.level}') or Decimal(0)
        hours += Decimal(h)
        lines.append(dict(item=i, service=s, std=std, gen=gen, genuine=bool(genuine), pick=pick, ok=pick['ok'], parts=part_rows,
                          hours=h, customer_name=pick['name'] or pb.customer_name(s, row.model_name),
                          description=desc['texts'][i.service_id][0], description_source=desc['texts'][i.service_id][1],
                          warranty=pick['warranty']))
    subtotal = sum(ln['pick']['price'] or 0 for ln in lines)
    vat = sum(pb.with_vat(ln['pick']['price']) - ln['pick']['price'] for ln in lines if ln['ok'])
    out = dict(lines=lines, ai=desc['state'], ok=ok and bool(lines), subtotal=subtotal, vat=vat, total=subtotal + vat,
               warranty=max((ln['warranty'] or 0 for ln in lines if ln['ok']), default=None))
    if with_cost:
        out['margin'] = margin(session, t, row, out, hours)
    out['below_floor'] = _below_floor(session, t, row, out, hours)
    return out


def margin(session: Session, t: ServiceTicket, row: RepairInspection, q: dict, hours) -> dict:
    from .service_expenses import COUNTED
    from .models import ServiceExpense
    from sqlalchemy import func
    revenue = q['subtotal']
    parts = sum(p['cost'] or 0 for ln in q['lines'] for p in ln['parts'])
    rate = hourly_rate(session, row.inspected_by)
    labour = int((Decimal(hours) * (rate or 0)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    rule = tech_rule(session, t.kind, row.inspected_by, _now().date())
    base = max(0, revenue - parts)  # hoa hồng thợ tính trên gói − giá vốn linh kiện (TUẤN duyệt 14/11)
    commission = int((Decimal(rule.fixed_amount) + Decimal(rule.pct) / 100 * base).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if rule else 0
    expenses = int(session.scalar(select(func.coalesce(func.sum(ServiceExpense.amount), 0)).where(
        ServiceExpense.ticket_id == t.id, ServiceExpense.status.in_(COUNTED))) or 0)
    profit = revenue - parts - labour - commission - expenses
    pct = round(profit * 100 / revenue, 1) if revenue else None
    return dict(revenue=revenue, parts=parts, hours=hours, rate=rate, labour=labour, rule=rule, commission=commission,
                expenses=expenses, profit=profit, pct=pct, floor=FLOOR_PCT, below=pct is not None and pct < FLOOR_PCT)


def _below_floor(session, t, row, q, hours) -> bool:
    if not q['ok']:
        return False
    m = q.get('margin') or margin(session, t, row, q, hours)
    return m['below']


def apply(session: Session, user: User, ticket_id: int, version, choices: dict, notes: dict | None = None) -> RepairInspection:
    """Điều phối áp giá bảng giá vào phiếu: mỗi dịch vụ một dòng PHI trọn gói; dòng khác của phiếu giữ nguyên."""
    from . import service_charges, service_tickets
    from .schemas import ServiceCharges, ServiceLineIn
    from .stock_docs import _transaction
    if not has_role(user, *service_tickets.DISPATCHERS):
        raise BusinessError('FORBIDDEN', 'Chỉ điều phối hoặc giám đốc áp báo giá.', status=403)
    with _transaction(session):
        t = load_ticket(session, user, ticket_id, lock=True)
        service_tickets._check_version(t, version)
        if not is_waiting(t):
            raise BusinessError('BAD_STATUS', 'Phiếu không ở bước chờ sửa tại xưởng.', status=409)
        row = session.get(RepairInspection, t.id, with_for_update=True)
        if row is None or not row.items:
            raise BusinessError('NO_INSPECTION', 'Thợ chưa ghi kết quả kiểm tra máy.', status=409)
        for i in row.items:
            if i.service_id in choices:
                i.genuine = bool(choices[i.service_id])
        q = quote(session, t, row, with_cost=True)
        for ln in q['lines']:
            # Điều phối duyệt mô tả cho khách (sửa được); để trống thì giữ bản đang hiện (Codex soạn hoặc mẫu bảng giá).
            text = ' '.join(str((notes or {}).get(ln['item'].service_id, '') or '').split())[:MAX_NOTE] or ln['description']
            ln['item'].customer_note = ln['description'] = text
        if not q['ok']:
            missing = ', '.join(f"{ln['service'].name} cấp {ln['item'].level}" for ln in q['lines'] if not ln['ok'])
            raise BusinessError('NO_PRICE', f'Bảng giá chưa có giá cho: {missing}. Giám đốc thêm giá (hoặc giá riêng model) rồi áp lại.', status=409)
        if q['margin']['below'] and 'director' not in user.roles:
            raise BusinessError('BELOW_FLOOR', f'Lãi trực tiếp dưới sàn {FLOOR_PCT}%: cần giám đốc duyệt và áp báo giá.', status=409)
        old = set(row.applied_lines or [])
        keep = [ServiceLineIn(**service_charges.line_input(x)) for x in t.lines if not (x.kind == 'PHI' and x.stock_doc_id is None and x.description in old)]
        new = [ServiceLineIn(kind='PHI', description=ln['customer_name'][:500], qty=Decimal(1), unit_price=ln['pick']['price'],
                             vat_rate=DEFAULT_VAT_RATE) for ln in q['lines']]
        body = ServiceCharges(billing='THU_TIEN', lines=keep + new, version=t.version)
        service_charges.set_charges_in_tx(session, user, t, body)
        before = snapshot(row)
        row.applied_lines = [ln['customer_name'][:500] for ln in q['lines']]
        row.applied_at, row.applied_by, row.warranty_months = _now(), user.id, q['warranty']
        audit.record(session, user, 'repair_inspection.apply', 'service_ticket', str(t.id), before,
                     dict(snapshot(row), lines=[[ln['customer_name'], ln['pick']['price'], ln['item'].level, ln['genuine'], ln['description']] for ln in q['lines']],
                          pct=q['margin']['pct'], level_changes=[[ln['service'].name, ln['item'].level_auto, ln['item'].level,
                                                                  ln['item'].level_reason] for ln in q['lines']
                                                                 if ln['item'].level != ln['item'].level_auto]))
    return row

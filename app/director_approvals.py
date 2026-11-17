"""Đợt 4 rà soát (TUẤN duyệt mẫu 16/11/2026): trang "Chờ anh duyệt" gom mọi việc chờ giám đốc quyết.

Đợt 2 tối ưu thao tác (TUẤN duyệt mẫu 17/11/2026): mỗi dòng có Duyệt/Từ chối ngay tại trang, gọi đúng hàm nghiệp vụ
của trang gốc (cùng quyền, phiên bản, lý do bắt buộc); kiểm kê vẫn mở phiếu để xem chênh lệch. decide() là điểm vào duy nhất.
Dòng kế toán duyệt được trong hạn mức có cờ kt=True (vẫn hiện để giám đốc thấy đủ tiền ra).
"""
from datetime import datetime

from sqlalchemy import select

from . import approval_limits
from .models import (AiAction, CashCorrection, CashDoc, Customer, PendingDisposition, PendingItem, Product, SalesOrder,
                     SalesRefund, ServiceExpense, ServiceTicket, StockDoc, User)
from .service_sla import TZ

# Nhóm ô đếm trên đầu trang.
GROUPS = {'tien': 'Tiền ra (hoàn tiền, chi phí, tạm ứng)', 'kho': 'Kho (hủy hàng, kiểm kê, trả NCC)',
          'nhan-su': 'Chấm công · sửa phiếu', 'ai': 'Đội AI đề xuất'}
KIND_GROUP = {'HOAN_TIEN': 'tien', 'CHI_PHI': 'tien', 'TAM_UNG': 'tien', 'HUY_HANG': 'kho', 'KIEM_KE': 'kho', 'TRA_NCC': 'kho',
              'BO_SUNG': 'nhan-su', 'NGHI': 'nhan-su', 'SUA_PHIEU': 'nhan-su', 'AI': 'ai'}
KIND_LABEL = {'HOAN_TIEN': 'Hoàn tiền khách', 'CHI_PHI': 'Chi phí phát sinh', 'TAM_UNG': 'Tạm ứng lương', 'HUY_HANG': 'Hủy hàng',
              'KIEM_KE': 'Kiểm kê', 'TRA_NCC': 'Trả hàng lỗi NCC', 'BO_SUNG': 'Sửa chấm công', 'NGHI': 'Xin nghỉ',
              'SUA_PHIEU': 'Sửa phiếu thu chi', 'AI': 'Đội AI'}
# Tiền ra khỏi quỹ khi duyệt (ô tổng trên Tổng quan); hủy hàng/kiểm kê là giá trị kho, không cộng.
CASH_OUT = ('HOAN_TIEN', 'CHI_PHI', 'TAM_UNG')


# Loại duyệt được ngay tại dòng; KIEM_KE phải mở phiếu xem chênh lệch trước khi ghi sổ.
INLINE = ('HOAN_TIEN', 'CHI_PHI', 'TAM_UNG', 'BO_SUNG', 'NGHI', 'HUY_HANG', 'TRA_NCC', 'SUA_PHIEU', 'AI')


def _row(kind, number, title, who, amount, link, since=None, kt=False, ident=None, version=None):
    return dict(kind=kind, label=KIND_LABEL[kind], group=KIND_GROUP[kind], number=number or '', title=title or '',
                who=who or '', amount=amount, link=link, since=since, kt=kt, id=ident, version=version,
                inline=kind in INLINE and ident is not None)


def rows(session) -> list[dict]:
    names = dict(session.execute(select(User.id, User.full_name)).all())
    out = []
    for r in session.execute(select(SalesRefund, Customer.name).outerjoin(SalesOrder, SalesOrder.id == SalesRefund.order_id)
                             .outerjoin(Customer, Customer.code == SalesOrder.customer_code)
                             .where(SalesRefund.status == 'CHO_DUYET').order_by(SalesRefund.id)):
        x, cust = r
        out.append(_row('HOAN_TIEN', x.number, cust or x.reason, names.get(x.requested_by), int(x.amount), '/bao-thu?tab=hoan-tien',
                        ident=x.id, version=x.version))
    table = approval_limits.limits(session)
    finance_ids = approval_limits._finance_ids(session)
    for x, tno, ono in session.execute(select(ServiceExpense, ServiceTicket.number, SalesOrder.number)
                                       .outerjoin(ServiceTicket, ServiceTicket.id == ServiceExpense.ticket_id)
                                       .outerjoin(SalesOrder, SalesOrder.id == ServiceExpense.order_id)
                                       .where(ServiceExpense.status == 'CHO_DUYET').order_by(ServiceExpense.id)):
        out.append(_row('CHI_PHI', tno or ono, x.description, names.get(x.technician_id), int(x.amount), f'/chi-phi-tho?tab=cho-duyet#cp-{x.id}',
                        x.created_at, approval_limits._delegable('CHI_PHI', int(x.cost), x.technician_id, table, finance_ids),
                        ident=x.id, version=x.version))
    for q in approval_limits.queue(session):
        out.append(_row(q['key'], q['number'], q['label'], q['who'], q['amount'] or None, q['link'], None, q['delegable'],
                        ident=q['id'], version=q.get('version')))
    for x, product in session.execute(select(PendingDisposition, Product.name).join(PendingItem, PendingItem.id == PendingDisposition.item_id)
                                      .join(Product, Product.code == PendingItem.product_code)
                                      .where(PendingDisposition.kind == 'TRA_NCC', PendingDisposition.status == 'CHO_DUYET')
                                      .order_by(PendingDisposition.requested_at, PendingDisposition.id)):
        out.append(_row('TRA_NCC', x.number, product, names.get(x.requested_by), int(x.total or 0), f'/cho-xu-ly/duyet-tra-ncc#xl-{x.id}',
                        x.requested_at, True, ident=x.id, version=x.version))
    for x in session.scalars(select(StockDoc).where(StockDoc.kind == 'KK', StockDoc.status == 'NHAP').order_by(StockDoc.created_at, StockDoc.id)):
        out.append(_row('KIEM_KE', x.number, f'Kho {x.warehouse_code}', names.get(x.created_by), None, f'/phieu-kho/{x.id}', x.created_at))
    for x, doc in session.execute(select(CashCorrection, CashDoc).join(CashDoc, CashDoc.id == CashCorrection.doc_id)
                                  .where(CashCorrection.status == 'CHO_DUYET').order_by(CashCorrection.id)):
        out.append(_row('SUA_PHIEU', doc.number, x.reason, names.get(x.requested_by), int(x.new_amount), f'/thu-chi/{doc.id}',
                        ident=x.id, version=x.version))
    from .ai_agent import TABS
    for x in session.scalars(select(AiAction).where(AiAction.status.in_(TABS['cho-duyet'][1])).order_by(AiAction.created_at, AiAction.id)):
        out.append(_row('AI', '', x.title, 'Đội AI', None, '/tro-ly-ai', x.created_at, ident=x.id))
    return out


def _version(raw) -> int:
    from .errors import BusinessError
    try:
        value = int(str(raw))
        if value < 1:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise BusinessError('VERSION_REQUIRED', 'Trang đã cũ, hãy tải lại rồi thử lại.')


def decide(session, user, kind, ident, approve, version=None, reason=''):
    """Duyệt/Từ chối một dòng bằng đúng hàm của trang gốc. Mọi kiểm tra quyền, hạn mức, phiên bản, lý do nằm ở hàm đó."""
    from . import ai_agent, attendance, cash_corrections, payroll, pending_dispositions, sales_refunds, service_expenses
    from .errors import BusinessError
    reason = str(reason or '').strip()
    if kind not in INLINE:
        raise BusinessError('NOT_FOUND', 'Việc này phải mở phiếu để duyệt.', status=404)
    if kind == 'AI':
        return ai_agent.approve(session, user, ident) if approve else ai_agent.reject(session, user, ident, reason)
    version = _version(version)
    if kind == 'HOAN_TIEN':
        return sales_refunds.decide(session, user, ident, version, approve, reason)
    if kind == 'CHI_PHI':
        return service_expenses.decide(session, user, ident, version, approve, reason)
    if kind == 'TAM_UNG':
        return payroll.decide_advance(session, user, ident, version, approve, reason)
    if kind in ('BO_SUNG', 'NGHI'):
        return attendance.approve(session, user, ident, version, reason) if approve else attendance.reject(session, user, ident, version, reason)
    if kind == 'SUA_PHIEU':
        return cash_corrections.approve(session, user, ident, version) if approve else cash_corrections.reject(session, user, ident, reason, version)
    row = session.get(PendingDisposition, ident)
    if row is None or row.kind != ('HUY' if kind == 'HUY_HANG' else 'TRA_NCC'):
        raise BusinessError('NOT_FOUND', 'Không tìm thấy phiếu xử lý.', status=404)
    pending_dispositions._decider(session, user, ident)
    try:
        if approve:
            return pending_dispositions.approve(session, user, ident, version)
        return pending_dispositions.reject(session, user, ident, version, reason)
    except ValueError:
        raise BusinessError('VERSION_CONFLICT', 'Trang đã cũ, hãy tải lại.', status=409)


def age(since: datetime | None, now: datetime) -> str:
    if not since:
        return ''
    mins = int((now - since).total_seconds() // 60)
    if mins < 60:
        return f'chờ {max(mins, 1)} phút'
    if mins < 24 * 60:
        return f'chờ {mins // 60} giờ'
    return f'chờ {mins // (24 * 60)} ngày'


def summary(session, now: datetime | None = None) -> dict:
    """Ô trên Tổng quan: số việc, tiền ra, việc chờ lâu nhất (trong các dòng có giờ)."""
    now = now or datetime.now(TZ)
    items = rows(session)
    dated = [r for r in items if r['since']]
    oldest = min(dated, key=lambda r: r['since']) if dated else None
    return dict(n=len(items), cash=sum(r['amount'] or 0 for r in items if r['kind'] in CASH_OUT),
                oldest=oldest, oldest_age=age(oldest['since'], now) if oldest else '')

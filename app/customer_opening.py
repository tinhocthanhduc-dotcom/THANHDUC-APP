"""Nợ đầu kỳ khách hàng (TUẤN 02/11/2026): nợ cũ trước khi dùng app, gồm VAT.

Giám đốc nhập/sửa; kinh doanh hoặc kế toán Báo thu trừ dần (sales_receipts.report_for_opening);
số còn nợ đi chung finance_reports._debts() nên Công nợ khách, hạn mức, cảnh báo đều tính.
"""
from datetime import date

from sqlalchemy import select

from . import audit, sales_receipts
from .errors import BusinessError
from .models import Customer, CustomerOpeningDebt, SalesReceipt
from .security import has_role

MAX_AMOUNT = 9_223_372_036_854_775_807


def check_writer(user):
    if not has_role(user, 'director'):
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được nhập nợ đầu kỳ của khách.', status=403)


def get(session, customer_code):
    return session.scalar(select(CustomerOpeningDebt).where(CustomerOpeningDebt.customer_code == customer_code))


def snapshot(row):
    if row is None:
        return None
    return dict(id=row.id, number=row.number, customer_code=row.customer_code, amount=row.amount,
                as_of=row.as_of.isoformat(), note=row.note, version=row.version)


def set_opening(session, user, customer_code, amount, as_of, note='', version=0):
    """Tạo hoặc sửa nợ đầu kỳ. Số 0 = xóa, chỉ khi chưa có báo thu nào. Không cho hạ dưới số đã thu + chờ xác nhận."""
    check_writer(user)
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= MAX_AMOUNT:
        raise BusinessError('BAD_AMOUNT', 'Số nợ đầu kỳ phải là số nguyên đồng, không âm.')
    if not isinstance(as_of, date):
        raise BusinessError('BAD_DATE', 'Ngày chốt nợ chưa hợp lệ.')
    if as_of > sales_receipts._today():
        raise BusinessError('BAD_DATE', 'Ngày chốt nợ đầu kỳ không được sau hôm nay.')
    note = str(note or '').strip()[:4000]
    with sales_receipts._transaction(session):
        customer = session.scalar(select(Customer).where(Customer.code == customer_code).with_for_update())
        if customer is None:
            raise BusinessError('NOT_FOUND', 'Không tìm thấy khách hàng.', status=404)
        row = session.scalar(select(CustomerOpeningDebt).where(CustomerOpeningDebt.customer_code == customer_code)
                             .with_for_update().execution_options(populate_existing=True))
        current = 0 if row is None else row.version
        if version != current:
            raise BusinessError('VERSION_CONFLICT', 'Người khác vừa sửa nợ đầu kỳ của khách này, hãy tải lại.', {'current': current}, status=409)
        before = snapshot(row)
        if row is None:
            if amount == 0:
                raise BusinessError('BAD_AMOUNT', 'Nhập số nợ đầu kỳ lớn hơn 0.')
            row = CustomerOpeningDebt(number='NDK-' + customer_code, customer_code=customer_code,
                                      amount=amount, as_of=as_of, note=note, created_by=user.id)
            session.add(row)
            session.flush()
            audit.record(session, user, 'customer_opening_debt.set', 'customer_opening_debt', str(row.id), None, snapshot(row))
            return row
        used = sales_receipts.balance(row)
        if amount == 0:
            if session.scalar(select(SalesReceipt.id).where(SalesReceipt.opening_id == row.id).limit(1)) is not None:
                raise BusinessError('HAS_RECEIPTS', 'Khách đã có báo thu trừ nợ đầu kỳ, không xóa được. Chỉ sửa số tiền.', status=409)
            audit.record(session, user, 'customer_opening_debt.delete', 'customer_opening_debt', str(row.id), before, None)
            session.delete(row)
            return None
        floor = used['paid'] + used['pending']
        if amount < floor:
            raise BusinessError('BELOW_COLLECTED', 'Số nợ đầu kỳ không được nhỏ hơn số đã thu và đang chờ xác nhận.',
                                {'collected': floor}, status=409)
        row.amount, row.as_of, row.note = amount, as_of, note
        session.flush()
        audit.record(session, user, 'customer_opening_debt.set', 'customer_opening_debt', str(row.id), before, snapshot(row))
        return row

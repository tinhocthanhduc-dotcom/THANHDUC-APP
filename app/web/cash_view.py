"""Thu chi · Sổ quỹ (TUẤN 20/11/2026): mỗi phiếu nói rõ chi cho việc gì (danh mục), cho ai (khách, NCC, nhân viên)
và chứng từ gốc. Chỉ đọc, không đổi nghiệp vụ phiếu hay số dư quỹ."""
from sqlalchemy import select

from ..models import CashCategory, Customer, SalaryAdvance, SalesReceipt, SalesRefund, Supplier, SupplierPayment, User
from ..text import fold

WHO = {'khach': 'Khách', 'ncc': 'NCC', 'nv': 'Nhân viên', 'khac': 'Khác'}
NO_CATEGORY = '_chua'


def _map(session, model, key, codes, *cols):
    codes = {c for c in codes if c}
    if not codes:
        return {}
    return {row[0]: row[1:] for row in session.execute(select(key, *cols).where(key.in_(codes)))}


def describe(session, docs):
    """{doc.id: dict(cat, cat_dir, who, who_kind, src_label, src_href)} cho các phiếu truyền vào."""
    cats = {c.code: c for c in session.scalars(select(CashCategory))}
    customers = _map(session, Customer, Customer.code, (d.customer_code for d in docs), Customer.name)
    suppliers = _map(session, Supplier, Supplier.code, (d.supplier_code for d in docs), Supplier.name)
    staff = {fold(n) for n in session.scalars(select(User.full_name).where(User.full_name.is_not(None))) if n and n.strip()}
    ids = {}
    for d in docs:
        if d.source_type and d.source_id and d.source_id.isdigit():
            ids.setdefault(d.source_type, set()).add(int(d.source_id))
    numbers = {}
    for source, model in (('SALES_RECEIPT', SalesReceipt), ('SUPPLIER_PAYMENT', SupplierPayment),
                          ('SALARY_ADVANCE', SalaryAdvance), ('SALES_REFUND', SalesRefund)):
        if ids.get(source):
            numbers[source] = dict(session.execute(select(model.id, model.number).where(model.id.in_(ids[source]))).all())
    out = {}
    for d in docs:
        cat = cats.get(d.category_code) if d.category_code else None
        if d.customer_code:
            kind, name = 'khach', (customers.get(d.customer_code) or (d.partner_name or d.customer_code,))[0]
        elif d.supplier_code:
            kind, name = 'ncc', (suppliers.get(d.supplier_code) or (d.partner_name or d.supplier_code,))[0]
        elif d.source_type in ('PAYROLL_LINE', 'SALARY_ADVANCE') or (d.partner_name and fold(d.partner_name) in staff):
            kind, name = 'nv', d.partner_name or ''
        elif d.partner_name:
            kind, name = 'khac', d.partner_name
        else:
            kind, name = '', ''
        label = href = ''
        ident = int(d.source_id) if d.source_id and d.source_id.isdigit() else None
        number = numbers.get(d.source_type, {}).get(ident, '')
        if d.source_type == 'SALES_RECEIPT':
            label, href = f'Báo thu {number}'.strip(), f'/bao-thu/{ident}'
        elif d.source_type == 'SUPPLIER_PAYMENT':
            label, href = f'Trả NCC {number}'.strip(), f'/cong-no-ncc/{d.supplier_code}' if d.supplier_code else '/cong-no-ncc'
        elif d.source_type == 'SALARY_ADVANCE':
            label, href = f'Tạm ứng {number}'.strip(), '/tam-ung'
        elif d.source_type == 'PAYROLL_LINE':
            label, href = 'Bảng lương', '/luong/chi'
        elif d.source_type == 'SERVICE_EXPENSE':
            label, href = 'Chi phí phát sinh', '/chi-phi-tho'
        elif d.source_type == 'SALES_REFUND':
            label, href = f'Hoàn tiền {number}'.strip(), '/hoan-tien'
        out[d.id] = dict(cat=cat.name if cat else '', cat_dir=cat.direction if cat else '', who=name or '',
                         who_kind=kind, src_label=label, src_href=href)
    return out


def matches(info, danh_muc, doi_tuong):
    def ok(doc):
        i = info[doc.id]
        if danh_muc == NO_CATEGORY and doc.category_code and doc.kind in ('PT', 'PC'):
            return False
        if danh_muc and danh_muc != NO_CATEGORY and doc.category_code != danh_muc:
            return False
        return not doi_tuong or i['who_kind'] == doi_tuong
    return ok


def category_chips(docs, info, direction, link, current):
    """Tổng phiếu đã ghi theo danh mục (lớn đến nhỏ), bấm là lọc danh mục đó."""
    kind = 'PT' if direction == 'THU' else 'PC'
    sums = {}
    for d in docs:
        if d.status == 'DA_GHI' and d.kind == kind and not d.is_opening:
            key = d.category_code or NO_CATEGORY
            name = info[d.id]['cat'] or 'Chưa chọn danh mục'
            total, _ = sums.get(key, (0, name))
            sums[key] = (total + d.amount, name)
    rows = sorted(sums.items(), key=lambda kv: -kv[1][0])
    return [dict(code=code, name=name, amount=amount, on=current == code,
                 href=link(danh_muc='' if current == code else code, loai=kind)) for code, (amount, name) in rows]

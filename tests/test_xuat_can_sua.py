"""Lệnh xuất 3 bảng "cần sửa" (rà soát 17/11/2026): đúng dòng, đọc được bằng Excel cũ, không ghi gì vào database."""
from sqlalchemy import func, select

from app.models import AuditLog, Customer, CustomerPhone, Product, ServiceTicket
from scripts import xuat_can_sua


def rows(path):
    raw = path.read_bytes()
    assert raw[:2] == b'\xff\xfe'  # UTF-16 có BOM: Office 2010 mở thẳng
    return [line.split('\t') for line in raw.decode('utf-16').strip('\r\n').split('\r\n')]


def test_xuat_ba_bang_chi_doc(db, make_user, tmp_path, capsys):
    user = make_user('thanh', 'sales', 'reception')
    db.add_all([Customer(code='KTD-000001', kind='CONG_TY', name='Công ty  An Phát', tax_code='', address='12 Lê Lợi\nQ1'),
                Customer(code='KTD-000002', kind='CONG_TY', name='công ty an phát', tax_code='0301234567'),
                Customer(code='KTD-000003', kind='CA_NHAN', name='Khách Lẻ'),
                Customer(code='KTD-000004', kind='CA_NHAN', name='Khách Thử', is_test=True),
                Product(code='TD-0001', name='Máy in A', group_name='Máy in', sale_price=0),
                Product(code='TD-0002', name='Mực B', group_name='Mực', sale_price=100_000, active=False)])
    db.flush()
    db.add(CustomerPhone(customer_code='KTD-000001', phone='0901234567'))
    db.add(ServiceTicket(number='DV-26-00001', customer_code='KTD-000001', created_by=user.id, status='MOI', symptom='Kẹt giấy'))
    db.commit()
    before = db.scalar(select(func.count()).select_from(AuditLog))
    seen = db.scalar(select(func.max(Customer.updated_at)))

    xuat_can_sua.main(['--thu-muc', str(tmp_path)])

    dup = rows(tmp_path / '1-khach-trung-ten.csv')
    assert dup[0][:4] == ['Nhóm', 'Mã khách', 'Loại', 'Tên'] and len(dup) == 3
    by_code = {r[1]: r for r in dup[1:]}
    # Khách có phiếu được gợi ý giữ; xuống dòng trong địa chỉ không làm lệch cột.
    assert by_code['KTD-000001'][9] == 'giữ' and by_code['KTD-000001'][6] == '12 Lê Lợi Q1' and by_code['KTD-000001'][8] == '1'
    assert by_code['KTD-000002'][9] == 'gộp vào KTD-000001'
    lack = rows(tmp_path / '2-khach-dang-giao-dich-thieu.csv')
    assert [r[0] for r in lack[1:]] == ['KTD-000001'] and lack[1][8] == 'MST'
    goods = rows(tmp_path / '3-hang-hoa-can-sua.csv')
    assert [r[0] for r in goods[1:]] == ['TD-0001']
    assert goods[1][5] == 'Chưa có giá bán; Máy in chưa bật serial; Chưa từng nhập kho'
    out = capsys.readouterr().out
    assert '1 nhóm, 2 khách' in out and 'An Phát' not in out and '0901234567' not in out
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before
    assert db.scalar(select(func.max(Customer.updated_at))) == seen

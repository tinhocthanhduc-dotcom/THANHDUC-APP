"""Lệnh in số liệu vận hành (rà soát 17/11/2026): chạy được trên dữ liệu thật, chỉ đọc, không in thông tin khách."""
from sqlalchemy import func, select

from app.models import AuditLog, Customer, CustomerPhone, Product
from scripts import so_lieu_van_hanh


def test_in_so_lieu_chi_doc(db, make_user, capsys):
    make_user('thanh', 'sales', 'finance')
    make_user('tuan', 'director')
    db.add_all([Customer(code='KH1', kind='CONG_TY', name='Công ty Bí Mật', tax_code=''),
                Customer(code='KH2', kind='CA_NHAN', name='Khách Lẻ'),
                Product(code='TD-0001', name='Máy in A', group_name='Máy in', sale_price=0)])
    db.flush()
    db.add(CustomerPhone(customer_code='KH2', phone='0901234567'))
    db.commit()
    before = db.scalar(select(func.count()).select_from(AuditLog))
    so_lieu_van_hanh.main(['--ngay', '14'])
    out = capsys.readouterr().out
    assert 'SỐ LIỆU VẬN HÀNH' in out and 'Thanh | sales,finance' in out
    assert 'công ty chưa có MST: 1' in out
    assert 'Chưa có SĐT: 1' in out
    assert 'Chưa có giá bán: 1' in out
    assert 'Nhóm "Máy in" chưa bật theo dõi serial: 1' in out
    assert 'Màn dùng nhiều nhất' in out
    # Không in tên hay SĐT khách, không ghi gì thêm.
    assert 'Bí Mật' not in out and '0901234567' not in out
    assert db.scalar(select(func.count()).select_from(AuditLog)) == before

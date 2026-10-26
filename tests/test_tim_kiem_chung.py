"""Luật tìm chung (app/search.py): không dấu, nhiều chữ đảo thứ tự, viết tắt, SĐT một phần, xếp hạng."""
from datetime import datetime, timedelta, timezone

from app import customers, search
from app.models import Customer, CustomerPhone, Supplier
from app.text import fold


def test_text_matches_words_in_any_order_without_accents():
    assert search.text_matches('hung anh', 'Anh Hùng · 0903112233')
    assert search.text_matches('cty thanh', 'Công ty TNHH Thành Long')
    assert search.text_matches('', 'bất kỳ')
    assert not search.text_matches('hung chi', 'Anh Hùng')


def _customer(db, code, name, phone='', minutes=0):
    c = Customer(code=code, name=name, search_text=fold(f'{code} {name} {phone}'),
                 updated_at=datetime(2026, 10, 26, tzinfo=timezone.utc) - timedelta(minutes=minutes))
    if phone:
        c.phones.append(CustomerPhone(phone=phone))
    db.add(c)
    return c


def test_customer_search_ranks_and_matches_parts(db):
    _customer(db, 'KTD-000123', 'Anh Hùng', '0903112233', minutes=30)
    _customer(db, 'KTD-000302', 'Chị Hùng Đức Hạnh', '0987654321', minutes=1)
    _customer(db, 'KTD-000045', 'Công ty TNHH Thành Long', '02838124455')
    _customer(db, 'KTD-000120', 'Văn phòng luật Anh Đức', '0903998112', minutes=5)
    db.commit()
    codes = lambda q: [c.code for c in customers.search(db, q)]
    # "hung anh": khách có cả hai từ đúng đầu từ lên trước; "Hạnh" chỉ chứa "anh" ở giữa nên xếp sau.
    assert codes('hung anh') == ['KTD-000123', 'KTD-000302']
    assert codes('cty thanh') == ['KTD-000045']
    # SĐT gõ một phần, có dấu cách.
    assert codes('0903 112') == ['KTD-000123']
    assert set(codes('112')) == {'KTD-000123', 'KTD-000120'}
    # Mã trùng đúng lên đầu dù khách khác mới cập nhật hơn.
    assert codes('ktd-000120')[0] == 'KTD-000120'
    # "duc": tên có từ bắt đầu bằng "duc" trước; cùng hạng thì khách mới cập nhật lên trước.
    assert codes('duc') == ['KTD-000302', 'KTD-000120']


def test_supplier_list_search_uses_all_words(client, db, make_user):
    from test_web import service_login
    db.add_all([Supplier(code='NCC-0001', name='Công ty Mực in Sài Gòn', search_text=fold('NCC-0001 Công ty Mực in Sài Gòn')),
                Supplier(code='NCC-0002', name='Linh kiện Sài Gòn', search_text=fold('NCC-0002 Linh kiện Sài Gòn'))])
    db.commit()
    service_login(client, make_user('kho.tim', 'warehouse'))
    html = client.get('/nha-cung-cap?q=sai+gon+muc').text
    assert 'NCC-0001' in html and 'NCC-0002' not in html

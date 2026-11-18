"""Đợt 4b (TUẤN duyệt mẫu 18/11/2026): Nhận đơn hiện khung "Lần trước" + nút Dùng lại; máy lần trước xếp đầu, KHÔNG tự tick;
địa điểm hiện người liên hệ + Gọi."""
import json
import re
from html import unescape

from sqlalchemy import select

from app.models import CustomerSite, Equipment
from test_web import service_form, service_login, service_people  # noqa: F401


def _site(db):
    return db.scalar(select(CustomerSite).where(CustomerSite.customer_code == 'KTD-000001', CustomerSite.name == 'Văn phòng'))


def test_last_intake_card_fills_on_tap_only(client, db, service_people):
    p = service_people
    site = _site(db)
    site.contact_name, site.contact_phone = 'Chị Lan', '0909111222'
    db.add(Equipment(serial='ZZ999', product_code='TD-0001', customer_code='KTD-000001', search_text='zz999', created_by=p['dispatcher'].id))
    db.commit()
    csrf = service_login(client, p['dispatcher'])
    page = client.get('/dich-vu/moi?customer_code=KTD-000001').text
    assert 'data-sv-last-use' not in page  # chưa có phiếu nào
    r = client.post('/dich-vu/moi', data=service_form(csrf, kind='SUA_TAI_CHO', site_id=str(site.id), pick=['ZZ999'],
                                                      assignee_id=str(p['technician'].id), symptom='Kẹt giấy khay 2'), follow_redirects=False)
    assert r.status_code == 303, r.text
    for url in ('/dich-vu/moi?customer_code=KTD-000001', '/dich-vu/chon-khach?customer_code=KTD-000001'):
        page = client.get(url).text
        m = re.search(r'data-sv-last-use="([^"]+)"', page)
        assert m, url
        data = json.loads(unescape(m.group(1)))
        assert data['kind'] == 'SUA_TAI_CHO' and data['site_id'] == str(site.id) and data['serials'] == ['ZZ999']
        assert data['technician_id'] == str(p['technician'].id)
        assert 'Kẹt giấy khay 2' in page and 'Lần trước' in page
        # Máy lần trước xếp đầu, có nhãn, nhưng không tự tick (TUẤN 05/11).
        assert page.index('value="ZZ999"') < page.index('value="AB123"')
        assert not re.search(r'name="pick" value="ZZ999" checked', page)
        assert 'data-label="Văn phòng" data-contact="Chị Lan" data-phone="0909111222"' in page and 'data-sv-site-contact' in page


def test_ink_tickets_not_shown_in_last_card(client, db, service_people):
    p = service_people
    csrf = service_login(client, p['dispatcher'])
    r = client.post('/dich-vu/moi', data=service_form(csrf, kind='MUC_DINH_KY', ink_code=['TD-0001'], ink_qty=['1']), follow_redirects=False)
    assert r.status_code == 303, r.text[-600:]
    page = client.get('/dich-vu/moi?customer_code=KTD-000001').text
    assert 'data-sv-last-use' not in page  # Mực định kỳ có khung Dùng lại riêng

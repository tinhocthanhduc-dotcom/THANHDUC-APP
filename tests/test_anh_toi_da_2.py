"""Ô ảnh chuẩn (TUẤN 24/10): mỗi ô tối đa 2 ảnh; Tệp khác của phiếu dịch vụ không giới hạn."""
import re

import pytest

from app import attachments, equipment_custody as custody, service_workshop as svc
from app.errors import BusinessError
from test_mv1_workshop import onsite, held, people, purchase_people, intake, photo  # noqa: F401
from test_web import d5_image, service_login


def add(db, user, t, tag, entity='service_ticket'):
    return attachments.add(db, user, entity, str(t.id), tag, tag + '.png', d5_image())


def test_third_photo_in_same_slot_is_refused_until_one_is_hidden(db, onsite):
    p, a, b, t, eq = onsite
    first = add(db, p['technician'], t, 'SAU'); add(db, p['technician'], t, 'SAU')
    with pytest.raises(BusinessError) as e: add(db, p['technician'], t, 'SAU')
    assert e.value.code == 'PHOTO_LIMIT' and e.value.status == 409
    add(db, p['technician'], t, 'TRUOC')  # ô khác vẫn chụp được
    attachments.hide(db, p['technician'], first.id, 'Chụp lại', first.version)
    add(db, p['technician'], t, 'SAU')
    for _ in range(3): add(db, p['dispatcher'], t, 'KHAC')  # Tệp khác: giấy tờ, không giới hạn


def test_custody_photos_share_slot_with_ticket_photos(db, onsite):
    p, a, b, t, eq = onsite
    photo(db, p, t); add(db, p['technician'], t, 'NHAN_MAY')
    with pytest.raises(BusinessError) as e: photo(db, p, t)
    assert e.value.code == 'PHOTO_LIMIT'
    # Đã nhận máy về: lượt giữ máy mới tính lại từ đầu.
    svc.bring(db, p['technician'], t.id, t.version, intake(eq), [])
    db.expire_all(); photo(db, p, t, 'TRA_MAY'); photo(db, p, t, 'TRA_MAY')
    with pytest.raises(BusinessError): photo(db, p, t, 'TRA_MAY')


def test_technician_upload_returns_limit_and_page_hides_capture(db, onsite, client):
    p, a, b, t, eq = onsite
    csrf = service_login(client, p['technician'])
    fk = re.search(r'name="form_key" value="([^"]+)"', client.get(f'/tho/phieu/{t.id}').text).group(1)
    for i in range(3):
        r = client.post(f'/tho/phieu/{t.id}/anh', data=dict(csrf=csrf, form_key=fk, tag='SAU'), files={'file': ('s.png', d5_image(), 'image/png')})
        assert r.status_code == (201 if i < 2 else 409), r.text
        if i < 2: fk = r.json()['form_key']
    assert r.json()['code'] == 'PHOTO_LIMIT'
    page = client.get(f'/tho/phieu/{t.id}')
    slot = page.text.split('data-photo-tag="SAU"')[1].split('</section>')[0]
    assert 'hidden data-tech-capture' in slot and 'Đủ 2 ảnh' in slot and slot.count('data-saved-photo') == 2

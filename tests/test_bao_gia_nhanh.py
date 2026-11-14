"""Báo giá nhanh HG (TUẤN duyệt mẫu B1 14/11/2026, đợt 2 báo giá sửa chữa).

Điều kiện xong: chỉ hiện dịch vụ + cấp đã khai cho hiện tượng (không tự lấy C1–C3), giá gồm VAT lấy từ bảng giá,
model lạ / thiếu giá → Cần kỹ thuật xác nhận, không đoán; tận nơi cộng phụ phí; tin không có linh kiện; lưu ảnh chụp giá;
chuyển thành phiếu nhận máy / hẹn thợ điền sẵn, không nhập lại; kho và thợ không dùng được.
"""
import pytest
from sqlalchemy import select

from app import price_book as pb
from app import quick_quotes as qq
from app.errors import BusinessError
from app.models import AuditLog, QuickQuote, RepairGroup, ServiceTicket
from test_tk_tai_khoan import web_login
from test_web import service_form, service_login, service_people  # noqa: F401


def group(db, code):
    return db.scalar(select(RepairGroup).where(RepairGroup.code == code))


def setup_book(db, boss):
    a4, a3 = group(db, 'LASER_A4_DON'), group(db, 'LASER_A3')
    fuser = pb.save_service(db, boss, dict(kind='GOI', name='Sửa chữa cụm sấy', active='1', option_rows='0',
                                           **{f'g{a4.id}_p1': '250.000', f'g{a4.id}_p2': '450.000', f'g{a4.id}_p3': '650.000',
                                              f'g{a3.id}_p3': '1.300.000'}))
    feed = pb.save_service(db, boss, dict(kind='GOI', name='Sửa chữa bộ kéo giấy', active='1', option_rows='0',
                                          **{f'g{a4.id}_p1': '150.000', f'g{a4.id}_p2': '250.000', f'g{a4.id}_p3': '400.000'}))
    pb.save_service(db, boss, dict(kind='PHI', name='Phí kiểm tra máy', price='100.000', description='Miễn phí nếu khách đồng ý sửa.', active='1'))
    pb.save_service(db, boss, dict(kind='PHI', name='Phụ phí sửa tận nơi nội thành', price='50.000', active='1'))
    pb.save_service(db, boss, dict(kind='PHI', name='Cài đặt máy in, driver', price='100.000', active='1'))
    pb.save_model(db, boss, 'Canon LBP 2900', a4.id)
    pb.save_model(db, boss, 'Canon LBP 3500', a3.id)
    pb.save_model(db, boss, 'Epson L3110', group(db, 'PHUN_BINH').id)
    pb.save_symptom(db, boss, dict(name='Kẹt giấy', service_id=str(feed.id), level_min='1', level_max='2'))
    pb.save_symptom(db, boss, dict(name='Kẹt giấy', service_id=str(fuser.id), level_min='2', level_max='2'))
    pb.save_symptom(db, boss, dict(name='In nhăn giấy', service_id=str(fuser.id), level_min='1', level_max='3'))
    pb.save_symptom(db, boss, dict(name='Máy in kim kêu', service_id=str(feed.id), group_id=str(group(db, 'KIM_NHIET').id),
                                   level_min='1', level_max='1'))
    return fuser, feed


def test_compute_uses_declared_levels_vat_and_never_guesses(db, make_user):
    boss = make_user('hg.boss', 'director')
    fuser, feed = setup_book(db, boss)
    r = qq.compute(db, 'canon lbp 2900', ['Kẹt giấy'], 'CUA_HANG')
    assert r['model_name'] == 'Canon LBP 2900' and r['group'].code == 'LASER_A4_DON'
    lines = {ln['name']: ln for ln in r['lines']}
    # Kéo giấy khai C1–C2: 150k–250k chưa VAT → 162.000–270.000; không lấy C3 400k.
    assert lines['Sửa chữa bộ kéo giấy']['levels'] == [1, 2]
    assert (lines['Sửa chữa bộ kéo giấy']['low'], lines['Sửa chữa bộ kéo giấy']['high']) == (162_000, 270_000)
    # Cụm sấy khai đúng C2: một giá 486.000, không lấy C1/C3.
    assert lines['Sửa chữa cụm sấy']['levels'] == [2] and lines['Sửa chữa cụm sấy']['low'] == lines['Sửa chữa cụm sấy']['high'] == 486_000
    assert qq.range_text(lines['Sửa chữa bộ kéo giấy']) == '162.000 – 270.000' and qq.levels_text(lines['Sửa chữa bộ kéo giấy']) == 'C1–C2'
    assert r['check_fees'][0]['price'] == 108_000 and r['trip_fees'] == []
    # Hiện tượng chỉ của nhóm in kim không hiện cho máy laser.
    assert 'Máy in kim kêu' not in qq.symptom_names(db, r['model']) and 'Kẹt giấy' in qq.symptom_names(db, r['model'])
    text = qq.zalo_text(r)
    assert text.startswith('Dạ, máy Canon LBP 2900 bị kẹt giấy thì chi phí tùy nguyên nhân')
    assert '• Sửa chữa bộ kéo giấy: 162.000 – 270.000đ' in text and '• Sửa chữa cụm sấy: khoảng 486.000đ' in text
    assert 'Phí kiểm tra máy 108.000đ, miễn phí nếu khách đồng ý sửa.' in text and 'chỉ sửa khi anh/chị đồng ý' in text
    # Tận nơi: cộng phụ phí có chữ "tận nơi", không lấy phí cài đặt.
    r = qq.compute(db, 'Canon LBP 2900', ['Kẹt giấy'], 'TAN_NOI')
    assert [f['name'] for f in r['trip_fees']] == ['Phụ phí sửa tận nơi nội thành'] and '• Phụ phí sửa tận nơi nội thành: 54.000đ' in qq.zalo_text(r)
    # A3 chỉ có cấp 3 cụm sấy: hiện tượng khai C1–C3 → chỉ báo giá cấp có giá.
    r = qq.compute(db, 'Canon LBP 3500', ['In nhăn giấy'], 'CUA_HANG')
    assert r['lines'][0]['levels'] == [3] and r['lines'][0]['low'] == 1_404_000
    # Nhóm phun không làm dịch vụ: Cần kỹ thuật xác nhận.
    r = qq.compute(db, 'Epson L3110', ['Kẹt giấy'], 'CUA_HANG')
    assert all(not ln['ok'] for ln in r['lines']) and qq.range_text(r['lines'][0]) == qq.VERIFY
    assert 'cần kỹ thuật kiểm tra máy mới báo được chi phí' in qq.zalo_text(r) and 'Giá tham khảo' not in qq.zalo_text(r)
    # Model lạ: không đoán giá.
    r = qq.compute(db, 'Brother HL lạ', ['Kẹt giấy'], 'CUA_HANG')
    assert r['unknown_model'] and r['lines'] and not any(ln['ok'] for ln in r['lines'])


def test_create_snapshot_permissions_and_status_flow(db, make_user):
    boss, sales, kho, tho = (make_user('hg.boss2', 'director'), make_user('hg.sale', 'sales'), make_user('hg.kho', 'warehouse'),
                             make_user('hg.tho', 'technician'))
    fuser, feed = setup_book(db, boss)
    for u in (kho, tho):
        with pytest.raises(BusinessError) as exc:
            qq.create(db, u, model_name='Canon LBP 2900', symptoms=['Kẹt giấy'], place='CUA_HANG')
        assert exc.value.code == 'FORBIDDEN'
    with pytest.raises(BusinessError) as exc:
        qq.create(db, sales, model_name='Canon LBP 2900', symptoms=[], place='CUA_HANG')
    assert exc.value.code == 'BAD_SYMPTOM'
    row = qq.create(db, sales, model_name='Canon LBP 2900', symptoms=['Kẹt giấy'], place='TAN_NOI', contact='Anh Nam 0909')
    assert row.number.startswith('HG-26-') and row.status == 'MOI' and row.model_id
    assert [ln['name'] for ln in row.lines if not ln.get('fee')] == ['Sửa chữa bộ kéo giấy', 'Sửa chữa cụm sấy']
    # Bảng giá đổi sau: báo giá đã lập giữ nguyên giá.
    a4 = group(db, 'LASER_A4_DON')
    pb.save_service(db, boss, dict(id=str(feed.id), version=str(feed.version), kind='GOI', name='Sửa chữa bộ kéo giấy', active='1',
                                   option_rows='0', **{f'g{a4.id}_p1': '200.000', f'g{a4.id}_p2': '300.000'}))
    db.expire_all()
    assert db.get(QuickQuote, row.id).lines[0]['low'] == 162_000
    assert qq.mark_sent(db, sales, row.id).status == 'DA_GUI'
    assert qq.decline(db, sales, row.id, 'chê đắt').status == 'KHONG_SUA'
    with pytest.raises(BusinessError) as exc:
        qq.mark_sent(db, sales, row.id)
    assert exc.value.code == 'BAD_STATUS'
    assert qq.reopen(db, sales, row.id).status == 'DA_GUI'
    with pytest.raises(BusinessError) as exc:
        qq.send_bot(db, sales, row.id)
    assert exc.value.code == 'NOT_LINKED'
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.entity == 'quick_quote')))
    assert {'quick_quote.create', 'quick_quote.send', 'quick_quote.decline', 'quick_quote.reopen'} <= actions


def test_web_compose_save_and_convert_to_onsite_ticket(client, db, service_people, make_user):  # noqa: F811
    p = service_people
    setup_book(db, p['director'])
    csrf = service_login(client, p['dispatcher'])
    page = client.get('/hoi-gia/moi?model=Canon+LBP+2900&ht=K%E1%BA%B9t+gi%E1%BA%A5y').text
    assert 'Laser đen trắng A4 đơn năng' in page and '162.000 – 270.000' in page and 'Chuyển thành phiếu nhận máy' in page
    assert 'Hẹn thợ tới kiểm tra' in page and 'Bảo hành' not in page
    part = client.get('/hoi-gia/xem-truoc?model=Epson+L3110&ht=K%E1%BA%B9t+gi%E1%BA%A5y').text
    assert 'Cần kỹ thuật xác nhận' in part and '<html' not in part
    assert 'Minh Phát' in client.get('/hoi-gia/tim-khach?q=minh').text
    bad = client.post('/hoi-gia/luu', data=dict(csrf=csrf, model='Canon LBP 2900', noi='CUA_HANG'))
    assert bad.status_code == 400 and 'Chọn ít nhất một hiện tượng' in bad.text
    r = client.post('/hoi-gia/luu', data=dict(csrf=csrf, model='Canon LBP 2900', ht='Kẹt giấy', noi='CUA_HANG', khach='KTD-000001',
                                             next='hen', text='sửa tay', text_edited=''), follow_redirects=False)
    row = db.scalars(select(QuickQuote)).one()
    assert r.status_code == 303 and r.headers['location'] == f'/dich-vu/moi?hg={row.id}'
    assert row.customer_code == 'KTD-000001' and 'sửa tay' not in row.text and 'Sửa chữa bộ kéo giấy' in row.text
    form = client.get(r.headers['location']).text
    assert f'name="hg" value="{row.id}"' in form and 'Canon LBP 2900: Kẹt giấy. (Báo giá nhanh ' in form and 'Từ báo giá nhanh' in form
    ok = client.post('/dich-vu/moi', data=service_form(csrf, hg=str(row.id), symptom=qq.ticket_symptom(row)), follow_redirects=False)
    assert ok.status_code == 303
    t = db.scalars(select(ServiceTicket)).one()
    db.expire_all()
    row = db.get(QuickQuote, row.id)
    assert row.status == 'CHUYEN_PHIEU' and row.ticket_id == t.id and t.symptom.endswith(f'(Báo giá nhanh {row.number})')
    detail = client.get(f'/hoi-gia/{row.id}').text
    assert t.number in detail and 'Đã chuyển phiếu' in detail
    assert client.get(f'/dich-vu/moi?hg={row.id}').status_code == 409
    listing = client.get('/hoi-gia?view=chuyen').text
    assert row.number in listing and t.number in listing


def test_web_edited_text_copy_and_roles(client, db, service_people, make_user):  # noqa: F811
    p = service_people
    setup_book(db, p['director'])
    csrf = web_login(client, p['sales'])
    r = client.post('/hoi-gia/luu', data=dict(csrf=csrf, model='Canon LBP 2900', ht='Kẹt giấy', noi='TAN_NOI', next='copy',
                                             text='Dạ em báo giá sửa tay cho anh ạ', text_edited='1'), follow_redirects=False)
    row = db.scalars(select(QuickQuote)).one()
    assert r.status_code == 303 and row.status == 'DA_GUI' and row.sent_via == 'CA_NHAN' and row.text == 'Dạ em báo giá sửa tay cho anh ạ'
    page = client.get(f'/hoi-gia/moi').text
    # Kinh doanh chỉ nhận Mực định kỳ: có Chuyển thành phiếu nhận máy, không có Hẹn thợ.
    assert 'Chuyển thành phiếu nhận máy' in page and 'Hẹn thợ tới kiểm tra' not in page
    assert client.get(f'/dich-vu/moi?hg={row.id}').status_code == 403
    r = client.post(f'/hoi-gia/{row.id}/nhan', data=dict(csrf=csrf, text=row.text), follow_redirects=False)
    assert r.headers['location'] == f'/quay-sua/nhan?hg={row.id}'
    counter = client.get(r.headers['location']).text
    assert f'name="hg" value="{row.id}"' in counter and 'Canon LBP 2900: Kẹt giấy.' in counter
    assert row.number in client.get('/hoi-gia').text
    for role in ('warehouse', 'technician'):
        web_login(client, p[role])
        assert client.get('/hoi-gia', follow_redirects=False).status_code == 403

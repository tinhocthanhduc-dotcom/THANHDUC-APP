"""Báo giá gửi khách bản chuyên nghiệp có bảo hành (TUẤN duyệt mẫu Hc9Cnfxxq5MJh2fFps8xfN 20/11/2026).

Điều kiện xong: mỗi dòng có số tháng bảo hành (gói theo bảng giá, vật tư theo mã hàng, phí ghi tay 1 tháng); người gửi chọn
thời gian sửa dự kiến 1/2/3 ngày hoặc 1 tuần; 5 điều kiện bảo hành chuẩn, chỉ giám đốc sửa; trang khách mở link và bản In A4
hiện thông tin công ty (không logo, TUẤN 20/11), MST, bảng hạng mục có bảo hành, bằng chữ, điều kiện, nút Zalo/Gọi/In; trang khách không lộ điện thoại khách.
"""
from sqlalchemy import select

from app import repair_inspection as ri
from app import repair_quotes as rq
from app.models import Customer, Product, RepairQuoteVersion
from test_in_bao_gia_phieu import lines
from test_kiem_tra_may import book, form
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_xg1_workshop import holding, workshop  # noqa: F401
from test_web import service_login


def ready(db, p, t, make_user):
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    db.get(Product, 'TD-0001').warranty_months = 6
    db.commit()
    t = lines(db, p, t)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    ri.apply(db, p['dispatcher'], t.id, t.version, {})
    db.refresh(t)
    return boss, t


def test_line_warranty_lead_time_and_pages(client, db, workshop, make_user):
    p, a, b, t, eq = workshop
    boss, t = ready(db, p, t, make_user)
    csrf = service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'name="du_kien"' in page and '1 tuần' in page
    r = client.post(f'/kiem-tra-may/{t.id}/gui', data={'csrf': csrf, 'version': str(t.version), 'du_kien': '2'}, follow_redirects=False)
    assert r.status_code == 303
    v = db.scalar(select(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == t.id))
    s = v.snapshot
    months = {ln['name']: ln[ln['pick']]['warranty'] for ln in s['lines']}
    assert months['Phí kiểm tra'] == rq.FEE_WARRANTY == 1 and months['Rulo ép'] == 6 and 3 in months.values()
    assert s['chosen']['warranty'] == 6 and s['chosen']['total'] == 648000
    assert s['days'] == 2 and s['terms'] == list(rq.DEFAULT_TERMS)
    assert 'Sửa xong dự kiến 2 ngày' in v.text and 'Rulo ép: 50.000đ' in v.text
    # Trang khách: thông tin công ty không logo, MST, bảo hành từng dòng, bằng chữ, thời gian, điều kiện, Zalo/Gọi/In; không lộ điện thoại khách.
    client.cookies.clear()
    pub = client.get(f'/bg/{v.token}').text
    assert 'logo-thanh-duc' not in pub and 'Mã số thuế: <b>0313015734</b>' in pub and 'mailto:info@thanhduc.vn' in pub and '>Máy in Máy in' not in pub
    assert pub.count('Thời gian bảo hành:') == 1 and 'Thời gian bảo hành: <b>1 đến 6 tháng tùy hạng mục, kể từ ngày trả máy</b>' in pub
    assert '648.000' in pub and 'Bằng chữ: Sáu trăm bốn mươi tám nghìn đồng' in pub
    assert '>Bảo hành</th>' not in pub
    assert 'Sửa xong dự kiến 2 ngày' in pub and 'Điều kiện bảo hành' in pub and rq.DEFAULT_TERMS[2] in pub
    assert 'https://zalo.me/0888304668' in pub and 'tel:0888304668' in pub and 'window.print()' in pub
    customer = db.get(Customer, t.customer_code)
    for ph in filter(None, [getattr(customer, 'phone', None), getattr(customer, 'address', None)]):
        assert ph not in pub
    # Bản In A4 cùng dữ liệu: cột Bảo hành, SL, đơn giá, điều kiện, thời gian sửa.
    service_login(client, p['dispatcher'])
    pr = client.get(f'/kiem-tra-may/bao-gia/{v.id}/in').text
    assert rq.DEFAULT_TERMS[0] in pr and 'Sửa xong dự kiến 2 ngày' in pr
    assert pr.count('Thời gian bảo hành:') == 1 and '<b>Thời gian bảo hành:</b> 1 đến 6 tháng tùy hạng mục' in pr and '>Bảo hành</th>' not in pr


def test_lead_time_optional_and_invalid_ignored(client, db, workshop, make_user):
    p, a, b, t, eq = workshop
    boss, t = ready(db, p, t, make_user)
    csrf = service_login(client, p['dispatcher'])
    client.post(f'/kiem-tra-may/{t.id}/gui', data={'csrf': csrf, 'version': str(t.version), 'du_kien': '5'}, follow_redirects=False)
    v = db.scalar(select(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == t.id))
    assert v.snapshot['days'] is None and 'Sửa xong dự kiến' not in v.text
    client.cookies.clear()
    assert 'Sửa xong dự kiến' not in client.get(f'/bg/{v.token}').text


def test_only_director_edits_terms(client, db, workshop, make_user):
    p, a, b, t, eq = workshop
    boss, t = ready(db, p, t, make_user)
    csrf = service_login(client, p['dispatcher'])
    assert client.get('/bang-gia/dieu-kien-bao-hanh').status_code == 403
    r = client.post('/bang-gia/dieu-kien-bao-hanh', data={'csrf': csrf, 'text': 'Bảo hành tùy ý'})
    assert r.status_code == 403 and rq.terms(db) == list(rq.DEFAULT_TERMS)
    csrf = service_login(client, boss)
    assert 'Điều kiện bảo hành' in client.get('/bang-gia?tab=goi').text
    page = client.get('/bang-gia/dieu-kien-bao-hanh').text
    assert rq.DEFAULT_TERMS[0] in page
    r = client.post('/bang-gia/dieu-kien-bao-hanh', data={'csrf': csrf, 'text': '  Bảo hành 3 tháng tại cửa hàng.\n\nĐổi mới trong 7 ngày nếu lỗi linh kiện.  '},
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    assert rq.terms(db) == ['Bảo hành 3 tháng tại cửa hàng.', 'Đổi mới trong 7 ngày nếu lỗi linh kiện.']
    v = rq.create(db, p['dispatcher'], t.id, t.version)
    assert v.snapshot['terms'] == rq.terms(db)
    client.cookies.clear()
    pub = client.get(f'/bg/{v.token}').text
    assert 'Đổi mới trong 7 ngày nếu lỗi linh kiện.' in pub and rq.DEFAULT_TERMS[2] not in pub
    # Để trống: quay về 5 dòng chuẩn.
    rq.save_terms(db, boss, '  \n ')
    assert rq.terms(db) == list(rq.DEFAULT_TERMS)



def test_warranty_text():
    one = lambda m: dict(pick='PA1', PA1=dict(price=1, vat=0, warranty=m))
    assert rq.warranty_text(dict(lines=[one(1), one(1)])) == '1 tháng kể từ ngày trả máy'
    assert rq.warranty_text(dict(lines=[one(0)])) == 'Không bảo hành'
    assert rq.warranty_text(dict(lines=[one(None)])) == ''  # báo giá cũ chưa ghi số tháng
    assert rq.warranty_text(dict(lines=[one(1), one(0), one(6)])) == 'tối đa 6 tháng tùy hạng mục, kể từ ngày trả máy'

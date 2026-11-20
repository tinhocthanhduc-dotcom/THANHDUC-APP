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
    assert months['Phí vệ sinh máy'] == rq.FEE_WARRANTY == 1 and months['Rulo ép'] == 6 and 3 in months.values()
    assert s['chosen']['warranty'] == 6 and s['chosen']['total'] == 648000
    assert s['days'] == 2 and s['terms'] == list(rq.DEFAULT_TERMS)
    assert 'Sửa xong dự kiến 2 ngày' in v.text and 'Rulo ép: 50.000đ' in v.text
    # Trang khách: thông tin công ty không logo, MST, bảo hành từng dòng, bằng chữ, thời gian, điều kiện, Zalo/Gọi/In; không lộ điện thoại khách.
    client.cookies.clear()
    pub = client.get(f'/bg/{v.token}').text
    assert 'logo-thanh-duc' not in pub and 'Mã số thuế: <b>0313015734</b>' in pub and 'mailto:info@thanhduc.vn' in pub and '>Máy in Máy in' not in pub
    assert pub.count('Thời gian bảo hành:') == 1 and 'Thời gian bảo hành: <b>3 tháng kể từ ngày trả máy</b>' in pub
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
    assert pr.count('Thời gian bảo hành:') == 1 and '<b>Thời gian bảo hành:</b> 3 tháng kể từ ngày trả máy' in pr and '>Bảo hành</th>' not in pr


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
    one = lambda m, price=1, extra=False: dict(pick='PA1', PA1=dict(price=price, vat=0, warranty=m), extra=extra)
    # Lấy số tháng của gói chính (gói giá cao nhất), không lấy vật tư/phí khác.
    assert rq.warranty_text(dict(lines=[one(3, 450000), one(1, 100000, True), one(6, 900000, True)])) == '3 tháng kể từ ngày trả máy'
    assert rq.warranty_text(dict(lines=[one(3, 100000), one(6, 500000)])) == '6 tháng kể từ ngày trả máy'
    assert rq.warranty_text(dict(lines=[one(1, 1100000, True)])) == '1 tháng kể từ ngày trả máy'  # chỉ có phí ghi tay
    assert rq.warranty_text(dict(lines=[one(0)])) == 'Không bảo hành'
    assert rq.warranty_text(dict(lines=[one(None)])) == ''  # báo giá cũ chưa ghi số tháng


def fee_ticket(db, p, t, make_user):
    """Gói sửa đã duyệt (450.000 + VAT) + dòng "Phí kiểm tra máy" 100.000 + VAT ghi tay trên phiếu."""
    from app import service_charges
    from app.models import ServiceTicket
    from app.schemas import ServiceCharges
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    ri.apply(db, p['dispatcher'], t.id, t.version, {})
    t = db.get(ServiceTicket, t.id)
    db.refresh(t)
    keep = [service_charges.line_input(x) for x in t.lines]
    service_charges.set_charges(db, p['dispatcher'], t.id, ServiceCharges(version=t.version, billing='THU_TIEN', lines=keep + [
        dict(kind='PHI', description='Phí kiểm tra máy', qty=1, unit_price=100000, vat_rate=8)]))
    db.refresh(t)
    return t


def test_inspection_fee_free_when_customer_agrees(client, db, workshop, make_user):
    """TUẤN chọn 20/11/2026: phí kiểm tra miễn khi khách đồng ý sửa (không cộng tổng), không sửa thì thu."""
    from test_gui_bao_gia_sua import tomorrow
    from app.models import ServiceTicket
    p, a, b, t, eq = workshop
    t = fee_ticket(db, p, t, make_user)
    pv = rq.preview(db, t, ri.inspection(db, t.id))
    assert pv['ready'] and pv['total'] == 486000 and pv['fee'] == dict(subtotal=100000, vat=8000, total=108000)
    v = rq.create(db, p['dispatcher'], t.id, t.version)
    s = v.snapshot
    assert s['chosen']['total'] == 486000 and s['fee']['total'] == 108000
    assert [ln['name'] for ln in s['lines'] if ln.get('free_if_agree')] == ['Phí kiểm tra máy']
    assert 'Phí kiểm tra máy: miễn phí khi anh/chị đồng ý sửa (không sửa: 108.000đ gồm VAT)' in v.text and 'Tổng cộng: 486.000đ' in v.text
    client.cookies.clear()
    pub = client.get(f'/bg/{v.token}').text
    assert 'Miễn phí khi anh/chị đồng ý sửa' in pub and 'Nếu không sửa: thu phí kiểm tra <b>108.000đ</b>' in pub and '486.000đ' in pub
    service_login(client, p['dispatcher'])
    pr = client.get(f'/kiem-tra-may/bao-gia/{v.id}/in').text
    assert 'Nếu khách không sửa:</b> thu phí kiểm tra 108.000 đồng' in pr and '486.000 đồng' in pr
    # Phiếu chưa đổi: không phải lập lần mới.
    db.refresh(t)
    assert rq.open_version(db, t.id).id == v.id
    # Khách đồng ý: dòng phí kiểm tra về 0đ, tiền phiếu đúng tổng khách đã đồng ý.
    rq.agree(db, p['dispatcher'], v.id, t.version, 'PA1', 'ZALO', 'Chị Lan', tomorrow())
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    fee = [x for x in t.lines if x.description == 'Phí kiểm tra máy']
    assert len(fee) == 1 and fee[0].amount == fee[0].vat_amount == 0
    assert sum(x.amount + x.vat_amount for x in t.lines) == 486000 and t.quote_approved_at


def test_inspection_fee_kept_when_customer_declines(db, workshop, make_user):
    from app.models import ServiceTicket
    p, a, b, t, eq = workshop
    t = fee_ticket(db, p, t, make_user)
    v = rq.create(db, p['dispatcher'], t.id, t.version)
    rq.not_yet(db, p['dispatcher'], v.id, rq.REASONS[0] if rq.REASONS[0] != 'Khác' else rq.REASONS[1])
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    fee = [x for x in t.lines if x.description == 'Phí kiểm tra máy']
    assert fee[0].amount + fee[0].vat_amount == 108000

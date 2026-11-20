"""In báo giá ngay trên phiếu dịch vụ + gộp 2 khung báo giá (TUẤN duyệt 20/11/2026, mẫu CvpYPGy4Y7zngUm8sk4Ssp).

Điều kiện xong: tổng gửi khách = gói sửa đã áp + mọi dòng có giá khác trên phiếu; gói chưa có giá thì vẫn in được phần
vật tư/phí đang có (không in dòng chưa có giá); tổng 0 đ thì chặn in/gửi; nút In báo giá trên đầu phiếu tự lưu lần mới
khi chưa có lần đúng, in lại lần cũ khi còn đúng; một khung Báo giá cho khách, không còn khung xưởng cũ.
"""
from sqlalchemy import func, select

from app import repair_inspection as ri
from app import repair_quotes as rq
from app import service_charges
from app.models import RepairQuoteVersion, ServiceTicket
from app.schemas import ServiceCharges
from test_gui_bao_gia_sua import err, tomorrow
from test_kiem_tra_may import book, form
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_xg1_workshop import holding, workshop  # noqa: F401
from test_web import service_login


def lines(db, p, t):
    """Như phiếu DV-26-00131: một dòng phí ghi tay + một dòng vật tư có giá (100.000 + 50.000, VAT 8% → 162.000)."""
    t = db.get(ServiceTicket, t.id)
    keep = [service_charges.line_input(x) for x in t.lines]
    service_charges.set_charges(db, p['dispatcher'], t.id, ServiceCharges(version=t.version, billing='THU_TIEN', lines=keep + [
        dict(kind='PHI', description='Phí kiểm tra', qty=1, unit_price=100000, vat_rate=8),
        dict(kind='VAT_TU', description='Rulo ép', product_code='TD-0001', warehouse_code='K58', qty=1, unit_price=50000, vat_rate=8)]))
    db.refresh(t)
    return t


def unknown_model(db, p, make_user):
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    t = p['t']
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy'], model='Canon LBP 121dn'))
    db.refresh(t)
    return t


def test_missing_package_prints_ticket_lines_only(client, db, workshop, make_user):
    p, a, b, t, eq = workshop
    t = unknown_model(db, dict(p, t=t), make_user)
    row = ri.inspection(db, t.id)
    pv = rq.preview(db, t, row)
    assert (pv['ready'], pv['total']) == (False, 0) and '0 đ' in pv['reason']
    err('NO_PRICE', rq.create, db, p['dispatcher'], t.id, t.version)
    db.rollback()
    csrf = service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'data-rq-print-head' in page and 'disabled title="Báo giá đang 0 đ' in page
    r = client.post(f'/kiem-tra-may/{t.id}/in-bao-gia', data={'csrf': csrf, 'version': str(t.version)}, follow_redirects=False)
    assert r.status_code == 409 and 'Báo giá đang 0 đ' in r.text
    # Thêm vật tư có giá: in được phần đó, dòng gói chưa có giá không in ra.
    t = lines(db, p, t)
    pv = rq.preview(db, t, ri.inspection(db, t.id))
    assert pv['ready'] and pv['total'] == 162000 and pv['missing'] and len(pv['extras']) == 2
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'Báo giá cho khách' in page and 'Tổng gửi khách' in page and 'data-rq-missing' in page and 'Rulo ép' in page
    assert 'data-xg-panel' not in page and page.count('id="workshop-approve"') == 1 and 'Khách trả lời' in page
    assert 'disabled title=' not in page
    r = client.post(f'/kiem-tra-may/{t.id}/in-bao-gia', data={'csrf': csrf, 'version': str(t.version)}, follow_redirects=False)
    v = db.scalar(select(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == t.id))
    assert r.status_code == 303 and r.headers['location'] == f'/kiem-tra-may/bao-gia/{v.id}/in'
    assert v.snapshot['chosen']['total'] == 162000 and v.snapshot['missing'] and all(ln['extra'] for ln in v.snapshot['lines'])
    pr = client.get(r.headers['location']).text
    assert '162.000' in pr and 'Rulo ép' in pr and 'Sửa chữa cụm sấy' not in pr and 'TD-0001' not in pr
    # Bấm lại: in đúng lần đang chờ, không lập lần mới.
    r = client.post(f'/kiem-tra-may/{t.id}/in-bao-gia', data={'csrf': csrf, 'version': str(t.version)}, follow_redirects=False)
    assert r.headers['location'] == f'/kiem-tra-may/bao-gia/{v.id}/in'
    assert db.scalar(select(func.count()).select_from(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == t.id)) == 1
    # Khách đồng ý theo bản đã gửi: dùng nút xưởng như cũ.
    rq.agree(db, p['dispatcher'], v.id, t.version, 'PA1', 'ZALO', 'Chị Lan', tomorrow())
    db.expire_all()
    assert db.get(ServiceTicket, t.id).quote_approved_at and db.get(RepairQuoteVersion, v.id).status == 'DONG_Y'


def test_applied_package_plus_ticket_lines_and_alternative(db, workshop, make_user):
    p, a, b, t, eq = workshop
    boss = make_user('rq.boss', 'director')
    fuser, feed, opts = book(db, boss)
    t = lines(db, p, t)
    ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    ri.apply(db, p['dispatcher'], t.id, t.version, {})
    db.refresh(t)
    # Gói 450.000 + VAT = 486.000, cộng phí kiểm tra + rulo ép 162.000: tổng khớp tiền thật của phiếu.
    assert rq.preview(db, t, ri.inspection(db, t.id))['total'] == 648000 == sum(x.amount + x.vat_amount for x in t.lines)
    v = rq.create(db, p['dispatcher'], t.id, t.version)
    s = v.snapshot
    assert s['chosen']['total'] == 648000 and s['missing'] == [] and len(s['lines']) == 3
    assert s['alt_key'] == 'PA2' and s['alt']['total'] == 648000 + 162000
    assert 'Rulo ép: 50.000đ' in v.text and '648.000đ' in v.text
    # Khách chọn chính hãng: áp lại gói, dòng khác giữ nguyên, tổng đúng bản đã gửi.
    db.refresh(t)
    rq.agree(db, p['dispatcher'], v.id, t.version, 'PA2', 'ZALO', 'Chị Lan', tomorrow())
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert sum(x.amount + x.vat_amount for x in t.lines) == 810000 and t.quote_approved_at


def test_changed_ticket_lines_make_new_version_on_print(client, db, workshop, make_user):
    p, a, b, t, eq = workshop
    t = unknown_model(db, dict(p, t=t), make_user)
    t = lines(db, p, t)
    v1 = rq.create(db, p['dispatcher'], t.id, t.version)
    db.refresh(t)
    keep = [service_charges.line_input(x) for x in t.lines if x.description != 'Phí kiểm tra']
    service_charges.set_charges(db, p['dispatcher'], t.id, ServiceCharges(version=t.version, billing='THU_TIEN', lines=keep))
    db.refresh(t)
    csrf = service_login(client, p['dispatcher'])
    r = client.post(f'/kiem-tra-may/{t.id}/in-bao-gia', data={'csrf': csrf, 'version': str(t.version)}, follow_redirects=False)
    db.expire_all()
    v2 = db.scalar(select(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == t.id, RepairQuoteVersion.status == 'CHO'))
    assert r.status_code == 303 and v2.no == 2 and v2.snapshot['chosen']['total'] == 54000
    assert db.get(RepairQuoteVersion, v1.id).status == 'THAY_THE'

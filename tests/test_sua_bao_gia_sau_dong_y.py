"""In lại + sửa báo giá sau khi khách đã đồng ý (TUẤN duyệt 26/11/2026, phiếu DV-26-00164).

Điều kiện xong: sau Khách đồng ý vẫn có nút In báo giá trên đầu phiếu (in lần khách đồng ý, không lập lần mới);
danh sách Báo giá đã lập có nút In A4 từng dòng; "Sửa báo giá" trong Việc khác chỉ giám đốc/kinh doanh, bắt buộc lý do,
bỏ Khách đồng ý + về bước Báo giá, lần cũ giữ trong lịch sử, chỉ trước Sửa xong.
"""
import pytest
from sqlalchemy import func, select

from app import repair_inspection as ri
from app import repair_quotes as rq
from app import service_workshop
from app.errors import BusinessError
from app.models import RepairQuoteVersion, ServiceStep, ServiceTicket
from test_bang_xuong import _agreed
from test_gui_bao_gia_sua import tomorrow
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_web import service_login
from test_xg1_workshop import holding, repaired, workshop  # noqa: F401


def _count(db, t):
    return db.scalar(select(func.count()).select_from(RepairQuoteVersion).where(RepairQuoteVersion.ticket_id == t.id))


def test_print_again_and_reopen_quote(client, db, workshop, make_user):
    p, a, b, t, eq = workshop
    p = dict(p, workshop=workshop)
    tho = make_user('sbg.tho', 'technician')
    t, fuser, opts, v = _agreed(db, p, make_user, tho)
    assert t.quote_approved_at and t.assignee_id == tho.id

    # In lại: nút đầu phiếu trỏ đúng lần khách đồng ý, không lập lần mới; danh sách có In A4.
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'data-rq-print-again' in page and f'/kiem-tra-may/bao-gia/{v.id}/in' in page
    assert 'Sửa báo giá' in page and 'id="service-quote-reopen"' in page
    assert client.get(f'/kiem-tra-may/bao-gia/{v.id}/in').status_code == 200
    assert _count(db, t) == 1
    lst = client.get('/bao-gia-sua?view=xong').text
    assert 'data-print-a4' in lst

    # Kho chỉ điều phối (không kinh doanh) và thợ: không thấy, gọi thẳng bị 403.
    kho = make_user('sbg.kho', 'dispatcher', 'warehouse')
    assert not service_workshop.can_reopen_quote(kho, t) and not service_workshop.can_reopen_quote(tho, t)
    for u in (kho, tho):
        with pytest.raises(BusinessError) as e:
            service_workshop.reopen_quote(db, u, t.id, t.version, 'Khách đổi ý')
        assert e.value.status == 403
        db.rollback()
    service_login(client, kho)
    assert 'id="service-quote-reopen"' not in client.get(f'/dich-vu/{t.id}').text

    # Bắt buộc lý do.
    with pytest.raises(BusinessError) as e:
        service_workshop.reopen_quote(db, p['dispatcher'], t.id, t.version, '  ')
    assert e.value.code == 'REASON_REQUIRED'
    db.rollback()

    # Sửa báo giá qua web: bỏ Khách đồng ý, về bước Báo giá, giữ lần cũ, ghi bước.
    csrf = service_login(client, p['dispatcher'])
    db.refresh(t)
    r = client.post(f'/dich-vu/{t.id}/xuong/sua-bao-gia', data={'csrf': csrf, 'version': str(t.version), 'reason': 'Thêm thay rulo ép'},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == f'/dich-vu/{t.id}?sua_bao_gia=1'
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    row = ri.inspection(db, t.id)
    assert t.quote_approved_at is None and t.status == 'TAM_DUNG' and t.hold_reason == 'CHO_SUA_XUONG' and t.assignee_id == tho.id
    assert row.applied_at is None and row.recheck_reason.startswith('Sửa báo giá: Thêm thay rulo ép')
    assert db.get(RepairQuoteVersion, v.id).status == 'DONG_Y' and _count(db, t) == 1
    step = db.scalar(select(ServiceStep).where(ServiceStep.ticket_id == t.id).order_by(ServiceStep.id.desc()).limit(1))
    assert (step.action, step.note) == ('quote_reopen', 'Thêm thay rulo ép')
    page = client.get(r.headers['location']).text
    assert 'data-quote-reopened' in page and 'id="service-quote-reopen"' not in page
    # Thợ không báo sửa xong được khi đang chờ báo giá lại.
    assert not service_workshop.can_report(tho, t)

    # Lại các bước cũ: duyệt giá, lập lần 2, khách đồng ý lần mới.
    ri.apply(db, p['dispatcher'], t.id, t.version, {})
    db.refresh(t)
    v2 = rq.create(db, p['dispatcher'], t.id, t.version)
    assert v2.no == 2 and v2.id != v.id
    rq.mark_sent(db, p['dispatcher'], v2.id, 'COPY')
    db.refresh(t)
    rq.agree(db, p['dispatcher'], v2.id, t.version, 'PA1', 'ZALO', 'Anh Nam', tomorrow(), '', tho.id)
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.quote_approved_at and db.get(RepairQuoteVersion, v2.id).status == 'DONG_Y'
    assert f'/kiem-tra-may/bao-gia/{v2.id}/in' in client.get(f'/dich-vu/{t.id}').text

    # Sau Sửa xong: không còn Sửa báo giá.
    photo(db, p, t, 'SAU', who='dispatcher')
    repaired(db, p, t)
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert not service_workshop.can_reopen_quote(p['dispatcher'], t)
    with pytest.raises(BusinessError) as e:
        service_workshop.reopen_quote(db, p['dispatcher'], t.id, t.version, 'Muộn rồi')
    assert e.value.status == 409
    db.rollback()
    assert 'id="service-quote-reopen"' not in client.get(f'/dich-vu/{t.id}').text

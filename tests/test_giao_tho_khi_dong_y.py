"""Khách đồng ý ngay ở khung Khách trả lời (không có lần gửi đang chờ, vd sau Sửa báo giá) cũng giao thợ sửa (TUẤN 27/11/2026, DV-26-00160)."""
from app import repair_inspection as ri
from app.models import ServiceTicket
from test_bao_hanh_rieng_phieu import _setup
from test_gui_bao_gia_sua import tomorrow
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_web import service_login
from test_xg1_workshop import holding, workshop  # noqa: F401


def test_approve_without_open_version_assigns_repairer(client, db, workshop, make_user):
    p, t, fuser, opts = _setup(db, workshop, make_user)
    disp = p['dispatcher']
    ri.apply(db, disp, t.id, t.version, {})
    csrf = service_login(client, disp)
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'Giao thợ sửa' in page and 'name="repairer"' in page
    tech = p['technician']
    r = client.post(f'/dich-vu/{t.id}/xuong/dong-y', data={'csrf': csrf, 'version': str(t.version), 'promised_return_on': tomorrow().isoformat(),
                                                          'repairer': str(tech.id), 'note': 'Anh Trân đồng ý qua Zalo'}, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.quote_approved_at and t.assignee_id == tech.id

"""Thẻ B: báo cáo thợ bắt buộc (lỗi gặp, cách xử lý, ảnh biên bản), vị trí lúc đến khách, nháp trên máy chủ."""
import json

import pytest
from sqlalchemy import select

from app import attachments
from app.models import Attachment, ServiceDraft, ServiceTicket, ServiceVisit
from test_my_work import people, purchase_people, ticket, Page  # noqa: F401
from test_technician_stock import held, transfer, balance  # noqa: F401
from test_ux1b_hoan_thanh_gop import job, snapshot  # noqa: F401
from test_ux1e_gon_man_tho import data, post
from test_web import service_login


@pytest.mark.parametrize('changes,code', [
    (dict(fault_found='Kẹt giấy', fix_done=''), 'FIX_REQUIRED'),
    (dict(fix_done='x' * 2001), 'TEXT_TOO_LONG'),
    (dict(repair_result='', fix_extra='Thay drum'), 'FIX_REQUIRED'),
    (dict(repair_result='LAI', fix_extra='Thay drum'), 'FIX_REQUIRED'),
])
def test_report_result_required_rolls_back(client, db, job, changes, code):
    p, own, t = job
    form = data(client, p, t, **changes); before = snapshot(db, t); stock = balance(db, own.code)
    r = post(client, t, form)
    assert r.status_code == 400 and f'data-error-code="{code}"' in r.text and snapshot(db, t) == before and balance(db, own.code) == stock
    if 'fix_extra' in changes: assert 'value="Thay drum"' in r.text  # thợ không mất chữ đã ghi


def test_bien_ban_photo_no_longer_required(client, db, job):
    # TUẤN 22/10: chỉ cần ảnh nghiệm thu, bỏ ảnh biên bản có chữ ký khách.
    p, _, t = job
    for a in db.scalars(select(Attachment).where(Attachment.entity_id == str(t.id), Attachment.tag == 'BIEN_BAN')):
        attachments.hide(db, p['technician'], a.id, 'Chụp lại', a.version)
    form = data(client, p, t, fault_found='', fix_done='')
    html = client.get(f'/tho/phieu/{t.id}').text
    assert 'data-check="bienban"' not in html and 'data-photo-tag="BIEN_BAN"' not in html and 'Ghi chú hoàn thành' not in html
    r = post(client, t, form | dict(repair_result='MOT_PHAN', fault=['Kẹt giấy', 'Không có'], work=['Thay drum', 'Vệ sinh máy'], fix_extra='Hẹn thay trục lần sau'))
    assert r.status_code == 303
    db.expire_all(); t = db.get(ServiceTicket, t.id)
    assert t.status in ('HOAN_THANH', 'DA_DONG') and (t.fault_found, t.fix_done) == ('Kẹt giấy', 'Xong một phần: Thay drum, Vệ sinh máy, Hẹn thay trục lần sau')


def test_report_saved_and_shown_to_dispatcher(client, db, job):
    p, _, t = job
    service_login(client, p['technician']); html = client.get(f'/tho/phieu/{t.id}').text
    assert 'name="repair_result" value="XONG"' in html and 'name="fix_extra"' in html
    # TUẤN 27/10 (mẫu LoaiDvTho, thay quyết định 24/10): lỗi gặp và đã làm gộp một khung chip.
    assert 'name="fault"' in html and 'name="work"' in html and 'Lỗi · việc đã làm' in html
    assert 'Ảnh nghiệm thu' in html and html.index('id="bao-cao"') < html.index('id="completion-checks"')
    assert 'name="fault_found"' not in html and 'name="fix_done"' not in html
    assert post(client, t, data(client, p, t, fault_found='', fix_done='', repair_result='XONG', fault=['Kẹt giấy'], work=['Thay drum'])).status_code == 303
    db.expire_all(); t = db.get(ServiceTicket, t.id)
    assert t.status in ('HOAN_THANH', 'DA_DONG') and (t.fault_found, t.fix_done) == ('Kẹt giấy', 'Đã sửa xong: Thay drum')
    service_login(client, p['dispatcher']); page = client.get(f'/dich-vu/{t.id}').text
    assert 'data-fault-found>Kẹt giấy' in page and 'data-fix-done>Đã sửa xong: Thay drum' in page


def test_upload_bien_ban_tag(client, db, held):
    p, _, _ = held; t = ticket(db, p, 'DANG_LAM'); csrf = service_login(client, p['technician'])
    r = client.post(f'/tho/phieu/{t.id}/anh', data=dict(csrf=csrf, tag='BIEN_BAN'), files={'file': ('bb.png', __import__('test_web').d5_image(), 'image/png')})
    assert r.status_code == 201 and db.get(Attachment, r.json()['id']).tag == 'BIEN_BAN'
    # Nhãn lạ vẫn về ảnh sau sửa như cũ.
    r = client.post(f'/tho/phieu/{t.id}/anh', data=dict(csrf=csrf, tag='KHAC'), files={'file': ('x.png', __import__('test_web').d5_image(), 'image/png')})
    assert r.status_code == 201 and db.get(Attachment, r.json()['id']).tag == 'SAU'


def arrive(client, db, p, t, **gps):
    service_login(client, p['technician'])
    form = Page(client.get(f'/tho/phieu/{t.id}').text).forms['service-DANG_LAM']['fields']
    assert {'gps_lat', 'gps_lng', 'gps_accuracy'} <= set(form)
    return client.post(f'/dich-vu/{t.id}/transition', data=form | gps, follow_redirects=False)


def test_gps_recorded_on_arrival(client, db, held):
    p, _, _ = held; t = ticket(db, p, 'DA_HEN')
    r = arrive(client, db, p, t, gps_lat='10.7769421', gps_lng='106.7009', gps_accuracy='18.4')
    assert r.status_code == 303
    visit = db.scalar(select(ServiceVisit).where(ServiceVisit.ticket_id == t.id))
    db.refresh(visit)
    assert (str(visit.arrive_lat), str(visit.arrive_lng), visit.arrive_accuracy) == ('10.776942', '106.700900', 18)
    service_login(client, p['dispatcher']); assert 'data-visit-gps' in client.get(f'/dich-vu/{t.id}').text
    # Vị trí chỉ ghi một lần; sửa lại bằng SQL bị chặn.
    from sqlalchemy import text
    with pytest.raises(Exception, match='bat bien'):
        db.execute(text('update service_visits set arrive_lat=1, version=version+1 where id=:id'), dict(id=visit.id))
    db.rollback()


@pytest.mark.parametrize('gps', [dict(gps_lat='abc', gps_lng='106.7'), dict(gps_lat='91', gps_lng='106.7'),
                                 dict(gps_lat='10.7', gps_lng=''), dict(gps_lat='', gps_lng=''), dict(gps_lat='nan', gps_lng='1')])
def test_bad_or_missing_gps_never_blocks(client, db, held, gps):
    p, _, _ = held; t = ticket(db, p, 'DA_HEN')
    assert arrive(client, db, p, t, **gps).status_code == 303
    db.expire_all(); assert db.get(ServiceTicket, t.id).status == 'DANG_LAM'
    visit = db.scalar(select(ServiceVisit).where(ServiceVisit.ticket_id == t.id))
    assert visit.arrive_lat is None and visit.arrive_lng is None


def nhap(client, csrf, t, payload):
    return client.post(f'/tho/phieu/{t.id}/nhap', data=dict(csrf=csrf, data=json.dumps(payload)))


def test_draft_save_upsert_restore_and_cleared_on_complete(client, db, job):
    p, _, t = job; csrf = service_login(client, p['technician'])
    assert nhap(client, csrf, t, dict(fault_found='Kẹt', fix_done='')).json() == dict(ok=True)
    assert nhap(client, csrf, t, {'fault_found': 'Kẹt giấy', 'mat:TD-0001': '2', 'collection_mode': 'FULL'}).status_code == 200
    db.expire_all(); rows = list(db.scalars(select(ServiceDraft).where(ServiceDraft.ticket_id == t.id)))
    assert len(rows) == 1 and rows[0].data == {'fault_found': 'Kẹt giấy', 'mat:TD-0001': '2', 'collection_mode': 'FULL'}
    html = client.get(f'/tho/phieu/{t.id}').text
    draft = html.split("data-draft='", 1)[1].split("'", 1)[0]
    assert json.loads(draft.replace('&#34;', '"')) == rows[0].data
    assert post(client, t, data(client, p, t)).status_code == 303
    db.expire_all(); assert db.scalar(select(ServiceDraft).where(ServiceDraft.ticket_id == t.id)) is None
    # Phiếu đã đóng thì không lưu nháp nữa.
    assert nhap(client, csrf, t, dict(fault_found='x')).status_code in (403, 409)


def test_draft_forbidden_and_invalid(client, db, job):
    p, _, t = job
    csrf = service_login(client, p['other_tech'])
    assert nhap(client, csrf, t, dict(fault_found='x')).status_code == 403
    csrf = service_login(client, p['technician'])
    assert client.post(f'/tho/phieu/{t.id}/nhap', data=dict(csrf='sai', data='{}')).status_code == 403
    assert client.post(f'/tho/phieu/{t.id}/nhap', data=dict(csrf=csrf, data='không phải json')).json()['code'] == 'BAD_DRAFT'
    assert nhap(client, csrf, t, dict(fault_found=1)).json()['code'] == 'BAD_DRAFT'
    assert nhap(client, csrf, t, ['x']).json()['code'] == 'BAD_DRAFT'
    assert nhap(client, csrf, t, dict(note='x' * 17000)).json()['code'] == 'DRAFT_TOO_LARGE'
    db.expire_all(); assert db.scalar(select(ServiceDraft)) is None

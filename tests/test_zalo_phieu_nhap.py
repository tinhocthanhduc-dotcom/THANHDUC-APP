"""Phiếu dịch vụ / mực nháp từ Zalo (TUẤN duyệt mẫu 06/11/2026, Trợ lý Zalo đợt 2).

Bot chỉ ghi nháp; điều phối bấm Tạo phiếu (màn Nhận đơn điền sẵn, tự chọn máy/thợ) hoặc Bỏ nháp có lý do.
"""
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import zalo_phieu_nhap as pn
from app.models import AuditLog, Customer, ServiceTicket, ZaloTicketDraft, ZaloTroLy
from test_web import service_form, service_login
from test_zalo_tin_nhan import H, event
from test_zalo_tro_ly import FK, call, last_log, linked, say, texts, zalo  # noqa: F401 - fixture zalo


@pytest.fixture
def shop(db, make_user):
    users = {r: make_user('pn.' + r, r) for r in ('dispatcher', 'sales', 'technician', 'finance', 'director')}
    db.add_all([Customer(code='KTD-000001', name='Công ty An Phú', address='12 Nguyễn Văn Linh', search_text='ktd-000001 cong ty an phu'),
                Customer(code='KTD-000002', name='Đã ngừng', status='NGUNG', search_text='da ngung')])
    db.add(ZaloTroLy(id=1, enabled=True, draft_user_id=users['sales'].id))
    db.commit()
    return users


def tomorrow():
    return (pn._now().date() + timedelta(days=1)).isoformat()


def staff_draft(client, db, zalo, user, mid='m1', **args):
    linked(client, db, user=user)
    zalo['script'] = [call('tao_phieu_nhap', **{**dict(ma_khach='KTD-000001', loai='SUA_TAI_CHO', loi='Máy in kẹt giấy liên tục',
                                                      hen_ngay=tomorrow(), buoi='SANG', muc='GAP'), **args}), say('Đã lên phiếu nháp.')]
    client.post('/zalo/webhook', json=event(mid=mid, text='lên phiếu sửa máy An Phú kẹt giấy, sáng mai, gấp'), headers=H)
    db.expire_all()
    return db.scalars(select(ZaloTicketDraft).order_by(ZaloTicketDraft.id.desc())).first()


def test_nhan_vien_nho_bot_len_phieu_nhap(client, db, shop, zalo):
    row = staff_draft(client, db, zalo, shop['dispatcher'])
    assert 'tao_phieu_nhap' in zalo['ai'][0]['tools']
    assert (row.status, row.source, row.sender_id, row.customer_code, row.kind, row.priority, row.slot) == (
        'CHO', 'NHAN_VIEN', shop['dispatcher'].id, 'KTD-000001', 'SUA_TAI_CHO', 'GAP', 'SANG')
    assert row.want_date.isoformat() == tomorrow() and row.number.startswith('NZ-') and 'kẹt giấy' in row.question
    assert row.number in texts(zalo)[-1]
    log = last_log(db)
    assert (log.result, row.log_id) == ('PHIEU_NHAP', log.id)
    assert db.scalars(select(ServiceTicket)).all() == []  # nháp không phải phiếu thật
    assert db.scalars(select(AuditLog).where(AuditLog.action == 'zalo_draft.create')).one().user_id == shop['dispatcher'].id


def test_mot_tin_mot_nhap_va_bao_loi_khach_sai(client, db, shop, zalo):
    linked(client, db, user=shop['sales'])
    args = dict(loai='', loi='Máy báo lỗi giấy', hen_ngay='2020-01-01', buoi='SANG', muc='THUONG')
    zalo['script'] = [call('tao_phieu_nhap', ma_khach='KTD-000002', **args), call('tao_phieu_nhap', ma_khach='KTD-000001', **args),
                      call('tao_phieu_nhap', ma_khach='KTD-000001', **args), say('Xong')]
    client.post('/zalo/webhook', json=event(mid='m1', text='lên phiếu'), headers=H)
    outs = [json.loads(s['messages'][-1]['content']) for s in zalo['ai'][1:]]
    assert 'ngừng' in outs[0]['loi'] and 'da_tao' in outs[1] and 'rồi' in outs[2]['loi']
    row = db.scalars(select(ZaloTicketDraft)).one()
    assert (row.kind, row.want_date, row.slot) == (None, None, None)  # ngày đã qua bị bỏ, buổi đi theo ngày


def test_tho_va_tat_cau_hinh_thi_khong_co_cong_cu(client, db, shop, zalo):
    linked(client, db, user=shop['technician'])
    zalo['script'] = [say('Nhắn điều phối nhé.')]
    client.post('/zalo/webhook', json=event(mid='m1', text='lên phiếu sửa máy'), headers=H)
    assert 'tao_phieu_nhap' not in zalo['ai'][0]['tools']
    db.get(ZaloTroLy, 1).ticket_drafts = False
    db.commit()
    linked(client, db, chat='c2', user=shop['sales'])
    zalo['script'] = [say('Nhắn điều phối nhé.')]
    client.post('/zalo/webhook', json=event(mid='m2', text='lên phiếu sửa máy', chat='c2'), headers=H)
    assert 'tao_phieu_nhap' not in zalo['ai'][-1]['tools']


def test_khach_bao_giao_muc_thanh_phieu_nhap(client, db, shop, zalo):
    linked(client, db, customer='KTD-000001', name='Kế toán An Phú')
    zalo['script'] = [call('tao_phieu_nhap', loai='MUC_DINH_KY', loi='', hen_ngay='', buoi='', muc='THUONG'), say('ok')]
    client.post('/zalo/webhook', json=event(mid='m1', text='em ơi giao mực như mọi tháng nhé'), headers=H)
    assert 'tao_phieu_nhap' in zalo['ai'][0]['tools']
    row = db.scalars(select(ZaloTicketDraft)).one()
    assert (row.source, row.sender_id, row.customer_code, row.symptom) == ('KHACH', None, 'KTD-000001', 'Giao mực định kỳ')
    assert texts(zalo)[-1] == 'Thành Đức đã nhận yêu cầu giao mực của anh/chị. Điều phối sẽ gọi lại để hẹn giờ trong giờ làm việc (7:30–17:30, T2–T7).'
    assert last_log(db).result == 'PHIEU_NHAP'


def test_chi_dieu_phoi_xem_va_duyet(client, db, shop, zalo):
    row = staff_draft(client, db, zalo, shop['sales'])
    for role in ('technician', 'sales', 'finance'):
        service_login(client, shop[role])
        assert client.get('/phieu-nhap-zalo').status_code == 403
        assert client.get(f'/phieu-nhap-zalo/{row.id}').status_code == 403
        assert client.get(f'/dich-vu/moi?nhap={row.id}').status_code in (403, 404)
    service_login(client, shop['dispatcher'])
    html = client.get('/phieu-nhap-zalo').text
    assert row.number in html and 'Công ty An Phú' in html
    html = client.get(f'/phieu-nhap-zalo/{row.id}').text
    assert 'lên phiếu sửa máy An Phú' in html and f'/dich-vu/moi?nhap={row.id}' in html
    assert 'Nháp từ Zalo' in client.get('/dieu-phoi').text
    assert 'Phiếu nháp từ Zalo chờ duyệt' in client.get('/viec-cua-toi').text


def test_tao_phieu_tu_nhap_dien_san_khong_chon_may(client, db, shop, zalo):
    row = staff_draft(client, db, zalo, shop['sales'])
    data = pn.prefill(db, row)
    assert (data['customer_code'], data['kind'], data['priority'], data['symptom']) == ('KTD-000001', 'SUA_TAI_CHO', 'GAP', 'Máy in kẹt giấy liên tục')
    assert 'serial' not in data  # TUẤN 05/11: không tự gắn máy cũ
    csrf = service_login(client, shop['dispatcher'])
    html = client.get(f'/dich-vu/moi?nhap={row.id}').text
    assert f'data-zalo-draft="{row.number}"' in html and f'name="nhap" value="{row.id}"' in html and 'Máy in kẹt giấy liên tục' in html
    sent_before = len(texts(zalo))
    r = client.post('/dich-vu/moi', data=service_form(csrf, nhap=str(row.id), symptom='Máy in kẹt giấy liên tục', priority='GAP',
                                                      form_key=FK.format(1)), follow_redirects=False)
    assert r.status_code == 303, r.text[r.text.find('Chưa lưu'):][:300]
    db.expire_all()
    ticket = db.scalars(select(ServiceTicket)).one()
    row = db.get(ZaloTicketDraft, row.id)
    assert (row.status, row.ticket_id, row.decided_by) == ('DA_TAO', ticket.id, shop['dispatcher'].id)
    assert ticket.serial is None and ticket.created_by == shop['dispatcher'].id
    assert ticket.number in texts(zalo)[-1] and len(texts(zalo)) == sent_before + 1  # báo lại nhân viên đã nhắn
    # bấm lại: không tạo phiếu thứ hai
    assert client.get(f'/dich-vu/moi?nhap={row.id}').status_code == 409
    r = client.post('/dich-vu/moi', data=service_form(csrf, nhap=str(row.id), form_key=FK.format(2)))
    assert r.status_code == 409 and len(db.scalars(select(ServiceTicket)).all()) == 1


def test_bo_nhap_phai_co_ly_do(client, db, shop, zalo):
    row = staff_draft(client, db, zalo, shop['sales'])
    csrf = service_login(client, shop['dispatcher'])
    url = f'/phieu-nhap-zalo/{row.id}/bo'
    assert client.post(url, data={'csrf': csrf, 'form_key': FK.format(1), 'version': row.version, 'ly_do': ''}).status_code == 400
    assert client.post(url, data={'csrf': csrf, 'form_key': FK.format(2), 'version': row.version, 'ly_do': 'KHAC', 'ghi_chu': ''}).status_code == 400
    assert client.post(url, data={'csrf': csrf, 'form_key': FK.format(3), 'version': row.version + 5, 'ly_do': 'TRUNG'}).status_code == 409
    r = client.post(url, data={'csrf': csrf, 'form_key': FK.format(4), 'version': row.version, 'ly_do': 'TRUNG'}, follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    row = db.get(ZaloTicketDraft, row.id)
    assert (row.status, row.drop_reason, row.decided_by) == ('DA_BO', 'TRUNG', shop['dispatcher'].id)
    assert 'đã bị bỏ: trùng phiếu đã có' in texts(zalo)[-1]
    assert db.scalars(select(AuditLog).where(AuditLog.action == 'zalo_draft.drop')).one()
    assert client.post(url, data={'csrf': csrf, 'form_key': FK.format(5), 'version': row.version, 'ly_do': 'TRUNG'}).status_code == 409
    assert row.number in client.get('/phieu-nhap-zalo?view=da-bo').text
    assert client.get(f'/dich-vu/moi?nhap={row.id}').status_code == 409

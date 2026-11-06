"""Tin gửi khách (Trợ lý Zalo đợt 4, TUẤN duyệt mẫu 06/11/2026).

App soạn từ số liệu thật; người làm sửa rồi tự gửi qua Zalo công ty (khách đã gắn) hoặc sao chép + Đã gửi. Không tự gửi.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import tin_gui_khach as tg
from app import zalo_agent as agent
from app.models import (AuditLog, Customer, CustomerMessage, CustomerPhone, CustomerOpeningDebt, Equipment, Quote, QuoteLine, ServiceStep,
                        ServiceTicket, ZaloAiJob, ZaloChat, ZaloMessage, ZaloTroLy)
from test_web import service_login
from test_zalo_tin_nhan import H, event
from test_zalo_tro_ly import FK, call, linked, say, texts, zalo  # noqa: F401 - fixture zalo

BANK = '0123456789 – Vietcombank – Cty TNHH TMDV Tin Học Thành Đức'


@pytest.fixture
def shop(db, make_user):
    users = {r: make_user('tg.' + r, r) for r in ('finance', 'sales', 'dispatcher', 'technician', 'director')}
    db.add_all([Customer(code='KTD-000001', name='Công ty An Phú', search_text='ktd-000001 cong ty an phu'),
                Customer(code='KTD-000002', name='Cửa hàng Minh Long', search_text='ktd-000002 cua hang minh long')])
    db.flush()
    db.add(CustomerOpeningDebt(number='NDK-1', customer_code='KTD-000001', amount=6_050_000,
                               as_of=tg._now().date() - timedelta(days=65), created_by=users['director'].id))
    db.add(ZaloTroLy(id=1, enabled=True, draft_user_id=users['sales'].id, bank_line=BANK))
    db.commit()
    return users


def customer_chat(db, code='KTD-000001', chat_id='kh-1'):
    row = ZaloChat(chat_id=chat_id, chat_type='PRIVATE', display_name='Kế toán An Phú', customer_code=code,
                   last_message_at=datetime.now(timezone.utc))
    db.add(row)
    db.commit()
    return row


def form(csrf, n, row, **kw):
    return {'csrf': csrf, 'form_key': FK.format(n), 'version': str(row.version), **kw}


def fresh(db, msg_id):
    db.expire_all()
    return db.get(CustomerMessage, msg_id)


def test_nhac_no_soan_tu_cong_no_roi_gui_qua_zalo_cong_ty(client, db, shop, zalo):
    chat = customer_chat(db)
    csrf = service_login(client, shop['finance'])
    assert 'Soạn tin' in client.get('/cong-no-khach-hang').text
    r = client.get('/tin-gui-khach/moi?loai=NHAC_NO&khach=KTD-000001', follow_redirects=False)
    assert r.status_code == 303
    row = db.scalars(select(CustomerMessage)).one()
    assert (row.kind, row.status, row.source, row.number[:3]) == ('NHAC_NO', 'CHO', 'APP', 'TG-')
    assert '6.050.000 đ' in row.text and BANK in row.text and 'nội dung: KTD-000001' in row.text and row.facts['total'] == 6_050_000
    # mở lại nút: không soạn trùng
    assert client.get('/tin-gui-khach/moi?loai=NHAC_NO&khach=KTD-000001', follow_redirects=False).headers['location'] == f'/tin-gui-khach/{row.id}'
    html = client.get(f'/tin-gui-khach/{row.id}').text
    assert 'Gửi qua Zalo công ty' in html and 'Kế toán An Phú' in html and 'Sao chép tin' not in html
    assert texts(zalo) == []  # soạn không tự gửi
    edited = row.text.replace('Cảm ơn anh/chị!', 'Em cảm ơn anh/chị nhiều!')
    r = client.post(f'/tin-gui-khach/{row.id}/gui-zalo', data=form(csrf, 1, row, noi_dung=edited), follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    row = fresh(db, row.id)
    assert (row.status, row.sent_via, row.chat_pk, row.decided_by, row.edited_by) == ('DA_GUI', 'BOT', chat.id, shop['finance'].id, shop['finance'].id)
    assert texts(zalo) == [edited] and zalo['sent'][-1][1]['chat_id'] == 'kh-1'
    assert db.get(ZaloMessage, row.zalo_message_id).kind == 'tin_khach'
    assert db.scalars(select(AuditLog).where(AuditLog.action == 'customer_message.send')).one().user_id == shop['finance'].id
    # gửi xong không sửa, không gửi lại
    assert client.post(f'/tin-gui-khach/{row.id}/gui-zalo', data=form(csrf, 2, row, noi_dung=edited)).status_code == 409
    assert len(texts(zalo)) == 1
    # Q9: kế toán không nhắc lại trong 7 ngày; giám đốc vẫn soạn được, có cảnh báo
    assert client.get('/tin-gui-khach/moi?loai=NHAC_NO&khach=KTD-000001').status_code == 409
    service_login(client, shop['director'])
    client.get('/tin-gui-khach/moi?loai=NHAC_NO&khach=KTD-000001')
    again = db.scalars(select(CustomerMessage).where(CustomerMessage.status == 'CHO')).one()
    assert again.facts['last_sent'] and 'Đã nhắc nợ khách này ngày' in client.get(f'/tin-gui-khach/{again.id}').text


def test_khach_tra_loi_bao_chuong_nguoi_gui(client, db, shop, zalo):
    customer_chat(db)
    csrf = service_login(client, shop['finance'])
    client.get('/tin-gui-khach/moi?loai=NHAC_NO&khach=KTD-000001')
    row = db.scalars(select(CustomerMessage)).one()
    client.post(f'/tin-gui-khach/{row.id}/gui-zalo', data=form(csrf, 1, row, noi_dung=row.text))
    row = fresh(db, row.id)
    db.add(ZaloMessage(chat_pk=row.chat_pk, direction='IN', kind='text', text='bên mình chuyển hôm qua rồi em', status='DA_NHAN',
                       message_at=row.decided_at + timedelta(minutes=5)))
    db.commit()
    from app import my_work, notifications
    assert [n for n in notifications.items(db, shop['finance']) if n['id'] == f'customer_message_replies:{row.id}']
    html = client.get(f'/tin-gui-khach/{row.id}').text
    assert 'bên mình chuyển hôm qua rồi em' in html and 'Khách trả lời' in html
    assert fresh(db, row.id).reply_seen_at is not None
    assert not [g for g in my_work.collect(db, shop['finance'], keys=('customer_message_replies',)) if g['rows']]


def test_quyen_theo_loai_tin_va_ai_thay_tin_nao(client, db, shop, zalo):
    service_login(client, shop['technician'])
    assert client.get('/tin-gui-khach').status_code == 403
    service_login(client, shop['sales'])
    assert client.get('/tin-gui-khach/moi?loai=NHAC_NO&khach=KTD-000001').status_code == 403
    service_login(client, shop['finance'])
    client.get('/tin-gui-khach/moi?loai=NHAC_NO&khach=KTD-000001')
    row = db.scalars(select(CustomerMessage)).one()
    service_login(client, shop['sales'])
    assert client.get(f'/tin-gui-khach/{row.id}').status_code == 403  # tin người khác soạn
    assert row.number not in client.get('/tin-gui-khach').text
    service_login(client, shop['director'])
    assert row.number in client.get('/tin-gui-khach').text and client.get(f'/tin-gui-khach/{row.id}').status_code == 200
    # khách không còn nợ thì không soạn
    service_login(client, shop['finance'])
    assert client.get('/tin-gui-khach/moi?loai=NHAC_NO&khach=KTD-000002').status_code == 400


def make_quote(db, user):
    q = Quote(number='BG-26-00087', customer_code='KTD-000002', status='DA_GUI', subtotal=1_640_000, vat_total=131_200,
              total=1_771_200, created_by=user.id)
    q.lines = [QuoteLine(line_no=1, description='Hộp mực HP 12A chính hãng', qty=Decimal('2'), unit='hộp', unit_price=820_000,
                         vat_rate=8, amount=1_640_000, vat_amount=131_200)]
    db.add(q)
    db.commit()
    return q


def test_bao_gia_khach_chua_gan_thi_sao_chep_roi_bam_da_gui(client, db, shop, zalo):
    q = make_quote(db, shop['sales'])
    db.add(CustomerPhone(customer_code='KTD-000002', phone='0909123456'))
    db.commit()
    csrf = service_login(client, shop['sales'])
    assert f'/tin-gui-khach/moi?loai=BAO_GIA&amp;bao_gia={q.id}' in client.get(f'/bao-gia/{q.id}').text
    client.get(f'/tin-gui-khach/moi?loai=BAO_GIA&bao_gia={q.id}')
    row = db.scalars(select(CustomerMessage)).one()
    assert (row.customer_code, row.ref_type, row.ref_id) == ('KTD-000002', 'quote', q.id)
    assert 'BG-26-00087' in row.text and '1.771.200 đ' in row.text and 'Hộp mực HP 12A chính hãng × 2 hộp: 1.640.000 đ' in row.text
    html = client.get(f'/tin-gui-khach/{row.id}').text
    assert 'Sao chép tin' in html and 'Đã gửi bằng Zalo cá nhân' in html and 'Gửi qua Zalo công ty' not in html
    assert 'Gửi tới: <b>Cửa hàng Minh Long</b> · <b>0909123456</b>' in html and 'href="https://zalo.me/0909123456"' in html
    assert client.post(f'/tin-gui-khach/{row.id}/gui-zalo', data=form(csrf, 1, row, noi_dung=row.text)).status_code == 409
    r = client.post(f'/tin-gui-khach/{row.id}/da-gui', data=form(csrf, 2, row, noi_dung=row.text), follow_redirects=False)
    assert r.status_code == 303
    row = fresh(db, row.id)
    assert (row.status, row.sent_via, row.decided_by) == ('DA_GUI', 'CA_NHAN', shop['sales'].id) and texts(zalo) == []
    assert row.number in client.get('/tin-gui-khach?view=da-gui').text
    # báo giá đã hủy thì không soạn
    q.status = 'DA_HUY'
    db.commit()
    assert client.get(f'/tin-gui-khach/moi?loai=BAO_GIA&bao_gia={q.id}').status_code == 409


def test_bo_tin_va_tin_trong_hoac_dai_qua(client, db, shop, zalo):
    q = make_quote(db, shop['sales'])
    csrf = service_login(client, shop['sales'])
    client.get(f'/tin-gui-khach/moi?loai=BAO_GIA&bao_gia={q.id}')
    row = db.scalars(select(CustomerMessage)).one()
    assert client.post(f'/tin-gui-khach/{row.id}/luu', data=form(csrf, 1, row, noi_dung=' ')).status_code == 400
    assert client.post(f'/tin-gui-khach/{row.id}/luu', data=form(csrf, 2, row, noi_dung='x' * 2001)).status_code == 400
    assert client.post(f'/tin-gui-khach/{row.id}/luu', data=form(csrf, 3, row, noi_dung='Chào anh chị', version='99')).status_code == 409
    r = client.post(f'/tin-gui-khach/{row.id}/bo', data=form(csrf, 4, row), follow_redirects=False)
    assert r.status_code == 303 and fresh(db, row.id).status == 'DA_BO'


def finished_ticket(db, user, tech):
    db.add(Equipment(serial='VNB3K12345', customer_code='KTD-000002', model_name='HP LaserJet M404dn', created_by=user.id))
    db.flush()
    t = ServiceTicket(number='DV-26-00412', customer_code='KTD-000002', serial='VNB3K12345', kind='SUA_TAI_CHO', symptom='Kẹt giấy',
                      status='HOAN_THANH', assignee_id=tech.id, repaired_by=tech.id, warranty_months=1, created_by=user.id)
    db.add(t)
    db.flush()
    db.add(ServiceStep(ticket_id=t.id, action='complete', from_status='DANG_LAM', to_status='HOAN_THANH', actor_id=tech.id))
    db.commit()
    return t


def test_hoi_tham_sau_sua_tu_phieu_da_xong(client, db, shop, zalo):
    t = finished_ticket(db, shop['dispatcher'], shop['technician'])
    service_login(client, shop['dispatcher'])
    assert f'/tin-gui-khach/moi?loai=HOI_THAM&amp;phieu={t.id}' in client.get(f'/dich-vu/{t.id}').text
    client.get(f'/tin-gui-khach/moi?loai=HOI_THAM&phieu={t.id}')
    row = db.scalars(select(CustomerMessage)).one()
    assert 'DV-26-00412' in row.text and 'HP LaserJet M404dn' in row.text and 'VNB3K12345' in row.text
    assert agent.get_settings().company_hotline in row.text and 'bảo hành sửa chữa đến' in row.text
    t.status = 'DANG_LAM'
    db.commit()
    db.delete(row)
    db.commit()
    assert client.get(f'/tin-gui-khach/moi?loai=HOI_THAM&phieu={t.id}').status_code == 409


def test_viet_lai_bang_ai_chi_dung_khi_giu_du_so(client, db, shop, zalo, monkeypatch):
    q = make_quote(db, shop['sales'])
    csrf = service_login(client, shop['sales'])
    client.get(f'/tin-gui-khach/moi?loai=BAO_GIA&bao_gia={q.id}')
    row = db.scalars(select(CustomerMessage)).one()
    # Z2 không chạy: báo, không tạo lượt
    assert client.post(f'/tin-gui-khach/{row.id}/viet-lai', data=form(csrf, 1, row, noi_dung=row.text)).status_code == 409
    db.get(ZaloTroLy, 1).staff_engine = 'claude'
    db.commit()
    monkeypatch.setattr(agent, 'z2_alive', lambda session: True)
    client.post(f'/tin-gui-khach/{row.id}/viet-lai', data=form(csrf, 2, row, noi_dung=row.text))
    row = fresh(db, row.id)
    job = db.get(ZaloAiJob, row.ai_job_id)
    assert job.log_id is None and job.engine == 'claude' and 'BG-26-00087' in job.prompt
    assert client.get(f'/tin-gui-khach/{row.id}/ai').json() == {'status': 'DANG'}
    good = ('Dạ Thành Đức gửi anh/chị báo giá BG-26-00087 ạ:\n• Hộp mực HP 12A chính hãng × 2 hộp: 1.640.000 đ\n'
            'Tiền hàng chưa VAT 1.640.000 đ, VAT 131.200 đ. Tổng 1.771.200 đ, hiệu lực đến '
            + row.facts['valid_until'][8:10] + '/' + row.facts['valid_until'][5:7] + '/' + row.facts['valid_until'][:4] + '.\nAnh/chị ưng thì nhắn em nhé.')
    job.status, job.answer = 'XONG', good
    db.commit()
    html = client.get(f'/tin-gui-khach/{row.id}').text
    assert 'Dùng bản này' in html, html[html.find('Bản AI'):][:600]
    client.post(f'/tin-gui-khach/{row.id}/dung-ban-moi', data=form(csrf, 3, row))
    row = fresh(db, row.id)
    assert row.text == good and row.ai_job_id is None
    # bản AI đổi số: không cho dùng
    client.post(f'/tin-gui-khach/{row.id}/viet-lai', data=form(csrf, 4, row, noi_dung=row.text))
    row = fresh(db, row.id)
    bad = db.get(ZaloAiJob, row.ai_job_id)
    bad.status, bad.answer = 'XONG', good.replace('1.771.200', '1.700.000')
    db.commit()
    assert 'lệch số' in client.get(f'/tin-gui-khach/{row.id}').text
    assert client.post(f'/tin-gui-khach/{row.id}/dung-ban-moi', data=form(csrf, 5, row)).status_code == 409
    assert fresh(db, row.id).text == good


def test_so_trong_tin():
    assert tg.compare('Tổng 1.771.200 đ, BG-26-00087, ngày 13/11/2026', 'BG-26-00087 tổng 1771200đ ngày 13/11/2026') == ([], [])
    assert tg.compare('còn 4.850.000 đ', 'còn 4.800.000 đ') == (['4850000'], ['4800000'])


def test_nhan_bot_soan_tin_tra_link(client, db, shop, zalo):
    customer_chat(db, chat_id='kh-9')
    linked(client, db, user=shop['finance'])
    zalo['script'] = [call('soan_tin_khach', loai='NHAC_NO', ma_khach='KTD-000001', so_chung_tu=''), say('Em đã soạn tin nhắc nợ.')]
    client.post('/zalo/webhook', json=event(mid='m1', text='soạn tin nhắc nợ An Phú'), headers=H)
    assert 'soan_tin_khach' in zalo['ai'][0]['tools']
    db.expire_all()
    row = db.scalars(select(CustomerMessage)).one()
    assert (row.source, row.created_by, row.status) == ('ZALO', shop['finance'].id, 'CHO')
    assert f'/tin-gui-khach/{row.id}' in texts(zalo)[-1]
    assert not [m for m, p in zalo['sent'] if p.get('chat_id') == 'kh-9']  # bot không gửi cho khách
    # thợ không có công cụ soạn tin
    linked(client, db, chat='c2', user=shop['technician'])
    zalo['script'] = [say('Không làm được.')]
    client.post('/zalo/webhook', json=event(mid='m2', text='soạn tin nhắc nợ', chat='c2'), headers=H)
    assert 'soan_tin_khach' not in zalo['ai'][-1]['tools']


def test_tin_nhac_no_thu_hai_co_link_soan_tin(db, shop):
    from app import debt_reminder
    d = debt_reminder.build(db, shop['finance'], tg._now().date())
    text = debt_reminder.render_text(d, 'https://vd.test')
    assert 'Soạn tin: https://vd.test/tin-gui-khach/moi?loai=NHAC_NO&khach=KTD-000001' in text

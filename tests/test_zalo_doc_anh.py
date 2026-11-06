"""Trợ lý Zalo đợt 3 (TUẤN duyệt mẫu 06/11/2026): đọc ảnh nhân viên gửi bằng Codex/Claude trên Z2.

AI chỉ đọc chữ trong ảnh (JSON); app tra bảo hành, tồn, giá theo quyền và lập đơn mua NHÁP từ hóa đơn NCC.
"""
import io
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from PIL import Image
from sqlalchemy import select

from app import zalo_agent as agent
from app import zalo_doc_anh as doc
from app.config import get_settings
from app.models import (Attachment, Equipment, Product, PurchaseOrder, Supplier, ZaloAgentLog, ZaloAiJob, ZaloTroLy)
from test_web import service_login
from test_zalo_tin_nhan import H, event
from test_zalo_tro_ly import linked, shop, texts, zalo  # noqa: F401
from test_zalo_z2 import _alive


def _png():
    out = io.BytesIO()
    Image.new('RGB', (40, 30), (200, 200, 200)).save(out, 'PNG')
    return out.getvalue()


@pytest.fixture
def anh(monkeypatch, tmp_path, db, shop, make_user):
    """Giả Zalo (tải ảnh) và máy Z2 (đọc ảnh trả JSON theo kịch bản)."""
    monkeypatch.setattr(get_settings(), 'files_dir', str(tmp_path))
    state = dict(script=[], jobs=[], downloads=[])

    def fake_download(url):
        state['downloads'].append(url)
        return _png()
    monkeypatch.setattr(doc, 'download', fake_download)
    real_wait = agent._wait_job

    def fake_wait(session, job_id, wait):
        for j in agent.take_jobs(session, 5):
            state['jobs'].append(dict(j, image_ok=agent.job_image(session, j['id']).is_file()))
            step = state['script'].pop(0)
            agent.answer_job(session, j['id'], step if isinstance(step, str) else json.dumps(step, ensure_ascii=False))
        return real_wait(session, job_id, 1)
    monkeypatch.setattr(agent, '_wait_job', fake_wait)
    _alive(db, shop)
    db.get(ZaloTroLy, 1).staff_engine = 'codex'
    shop['warehouse'] = make_user('anh.kho', 'warehouse')
    shop['warehouse'].full_name = 'Nguyễn Bình Thạnh'
    db.add_all([Product(code='TD-0001', name='Máy in HP M404dn', unit='cái', search_text='td-0001 may in hp m404dn'),
                Product(code='TD-0233', name='Trống HP 12A', unit='cái', search_text='td-0233 trong hp 12a drum'),
                Supplier(code='NCC-0007', name='Cty Mực In Sài Gòn', tax_code='0301234567', search_text='ncc-0007 cty muc in sai gon')])
    db.flush()
    db.add(Equipment(serial='VNB3K12345', product_code='TD-0001', customer_code='KTD-000001', search_text='vnb3k12345',
                     warranty_until=agent._now().date() + timedelta(days=120), created_by=shop['director'].id))
    db.commit()
    return state


def photo(client, mid='p1', caption='', chat='c1'):
    client.post('/zalo/webhook', json=event(mid=mid, text='', chat=chat, kind='message.image.received',
                                            photo='https://zalo.example/anh.jpg', caption=caption), headers=H)


def log_of(db):
    db.expire_all()
    return db.scalars(select(ZaloAgentLog).order_by(ZaloAgentLog.id.desc())).first()


def test_tem_may_tra_bao_hanh(client, db, shop, zalo, anh):
    linked(client, db, user=shop['technician'])
    anh['script'] = [dict(loai='TEM_MAY', ro=True, tem=dict(hang='HP', model='LaserJet Pro M404dn', serial='VNB3K12345'))]
    photo(client, caption='máy này còn BH ko')
    job = anh['jobs'][0]
    assert job['may'] == 'codex' and job['anh'] == f"/ai/zalo/viec/{job['id']}/anh" and job['image_ok']
    assert 'máy này còn BH ko' in job['lenh'] and 'HOA_DON' in job['lenh']
    assert zalo['ai'] == []  # không gọi API
    out = texts(zalo)
    assert out[-2] == doc.WAIT_TEXT
    assert 'serial VNB3K12345' in out[-1] and 'Công ty An Phú' in out[-1] and 'còn hạn đến' in out[-1] and '/thiet-bi/VNB3K12345' in out[-1]
    log = log_of(db)
    assert (log.result, log.image_kind, log.engine) == ('TRA_LOI', 'TEM_MAY', 'codex')
    assert log.image_path.startswith('zalo_anh/') and doc.path_of(log.image_path) is not None
    assert log.tools[-1]['ten'] == 'doc_anh' and 'VNB3K12345' in log.tools[-1]['ra']


def test_tem_mo_thi_nho_chup_lai(client, db, shop, zalo, anh):
    linked(client, db, user=shop['technician'])
    anh['script'] = [dict(loai='TEM_MAY', ro=False, chua_ro='serial bị lóa', tem=dict(hang='Canon', model='LBP 2900', serial=''))]
    photo(client)
    assert 'serial bị lóa' in texts(zalo)[-1] and 'chụp lại' in texts(zalo)[-1]
    assert log_of(db).result == 'CHUP_LAI'


def test_hop_muc_tho_khong_thay_gia_kinh_doanh_thay_gia(client, db, shop, zalo, anh):
    box = dict(loai='HOP_MUC', ro=True, hop=dict(hang='HP', ma=['Q2612A', '12A'], ten='HP 12A'))
    linked(client, db, user=shop['technician'])
    anh['script'] = [box, box]
    photo(client, mid='p1')
    tech = texts(zalo)[-1]
    assert 'TD-0142' in tech and 'Kho chính 8' in tech and 'giá' not in tech and '820' not in tech
    linked(client, db, chat='c2', user=shop['sales'])
    photo(client, mid='p2', chat='c2')
    sale = texts(zalo)[-1]
    assert 'TD-0142' in sale and 'giá bán 820.000' in sale and '600' not in sale  # không giá vốn


def test_hoa_don_thanh_don_mua_nhap_co_anh(client, db, shop, zalo, anh):
    linked(client, db, user=shop['warehouse'])
    anh['script'] = [dict(loai='HOA_DON', ro=True, hoa_don=dict(
        ncc='CÔNG TY MỰC IN SÀI GÒN', mst='0301 234 567', so='0001234', ngay=agent._now().date().isoformat(), gia_gom_vat=False, tong=3696000,
        dong=[dict(ten='Mực HP 12A chính hãng', ma='', sl=4, don_gia=600000, thue=10),
              dict(ten='Trống HP 12A', ma='', sl=3, don_gia=95000, thue=10),
              dict(ten='Drum Brother DR-2385', ma='DR-2385', sl=2, don_gia=450000, thue=10)]))]
    photo(client, caption='lên đơn mua')
    order = db.scalars(select(PurchaseOrder)).one()
    assert (order.status, order.supplier_code, order.created_by, order.price_includes_vat) == ('NHAP', 'NCC-0007', shop['warehouse'].id, False)
    assert sorted((l.product_code, l.qty, l.unit_cost) for l in order.lines) == [('TD-0142', Decimal(4), 600000), ('TD-0233', Decimal(3), 95000)]
    assert 'Chưa ghép mã: Drum Brother DR-2385' in order.note
    att = db.scalars(select(Attachment).where(Attachment.entity == 'purchase_order')).one()
    assert att.entity_id == str(order.id)
    reply = texts(zalo)[-1]
    assert order.number in reply and '2 dòng đã ghép mã' in reply and 'Drum Brother' in reply and f'/don-mua/{order.id}' in reply
    log = log_of(db)
    assert (log.result, log.purchase_order_id) == ('DON_MUA', order.id) and log.image_read['chua_ghep'][0]['ten'] == 'Drum Brother DR-2385'
    service_login(client, shop['warehouse'])
    html = client.get(f'/don-mua/{order.id}').text
    assert 'Lập từ ảnh hóa đơn Zalo' in html and 'Drum Brother DR-2385' in html and f'/attachments/{att.id}/file' in html
    assert client.get(f'/attachments/{att.id}/file').status_code == 200


def test_hoa_don_khong_khop_ncc_hoac_khong_co_quyen_kho(client, db, shop, zalo, anh):
    bill = dict(loai='HOA_DON', ro=True, hoa_don=dict(ncc='Cty Lạ', mst='0999999999', so='9', ngay='', tong=100000,
                                                      dong=[dict(ten='Mực HP 12A', sl=1, don_gia=100000)]))
    linked(client, db, user=shop['warehouse'])
    anh['script'] = [bill, bill]
    photo(client, mid='p1')
    assert 'Chưa khớp nhà cung cấp' in texts(zalo)[-1]
    linked(client, db, chat='c2', user=shop['sales'])
    photo(client, mid='p2', chat='c2')
    assert 'Chỉ thủ kho hoặc giám đốc' in texts(zalo)[-1]
    assert db.scalars(select(PurchaseOrder)).all() == []


def test_khong_co_z2_thi_bao_khong_dung_api(client, db, shop, zalo, anh):
    db.get(ZaloTroLy, 1).staff_engine = 'api'
    db.commit()
    linked(client, db, user=shop['technician'])
    photo(client)
    assert texts(zalo)[-1] == doc.NO_Z2_TEXT and zalo['ai'] == [] and anh['jobs'] == [] and anh['downloads'] == []
    assert log_of(db).result == 'LOI'


def test_anh_khach_va_tat_doc_anh_thi_khong_doc(client, db, shop, zalo, anh):
    linked(client, db, customer='KTD-000001')
    photo(client, mid='p1')
    linked(client, db, chat='c2', user=shop['technician'])
    db.get(ZaloTroLy, 1).read_images = False
    db.commit()
    photo(client, mid='p2', chat='c2')
    assert anh['jobs'] == [] and anh['downloads'] == []
    assert [r.asker for r in db.scalars(select(ZaloAgentLog))] == ['CHUA_GAN', 'CHUA_GAN']  # chỉ tin "chào" lúc chưa gắn


def test_cong_z2_lay_anh_can_khoa_va_luot_dang_lam(client, db, shop, zalo, anh):
    raw = _alive(db, shop)
    job = ZaloAiJob(log_id=None, engine='codex', prompt='x', image_path='zalo_anh/khong-co.jpg')
    # cần một nhật ký thật cho khóa ngoại
    linked(client, db, user=shop['technician'])
    anh['script'] = [dict(loai='KHAC', ro=True)]
    photo(client)
    log = log_of(db)
    assert 'chưa nhận ra' in texts(zalo)[-1]
    job.log_id, job.image_path = log.id, log.image_path
    db.add(job)
    db.commit()
    hdr = {'Authorization': 'Bearer ' + raw}
    assert client.get(f'/ai/zalo/viec/{job.id}/anh', headers=hdr).status_code == 404  # chưa lấy (chưa Đang làm)
    taken = client.get('/ai/zalo/viec', headers=hdr).json()
    assert taken[0]['anh'] == f'/ai/zalo/viec/{job.id}/anh'
    r = client.get(f'/ai/zalo/viec/{job.id}/anh', headers=hdr)
    assert r.status_code == 200 and r.headers['content-type'] == 'image/jpeg'
    assert client.get(f'/ai/zalo/viec/{job.id}/anh').status_code in (401, 403)


def test_nhat_ky_co_anh_va_xoa_anh_cu(client, db, shop, zalo, anh):
    linked(client, db, user=shop['technician'])
    anh['script'] = [dict(loai='TEM_MAY', ro=True, tem=dict(model='M404dn', serial='VNB3K12345'))]
    photo(client)
    log = log_of(db)
    service_login(client, shop['director'])
    html = client.get('/zalo/tro-ly?tab=nhat-ky').text
    assert f'/zalo/tro-ly/anh/{log.id}' in html and 'Tem máy' in html and 'S/N VNB3K12345' in html
    assert client.get(f'/zalo/tro-ly/anh/{log.id}').status_code == 200
    service_login(client, shop['technician'])
    assert client.get(f'/zalo/tro-ly/anh/{log.id}').status_code == 403
    path = doc.path_of(log.image_path)
    log.created_at -= timedelta(days=31)
    db.commit()
    assert doc.cleanup(db) == 1
    assert not path.exists() and db.get(ZaloAgentLog, log.id).image_path == ''


def test_cau_hinh_bat_tat_doc_anh(client, db, shop, zalo, anh):
    from test_zalo_tro_ly import FK
    csrf = service_login(client, shop['director'])
    client.post('/zalo/tro-ly/cau-hinh', data={'csrf': csrf, 'form_key': FK.format(1), 'bat': '1', 'nguoi_nhan': shop['sales'].id,
                                               'doc_anh': '0', 'don_mua': '0'})
    db.expire_all()
    row = db.get(ZaloTroLy, 1)
    assert (row.read_images, row.invoice_po) == (False, False)


def test_parse_va_tom_tat():
    assert doc.parse('rác')['loai'] == 'KHAC'
    got = doc.parse('```json\n{"loai": "tem_may", "tem": {"model": "M404", "serial": "AB1"}}\n```')
    assert got['loai'] == 'TEM_MAY' and got['ro'] is True and doc.brief(got) == 'M404 · S/N AB1'

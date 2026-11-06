"""Chụp tem máy tại quầy (TUẤN duyệt mẫu 06/11/2026): máy Z2 đọc model + serial, app tra serial và trả dữ liệu để
màn Nhận máy khách mang tới điền sẵn. AI không lưu gì; ảnh tem chỉ vào phiếu khi nhân viên bấm Nhận máy có tick."""
import io
import json
import uuid
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import select, update

from app import equipment, quay_doc_tem as tem, zalo_agent as agent
from app.config import get_settings
from app.models import Attachment, Product, ServiceTicket, ZaloAiJob, ZaloTroLy
from app.schemas import EquipmentIn
from test_my_work import people, purchase_people  # noqa: F401
from test_web import d5_image, service_login
from test_zalo_z2 import _alive


def _jpg():
    out = io.BytesIO()
    Image.new('RGB', (60, 40), (220, 220, 220)).save(out, 'JPEG')
    return out.getvalue()


@pytest.fixture
def quay(db, people, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), 'files_dir', str(tmp_path))
    db.add(ZaloTroLy(id=1, enabled=False, staff_engine='codex'))
    db.commit()
    _alive(db, people)
    equipment.create(db, people['dispatcher'], EquipmentIn(serial='PHBQB12345', product_code='TD-0001', customer_code='KTD-000001'))
    db.execute(update(Product).where(Product.code == 'TD-0001').values(name='Máy in HP M404dn', search_text='td-0001 may in hp m404dn'))
    db.commit()
    return people


def send(client, csrf, data=None):
    return client.post('/quay-sua/doc-tem', data={'csrf': csrf, 'form_key': str(uuid.uuid4())},
                       files={'photo': ('tem.jpg', data if data is not None else _jpg(), 'image/jpeg')})


def z2_answers(db, obj):
    jobs = agent.take_jobs(db, 5)
    for j in jobs:
        assert j['anh'] == f"/ai/zalo/viec/{j['id']}/anh" and agent.job_image(db, j['id']).is_file()
        agent.answer_job(db, j['id'], obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))
    return jobs


def test_doc_tem_serial_co_san_tra_khach_may_bao_hanh(client, db, quay):
    csrf = service_login(client, quay['sales'])
    res = send(client, csrf)
    job = res.json()['job']
    assert client.get(f'/quay-sua/doc-tem/{job}').json() == {'status': 'DANG'}
    row = db.get(ZaloAiJob, job)
    assert row.log_id is None and row.engine == 'codex' and 'S/N' in row.prompt
    z2_answers(db, {'loai': 'TEM_MAY', 'ro': True, 'tem': {'hang': 'HP', 'model': 'LaserJet Pro M404dn', 'serial': 'phbq b12345'}})
    r = client.get(f'/quay-sua/doc-tem/{job}').json()
    assert r['status'] == 'XONG' and r['found'] and r['serial'] == 'PHBQB12345' and r['ro']
    assert r['customer'] == {'code': 'KTD-000001', 'name': 'Minh Phát', 'active': True}
    assert 'held' not in r and r['machine'].startswith('PHBQB12345 · ')
    assert client.get(f'/quay-sua/doc-tem/{job}/anh').status_code == 200
    # Người khác không xem được lượt / ảnh của mình.
    service_login(client, quay['other_sales'])
    assert client.get(f'/quay-sua/doc-tem/{job}').status_code == 404
    assert client.get(f'/quay-sua/doc-tem/{job}/anh').status_code == 404


def test_serial_chua_co_goi_y_ma_hang_theo_model(client, db, quay):
    csrf = service_login(client, quay['dispatcher'])
    job = send(client, csrf).json()['job']
    z2_answers(db, '```json\n{"loai":"TEM_MAY","ro":true,"tem":{"hang":"HP","model":"M404dn","serial":"VNB9X0001"}}\n```')
    r = client.get(f'/quay-sua/doc-tem/{job}').json()
    assert r['found'] is False and r['serial'] == 'VNB9X0001' and r['product'] == {'code': 'TD-0001', 'name': 'Máy in HP M404dn'}


def test_tem_mo_hoac_khong_phai_tem_thi_bao_chup_lai(client, db, quay):
    csrf = service_login(client, quay['sales'])
    job = send(client, csrf).json()['job']
    z2_answers(db, {'loai': 'TEM_MAY', 'ro': False, 'chua_ro': 'serial bị lóa', 'tem': {'hang': 'HP', 'model': 'M404dn', 'serial': ''}})
    r = client.get(f'/quay-sua/doc-tem/{job}').json()
    assert r['ro'] is False and r['chua_ro'] == 'serial bị lóa' and 'found' not in r
    job = send(client, csrf).json()['job']
    z2_answers(db, 'không phải JSON')
    r = client.get(f'/quay-sua/doc-tem/{job}').json()
    assert r['ro'] is False and r['chua_ro']


def test_z2_loi_hoac_qua_gio(client, db, quay):
    csrf = service_login(client, quay['sales'])
    job = send(client, csrf).json()['job']
    agent.take_jobs(db, 5)
    agent.fail_job(db, job, 'codex hết hạn đăng nhập')
    r = client.get(f'/quay-sua/doc-tem/{job}').json()
    assert r['status'] == 'LOI' and 'codex hết hạn' in r['error']
    job = send(client, csrf).json()['job']
    db.execute(update(ZaloAiJob).where(ZaloAiJob.id == job).values(created_at=agent._now() - timedelta(seconds=tem.WAIT + 5)))
    db.commit()
    assert client.get(f'/quay-sua/doc-tem/{job}').json()['status'] == 'LOI'
    db.expire_all()
    assert db.get(ZaloAiJob, job).status == 'LOI'


def test_khong_co_z2_khong_goi_api_va_tho_khong_dung(client, db, quay):
    db.get(ZaloTroLy, 1).staff_engine = 'api'
    db.commit()
    csrf = service_login(client, quay['sales'])
    res = send(client, csrf)
    assert res.json()['code'] == 'NO_Z2' and db.scalar(select(ZaloAiJob.id)) is None
    db.get(ZaloTroLy, 1).staff_engine = 'claude'
    db.commit()
    assert send(client, csrf, b'khong phai anh').json()['code'] == 'BAD_PHOTO'
    job = send(client, csrf).json()['job']
    assert 'anh.jpg' in db.get(ZaloAiJob, job).prompt
    csrf = service_login(client, quay['technician'])
    assert send(client, csrf).status_code == 403


def test_nhan_may_co_tick_them_anh_tem_vao_anh_nhan_may(client, db, quay):
    csrf = service_login(client, quay['sales'])
    job = send(client, csrf).json()['job']
    day = (agent._now() + timedelta(days=1)).date().isoformat()
    base = {'csrf': csrf, 'customer_code': 'KTD-000001', 'serial': 'PHBQB12345', 'symptom': 'Kẹt giấy khay 2',
            'condition_note': 'Vỏ trầy', 'accessories': 'DAY_NGUON', 'expected': day, 'tem_job': str(job), 'tem_anh': '1'}
    res = client.post('/quay-sua/nhan', data=base, files={'photo': ('may.png', d5_image(), 'image/png')}, follow_redirects=False)
    assert res.status_code == 303, res.text
    t = db.scalars(select(ServiceTicket).order_by(ServiceTicket.id.desc())).first()
    names = sorted(a.filename for a in db.scalars(select(Attachment).where(Attachment.entity == 'service_ticket', Attachment.entity_id == str(t.id),
                                                                           Attachment.tag == 'NHAN_MAY')))
    assert names == ['may.png', 'tem-may.jpg']
    # Lượt của người khác hoặc không tick thì không thêm ảnh tem, vẫn nhận máy bình thường.
    assert tem.photo(db, quay['other_sales'], job) is None and tem.photo(db, quay['sales'], 'abc') is None


def test_xoa_anh_tem_cu(client, db, quay):
    csrf = service_login(client, quay['sales'])
    job = send(client, csrf).json()['job']
    path = tem.image(db, quay['sales'], job)
    db.execute(update(ZaloAiJob).where(ZaloAiJob.id == job).values(created_at=agent._now() - timedelta(days=31)))
    db.commit()
    assert tem.cleanup(db) == 1 and not path.exists()
    db.expire_all()
    assert db.get(ZaloAiJob, job).image_path == ''


def test_man_nhan_may_co_o_chup_tem(client, db, quay):
    service_login(client, quay['sales'])
    html = client.get('/quay-sua/nhan').text
    assert 'data-tem-box' in html and 'Chụp tem máy để điền sẵn' in html and 'quay-doc-tem.js' in html and 'name="tem_anh"' in html

"""Trợ lý Zalo đợt 1 (TUẤN duyệt 06/11/2026): tra cứu nhanh + báo cáo theo câu hỏi, chỉ đọc, quyền theo vai trò."""
import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import zalo_tra_cuu as tc
from app.models import Customer, Equipment, Product, SalesReceipt, ServiceStep, ServiceTicket, ZaloAgentLog
from test_zalo_tin_nhan import H, event
from test_zalo_tro_ly import call, chat_of, last_log, linked, say, texts, zalo  # noqa: F401 - fixture

TZ = tc.TZ


@pytest.fixture
def ppl(db, make_user):
    p = {r: make_user('tc.' + r, r) for r in ('technician', 'sales', 'director', 'finance', 'dispatcher', 'warehouse')}
    p['binh'] = make_user('tc.binh', 'technician')
    p['binh'].full_name = 'Nguyễn Bình Thạnh'
    p['technician'].full_name = 'Đoàn Cảnh Phong'
    db.add_all([Customer(code='KTD-000001', name='Công ty An Phú', search_text='ktd-000001 cong ty an phu'),
                Product(code='TD-0001', name='Máy in HP M404dn', unit='cái', search_text='td-0001 may in hp m404dn')])
    db.commit()
    return p


def ticket(db, p, number, who='technician', status='DANG_LAM', due=None, **kw):
    row = ServiceTicket(number=number, customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Kẹt giấy', status=status,
                        assignee_id=p[who].id, created_by=p['dispatcher'].id, due_at=due, **kw)
    db.add(row)
    db.flush()
    return row


def now():
    return datetime.now(TZ)


def tools_of(db, user):
    return [t['function']['name'] for t in tc.build(db, user)[0]]


def test_cong_cu_theo_vai_tro(db, ppl):
    assert tools_of(db, ppl['technician']) == ['bao_hanh', 'trang_thai', 'phieu_qua_hen', 'viec_tho']
    assert tools_of(db, ppl['sales']) == ['bao_hanh', 'trang_thai', 'doanh_thu']  # doanh số của mình, không công nợ
    assert tools_of(db, ppl['dispatcher']) == ['bao_hanh', 'trang_thai', 'phieu_qua_hen', 'viec_tho']
    assert tools_of(db, ppl['finance']) == ['bao_hanh', 'trang_thai', 'phieu_qua_hen', 'no_khach', 'doanh_thu']
    assert tools_of(db, ppl['director']) == ['bao_hanh', 'trang_thai', 'phieu_qua_hen', 'viec_tho', 'no_khach', 'doanh_thu']
    assert 'việc của thợ' in tc.labels(tc.rights(ppl['director'])) and 'phiếu của tôi' in tc.labels(tc.rights(ppl['technician']))


def test_tho_chi_xem_phieu_cua_minh(db, ppl):
    mine, other = ticket(db, ppl, 'DV-1'), ticket(db, ppl, 'DV-2', who='binh')
    db.commit()
    r = tc.rights(ppl['technician'])
    assert tc.tool_status(db, ppl['technician'], r, 'dv-1')['ket_qua'][0]['trang_thai'] == 'Đang làm'
    assert 'không có quyền' in tc.tool_status(db, ppl['technician'], r, 'DV-2')['loi']
    by_customer = tc.tool_status(db, ppl['technician'], r, 'KTD-000001')
    assert [x['so'] for x in by_customer['ket_qua']] == ['DV-1']
    rs = tc.rights(ppl['sales'])  # kinh doanh không vào Dịch vụ
    assert 'không có quyền' in tc.tool_status(db, ppl['sales'], rs, 'DV-1')['loi']
    rd = tc.rights(ppl['dispatcher'])
    assert {x['so'] for x in tc.tool_status(db, ppl['dispatcher'], rd, 'KTD-000001')['ket_qua']} == {'DV-1', 'DV-2'}
    assert 'Không thấy' in tc.tool_status(db, ppl['dispatcher'], rd, 'XYZ')['loi']
    assert mine.id and other.id


def test_phieu_qua_hen_va_viec_tho(db, ppl):
    late = now() - timedelta(hours=5)
    ticket(db, ppl, 'DV-1', due=late)
    ticket(db, ppl, 'DV-2', who='binh', due=late)
    ticket(db, ppl, 'DV-3', who='binh', due=now() + timedelta(hours=5))
    done = ticket(db, ppl, 'DV-4', status='HOAN_THANH')
    db.add(ServiceStep(ticket_id=done.id, action='transition', to_status='HOAN_THANH', actor_id=ppl['technician'].id))
    db.commit()
    r = tc.rights(ppl['technician'])
    mine = tc.tool_overdue(db, ppl['technician'], r)
    assert mine['tong_qua_hen'] == 1 and mine['ket_qua'][0]['so'] == 'DV-1' and mine['link'].endswith('/tho')
    alls = tc.tool_overdue(db, ppl['dispatcher'], tc.rights(ppl['dispatcher']))
    assert alls['tong_qua_hen'] == 2 and 'view=overdue' in alls['link']
    me = tc.tool_technician(db, ppl['technician'], r, 'Bình')  # thợ hỏi tên người khác vẫn chỉ ra việc của mình
    assert me['tho'] == 'Đoàn Cảnh Phong' and me['hoan_thanh_hom_nay'] == 1 and me['dang_mo'] == 1
    binh = tc.tool_technician(db, ppl['dispatcher'], tc.rights(ppl['dispatcher']), 'binh thanh')
    assert binh['tho'] == 'Nguyễn Bình Thạnh' and binh['dang_mo'] == 2 and binh['phieu_dang_mo_theo_thu_tu'][0]['so'] == 'DV-2'
    assert 'cac_tho' in tc.tool_technician(db, ppl['dispatcher'], tc.rights(ppl['dispatcher']), 'không ai')
    assert 'loi' in tc.tool_technician(db, ppl['finance'], tc.rights(ppl['finance']), 'Phong')


def test_no_khach_cung_nguon_cong_no(db, ppl):
    t = ticket(db, ppl, 'DV-9', status='HOAN_THANH', billing='THU_TIEN', subtotal=1_000_000, vat_total=100_000, total=1_100_000,
               receivable=1_100_000, priced_at=now() - timedelta(days=3))
    db.add_all([SalesReceipt(number='BT-1', ticket_id=t.id, method='TIEN_MAT', amount=300_000, received_at=now().date(),
                             status='DA_XAC_NHAN', reported_by=ppl['technician'].id, decided_by=ppl['finance'].id, decided_at=now()),
                SalesReceipt(number='BT-2', ticket_id=t.id, method='TIEN_MAT', amount=200_000, received_at=now().date(),
                             status='CHO_XAC_NHAN', reported_by=ppl['technician'].id)])
    db.commit()
    out = tc.tool_debt(db, ppl['finance'], 'KTD-000001')
    assert (out['con_no_gom_vat'], out['trong_do_dang_o_nhan_vien'], out['khach_chua_tra']) == (800_000, 200_000, 600_000)
    assert out['hang_muc'][0]['so'] == 'DV-9' and out['hang_muc'][0]['so_ngay'] == 3 and '/khach-hang/KTD-000001' in out['link']
    assert 'loi' in tc.tool_debt(db, ppl['finance'], 'KTD-999')
    from app.errors import BusinessError
    with pytest.raises(BusinessError):  # nguồn số liệu tự chặn người không phải kế toán
        tc.tool_debt(db, ppl['sales'], 'KTD-000001')
    today = tc.tool_revenue(db, ppl['finance'], tc.rights(ppl['finance']), 'hom_nay')
    assert (today['doanh_thu_dich_vu_chua_vat'], today['da_thu_da_xac_nhan'], today['bao_thu_cho_xac_nhan_hien_tai']) == (
        0, 300_000, dict(so_tien=200_000, so_lan=1))
    mine = tc.tool_revenue(db, ppl['sales'], tc.rights(ppl['sales']), 'hom_nay')
    assert set(mine) == {'ky', 'tu', 'den', 'doanh_so_cua_toi_chua_vat', 'so_don'}  # không thấy tiền thu, dịch vụ


def test_ky_bao_cao():
    d = date(2026, 11, 6)  # thứ Sáu
    assert tc.period('tuan_nay', d)[:2] == (date(2026, 11, 2), d)
    assert tc.period('thang_truoc', d)[:2] == (date(2026, 10, 1), date(2026, 10, 31))
    assert tc.period('hom_qua', d)[:2] == (date(2026, 11, 5),) * 2
    assert tc.period('bậy', d)[:3] == (d, d, 'hôm nay')


def test_bao_hanh_theo_serial(db, ppl):
    db.add(Equipment(serial='VNB3K12345', product_code='TD-0001', customer_code='KTD-000001', search_text='vnb3k12345',
                     warranty_until=now().date() + timedelta(days=30), created_by=ppl['director'].id))
    db.commit()
    out = tc.tool_warranty(db, ' vnb3k 12345 ')
    assert (out['may'], out['khach'], out['con_bao_hanh_may']) == ('Máy in HP M404dn', 'Công ty An Phú', True)
    miss = tc.tool_warranty(db, '12345')
    assert 'loi' in miss and miss['serial_gan_giong'] == ['VNB3K12345']


def test_qua_zalo_kinh_doanh_khong_goi_duoc_cong_no(client, db, ppl, zalo):
    from app.models import ZaloTroLy
    db.add(ZaloTroLy(id=1, enabled=True, draft_user_id=ppl['sales'].id))
    db.commit()
    linked(client, db, user=ppl['sales'])
    zalo['script'] = [call('no_khach', ma_khach='KTD-000001'), say('Em không tra công nợ được.')]
    client.post('/zalo/webhook', json=event(mid='m1', text='An Phú còn nợ bao nhiêu'), headers=H)
    assert 'no_khach' not in zalo['ai'][0]['tools']
    assert json.loads(zalo['ai'][1]['messages'][-1]['content']) == dict(loi='Không có công cụ này.')
    assert 'doanh số của tôi' in zalo['ai'][0]['messages'][0]['content']


def test_qua_zalo_ke_toan_hoi_no(client, db, ppl, zalo):
    from app.models import ZaloTroLy
    db.add(ZaloTroLy(id=1, enabled=True, draft_user_id=ppl['sales'].id))
    db.commit()
    linked(client, db, user=ppl['finance'])
    zalo['script'] = [call('tim_khach', tu_khoa='An Phú'), call('no_khach', ma_khach='KTD-000001'), say('An Phú không còn nợ.')]
    client.post('/zalo/webhook', json=event(mid='m1', text='An Phú còn nợ bao nhiêu'), headers=H)
    log = last_log(db)
    assert [t['ten'] for t in log.tools] == ['tim_khach', 'no_khach'] and log.result == 'TRA_LOI'
    assert texts(zalo)[-1] == 'An Phú không còn nợ.'
    assert db.scalar(select(func.count()).select_from(ZaloAgentLog)) >= 1

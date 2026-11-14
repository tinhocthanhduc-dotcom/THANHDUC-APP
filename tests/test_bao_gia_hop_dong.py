"""Báo giá hợp đồng sửa chữa (TUẤN duyệt mẫu H1–H3 bản 3, 14/11/2026).

Điều kiện xong: lấy mọi gói của nhóm máy từng model, chỉ các cấp đã chọn; giảm % và sửa từng ô chỉ giám đốc;
bản chụp không có giá vốn/phương án; khách ký thì máy của khách vào xưởng lấy đúng ô giá hợp đồng, ô không có trên
hợp đồng vẫn theo bảng giá chung; ngừng áp hoặc khách khác thì theo bảng giá chung; link khách không cần đăng nhập.
"""
from datetime import timedelta

import pytest
from starlette.datastructures import FormData

from app import contract_quotes as cq
from app import repair_inspection as ri
from app import zalo_agent
from app.errors import BusinessError
from app.models import ContractQuote, Customer
from test_kiem_tra_may import book, form
from test_mv1_workshop import onsite, photo  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_xg1_workshop import holding, workshop  # noqa: F401


def err(code, fn, *a, **kw):
    with pytest.raises(BusinessError) as e:
        fn(*a, **kw)
    assert e.value.code == code
    return e.value


def fd(customer='KTD-000001', models=(('canon lbp 2900', 3),), levels=('1', '2', '3', 'CH'), giam='', picks=None, extra=()):
    items = [('khach', customer), ('lv_seen', '1'), ('kt_seen', '1'), ('kiem_tra', '1'), ('hieu_luc', '30'), ('ap_thang', '12'), ('giam', giam)]
    items += [('lv', lv) for lv in levels]
    for i, (name, qty) in enumerate(models):
        items += [(f'model_{i}', name), (f'qty_{i}', str(qty))]
        if picks is not None:
            items.append((f'seen_{i}', 'Canon LBP 2900'))
            for sid, lvs in picks.items():
                items += [(f'pick_{i}_{sid}', lv) for lv in lvs]
    return cq.parse(FormData(items + list(extra)))


@pytest.fixture
def priced(db, workshop, make_user):
    p, a, b, t, eq = workshop
    boss = make_user('hd.boss', 'director')
    fuser, feed, opts = book(db, boss)
    return p, t, fuser, feed, opts, boss


def test_create_takes_all_packages_of_group_and_only_picked_levels(db, priced):
    p, t, fuser, feed, opts, boss = priced
    disp = p['dispatcher']
    err('FORBIDDEN', cq.create, db, p['technician'], fd())
    err('DIRECTOR_ONLY', cq.create, db, disp, fd(giam='10'))
    err('UNKNOWN_MODEL', cq.create, db, disp, fd(models=(('HP 9999', 1),)))
    err('LEVEL_REQUIRED', cq.create, db, disp, fd(levels=()))
    db.rollback()
    q = cq.create(db, disp, fd(levels=('1', '2', 'CH')))
    assert q.number.startswith('HD-26-') and q.status == 'CHO' and len(q.token) >= 24 and q.levels == ['1', '2', 'CH']
    m = q.snapshot['models'][0]
    assert (m['name'], m['qty']) == ('Canon LBP 2900', 3) and m['group']
    rows = {r['service_id']: r['cells'] for r in m['rows']}
    assert {k: (v or {}).get('price') for k, v in rows[fuser.id].items()} == {'1': 250000, '2': 450000, '3': None, 'CH': 600000}
    assert rows[feed.id]['1']['price'] == 150000 and rows[feed.id]['2'] is None
    raw = str(q.snapshot)
    assert 'bao lụa' not in raw and 'cost' not in raw and 'Thay rulo' not in raw
    assert q.snapshot['extras'][0]['name'].startswith('Kiểm tra') and cq.link(q) in q.text
    # Từng gói chọn cấp riêng: chỉ sấy cấp 3.
    q2 = cq.create(db, disp, fd(picks={fuser.id: ['3']}))
    assert [r['service_id'] for r in q2.snapshot['models'][0]['rows']] == [fuser.id] and q2.levels == ['3']


def test_director_discount_and_cell_edit(db, priced):
    p, t, fuser, feed, opts, boss = priced
    q = cq.create(db, boss, fd(giam='10'))
    cells = {r['service_id']: r['cells'] for r in q.snapshot['models'][0]['rows']}[fuser.id]
    assert (cells['2']['price'], cells['2']['base'], cells['2']['price'] + cells['2']['vat']) == (405000, 450000, 437400)
    # Điều phối sửa lại (giữ giảm của giám đốc) được, nhưng không đổi mức giảm.
    cq.update(db, p['dispatcher'], q.id, q.version, fd(giam='10'))
    err('DIRECTOR_ONLY', cq.update, db, p['dispatcher'], q.id, q.version, fd(giam='20'))
    db.rollback()
    err('DIRECTOR_ONLY', cq.set_cells, db, p['dispatcher'], q.id, q.version, FormData())
    db.rollback()
    q = db.get(ContractQuote, q.id)
    sid = fuser.id
    cq.set_cells(db, boss, q.id, q.version, FormData([(f'o_0_{sid}_2', '486.000')]))
    c = {r['service_id']: r['cells'] for r in db.get(ContractQuote, q.id).snapshot['models'][0]['rows']}[sid]['2']
    assert (c['price'], c['price'] + c['vat']) == (450000, 486000)


def test_signed_contract_price_applies_to_customer_machine(db, priced, monkeypatch):
    p, t, fuser, feed, opts, boss = priced
    q = cq.create(db, boss, fd(customer=t.customer_code, giam='10', levels=('1', '2', 'CH')))
    today = cq._today()
    err('BAD_DATE', cq.sign, db, p['dispatcher'], q.id, q.version, today + timedelta(days=1))
    db.rollback()
    q = cq.sign(db, p['dispatcher'], q.id, q.version, today, 'HĐ 12/2026')
    assert q.status == 'DA_KY' and q.apply_until == cq.add_months(today, 12) - timedelta(days=1)
    row = ri.save(db, p['technician'], t.id, form(fuser, opts, ['Thay bao lụa sấy']))
    out = ri.quote(db, t, row)
    ln = out['lines'][0]
    assert ln['service'].id == fuser.id
    assert (ln['std']['price'], ln['std']['source'], ln['gen']['price']) == (405000, 'hop_dong', 540000)
    assert out['contract']['number'] == q.number and out['total'] == 437400
    # Cấp 3 không có trên hợp đồng: theo bảng giá chung.
    assert cq.lookup(db, t.customer_code, fuser.id, 'Canon LBP 2900', 3)['price'] == 650000
    # Khách khác: bảng giá chung.
    assert cq.lookup(db, 'KTD-999999', fuser.id, 'Canon LBP 2900', 2)['price'] == 450000
    # Ngừng áp: về bảng giá chung; link khách không hiện giá.
    cq.stop(db, p['dispatcher'], q.id, q.version, 'Thanh lý hợp đồng')
    assert cq.lookup(db, t.customer_code, fuser.id, 'Canon LBP 2900', 2)['price'] == 450000
    assert cq.public(db, q.token)[1] == 'ngung'


def test_public_states_and_expiry(db, priced, monkeypatch):
    p, t, fuser, feed, opts, boss = priced
    q = cq.create(db, p['dispatcher'], fd())
    assert cq.public(db, 'x' * 30)[1] == 'khong_co' and cq.public(db, q.token)[1] == 'ok'
    monkeypatch.setattr(cq, '_today', lambda: q.valid_until + timedelta(days=1))
    assert cq.public(db, q.token)[1] == 'het_han'
    err('EXPIRED', cq.sign, db, p['dispatcher'], q.id, q.version, q.valid_until + timedelta(days=1))


def test_zalo_tool_repair_prices(db, priced):
    p, t, fuser, feed, opts, boss = priced
    assert zalo_agent.tool_repair_prices(db, 'Brother XYZ', '', '')['loi']
    out = zalo_agent.tool_repair_prices(db, 'Canon LBP 2900', 'say', '')
    assert out['ket_qua'][0]['gia_gom_vat'] == {'cap_1': 270000, 'cap_2': 486000, 'cap_3': 702000, 'chinh_hang_cap_2': 648000}
    q = cq.create(db, boss, fd(customer=t.customer_code, giam='10'))
    cq.sign(db, boss, q.id, q.version, cq._today())
    out = zalo_agent.tool_repair_prices(db, 'Canon LBP 2900', 'say', t.customer_code)
    assert out['ket_qua'][0]['gia_gom_vat']['cap_2'] == 437400 and out['ket_qua'][0]['gia_hop_dong'] == q.number


def test_web_flow_print_public_and_permissions(client, db, priced):
    from test_web import service_login
    p, t, fuser, feed, opts, boss = priced
    service_login(client, p['technician'])
    assert client.get('/bao-gia-hop-dong').status_code == 403
    assert 'href="/tra-gia"' in client.get('/tho').text
    csrf = service_login(client, p['dispatcher'])
    assert client.get('/bao-gia-hop-dong/moi').status_code == 200
    data = {'csrf': csrf, 'khach': t.customer_code, 'model_0': 'Canon LBP 2900', 'qty_0': '2', 'lv_seen': '1', 'kt_seen': '1',
            'lv': ['1', '2', '3', 'CH'], 'hieu_luc': '30', 'ap_thang': '12', 'giam': ''}
    page = client.post('/bao-gia-hop-dong/moi', data=dict(data, do='lay-goi'))
    assert page.status_code == 200 and 'Sửa chữa cụm sấy' in page.text and 'cụm sấy máy Canon' not in page.text and 'Chọn chung cho tất cả model' in page.text
    r = client.post('/bao-gia-hop-dong/moi', data=dict(data, do='luu'), follow_redirects=False)
    assert r.status_code == 303
    q = db.query(ContractQuote).one()
    detail = client.get(f'/bao-gia-hop-dong/{q.id}').text
    assert cq.link(q) in detail and 'Khách đã ký' in detail and 'Lưu giá từng ô' not in detail
    pr = client.get(f'/bao-gia-hop-dong/{q.id}/in')
    assert pr.status_code == 200 and 'BÁO GIÁ DỊCH VỤ SỬA CHỮA MÁY IN' in pr.text and '486.000' in pr.text and 'bao lụa' not in pr.text
    client.cookies.clear()
    pub = client.get(f'/bgh/{q.token}')
    assert pub.status_code == 200 and '486.000' in pub.text and pub.headers['cache-control'] == 'no-store'
    assert client.get('/bgh/' + 'y' * 30).status_code == 404

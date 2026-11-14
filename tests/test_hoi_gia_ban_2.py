"""Hỏi giá sửa bản 2 (TUẤN duyệt mẫu HG1–HG3, 14/11/2026).

Điều kiện xong: lưới cấp 1/2/3/chính hãng, mặc định bật cấp đã khai, bật/tắt từng ô; chỉ giám đốc sửa giá ô (giá bảng giữ
để so); hạng mục ngoài bảng giá có kiểm tra; tin ghi khoảng hoặc từng cấp và có link báo giá; sửa phiếu giữ số HG, tin đã gửi
về Chờ khách; phiếu đóng không sửa; bản in A4 và link khách không đăng nhập, hết hạn thì không hiện giá.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select
from starlette.datastructures import FormData

from app import price_book as pb
from app import quick_quotes as qq
from app.errors import BusinessError
from app.models import AuditLog, QuickQuote
from test_bao_gia_nhanh import group, setup_book
from test_tk_tai_khoan import web_login
from test_web import service_login, service_people  # noqa: F401


def err(code, fn, *a, **kw):
    with pytest.raises(BusinessError) as e:
        fn(*a, **kw)
    assert e.value.code == code
    return e.value


def opts(items):
    return qq.parse_options(FormData(items))


@pytest.fixture
def book(db, make_user):
    boss = make_user('hg2.boss', 'director')
    sales = make_user('hg2.sales', 'sales')
    fuser, feed = setup_book(db, boss)
    a4 = group(db, 'LASER_A4_DON')
    pb.save_service(db, boss, dict(id=str(fuser.id), version=str(fuser.version), kind='GOI', name='Sửa chữa cụm sấy', active='1',
                                   option_rows='0', **{f'g{a4.id}_p1': '250.000', f'g{a4.id}_p2': '450.000', f'g{a4.id}_p3': '650.000',
                                                       f'g{a4.id}_ch2': '600.000'}))
    return boss, sales, fuser, feed


def test_grid_defaults_picks_and_text_modes(db, book):
    boss, sales, fuser, feed = book
    r = qq.compute(db, 'Canon LBP 2900', ['Kẹt giấy'], 'CUA_HANG')
    lines = {ln['service_id']: ln for ln in r['lines']}
    # Đủ ô có giá; mặc định chỉ bật cấp đã khai (sấy C2, kéo giấy C1–C2).
    assert {k: (c or {}).get('price') for k, c in lines[fuser.id]['cells'].items()} == {'1': 270000, '2': 486000, '3': 702000, 'CH': 648000}
    assert [k for k, c in lines[fuser.id]['cells'].items() if c and c['on']] == ['2']
    assert lines[feed.id]['levels'] == [1, 2] and lines[feed.id]['cells']['3']['on'] is False
    # Bật C3 + chính hãng cho sấy, tắt hết kéo giấy: kéo giấy thành chưa có giá báo.
    o = opts([('cap_sid', str(fuser.id)), ('cap_sid', str(feed.id)), ('cap', f'{fuser.id}:3'), ('cap', f'{fuser.id}:CH'), ('cach', 'CAP')])
    r = qq.compute(db, 'Canon LBP 2900', ['Kẹt giấy'], 'CUA_HANG', o['picks'])
    lines = {ln['service_id']: ln for ln in r['lines']}
    assert lines[fuser.id]['levels'] == [3] and lines[fuser.id]['genuine'] and (lines[fuser.id]['low'], lines[fuser.id]['high']) == (648000, 702000)
    assert not lines[feed.id]['ok'] and qq.levels_text(lines[fuser.id]) == 'C3 + CH'
    text = qq.zalo_text(r, 'CAP', 'https://x/hg/abc')
    assert '• Sửa chữa cụm sấy: cấp 3 702.000đ · chính hãng 648.000đ' in text and 'bộ kéo giấy' not in text
    assert text.endswith('Xem báo giá chi tiết: https://x/hg/abc')
    assert '• Sửa chữa cụm sấy: 648.000 – 702.000đ' in qq.zalo_text(r, 'KHOANG')


def test_director_price_and_custom_items(db, book):
    boss, sales, fuser, feed = book
    o = opts([('gia_%d_2' % fuser.id, '420.000'), ('hm_ten', 'Thay cụm scan'), ('hm_gia', '650.000'), ('hm_bh', '3'), ('cach', 'CAP')])
    kw = dict(model_name='Canon LBP 2900', symptoms=['Kẹt giấy'], place='CUA_HANG')
    e = err('DIRECTOR_ONLY', qq.create, db, sales, options=o, **kw)
    assert e.status == 403
    db.rollback()
    # Giá đúng bằng bảng giá gửi lên từ form thì không tính là sửa.
    same = opts([('gia_%d_2' % fuser.id, '486.000')])
    assert qq.create(db, sales, options=same, **kw).lines[1]['cells']['2']['price'] == 486000
    err('BAD_ITEM', qq.create, db, boss, options=opts([('hm_ten', 'Thay scan'), ('hm_gia', '')]), **kw)
    db.rollback()
    err('BAD_ITEM', qq.create, db, boss, options=opts([('hm_ten', 'Thay scan'), ('hm_gia', '500.000'), ('hm_bh', '48')]), **kw)
    db.rollback()
    err('BAD_PRICE', qq.create, db, boss, options=opts([('gia_%d_2' % fuser.id, '5')]), **kw)
    db.rollback()
    row = qq.create(db, boss, options=o, **kw)
    cell = next(ln for ln in row.lines if ln.get('service_id') == fuser.id)['cells']['2']
    assert (cell['price'], cell['base']) == (420000, 486000) and row.price_mode == 'CAP'
    custom = [ln for ln in row.lines if ln.get('custom')]
    assert custom == [dict(custom=True, name='Thay cụm scan', price=650000, warranty=3, ok=True, low=650000, high=650000, levels=[], cells={}, reason='')]
    assert '• Thay cụm scan: 650.000đ' in row.text and 'cấp 2 420.000đ' in row.text and qq.link(row) in row.text
    log = db.scalars(select(AuditLog).where(AuditLog.action == 'quick_quote.create').order_by(AuditLog.id.desc())).first()
    assert log.after['overridden'] == [f'{fuser.id}:2']


def test_update_keeps_number_and_resets_sent(db, book):
    boss, sales, fuser, feed = book
    row = qq.create(db, sales, model_name='Canon LBP 2900', symptoms=['Kẹt giấy'], place='CUA_HANG')
    number, token = row.number, row.token
    qq.mark_sent(db, sales, row.id)
    o = opts([('cap_sid', str(fuser.id)), ('cap', f'{fuser.id}:1'), ('hm_ten', 'Thay cụm scan'), ('hm_gia', '650000'), ('cach', 'CAP')])
    err('DIRECTOR_ONLY', qq.update, db, sales, row.id, model_name='Canon LBP 2900', symptoms=['Kẹt giấy'], place='CUA_HANG',
        options=opts([('gia_%d_2' % fuser.id, '1.000.000')]))
    db.rollback()
    row = qq.update(db, sales, row.id, model_name='Canon LBP 2900', symptoms=['Kẹt giấy', 'In nhăn giấy'], place='TAN_NOI', options=o)
    assert (row.number, row.token, row.status, row.edits, row.updated_by) == (number, token, 'MOI', 1, sales.id)
    fu = next(ln for ln in row.lines if ln.get('service_id') == fuser.id)
    assert fu['levels'] == [1] and any(ln.get('custom') for ln in row.lines) and row.place == 'TAN_NOI'
    assert 'cấp 1 270.000đ' in row.text and 'Phụ phí sửa tận nơi' in row.text
    d = qq.from_row(row)
    assert d['options']['picks'][fuser.id] == {'1'} and d['options']['customs'][0]['price'] == 650000 and d['options']['mode'] == 'CAP'
    assert set(db.scalars(select(AuditLog.action).where(AuditLog.entity_id == str(row.id)))) >= {'quick_quote.update'}
    qq.decline(db, sales, row.id)
    err('BAD_STATUS', qq.update, db, sales, row.id, model_name='Canon LBP 2900', symptoms=['Kẹt giấy'], place='CUA_HANG')


def test_web_edit_print_and_public_link(client, db, service_people, monkeypatch):  # noqa: F811
    p = service_people
    fuser, feed = setup_book(db, p['director'])
    csrf = service_login(client, p['dispatcher'])
    data = dict(csrf=csrf, model='Canon LBP 2900', ht='Kẹt giấy', noi='CUA_HANG', next='', cach='CAP',
                hm_ten='Thay cụm scan', hm_gia='650.000', hm_bh='3')
    r = client.post('/hoi-gia/luu', data=data, follow_redirects=False)
    row = db.scalars(select(QuickQuote)).one()
    assert r.status_code == 303 and any(ln.get('custom') for ln in row.lines)
    detail = client.get(f'/hoi-gia/{row.id}').text
    assert f'/hoi-gia/{row.id}/sua' in detail and f'/hoi-gia/{row.id}/in' in detail and qq.link(row) in detail and 'ngoài bảng giá' in detail
    page = client.get(f'/hoi-gia/{row.id}/sua').text
    assert 'Sửa ' + row.number in page and 'value="Thay cụm scan"' in page and f'value="{fuser.id}:2" checked' in page
    assert 'name="gia_' not in page  # điều phối không sửa giá ô
    r = client.post(f'/hoi-gia/{row.id}/sua', data=dict(data, cap_sid=[str(fuser.id), str(feed.id)], cap=[f'{fuser.id}:3', f'{feed.id}:1']),
                    follow_redirects=False)
    assert r.status_code == 303
    db.expire_all()
    row = db.get(QuickQuote, row.id)
    assert row.edits == 1 and 'cấp 3 702.000đ' in row.text
    pr = client.get(f'/hoi-gia/{row.id}/in')
    assert pr.status_code == 200 and 'BÁO GIÁ DỊCH VỤ SỬA CHỮA MÁY IN' in pr.text and '702.000' in pr.text and 'Thay cụm scan' in pr.text
    assert 'cụm sấy máy Canon' not in pr.text
    client.cookies.clear()
    pub = client.get(f'/hg/{row.token}')
    assert pub.status_code == 200 and '702.000' in pub.text and pub.headers['cache-control'] == 'no-store' and 'class="back"' not in pub.text
    assert client.get('/hg/' + 'z' * 30).status_code == 404
    monkeypatch.setattr(qq, '_now', lambda: qq.datetime.now(qq.TZ) + timedelta(days=qq.VALID_DAYS + 2))
    gone = client.get(f'/hg/{row.token}')
    assert gone.status_code == 410 and '702.000' not in gone.text
    # Giám đốc thấy ô sửa giá.
    monkeypatch.undo()
    service_login(client, p['director'])
    assert f'name="gia_{fuser.id}_2"' in client.get(f'/hoi-gia/{row.id}/sua').text
    for role in ('technician', 'warehouse'):
        web_login(client, p[role])
        assert client.get(f'/hoi-gia/{row.id}/in', follow_redirects=False).status_code == 403

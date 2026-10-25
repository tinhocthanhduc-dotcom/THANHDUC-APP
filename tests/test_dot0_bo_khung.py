"""Đợt 0: bộ khung dùng chung (nút theo nghĩa màu, 3 khổ trang, macro đầu trang/ô số/lọc/nhãn/tổng/bước/trống).

Test quét giữ cho màn mới lắp từ bộ khung thay vì tự vẽ lại đầu trang và nút kiểu riêng."""
import re
from pathlib import Path

from app.web import nav
from test_tk_tai_khoan import web_login

ROOT = Path(__file__).resolve().parents[1] / 'app' / 'web'
TEMPLATES = ROOT / 'templates'
CSS = (ROOT / 'static' / 'app.css').read_text()
KIT = ('badge', 'page_head', 'tiles', 'filter_chips', 'filter_bar', 'totals', 'steps', 'next_box', 'empty', 'toast')
# Màn cũ còn tự vẽ <h1>. Mỗi đợt chuyển màn sang page_head thì xóa tên khỏi đây; màn mới không được thêm vào.
LEGACY_H1 = frozenset((
    'bao_cao_ban_hang.jinja', 'bao_cao_cho_xu_ly.jinja', 'bao_cao_mua_hang.jinja', 'bao_cao_tai_chinh.jinja',
    'bao_gia_ds.jinja', 'bao_gia_form.jinja', 'bao_gia_in.jinja', 'bao_thu_chi_tiet.jinja',
    'bao_thu_ds.jinja', 'ca_nhan.jinja',
    'cho_xu_ly_chi_tiet.jinja', 'cho_xu_ly_chuyen.jinja',
    'cho_xu_ly_duyet_huy.jinja', 'cho_xu_ly_duyet_tra_ncc.jinja', 'chot_so.jinja',
    'cong_no_ncc_chi_tiet.jinja', 'cong_no_ncc_ds.jinja', 'dang_nhap.jinja', 'danh_muc_ds.jinja',
    'danh_muc_form.jinja', 'dich_vu_bien_ban.jinja', 'dich_vu_can_thu.jinja',
    'dieu_chinh.jinja',
    'dieu_phoi_bao_cao.jinja', 'doi_mat_khau.jinja', 'don_ban_can_thu.jinja', 'don_ban_ds.jinja',
    'don_ban_form.jinja', 'don_ban_in.jinja', 'don_ban_tu_bao_gia.jinja',
    'don_mua_form.jinja', 'don_mua_in.jinja', 'don_mua_nhan_trang.jinja', 'don_mua_tra_form.jinja',
    'ghep_tai_chinh.jinja', 'giao_don.jinja', 'giu_may.jinja', 'hang_form.jinja',
    'hang_lich_su.jinja', 'hang_phieu_ck.jinja', 'hang_toi_giu.jinja', 'ho_so_cua_toi.jinja',
    'hoan_tien_ds.jinja', 'kho_giao.jinja',
    'loi.jinja',
    'nhan_su_chi_tiet.jinja',
    'nhan_su_moi.jinja', 'nhom_hang.jinja', 
    'so_quy.jinja', 
    'the_kho.jinja', 'thiet_bi_form.jinja', 'tho_giao_don.jinja', 'tho_hom_nay.jinja',
    'tho_phieu.jinja', 'tho_tra_hang.jinja', 'tho_tra_hang_moi.jinja', 'thu_chi_in.jinja', 'tien_toi_giu.jinja', 'tom_tat_ngay.jinja',
    'tom_tat_ngay_list.jinja', 'ton_dau_ky.jinja', 'ton_dau_ky_ket_qua.jinja', 'ton_ds.jinja',
    'tong_quan.jinja', 'tra_hang_chi_tiet.jinja', 'tra_hang_form.jinja', 'tra_hang_in.jinja',
    'xin_linh_kien.jinja',
))


def _rule(selector):
    m = re.search(r'(?m)^' + re.escape(selector) + r' \{([^}]*)\}', CSS)
    assert m, selector
    return m.group(1)


def test_kit_macros_and_classes_exist():
    ui = (TEMPLATES / '_ui.jinja').read_text()
    for name in KIT:
        assert '{% macro ' + name + '(' in ui, name
    for cls in ('.page-wide', '.page-mid', '.page-head', '.badge.b-cho', '.badge.b-xong', '.badge.b-huy', '.tiles',
                '.filter-bar', '.filter-chip', '.totals', '.doc-grid', '.steps-bar', '.next-box', '.empty-state', '.list-table'):
        assert cls in CSS, cls
    # Ba khổ: rộng 1200 (+ lề), vừa 720 (+ lề); khổ thợ giữ riêng.
    assert 'main:has(> .page-wide) { max-width: 1232px; }' in CSS
    assert 'main:has(> .page-mid) { max-width: 752px; }' in CSS


def test_one_button_system_with_colour_meaning():
    base = _rule('button, .btn')
    assert 'min-height: 44px' in base and 'border-radius: 12px' in base
    # Mở màn khác (a.primary) xanh dương; nút gửi form (button.primary) xanh lá: nút chính ở thanh bước tự theo luật.
    assert 'var(--info-solid)' in _rule('.primary')
    assert 'var(--ok-solid)' in _rule('button.primary')
    assert '--info-bg:' in CSS


def test_templates_follow_button_rules():
    bad = []
    for path in TEMPLATES.glob('*.jinja'):
        html = path.read_text()
        # Nút "+ Tạo…" là mở việc mới: xanh dương, không xanh lá.
        bad += [f'{path.name}: {m}' for m in re.findall(r'<a [^>]*class="[^"]*\bbtn-ok\b[^"]*"[^>]*>\s*\+[^<]*', html)]
        # Lọc, Xem, Tải CSV là việc phụ: viền xám, không tô màu.
        bad += [f'{path.name}: {m}' for m in re.findall(r'<(?:a|button) [^>]*class="[^"]*\bbtn-(?:info|ok)\b[^"]*"[^>]*>\s*(?:Lọc|Xem|Tải CSV)\s*<', html)]
        # Không tô màu nút bằng style riêng.
        bad += [f'{path.name}: {m}' for m in re.findall(r'<(?:a|button)\b[^>]*style="[^"]*background[^>]*>', html)]
    assert not bad, bad


def test_new_screens_use_page_head():
    raw = {p.name for p in TEMPLATES.glob('*.jinja') if '<h1' in p.read_text()} - {'base.jinja', '_ui.jinja'}
    new = raw - LEGACY_H1
    assert not new, f'Màn mới hãy dùng page_head trong _ui.jinja thay vì tự vẽ <h1>: {sorted(new)}'
    moved = LEGACY_H1 - raw
    assert not moved, f'Đã chuyển sang page_head, xóa khỏi LEGACY_H1: {sorted(moved)}'


def test_guide_shows_the_kit(client, make_user):
    web_login(client, make_user('dot0.director', 'director'))
    html = client.get('/ui/quy-chuan').text
    assert '<div class="page-wide">' in html and '<header class="page-head">' in html
    for marker in ('class="tiles"', 'class="tile on t-cho"', 'class="filter-bar" method="get" action="/ui/quy-chuan" data-autofilter',
                   'class="filter-chip"', '<ol class="steps-bar"', 'aria-current="step">Giao hàng', 'class="card next-box"',
                   '<dl class="totals">', 'class="tot-due"', 'class="badge b-huy"', 'class="empty-state"', 'class="btn-new"'):
        assert marker in html, marker
    assert 'data-toast=' not in html


def test_macros_render():
    from app.web import templates
    ui = templates.env.get_template('_ui.jinja').module
    assert str(ui.badge('Chờ nhận', 'cho')) == '<span class="badge b-cho">Chờ nhận</span>'
    head = str(ui.page_head('DB-26-00005', 'Đơn bán', back='/don-ban', badge_label='Đã chốt', badge_tone='lam'))
    assert 'href="/don-ban" aria-label="Quay lại"' in head and '<h1>DB-26-00005</h1><span class="badge b-lam">Đã chốt</span>' in head
    assert 'ph-actions' not in head
    assert str(ui.filter_chips([], '/x')) == ''
    assert '<li class="done">✓ Nháp</li><li class="cur" aria-current="step">Giao</li>' in str(ui.steps([('Nháp', 'done'), ('Giao', 'cur')]))
    assert str(ui.toast('Đã chốt DB-26-00005')) == '<span hidden data-toast="Đã chốt DB-26-00005"></span>'


def test_filter_bar_autosubmits_selects_not_search():
    js = (ROOT / 'static' / 'ui.js').read_text()
    assert "hasAttribute('data-autofilter')" in js and "event.target.type !== 'search'" in js


def test_kho_giao_lights_my_work_and_keeps_warehouse_only(make_user):
    sales, warehouse = make_user('dot0.sales', 'sales'), make_user('dot0.kho', 'warehouse')
    finance = make_user('dot0.ketoan', 'finance')
    groups = nav.menu(warehouse, '/kho/giao/5')['groups']
    active = [item['label'] for group in groups for item in group['items'] if item['active']]
    assert active == ['Việc của tôi']
    assert all(item['label'] != 'Giao hàng' for group in groups for item in group['items'])
    # Đợt 1b: kinh doanh dùng chung màn giao; kế toán thì không.
    assert nav.allowed(warehouse, '/kho/giao/5') and nav.allowed(sales, '/kho/giao/5') and not nav.allowed(finance, '/kho/giao/5')
    assert not any(item['active'] for group in nav.menu(warehouse, '/kho')['groups'] for item in group['items'] if item['label'] == 'Việc của tôi')

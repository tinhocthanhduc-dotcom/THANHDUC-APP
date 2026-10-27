"""UI0: quy chuẩn giao diện chung (màu, nút, vuốt, toast, ô tiền, lỗi đúng ô)."""
import re
from pathlib import Path

import pytest

from app.errors import BusinessError
from app.web.money_input import parse_money
from test_tk_tai_khoan import web_login

STATIC = Path(__file__).resolve().parents[1] / 'app' / 'web' / 'static'
TEMPLATES = Path(__file__).resolve().parents[1] / 'app' / 'web' / 'templates'
TOKENS = ('--bg', '--surface', '--text', '--muted', '--line', '--ok-solid', '--ok-solid-text', '--bad-solid',
          '--bad-solid-text', '--info-solid', '--info-solid-text', '--warn-solid', '--warn-solid-text')


@pytest.mark.parametrize('raw,value', [('540.000', 540000), ('540000', 540000), ('540.000 đ', 540000),
                                       ('1.234.567 đ', 1234567), ('0', 0), (' 12.000đ ', 12000)])
def test_parse_money_ok(raw, value):
    assert parse_money(raw) == value


@pytest.mark.parametrize('raw,code', [('', 'MONEY_REQUIRED'), ('abc', 'MONEY_FORMAT'), ('-5.000', 'MONEY_NEGATIVE'),
                                      ('1,5', 'MONEY_FORMAT'), ('54.00', 'MONEY_FORMAT')])
def test_parse_money_rejects_with_vietnamese_sentence(raw, code):
    with pytest.raises(BusinessError) as exc:
        parse_money(raw, 'Tiền thu khách')
    assert exc.value.code == code
    assert exc.value.message.startswith('Tiền thu khách') and code not in exc.value.message


def test_parse_money_zero_can_be_blocked():
    with pytest.raises(BusinessError) as exc:
        parse_money('0', allow_zero=False)
    assert exc.value.code == 'MONEY_ZERO'


def test_css_has_tokens_for_light_and_dark():
    css = (STATIC / 'app.css').read_text()
    root = css[css.index(':root {'):css.index('}', css.index(':root {'))]
    dark = css[css.index(':root[data-theme="dark"] {'):]
    dark = dark[:dark.index('}')]
    for token in TOKENS:
        assert token + ':' in root, token
        assert token + ':' in dark, token
    assert ':root:not([data-theme="light"])' in css


@pytest.mark.parametrize('role', ['technician', 'warehouse', 'finance', 'admin'])
def test_guide_only_for_director(client, make_user, role):
    web_login(client, make_user('ui0.' + role, role))
    assert client.get('/ui/quy-chuan').status_code == 403


def test_guide_renders_components(client, make_user):
    web_login(client, make_user('ui0.director', 'director'))
    r = client.get('/ui/quy-chuan')
    assert r.status_code == 200
    html = r.text
    # Template gốc: chọn Sáng/Tối trước khi vẽ, nạp ui.js, có chỗ hiện toast.
    head = html[:html.index('</head>')]
    assert "localStorage.getItem('td-theme')" in head and '/static/ui.js' in head
    assert 'data-theme-toggle' in html and 'data-toast-stack' in html
    # Vuốt: luôn có nút submit thật bên trong để không JS vẫn dùng được.
    swipes = re.findall(r'<div class="swipe[^"]*" data-swipe[^>]*>\s*<button type="submit"', html)
    assert len(swipes) == 3
    # Chip là input thật; ô tiền là ô chữ kiểu số, không phải type=number.
    assert re.search(r'<label class="chip"><input type="radio"', html)
    assert re.search(r'<label class="chip"><input type="checkbox"', html)
    money = re.search(r'<input name="so_tien"[^>]*>', html).group(0)
    assert 'inputmode="numeric"' in money and 'data-money' in money and 'type="number"' not in money
    assert 'class="pick-list"' in html and 'data-step="1"' in html


def test_guide_form_marks_only_wrong_field_and_keeps_values(client, make_user):
    web_login(client, make_user('ui0.director2', 'director'))
    r = client.get('/ui/quy-chuan', params=dict(thu='1', ten='Công ty Minh Phát', so_tien='abc'))
    assert r.status_code == 200
    html = r.text
    ten = re.search(r'<label class="([^"]*)">Tên khách<input name="ten" value="([^"]*)"', html)
    assert 'field-invalid' not in ten.group(1) and ten.group(2) == 'Công ty Minh Phát'
    money = re.search(r'<label class="([^"]*)">Tiền thu khách<input name="so_tien" value="([^"]*)"', html)
    assert 'field-invalid' in money.group(1) and money.group(2) == 'abc'
    assert 'id="loi-so_tien">Tiền thu khách chỉ gồm chữ số, ví dụ 540.000.</small>' in html
    assert 'id="loi-ten"' not in html and 'data-toast=' not in html
    ok = client.get('/ui/quy-chuan', params=dict(thu='1', ten='A', so_tien='540.000')).text
    assert 'field-invalid' not in ok and 'data-toast="Đã ghi nhận (trang mẫu, không lưu)"' in ok


def test_ui_js_rules():
    js = (STATIC / 'ui.js').read_text()
    # Chỉ một cử chỉ trong app: vuốt sang phải để xác nhận; không có xử lý vuốt sang trái.
    assert 'requestSubmit' in js and 'swipeleft' not in js.lower()
    assert "event.key === 'Escape'" in js and "event.key === '/'" in js


def test_swipe_drags_from_whole_bar_and_explains_lock():
    # TUẤN 27/10 "nhiều lúc không kéo được": kéo từ cả thanh, khóa thì báo mục còn thiếu, iOS 15 vẫn xám nút tròn.
    js = (STATIC / 'ui.js').read_text()
    css = (STATIC / 'app.css').read_text()
    assert "box.addEventListener('pointerdown'" in js and "knob.addEventListener('pointerdown'" not in js
    assert "'Còn thiếu: '" in js and "[data-check][data-ok=\"0\"]" in js
    assert 'swipe-locked' in js and '.swipe.swipe-locked .swipe-knob' in css
    assert '.swipe.swipe-on > button { pointer-events: none; }' in css and 'touch-action: pan-y' in css


def test_post_forms_share_double_submit_guard():
    # Chặn bấm hai lần (và vòng xoay CSS) nằm chung ở base.jinja cho mọi form POST.
    base = (TEMPLATES / 'base.jinja').read_text()
    assert 'form.dataset.submitting' in base
    assert 'form[data-submitting] button:disabled::before' in (STATIC / 'app.css').read_text()

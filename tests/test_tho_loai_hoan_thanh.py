"""Lỗi chạy thử 25/10: thợ chọn Loại dịch vụ (lưu tại chỗ) nhưng mục "Đã chọn loại dịch vụ" vẫn ✗, khóa vuốt Hoàn thành.
Đã kiểm trên Chromium: chọn loại → service-kind.js phát service-kind-saved → technician-completion.js đánh ✓ và mở nút."""
from pathlib import Path

from test_my_work import people, purchase_people  # noqa: F401
from test_technician_stock import held, transfer, balance  # noqa: F401
from test_ux1b_hoan_thanh_gop import job  # noqa: F401
from test_web import service_login

STATIC = Path(__file__).resolve().parents[1] / 'app' / 'web' / 'static'


def test_kind_saved_event_updates_completion_check():
    kind = (STATIC / 'service-kind.js').read_text(encoding='utf-8')
    done = (STATIC / 'technician-completion.js').read_text(encoding='utf-8')
    assert "new CustomEvent('service-kind-saved'" in kind
    listener = done.index('"service-kind-saved"')
    assert '[data-check=kind]' in done[listener:listener + 300] and 'update()' in done[listener:listener + 400]


def test_page_without_kind_has_inline_select_and_kind_check(client, db, job):  # noqa: F811
    p, _, t = job
    t.kind = None; db.commit()
    service_login(client, p['technician'])
    page = client.get(f'/tho/phieu/{t.id}').text
    assert 'data-kind-inline' in page and 'data-check="kind"' in page and 'data-ok="0"' in page
    assert page.index('service-kind.js') < page.index('technician-completion.js')


def test_tho_kind_change_saves_draft_then_reloads():
    """TUẤN 27/10/2026: chọn loại dịch vụ ở màn thợ mà nội dung không đổi theo loại (lưu tại chỗ, không tải lại).
    Màn thợ giờ lưu nháp rồi tải lại để hiện đúng nội dung của loại vừa chọn."""
    kind = (STATIC / 'service-kind.js').read_text(encoding='utf-8')
    report = (STATIC / 'technician-report.js').read_text(encoding='utf-8')
    reload = kind.index("hasAttribute('data-kind-reload')")
    assert 'thoSaveDraft' in kind[reload:reload + 400] and 'location.reload()' in kind[reload:reload + 500]
    assert 'window.thoSaveDraft' in report and 'return fetch(' in report


def test_only_technician_kind_select_reloads(client, db, job):  # noqa: F811
    p, _, t = job
    t.kind = None; db.commit()
    service_login(client, p['technician'])
    assert 'data-kind-reload' in client.get(f'/tho/phieu/{t.id}').text
    service_login(client, p['dispatcher'])
    page = client.get(f'/dich-vu/{t.id}').text
    assert 'data-kind-inline' in page and 'data-kind-reload' not in page

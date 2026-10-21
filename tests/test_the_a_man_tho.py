"""Thẻ A: màn thợ theo quy chuẩn UI0 (vuốt, thanh đáy, chip lý do, tick nhiều mã)."""
import re

from sqlalchemy import select

from app import part_requests as svc
from app.errors import BusinessError
from app.models import AuditLog, PartRequest
from app.schemas import PartStockIn
from test_my_work import people, purchase_people, ticket  # noqa: F401  (fixtures)
from test_technician_stock import held, transfer  # noqa: F401
from test_web import service_login


def swipe_buttons(html):
    return re.findall(r'<div class="swipe" data-swipe data-tone="(\w+)"[^>]*><button[^>]*type="submit"[^>]*>([^<]*)</button>', html)


def test_working_ticket_has_bottom_bar_and_swipe_to_complete(client, db, held):
    p, a, b = held
    t = ticket(db, p, 'DANG_LAM')
    service_login(client, p['technician'])
    html = client.get(f'/tho/phieu/{t.id}').text
    bar = html[html.index('<div class="tho-bottom-action">'):]
    # Thanh đáy: Gọi khách / Việc khác, rồi nút vuốt Hoàn thành có nút submit thật.
    assert 'data-open-details' in bar and 'href="#viec-khac"' in bar and 'id="viec-khac"' in html
    tone, label = swipe_buttons(bar)[0]
    assert tone == 'ok' and label == 'Vuốt để Hoàn thành'
    primary = re.search(r'<button[^>]*data-tho-primary[^>]*>', bar).group(0)
    assert 'form="service-complete"' in primary and 'disabled' in primary
    # Thông tin khách chỉ hiển thị; lỗi khách báo nổi bật.
    assert 'thợ không sửa' in html and 'tho-symptom' in html
    # Lý do tạm dừng là chip radio thật, không còn select.
    assert '<select name="hold_reason"' not in html
    assert re.search(r'<label class="chip"><input type="radio" name="hold_reason" value="\w+" required', html)
    # Không làm được → Đóng phiếu phải vuốt (đỏ); chuyển thợ cũng vuốt (xanh dương).
    tones = {label: tone for tone, label in swipe_buttons(html)}
    assert tones.get('Vuốt để đóng phiếu: Không làm được') == 'danger'
    assert tones.get('Vuốt để Chuyển thợ') == 'info'
    # Chip điền nhanh lý do cho ô ghi chú, ô lý do miễn phí và lý do chuyển thợ.
    assert 'data-fill="note"' in html and 'data-fill="free_reason"' in html and 'data-fill="reason"' in html


def test_assigned_ticket_primary_is_tap_not_swipe(client, db, held):
    p, a, b = held
    t = ticket(db, p, 'DA_GIAO')
    service_login(client, p['technician'])
    html = client.get(f'/tho/phieu/{t.id}').text
    bar = html[html.index('<div class="tho-bottom-action">'):]
    primary = re.search(r'<button[^>]*data-tho-primary[^>]*>([^<]*)</button>', bar)
    assert primary.group(1) == 'Nhận đơn' and 'btn-ok' in primary.group(0)
    assert not re.search(r'data-swipe[^>]*><button[^>]*data-tho-primary', html)


def test_today_list_highlights_symptom(client, db, held):
    p, a, b = held
    ticket(db, p, 'DA_GIAO')
    service_login(client, p['technician'])
    html = client.get('/tho').text
    assert 'tho-symptom' in html and 'Máy bị kẹt giấy' in html


def test_return_screen_is_tick_list(client, db, held):
    p, a, b = held
    transfer(db, p)
    service_login(client, p['technician'])
    html = client.get('/tho-tra-hang/moi').text
    assert 'data-return-pick' in html and 'Trả mã này' in html
    qty = re.search(r'<input name="return_qty"[^>]*>', html).group(0)
    assert 'min="0"' in qty
    assert 'data-fill-sel="[data-damage-reason]"' in html
    # Số lượng hiện "3" (không phải "3.000" dễ đọc nhầm thành ba nghìn).
    assert 'data-held="3"' in html and 'Đang giữ 3<' in html
    # Ô tick phát 'input' trước 'change': phải bỏ qua, nếu không tick sẽ tự bật lại thành bỏ tick.
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / 'app' / 'web' / 'static' / 'technician-returns.js').read_text()
    assert "if(event.target.closest('[data-return-pick]'))return;" in js


def test_many_parts_in_one_submit(client, db, held):
    p, a, b = held
    csrf = service_login(client, p['technician'])
    r = client.post('/hang-toi-giu/xin-linh-kien', data=dict(csrf=csrf, product_code=['TD-0001', 'TD-0002'],
                    qty=['2', '1'], reason='Mang sẵn tuần này'), follow_redirects=False)
    assert r.status_code == 303
    rows = list(db.scalars(select(PartRequest).order_by(PartRequest.id)))
    assert [(r.product_code, int(r.qty), r.reason, r.ticket_id) for r in rows] == [
        ('TD-0001', 2, 'Mang sẵn tuần này', None), ('TD-0002', 1, 'Mang sẵn tuần này', None)]
    page = client.get('/hang-toi-giu/xin-linh-kien').text
    assert 'data-part-multi' in page and 'data-part-line-template' in page


def test_many_parts_one_bad_code_creates_nothing_and_keeps_lines(client, db, held):
    p, a, b = held
    csrf = service_login(client, p['technician'])
    r = client.post('/hang-toi-giu/xin-linh-kien', data=dict(csrf=csrf, product_code=['TD-0001', 'KHONG-CO'],
                    qty=['2', '1'], reason='Mang sẵn'))
    assert r.status_code == 400 and 'KHONG-CO' in r.text and 'không tồn tại' in r.text
    assert db.scalar(select(PartRequest)) is None
    assert db.scalar(select(AuditLog).where(AuditLog.action == 'part_request.create')) is None
    # Giữ nguyên các dòng đã chọn để sửa tiếp.
    assert 'value="TD-0001"' in r.text and 'value="KHONG-CO"' in r.text


def test_many_parts_duplicate_and_limits(db, held):
    p, a, b = held
    body = PartStockIn(product_code='TD-0001', qty=1, reason='Mang sẵn')
    for bodies, code in (([body, body], 'DUPLICATE_PRODUCT'), ([], 'LINES_REQUIRED'),
                         ([PartStockIn(product_code=f'TD-{i:04d}', qty=1, reason='x') for i in range(21)], 'TOO_MANY_LINES')):
        try:
            svc.create_stock_many(db, p['technician'], bodies)
        except BusinessError as exc:
            assert exc.code == code
        else:
            raise AssertionError(code)
    assert db.scalar(select(PartRequest)) is None


def test_ui_js_fill_and_open_details():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / 'app' / 'web' / 'static' / 'ui.js').read_text()
    assert '[data-fill-value]' in js and '[data-open-details]' in js


def test_technician_shell_tabs_and_grouped_cards(client, db, held):
    # Theo bản mẫu: thanh trên gọn, tab dưới; Hôm nay chia nhóm, cả thẻ là một liên kết mở phiếu.
    p, a, b = held
    working = ticket(db, p, 'DANG_LAM'); new = ticket(db, p, 'DA_GIAO')
    service_login(client, p['technician'])
    html = client.get('/tho').text
    assert 'class="tho-app"' in html and 'class="tho-tabs"' in html and 'data-web-menu' not in html
    assert html.index('>Đang làm</h2>') < html.index(working.number) < html.index('>Sắp tới</h2>') < html.index(new.number)
    card = re.search(rf'<a class="card tho-job[^"]*" href="/tho/phieu/{new.id}"[^>]*>', html).group(0)
    assert 'data-next-label="Nhận đơn"' in card
    page = client.get(f'/tho/phieu/{working.id}').text
    assert 'class="tho-back" href="/tho"' in page and 'class="tho-tabs"' not in page
    # Người không phải thợ thuần vẫn dùng khung cũ.
    service_login(client, p['dispatcher'])
    assert 'class="tho-tabs"' not in client.get('/dich-vu').text


def test_ux5a_completion_shows_only_issued_or_chosen_materials(client, db, held):
    # Thợ giữ nhiều mã: chỉ mã đã cấp cho phiếu hiện sẵn, mã khác ẩn sau ô tìm.
    from app import stock_docs, stock_posting
    from app.schemas import PartRequestIn, StockDocIn
    from test_my_work import DAY
    p, a, b = held
    doc = stock_docs.create(db, p['warehouse'], StockDocIn(kind='PN', warehouse_code='K58', supplier_code='NCC-0001', doc_date=DAY,
        lines=[dict(product_code='TD-0002', qty=5, unit_cost=1000)]))
    stock_posting.post(db, p['warehouse'], doc.id, doc.version)
    transfer(db, p)
    d = stock_docs.create(db, p['warehouse'], StockDocIn(kind='CK', warehouse_code='K58', to_warehouse_code='K501', doc_date=DAY,
        lines=[dict(product_code='TD-0002', qty=2)]))
    stock_posting.post(db, p['warehouse'], d.id, d.version)
    t = ticket(db, p, 'DANG_LAM')
    r = svc.create(db, p['technician'], t.id, PartRequestIn(version=t.version, product_code='TD-0001', qty=1, reason='Thay drum'))
    svc.supply(db, p['warehouse'], r.id, 'K58', r.version)
    db.refresh(t); t.status = 'DANG_LAM'; db.commit()  # thợ đã nhận linh kiện, làm tiếp
    service_login(client, p['technician'])
    html = client.get(f'/tho/phieu/{t.id}').text
    rows = dict(re.findall(r'(<div class="tech-material"[^>]*>)<input type="hidden" name="material_product" value="([^"]+)"', html))
    rows = {code: tag for tag, code in rows.items()}
    assert 'data-pick-extra' not in rows['TD-0001'] and 'data-pick-extra' in rows['TD-0002']
    assert 'Đã cấp cho phiếu 1' in html and 'data-item-picker' in html and 'data-pick-search' in html
    # Mã đã cấp vẫn bắt đầu với 0: thợ tự xác nhận số đã lắp, không tự trừ kho.
    assert re.search(r'value="TD-0001"><span>.*?name="material_qty" value="0"', html)
    # Mã đã cấp đứng trước mã tìm thêm.
    assert html.index('value="TD-0001"') < html.index('value="TD-0002"')


def test_ux5a_return_rows_hidden_until_picked(client, db, held):
    p, a, b = held
    transfer(db, p)
    service_login(client, p['technician'])
    html = client.get('/tho-tra-hang/moi').text
    row = re.search(r'<fieldset class="tho-ret"[^>]*>', html).group(0)
    assert 'data-pick-row' in row and 'data-pick-extra' in row and 'Thêm mã cần trả' in html
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / 'app' / 'web' / 'static'
    ui = (static / 'ui.js').read_text()
    assert '[data-item-picker]' in ui and "normalize('NFD')" in ui
    assert "addEventListener('item-picked'" in (static / 'technician-returns.js').read_text()


def test_today_reminds_technician_to_clock_in_without_blocking(client, db, held, monkeypatch):
    from app import attendance, employees, service_tickets
    p, a, b = held
    tech = p['technician']
    ticket(db, p, 'DA_GIAO')
    service_login(client, tech)
    # Chưa có hồ sơ nhân sự: không nhắc (chưa chấm công được).
    assert 'data-not-clocked-in' not in client.get('/tho').text
    employees.create(db, p['director'], tech.id, dict(position='Kỹ thuật', start_date='2026-01-01'))
    html = client.get('/tho').text
    # Chỉ nhắc: vẫn thấy việc và vẫn có nút Nhận đơn.
    assert 'data-not-clocked-in' in html and 'href="/cham-cong"' in html and 'Nhận đơn' in html
    monkeypatch.setattr(attendance, '_now', service_tickets._now)
    attendance.punch(db, tech, 'IN')
    assert 'data-not-clocked-in' not in client.get('/tho').text

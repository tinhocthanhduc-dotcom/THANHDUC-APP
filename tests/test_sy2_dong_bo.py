"""SY2 (báo cáo đồng bộ 25/10/2026): F7 bước tiếp phiếu HOAN_THANH theo AD1, trạng thái chờ hoa hồng, F8 khung cuộn bảng lương."""
from pathlib import Path
from app import attachments, service_tickets
from app.web.customer_overview import _service_next
from test_my_work import people, purchase_people, ticket  # noqa: F401
from test_technician_stock import held, transfer, charges  # noqa: F401
from test_recovered_parts import recover
from test_web import service_login, d5_image  # noqa: F401
from test_tk_tai_khoan import web_login
from test_ns4_commissions import p as commission_people  # noqa: F401


def _finished_with_recovery(db, p):
    transfer(db, p, qty=2); t = ticket(db, p, 'DANG_LAM'); charges(db, p, t); r = recover(db, p, t)
    attachments.add(db, p['technician'], 'service_ticket', str(t.id), 'SAU', 'sau.png', d5_image())
    service_tickets.transition(db, p['technician'], t.id, 'HOAN_THANH', t.version)
    db.expire_all(); db.refresh(t)
    assert t.status == 'HOAN_THANH' and r.status == 'CHO_NOP'
    return t, r


def test_f7_next_step_names_missing_auto_close_condition(client, db, held):
    p, _, _ = held
    t, _ = _finished_with_recovery(db, p)
    names = {p['technician'].id: 'Thợ A'}
    who, title = _service_next(t, names, db)
    assert who == 'Thợ A' and title == 'Nộp linh kiện thu hồi cho kho, phiếu tự đóng'
    assert 'tiền' not in title.lower()
    # Các màn dùng chung: điều phối, trang khách, chi tiết phiếu đều nói cùng một câu; câu cũ biến mất.
    service_login(client, p['dispatcher'])
    for url in ('/dieu-phoi', f'/khach-hang/{t.customer_code}', f'/dich-vu/{t.id}'):
        page = client.get(url).text
        assert 'Kiểm tra tiền rồi đóng phiếu' not in page, url
    assert 'Nộp linh kiện thu hồi cho kho, phiếu tự đóng' in client.get(f'/khach-hang/{t.customer_code}?tab=dich-vu').text
    # Không có session: câu chung, vẫn không nhắc tiền.
    assert _service_next(t, names) == ('Thợ A', 'Trả máy, nộp thu hồi để phiếu tự đóng')


def test_commission_finance_without_locked_month_sees_waiting_state(client, db, commission_people):
    p = commission_people
    web_login(client, p['finance'])
    page = client.get('/hoa-hong/thang')
    assert page.status_code == 200 and 'Chưa có tháng hoa hồng nào được khóa' in page.text and 'Khóa tháng' not in page.text
    # Chỉ định tháng chưa khóa vẫn 403 (không mở quyền xem bản chưa duyệt).
    assert client.get('/hoa-hong/thang?thang=2027-01').status_code == 403
    assert client.get('/hoa-hong/thang.csv').status_code == 403
    # Giám đốc không đổi: vẫn vào bảng nháp của tháng hiện tại.
    web_login(client, p['director'])
    page = client.get('/hoa-hong/thang')
    assert page.status_code == 200 and 'Chưa có tháng hoa hồng nào được khóa' not in page.text


def test_f8_scroll_frames_are_positioned():
    css = Path('app/web/static/app.css').read_text(encoding='utf-8')
    for cls in ('.payroll-scroll{', '.commission-scroll{', '.table-scroll {'):
        rule = css[css.index(cls):]
        assert 'position' in rule[:rule.index('}')] and 'relative' in rule[:rule.index('}')], cls

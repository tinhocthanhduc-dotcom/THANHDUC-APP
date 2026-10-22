"""Hàng tôi giữ: danh sách dài chỉ hiện việc đang chờ + phiếu xong gần đây; phần còn lại ở trang lịch sử."""
import re

from sqlalchemy import text

from app import part_requests as svc
from test_my_work import people, purchase_people  # noqa: F401  (fixtures)
from test_technician_stock import held  # noqa: F401
from test_lk_xin_mang_theo import ask
from test_web import service_login


def numbers(html):
    return re.findall(r'<strong>(YC-[0-9-]+)</strong>', html)


def test_main_page_keeps_waiting_and_recent_done_only(client, db, held):
    p, a, b = held
    rows = [ask(db, p) for _ in range(9)]
    for r in rows[:7]:
        svc.cancel(db, p['technician'], r.id, 'Không cần nữa', r.version)
    # 3 phiếu đã hủy từ 10 ngày trước: không còn trên màn chính.
    db.execute(text('update part_requests set created_at=now()-interval \'10 days\' where id in (:a,:b,:c)'),
               dict(a=rows[0].id, b=rows[1].id, c=rows[2].id))
    db.commit()
    service_login(client, p['technician'])
    html = client.get('/hang-toi-giu').text
    shown = numbers(html)
    # 2 chờ cấp luôn hiện; đã xong gần đây tối đa 5 (ở đây còn 4 trong 7 ngày).
    assert rows[7].number in shown and rows[8].number in shown and len(shown) == 6
    assert rows[0].number not in shown and 'Yêu cầu linh kiện · 9' in html
    assert 'href="/hang-toi-giu/lich-su?loai=yeu-cau">Xem lịch sử (9)' in html


def test_history_pages_and_month_filter(client, db, held):
    p, a, b = held
    rows = [ask(db, p) for _ in range(23)]
    db.execute(text("update part_requests set created_at='2026-08-15 03:00+00' where id=:i"), dict(i=rows[0].id))
    db.commit()
    service_login(client, p['technician'])
    first = client.get('/hang-toi-giu/lich-su?loai=yeu-cau')
    assert first.status_code == 200 and len(numbers(first.text)) == 20 and 'Trang 1/2' in first.text
    assert numbers(first.text)[0] == rows[-1].number
    second = client.get('/hang-toi-giu/lich-su?loai=yeu-cau&trang=2').text
    assert len(numbers(second)) == 3 and rows[0].number in numbers(second)
    august = client.get('/hang-toi-giu/lich-su?loai=yeu-cau&thang=2026-08').text
    assert numbers(august) == [rows[0].number] and '1 phiếu trong tháng 08/2026' in august
    bad = client.get('/hang-toi-giu/lich-su?loai=xyz&thang=abc&trang=99')
    assert bad.status_code == 200 and 'Trang 2/2' in bad.text
    returns = client.get('/hang-toi-giu/lich-su?loai=tra-kho')
    assert returns.status_code == 200 and 'Không có phiếu.' in returns.text


def test_history_is_per_technician(client, db, held):
    p, a, b = held
    mine = ask(db, p)
    service_login(client, p['other_tech'])
    assert mine.number not in client.get('/hang-toi-giu/lich-su?loai=yeu-cau').text

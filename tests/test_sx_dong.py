"""Nhắc phiếu sản xuất bị đọng (TUẤN duyệt mẫu 06/11/2026): đếm theo giờ làm việc, chỉ đọc, Zalo gộp theo mốc."""
from datetime import date, datetime
from types import SimpleNamespace

from app import production as prod, production_stale as st
from app.models import AttendanceHoliday, DispatchZaloSent, ProductionOrder, StockLedger, ZaloChat
from test_production import shop, create, finish  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_web import service_login

TZ = st.TZ


def at(d, h, m=0):
    return datetime(2026, 11, d, h, m, tzinfo=TZ)


def row(status, **kw):
    base = dict(id=1, status=status, created_at=None, started_at=None, finished_at=None, px_doc_id=1, technician_id=5)
    base.update(kw)
    return SimpleNamespace(**base)


def test_working_minutes_skip_lunch_sunday_and_holiday():
    assert st.working_minutes(at(9, 7, 30), at(9, 17, 30), set()) == 480          # một ngày = 8 giờ, bỏ nghỉ trưa
    assert st.working_minutes(at(9, 11, 0), at(9, 14, 0), set()) == 60            # 11:00–11:30 + 13:30–14:00
    assert st.working_minutes(at(7, 16, 30), at(9, 8, 30), set()) == 120          # thứ Bảy → thứ Hai, bỏ Chủ nhật
    assert st.working_minutes(at(7, 16, 30), at(9, 8, 30), {date(2026, 11, 9)}) == 60
    assert st.working_minutes(at(9, 18, 0), at(9, 8, 0), set()) == 0


def test_check_rules():
    now = at(11, 10, 0)                                                            # thứ Tư
    assert st.check(row('MOI', created_at=at(11, 7, 30)), now, set()) is None
    s = st.check(row('MOI', created_at=at(10, 9, 0)), now, set())                  # 1 ngày làm việc
    assert s['code'] == 'CHUA_BAT_DAU' and not s['dispatcher'] and s['label'] == 'Chưa bắt đầu · 1 ngày'
    assert st.check(row('MOI', created_at=at(9, 7, 30)), now, set())['dispatcher']  # quá 2 ngày → điều phối
    assert st.check(row('MOI', created_at=at(11, 7, 30), technician_id=None), at(11, 17, 30), set())['dispatcher']
    s = st.check(row('DANG_LAM', started_at=at(9, 7, 30)), now, set())
    assert s['code'] == 'LAM_LAU' and s['dispatcher'] and not s['warehouse']
    assert st.check(row('DANG_LAM', started_at=at(10, 7, 30)), now, set()) is None
    assert st.check(row('HOAN_THANH', finished_at=at(11, 8, 0)), now, set()) is None   # 2 giờ < 4 giờ
    s = st.check(row('HOAN_THANH', finished_at=at(10, 15, 0)), now, set())
    assert s['code'] == 'CHO_NHAP' and s['warehouse'] and s['label'] == 'Chờ nhập kho · 5 giờ'
    s = st.check(row('HOAN_THANH', finished_at=at(11, 9, 59), px_doc_id=None), now, set())
    assert s['code'] == 'CHUA_VAT_TU' and s['warehouse'] and s['label'] == 'Chưa xuất vật tư'
    assert st.check(row('DA_NHAP', finished_at=at(1, 8, 0)), now, set()) is None


def _stale(db, shop):
    p, a, _, clock = shop
    clock['now'] = datetime(2027, 2, 3, 10, 0, tzinfo=TZ)                         # thứ Tư
    old = create(db, shop)
    db.query(ProductionOrder).filter_by(id=old.id).update(dict(created_at=datetime(2027, 1, 29, 8, 0, tzinfo=TZ)))
    fresh = create(db, shop)
    db.query(ProductionOrder).filter_by(id=fresh.id).update(dict(created_at=datetime(2027, 2, 3, 9, 0, tzinfo=TZ)))
    db.commit()
    return p, old, fresh, clock


def test_list_tile_filter_and_technician_cards(client, db, shop):  # noqa: F811
    p, old, fresh, clock = _stale(db, shop)
    service_login(client, p['dispatcher'])
    html = client.get('/san-xuat').text
    assert 'Đọng' in html and 'Chưa bắt đầu · 4 ngày' in html and '?dong=1' in html
    page = client.get('/san-xuat?dong=1').text
    assert old.number in page and fresh.number not in page
    service_login(client, p['technician'])
    card = client.get('/tho').text
    assert 'Chưa bắt đầu · 4 ngày' in card


def test_my_work_marks_stale_production_red(db, shop, monkeypatch):  # noqa: F811
    from app import my_work
    p, old, fresh, clock = _stale(db, shop)
    monkeypatch.setattr(my_work, '_now', lambda: clock['now'])
    group = next(g for g in my_work.collect(db, p['technician']) if g['key'] == 'production_work')
    by = {r['number']: r for r in group['rows']}
    assert by[old.number]['overdue'] and 'Chưa bắt đầu · 4 ngày' in by[old.number]['customer']
    assert not by[fresh.number]['overdue']


def test_zalo_digest_morning_all_afternoon_only_new(db, shop, monkeypatch):  # noqa: F811
    p, old, fresh, clock = _stale(db, shop)
    settings = st.get_settings().model_copy(update={'zalo_bot_token': 'token', 'sx_stale_zalo': True})
    monkeypatch.setattr(st, 'get_settings', lambda: settings)
    chat = ZaloChat(chat_id='c-dp', chat_type='PRIVATE', display_name='Điều phối', user_id=p['dispatcher'].id)
    db.add(chat)
    db.commit()
    sent = []
    sender = lambda session, c, text, user, kind: sent.append((c.chat_id, text, kind))  # noqa: E731
    ledger = db.query(StockLedger).count()
    assert st.run_once(db, now=datetime(2027, 2, 3, 7, 59, tzinfo=TZ), sender=sender) == 0
    assert st.run_once(db, now=datetime(2027, 2, 3, 8, 5, tzinfo=TZ), sender=sender) == 1
    assert sent[0][0] == 'c-dp' and sent[0][2] == 'san_xuat' and old.number in sent[0][1] and fresh.number not in sent[0][1]
    assert 'chưa bắt đầu' in sent[0][1] and '/san-xuat?dong=1' in sent[0][1]
    assert st.run_once(db, now=datetime(2027, 2, 3, 8, 30, tzinfo=TZ), sender=sender) == 0     # mỗi mốc một lần
    # 13:45: phiếu cũ đã báo sáng nay thì không báo lại; không có phiếu mới thì không gửi tin.
    assert st.run_once(db, now=datetime(2027, 2, 3, 13, 50, tzinfo=TZ), sender=sender) == 0
    assert len(sent) == 1
    assert db.query(StockLedger).count() == ledger
    # Ngày lễ không gửi.
    db.add(AttendanceHoliday(day=date(2027, 2, 4), name='Tết'))
    db.commit()
    assert st.run_once(db, now=datetime(2027, 2, 4, 8, 5, tzinfo=TZ), sender=sender) == 0
    assert db.query(DispatchZaloSent).filter(DispatchZaloSent.mark.like('sx-dong:%')).count() == 2

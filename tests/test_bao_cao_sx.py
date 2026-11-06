"""Báo cáo sản xuất tuần (TUẤN duyệt mẫu 06/11/2026): chỉ đọc, giá vốn chỉ kho/kế toán/giám đốc."""
from datetime import date, datetime

from app import production_report as rep, weekly_zalo
from app.models import ProductionOrder, StockLedger, ZaloChat
from test_production import shop, create, finish, receive  # noqa: F401
from test_my_work import people, purchase_people  # noqa: F401
from test_technician_stock import held  # noqa: F401
from test_web import service_login

TZ = rep.TZ


def _week(db, shop):
    p, a, _, clock = shop
    clock['now'] = datetime(2027, 1, 13, 9, 0, tzinfo=TZ)
    row = finish(db, shop, good=9, scrap=1)
    db.query(ProductionOrder).filter_by(id=row.id).update(dict(started_at=datetime(2027, 1, 12, 8, 0, tzinfo=TZ),
                                                              finished_at=datetime(2027, 1, 12, 14, 0, tzinfo=TZ)))
    db.commit()
    receive(db, shop, db.get(ProductionOrder, row.id))
    return p, row


def test_build_counts_scrap_cost_and_hours(db, shop):  # noqa: F811
    p, row = _week(db, shop)
    ledger = db.query(StockLedger).count()
    d = rep.build(db, *rep.week_of(date(2027, 1, 12)))
    assert d['orders'] == 1 and d['good'] == 9 and d['scrap'] == 1 and round(d['rate'], 2) == 0.10
    prod = d['products'][0]
    assert prod['code'] == 'TD-0100' and prod['good'] == 9 and prod['cost'] and prod['cost'] > 0
    assert d['techs'][0]['orders'] == 1 and round(d['techs'][0]['hours'], 1) == 4.0    # 8:00–11:30 + 13:30–14:00
    assert db.query(StockLedger).count() == ledger
    assert rep.build(db, *rep.week_of(date(2027, 1, 19)))['orders'] == 0
    text = rep.render_text(d, 'https://vd.test')
    assert text.startswith('🏭 SẢN XUẤT TUẦN 11/01 – 17/01\nTốt 9 · hỏng 1 (10,0%) · 1 phiếu') and '/san-xuat/bao-cao' in text


def test_page_roles(client, db, shop):  # noqa: F811
    p, row = _week(db, shop)
    service_login(client, p['warehouse'])
    html = client.get('/san-xuat/bao-cao?tuan=2027-01-12').text
    assert 'Báo cáo sản xuất' in html and 'Theo sản phẩm' in html and 'Hộp mực tái sinh' in html
    assert 'href="/san-xuat/bao-cao"' in client.get('/san-xuat').text
    service_login(client, p['technician'])
    assert client.get('/san-xuat/bao-cao', follow_redirects=False).status_code in (303, 403)
    assert '/san-xuat/bao-cao' not in client.get('/san-xuat').text


def test_monday_zalo_to_director(db, shop, monkeypatch):  # noqa: F811
    p, row = _week(db, shop)
    settings = weekly_zalo.get_settings().model_copy(update={'zalo_bot_token': 'token'})
    monkeypatch.setattr(weekly_zalo, 'get_settings', lambda: settings)
    db.add(ZaloChat(chat_id='c-gd', chat_type='PRIVATE', display_name='Giám đốc', user_id=p['director'].id))
    db.commit()
    sent = []
    sender = lambda session, chat, text, user, kind: sent.append((chat.chat_id, text, kind))  # noqa: E731
    assert weekly_zalo.run_once(db, now=datetime(2027, 1, 18, 7, 45, tzinfo=TZ), sender=sender) == 1
    assert sent[0][0] == 'c-gd' and sent[0][2] == 'san_xuat' and 'TUẦN 11/01 – 17/01' in sent[0][1]
    assert weekly_zalo.run_once(db, now=datetime(2027, 1, 18, 8, 30, tzinfo=TZ), sender=sender) == 0

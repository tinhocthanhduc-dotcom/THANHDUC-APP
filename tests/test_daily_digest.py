import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import alerts, daily_digest as svc, dashboard, stock_min_levels
from app.errors import BusinessError
from app.models import AuditLog, DailyDigest, FundLedger, Product, StockLedger, User
from app.schemas import StockMinLevelIn
from scripts.daily_digest import main
from test_purchase_orders import purchase_people, DAY
from test_supplier_debts import m3_people
from test_purchase_reports import report_people, received, completed, clock
from test_dashboard import people, fee
from test_web import service_login


def test_snapshot_matches_sources_iso_json_and_no_ledger_changes(db, people):
    p = people
    received(db, p, qty=5, cost=100000, day=DAY-timedelta(days=100))
    fee(db, p, 1200000)
    completed(db, p)
    counts_before = [db.query(m).count() for m in (StockLedger, FundLedger)]
    summary = dashboard.summary(db, p['director'], DAY)
    notices = alerts.collect(db, p['director'], today=DAY)
    row = svc.save(db, p['director'], DAY)
    for key in ('revenue_today', 'revenue_month', 'gross_margin_month', 'fund_total', 'funds',
                'customer_debt', 'supplier_debt', 'inventory', 'inventory_total', 'services', 'pending_receipts'):
        assert row.data[key] == summary[key]
    assert row.data['day'] == DAY.isoformat()
    assert row.data['alert_counts'] == notices.counts
    assert row.data['red_alerts'] == [a for a in notices if a['level'] == 'DO'][:5]
    assert row.data['red_alert_count'] == notices.red_count
    assert row.created_by == p['director'].id
    assert json.loads(json.dumps(row.data)) == row.data
    assert row.text == svc.render_text(row.data)
    assert [db.query(m).count() for m in (StockLedger, FundLedger)] == counts_before


def test_upsert_one_day_new_numbers_version_and_before_after_audit(db, people):
    p = people
    row = svc.save(db, p['director'], DAY)
    first = row.version
    assert row.data['revenue_today'] == 0
    fee(db, p, 1234567)
    updated = svc.save(db, p['director'], DAY)
    assert db.query(DailyDigest).count() == 1
    assert updated.version == first+1 and updated.data['revenue_today'] == 1234567
    logs = list(db.scalars(select(AuditLog).where(AuditLog.entity == 'daily_digest').order_by(AuditLog.id)))
    assert [log.action for log in logs] == ['digest.create', 'digest.update']
    assert logs[0].before is None and logs[0].after['version'] == first
    assert logs[1].before['data']['revenue_today'] == 0
    assert logs[1].after['data']['revenue_today'] == 1234567
    assert logs[1].after['version'] == first+1
    # Lưu cùng số liệu vẫn là một phiên bản mới có audit.
    assert svc.save(db, p['director'], DAY).version == first+2


def test_script_historical_day_not_today_and_default_vietnam_day(db, people, monkeypatch, capsys):
    p = people
    clock(monkeypatch, datetime(2026, 10, 17, 23, 30, tzinfo=dashboard.TZ))
    fee(db, p, 120000)
    clock(monkeypatch, datetime(2026, 10, 18, 12, tzinfo=dashboard.TZ))
    fee(db, p, 990000)
    main(['--user', p['director'].username, '--day', '2026-10-17'])
    old = db.get(DailyDigest, DAY-timedelta(days=1))
    assert old.data['revenue_today'] == 120000 and old.created_by is None
    assert '17/10/2026' in capsys.readouterr().out
    # UTC vẫn là ngày trước, mặc định phải lấy ngày VN.
    monkeypatch.setattr(dashboard, '_now', lambda: datetime(2026, 10, 18, 0, 30, tzinfo=dashboard.TZ))
    main(['--user', p['director'].username])
    assert db.get(DailyDigest, DAY).data['revenue_today'] == 990000
    assert '990.000 đồng' in capsys.readouterr().out


def test_text_empty_alerts_warehouses_and_funds(db, make_user):
    user = make_user('director', 'director')
    data = svc.build(db, user, DAY)
    assert data['funds'] == data['inventory'] == []
    data['revenue_today'] = 1234567
    rendered = svc.render_text(data)
    assert '1.234.567 đồng' in rendered
    assert 'Không có việc cần xử lý' in rendered
    assert 'Cảnh báo đỏ: 0' in rendered


@pytest.mark.parametrize('kind', ['sales', 'locked', 'missing'])
def test_script_refuses_non_director_locked_missing_without_writes(db, make_user, kind, capsys):
    if kind != 'missing':
        user = make_user(kind, 'director' if kind == 'locked' else 'sales')
        if kind == 'locked':
            user.active = False
            db.commit()
    before = db.query(AuditLog).count()
    with pytest.raises(SystemExit) as exc:
        main(['--user', kind])
    assert exc.value.code == 1
    assert 'giám đốc đang hoạt động' in capsys.readouterr().err
    assert db.query(DailyDigest).count() == 0 and db.query(AuditLog).count() == before


def test_finance_filtered_text_tables_metadata_and_cannot_save(client, db, people):
    p = people
    received(db, p, day=DAY-timedelta(days=100), cost=999999)
    row = svc.save(db, p['director'], DAY)
    assert row.data['alert_counts']['CHAM_LUAN_CHUYEN'] == 1
    assert 'chậm luân chuyển' in row.text
    csrf = service_login(client, p['finance'])
    response = client.get('/tong-quan/ngay/'+DAY.isoformat())
    assert response.status_code == 200 and 'Doanh thu ngày' in response.text
    for hidden in ('Lãi gộp', 'tồn kho', 'tồn theo kho', 'chậm luân chuyển', 'CHAM_LUAN_CHUYEN'):
        assert hidden not in response.text
    data = svc.load(db, p['finance'], DAY)['data']
    assert not set(svc.SENSITIVE).intersection(data)
    assert 'CHAM_LUAN_CHUYEN' not in data['alert_counts']
    assert client.get('/tong-quan/ngay').status_code == 200
    overview = client.get('/tong-quan')
    assert 'Lưu tóm tắt hôm nay' not in overview.text
    assert client.post('/tong-quan/ngay', data={'csrf': csrf}).status_code == 403
    with pytest.raises(BusinessError) as exc:
        svc.save(db, p['finance'], DAY)
    assert exc.value.code == 'FORBIDDEN'
    db.refresh(row)
    assert row.version == 1 and 'inventory' in row.data


@pytest.mark.parametrize('role', ['sales', 'warehouse', 'technician'])
def test_other_roles_cannot_build_save_or_view(client, db, people, role):
    p = people
    svc.save(db, p['director'], DAY)
    for fn in (svc.build, svc.save, svc.load):
        with pytest.raises(BusinessError) as exc:
            fn(db, p[role], DAY)
        assert exc.value.status == 403
    csrf = service_login(client, p[role])
    for path in ('/tong-quan/ngay', '/tong-quan/ngay/'+DAY.isoformat()):
        assert client.get(path).status_code == 403
    assert client.post('/tong-quan/ngay', data={'csrf': csrf}).status_code == 403


def test_web_save_csrf_detail_missing_date_copy_and_preserved_snapshot(client, db, people):
    p = people
    csrf = service_login(client, p['director'])
    assert client.get('/tong-quan/ngay/2026-01-01').status_code == 404
    overview = client.get('/tong-quan')
    assert 'Lưu tóm tắt hôm nay' in overview.text and 'Các ngày trước' in overview.text
    assert client.post('/tong-quan/ngay', data={}).status_code == 403
    assert client.post('/tong-quan/ngay', data={'csrf': 'wrong'}).status_code == 403
    assert db.query(DailyDigest).count() == 0
    response = client.post('/tong-quan/ngay', data={'csrf': csrf}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/tong-quan/ngay/'+DAY.isoformat()
    fee(db, p, 120000)
    detail = client.get(response.headers['location'])
    assert detail.status_code == 200 and 'Sao chép' in detail.text and 'Lãi gộp tháng' in detail.text
    assert 'chậm luân chuyển' in detail.text and 'digest-text' in detail.text
    assert svc.load(db, p['director'], DAY)['data']['revenue_today'] == 0
    assert '0 cảnh báo đỏ' in client.get('/tong-quan/ngay').text


def test_listing_newest_first_thirty_per_page(client, db, people):
    p = people
    data = svc.build(db, p['director'], DAY)
    for i in range(31):
        day = DAY-timedelta(days=i)
        db.add(DailyDigest(day=day, data=dict(data, day=day.isoformat()), text='Bản thử', created_by=p['director'].id))
    db.commit()
    service_login(client, p['finance'])
    page1 = client.get('/tong-quan/ngay')
    page2 = client.get('/tong-quan/ngay?page=2')
    assert page1.status_code == page2.status_code == 200
    assert page1.text.count('class="overview-tile"') == 30
    assert page2.text.count('class="overview-tile"') == 1
    assert '/tong-quan/ngay/'+DAY.isoformat() in page1.text
    assert '/tong-quan/ngay/'+(DAY-timedelta(days=30)).isoformat() not in page1.text
    assert 'Trang sau' in page1.text and 'Trang trước' in page2.text
    assert client.get('/tong-quan/ngay?page=0').status_code == 422


def test_red_total_before_twenty_limit_and_only_five_saved(db, people):
    p = people
    for i in range(25):
        code = f'TD-{i+100:04}'
        db.add(Product(code=code, name=f'Hàng {i}'))
        db.commit()
        stock_min_levels.set_level(db, p['warehouse'], 'K58', code, StockMinLevelIn(min_qty=1, version=0))
    row = svc.save(db, p['director'], DAY)
    assert row.data['alert_counts']['DUOI_TON_TOI_THIEU'] == 25
    assert row.data['red_alert_count'] == 25
    assert len(row.data['red_alerts']) == 5


def test_same_day_parallel_saves_are_serialized(db, engine, people):
    user_id = people['director'].id
    barrier = Barrier(2)
    def run():
        with Session(engine, expire_on_commit=False) as session:
            user = session.get(User, user_id)
            barrier.wait(timeout=10)
            return svc.save(session, user, DAY).version
    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(lambda _: run(), range(2)))
    assert sorted(versions) == [1, 2]
    assert db.query(DailyDigest).count() == 1
    assert db.query(AuditLog).filter(AuditLog.entity == 'daily_digest').count() == 2


def test_audit_failure_rolls_back_snapshot(db, people, monkeypatch):
    p = people
    def fail(*args, **kwargs):
        raise RuntimeError('Lỗi ghi audit thử nghiệm')
    monkeypatch.setattr(svc.audit, 'record', fail)
    with pytest.raises(RuntimeError):
        svc.save(db, p['director'], DAY)
    assert db.query(DailyDigest).count() == 0


def test_director_reads_saved_text_even_if_formatter_changes(db, people, monkeypatch):
    p = people
    row = svc.save(db, p['director'], DAY)
    saved_text = row.text
    monkeypatch.setattr(svc, 'render_text', lambda data: 'Định dạng mới cho kế toán')
    assert svc.load(db, p['director'], DAY)['text'] == saved_text
    assert svc.load(db, p['finance'], DAY)['text'] == 'Định dạng mới cho kế toán'

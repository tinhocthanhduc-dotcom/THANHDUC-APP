"""Đội AI đợt 1 (TUẤN duyệt 15/11/2026): Trưởng nhóm AI chia người + hạn, 3 việc làm tiếp, nhắc/báo lên, đổi thợ, màn /doi-ai."""
import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import my_work, nhan_vien_ao as nva, service_suggest, service_tickets, truong_nhom as tn
from app.models import AttendancePunch, Customer, ServiceTicket, TeamTask, VirtualAgentLog
from app.schemas import ServiceTicketIn
from test_nhan_vien_ao import clock_in, link_zalo
from test_web import csrf_of, service_login

MON = datetime(2026, 11, 16, 9, 0, tzinfo=tn.TZ)   # thứ Hai


@pytest.fixture
def team(db, make_user):
    db.add(Customer(code='KTD-000001', name='Minh Phát', search_text='minh phat', address='58 Mã Lò, Q. Bình Tân, HCM'))
    db.commit()
    tn._last['sync'] = None
    return dict(director=make_user('dai.gd', 'director'), thanh=make_user('dai.thanh', 'sales', 'finance'),
                thanh2=make_user('dai.thanh2', 'sales'), thanh3=make_user('dai.dp', 'dispatcher', 'warehouse'),
                phong=make_user('dai.phong', 'technician'), a=make_user('dai.a', 'technician'))


def fake_work(monkeypatch, by_user):
    """by_user: {username: [(key, title, [rows])]}"""
    def collect(session, user, today=None, keys=None):
        out = []
        for key, title, rows in by_user.get(user.username, []):
            if keys is None or key in keys:
                out.append(dict(key=key, title=title, count=len(rows), rows=rows))
        return out
    monkeypatch.setattr(my_work, 'collect', collect)


def row(i, **kw):
    return dict(id=i, number=f'X-{i}', customer='Minh Phát', link=f'/x/{i}', **kw)


def task(db, key, ref):
    return db.scalar(select(TeamTask).where(TeamTask.item_key == key, TeamTask.ref == str(ref), TeamTask.done_at.is_(None)))


def test_sync_picks_owner_due_and_closes(db, team, monkeypatch):
    shared = row(1)
    fake_work(monkeypatch, {
        'dai.gd': [('receipts_pending', 'Báo thu chờ xác nhận', [shared])],
        'dai.thanh': [('receipts_pending', 'Báo thu chờ xác nhận', [shared]), ('orders_unreported', 'Đã giao · chưa báo thu đủ', [row(2), row(3)])],
        'dai.thanh2': [('quotes_accepted', 'Báo giá đã đồng ý', [row(4)])],
        'dai.dp': [('parts_pending', 'Yêu cầu linh kiện chờ cấp', [row(5)]), ('stock_low', 'Hàng dưới mức', [row(6)]),
                   ('service_today', 'Hôm nay có hẹn', [row(7, appointment_at=MON + timedelta(hours=2))])],
    })
    out = tn.sync(db, MON)
    assert out['new'] == 7
    t = task(db, 'receipts_pending', 1)
    assert t.owner_id == team['thanh'].id                          # không giao giám đốc khi nhân viên cũng thấy
    assert t.due_at == datetime(2026, 11, 17, 9, 0, tzinfo=tn.TZ)  # việc thường: hết 1 ngày làm việc
    assert task(db, 'parts_pending', 5).due_at == MON + timedelta(hours=2) and task(db, 'parts_pending', 5).urgent   # việc gấp 2 giờ
    assert task(db, 'stock_low', 6).due_at is None                 # mua hàng không có hạn
    assert task(db, 'service_today', 7).due_at == MON + timedelta(hours=2)
    sat = datetime(2026, 11, 21, 9, 0, tzinfo=tn.TZ)
    assert tn._add_workdays(db, sat) == datetime(2026, 11, 23, 9, 0, tzinfo=tn.TZ)   # bỏ Chủ nhật
    # Việc biến khỏi Việc của tôi thì xong; người cũ vẫn thấy thì giữ người cũ.
    fake_work(monkeypatch, {'dai.thanh': [('receipts_pending', 'Báo thu chờ xác nhận', [shared])],
                            'dai.thanh2': [('receipts_pending', 'Báo thu chờ xác nhận', [shared])]})
    out = tn.sync(db, MON + timedelta(minutes=5))
    assert out['done'] == 6 and task(db, 'receipts_pending', 1).owner_id == team['thanh'].id


def test_sync_balances_load_between_staff(db, team, monkeypatch):
    rows_ = [row(i) for i in range(1, 7)]
    fake_work(monkeypatch, {'dai.thanh': [('orders_draft', 'Đơn nháp chưa chốt', rows_)],
                            'dai.thanh2': [('orders_draft', 'Đơn nháp chưa chốt', rows_)]})
    tn.sync(db, MON)
    owners = db.execute(select(TeamTask.owner_id, func.count()).group_by(TeamTask.owner_id)).all()
    assert sorted(n for _, n in owners) == [3, 3]


def test_next_for_orders_late_first(db, team, monkeypatch):
    fake_work(monkeypatch, {'dai.thanh': [('orders_draft', 'Đơn nháp chưa chốt', [row(1), row(2)]),
                                          ('orders_waiting', 'Đơn chờ giao', [row(3)]),
                                          ('quotes_open', 'Báo giá chờ kết quả', [row(4)])]})
    tn.sync(db, MON)
    t = task(db, 'orders_draft', 2)
    t.due_at = MON - timedelta(hours=3)
    db.commit()
    nexts = tn.next_for(db, team['thanh'], 3, MON)
    assert [n['number'] for n in nexts] == ['X-2', 'X-3', 'X-1']
    assert nexts[0]['late'] and nexts[0]['when'] == 'Trễ 3 giờ' and nexts[1]['when'] == 'Hạn 11:00'
    nva.rows(db)['TRUONG_NHOM'].enabled = False
    db.commit()
    assert tn.next_for(db, team['thanh'], 3, MON) == []


def test_o_ai_nhac_gop_viec_tre_theo_loai(db, team, monkeypatch):
    """TUẤN duyệt 18/11/2026: ô AI nhắc thay khung Làm tiếp theo thứ tự."""
    fake_work(monkeypatch, {'dai.thanh': [('orders_draft', 'Đơn nháp chưa chốt', [row(1), row(2)]),
                                          ('orders_waiting', 'Đơn chờ giao', [row(3)])]})
    tn.sync(db, MON)
    note = tn.note_for(db, team['thanh'], MON)
    assert note['late'] is False and note['head'] == 'Không có việc trễ.' and 'Gần hạn nhất: X-' in note['tail']
    for ref in (1, 2):
        task(db, 'orders_draft', ref).due_at = MON - timedelta(days=1, hours=1)
    task(db, 'orders_waiting', 3).due_at = MON - timedelta(days=1, hours=2)
    db.commit()
    note = tn.note_for(db, team['thanh'], MON)
    assert note['late'] and note['head'] == '3 việc trễ 1 ngày' and note['label'] == 'Mở việc trễ nhất ›'
    assert note['parts'] == ['1 đơn chờ giao (X-3)', '2 đơn nháp chưa chốt (X-1, X-2)'] and note['link'] == '/x/3'
    nva.rows(db)['TRUONG_NHOM'].enabled = False
    db.commit()
    assert tn.note_for(db, team['thanh'], MON) is None


@pytest.fixture
def zalo_on(monkeypatch):
    settings = nva.get_settings().model_copy(update=dict(zalo_bot_token='token', dispatch_zalo=True))
    monkeypatch.setattr(nva, 'get_settings', lambda: settings)
    monkeypatch.setattr(nva, '_working', lambda session, now: True)
    from app import config, dispatch_alerts
    monkeypatch.setattr(dispatch_alerts, 'get_settings', lambda: settings)
    monkeypatch.setattr(config, 'get_settings', lambda: settings)
    return settings


def test_escalate_owner_then_dispatcher_then_director(db, team, monkeypatch, zalo_on):
    for key, u in (('z-thanh', team['thanh']), ('z-dp', team['thanh3']), ('z-gd', team['director']), ('z-a', team['a'])):
        link_zalo(db, u, key)
    fake_work(monkeypatch, {'dai.dp': [('parts_pending', 'Yêu cầu linh kiện chờ cấp', [row(1)])],
                            'dai.thanh': [('orders_draft', 'Đơn nháp chưa chốt', [row(2)])],
                            'dai.a': [('service_assigned', 'Mới giao · chờ nhận', [row(3, due_at=MON - timedelta(hours=5))])],
                            'dai.gd': [('refunds_pending', 'Hoàn tiền chờ duyệt', [row(4)])]})
    tn.sync(db, MON)
    sent = []
    send = lambda *a: sent.append(a)
    assert tn.escalate(db, MON + timedelta(minutes=10), send) == 0   # chưa quá hạn
    t1 = MON + timedelta(hours=2, minutes=5)
    tn.escalate(db, t1, send)
    texts = {s[1].chat_id: s[2] for s in sent}
    assert set(texts) == {'z-dp'} and 'X-1' in texts['z-dp'] and 'quá hạn' in texts['z-dp']   # thợ: Nhắc hẹn AI lo; giám đốc: việc của mình
    sent.clear()
    tn.escalate(db, t1 + timedelta(minutes=10), send)
    assert sent == []                                                  # mỗi việc nhắc một lần
    tn.escalate(db, t1 + timedelta(minutes=31), send)
    assert sent == []                                                  # người làm chính là điều phối: không tự báo mình
    tn.escalate(db, MON + timedelta(hours=4, minutes=6), send)        # việc gấp quá hạn 2 giờ
    texts = {s[1].chat_id: s[2] for s in sent}
    assert 'z-gd' in texts and 'X-1' in texts['z-gd'] and '/doi-ai' in texts['z-gd']
    assert task(db, 'parts_pending', 1).director_at is not None
    assert task(db, 'service_assigned', 3).reminded_at is None
    # Đơn nháp (việc thường): nhắc sau 1 ngày làm việc, báo giám đốc thêm 1 ngày nữa.
    sent.clear()
    tue = datetime(2026, 11, 17, 9, 10, tzinfo=tn.TZ)
    tn.escalate(db, tue, send)
    assert [s[1].chat_id for s in sent] == ['z-thanh']
    sent.clear()
    tn.escalate(db, tue + timedelta(days=1), send)
    assert [s[1].chat_id for s in sent] == ['z-gd'] and 'X-2' in sent[0][2]


def test_rebalance_moves_unaccepted_ticket_and_undo(db, team, monkeypatch, zalo_on):
    p = team
    t = service_tickets.create(db, p['thanh3'], ServiceTicketIn(customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Kẹt giấy', priority='THUONG'))
    t = service_tickets.assign(db, p['thanh3'], t.id, p['a'].id, t.version)
    for i in range(8):
        db.add(TeamTask(item_key='service_accepted', ref=f'r{i}', owner_id=p['a'].id, title='x'))
    db.commit()
    later = service_tickets._now() + timedelta(minutes=40)
    # Chấm công vào đúng ngày của mốc chạy: chạy test lúc gần nửa đêm giờ VN thì "later" đã sang ngày mới.
    db.add(AttendancePunch(user_id=p['phong'].id, kind='IN', at=later - timedelta(minutes=1), source='APP', off_hours=False))
    db.commit()
    monkeypatch.setattr(service_suggest, 'rank_many', lambda session, tickets, now=None, techs=None: {
        tickets[0].id: [dict(id=p['a'].id, name='A', why=[], blocked=''), dict(id=p['phong'].id, name='Phong', why=['rảnh'], blocked='')]})
    link_zalo(db, p['thanh3'], 'z-dp')
    link_zalo(db, p['a'], 'z-a')
    sent = []
    assert tn.rebalance(db, later, sender=lambda *a: sent.append(a)) == 1
    db.expire_all()
    t = db.get(ServiceTicket, t.id)
    assert t.assignee_id == p['phong'].id and t.status == 'DA_GIAO'
    entry = db.scalar(select(VirtualAgentLog).where(VirtualAgentLog.action == 'DOI_NGUOI'))
    assert entry.target_id == p['phong'].id and '8 việc' in entry.reason
    texts = {s[1].chat_id: s[2] for s in sent}
    assert 'Phong' in texts['z-dp'] and 'không cần đi' in texts['z-a'].lower()
    assert tn.rebalance(db, later) == 0                                # không đổi lại phiếu đã đổi
    assert tn.can_undo(db, entry)
    tn.undo(db, p['thanh3'], entry.id)
    db.expire_all()
    assert db.get(ServiceTicket, t.id).assignee_id == p['a'].id
    assert not tn.can_undo(db, db.get(VirtualAgentLog, entry.id))


def test_rebalance_off_or_not_overloaded(db, team, monkeypatch, zalo_on):
    p = team
    t = service_tickets.create(db, p['thanh3'], ServiceTicketIn(customer_code='KTD-000001', kind='SUA_TAI_CHO', symptom='Kẹt giấy', priority='THUONG'))
    service_tickets.assign(db, p['thanh3'], t.id, p['a'].id, t.version)
    db.commit()
    clock_in(db, p['phong'])
    later = service_tickets._now() + timedelta(minutes=40)
    assert tn.rebalance(db, later) == 0                                 # thợ A chưa quá tải
    row_ = nva.rows(db)['TRUONG_NHOM']
    row_.config = dict(nva.cfg(row_), reassign=False)
    for i in range(9):
        db.add(TeamTask(item_key='service_accepted', ref=f'r{i}', owner_id=p['a'].id, title='x'))
    db.commit()
    assert tn.rebalance(db, later) == 0


def test_brief_replaces_clerk(db, team, monkeypatch, zalo_on):
    link_zalo(db, team['thanh'], 'z-thanh')
    link_zalo(db, team['director'], 'z-gd')
    fake_work(monkeypatch, {'dai.thanh': [('orders_unreported', 'Đã giao · chưa báo thu đủ', [row(1), row(2)]), ('orders_draft', 'Đơn nháp', [row(3)])],
                            'dai.gd': [('orders_draft', 'Đơn nháp', [row(9)])]})
    monkeypatch.setattr(nva, 'clerk_slot', lambda session, now, times: '07:45')
    sent = []
    assert tn.brief_once(db, MON, lambda *a: sent.append(a)) == 1
    body = sent[0][2]
    assert sent[0][1].chat_id == 'z-thanh' and 'việc nên làm tiếp lúc 07:45' in body and '1. X-' in body and 'Còn treo' in body
    assert tn.brief_once(db, MON, lambda *a: sent.append(a)) == 0
    assert nva.clerk_once(db, MON, lambda *a: sent.append(a)) == 0 and len(sent) == 1


def test_pages(db, team, client, monkeypatch):
    fake_work(monkeypatch, {'dai.thanh': [('orders_draft', 'Đơn nháp chưa chốt', [row(1)])]})
    tn.sync(db)
    monkeypatch.undo()   # màn Việc của tôi dùng lại dữ liệu thật; 3 việc làm tiếp đọc từ team_tasks
    service_login(client, team['thanh3'])
    assert client.get('/doi-ai').status_code == 403
    service_login(client, team['thanh'])
    assert client.get('/doi-ai').status_code == 403
    page = client.get('/viec-cua-toi').text
    assert 'Làm tiếp theo thứ tự' not in page and 'aria-label="AI nhắc việc"' in page and 'X-1' in page
    service_login(client, team['director'])
    page = client.get('/doi-ai').text
    assert 'Trưởng nhóm AI' in page and 'Dai.Thanh' in page and 'X-1' in page and 'Đội AI vừa làm' in page
    assert 'Quá hạn' in client.get('/doi-ai?xem=qua-han').text
    nva_page = client.get('/nhan-vien-ao').text
    form = nva_page[nva_page.index('action="/nhan-vien-ao/TRUONG_NHOM"'):]
    key = re.search(r'name="form_key" value="([^"]+)"', form).group(1)
    res = client.post('/nhan-vien-ao/TRUONG_NHOM', data=dict(csrf=csrf_of(nva_page), form_key=key, cai_dat='1', bat='1', overload='6', gap='2',
                                                             undo_minutes='15', reassign='0', times='07:30, 13:30'), follow_redirects=False)
    assert res.status_code == 303
    db.expire_all()
    c = nva.cfg(nva.rows(db)['TRUONG_NHOM'])
    assert c['overload'] == 6 and c['gap'] == 2 and c['reassign'] is False and c['times'] == ['07:30', '13:30']

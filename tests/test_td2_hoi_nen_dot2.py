"""TD2 đợt 2: hỏi nền gửi lại dấu; chưa ai ghi gì thì máy chủ trả "không đổi" mà không tính lại nhóm việc."""
from app import my_work
from test_my_work import people, purchase_people, ticket
from test_web import service_login


def test_bell_and_queue_skip_work_when_nothing_written(client, db, people, monkeypatch):
    pp = people
    ticket(db, pp, 'MOI')
    service_login(client, pp['dispatcher'])
    full = client.get('/ca-nhan/thong-bao').json()
    assert full['items'] and full['dau']
    calls = []
    real = my_work._group
    monkeypatch.setattr(my_work, '_group', lambda *a, **k: calls.append(1) or real(*a, **k))
    import app.web as web
    monkeypatch.setattr(web, 'db_writes', lambda session: full['dau'])
    same = client.get('/ca-nhan/thong-bao', params=dict(dau=full['dau']))
    assert same.json() == dict(same=True, dau=full['dau']) and same.headers['cache-control'] == 'no-store' and not calls
    # Dấu cũ (đã có người ghi) hoặc không gửi dấu: tính lại đầy đủ.
    assert client.get('/ca-nhan/thong-bao', params=dict(dau='cu')).json()['items'] and calls
    calls.clear()
    stamp = client.get('/viec-cua-toi/moi').json()
    assert stamp['stamp'] and stamp['dau'] == full['dau'] and calls
    calls.clear()
    assert client.get('/viec-cua-toi/moi', params=dict(dau=full['dau'])).json() == dict(same=True, dau=full['dau']) and not calls


def test_real_write_counter_reads(client, db, people):
    from app.web import db_writes
    assert db_writes(db).isdigit()


def test_page_does_not_load_login_twice(client, db, people):
    from sqlalchemy import event
    from app.db import engine
    service_login(client, people['dispatcher'])
    sql = []
    cap = lambda conn, cursor, statement, *a: sql.append(statement)
    event.listen(engine(), 'before_cursor_execute', cap)
    try:
        assert client.get('/ca-nhan').status_code == 200
    finally:
        event.remove(engine(), 'before_cursor_execute', cap)
    assert sum('FROM employees' in s for s in sql) <= 1


def test_must_change_password_still_redirects(client, db, people):
    u = people['dispatcher']
    service_login(client, u)
    u.must_change_password = True
    db.commit()
    r = client.get('/viec-cua-toi', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/doi-mat-khau'

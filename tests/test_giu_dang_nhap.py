"""Phiên web tự gia hạn (TUẤN 11/11/2026): còn dùng thì không phải đăng nhập lại mỗi sáng."""
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings
from app.web import COOKIE, csrf_token
from test_tk_tai_khoan import staff, web_login  # noqa: F401


def _forge(token, **changes):
    s = get_settings()
    payload = jwt.decode(token, s.secret_key, algorithms=["HS256"])
    payload.update(changes)
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def _set(client, token):
    domain = next(c.domain for c in client.cookies.jar if c.name == COOKIE)
    client.cookies.clear()
    client.cookies.set(COOKIE, token, domain=domain)


def test_login_cookie_lasts_idle_days_not_12_hours(client, staff):
    web_login(client, staff['sales'])
    payload = jwt.decode(client.cookies.get(COOKIE), options={"verify_signature": False})
    assert payload['exp'] - payload['iat'] == get_settings().web_idle_days * 86400
    assert isinstance(payload['cs'], str) and payload['lat'] == payload['iat']


def test_fresh_session_not_renewed(client, staff):
    web_login(client, staff['sales'])
    r = client.get('/viec-cua-toi', follow_redirects=False)
    assert r.status_code == 200 and COOKIE + '=' not in r.headers.get('set-cookie', '')


def test_old_session_renewed_keeps_csrf_and_login_time(client, staff):
    csrf = web_login(client, staff['sales'])
    old = client.cookies.get(COOKIE)
    hour_ago = int((datetime.now(UTC) - timedelta(hours=20)).timestamp())
    aged = _forge(old, iat=hour_ago, lat=hour_ago)
    _set(client, aged)
    r = client.get('/viec-cua-toi', follow_redirects=False)
    assert r.status_code == 200 and COOKIE + '=' in r.headers['set-cookie']
    new = client.cookies.get(COOKIE)
    assert new != aged
    p = jwt.decode(new, get_settings().secret_key, algorithms=["HS256"])
    assert p['lat'] == hour_ago and p['iat'] > hour_ago
    assert csrf_token(new) == csrf  # trang mở ở tab khác vẫn gửi được
    assert client.post('/dang-xuat', data=dict(csrf=csrf), follow_redirects=False).status_code == 303


def test_legacy_token_keeps_its_csrf_after_renewal(client, staff):
    web_login(client, staff['sales'])
    p = jwt.decode(client.cookies.get(COOKIE), options={"verify_signature": False})
    long_ago = int((datetime.now(UTC) - timedelta(hours=5)).timestamp())
    legacy = jwt.encode(dict(tv=p['tv'], sub=p['sub'], iat=long_ago, exp=p['exp']), get_settings().secret_key, algorithm="HS256")
    legacy_csrf = csrf_token(legacy)
    _set(client, legacy)
    assert client.get('/viec-cua-toi', follow_redirects=False).status_code == 200
    assert csrf_token(client.cookies.get(COOKIE)) == legacy_csrf


def test_session_past_max_days_must_log_in(client, staff):
    web_login(client, staff['sales'])
    old = client.cookies.get(COOKIE)
    long_ago = int((datetime.now(UTC) - timedelta(days=get_settings().web_max_days + 1)).timestamp())
    _set(client, _forge(old, lat=long_ago))
    r = client.get('/viec-cua-toi', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith('/dang-nhap')


def test_expired_idle_session_must_log_in(client, staff):
    web_login(client, staff['sales'])
    past = int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())
    _set(client, _forge(client.cookies.get(COOKIE), exp=past))
    r = client.get('/viec-cua-toi', follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'].startswith('/dang-nhap')


def test_logout_not_undone_by_renewal(client, staff):
    csrf = web_login(client, staff['sales'])
    old = client.cookies.get(COOKIE)
    ago = int((datetime.now(UTC) - timedelta(hours=3)).timestamp())
    _set(client, _forge(old, iat=ago))
    r = client.post('/dang-xuat', data=dict(csrf=csrf), follow_redirects=False)
    assert r.status_code == 303
    cookies = [v for k, v in r.headers.multi_items() if k == 'set-cookie' and v.startswith(COOKIE + '=')]
    assert len(cookies) == 1 and ('Max-Age=0' in cookies[0] or 'expires=' in cookies[0].lower())
    assert client.get('/viec-cua-toi', follow_redirects=False).status_code == 303


def test_locked_account_renewed_cookie_still_rejected(client, db, staff):
    web_login(client, staff['sales'])
    ago = int((datetime.now(UTC) - timedelta(hours=3)).timestamp())
    _set(client, _forge(client.cookies.get(COOKIE), iat=ago))
    user = staff['sales']
    db.refresh(user)
    user.token_version += 1
    db.commit()
    r = client.get('/viec-cua-toi', follow_redirects=False)
    assert r.status_code == 303 and COOKIE + '=' not in r.headers.get('set-cookie', '')
    assert r.headers['location'].startswith('/dang-nhap')

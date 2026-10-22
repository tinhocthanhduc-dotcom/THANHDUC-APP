"""Web Push (RFC 8291 mã hóa aes128gcm + RFC 8292 VAPID) tự viết bằng cryptography, không thư viện ngoài.

Vòng gửi chỉ ĐỌC hàng việc (notifications.items) và ghi bảng push_subscriptions; không đụng tiền/kho/quyền.
"""
import base64
import json
import logging
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select, text

from . import notifications
from .errors import BusinessError
from .models import PushKey, PushSubscription, User

log = logging.getLogger(__name__)
LOCK_ID = 7_301_026  # pg advisory lock: 2 worker uvicorn chỉ một worker gửi.
EVERY = 20
MAX_PER_ROUND = 3
SUBJECT = 'https://quanlythanhduc.com'


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def _raw(public_key) -> bytes:
    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def keys(session):
    row = session.scalar(select(PushKey).order_by(PushKey.id).limit(1))
    if row is None:
        private = ec.generate_private_key(ec.SECP256R1())
        row = PushKey(id=1, public_key=b64(_raw(private.public_key())), private_pem=private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
        session.add(row)
        session.commit()
        row = session.scalar(select(PushKey).order_by(PushKey.id).limit(1))
    return row


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None, sender=None) -> bytes:
    ua_raw = unb64(p256dh)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_raw)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    as_raw = _raw(sender.public_key())
    salt = salt or os.urandom(16)
    shared = sender.exchange(ec.ECDH(), ua_public)
    ikm = _hkdf(unb64(auth), shared, b'WebPush: info\x00' + ua_raw + as_raw, 32)
    cek = _hkdf(salt, ikm, b'Content-Encoding: aes128gcm\x00', 16)
    nonce = _hkdf(salt, ikm, b'Content-Encoding: nonce\x00', 12)
    body = AESGCM(cek).encrypt(nonce, payload + b'\x02', None)
    return salt + struct.pack('!IB', 4096, len(as_raw)) + as_raw + body


def _vapid(endpoint, key_row):
    parts = urlsplit(endpoint)
    private = serialization.load_pem_private_key(key_row.private_pem.encode(), None)
    token = jwt.encode({'aud': f'{parts.scheme}://{parts.netloc}', 'exp': int(time.time()) + 12 * 3600, 'sub': SUBJECT},
                       private, algorithm='ES256')
    return f'vapid t={token}, k={key_row.public_key}'


def send(sub, key_row, message: dict) -> int:
    """Trả mã HTTP của dịch vụ push (201 là nhận). 404/410 = máy đã hủy đăng ký."""
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), sub.p256dh, sub.auth)
    req = urllib.request.Request(sub.endpoint, data=body, method='POST', headers={
        'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream', 'TTL': '86400',
        'Urgency': 'high', 'Authorization': _vapid(sub.endpoint, key_row)})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        log.warning('web push: không gửi được tới %s', urlsplit(sub.endpoint).netloc)
        return 0


def subscribe(session, user, data: dict):
    try:
        endpoint, p256dh, auth = data['endpoint'], data['keys']['p256dh'], data['keys']['auth']
        if not str(endpoint).startswith('https://') or len(unb64(p256dh)) != 65 or len(unb64(auth)) != 16:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise BusinessError('BAD_PUSH', 'Trình duyệt gửi đăng ký thông báo chưa hợp lệ.')
    row = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if row is None:
        row = PushSubscription(user_id=user.id, endpoint=endpoint, p256dh=p256dh, auth=auth)
        session.add(row)
    elif row.user_id != user.id or row.p256dh != p256dh or row.auth != auth:
        # Cùng máy đổi người đăng nhập: báo cho người mới, quên các việc đã báo của người cũ.
        row.user_id, row.p256dh, row.auth, row.seen = user.id, p256dh, auth, None
    session.commit()
    return row


def unsubscribe(session, user, endpoint: str):
    row = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint, PushSubscription.user_id == user.id))
    if row is not None:
        session.delete(row)
        session.commit()


def run_once(session, sender=send) -> int:
    """Một vòng: mỗi máy đã đăng ký nhận việc mới chưa báo. Lần đầu chỉ ghi nhớ, không báo việc cũ."""
    subs = list(session.scalars(select(PushSubscription).order_by(PushSubscription.user_id, PushSubscription.id)))
    if not subs:
        return 0
    key_row, sent, cache = keys(session), 0, {}
    for sub in subs:
        user = session.get(User, sub.user_id)
        if user is None or not user.active:
            continue
        if user.id not in cache:
            cache[user.id] = notifications.with_reads(session, user, notifications.items(session, user))
        items = cache[user.id]
        ids = [item['id'] for item in items]
        if sub.seen is None:
            sub.seen = json.dumps(ids)
            continue
        known = set(json.loads(sub.seen))
        fresh = [item for item in items if item['id'] not in known and not item['read']]
        gone = False
        for item in fresh[:MAX_PER_ROUND]:
            status = sender(sub, key_row, dict(title=item['title'], body=item['text'], url=item['link'], tag=item['id']))
            if status in (404, 410):
                session.delete(sub)
                gone = True
                break
            sent += 1
        if gone:
            continue
        if len(fresh) > MAX_PER_ROUND:
            sender(sub, key_row, dict(title='Thành Đức', body=f'Và {len(fresh) - MAX_PER_ROUND} việc mới khác', url='/', tag='more'))
        sub.seen = json.dumps(ids)
    session.commit()
    return sent


def _loop():
    from .db import engine
    eng = engine()
    while True:
        try:
            with eng.connect() as conn:
                locked = conn.execute(text('select pg_try_advisory_lock(:k)'), dict(k=LOCK_ID)).scalar()
                conn.commit()  # khóa theo phiên vẫn giữ; không để kết nối treo trong giao dịch.
                if locked:
                    from sqlalchemy.orm import Session
                    while True:
                        with Session(bind=eng, expire_on_commit=False) as session:
                            run_once(session)
                        time.sleep(EVERY)
        except Exception:  # noqa: BLE001 - vòng nền không được làm sập app; thử lại sau.
            log.exception('web push loop')
        time.sleep(EVERY)


def start():
    threading.Thread(target=_loop, name='web-push', daemon=True).start()

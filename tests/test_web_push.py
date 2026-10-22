"""Web Push: mã hóa đúng RFC 8291, VAPID đúng RFC 8292, chỉ báo việc mới của đúng người, máy hủy thì bỏ."""
import json
import os
import struct

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select

from app import web_push
from app.models import PushSubscription
from test_my_work import people, purchase_people, ticket
from test_web import service_login


def hkdf(salt, ikm, info, n):
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)


def device():
    private = ec.generate_private_key(ec.SECP256R1())
    return private, os.urandom(16)


def decrypt(body, private, auth):
    # Phía trình duyệt (RFC 8291 §3.4), viết độc lập với web_push.encrypt.
    salt, rs, idlen = body[:16], *struct.unpack('!IB', body[16:21])
    sender_raw = body[21:21+idlen]
    ua_raw = web_push._raw(private.public_key())
    shared = private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_raw))
    ikm = hkdf(auth, shared, b'WebPush: info\x00' + ua_raw + sender_raw, 32)
    plain = AESGCM(hkdf(salt, ikm, b'Content-Encoding: aes128gcm\x00', 16)).decrypt(
        hkdf(salt, ikm, b'Content-Encoding: nonce\x00', 12), body[21+idlen:], None)
    assert rs == 4096 and plain.endswith(b'\x02')
    return plain[:-1]


def sub_json(private, auth, endpoint='https://push.example/abc'):
    return json.dumps(dict(endpoint=endpoint, keys=dict(p256dh=web_push.b64(web_push._raw(private.public_key())), auth=web_push.b64(auth))))


def test_encrypt_and_vapid(db):
    private, auth = device()
    body = web_push.encrypt('Mới giao · DV-26'.encode(), web_push.b64(web_push._raw(private.public_key())), web_push.b64(auth))
    assert decrypt(body, private, auth).decode() == 'Mới giao · DV-26'
    row = web_push.keys(db)
    assert web_push.keys(db).public_key == row.public_key  # tạo một lần, dùng lại
    header = web_push._vapid('https://fcm.googleapis.com/fcm/send/xyz', row)
    token = header.split('t=')[1].split(',')[0]
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), web_push.unb64(row.public_key))
    claims = jwt.decode(token, public, algorithms=['ES256'], audience='https://fcm.googleapis.com')
    assert claims['sub'].startswith('https://') and header.endswith('k=' + row.public_key)


def test_subscribe_then_only_new_work_is_pushed(client, db, people):
    p = people
    csrf = service_login(client, p['technician'])
    assert client.get('/ca-nhan/thong-bao/khoa').json()['public_key']
    private, auth = device()
    assert client.post('/ca-nhan/thong-bao/dang-ky', data=dict(subscription=sub_json(private, auth))).status_code == 403
    assert client.post('/ca-nhan/thong-bao/dang-ky', data=dict(csrf=csrf, subscription='{"endpoint":"http://x"}')).status_code == 400
    assert client.post('/ca-nhan/thong-bao/dang-ky', data=dict(csrf=csrf, subscription=sub_json(private, auth))).status_code == 200
    ticket(db, p, 'DA_GIAO')  # có trước khi bật: không báo lại
    sent = []
    def fake(sub, key_row, message):
        sent.append((sub.user_id, json.loads(decrypt(web_push.encrypt(json.dumps(message).encode(), sub.p256dh, sub.auth), private, auth))))
        return 201
    assert web_push.run_once(db, fake) == 0 and sent == []
    ticket(db, p, 'DA_GIAO', who='other_tech')
    assert web_push.run_once(db, fake) == 0
    mine = ticket(db, p, 'DA_GIAO')
    assert web_push.run_once(db, fake) == 1
    assert sent == [(p['technician'].id, dict(title='Mới giao · chờ nhận', body=f'{mine.number} · Minh Phát', url=f'/dich-vu/{mine.id}', tag=f'service_assigned:{mine.id}'))]
    assert web_push.run_once(db, fake) == 0  # đã báo thì không báo lại
    # Máy cùng endpoint đăng nhập người khác: chuyển sang người mới, quên lịch sử.
    csrf = service_login(client, p['other_tech'])
    client.post('/ca-nhan/thong-bao/dang-ky', data=dict(csrf=csrf, subscription=sub_json(private, auth)))
    row = db.scalar(select(PushSubscription));db.refresh(row)
    assert row.user_id == p['other_tech'].id and row.seen is None


def test_gone_device_is_removed_and_unsubscribe(client, db, people):
    p = people
    csrf = service_login(client, p['technician'])
    private, auth = device()
    client.post('/ca-nhan/thong-bao/dang-ky', data=dict(csrf=csrf, subscription=sub_json(private, auth)))
    web_push.run_once(db, lambda *a: 201)
    ticket(db, p, 'DA_GIAO')
    web_push.run_once(db, lambda *a: 410)
    assert db.scalar(select(PushSubscription)) is None
    client.post('/ca-nhan/thong-bao/dang-ky', data=dict(csrf=csrf, subscription=sub_json(private, auth)))
    assert client.post('/ca-nhan/thong-bao/huy', data=dict(csrf=csrf, endpoint='https://push.example/abc')).status_code == 200
    db.expire_all();assert db.scalar(select(PushSubscription)) is None


def test_service_worker_and_manifest(client, people):
    r = client.get('/sw.js')
    assert r.status_code == 200 and 'showNotification' in r.text and 'javascript' in r.headers['content-type']
    service_login(client, people['technician'])
    page = client.get('/tho').text
    assert 'rel="manifest"' in page and 'data-push-toggle' in page
    assert client.get('/static/manifest.webmanifest').status_code == 200

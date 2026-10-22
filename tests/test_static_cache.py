"""Tệp giao diện (CSS/JS) luôn được trình duyệt hỏi lại máy chủ, để bản cập nhật hiện ngay."""


def test_static_files_revalidate(client):
    r = client.get('/static/app.css')
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-cache' and r.headers.get('etag')
    again = client.get('/static/app.css', headers={'if-none-match': r.headers['etag']})
    assert again.status_code == 304

"""Tệp giao diện gọi thẳng được trình duyệt hỏi lại máy chủ; tệp có ?v= (asset) giữ lâu vì bản mới là URL mới."""


def test_static_files_revalidate(client):
    r = client.get('/static/app.css')
    assert r.status_code == 200 and r.headers['cache-control'] == 'no-cache' and r.headers.get('etag')
    again = client.get('/static/app.css', headers={'if-none-match': r.headers['etag']})
    assert again.status_code == 304


def test_versioned_assets_cached_long(client):
    # TD3: URL có ?v=<dấu nội dung> từ asset(): đổi nội dung là đổi URL, nên trình duyệt giữ lâu, không hỏi lại.
    from app.web import asset
    url = asset('app.css')
    assert '?v=' in url
    r = client.get(url)
    assert r.status_code == 200 and r.headers['cache-control'] == 'public, max-age=31536000, immutable'

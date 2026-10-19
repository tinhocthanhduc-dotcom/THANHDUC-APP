"""Trình duyệt test lấy khóa từ HTML thật; form_key chỉ tự thêm cho helper cũ.

Test cố tình thiếu khóa dùng client.request('POST', ...); không có đường tắt
nào trong ứng dụng. Form đã gửi form_key tường minh được giữ nguyên để test replay.
"""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from fastapi.testclient import TestClient


class Forms(HTMLParser):
    def __init__(self, html, url):
        super().__init__()
        self.url = str(url)
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = {'method': attrs.get('method', 'get').lower(),
                            'path': urlsplit(urljoin(self.url, attrs.get('action', self.url))).path,
                            'fields': {}}
            self.forms.append(self.current)
        elif tag == 'input' and self.current is not None and attrs.get('name'):
            self.current['fields'][attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None


def key_for(client, path):
    """Mở form tại URL tương ứng hoặc trang cha chứa form hành động đó."""
    path = urlsplit(str(path)).path
    candidate = path
    fallback = None
    while True:
        page = client.get(candidate, follow_redirects=True)
        forms = Forms(page.text, page.url).forms
        for form in forms:
            key = form['fields'].get('form_key')
            if form['method'] == 'post' and key:
                if form['path'] == path:
                    return key
                fallback = key
        if candidate == '/':
            break
        candidate = candidate.rsplit('/', 1)[0] or '/'
    # Test quyền cố tình POST route không được thấy form: vẫn dùng khóa đã vẽ
    # trên trang của user, để server kiểm quyền thay vì thiếu khóa che mất lỗi 403.
    assert fallback, f'Không tìm thấy form_key trong trang cho {path}'
    return fallback


class FormClient(TestClient):
    def post(self, url, **kwargs):
        data = kwargs.get('data')
        if isinstance(data, dict) and 'csrf' in data and 'form_key' not in data:
            kwargs['data'] = {**data, 'form_key': key_for(self, url)}
        return super().post(url, **kwargs)

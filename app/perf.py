"""TD1: đo tốc độ mở màn. Chỉ ghi số đo ra tệp nhật ký, không đụng dữ liệu nghiệp vụ.

Mỗi lượt gọi máy chủ ghi một dòng: màn nào, mất bao lâu, bao nhiêu câu truy vấn, thời gian chờ database,
dung lượng trả về. Câu truy vấn chậm ghi riêng (chỉ câu lệnh, không ghi giá trị tham số).
Trình duyệt gửi thêm thời điểm trang thao tác được (/ca-nhan/toc-do) để đo cả đường mạng tới điện thoại.
Tệp theo ngày (giờ Việt Nam) trong thư mục tệp đính kèm/_toc-do, giữ 14 ngày, hai worker cùng ghi nối đuôi.
"""
import contextvars
import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import event
from sqlalchemy.engine import Engine

from .config import get_settings

VN = ZoneInfo('Asia/Ho_Chi_Minh')
SLOW_SQL_MS = 200
KEEP_DAYS = 14
TARGET_MS = 2000
# Hỏi nền 15 giây một lần (chuông, Việc của tôi, màn thợ, điều phối): tách riêng khỏi lượt mở màn.
POLLING = ('/ca-nhan/thong-bao', '/viec-cua-toi/moi', '/tho/moi', '/dieu-phoi/moi')

_current = contextvars.ContextVar('td_perf', default=None)
_lock = threading.Lock()
_cleaned = set()


def folder() -> Path:
    return Path(get_settings().files_dir) / '_toc-do'


def _file(day) -> Path:
    return folder() / f'toc-do-{day.isoformat()}.jsonl'


def _cleanup(today):
    if today in _cleaned:
        return
    _cleaned.add(today)
    oldest = today - timedelta(days=KEEP_DAYS - 1)
    for path in folder().glob('toc-do-*.jsonl'):
        try:
            if datetime.strptime(path.stem[7:], '%Y-%m-%d').date() < oldest:
                path.unlink()
        except (ValueError, OSError):
            pass


def write(rows):
    """Ghi nối đuôi; lỗi ổ đĩa không bao giờ làm hỏng lượt mở màn."""
    if not rows:
        return
    try:
        now = datetime.now(VN)
        folder().mkdir(parents=True, exist_ok=True)
        _cleanup(now.date())
        data = ''.join(json.dumps(dict(t=round(now.timestamp(), 1), **row), ensure_ascii=False) + '\n' for row in rows)
        with _lock, open(_file(now.date()), 'a', encoding='utf-8') as f:
            f.write(data)
    except OSError:
        pass


@event.listens_for(Engine, 'before_cursor_execute')
def _before(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault('td_perf_start', []).append(time.perf_counter())


@event.listens_for(Engine, 'after_cursor_execute')
def _after(conn, cursor, statement, parameters, context, executemany):
    starts = conn.info.get('td_perf_start')
    if not starts:
        return
    ms = (time.perf_counter() - starts.pop()) * 1000
    rec = _current.get()
    if rec is None:
        return
    rec['q'] += 1
    rec['db'] += ms
    if ms >= SLOW_SQL_MS:
        rec['slow'].append(dict(ms=round(ms), sql=' '.join(statement.split())[:600]))


def _route(scope):
    route = scope.get('route')
    if route is not None and getattr(route, 'path', None):
        return route.path
    path = scope.get('path', '')
    return '/static/*' if path.startswith('/static/') else path[:120]


class Middleware:
    """ASGI ngoài cùng: bấm giờ từ lúc nhận yêu cầu tới byte cuối cùng gửi đi."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not get_settings().perf_log:
            return await self.app(scope, receive, send)
        rec = dict(q=0, db=0.0, slow=[], st=0, b=0)
        token = _current.set(rec)
        start = time.perf_counter()

        async def counted(message):
            if message['type'] == 'http.response.start':
                rec['st'] = message['status']
            elif message['type'] == 'http.response.body':
                rec['b'] += len(message.get('body', b''))
            await send(message)
        try:
            await self.app(scope, receive, counted)
        finally:
            _current.reset(token)
            route = _route(scope)
            row = dict(k='s', m=scope.get('method', ''), r=route, st=rec['st'], ms=round((time.perf_counter() - start) * 1000, 1),
                       q=rec['q'], db=round(rec['db'], 1), b=rec['b'])
            write([row] + [dict(k='q', r=route, **s) for s in rec['slow']])


def browser(route, form):
    """Số đo trình duyệt gửi về: ttfb (chờ máy chủ + mạng), ready (thao tác được), load, kb tải."""
    def num(key, top=600000):
        try:
            value = float(form.get(key, ''))
        except (TypeError, ValueError):
            return None
        return round(value, 1) if 0 <= value <= top else None
    ready = num('ready')
    if ready is None or not str(route).startswith('/'):
        return
    write([dict(k='b', r=str(route)[:120], nav=str(form.get('nav', ''))[:20], ttfb=num('ttfb'), ready=ready, load=num('load'), kb=num('kb', 10**6))])


def _pct(values, p):
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


def read(days=1, today=None):
    today = today or datetime.now(VN).date()
    rows = []
    for i in range(days):
        path = _file(today - timedelta(days=i))
        if not path.exists():
            continue
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def kind(route, method):
    if route == '/ca-nhan/toc-do':
        return 'Số đo trình duyệt'
    if route in POLLING:
        return 'Hỏi nền 15 giây'
    if route.startswith('/static'):
        return 'CSS/JS/ảnh giao diện'
    if route.startswith('/attachments'):
        return 'Ảnh, tệp đính kèm'
    if method != 'GET':
        return 'Lưu/gửi'
    return 'Mở màn'


def summary(days=1, today=None):
    rows = read(days, today)
    server, sql, br = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        k = row.get('k')
        if k == 's':
            server[(row.get('m', ''), row.get('r', ''))].append(row)
        elif k == 'q':
            sql[row.get('sql', '')].append(row)
        elif k == 'b' and row.get('nav') in ('navigate', 'reload', ''):
            br[row.get('r', '')].append(row)
    pages = []
    for (method, route), items in server.items():
        ms = [r['ms'] for r in items]
        q = [r.get('q', 0) for r in items]
        pages.append(dict(method=method, route=route, kind=kind(route, method), n=len(items), p50=_pct(ms, .5), p95=_pct(ms, .95),
                          max=max(ms), q=round(sum(q) / len(q), 1), qmax=max(q), db=round(sum(r.get('db', 0) for r in items) / len(items), 1),
                          kb=round(sum(r.get('b', 0) for r in items) / len(items) / 1024, 1), total=round(sum(ms) / 1000, 1)))
    pages.sort(key=lambda p: -p['total'])
    screens = []
    for route, items in br.items():
        ready = [r['ready'] for r in items]
        screens.append(dict(route=route, n=len(items), p50=_pct(ready, .5), p95=_pct(ready, .95),
                            ok=round(100 * sum(1 for v in ready if v <= TARGET_MS) / len(ready)),
                            ttfb=_pct([r['ttfb'] for r in items if r.get('ttfb') is not None], .5),
                            kb=round(sum(r.get('kb') or 0 for r in items) / len(items), 1)))
    screens.sort(key=lambda s: (s['ok'], -s['n']))
    slow = [dict(sql=text, n=len(items), max=max(r['ms'] for r in items), avg=round(sum(r['ms'] for r in items) / len(items)),
                 routes=', '.join(sorted({r.get('r', '') for r in items}))[:200]) for text, items in sql.items()]
    slow.sort(key=lambda s: -s['n'] * s['avg'])
    all_ready = [r['ready'] for items in br.values() for r in items]
    open_ms = [p for (m, r), items in server.items() if kind(r, m) == 'Mở màn' for p in (i['ms'] for i in items)]
    total = sum(p['total'] for p in pages) or 1
    polling = sum(p['total'] for p in pages if p['kind'] == 'Hỏi nền 15 giây')
    return dict(pages=pages, screens=screens, slow=slow,
                browser_n=len(all_ready), browser_ok=round(100 * sum(1 for v in all_ready if v <= TARGET_MS) / len(all_ready)) if all_ready else None,
                open_n=len(open_ms), open_p95=_pct(open_ms, .95), polling_share=round(100 * polling / total),
                polling_n=sum(p['n'] for p in pages if p['kind'] == 'Hỏi nền 15 giây'), slow_n=sum(s['n'] for s in slow))

"""TD1: trang Tốc độ app (chỉ đọc tệp số đo) và chỗ nhận số đo trình duyệt gửi về."""
import hmac

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import perf
from ..errors import BusinessError
from ..db import get_session
from ..models import User
from . import nav, render, web_user

router = APIRouter()

PERIODS = {'hom-nay': ('Hôm nay', 1), '7-ngay': ('7 ngày', 7), '14-ngay': ('14 ngày', perf.KEEP_DAYS)}


@router.get('/toc-do')
async def page(request: Request, ky: str = 'hom-nay', user: User = Depends(web_user)):
    ky = ky if ky in PERIODS else 'hom-nay'
    data = await run_in_threadpool(perf.summary, PERIODS[ky][1])
    return render(request, 'toc_do.jinja', d=data, ky=ky, periods=PERIODS, target=perf.TARGET_MS, slow_ms=perf.SLOW_SQL_MS, enabled=perf.get_settings().perf_log)


@router.post('/ca-nhan/toc-do')
async def beacon(request: Request, user: User = Depends(web_user)):
    # Chỉ ghi số đo của chính trang người dùng vừa mở; kiểm csrf để trang lạ không ghi được.
    form = await request.form()
    if not hmac.compare_digest(str(form.get('csrf', '')), request.state.csrf):
        raise BusinessError('BAD_CSRF', 'Trang đã cũ, hãy tải lại rồi thử lại.', status=403)
    if perf.get_settings().perf_log:
        await run_in_threadpool(perf.browser, form.get('route', ''), form)
    return Response(status_code=204)


def usage(rows, users):
    """Lượt mở màn theo mục menu: chỉ GET loại Mở màn thành công; hỏi nền, tệp tĩnh, lưu/gửi không tính."""
    from collections import defaultdict
    from datetime import datetime
    opens, days, people, unknown = defaultdict(int), defaultdict(set), defaultdict(lambda: defaultdict(int)), defaultdict(int)
    for row in rows:
        if row.get('k') != 's' or row.get('m') != 'GET' or not (200 <= int(row.get('st') or 0) < 400):
            continue
        route = row.get('r', '')
        if perf.kind(route, 'GET') != 'Mở màn':
            continue
        item = nav.matching(route)
        if item is None:
            continue
        key = item['href']
        opens[key] += 1
        days[key].add(datetime.fromtimestamp(row.get('t', 0), perf.VN).date())
        if row.get('u') is None:
            unknown[key] += 1
        else:
            people[key][row['u']] += 1
    result, unused = [], []
    for group in nav.GROUPS:
        for item in group['items']:
            if item.get('hidden'):
                continue
            key = item['href']
            entry = dict(group=group['label'] or 'Của tôi', label=item['label'], href=key, n=opens.get(key, 0), days=len(days.get(key, ())),
                         who=sorted(((users.get(uid, f'#{uid}'), n) for uid, n in people.get(key, {}).items()), key=lambda x: -x[1]),
                         unknown=unknown.get(key, 0))
            (result if entry['n'] else unused).append(entry)
    result.sort(key=lambda e: -e['n'])
    return result, unused


@router.get('/toc-do/man-dung')
def screen_usage(request: Request, ky: str = '14-ngay', user: User = Depends(web_user), session: Session = Depends(get_session)):
    ky = ky if ky in PERIODS else '14-ngay'
    rows = perf.read(PERIODS[ky][1])
    users = dict(session.execute(select(User.id, User.full_name)).all())
    used, unused = usage(rows, users)
    return render(request, 'toc_do_man_dung.jinja', used=used, unused=unused, ky=ky, periods=PERIODS, enabled=perf.get_settings().perf_log,
                  total=sum(e['n'] for e in used))

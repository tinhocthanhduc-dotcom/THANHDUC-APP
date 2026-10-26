"""TD1: trang Tốc độ app (chỉ đọc tệp số đo) và chỗ nhận số đo trình duyệt gửi về."""
import hmac

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .. import perf
from ..errors import BusinessError
from ..models import User
from . import render, web_user

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

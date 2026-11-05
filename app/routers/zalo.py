"""Cửa Zalo gọi vào khi có tin mới. Không đăng nhập; chỉ nhận khi đúng khóa bí mật trong header."""
import json

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .. import zalo_bot as svc

router = APIRouter()
MAX_BODY = 256 * 1024


@router.post('/zalo/webhook', include_in_schema=False)
async def webhook(request: Request, background: BackgroundTasks):
    if not svc.valid_secret(request.headers.get('x-bot-api-secret-token', '')):
        return JSONResponse({'ok': False}, status_code=403)
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({'ok': False}, status_code=413)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({'ok': False}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({'ok': False}, status_code=400)
    notice = await run_in_threadpool(svc.receive_event, payload)
    if notice:  # Trả lời Zalo trước, gửi lời báo lần đầu sau.
        background.add_task(svc.send_notice, notice)
    return {'ok': True}

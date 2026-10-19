"""Khóa form web bằng transaction riêng, không thay đổi transaction nghiệp vụ.

Lỗi không dự kiến giữ khóa: chứng từ có thể đã commit trước lỗi đó.
API JSON không gọi reserve nên không tham gia cơ chế này.
"""
import argparse
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse

from .db import engine
from .errors import BusinessError
from .models import FormSubmission


class Replay(Exception):
    def __init__(self, url: str):
        self.url = url


def form_key() -> str:
    return str(uuid4())


def reserve(value, user_id: int, path: str) -> UUID:
    try:
        key = UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise BusinessError("BAD_FORM_KEY", "Trang đã cũ, hãy tải lại rồi thử lại.", status=400)
    with Session(engine()) as session, session.begin():
        while True:
            inserted = session.scalar(insert(FormSubmission).values(
                key=key, user_id=user_id, path=path, status="DANG_XU_LY",
            ).on_conflict_do_nothing(index_elements=["key"]).returning(FormSubmission.key))
            if inserted is not None:
                break
            previous = session.get(FormSubmission, key)
            # Lần gửi trước có thể vừa gỡ khóa sau lỗi, giữa INSERT và SELECT.
            # READ COMMITTED cho phép thử giữ lại khóa trong cùng transaction.
            if previous is None:
                continue
            if previous.user_id != user_id:
                raise BusinessError("FORBIDDEN", "Bạn không có quyền sử dụng form này.", status=403)
            if previous.status == "XONG":
                raise Replay(previous.result_url)
            raise BusinessError("ALREADY_SUBMITTED", "Yêu cầu này đang được xử lý, hãy tải lại trang để xem kết quả.", status=409)
    return key


def finish(key: UUID, url: str) -> None:
    with Session(engine()) as session, session.begin():
        session.execute(update(FormSubmission).where(
            FormSubmission.key == key, FormSubmission.status == "DANG_XU_LY",
        ).values(status="XONG", result_url=url, finished_at=datetime.now(timezone.utc)))


def release(key: UUID) -> None:
    with Session(engine()) as session, session.begin():
        session.execute(delete(FormSubmission).where(
            FormSubmission.key == key, FormSubmission.status == "DANG_XU_LY",
        ))


class SubmissionMiddleware:
    """Chốt khóa trước khi gửi response, cả route bắt lỗi và vẽ lại form."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)

        async def respond(message):
            key = scope.get("state", {}).get("form_submission_key")
            if message["type"] == "http.response.start" and key is not None:
                status = message["status"]
                location = dict(message.get("headers", [])).get(b"location")
                if status == 303 and location:
                    await run_in_threadpool(finish, key, location.decode("latin-1"))
                elif status < 500 or scope["state"].get("form_business_error"):
                    await run_in_threadpool(release, key)
            await send(message)

        await self.app(scope, receive, respond)


def install(app) -> None:
    async def replay_handler(request, exc: Replay):
        return RedirectResponse(exc.url, status_code=303)

    app.add_exception_handler(Replay, replay_handler)
    app.add_middleware(SubmissionMiddleware)


def purge() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    with Session(engine()) as session, session.begin():
        return session.execute(delete(FormSubmission).where(FormSubmission.created_at < cutoff)).rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description="Dọn khóa form cũ hơn 30 ngày.")
    parser.add_argument("command", choices=["purge"])
    parser.parse_args()
    print(f"Đã xóa {purge()} khóa form cũ hơn 30 ngày.")


if __name__ == "__main__":
    main()

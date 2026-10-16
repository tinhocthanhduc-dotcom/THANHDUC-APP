from fastapi import Request
from fastapi.responses import JSONResponse


class BusinessError(Exception):
    """Lỗi nghiệp vụ có mã cố định để giao diện Apps Script hiển thị đúng thông báo."""

    status = 400

    def __init__(self, code: str, message: str = "", details: dict | None = None, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.message = message or code
        self.details = details or {}
        if status:
            self.status = status


async def business_error_handler(_: Request, exc: BusinessError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "message": exc.message, "details": exc.details},
    )

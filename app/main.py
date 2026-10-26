from fastapi import Depends, FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import web
from . import form_keys
from .db import get_session
from .errors import BusinessError, business_error_handler
from .routers import sales_returns, equipment_custody, part_requests, purchase_returns, supplier_debts, purchase_receipts, purchase_orders, attachments, auth, customers, products, quotes, sales_orders, sales_receipts, service_tickets, stock_docs, suppliers, warehouses

from .routers import production

app = FastAPI(title="Thành Đức Core", version="0.2.0")
form_keys.install(app)
app.include_router(production.router)
from .routers import recovered_parts
app.include_router(recovered_parts.router)
app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(products.router)
app.include_router(suppliers.router)
app.include_router(warehouses.router)
app.include_router(stock_docs.router)
app.include_router(service_tickets.router)
app.include_router(part_requests.router)
app.include_router(equipment_custody.router)
app.include_router(attachments.router)
app.include_router(quotes.router)
app.include_router(sales_receipts.router)
app.include_router(sales_orders.router)
app.include_router(purchase_orders.router)
app.include_router(purchase_receipts.router)
app.include_router(purchase_returns.router)
app.include_router(supplier_debts.router)
app.include_router(sales_returns.router)
app.include_router(web.router)
app.mount("/static", web.static, name="static")
app.add_exception_handler(web.LoginRequired, web.login_required_handler)
app.add_exception_handler(web.PasswordChangeRequired,web.password_change_handler)
app.add_exception_handler(StarletteHTTPException,web.get_on_post_url)


@app.exception_handler(BusinessError)
async def business_error(request: Request, exc: BusinessError):
    # Trang web hiện trang lỗi tiếng Việt; API trả JSON cho app gọi vào.
    if getattr(request.state, "web", False):
        return await web.web_error_page(request, exc)
    return await business_error_handler(request, exc)


@app.on_event("startup")
def start_push():
    from .config import get_settings
    if get_settings().push_loop:
        from . import web_push
        web_push.start()


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Service worker phải nằm ở gốc để nhận thông báo cho mọi trang.
    from fastapi.responses import FileResponse
    from pathlib import Path
    return FileResponse(Path(__file__).parent / "web" / "static" / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/health", tags=["Hệ thống"])
def health(session: Session = Depends(get_session)):
    session.execute(text("select 1"))
    return {"ok": True}

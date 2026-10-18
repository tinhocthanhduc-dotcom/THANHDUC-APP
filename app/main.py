from fastapi import Depends, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import web
from .db import get_session
from .errors import BusinessError, business_error_handler
from .routers import purchase_orders, attachments, auth, customers, products, quotes, sales_orders, sales_receipts, service_tickets, stock_docs, suppliers, warehouses

app = FastAPI(title="Thành Đức Core", version="0.2.0")
app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(products.router)
app.include_router(suppliers.router)
app.include_router(warehouses.router)
app.include_router(stock_docs.router)
app.include_router(service_tickets.router)
app.include_router(attachments.router)
app.include_router(quotes.router)
app.include_router(sales_receipts.router)
app.include_router(sales_orders.router)
app.include_router(purchase_orders.router)
app.include_router(web.router)
app.mount("/static", web.static, name="static")
app.add_exception_handler(web.LoginRequired, web.login_required_handler)


@app.exception_handler(BusinessError)
async def business_error(request: Request, exc: BusinessError):
    # Trang web hiện trang lỗi tiếng Việt; API trả JSON cho app gọi vào.
    if getattr(request.state, "web", False):
        return await web.web_error_page(request, exc)
    return await business_error_handler(request, exc)


@app.get("/health", tags=["Hệ thống"])
def health(session: Session = Depends(get_session)):
    session.execute(text("select 1"))
    return {"ok": True}

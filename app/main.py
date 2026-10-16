from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import get_session
from .errors import BusinessError, business_error_handler
from .routers import auth, customers, products

app = FastAPI(title="Thành Đức Core", version="0.1.0")
app.add_exception_handler(BusinessError, business_error_handler)
app.include_router(auth.router)
app.include_router(customers.router)
app.include_router(products.router)


@app.get("/health", tags=["Hệ thống"])
def health(session: Session = Depends(get_session)):
    session.execute(text("select 1"))
    return {"ok": True}

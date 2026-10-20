"""Test chạy trên PostgreSQL thật. Đặt TD_TEST_DATABASE_URL tới một database rỗng dành riêng cho test."""
import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from web_forms import FormClient
from sqlalchemy import create_engine, text

TEST_URL = os.environ.get("TD_TEST_DATABASE_URL", "postgresql+psycopg://postgres@localhost:5432/thanhduc_test")
os.environ["TD_DATABASE_URL"] = TEST_URL
os.environ["TD_SECRET_KEY"] = "test-secret-key-at-least-32-bytes-long"
os.environ["TD_FILES_DIR"] = tempfile.mkdtemp(prefix="td-files-")

from app.config import get_settings  # noqa: E402
from app.db import get_session, reset_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402

TABLES = "sales_return_lines, sales_returns, sales_refunds, sales_delivery_attempts, sales_delivery_lines, sales_deliveries, stock_reservations, form_submissions, equipment_custody, part_requests, daily_digests, purchase_return_lines, purchase_returns, supplier_payments, supplier_opening_debts, purchase_receipt_lines, purchase_receipts, purchase_order_lines, purchase_orders, cash_corrections, day_closings, fund_ledger, fund_balances, cash_docs, finance_mappings, cash_categories, funds, sales_receipts, sales_order_lines, sales_orders, quote_lines, quotes, attachments, equipment, customer_sites, service_steps, service_tickets, stock_min_levels, stock_ledger, stock_balances, stock_doc_lines, stock_docs, audit_log, customer_phones, customer_contacts, customers, products, suppliers, warehouses, users, doc_counters"
PASSWORD = "matkhau-test-123"


@pytest.fixture(scope="session")
def engine():
    get_settings.cache_clear()
    reset_engine()
    eng = create_engine(TEST_URL)
    with eng.begin() as conn:
        conn.execute(text("drop schema public cascade; create schema public"))
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.attributes["database_url"] = TEST_URL
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean(engine):
    with engine.begin() as conn:
        conn.execute(text("alter table audit_log disable trigger audit_log_readonly"))
        conn.execute(text(f"truncate {TABLES} restart identity cascade"))
        conn.execute(text("alter table audit_log enable trigger audit_log_readonly"))
        conn.execute(text("alter sequence ktd_seq restart with 1"))
        conn.execute(text("alter sequence ncc_seq restart with 1"))
        # Các dòng cấu hình cố định do migration tạo, phục hồi sau mỗi lần truncate.
        conn.execute(text("insert into finance_mappings (key, version) values "
                          "('THU_TIEN_MAT',1),('THU_CHUYEN_KHOAN',1),"
                          "('DANH_MUC_BAN_HANG',1),('DANH_MUC_DICH_VU',1),('DANH_MUC_MUA_HANG',1),('DANH_MUC_HOAN_TIEN_BAN',1)"))
    yield


@pytest.fixture
def db(engine):
    gen = get_session()
    session = next(gen)
    yield session
    session.close()


@pytest.fixture
def client(engine):
    with FormClient(app) as c:
        yield c


@pytest.fixture
def make_user(db):
    def _make(username: str, *roles: str) -> User:
        u = User(username=username, full_name=username.title(), password_hash=hash_password(PASSWORD), roles=list(roles))
        db.add(u)
        db.commit()
        return u

    return _make


@pytest.fixture
def login(client, make_user):
    def _login(username: str, *roles: str) -> dict:
        make_user(username, *roles)
        r = client.post("/auth/login", data={"username": username, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["access_token"]}

    return _login


@pytest.fixture
def configured_finance(db,make_user):
    """T3: chỉ fixture cần xác nhận BT mới ghép, không tự cấu hình ở production."""
    from app import finance_catalogs,finance_mappings
    from app.models import Fund,CashCategory
    from app.schemas import FundIn,CashCategoryIn
    director=make_user('mapping.setup','director')
    for code,kind,account in [('QUY-TM','TIEN_MAT',''),('TK-ACB','NGAN_HANG','123456789')]:
        finance_catalogs.create(db,director,Fund,FundIn(code=code,name=code,kind=kind,account_no=account))
    for code in ('THU-BH','THU-DV'):
        finance_catalogs.create(db,director,CashCategory,CashCategoryIn(code=code,name=code,direction='THU'))
    for key,target in [('THU_TIEN_MAT','QUY-TM'),('THU_CHUYEN_KHOAN','TK-ACB'),
                       ('DANH_MUC_BAN_HANG','THU-BH'),('DANH_MUC_DICH_VU','THU-DV')]:
        finance_mappings.set_mapping(db,director,key,version=1,
            **({'fund_code':target} if key.startswith('THU_') else {'category_code':target}))
    return director

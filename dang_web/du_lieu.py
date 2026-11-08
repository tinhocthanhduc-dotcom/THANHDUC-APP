"""Đọc danh mục hàng từ database của app. Phiên kết nối đặt CHỈ ĐỌC: lỡ có câu lệnh ghi, PostgreSQL sẽ từ chối."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

READ_ONLY_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=30000"

CAU_SAN_PHAM = """
select p.code, p.name, p.group_name, p.unit, p.sale_price, p.warranty_months,
       coalesce(sum(b.qty) filter (where w.kind = 'KHO' and w.active), 0) as ton
from products p
left join stock_balances b on b.product_code = p.code
left join warehouses w on w.code = b.warehouse_code
where p.active and not p.is_test and p.code = any(%s)
group by p.code
order by p.code
"""

CAU_DEM = "select count(*) from products where active and not is_test"


@dataclass
class SanPham:
    code: str
    name: str
    group_name: str
    unit: str
    sale_price: int
    warranty_months: int
    ton: Decimal

    @property
    def con_hang(self) -> bool:
        return self.ton > 0


def url_psycopg(url: str) -> str:
    """App dùng dạng SQLAlchemy postgresql+psycopg://; psycopg cần postgresql://."""
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return url


def ket_noi(url: str):
    import psycopg

    if not url:
        raise RuntimeError("Chưa có TD_DATABASE_URL (đường nối database của app).")
    return psycopg.connect(url_psycopg(url), options=READ_ONLY_OPTIONS, connect_timeout=10)


def lay_san_pham(conn, codes: list[str]) -> dict[str, SanPham]:
    codes = [c.strip().upper() for c in codes if c.strip()]
    if not codes:
        return {}
    with conn.cursor() as cur:
        cur.execute(CAU_SAN_PHAM, (codes,))
        rows = cur.fetchall()
    return {r[0]: SanPham(*r) for r in rows}


def dem_san_pham(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(CAU_DEM)
        return int(cur.fetchone()[0])


# Hàng mới nhập (đơn mua, sản xuất, phiếu nhập tay) vào kho bán trong N ngày, còn tồn; mới nhập nhất trước.
# Cùng điều kiện với khối "Hàng mới nhập chưa có trên web" của màn Đăng web (app/web/dang_web.py CAU_MOI_NHAP).
CAU_MOI_NHAP = """
with moi as (
  select l.product_code as code, max(d.doc_date) as ngay
  from stock_ledger l
  join stock_docs d on d.id = l.doc_id
  join warehouses w on w.code = l.warehouse_code
  where d.kind = 'PN' and d.status = 'DA_GHI' and l.qty_in > 0 and not l.is_reversal and w.kind = 'KHO'
    and (d.source_kind is null or d.source_kind in ('PURCHASE_RECEIPT', 'SAN_XUAT'))
    and d.doc_date >= current_date - %s::int
  group by l.product_code
)
select p.code, p.name, moi.ngay
from moi
join products p on p.code = moi.code
where p.active and not p.is_test
  and (select coalesce(sum(b.qty), 0) from stock_balances b join warehouses w on w.code = b.warehouse_code
       where b.product_code = p.code and w.kind = 'KHO' and w.active) > 0
order by moi.ngay desc, p.code desc
"""


def hang_moi_nhap(conn, so_ngay: int = 30) -> list[tuple[str, str]]:
    """[(mã, tên)] hàng mới nhập còn tồn, mới nhất trước."""
    with conn.cursor() as cur:
        cur.execute(CAU_MOI_NHAP, (int(so_ngay),))
        return [(r[0], r[1]) for r in cur.fetchall()]

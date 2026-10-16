"""Xóa toàn bộ khách và hàng thử nghiệm (is_test) trước khi chạy thật. Nhật ký vẫn giữ lại.

    python -m scripts.clear_test_data          # chỉ đếm
    python -m scripts.clear_test_data --yes    # xóa thật
"""
import argparse

from sqlalchemy import text

from app.db import get_session


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    session = next(get_session())
    customers = session.execute(text("select count(*) from customers where is_test")).scalar_one()
    products = session.execute(text("select count(*) from products where is_test")).scalar_one()
    print(f"Khách thử nghiệm: {customers}, hàng thử nghiệm: {products}")
    if not args.yes:
        print("Chưa xóa. Thêm --yes để xóa.")
        return
    session.execute(text("update customers set merged_into = null, status = 'NGUNG' where merged_into in (select code from customers where is_test)"))
    session.execute(text("delete from customers where is_test"))
    session.execute(text("delete from products where is_test"))
    session.commit()
    print("Đã xóa.")


if __name__ == "__main__":
    main()

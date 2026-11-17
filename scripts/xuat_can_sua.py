"""Rà soát vận hành 17/11/2026: xuất 3 bảng "cần sửa" để dọn dữ liệu khách và hàng hóa. CHỈ ĐỌC database.

Chạy trên Z2 (anh dán nguyên dòng):
  docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.tunnel.yml --env-file deploy/.env \
      exec -T api python -m scripts.xuat_can_sua

Tệp ghi vào /web/can-sua trong container, tức ~/THANHDUC-WEB/can-sua trên Z2. Mở bằng Excel (kể cả Office 2010):
UTF-16, cách bằng tab, giống tệp bảng giá tải về.
  1-khach-trung-ten.csv            các nhóm khách trùng hệt tên, kèm số đơn/phiếu để biết giữ mã nào
  2-khach-dang-giao-dich-thieu.csv khách có đơn/phiếu mà thiếu MST (công ty), SĐT hoặc địa chỉ
  3-hang-hoa-can-sua.csv           mã hàng chưa có giá, chưa nhóm, máy in chưa bật serial, chưa từng nhập kho

Giao dịch database đặt READ ONLY và rollback ở cuối; lệnh không sửa gì trong app. Tệp có tên, SĐT, MST khách:
chỉ để trong máy công ty, không gửi ra ngoài.
"""
import argparse
import csv
import io
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import engine

BASE = "c.status='DANG_DUNG' and not c.is_test and c.merged_into is null"
CUSTOMERS = f"""
select c.code, c.kind, c.name, c.tax_code, c.address,
       lower(regexp_replace(c.name, '\\s+', '', 'g')) as norm,
       coalesce((select string_agg(p.phone, ', ' order by p.phone) from customer_phones p where p.customer_code = c.code), '') as phones,
       (select count(*) from sales_orders o where o.customer_code = c.code and o.status <> 'DA_HUY') as orders,
       (select count(*) from service_tickets t where t.customer_code = c.code) as tickets
from customers c where {BASE} order by c.name, c.code"""
PRODUCTS = """
select p.code, p.name, p.group_name, p.unit, p.sale_price, p.track_serial,
       exists (select 1 from stock_ledger l where l.product_code = p.code and l.qty_in > 0) as stocked
from products p where p.active and not p.is_test order by p.group_name, p.code"""
KIND = {'CONG_TY': 'Công ty', 'CA_NHAN': 'Cá nhân'}


def duplicate_names(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[r.norm].append(r)
    out, n = [], 0
    # Nhóm có khách đang giao dịch lên trước: gộp những nhóm này trước là có ích ngay.
    for members in sorted((g for g in groups.values() if len(g) > 1), key=lambda g: (-sum(r.orders + r.tickets for r in g), g[0].name)):
        n += 1
        keep = max(members, key=lambda r: (r.orders + r.tickets, bool(r.tax_code), bool(r.phones)))
        for r in members:
            out.append([n, r.code, KIND.get(r.kind, r.kind), r.name, r.tax_code, r.phones, r.address, r.orders, r.tickets,
                        'giữ' if r is keep else f'gộp vào {keep.code}', ''])
    return out, n


def missing_fields(rows):
    out = []
    for r in rows:
        if not (r.orders + r.tickets):
            continue
        lack = [label for label, bad in (('MST', r.kind == 'CONG_TY' and not r.tax_code), ('SĐT', not r.phones), ('Địa chỉ', not (r.address or '').strip())) if bad]
        if lack:
            out.append([r.code, KIND.get(r.kind, r.kind), r.name, r.tax_code, r.phones, r.address, r.orders, r.tickets, ', '.join(lack), '', '', ''])
    return out


def product_issues(rows):
    out = []
    for r in rows:
        lack = [label for label, bad in (('Chưa có giá bán', r.sale_price <= 0), ('Chưa có nhóm hàng', not (r.group_name or '').strip()),
                                         ('Máy in chưa bật serial', 'máy in' in (r.group_name or '').lower() and not r.track_serial),
                                         ('Chưa từng nhập kho', not r.stocked)) if bad]
        if lack:
            out.append([r.code, r.name, r.group_name, r.unit, r.sale_price, '; '.join(lack), '', ''])
    return out


def write(path, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter='\t', lineterminator='\r\n')
    writer.writerow(header)
    # Xuống dòng hoặc tab trong ghi chú, địa chỉ làm lệch cột khi mở bằng Excel.
    writer.writerows([[' '.join(str(v).split()) if isinstance(v, str) else v for v in row] for row in rows])
    path.write_bytes(buf.getvalue().encode('utf-16'))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Xuất 3 bảng dữ liệu cần sửa, chỉ đọc database.')
    parser.add_argument('--thu-muc', default='/web/can-sua', help='Thư mục ghi tệp (mặc định /web/can-sua = ~/THANHDUC-WEB/can-sua trên Z2)')
    args = parser.parse_args(argv)
    with Session(engine()) as session:
        session.execute(text('set transaction read only'))
        try:
            customers = session.execute(text(CUSTOMERS)).all()
            products = session.execute(text(PRODUCTS)).all()
        finally:
            session.rollback()
    out = Path(args.thu_muc)
    out.mkdir(parents=True, exist_ok=True)
    dup, groups = duplicate_names(customers)
    lack = missing_fields(customers)
    goods = product_issues(products)
    write(out / '1-khach-trung-ten.csv',
          ['Nhóm', 'Mã khách', 'Loại', 'Tên', 'MST', 'SĐT', 'Địa chỉ', 'Số đơn bán', 'Số phiếu dịch vụ', 'Gợi ý', 'Quyết định (giữ / gộp vào mã nào / khác người)'], dup)
    write(out / '2-khach-dang-giao-dich-thieu.csv',
          ['Mã khách', 'Loại', 'Tên', 'MST', 'SĐT', 'Địa chỉ', 'Số đơn bán', 'Số phiếu dịch vụ', 'Đang thiếu', 'MST bổ sung', 'SĐT bổ sung', 'Địa chỉ bổ sung'], lack)
    write(out / '3-hang-hoa-can-sua.csv',
          ['Mã hàng', 'Tên', 'Nhóm hàng', 'Đơn vị', 'Giá bán hiện tại', 'Cần sửa', 'Giá bán đúng (chưa VAT)', 'Ghi chú'], goods)
    print(f'Đã ghi vào {out}:')
    print(f'- 1-khach-trung-ten.csv: {groups} nhóm, {len(dup)} khách')
    print(f'- 2-khach-dang-giao-dich-thieu.csv: {len(lack)} khách')
    print(f'- 3-hang-hoa-can-sua.csv: {len(goods)} mã hàng')


if __name__ == '__main__':
    main()

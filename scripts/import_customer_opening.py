"""Nhập nợ đầu kỳ khách hàng (nợ cũ trước khi dùng app, gồm VAT) từ file CSV.

    python -m scripts.import_customer_opening --csv no-dau-ky-khach.csv --user tuan [--ngay 2026-10-31] [--apply]

Cột: MA_KHACH, SO_NO (số đồng, có thể có dấu chấm), NGAY (YYYY-MM-DD hoặc DD/MM/YYYY, trống = --ngay), GHI_CHU.
Chạy thử (không --apply) không ghi gì, chỉ in từng dòng: TAO_MOI / CAP_NHAT / DA_CO / LOI.
Ghi bằng tài khoản giám đốc (--user), cùng luật màn Nợ đầu kỳ: không hạ dưới số đã thu, có nhật ký.
"""
import argparse
import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import customer_opening  # noqa: E402
from app.db import engine  # noqa: E402
from app.errors import BusinessError  # noqa: E402
from app.models import Customer, User  # noqa: E402
from app.security import has_role  # noqa: E402


def read_csv(path) -> list[dict]:
    with open(path, encoding='utf-8-sig', newline='') as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        return [{(k or '').strip().upper(): (v or '').strip() for k, v in row.items()} for row in csv.DictReader(f, dialect=dialect)]


def _amount(raw):
    s = re.sub(r'[.,\s]', '', raw or '')
    if not s.isdigit():
        raise ValueError(f'Số nợ không hợp lệ: {raw!r}')
    return int(s)


def _day(raw, default):
    raw = (raw or '').strip()
    if not raw:
        if default is None:
            raise ValueError('Thiếu ngày (cột NGAY hoặc --ngay)')
        return default
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Ngày không hợp lệ: {raw!r}')


def run(session, user, rows, default_day=None, apply=False):
    out = []
    for i, r in enumerate(rows, start=2):
        code = r.get('MA_KHACH', '')
        try:
            amount, day = _amount(r.get('SO_NO', '')), _day(r.get('NGAY', ''), default_day)
            if session.get(Customer, code) is None:
                raise ValueError(f'Không có khách {code!r}')
            row = customer_opening.get(session, code)
            note = r.get('GHI_CHU', '')
            if row is not None and (row.amount, row.as_of, row.note) == (amount, day, note):
                out.append((i, code, 'DA_CO', amount)); continue
            action = 'TAO_MOI' if row is None else 'CAP_NHAT'
            if apply:
                customer_opening.set_opening(session, user, code, amount, day, note, row.version if row else 0)
            out.append((i, code, action, amount))
        except (ValueError, BusinessError) as exc:
            session.rollback()
            out.append((i, code, 'LOI', getattr(exc, 'message', str(exc))))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='Nhập nợ đầu kỳ khách hàng từ CSV.')
    ap.add_argument('--csv', required=True)
    ap.add_argument('--user', required=True, help='Tên đăng nhập giám đốc')
    ap.add_argument('--ngay', type=date.fromisoformat, default=None, help='Ngày chốt nợ mặc định YYYY-MM-DD')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args(argv)
    with Session(engine(), expire_on_commit=False) as session:
        user = session.scalar(select(User).where(User.username == args.user.strip().lower()))
        if user is None or not user.active or not has_role(user, 'director'):
            ap.exit(1, 'Lỗi: --user phải là giám đốc đang hoạt động.\n')
        result = run(session, user, read_csv(args.csv), args.ngay, args.apply)
    total = 0
    for line, code, action, value in result:
        print(f'{line:>4}  {code:<14} {action:<9} {value}')
        if action in ('TAO_MOI', 'CAP_NHAT', 'DA_CO'):
            total += value
    errors = sum(1 for r in result if r[2] == 'LOI')
    print(f'Tổng nợ đầu kỳ hợp lệ: {total:,} đồng · {errors} dòng lỗi'.replace(',', '.'))
    print('ĐÃ GHI.' if args.apply else 'CHẠY THỬ, chưa ghi gì. Thêm --apply để ghi.')


if __name__ == '__main__':
    main()

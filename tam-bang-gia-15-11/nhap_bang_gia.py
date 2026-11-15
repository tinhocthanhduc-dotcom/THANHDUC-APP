"""Nhập bảng giá đề xuất 15/11/2026 (TUẤN duyệt). Chạy TRONG container api, thư mục /app:

    python /tmp/bg/nhap_bang_gia.py /tmp/bg            # chạy thử: sao lưu giá hiện tại + báo thay đổi, KHÔNG ghi
    python /tmp/bg/nhap_bang_gia.py /tmp/bg --apply    # ghi thật (dùng hàm nhập Excel của app, ghi Lịch sử đổi giá)

Mặc định bỏ qua những dòng giá trên app KHÁC giá mẫu 14/11 (tức anh đã tự sửa) và những mã hàng đã có giá bán;
thêm --ca-gia-da-sua để ghi đè cả những dòng đó. Không đụng logic tiền, chỉ đổi giá bảng giá.
"""
import csv, io, json, re, sys
from pathlib import Path

sys.path.insert(0, '/app')
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import price_book as pb  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import User  # noqa: E402

D = Path(sys.argv[1])
APPLY = '--apply' in sys.argv
FORCE = '--ca-gia-da-sua' in sys.argv
FILES = (('goi', 'nhap-goi-sua.txt'), ('dich-vu', 'nhap-dich-vu.txt'), ('muc', 'nhap-hang-hoa.txt'))
exp = json.loads((D / 'gia-mau-14-11.json').read_text(encoding='utf-8'))


def base_label(c, kind):
    if kind == 'dich-vu':
        return c['key']
    return c['label']


def to_bytes(head, rows):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter='\t', lineterminator='\r\n')
    w.writerow(head)
    w.writerows(rows)
    return buf.getvalue().encode('utf-16')


report = dict(apply=APPLY, files={})
with Session(engine()) as s:
    user = s.scalar(select(User).where(User.active.is_(True), User.roles.any('director'))
                    .order_by(User.last_login_at.desc().nulls_last(), User.id))
    assert user and pb.can_edit(user), 'không có tài khoản giám đốc'
    report['user'] = user.username
    bk = D / 'sao-luu-truoc-khi-nhap'
    bk.mkdir(exist_ok=True)
    for tab in ('may-in', 'muc', 'linh-kien', 'dich-vu', 'goi'):
        (bk / f'{tab}.txt').write_text(pb.export_csv(s, tab, delimiter='\t').lstrip('﻿'), encoding='utf-16')
    for tab, fname in FILES:
        raw = (D / fname).read_bytes()
        plan = pb.plan_import(s, tab, raw)
        kind = plan['kind']
        keep, skipped = [], []
        for c in plan['changes']:
            lab = base_label(c, kind)
            if kind == 'hang':
                edited = c['old'] not in (None, 0)
            else:
                edited = c['old'] is not None and exp.get(kind, {}).get(lab) != c['old']
            (skipped if edited and not FORCE else keep).append(dict(label=c['label'], old=c['old'], new=c['new']))
        report['files'][fname] = dict(errors=plan['errors'], changes=len(plan['changes']), ghi=keep, bo_qua_da_sua=skipped)
        if APPLY and keep:
            text = pb.decode_text(raw)
            rows = list(csv.reader(io.StringIO(text), delimiter='\t'))
            bad = {int(m.group(1)) for e in plan['errors'] if (m := re.match(r'Dòng (\d+):', e))}
            if bad:  # dòng lỗi (vd mã hàng không có trên app) bỏ ra, phần còn lại vẫn nhập
                rows = [r for n, r in enumerate(rows, start=1) if n not in bad]
                raw = to_bytes(rows[0], rows[1:])
            skip_labels = {x['label'] for x in skipped}
            if skip_labels:
                # Lọc dòng file theo nhãn bị bỏ qua (tính lại nhãn từng dòng bằng plan_import một dòng).
                kept = [rows[0]]
                for r in rows[1:]:
                    one = pb.plan_import(s, tab, to_bytes(rows[0], [r]))
                    if not any(c['label'] in skip_labels for c in one['changes']):
                        kept.append(r)
                raw = to_bytes(rows[0], kept[1:])
            n = pb.apply_import(s, user, tab, raw)
            report['files'][fname]['da_ghi'] = n
out = D / ('ket-qua-nhap.json' if APPLY else 'ket-qua-chay-thu.json')
out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
for f, r in report['files'].items():
    print(f, 'lỗi', len(r['errors']), '| thay đổi', r['changes'], '| ghi', len(r['ghi']), '| bỏ qua (đã sửa)', len(r['bo_qua_da_sua']),
          '| đã ghi', r.get('da_ghi', '-'))
    for e in r['errors'][:10]:
        print('   ', e)
print('Kết quả:', out)

"""Rà soát vận hành 17/11/2026: in số liệu dùng thật để lên kế hoạch. CHỈ ĐỌC.

Chạy trên Z2 (anh dán nguyên dòng, giống lệnh cập nhật):
  docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.tunnel.yml --env-file deploy/.env \
      exec -T api python -m scripts.so_lieu_van_hanh

Giao dịch database đặt READ ONLY (Postgres từ chối mọi lệnh ghi) và rollback ở cuối; không gửi Zalo, không ghi tệp.
Chỉ in số đếm và tên nhân viên; không in tên/SĐT/MST khách, không in số tiền từng phiếu.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import perf
from app.db import engine


def _q(session, sql, **kw):
    return session.execute(text(sql), kw).all()


def _one(session, sql, **kw):
    return session.execute(text(sql), kw).scalar() or 0


def _h(title):
    print(f'\n== {title} ==')


def users(session):
    _h('Tài khoản')
    rows = _q(session, "select id, full_name, roles, active, last_login_at from users where 'ai_agent' <> all(roles) order by id")
    for r in rows:
        last = r.last_login_at.strftime('%d/%m %H:%M') if r.last_login_at else '-'
        print(f'#{r.id} {r.full_name} | {",".join(r.roles)} | {"đang dùng" if r.active else "khóa"} | đăng nhập cuối {last}')
    return {r.id: r.full_name for r in rows}


def activity(session, names, since):
    _h(f'Thao tác ghi sổ từ {since:%d/%m} (audit_log), theo người')
    rows = _q(session, 'select user_id, action, count(*) n from audit_log where at >= :s group by 1, 2', s=since)
    per = defaultdict(Counter)
    for r in rows:
        per[r.user_id][r.action] += r.n
    for uid, c in sorted(per.items(), key=lambda kv: -sum(kv[1].values())):
        who = names.get(uid, f'#{uid}' if uid else 'hệ thống/AI')
        top = ', '.join(f'{a} {n}' for a, n in c.most_common(15))
        print(f'- {who}: tổng {sum(c.values())} | {top}')
    _h('Số thao tác theo ngày')
    for r in _q(session, "select (at at time zone 'Asia/Ho_Chi_Minh')::date d, count(*) n from audit_log where at >= :s group by 1 order by 1", s=since):
        print(f'{r.d:%d/%m} {r.n}')


def receipts(session, names, since):
    _h(f'Báo thu từ {since:%d/%m}: ai lập → ai xác nhận, chờ bao lâu')
    rows = _q(session, """select reported_by, decided_by, status, count(*) n,
                                 percentile_cont(0.5) within group (order by extract(epoch from decided_at - reported_at)/60) p50,
                                 percentile_cont(0.9) within group (order by extract(epoch from decided_at - reported_at)/60) p90
                          from sales_receipts where reported_at >= :s group by 1, 2, 3 order by 4 desc""", s=since)
    for r in rows:
        wait = f' | chờ giữa {r.p50:.0f} phút, 90% dưới {r.p90:.0f} phút' if r.p50 is not None else ''
        print(f'- {names.get(r.reported_by, r.reported_by)} → {names.get(r.decided_by, "-")} | {r.status} | {r.n} phiếu{wait}')
    print('Đang chờ xác nhận:', _one(session, "select count(*) from sales_receipts where status='CHO_XAC_NHAN'"))


def approvals_now(session):
    _h('Đang chờ giám đốc (trang Chờ anh duyệt)')
    from app import director_approvals
    rows = director_approvals.rows(session)
    c = Counter(r['label'] for r in rows)
    kt = Counter(r['label'] for r in rows if r['kt'])
    for label, n in c.most_common():
        print(f'- {label}: {n} (kế toán duyệt được {kt.get(label, 0)})')
    if not rows:
        print('- không có')


def data_quality(session):
    _h('Dữ liệu khách')
    base = "from customers c where c.status='DANG_DUNG' and not c.is_test and c.merged_into is null"
    print('Khách đang dùng:', _one(session, f'select count(*) {base}'))
    print('Công ty:', _one(session, f"select count(*) {base} and c.kind='CONG_TY'"),
          '| công ty chưa có MST:', _one(session, f"select count(*) {base} and c.kind='CONG_TY' and coalesce(c.tax_code,'')=''"))
    print('Chưa có SĐT:', _one(session, f'select count(*) {base} and not exists (select 1 from customer_phones p where p.customer_code=c.code)'))
    print('Chưa có địa chỉ:', _one(session, f"select count(*) {base} and coalesce(c.address,'')=''"))
    print('MST trùng giữa 2 khách trở lên:', _one(session, f"""select count(*) from (select c.tax_code {base} and c.tax_code<>''
                                                              group by 1 having count(*)>1) x"""))
    print('Tên trùng hệt (bỏ dấu cách/hoa thường):', _one(session, f"""select count(*) from (select lower(regexp_replace(c.name,'\\s+','','g')) {base}
                                                                 group by 1 having count(*)>1) x"""))
    print('Khách có đơn/phiếu từ 25/10:', _one(session, """select count(distinct k) from (
        select customer_code k from sales_orders where status<>'DA_HUY' union all select customer_code from service_tickets) x"""))
    _h('Dữ liệu hàng hóa')
    pb = "from products p where p.active and not p.is_test"
    print('Mã đang bán:', _one(session, f'select count(*) {pb}'))
    print('Chưa có giá bán:', _one(session, f'select count(*) {pb} and p.sale_price<=0'))
    print('Chưa từng nhập kho (chưa có giá vốn):', _one(session, f'select count(*) {pb} and not exists (select 1 from stock_ledger l where l.product_code=p.code and l.qty_in>0)'))
    print('Nhóm "Máy in" chưa bật theo dõi serial:', _one(session, f"select count(*) {pb} and p.group_name ilike '%máy in%' and not p.track_serial"))
    print('Chưa có nhóm hàng:', _one(session, f"select count(*) {pb} and coalesce(p.group_name,'')=''"))
    print('Đã bán từ 25/10 (có giao):', _one(session, """select count(distinct product_code) from sales_order_lines l
        join sales_orders o on o.id=l.order_id where o.status in ('DA_GIAO','GIAO_MOT_PHAN')"""))


def speed(days):
    _h(f'Màn dùng nhiều nhất {days} ngày (nhật ký /toc-do)')
    s = perf.summary(days)
    opens = [p for p in s['pages'] if p['kind'] in ('Mở màn', 'Lưu/gửi')]
    for p in sorted(opens, key=lambda p: -p['n'])[:30]:
        print(f"{p['method']} {p['route']} | {p['n']} lượt | giữa {p['p50']:.0f} ms | 95% {p['p95']:.0f} ms")
    _h('Màn chậm nhất trên điện thoại/máy tính (đo ở trình duyệt)')
    for x in s['screens'][:12]:
        print(f"{x['route']} | {x['n']} lượt | đạt dưới 2 giây {x['ok']}% | 95% {x['p95']:.0f} ms")
    print(f"Tổng: {s['open_n']} lượt mở màn, 95% dưới {s['open_p95']:.0f} ms; trình duyệt đạt {s['browser_ok']}%")


def main(argv=None):
    parser = argparse.ArgumentParser(description='In số liệu vận hành, chỉ đọc.')
    parser.add_argument('--ngay', type=int, default=14, help='Số ngày gần nhất (mặc định 14, nhật ký tốc độ giữ 14 ngày)')
    args = parser.parse_args(argv)
    since = datetime.now(perf.VN) - timedelta(days=args.ngay)
    print(f'SỐ LIỆU VẬN HÀNH · {datetime.now(perf.VN):%d/%m/%Y %H:%M} · {args.ngay} ngày')
    with Session(engine()) as session:
        session.execute(text('set transaction read only'))
        try:
            names = users(session)
            activity(session, names, since)
            receipts(session, names, since)
            approvals_now(session)
            data_quality(session)
        finally:
            session.rollback()
    speed(args.ngay)


if __name__ == '__main__':
    main()

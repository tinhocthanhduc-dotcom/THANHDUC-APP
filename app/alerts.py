"""R2: cảnh báo theo luật, chỉ SELECT. AlertList là list[dict] kèm metadata .counts."""
from datetime import date, datetime, time, timedelta
from urllib.parse import quote, urlencode

from sqlalchemy import Date, case, cast, exists, func, literal, select, union_all

from . import config, dashboard, day_closings, finance_reports, purchase_reports, sales_reports, service_tickets, stock_reservations
from .models import (CashDoc, Customer, Product, PurchaseOrder, SalesReceipt, ServiceTicket,
                     PendingItem, StockBalance, StockDoc, StockLedger, StockMinLevel, Warehouse, ServiceRecoveredPart, User)
from .security import has_role

LABELS = {'CHO_XU_LY_QUA_HAN':'Hàng chờ xử lý quá hạn','THU_HOI_CHUA_NOP':'Linh kiện hỏng chưa nộp','GIU_VUOT_TON':'Giữ vượt tồn','DV_QUA_HAN':'Dịch vụ quá hạn','DUOI_TON_TOI_THIEU':'Dưới tồn tối thiểu',
          'BAO_THU_CHO':'Báo thu chờ xác nhận','KHACH_VUOT_HAN_MUC':'Khách vượt hạn mức',
          'KHACH_NO_QUA_HAN':'Khách nợ quá hạn','NCC_NO_QUA_HAN':'NCC nợ quá hạn',
          'DON_MUA_TRE':'Đơn mua trễ hẹn','CHUA_CHOT_SO':'Ngày chưa chốt sổ',
          'CHAM_LUAN_CHUYEN':'Hàng chậm luân chuyển'}
LIMIT = 20


class AlertList(list):
    def __init__(self, items, counts, red_count=0):
        super().__init__(items)
        self.counts = counts
        self.red_count = red_count


def _money(value):
    return f'{int(value):,}'.replace(',','.')


def _qty(value):
    text=format(value,'f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text).replace('.',',')


def _stamp(value):
    if value is None:return datetime.max.replace(tzinfo=dashboard.TZ)
    if isinstance(value,datetime):return value.astimezone(dashboard.TZ)
    return datetime.combine(value,time.min,dashboard.TZ)


def _customer_link(code):
    return '/bao-cao/tai-chinh?'+urlencode({'khach':code})+'#chung-tu'


def collect(session,user,today=None,now=None) -> list[dict]:
    with session.no_autoflush:
        dashboard.check_reader(user)
        now=now or dashboard._now()
        now=now.replace(tzinfo=dashboard.TZ) if now.tzinfo is None else now.astimezone(dashboard.TZ)
        today=today or now.date()
        counts={k:0 for k in LABELS if k!='CHAM_LUAN_CHUYEN' or has_role(user,'director')}
        pending=pending_notices(session,user,now=now)
        counts.pop('CHO_XU_LY_QUA_HAN',None)
        counts.update(pending.counts)
        items=[dict(item,_oldest=_stamp(now)) for item in pending]
        red_count=pending.red_count
        def add(kind,stmt,build):
            # COUNT OVER chạy trước LIMIT, và sau GROUP BY của truy vấn nguồn.
            nonlocal red_count
            source=stmt.subquery()
            rows=session.execute(select(source,func.count().over().label('_count'),
                func.count().filter(source.c.level=='DO').over().label('_red_count'))
                .order_by(source.c.level,source.c.oldest,source.c.link_key).limit(LIMIT)).mappings().all()
            counts[kind]=int(rows[0]['_count']) if rows else 0
            red_count+=int(rows[0]['_red_count']) if rows else 0
            for row in rows:
                title,detail,link=build(row)
                items.append(dict(level=row['level'],kind=kind,title=title,detail=detail,link=link,_oldest=_stamp(row['oldest'])))

        t=ServiceTicket
        add('DV_QUA_HAN',select(t.id.label('link_key'),t.number,t.due_at.label('oldest'),literal('DO').label('level'))
            .where(t.status.in_(service_tickets.OPEN),t.due_at<now),
            lambda r:(f"{r['number']} quá hạn",f"Hạn xử lý {_stamp(r['oldest']).strftime('%d/%m/%Y %H:%M')}",f"/dich-vu/{r['link_key']}"))

        recovered = ServiceRecoveredPart
        add('THU_HOI_CHUA_NOP', select(recovered.id.label('link_key'), recovered.ticket_id, t.number,
            recovered.product_code, recovered.qty, recovered.holder_id, User.full_name, recovered.created_at.label('oldest'),
            literal('VANG').label('level')).join(t, t.id == recovered.ticket_id).join(User, User.id == recovered.holder_id)
            .where(recovered.status == 'CHO_NOP', recovered.created_at < now - timedelta(days=3)),
            lambda r: (f"{r['number']} · linh kiện hỏng chưa nộp", f"{r['full_name']}: {r['product_code']} × {r['qty']}, giữ quá 3 ngày.",
                '/linh-kien-thu-hoi?tho=' + str(r['holder_id'])))

        m=StockMinLevel;b=StockBalance
        qty=func.coalesce(b.qty,0)
        add('DUOI_TON_TOI_THIEU',select(m.product_code.label('link_key'),m.warehouse_code,Product.name,m.min_qty,qty.label('qty'),
            m.updated_at.label('oldest'),case((qty<=0,'DO'),else_='VANG').label('level'))
            .join(Product,Product.code==m.product_code).join(Warehouse,Warehouse.code==m.warehouse_code).where(Warehouse.kind=='KHO').outerjoin(b,(b.product_code==m.product_code)&(b.warehouse_code==m.warehouse_code))
            .where(m.min_qty>0,qty<m.min_qty),
            lambda r:(f"{r['link_key']} · {r['warehouse_code']}",f"{r['name']}: tồn {_qty(r['qty'])}, mức tối thiểu {_qty(r['min_qty'])}.",
                '/the-kho/'+quote(r['warehouse_code'],safe='')+'/'+quote(r['link_key'],safe='')))

        held=stock_reservations.held_query()
        add('GIU_VUOT_TON',select(b.product_code.label('link_key'),b.warehouse_code,Product.name,b.qty,held.c.reserved,
            held.c.oldest,literal('DO').label('level')).join(Product,Product.code==b.product_code).join(
            held,(held.c.warehouse_code==b.warehouse_code)&(held.c.product_code==b.product_code)).where(held.c.reserved>b.qty),
            lambda r:(f"Giữ vượt tồn: {r['link_key']} · {r['warehouse_code']}",
                f"{r['name']}: tồn {_qty(r['qty'])}, đang giữ {_qty(r['reserved'])}.",
                '/the-kho/'+quote(r['warehouse_code'],safe='')+'/'+quote(r['link_key'],safe='')))

        r=SalesReceipt
        add('BAO_THU_CHO',select(r.id.label('link_key'),r.number,r.amount,r.reported_at.label('oldest'),literal('VANG').label('level'))
            .where(r.status=='CHO_XAC_NHAN',r.reported_at<now-timedelta(days=config.ALERT_RECEIPT_PENDING_DAYS)),
            lambda r:(f"{r['number']} chờ xác nhận",f"{_money(r['amount'])} đồng, báo từ {_stamp(r['oldest']).strftime('%d/%m/%Y %H:%M')}.",'/bao-thu'))

        debts=finance_reports._debts()
        remaining=func.sum(debts.c.remaining)
        add('KHACH_VUOT_HAN_MUC',select(Customer.code.label('link_key'),Customer.name,Customer.allow_debt,Customer.credit_limit,
            remaining.label('remaining'),func.min(debts.c.day).label('oldest'),literal('DO').label('level'))
            .join(debts,debts.c.customer_code==Customer.code).group_by(Customer.code)
            .having((remaining>Customer.credit_limit)|((Customer.allow_debt.is_(False))&(remaining>0))),
            lambda r:(f"{r['name']} vượt hạn mức",f"Còn nợ {_money(r['remaining'])} đồng; "+
                (f"hạn mức {_money(r['credit_limit'])} đồng." if r['allow_debt'] else 'khách không được nợ.'),_customer_link(r['link_key'])))

        sales=sales_reports._debts(user,today)
        # Đơn bán tính theo hạn riêng; dịch vụ vẫn quá 30 ngày như trước.
        all_debts=finance_reports._debts()
        service_days=func.greatest(0,literal(today,type_=Date)-all_debts.c.day-config.ALERT_DEBT_DAYS)
        debts=union_all(select(sales.c.customer_code,sales.c.remaining,sales.c.days,
                cast(func.timezone('Asia/Ho_Chi_Minh',sales.c.confirmed_at),Date).label('oldest')),
            select(all_debts.c.customer_code,all_debts.c.remaining,service_days,all_debts.c.day)
                .where(all_debts.c.kind=='DV')).subquery()
        add('KHACH_NO_QUA_HAN',select(Customer.code.label('link_key'),Customer.name,func.sum(debts.c.remaining).label('amount'),
            func.max(debts.c.days).label('days'),func.min(debts.c.oldest).label('oldest'),
            case((func.max(debts.c.days)>config.ALERT_DEBT_DAYS,'DO'),else_='VANG').label('level'))
            .join(debts,debts.c.customer_code==Customer.code).where(debts.c.days>0).group_by(Customer.code),
            lambda r:(f"{r['name']} nợ quá hạn",f"{_money(r['amount'])} đồng; khoản cũ nhất quá hạn {r['days']} ngày.",_customer_link(r['link_key'])))

        ncc=purchase_reports._aging(today,overdue_days=config.ALERT_DEBT_DAYS).subquery()
        add('NCC_NO_QUA_HAN',select(ncc.c.supplier_code.label('link_key'),ncc.c.name,ncc.c.overdue,ncc.c.oldest,literal('VANG').label('level'))
            .where(ncc.c.overdue>0),
            lambda r:(f"{r['name']} có nợ NCC quá hạn",f"{_money(r['overdue'])} đồng quá {config.ALERT_DEBT_DAYS} ngày, sau phân bổ FIFO.",
                '/cong-no-ncc/'+quote(r['link_key'],safe='')))

        o=PurchaseOrder
        add('DON_MUA_TRE',select(o.id.label('link_key'),o.number,o.supplier_code,o.expected_date.label('oldest'),literal('VANG').label('level'))
            .where(o.status.in_(('DA_DAT','NHAN_MOT_PHAN')),o.expected_date<today),
            lambda r:(f"{r['number']} trễ hẹn",f"NCC {r['supplier_code']}, ngày hẹn {r['oldest'].strftime('%d/%m/%Y')}.",f"/don-mua/{r['link_key']}"))

        # Tối đa số ngày lookback, không nạp từng phiếu; dùng đúng helper chốt sổ T4.
        days=session.execute(select(CashDoc.doc_date,func.count().label('documents')).where(CashDoc.status=='DA_GHI',
            CashDoc.doc_date>=today-timedelta(days=config.ALERT_CLOSING_LOOKBACK_DAYS),CashDoc.doc_date<today)
            .group_by(CashDoc.doc_date).order_by(CashDoc.doc_date)).all()
        unclosed=[r for r in days if not day_closings.is_closed(session,r.doc_date)]
        counts['CHUA_CHOT_SO']=len(unclosed)
        for row in unclosed[:LIMIT]:
            items.append(dict(level='VANG',kind='CHUA_CHOT_SO',title=f"Ngày {row.doc_date.strftime('%d/%m/%Y')} chưa chốt sổ",
                detail=f'{row.documents} phiếu quỹ đã ghi sổ.',link='/chot-so',_oldest=_stamp(row.doc_date)))

        if has_role(user,'director'):
            cutoff=today-timedelta(days=config.ALERT_SLOW_STOCK_DAYS)
            # Lần nhập đầu gồm cả tồn đầu kỳ (KK) và chuyển kho đến, không chỉ PN.
            first=(select(StockLedger.warehouse_code,StockLedger.product_code,func.min(StockDoc.doc_date).label('oldest'))
                .join(StockDoc,StockDoc.id==StockLedger.doc_id).where(StockDoc.status=='DA_GHI',StockLedger.is_reversal.is_(False),StockLedger.qty_in>0)
                .group_by(StockLedger.warehouse_code,StockLedger.product_code).subquery())
            recent=exists(select(StockLedger.id).join(StockDoc,StockDoc.id==StockLedger.doc_id).where(
                StockLedger.warehouse_code==b.warehouse_code,StockLedger.product_code==b.product_code,
                StockDoc.kind=='PX',StockDoc.status=='DA_GHI',StockLedger.is_reversal.is_(False),StockLedger.qty_out>0,
                StockDoc.doc_date.between(cutoff,today)))
            add('CHAM_LUAN_CHUYEN',select(b.product_code.label('link_key'),b.warehouse_code,b.qty,Product.name,
                func.round(b.qty*b.avg_cost).label('value'),first.c.oldest,literal('VANG').label('level'))
                .join(Product,Product.code==b.product_code).join(first,(first.c.warehouse_code==b.warehouse_code)&(first.c.product_code==b.product_code))
                .where(b.qty>0,first.c.oldest<cutoff,~recent),
                lambda r:(f"{r['link_key']} · {r['warehouse_code']} chậm luân chuyển",f"{r['name']}: tồn {_qty(r['qty'])}, giá trị {_money(r['value'])} đồng; nhập đầu {r['oldest'].strftime('%d/%m/%Y')}, không xuất {config.ALERT_SLOW_STOCK_DAYS} ngày.",
                    '/the-kho/'+quote(r['warehouse_code'],safe='')+'/'+quote(r['link_key'],safe='')))

        items.sort(key=lambda r:(0 if r['level']=='DO' else 1,r['kind'],r['_oldest'],r['link']))
        for item in items:del item['_oldest']
        return AlertList(items,counts,red_count)


def pending_notices(session,user,now=None):
    if not has_role(user,'warehouse'):return AlertList([],{},0)
    from .config import get_settings
    days=get_settings().pending_alert_days;now=now or dashboard._now()
    stmt=select(PendingItem.number,PendingItem.source_ref,PendingItem.entered_at,Product.name,func.count().over().label('total_count')).join(Product,Product.code==PendingItem.product_code).where(
        PendingItem.status=='DANG_CHO',PendingItem.entered_at<now-timedelta(days=days)).order_by(PendingItem.entered_at,PendingItem.id).limit(LIMIT)
    with session.no_autoflush:rows=session.execute(stmt).all()
    count=rows[0].total_count if rows else 0
    items=[dict(level='DO',kind='CHO_XU_LY_QUA_HAN',title=f'{row.number} · {row.name}',
        detail=f'Hàng chờ xử lý quá {days} ngày · {row.source_ref}',link=f'/cho-xu-ly?min_days={days}') for row in rows]
    return AlertList(items,{'CHO_XU_LY_QUA_HAN':count},count)

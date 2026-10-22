"""Read-only workflow presentation. The existing core remains the final validator."""
from datetime import date
from decimal import Decimal, InvalidOperation
from sqlalchemy import select
from .. import quotes, sales_orders, sales_receipts, attachments
from ..models import Equipment, ServiceRecoveredPart
from ..security import has_role


def flow(labels, current, terminal=''):
    return dict(labels=labels if not terminal else [], current=current, terminal=terminal)


def quote(q, linked=None):
    status=q.status if q else 'NHAP'
    return flow(['Nháp','Đã gửi','Khách đồng ý','Đơn bán'],
        3 if linked else {'NHAP':0,'DA_GUI':1,'CHAP_NHAN':2}.get(status,0),
        {'DA_HUY':'Đã hủy','TU_CHOI':'Khách từ chối'}.get(status,''))


def order(o):
    status=o.status if o else 'NHAP'
    current={'NHAP':0,'DA_CHOT':1,'GIAO_MOT_PHAN':2}.get(status,0)
    if status=='DA_GIAO':current=3 if sales_receipts.balance(o)['remaining']>0 else 4
    return flow(['Nháp','Đã chốt','Giao hàng','Thu tiền','Xong'],current,'Đã hủy' if status=='DA_HUY' else '')


def service(t):
    labels=['Nhận đơn','Giao thợ','Thợ nhận']+([] if t.appointment_at else ['Hẹn'])+['Đến khách','Hoàn thành','Đóng']
    current={'MOI':0,'DA_GIAO':1,'DA_NHAN':2,'DA_HEN':len(labels)-4,
             'DANG_LAM':len(labels)-3,'CHO_LINH_KIEN':len(labels)-3,'TAM_DUNG':len(labels)-3,
             'HOAN_THANH':len(labels)-2,'DA_DONG':len(labels)-1}.get(t.status,0)
    # With an appointment already set, DA_HEN is ready for arrival.
    if t.status=='DA_HEN' and t.appointment_at:current=labels.index('Đến khách')
    return flow(labels,current,{'DA_HUY':'Đã hủy','KHONG_LAM_DUOC':'Không làm được','KHACH_HEN_SAU':'Khách hẹn lần sau'}.get(t.status,''))


def check(key,label,ok,blocking=True,**extra):
    return dict(key=key,label=label,ok=bool(ok),blocking=blocking,**extra)


def ready(checks):
    return all(c['ok'] or not c['blocking'] for c in checks)


def quote_checks(data):
    try: valid=date.fromisoformat(data.get('valid_until',''))>=quotes._today()
    except (ValueError,TypeError):valid=False
    return [check('customer','Đã chọn khách hàng',data.get('customer_code')),
            check('lines','Có ít nhất 1 dòng',data.get('lines')),
            check('valid-until','Hiệu lực đến ngày từ hôm nay trở đi',valid)]


def order_checks(session,user,data,stock_values,customer):
    from ..models import Product, Warehouse,StockBalance
    from ..pending_liquidation import supply_query
    pool=supply_query().subquery()
    checks=[check('customer','Đã chọn khách hàng',customer and customer.status=='DANG_DUNG'),
            check('lines','Có ít nhất 1 dòng',data['lines'])]
    demand={};total=0
    for i,line in enumerate(data['lines'],1):
        if line.get('kind')=='HANG':
            checks.append(check('warehouse-'+str(i),f'Dòng {i} đã chọn kho' if line.get('warehouse_code') else f'Dòng {i} chưa chọn kho',line.get('warehouse_code')))
        try:
            qty=Decimal(str(line.get('qty','')).replace(',','.'))
            price=str(line.get('unit_price') or '').replace('.','')
            product=session.get(Product,line.get('product_code')) if line.get('product_code') else None
            price=Decimal(price) if price else Decimal(product.sale_price if product else 0)
            if qty<=0 or price<0 or int(line.get('vat_rate',8)) not in (0,5,8,10):
                checks.append(check('line-'+str(i),f'Dòng {i}: kiểm tra số lượng, đơn giá và VAT',False))
            if line.get('kind')=='HANG':
                checks.append(check('product-'+str(i),f'Dòng {i}: đã chọn hàng đang dùng',product and product.active))
                wh=session.get(Warehouse,line.get('warehouse_code')) if line.get('warehouse_code') else None
                liquidation=bool(wh and wh.kind=='LOI' and session.scalar(select(pool.c.qty).where(pool.c.warehouse_code==wh.code,pool.c.product_code==line.get('product_code'),pool.c.qty>0)))
                if liquidation and data.get('fulfillment')=='THO_GIAO':checks.append(check('THANH_LY_KHONG_GIAO_THO','Hàng thanh lý giao tại kho hoặc kho giao, không giao qua thợ.',False))
                if liquidation:
                    balance=session.get(StockBalance,(wh.code,line['product_code']))
                    if balance and price<balance.avg_cost and not has_role(user,'director'):checks.append(check('CAN_GIAM_DOC_DUYET_GIA','Giá thanh lý dưới giá vốn, cần giám đốc chốt đơn.',False))
                if line.get('warehouse_code') and (not wh or not wh.active or (wh.kind!='KHO' and not liquidation)):checks.append(check('active-warehouse-'+str(i),f'Dòng {i}: kho không còn dùng',False))
            elif not line.get('description','').strip():checks.append(check('description-'+str(i),f'Dòng {i}: cần mô tả',False))
            amount=quotes._round(qty*price);total+=amount+quotes._round(Decimal(amount)*int(line.get('vat_rate',8))/100)
            if line.get('kind')=='HANG' and line.get('warehouse_code') and line.get('product_code'):
                key=(line['warehouse_code'],line['product_code']);demand[key]=demand.get(key,0)+qty
        except (InvalidOperation,ValueError,TypeError):
            checks.append(check('line-'+str(i),f'Dòng {i}: kiểm tra số lượng, đơn giá và VAT',False))
    for (wh,product),qty in demand.items():
        available=stock_values.get((wh,product),{}).get('available',0)
        missing=max(Decimal(0),qty-available)
        checks.append(check('stock-'+wh+'-'+product,
            f'{product} ở kho {wh}: thiếu {missing.normalize():f} · Sẽ giữ thiếu' if missing else f'{product} ở kho {wh}: đủ tồn khả dụng',not missing,bool(session.get(Warehouse,wh) and session.get(Warehouse,wh).kind=='LOI')))
    if data.get('payment_term')=='CONG_NO' and customer:
        state=sales_orders.credit_state(session,customer,total)
        override=has_role(user,'director') and 1<=len(data.get('credit_override_reason','').strip())<=2000
        checks.append(check('credit','Công nợ trong hạn mức' if state['allowed'] else 'Chưa được nợ hoặc vượt hạn mức'+(' · Giám đốc cần ghi lý do' if has_role(user,'director') else ''),state['allowed'],not override))
    return checks


def service_checks(session,t,primary):
    if t.status=='HOAN_THANH':
        machine=session.get(Equipment,t.serial) if t.serial else None
        from ..equipment_custody import pending
        returned=not machine or (machine.custody_location=='TAI_KHACH' and not pending(session,machine.serial))
        recovered=session.scalar(select(ServiceRecoveredPart.id).where(ServiceRecoveredPart.ticket_id==t.id,ServiceRecoveredPart.status=='CHO_NOP').limit(1))
        checks=[check('machine','Máy khách đã trả',returned),
                check('recovered','Linh kiện thu hồi đã nộp',recovered is None)]
        return [c for c in checks if not c['ok']]
    if not primary:return []
    form=primary.get('form','')
    if form=='service-HOAN_THANH':
        return [check('photo','Có ảnh sau sửa',attachments.has_visible(session,'service_ticket',t.id,'SAU')),
                check('billing','Đã lưu tiền / miễn phí',t.billing is not None)]
    return []

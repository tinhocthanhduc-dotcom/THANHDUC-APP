"""KPI và hoa hồng thợ giao đơn bán: chỉ lần giao của thợ, quy tắc loại Giao đơn riêng, hủy giao thì trừ lại."""
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import commissions as svc, kpi
from app.models import SalesDelivery, SalesDeliveryAttempt, SalesOrder
from test_ns3_kpi import p, purchase_people, stamp
from test_ns4_commissions import people, rule, ticket, JAN, FEB


def order(db,p,number,fulfillment='THO_GIAO'):
    o=SalesOrder(number=number,customer_code='KTD-000001',created_by=p['sales'].id,created_at=stamp('2027-01-02T07:00'),status='DA_GIAO',
                 fulfillment=fulfillment,delivery_assignee_id=p['technician'].id,subtotal=100,vat_total=0,total=100,receivable=100)
    db.add(o);db.flush();return o


def delivery(db,p,o,number,who='technician',day='2027-01-05T09:00',status='DA_GIAO'):
    g=SalesDelivery(number=number,order_id=o.id,delivered_by=p[who].id,delivered_at=stamp(day),subtotal=100,vat_total=0,total=100,status=status)
    db.add(g);db.flush();return g


@pytest.fixture
def trips(db,people):
    p=people
    a=order(db,p,'DB1');delivery(db,p,a,'GH1');delivery(db,p,a,'GH2',day='2027-01-31T23:30')
    delivery(db,p,a,'GH3',day='2027-02-01T00:10')                       # tháng sau theo giờ Việt Nam
    delivery(db,p,order(db,p,'DB2'),'GH4',who='warehouse')               # kho giao: không phải thợ
    delivery(db,p,order(db,p,'DB3',fulfillment='TAI_QUAY'),'GH5')         # đơn tại quầy: không tính
    delivery(db,p,order(db,p,'DB4'),'GH6',status='DA_HUY')               # lần giao đã hủy
    for reason in ('KHACH_VANG','HEN_LAI'):
        db.add(SalesDeliveryAttempt(order_id=a.id,assignee_id=p['technician'].id,reason=reason,created_at=stamp('2027-01-10T10:00')))
    db.commit();return p


def test_kpi_counts_only_technician_deliveries_in_month(db,trips):
    p=trips;row=kpi.compute(db,JAN)[p['technician'].id]
    assert row['tho_giao_don']==2 and row['tho_giao_khong_duoc']==2
    assert kpi.compute(db,FEB)[p['technician'].id]['tho_giao_don']==1
    assert {d['number'] for d in kpi.detail(db,p['director'],JAN,p['technician'].id,'tho_giao_don')}=={'GH1','GH2'}
    assert 'tho_giao_don' not in kpi.compute(db,JAN)[p['sales'].id]


def test_commission_needs_own_rule_and_pays_fixed_per_delivery(db,trips):
    p=trips;ticket(db,p)
    rule(db,p,'THO',service_kind=None,pct=10,fixed_amount=30000)   # quy tắc chung của thợ không áp cho giao đơn
    assert not [r for r in svc.draft(db,JAN) if r['source']=='GIAO_DON']
    rule(db,p,'THO',service_kind='GIAO_DON',pct=10,fixed_amount=15000)
    rows=[r for r in svc.draft(db,JAN) if r['source']=='GIAO_DON']
    assert sorted(r['number'] for r in rows)==['GH1','GH2']
    assert all(r['amount']==15000 and r['pct']==0 and r['user_id']==p['technician'].id and r['paid_ratio']==1 for r in rows)
    svc.lock(db,p['director'],JAN)
    # Hủy lần giao sau khi khóa: tháng sau trừ lại đúng số đã trả.
    db.execute(text("update sales_deliveries set status='DA_HUY' where number='GH1'"));db.commit()
    feb={r['number']:r for r in svc.draft(db,FEB) if r['source']=='GIAO_DON'}
    assert feb['GH1']['amount']==-15000 and feb['GH1']['note']=='Lần giao đã hủy' and feb['GH3']['amount']==15000
    assert svc.mine(db,p['technician'],JAN) and any(r['source']=='GIAO_DON' for r in svc.mine(db,p['technician'],JAN))


def test_database_rejects_delivery_line_without_delivery(db,trips):
    p=trips
    with pytest.raises(DBAPIError):
        db.execute(text("insert into commission_lines (month,user_id,source,base,pct,fixed_amount,paid_ratio,entitled,previous,amount,note,created_by)"
                        " values ('2027-01-01',:u,'GIAO_DON',0,0,1,1,1,0,1,'',:u)"),dict(u=p['technician'].id))
    db.rollback()

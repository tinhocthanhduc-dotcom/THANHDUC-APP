import re
from datetime import UTC, datetime

from app import quotes, sales_orders, service_charges
from app.models import Customer, ServiceTicket
from app.schemas import QuoteIn, SalesOrderIn, ServiceCharges
from test_purchase_orders import purchase_people
from conftest import PASSWORD


def test_default_eight_and_explicit_zero_persist_across_quote_sale_service(db,purchase_people,make_user):
    db.add(Customer(code='KTD-000001',name='Khách VAT'));db.commit()
    sales=purchase_people['sales'];dispatcher=make_user('vat.dispatcher','dispatcher')
    for rate in (None,0):
        line=dict(description='Phí',qty=1,unit_price=100000)
        if rate is not None:line['vat_rate']=rate
        quote=quotes.create(db,sales,QuoteIn(customer_code='KTD-000001',lines=[line]))
        order=sales_orders.create(db,sales,SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='KHAC',**line)]))
        ticket=ServiceTicket(number='DV-26-'+('00001' if rate is None else '00002'),customer_code='KTD-000001',
            kind='SUA_TAI_CHO',symptom='Thử VAT',status='DANG_LAM',assignee_id=dispatcher.id,created_by=dispatcher.id)
        db.add(ticket);db.commit()
        service_charges.set_charges(db,dispatcher,ticket.id,ServiceCharges(version=ticket.version,billing='THU_TIEN',lines=[dict(kind='PHI',**line)]))
        expected=8 if rate is None else 0
        assert quote.lines[0].vat_rate==order.lines[0].vat_rate==ticket.lines[0].vat_rate==expected
        assert quote.total==order.total==100000+expected*1000
        assert ticket.lines[0].vat_amount==expected*1000


def test_new_line_templates_select_eight_existing_zero_kept(client,db,purchase_people,make_user):
    db.add(Customer(code='KTD-000001',name='Khách VAT'));db.commit()
    u=purchase_people['director']
    line=dict(description='Dữ liệu cũ',qty=1,unit_price=100000,vat_rate=0)
    quote=quotes.create(db,u,QuoteIn(customer_code='KTD-000001',lines=[line]))
    order=sales_orders.create(db,u,SalesOrderIn(customer_code='KTD-000001',lines=[dict(kind='KHAC',**line)]))
    ticket=ServiceTicket(number='DV-26-00001',customer_code='KTD-000001',kind='SUA_TAI_CHO',symptom='Thử VAT',
        status='DANG_LAM',assignee_id=u.id,created_by=u.id)
    db.add(ticket);db.commit()
    service_charges.set_charges(db,u,ticket.id,ServiceCharges(version=ticket.version,billing='THU_TIEN',lines=[dict(kind='PHI',**line)]))
    client.post('/dang-nhap',data={'username':u.username,'password':PASSWORD,'next':'/khach-hang'})
    for path in ('/bao-gia/moi','/don-ban/moi','/don-mua/moi',f'/dich-vu/{ticket.id}'):
        page=client.get(path);assert page.status_code==200
        templates=re.findall(r'<template\b.*?</template>',page.text,re.S)
        assert templates and any(re.search(r'value="8"[^>]*checked',t) for t in templates),path
        assert all(not re.search(r'value="0"[^>]*checked',t) for t in templates if 'vat_' in t),path
    for path in (f'/bao-gia/{quote.id}',f'/don-ban/{order.id}',f'/dich-vu/{ticket.id}'):
        page=client.get(path)
        checked=[tag for tag in re.findall(r'<input\b[^>]*>',page.text) if 'name="vat_0"' in tag and 'checked' in tag]
        assert checked and 'value="0"' in checked[0],path
    db.expire_all()
    assert quote.lines[0].vat_rate==order.lines[0].vat_rate==ticket.lines[0].vat_rate==0

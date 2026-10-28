"""LG2: real PostgreSQL, bearer/cookie access and source ownership."""
import pytest
from sqlalchemy import select
from uuid import uuid4

from app import attachments, stock_docs
from app.models import AuditLog, FundLedger, StockBalance, StockDoc, StockDocLine, StockLedger, StockReservation
from app.schemas import StockDocIn
from app.security import issue_token
from app.web import nav
from test_my_work import people, purchase_people, ticket
from test_technician_stock import held
from test_web import service_login
from test_sales_returns import sold, receive


def headers(user):
    return {'Authorization': 'Bearer '+issue_token(user)}


@pytest.fixture
def sources(db, held):
    p, _, _ = held
    t = ticket(db,p,'DA_HEN')
    doc = db.scalar(select(StockDoc).where(StockDoc.kind=='PN'))
    return p,t,doc


READS = [
    ('/customers','sales','technician'),
    ('/customers/KTD-000001','sales','technician'),
    ('/products','warehouse','technician'),
    ('/products/TD-0001','warehouse','technician'),
    ('/suppliers','finance','technician'),
    ('/suppliers/NCC-0001','finance','technician'),
    ('/warehouses','warehouse','sales'),
    ('/warehouses/K58','warehouse','sales'),
    ('/stock-docs','warehouse','technician'),
    ('/stock-docs/{doc}','warehouse','technician'),
    ('/service-tickets','dispatcher','warehouse'),
    ('/service-tickets/{ticket}','technician','warehouse'),
    ('/service-tickets/{ticket}/visits','technician','warehouse'),
    ('/service-tickets/{ticket}/receipts','technician','warehouse'),
    ('/service-tickets/{ticket}/part-requests','technician','warehouse'),
]


@pytest.mark.parametrize('path,allowed,denied',READS)
def test_each_tightened_get_allows_reader_and_denies_other_role(client,sources,path,allowed,denied):
    p,t,doc=sources; path=path.format(ticket=t.id,doc=doc.id)
    assert client.get(path,headers=headers(p[allowed])).status_code==200
    response=client.get(path,headers=headers(p[denied]))
    assert response.status_code==403 and response.json()['error']=='FORBIDDEN'
    assert client.get(path).status_code==401


@pytest.mark.parametrize('role',['sales','reception','dispatcher','other_tech'])
def test_stock_cost_is_not_returned_to_other_business_roles(client,sources,role):
    p,_,doc=sources
    for path in ('/stock-docs',f'/stock-docs/{doc.id}'):
        response=client.get(path,headers=headers(p[role]))
        assert response.status_code==403
        assert 'unit_cost' not in response.text and 'amount' not in response.text


@pytest.mark.parametrize('role',['warehouse','finance','director'])
def test_stock_reader_keeps_cost_and_existing_shape(client,sources,role):
    p,_,doc=sources
    response=client.get(f'/stock-docs/{doc.id}',headers=headers(p[role]))
    assert response.status_code==200 and response.json()['lines'][0]['unit_cost']==1000
    assert client.get('/stock-docs',headers=headers(p[role])).json()[0]['id']==doc.id
    service_login(client,p[role])
    assert client.get('/phieu-kho').status_code==200
    page=client.get(f'/phieu-kho/{doc.id}')
    assert page.status_code==200
    if role=='finance':
        assert 'data-add-stock-line' not in page.text and 'stock-cancel' not in page.text
        assert 'data-stock-view' in page.text


def test_stock_api_and_web_use_one_policy_at_request_time(client,sources,monkeypatch):
    p,_,doc=sources
    finance=p['finance'];service_login(client,finance)
    stock_item=nav.matching('/phieu-kho')
    # Change only the shared declaration. Neither route has a copied role list.
    monkeypatch.setitem(stock_item,'roles',('finance',))
    assert client.get(f'/phieu-kho/{doc.id}').status_code==200
    assert client.get(f'/stock-docs/{doc.id}',headers=headers(finance)).status_code==200
    assert client.get(f'/stock-docs/{doc.id}',headers=headers(p['warehouse'])).status_code==403


@pytest.mark.parametrize('entity',['stock_doc','service_ticket'])
@pytest.mark.parametrize('transport',['bearer','cookie'])
def test_attachment_reads_enforce_parent_role(client,db,sources,entity,transport):
    p,t,doc=sources
    actor=p['warehouse'] if entity=='stock_doc' else p['technician']
    parent=doc if entity=='stock_doc' else t
    a=attachments.add(db,actor,entity,str(parent.id),'KHAC','private.pdf',b'%PDF-1.4 LG2')
    listing=f'/attachments?entity={entity}&entity_id={parent.id}'
    assert client.get(listing,headers=headers(actor)).status_code==200
    if transport=='cookie':
        service_login(client,actor);good={}
    else:good=headers(actor)
    response=client.get(f'/attachments/{a.id}/file',headers=good)
    assert response.status_code==200 and response.content==b'%PDF-1.4 LG2'
    if transport=='cookie':
        service_login(client,p['sales']);bad={}
    else:bad=headers(p['sales'])
    for path,h in ((listing,headers(p['sales'])),(f'/attachments/{a.id}/file',bad)):
        response=client.get(path,headers=h)
        assert response.status_code==403 and b'LG2' not in response.content


@pytest.mark.parametrize('transport',['bearer','cookie'])
def test_other_technician_cannot_list_or_download_service_attachment(client,db,sources,transport):
    p,t,_=sources
    a=attachments.add(db,p['technician'],'service_ticket',str(t.id),'KHAC','private.pdf',b'%PDF-1.4 assigned tech')
    if transport=='cookie':
        service_login(client,p['other_tech']);h={}
        # Listing remains a bearer-only API; file downloads also accept web cookies.
        assert client.get(f'/attachments/{a.id}/file').status_code==404
    else:
        h=headers(p['other_tech'])
        assert client.get(f'/attachments?entity=service_ticket&entity_id={t.id}',headers=h).status_code==404
        assert client.get(f'/attachments/{a.id}/file',headers=h).status_code==404
    assert client.get(f'/attachments/{a.id}/file',headers=headers(p['technician'])).status_code==200


def test_read_changes_do_not_grant_stock_write_to_finance(client,db,sources):
    p,_,doc=sources
    body=dict(kind='PN',warehouse_code='K58',supplier_code='NCC-0001',doc_date=doc.doc_date.isoformat(),
        reason='Kiểm tra quyền ghi',lines=[dict(product_code='TD-0001',qty=1,unit_cost=1000)])
    draft=stock_docs.create(db,p['warehouse'],StockDocIn(**body))
    csrf=service_login(client,p['finance'])
    page=client.get(f'/phieu-kho/{draft.id}')
    assert page.status_code==200 and 'data-stock-view' in page.text
    assert 'data-add-stock-line' not in page.text and 'stock-cancel' not in page.text
    def state():
        return [list(db.execute(select(*m.__table__.columns).order_by(*m.__table__.primary_key.columns)))
            for m in (StockDoc,StockDocLine,StockLedger,StockBalance,StockReservation,FundLedger,AuditLog)]
    before=state()
    actions=[
        ('POST','/stock-docs',body),
        ('PATCH',f'/stock-docs/{draft.id}',dict(version=draft.version,note='Không được sửa')),
        ('POST',f'/stock-docs/{draft.id}/post',dict(version=draft.version)),
        ('POST',f'/stock-docs/{draft.id}/cancel',dict(version=draft.version,reason='Không được hủy')),
        ('POST',f'/stock-docs/{doc.id}/void',dict(version=doc.version,reason='Không được đảo kho')),
    ]
    for method,path,data in actions:
        response=client.request(method,path,headers=headers(p['finance']),json=data)
        assert response.status_code==403 and response.json()['error']=='FORBIDDEN'
    web=dict(csrf=csrf,version=draft.version,kind='PN',warehouse_code='K58',supplier_code='NCC-0001',
        doc_date=doc.doc_date.isoformat(),reason='Không được ghi kho',product_code='TD-0001',qty='1',unit_cost='1000')
    for path in ('/phieu-kho/moi',f'/phieu-kho/{draft.id}',f'/phieu-kho/{draft.id}/post',
                 f'/phieu-kho/{draft.id}/cancel',f'/phieu-kho/{doc.id}/void'):
        response=client.post(path,data={**web,'form_key':str(uuid4())})
        assert response.status_code==403 and 'FORBIDDEN' in response.text
    assert state()==before


@pytest.mark.parametrize('transport',['bearer','cookie'])
def test_finance_reads_stock_attachments_without_stock_write(client,db,sources,transport):
    p,_,doc=sources
    a=attachments.add(db,p['warehouse'],'stock_doc',str(doc.id),'KHAC','stock.pdf',b'%PDF-1.4 stock cost')
    h=headers(p['finance'])
    assert client.get(f'/attachments?entity=stock_doc&entity_id={doc.id}',headers=h).status_code==200
    if transport=='cookie':
        service_login(client,p['finance']);h={}
    response=client.get(f'/attachments/{a.id}/file',headers=h)
    assert response.status_code==200 and response.content==b'%PDF-1.4 stock cost'


@pytest.mark.parametrize('role',['sales','technician','warehouse','finance','director'])
def test_sales_return_gets_with_cost_require_cost_role(client,db,sold,role):
    p,o,gh=sold;row=receive(db,p,o,gh)
    for path in (f'/sales-orders/{o.id}/returns',f'/sales-returns/{row.id}'):
        response=client.get(path,headers=headers(p[role]))
        if role in ('warehouse','finance','director'):
            assert response.status_code==200
            body=response.json();body=body[0] if isinstance(body,list) else body
            assert body['lines'][0]['unit_cost']==100 and body['lines'][0]['cost_amount']==200
        else:
            assert response.status_code==403 and 'unit_cost' not in response.text and 'cost_amount' not in response.text
    # The existing source workflow and web reader permissions remain available to sales.
    service_login(client,p['sales'])
    assert client.get(f'/tra-hang/{row.id}').status_code==200

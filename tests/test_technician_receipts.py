"""KT1: thợ báo thu phiếu được giao, tiền mặt chờ vẫn chưa vào quỹ."""
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app import sales_receipts as svc
from app.errors import BusinessError
from app.models import AuditLog, CashDoc, FundLedger, SalesReceipt, ServiceTicket, User
from app.schemas import SalesReceiptReport
from app.security import issue_token
from test_my_work import people, purchase_people, ticket, Page, next_action, DAY
from test_web import service_login


def report(db, p, t, amount=300000, method='TIEN_MAT', who='technician'):
    return svc.report_for_ticket(db,p[who],t.id,SalesReceiptReport(version=t.version,
        amount=amount,method=method,received_at=DAY,note='Khách trả tại chỗ'))


def fails(code, fn, *args, status=409, **kwargs):
    with pytest.raises(BusinessError) as err:fn(*args,**kwargs)
    assert (err.value.code,err.value.status)==(code,status)


def test_assigned_technician_reports_audited_pending_without_cash_posting(db,people):
    p=people;t=ticket(db,p,'HOAN_THANH')
    before=db.query(AuditLog).count()
    r=report(db,p,t)
    assert r.status=='CHO_XAC_NHAN' and r.ticket_id==t.id and r.reported_by==p['technician'].id
    assert r.cash_doc_id is None and r.number.startswith('BT-26-')
    assert db.query(AuditLog).count()==before+1
    audit=db.scalar(select(AuditLog).where(AuditLog.action=='sales_receipt.report'))
    assert audit.entity_id==str(r.id)
    assert db.query(CashDoc).count()==db.query(FundLedger).count()==0


def test_permission_uses_locked_refreshed_ticket_not_stale_assignment(db,engine,people):
    p=people;t=ticket(db,p,'HOAN_THANH')
    body=SalesReceiptReport(version=t.version+1,amount=1,method='TIEN_MAT')
    # Session thứ hai đã tải người được giao cũ; _ticket phải khóa và refresh trước kiểm quyền.
    with Session(engine,expire_on_commit=False) as other:
        stale=other.get(ServiceTicket,t.id)
        worker=other.get(User,p['technician'].id)
        t.assignee_id=p['other_tech'].id;db.commit()
        assert stale.assignee_id==worker.id
        sql=[]
        def capture(conn,cursor,statement,parameters,context,many):sql.append(statement)
        event.listen(engine,'before_cursor_execute',capture)
        try:fails('FORBIDDEN',svc.report_for_ticket,other,worker,t.id,body,status=403)
        finally:event.remove(engine,'before_cursor_execute',capture)
        assert any('FOR UPDATE' in q for q in sql)
    assert db.query(SalesReceipt).count()==0


@pytest.mark.parametrize('state,free',[('MOI',False),('DA_GIAO',False),('DANG_LAM',False),('HOAN_THANH',True)])
def test_unfinished_or_free_rejected(db,people,state,free):
    t=ticket(db,people,state,free=free)
    # MOI chưa giao cho thợ nên dùng điều phối để kiểm riêng luật trạng thái.
    fails('BAD_STATUS',report,db,people,t,who='dispatcher' if state=='MOI' else 'technician')
    assert db.query(SalesReceipt).count()==0


def test_two_reports_include_pending_overpayment_and_do_not_consume_number(db,people):
    p=people;t=ticket(db,p,'HOAN_THANH')
    a=report(db,p,t,300000);b=report(db,p,t,200000)
    fails('OVER_PAYMENT',report,db,p,t,1)
    assert svc.balance(t)==dict(receivable=500000,paid=0,pending=500000,remaining=500000)
    assert [r.number for r in db.scalars(select(SalesReceipt).order_by(SalesReceipt.id))]==['BT-26-00001','BT-26-00002']
    assert (a.status,b.status)==('CHO_XAC_NHAN','CHO_XAC_NHAN')


def test_held_by_own_cash_pending_only_readonly_and_confirm_removes(db,engine,people,configured_finance):
    p=people;t=ticket(db,p,'HOAN_THANH');other=ticket(db,p,'HOAN_THANH',who='other_tech')
    a=report(db,p,t,300000);b=report(db,p,t,100000);report(db,p,t,100000,'CHUYEN_KHOAN')
    report(db,p,other,100000,who='other_tech')
    before=[db.query(m).count() for m in (AuditLog,CashDoc,FundLedger)]
    with Session(engine) as s:
        s.execute(text('SET TRANSACTION READ ONLY'))
        held=svc.held_by(s,p['technician'])
    assert held['total']==400000 and {r['id'] for r in held['rows']}=={a.id,b.id}
    assert held['rows'][0]['ticket_number']==t.number and held['rows'][0]['customer']=='Minh Phát'
    assert held['rows'][0]['received_at']==DAY
    assert [db.query(m).count() for m in (AuditLog,CashDoc,FundLedger)]==before
    svc.confirm(db,p['finance'],a.id,a.version)
    assert svc.held_by(db,p['technician'])['total']==100000
    assert [r['id'] for r in svc.held_by(db,p['technician'])['rows']]==[b.id]
    svc.reject(db,p['finance'],b.id,'Chưa nhận tiền',b.version)
    assert svc.held_by(db,p['technician'])==dict(rows=[],total=0)


def test_web_next_step_form_history_csrf_money_and_other_technician_forbidden(client,db,people):
    p=people;t=ticket(db,p,'HOAN_THANH');csrf=service_login(client,p['technician'])
    page=client.get(f'/dich-vu/{t.id}')
    parsed=Page(page.text)
    assert parsed.primary['form']=='receipt-report' and 'Báo đã thu' in page.text
    assert 'name="amount" value="500.000"' in page.text
    data=dict(csrf=csrf,version=t.version,method='TIEN_MAT',amount='50.000',received_at=DAY.isoformat(),note='Thu tại nhà')
    assert client.post(f'/dich-vu/{t.id}/bao-thu',data={**data,'csrf':''}).status_code==403
    page=next_action(client,page,**{k:v for k,v in data.items() if k!='csrf'})
    assert page.status_code==200
    r=db.query(SalesReceipt).one();assert r.amount==50000
    assert r.number in page.text and 'Chờ xác nhận' in page.text and '450.000' in page.text
    assert 'Xác nhận</button>' not in page.text and 'Hủy báo thu</button>' not in page.text
    csrf=service_login(client,p['other_tech'])
    blocked=client.post(f'/dich-vu/{t.id}/bao-thu',data={**data,'csrf':csrf})
    assert blocked.status_code==403 and 'thợ được giao phiếu' in blocked.text
    assert db.query(SalesReceipt).count()==1


def test_api_foreign_technician_forbidden_and_assignee_can_report(client,db,people):
    p=people;t=ticket(db,p,'HOAN_THANH')
    url=f'/service-tickets/{t.id}/receipts'
    body=dict(version=t.version,method='TIEN_MAT',amount=100000)
    def auth(role):return {'Authorization':'Bearer '+issue_token(p[role])}
    assert client.post(url,json=body,headers=auth('other_tech')).status_code==403
    assert client.post(url,json=body,headers=auth('technician')).status_code==201


@pytest.mark.parametrize('role',['technician','other_tech','finance','dispatcher','sales','warehouse','director','admin'])
def test_held_web_all_roles_own_only_and_technician_menu(client,db,people,make_user,role):
    p=people
    if role=='admin':p[role]=make_user('kt1.admin','admin')
    a=ticket(db,p,'HOAN_THANH');b=ticket(db,p,'HOAN_THANH',who='other_tech')
    r=report(db,p,a,300000);s=report(db,p,b,200000,who='other_tech')
    report(db,p,a,50000,'CHUYEN_KHOAN')
    if role=='admin':
        from conftest import PASSWORD
        client.post('/dang-nhap',data=dict(username=p[role].username,password=PASSWORD),follow_redirects=False)
    else:service_login(client,p[role])
    page=client.get('/tien-toi-giu');assert page.status_code==200
    total=300000 if role=='technician' else 200000 if role=='other_tech' else 0
    assert f'data-held-total>{total:,} đồng'.replace(',','.') in page.text
    assert (f'data-held-receipt="{r.id}"' in page.text)==(role=='technician')
    assert (f'data-held-receipt="{s.id}"' in page.text)==(role=='other_tech')
    if role=='technician':assert 'data-nav-item' in page.text and 'href="/tien-toi-giu"' in page.text


def test_technician_cannot_decide_receipts_core_or_web(client,db,people,configured_finance):
    p=people;t=ticket(db,p,'HOAN_THANH');r=report(db,p,t)
    fails('FORBIDDEN',svc.confirm,db,p['technician'],r.id,r.version,status=403)
    fails('FORBIDDEN',svc.reject,db,p['technician'],r.id,'Không được',r.version,status=403)
    svc.confirm(db,p['finance'],r.id,r.version)
    fails('FORBIDDEN',svc.void,db,p['technician'],r.id,'Không được',r.version,status=403)
    csrf=service_login(client,p['technician'])
    for action in ('confirm','reject','void'):
        assert client.post(f'/bao-thu/{r.id}/{action}',data=dict(csrf=csrf,version=r.version,reason='Không được')).status_code==403

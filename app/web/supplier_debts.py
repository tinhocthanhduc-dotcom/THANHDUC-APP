"""M3: công nợ và trả NCC, CSRF cho mọi POST. Danh sách khuôn 3 + trang một NCC khung 2 cột (27/10/2026): chỉ đọc lại số của supplier_debts, không thêm luật tiền."""
import re
from datetime import timedelta
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import cash_docs, inventory_catalogs, supplier_debts as svc
from .. import search as tim
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import CashDoc, Fund, PurchaseOrder, PurchaseReceipt, Supplier, SupplierOpeningDebt, SupplierPayment, User
from ..schemas import SupplierOpeningDebtIn, SupplierPaymentIn
from ..security import has_role
from ..text import fold
from . import checked_form, money, paging, render, web_user
from .cash import _error
from .lists import _today

router=APIRouter()
TABS={'tat-ca':'Tất cả','no':'Còn phải trả','qua-han':'Quá {days} ngày','tra-truoc':'Đã trả trước'}
SORTS={'no':'Nợ nhiều nhất','lau':'Nợ lâu nhất','ten':'Tên NCC'}
DOC_LIMIT=3
LABELS={'pending_return':'Trả hàng lỗi giảm nợ','void_pending_return':'Hủy trả hàng lỗi giảm nợ','return':'Trả hàng NCC','void_return':'Hủy trả hàng NCC','opening':'Nợ đầu kỳ','receive':'Nhận hàng','void_receive':'Hủy nhận hàng','payment':'Trả NCC','void_payment':'Hủy trả NCC'}


def debt_user(user:User=Depends(web_user)):
    svc.check_reader(user)
    return user


def _amount(value,zero=False):
    text=str(value).strip()
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',text):
        raise BusinessError('BAD_AMOUNT','Số tiền phải là số nguyên đồng, dùng dấu chấm để ngăn hàng nghìn.')
    result=int(text.replace('.',''))
    if result<0 or (result==0 and not zero):raise BusinessError('BAD_AMOUNT','Số tiền trả phải lớn hơn 0.')
    return result


def _version(form,zero=False):
    text=str(form.get('version',''))
    if not text.isdigit() or (not zero and int(text)==0):raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản, hãy tải lại.')
    return int(text)


def _order_id(value):
    if not str(value or '').strip():return None
    if not str(value).isdigit() or int(value)<1:raise BusinessError('BAD_ORDER','Mã đơn mua không hợp lệ.')
    return int(value)


def _unpaid(session,codes,today):
    """Khoản còn nợ theo từng NCC, trừ cũ trước (cùng luật NCC2 ở Việc của tôi): nợ đầu kỳ trước, rồi phiếu nhận đã ghi theo (ngày, id);
    tiền đã trừ = đúng các khoản supplier_debts._deductions(). Chỉ đọc, gom theo đơn mua để hiện."""
    if not codes:return {}
    d=svc._deductions().subquery()
    credit={r.supplier_code:int(r.amount or 0) for r in session.execute(select(d.c.supplier_code,d.c.amount).where(d.c.supplier_code.in_(codes)))}
    charges=[(r.supplier_code,0,r.as_of,0,None,'',int(r.amount)) for r in session.execute(
        select(SupplierOpeningDebt.supplier_code,SupplierOpeningDebt.as_of,SupplierOpeningDebt.amount).where(SupplierOpeningDebt.supplier_code.in_(codes),SupplierOpeningDebt.amount>0))]
    charges+=[(r.supplier_code,1,r.receipt_date,r.id,r.order_id,r.number,int(r.total)) for r in session.execute(
        select(PurchaseOrder.supplier_code,PurchaseReceipt.receipt_date,PurchaseReceipt.id,PurchaseReceipt.order_id,PurchaseOrder.number,PurchaseReceipt.total)
        .join(PurchaseReceipt,PurchaseReceipt.order_id==PurchaseOrder.id).where(PurchaseReceipt.status=='DA_GHI',PurchaseOrder.supplier_code.in_(codes)))]
    out={}
    for code,_,day,_,order_id,number,amount in sorted(charges,key=lambda c:(c[0],c[1],c[2],c[3])):
        used=min(credit.get(code,0),amount);credit[code]=credit.get(code,0)-used;left=amount-used
        if left<=0:continue
        docs=out.setdefault(code,[])
        same=next((x for x in docs if order_id and x['order_id']==order_id),None)
        if same:same['left']+=left;same['received']+=amount
        else:docs.append(dict(order_id=order_id,number=number or 'Nợ đầu kỳ',day=day,days=(today-day).days,left=left,received=amount,part=used>0))
    return out


def _last_payments(session,codes):
    p=SupplierPayment
    last=select(p.supplier_code,p.paid_at,p.amount,func.row_number().over(partition_by=p.supplier_code,order_by=(p.paid_at.desc(),p.id.desc())).label('n')).where(
        p.status=='DA_GHI',p.supplier_code.in_(codes)).subquery()
    return {r.supplier_code:(r.paid_at,int(r.amount)) for r in session.execute(select(last).where(last.c.n==1))}


def _rows(session,user,today):
    alert=get_settings().supplier_debt_alert_days
    balances=svc.search(session,user,'')
    codes=[b['supplier_code'] for b in balances]
    suppliers={s.code:s for s in session.scalars(select(Supplier).where(Supplier.code.in_(codes)))} if codes else {}
    unpaid=_unpaid(session,codes,today);last=_last_payments(session,codes) if codes else {}
    rows=[]
    for b in balances:
        s=suppliers[b['supplier_code']];docs=unpaid.get(s.code,[]) if b['remaining']>0 else []
        since=docs[0]['day'] if docs else None
        days=(today-since).days if since else 0
        rows.append(dict(b,code=s.code,phones=list(s.phones or []),docs=docs,since=since,days=days,old=bool(docs) and days>alert,
                         advance=b['remaining']<0,last=last.get(s.code),
                         search=fold(' '.join([s.search_text or '',s.code,s.name,*(s.phones or []),*(x['number'] for x in docs)]))))
    return rows


def _match(row,q):
    if not fold(q):return True
    digits=''.join(ch for ch in q if ch.isdigit())
    return tim.text_matches(q,row['search']) or (len(digits)>=3 and any(digits in p for p in row['phones']))


def _listing(session,user,q='',tab='',xep='',trang='',so=''):
    today=_today();alert=get_settings().supplier_debt_alert_days
    q=q[:200];tab=tab if tab in TABS else 'tat-ca';xep=xep if xep in SORTS else 'no'
    all_rows=_rows(session,user,today)
    pick={'tat-ca':lambda r:True,'no':lambda r:r['remaining']>0,'qua-han':lambda r:r['old'],'tra-truoc':lambda r:r['advance']}
    found=[r for r in all_rows if _match(r,q)]
    rows=[r for r in found if pick[tab](r)]
    far=today+timedelta(days=36500)
    rows.sort(key={'no':lambda r:(r['remaining']<0,-abs(r['remaining']),r['code']),'lau':lambda r:(r['since'] or far,r['code']),'ten':lambda r:(fold(r['name']),r['code'])}[xep])
    query=dict(q=q,tab='' if tab=='tat-ca' else tab,xep='' if xep=='no' else xep)
    page,shown=paging.slice_rows(rows,trang,so,query)
    link=paging.linker('/cong-no-ncc',query)
    # Ô số đếm trên toàn bộ NCC có số dư (không theo ô tìm); bấm ô là chuyển tab.
    groups={k:[r for r in all_rows if pick[k](r)] for k in TABS if k!='tat-ca'}
    total=lambda k:sum(abs(r['remaining']) for r in groups[k])
    month=today.replace(day=1);p=SupplierPayment
    paid_n,paid_sum=session.execute(select(func.count(),func.coalesce(func.sum(p.amount),0)).where(p.status=='DA_GHI',p.paid_at>=month,p.paid_at<=today)).one()
    oldest=max((r['days'] for r in groups['qua-han']),default=0)
    tiles=[dict(label='Còn phải trả',n=f"{len(groups['no'])} NCC",sub=(money(total('no'))+' đ') if groups['no'] else 'Không còn nợ NCC',href='/cong-no-ncc?tab=no',on=tab=='no'),
           dict(label=f'Nợ quá {alert} ngày',n=f"{len(groups['qua-han'])} NCC",sub=(money(total('qua-han'))+f' đ · cũ nhất {oldest} ngày') if groups['qua-han'] else 'Không có',
                href='/cong-no-ncc?tab=qua-han',on=tab=='qua-han',tone='huy' if groups['qua-han'] else ''),
           dict(label='Đã trả trước',n=f"{len(groups['tra-truoc'])} NCC",sub=(money(total('tra-truoc'))+' đ · trừ vào lần nhận sau') if groups['tra-truoc'] else 'Không có',
                href='/cong-no-ncc?tab=tra-truoc',on=tab=='tra-truoc',tone='xong' if groups['tra-truoc'] else ''),
           dict(label=f'Đã trả NCC tháng {today.month}',n=money(int(paid_sum)),sub=f'{paid_n} lần trả · phiếu chi' if paid_n else 'Chưa trả lần nào')]
    tabs=[dict(label=label.format(days=alert),n=sum(1 for r in found if pick[k](r)),href=link(tab='' if k=='tat-ca' else k),on=tab==k) for k,label in TABS.items()]
    return dict(rows=shown,page=page,tiles=tiles,tabs=tabs,link=link,q=q,tab=tab,xep=xep,sorts=SORTS,doc_limit=DOC_LIMIT,
                debt_sum=sum(r['remaining'] for r in rows if r['remaining']>0),advance_sum=sum(-r['remaining'] for r in rows if r['remaining']<0))


def _detail(request,session,code,error=None,error_code='',status=200,pay_form=None,opening_form=None,void_id=None,reason=''):
    supplier=session.get(Supplier,code)
    if supplier is None:raise BusinessError('NOT_FOUND','Không tìm thấy nhà cung cấp.',status=404)
    user=request.state.user;balance=svc.balance(session,user,code)
    order_id=_order_id((pay_form or {}).get('order_id',request.query_params.get('don','')))
    order=svc.order_balance(session,user,order_id) if order_id else None
    if order and order['supplier_code']!=code:raise BusinessError('BAD_ORDER','Đơn mua không thuộc nhà cung cấp này.')
    maximum=min(balance['remaining'],order['remaining']) if order else balance['remaining']
    data=pay_form if pay_form is not None else dict(fund_code='',amount=money(max(0,maximum)),paid_at=cash_docs.today().isoformat(),note='',order_id=order_id or '')
    initial=svc.opening(session,user,code)
    shown=opening_form if opening_form is not None else dict(amount=money(initial.amount if initial else 0),as_of=initial.as_of.isoformat() if initial else cash_docs.today().isoformat(),note=initial.note if initial else '',version=initial.version if initial else 0)
    history=svc.payments(session,user,code);today=_today()
    docs=_unpaid(session,[code],today).get(code,[]) if balance['remaining']>0 else []
    since=docs[0]['day'] if docs else None
    days=(today-since).days if since else 0
    return render(request,'cong_no_ncc_chi_tiet.jinja',supplier=supplier,balance=balance,order=order,pay=data,opening=shown,
        timeline=list(reversed(svc.timeline(session,user,code))),labels=LABELS,history=history,docs=docs,since=since,days=days,
        old=bool(docs) and days>get_settings().supplier_debt_alert_days,
        quick=[money(v) for v in dict.fromkeys([balance['remaining'],*(x['left'] for x in docs[:1])]) if v>0 and not order],
        funds_names={f.code:f.name for f in session.scalars(select(Fund))},
        cash_numbers=dict(session.execute(select(CashDoc.id,CashDoc.number).where(CashDoc.id.in_([p.cash_doc_id for p in history]))).all()),
        funds=list(session.scalars(select(Fund).where(Fund.active.is_(True)).order_by(Fund.code))),director=has_role(user,'director'),opening_open=opening_form is not None,
        error=error,error_code=error_code,status=status,void_id=void_id,reason=reason,saved=request.query_params.get('da_luu')=='1' and not error)


@router.get('/cong-no-ncc')
def listing(request:Request,q:str='',tab:str='',xep:str='',trang:str='',so:str='',user:User=Depends(debt_user),session:Session=Depends(get_session)):
    return render(request,'cong_no_ncc_ds.jinja',director=has_role(user,'director'),**_listing(session,user,q,tab,xep,trang,so))


@router.get('/cong-no-ncc/ket-qua')
def results(request:Request,q:str='',tab:str='',xep:str='',user:User=Depends(debt_user),session:Session=Depends(get_session)):
    return render(request,'cong_no_ncc_dong.jinja',**_listing(session,user,q,tab,xep))


@router.get('/cong-no-ncc/tim')
def picker(request:Request,q:str='',user:User=Depends(debt_user),session:Session=Depends(get_session)):
    return render(request,'cong_no_ncc_chon.jinja',rows=inventory_catalogs.search(session,Supplier,q[:200]))


@router.get('/cong-no-ncc/{code}')
def detail(request:Request,code:str,user:User=Depends(debt_user),session:Session=Depends(get_session)):
    return _detail(request,session,code)


@router.post('/cong-no-ncc/{code}/tra')
def pay(request:Request,code:str,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user)
    raw={k:str(form.get(k,'')) for k in ('fund_code','amount','paid_at','order_id','note')}
    try:
        body=SupplierPaymentIn(**{**raw,'amount':_amount(raw['amount']),'order_id':_order_id(raw['order_id'])})
        svc.pay(session,user,code,**body.model_dump())
    except (BusinessError,ValidationError) as exc:
        return _detail(request,session,code,error=_error(exc),error_code=getattr(exc,'code','BAD_FORM'),status=getattr(exc,'status',400),pay_form=raw)
    query=urlencode({'da_luu':'1',**({'don':body.order_id} if body.order_id else {})})
    return RedirectResponse('/cong-no-ncc/'+quote(code,safe='')+'?'+query+'#tra-tien',status_code=303)


@router.post('/cong-no-ncc/{code}/dau-ky')
def opening(request:Request,code:str,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;svc.check_reader(user)
    if not has_role(user,'director'):raise BusinessError('FORBIDDEN','Chỉ giám đốc được đặt nợ đầu kỳ NCC.',status=403)
    raw={k:str(form.get(k,'')) for k in ('amount','as_of','note','version')}
    try:
        body=SupplierOpeningDebtIn(**{**raw,'amount':_amount(raw['amount'],True),'version':_version(form,True)})
        svc.set_opening(session,user,code,**body.model_dump())
    except (BusinessError,ValidationError) as exc:
        return _detail(request,session,code,error=_error(exc),error_code=getattr(exc,'code','BAD_FORM'),status=getattr(exc,'status',400),opening_form=raw)
    return RedirectResponse('/cong-no-ncc/'+quote(code,safe='')+'?da_luu=1#no-dau-ky',status_code=303)


@router.post('/cong-no-ncc/tra/{payment_id}/huy')
def void(request:Request,payment_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user)
    row=session.get(SupplierPayment,payment_id)
    if row is None:raise BusinessError('NOT_FOUND','Không tìm thấy lần trả NCC.',status=404)
    code=row.supplier_code;reason=str(form.get('reason',''))
    try:svc.void(session,user,payment_id,reason,_version(form))
    except BusinessError as exc:
        return _detail(request,session,code,error=_error(exc),error_code=exc.code,status=exc.status,void_id=payment_id,reason=reason)
    return RedirectResponse('/cong-no-ncc/'+quote(code,safe='')+'?da_luu=1#tra-tien',status_code=303)

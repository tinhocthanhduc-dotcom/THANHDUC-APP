"""T2: web chỉ gọi nghiệp vụ phiếu/sổ quỹ, không ghi balances/ledger trực tiếp."""
import re
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import cash_docs as docs, cash_posting as posting, day_closings
from ..config import get_settings
from ..customers import like_pattern
from ..db import get_session
from ..errors import BusinessError
from ..models import CashCategory, CashCorrection, CashDoc, Customer, Fund, Supplier, User
from ..money_words import money_words
from ..schemas import CashDocIn, CashDocPatch, CashDocCancel
from ..security import has_role
from ..text import fold
from . import checked_form, money, render
from .finance import finance_user, _version

router = APIRouter()


def _period(request):
    try:
        start = date.fromisoformat(request.query_params['from']) if request.query_params.get('from') else docs.today().replace(day=1)
        end = date.fromisoformat(request.query_params['to']) if request.query_params.get('to') else docs.today()
        if start>end:
            raise ValueError
        return start,end
    except ValueError:
        raise BusinessError('BAD_DATE_RANGE','Khoảng ngày chưa hợp lệ, ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')


def _funds(session,selected=()):
    return list(session.scalars(select(Fund).where(or_(Fund.active.is_(True),Fund.code.in_([c for c in selected if c])))
                               .order_by(Fund.code)))


def _error(exc):
    if isinstance(exc,ValidationError):
        return 'Ngày phiếu, số tiền và các trường nhập phải hợp lệ; số tiền là số nguyên đồng lớn hơn 0.'
    if exc.code=='NEGATIVE_CASH':
        d=exc.details
        return f"Quỹ {d['fund']} không đủ số dư: hiện có {money(d['available'])} đồng, số cần {money(d['requested'])} đồng."
    return exc.message


def _blank(kind):
    return dict(id=None,number='',kind=kind,doc_date=docs.today().isoformat(),fund_code='',to_fund_code=None,
                category_code=None,amount='',customer_code=None,supplier_code=None,partner_name='',description='',
                is_opening=False,status='NHAP',version=0,void_reason='')


def _data(form):
    fields=('kind','doc_date','fund_code','to_fund_code','category_code','customer_code','supplier_code','partner_name','description','amount')
    return {**{key:str(form.get(key,'')) for key in fields},'is_opening':form.get('is_opening')=='1'}


def _body(data):
    value=str(data['amount']).strip()
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)',value):
        raise BusinessError('BAD_AMOUNT','Số tiền phải là số nguyên đồng lớn hơn 0, ví dụ 120.000.')
    digits=value.replace('.','').lstrip('0') or '0'
    if len(digits)>19:
        raise BusinessError('BAD_AMOUNT','Số tiền vượt giới hạn số nguyên đồng.')
    return CashDocIn(**{**data,'amount':int(digits)})


def _form(request,session,data,doc=None,error=None,code='',status=200,reason='',correction_form=None):
    editable = doc is None or doc.status=='NHAP'
    closed=bool(doc and day_closings.is_closed(session,doc.doc_date))
    replacement=session.execute(select(CashDoc.id,CashDoc.number).join(CashCorrection,CashCorrection.new_doc_id==CashDoc.id).where(
        CashCorrection.doc_id==doc.id,CashCorrection.status=='DA_DUYET')).first() if doc else None
    proposal=correction_form if correction_form is not None else dict(version=doc.version if doc else 0,reason='',
        new_amount=money(doc.amount) if doc else '',new_fund_code=data.get('fund_code',''),
        new_category_code=data.get('category_code',''),new_description=data.get('description',''))
    return render(request,'thu_chi_form.jinja',d=data,doc=doc,kinds=docs.KINDS,states=docs.STATES,
                  funds=_funds(session,(data.get('fund_code'),data.get('to_fund_code'))),
                  director=has_role(request.state.user,'director'),editable=editable,
                  can_write=not data.get('is_opening') or has_role(request.state.user,'director'),
                  error=error,error_code=code,status=status,reason=reason,closed=closed,correction=proposal,replacement=replacement,
                  can_correct=bool(doc and doc.status=='DA_GHI' and doc.kind in ('PT','PC') and not doc.is_opening and not doc.source_type),
                  pending_correction=session.scalar(select(CashCorrection.id).where(CashCorrection.doc_id==doc.id,CashCorrection.status=='CHO_DUYET')) if doc else None,
                  saved=request.query_params.get('da_luu')=='1' and not error)


@router.get('/thu-chi')
def listing(request: Request,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    filters={key:request.query_params.get(key,'') for key in ('loai','quy','trang_thai','from','to')}
    rows=[];error=None
    try:
        start,end=_period(request)
        if not filters['from']: filters['from']=start.isoformat()
        if not filters['to']: filters['to']=end.isoformat()
        rows=docs.search(session,user,filters['loai'],filters['quy'],filters['trang_thai'],start,end)
    except BusinessError as exc:
        error=exc
    return render(request,'thu_chi_ds.jinja',rows=rows,filters=filters,kinds=docs.KINDS,states=docs.STATES,
                  funds=list(session.scalars(select(Fund).order_by(Fund.code))),error=error,status=error.status if error else 200)


@router.get('/thu-chi/tim/{target}')
def options(request: Request,target: str,q: str='',kind: str='PT',user: User=Depends(finance_user),session: Session=Depends(get_session)):
    if target.startswith('danh-muc-'):
        kind,target=target[len('danh-muc-'):],'danh-muc'
    models={'danh-muc':CashCategory,'khach':Customer,'nha-cung-cap':Supplier}
    model=models.get(target)
    if model is None:
        raise BusinessError('NOT_FOUND','Không tìm thấy danh mục.',status=404)
    stmt=select(model)
    if target=='danh-muc':
        if kind not in ('PT','PC'):
            return render(request,'thu_chi_chon.jinja',rows=[])
        stmt=stmt.where(CashCategory.active.is_(True),CashCategory.direction==('THU' if kind=='PT' else 'CHI'))
    elif target=='khach':
        stmt=stmt.where(Customer.status=='DANG_DUNG')
    else:
        stmt=stmt.where(Supplier.active.is_(True))
    if fold(q):
        stmt=stmt.where(model.search_text.like(like_pattern(fold(q[:200]))))
    return render(request,'thu_chi_chon.jinja',rows=list(session.scalars(stmt.order_by(model.code).limit(get_settings().search_limit))))


@router.get('/thu-chi/moi')
def new(request: Request,loai: str='PT',user: User=Depends(finance_user),session: Session=Depends(get_session)):
    if loai not in docs.KINDS:
        raise BusinessError('BAD_KIND','Loại phiếu chưa hợp lệ.')
    return _form(request,session,_blank(loai))


def _submit(request,form,session,doc_id=None,post=False):
    user=request.state.user
    docs.check_writer(user)
    doc=docs.load(session,user,doc_id) if doc_id else None
    data=_data(form)
    shown={**(docs.snapshot(doc) if doc else _blank(data['kind'])),**data,'version':str(form.get('version',''))}
    own_saved=False
    try:
        body=_body(data)
        if doc:
            doc=docs.update(session,user,doc.id,CashDocPatch(version=_version(form),**body.model_dump(exclude={'source_type','source_id'})))
        else:
            doc=docs.create(session,user,body)
        own_saved=True
        if post or form.get('action')=='post':
            doc=posting.post(session,user,doc.id,doc.version)
    except (BusinessError,ValidationError) as exc:
        if own_saved:
            doc=docs.load(session,user,doc.id)
            shown.update(id=doc.id,number=doc.number,version=doc.version,status=doc.status)
        return _form(request,session,shown,doc,error=_error(exc),code=getattr(exc,'code','BAD_FORM'),status=getattr(exc,'status',400))
    return RedirectResponse(f'/thu-chi/{doc.id}?da_luu=1',status_code=303)


@router.post('/thu-chi/moi')
def create(request: Request,form=Depends(checked_form),session: Session=Depends(get_session)):
    return _submit(request,form,session)


@router.get('/thu-chi/{doc_id}')
def detail(request: Request,doc_id: int,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    doc=docs.load(session,user,doc_id);data=docs.snapshot(doc);data['amount']=money(doc.amount)
    return _form(request,session,data,doc)


@router.post('/thu-chi/{doc_id}')
def update(request: Request,doc_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    return _submit(request,form,session,doc_id)


@router.post('/thu-chi/{doc_id}/post')
def post(request: Request,doc_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    if 'amount' in form:
        return _submit(request,form,session,doc_id,post=True)
    user=request.state.user;docs.check_writer(user)
    doc=docs.load(session,user,doc_id)
    try:
        posting.post(session,user,doc_id,_version(form))
    except BusinessError as exc:
        data=docs.snapshot(doc);data['amount']=money(doc.amount)
        return _form(request,session,data,doc,error=_error(exc),code=exc.code,status=exc.status)
    return RedirectResponse(f'/thu-chi/{doc_id}?da_luu=1',status_code=303)


@router.post('/thu-chi/{doc_id}/cancel')
def cancel(request: Request,doc_id: int,form=Depends(checked_form),session: Session=Depends(get_session)):
    user=request.state.user;docs.check_writer(user)
    doc=docs.load(session,user,doc_id);reason=str(form.get('reason',''))
    try:
        docs.check_source(doc)
        if not reason.strip():
            raise BusinessError('REASON_REQUIRED','Hủy phiếu phải nhập lý do.')
        if doc.status=='NHAP':
            docs.cancel(session,user,doc_id,CashDocCancel(version=_version(form),reason=reason))
        else:
            posting.void(session,user,doc_id,reason,_version(form))
    except (BusinessError,ValidationError) as exc:
        data=docs.snapshot(doc);data['amount']=money(doc.amount)
        return _form(request,session,data,doc,error=_error(exc),code=getattr(exc,'code','BAD_FORM'),
                     status=getattr(exc,'status',400),reason=reason)
    return RedirectResponse(f'/thu-chi/{doc_id}?da_luu=1',status_code=303)


@router.get('/thu-chi/{doc_id}/in')
def print_doc(request: Request,doc_id: int,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    doc=docs.load(session,user,doc_id)
    partner=session.get(Customer,doc.customer_code) if doc.customer_code else session.get(Supplier,doc.supplier_code) if doc.supplier_code else None
    return render(request,'thu_chi_in.jinja',d=doc,kinds=docs.KINDS,states=docs.STATES,company=get_settings(),
                  fund=session.get(Fund,doc.fund_code),partner=partner,words=money_words(doc.amount))


@router.get('/so-quy/{fund}')
def statement(request: Request,fund: str,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    start,end=_period(request)
    report=posting.statement(session,user,fund,start,end)
    return render(request,'so_quy.jinja',r=report,start=start.isoformat(),end=end.isoformat(),kinds=docs.KINDS)

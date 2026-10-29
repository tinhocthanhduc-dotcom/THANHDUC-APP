"""T2: web chỉ gọi nghiệp vụ phiếu/sổ quỹ, không ghi balances/ledger trực tiếp."""
import re
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .. import cash_docs as docs, cash_posting as posting, day_closings
from ..config import get_settings
from ..db import get_session
from ..errors import BusinessError
from ..models import CashCategory, CashCorrection, CashDoc, Customer, Fund, Supplier, User
from ..money_words import money_words
from ..schemas import CashDocIn, CashDocPatch, CashDocCancel
from ..security import has_role
from ..text import fold
from . import checked_form, money, render
from .finance import finance_user, _version
from .. import search as tim

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


def _where(exc,data):
    """Đợt 3: lỗi thuộc ô nào để tô đỏ đúng ô (chỉ đọc mã lỗi, không đổi nghiệp vụ)."""
    if isinstance(exc,ValidationError):
        return {str(e['loc'][0]):_error(exc) for e in exc.errors() if e['loc']}
    field={'BAD_AMOUNT':'amount','AMOUNT_TOO_LARGE':'amount','BAD_CATEGORY':'category_code','BAD_FUND':'fund_code',
           'NEGATIVE_CASH':'fund_code','BAD_TRANSFER':'to_fund_code','BAD_OPENING':'is_opening'}.get(exc.code)
    if exc.code=='BAD_FUND' and exc.details.get('fund') and exc.details['fund']==data.get('to_fund_code'):
        field='to_fund_code'
    if exc.code=='BAD_PARTNER':
        field='customer_code' if data.get('customer_code') else 'supplier_code'
    return {field:_error(exc)} if field else {}


def _form(request,session,data,doc=None,error=None,code='',status=200,reason='',correction_form=None,fields=None):
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
                  saved=request.query_params.get('da_luu')=='1' and not error,field_errors=fields or {},
                  category=session.get(CashCategory,data['category_code']) if data.get('category_code') else None,
                  customer=session.scalar(select(Customer).where(Customer.code==data['customer_code'])) if data.get('customer_code') else None,
                  supplier=session.get(Supplier,data['supplier_code']) if data.get('supplier_code') else None)


@router.get('/thu-chi')
def listing(request: Request,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    from . import paging
    filters={key:request.query_params.get(key,'') for key in ('loai','quy','trang_thai','from','to','q','trang','so')}
    filters['q']=filters['q'][:200]
    found=[];error=None
    try:
        start,end=_period(request)
        if not filters['from']: filters['from']=start.isoformat()
        if not filters['to']: filters['to']=end.isoformat()
        # Lấy đủ theo quỹ, loại, kỳ; tab trạng thái đếm trên tập này rồi mới lọc và cắt trang.
        found=docs.search(session,user,filters['loai'],filters['quy'],None,start,end)
        if filters['trang_thai'] and filters['trang_thai'] not in docs.STATES:
            raise BusinessError('BAD_STATUS','Trạng thái phiếu chưa hợp lệ.')
    except BusinessError as exc:
        error=exc
    if fold(filters['q']):
        key=fold(filters['q'])
        found=[d for d in found if tim.text_matches(key,' '.join((d.number,d.partner_name or '',d.description or '',d.customer_code or '',d.supplier_code or '')))]
    rows=[d for d in found if not filters['trang_thai'] or d.status==filters['trang_thai']]
    query={k:v for k,v in filters.items() if k not in ('trang','so')}
    page,shown=paging.slice_rows(rows,filters['trang'],filters['so'],query)
    link=paging.linker('/thu-chi',query)
    posted=[d for d in rows if d.status=='DA_GHI']
    sums={kind:sum(d.amount for d in posted if d.kind==kind) for kind in docs.KINDS}
    # Ô số: phiếu đã ghi trong kỳ đang xem (theo quỹ), bấm là lọc loại phiếu.
    in_period=[d for d in found if d.status=='DA_GHI']
    def tile(kind,label,tone):
        items=[d for d in in_period if d.kind==kind]
        return dict(label=label,n=len(items),sub=money(sum(d.amount for d in items))+' đ',href=link(loai=kind,trang_thai='DA_GHI'),on=filters['loai']==kind,tone=tone)
    drafts=sum(1 for d in found if d.status=='NHAP')
    tiles=[tile('PT','Thu trong kỳ','xong'),tile('PC','Chi trong kỳ','huy'),tile('CQ','Chuyển quỹ',''),
           dict(label='Phiếu nháp',n=drafts,sub='Chưa ghi sổ' if drafts else 'Không có phiếu treo',href=link(trang_thai='NHAP',loai=''),on=filters['trang_thai']=='NHAP' and not filters['loai'],tone='cho' if drafts else '')]
    tabs=[dict(label='Tất cả',n=len(found),href=link(trang_thai=''),on=not filters['trang_thai'])]+[
        dict(label=label,n=sum(1 for d in found if d.status==code),href=link(trang_thai=code),on=filters['trang_thai']==code) for code,label in docs.STATES.items()]
    chips=[dict(label=label,href=link(**{key:''})) for key,label in (('quy','Quỹ '+filters['quy']),('loai',docs.KINDS.get(filters['loai'],''))) if filters.get(key)]
    return render(request,'thu_chi_ds.jinja',rows=shown,page=page,tiles=tiles,tabs=tabs,chips=chips,link=link,sums=sums,filters=filters,kinds=docs.KINDS,states=docs.STATES,
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
        stmt=stmt.where(and_(*tim.like_all(model.search_text, q[:200])))
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
        return _form(request,session,shown,doc,error=_error(exc),code=getattr(exc,'code','BAD_FORM'),status=getattr(exc,'status',400),fields=_where(exc,data))
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
    maker=session.get(User,doc.created_by);poster=session.get(User,doc.posted_by) if doc.posted_by else None
    source=f'{doc.source_type} {doc.source_id}' if doc.source_type and doc.source_id else ''
    phones=', '.join(p.phone for p in partner.phones) if doc.customer_code and partner else ', '.join(partner.phones) if partner else ''
    return render(request,'thu_chi_in.jinja',d=doc,kinds=docs.KINDS,states=docs.STATES,company=get_settings(),back=f'/thu-chi/{doc.id}',
                  fund=session.get(Fund,doc.fund_code),to_fund=session.get(Fund,doc.to_fund_code) if doc.to_fund_code else None,
                  category=session.get(CashCategory,doc.category_code) if doc.category_code else None,partner=partner,source=source,phones=phones,
                  maker=maker.full_name if maker else '',poster=poster.full_name if poster else '',words=money_words(doc.amount),
                  watermark={'NHAP':'BẢN NHÁP','DA_HUY':'ĐÃ HỦY'}.get(doc.status,''))


@router.get('/so-quy/{fund}')
def statement(request: Request,fund: str,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    start,end=_period(request)
    report=posting.statement(session,user,fund,start,end)
    from . import paging
    # Số dư đầu/cuối và tổng thu chi tính trên cả kỳ; mỗi dòng giữ số dư sau của chính nó.
    page,report['entries']=paging.slice_rows(report['entries'],request.query_params.get('trang'),request.query_params.get('so'),
                                             {'from':start.isoformat(),'to':end.isoformat()})
    return render(request,'so_quy.jinja',r=report,page=page,start=start.isoformat(),end=end.isoformat(),kinds=docs.KINDS)

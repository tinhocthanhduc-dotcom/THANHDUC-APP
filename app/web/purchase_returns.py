"""M4: trả hàng từ lần nhận; các POST gọi lõi trong transaction và kiểm CSRF."""
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import purchase_orders, purchase_returns as svc, quotes
from ..db import get_session
from ..errors import BusinessError
from ..models import StockDoc, User
from ..schemas import PurchaseReturnIn
from . import checked_form, render
from .purchases import purchase_user, _error, _version, _form as order_form
from .quotes import _quantity

router=APIRouter()


def _form(request, session, receipt_id, data=None, error=None, code='', status=200):
    user=request.state.user
    receipt=svc._receipt(session,receipt_id)
    order=purchase_orders.load(session,user,receipt.order_id)
    if data is None:
        data=dict(version=order.version,return_date=quotes._today().isoformat(),reason='',quantities={})
    return render(request,'don_mua_tra_form.jinja',o=order,receipt=receipt,d=data,
        pn=session.get(StockDoc,receipt.stock_doc_id),order_lines={l.id:l for l in order.lines},
        returned=svc.returned_by_line(session,user,receipt_id),quantity=_quantity,
        error=error,error_code=code,status=status)


@router.get('/don-mua/lan-nhan/{receipt_id}/tra')
def form(request:Request,receipt_id:int,user:User=Depends(purchase_user),session:Session=Depends(get_session)):
    svc.check_writer(user)
    if svc._receipt(session,receipt_id).status!='DA_GHI':
        raise BusinessError('BAD_STATUS','Chỉ trả hàng từ lần nhận đang ghi sổ.',status=409)
    return _form(request,session,receipt_id)


@router.post('/don-mua/lan-nhan/{receipt_id}/tra')
def create(request:Request,receipt_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user)
    data=dict(version=str(form.get('version','')),return_date=str(form.get('return_date','')),reason=str(form.get('reason','')),
        quantities={str(k)[4:]:str(v) for k,v in form.items() if str(k).startswith('qty_')})
    try:
        lines=[]
        for ident,value in data['quantities'].items():
            try:
                qty=Decimal(value.replace(',','.'));line_id=int(ident)
                if not qty.is_finite() or qty<0:raise InvalidOperation
            except (ValueError,InvalidOperation):
                raise BusinessError('BAD_QTY','Số trả phải là số không âm, tối đa 3 chữ số lẻ; nhập 0 để bỏ qua.')
            if qty:lines.append(dict(receipt_line_id=line_id,qty=qty))
        body=PurchaseReturnIn(version=_version(form),return_date=data['return_date'],reason=data['reason'],lines=lines)
        row=svc.create(session,user,receipt_id,body.version,body.return_date,body.reason,body.lines)
    except (BusinessError,ValidationError) as exc:
        message,code,status=_error(exc)
        return _form(request,session,receipt_id,data,message,code,status)
    return RedirectResponse(f'/don-mua/{row.order_id}?da_luu=1#tra-hang',status_code=303)


@router.post('/don-mua/tra-hang/{return_id}/huy')
def void(request:Request,return_id:int,form=Depends(checked_form),session:Session=Depends(get_session)):
    user=request.state.user;svc.check_writer(user)
    row=svc.load(session,user,return_id);order_id=row.order_id;reason=str(form.get('reason',''))
    try:svc.void(session,user,return_id,reason,_version(form))
    except BusinessError as exc:
        message,code,status=_error(exc)
        return order_form(request,session,row=purchase_orders.load(session,user,order_id),error=message,code=code,status=status,
            reason=reason,receipt_action='void_return',failed_receipt=return_id)
    return RedirectResponse(f'/don-mua/{order_id}?da_luu=1#tra-hang',status_code=303)

"""T1: màn hình quỹ, danh mục thu chi và ghép tài chính."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import finance_catalogs as catalogs, finance_mappings as mappings
from ..db import get_session
from ..errors import BusinessError
from ..models import Fund, CashCategory, User
from ..schemas import FundIn, FundPatch, CashCategoryIn, CashCategoryPatch
from ..security import has_role
from . import checked_form, render, web_user

router = APIRouter()
KINDS = {'TIEN_MAT':'Tiền mặt','NGAN_HANG':'Ngân hàng'}
DIRECTIONS = {'THU':'Thu','CHI':'Chi'}


def finance_user(user: User=Depends(web_user)):
    catalogs.check_reader(user)
    return user


def _version(form):
    try:
        version = int(str(form.get('version','')))
        if version < 1:
            raise ValueError
        return version
    except (ValueError,TypeError):
        raise BusinessError('VERSION_REQUIRED','Phải gửi phiên bản, hãy tải lại trang.')


def _register(path,model,in_schema,patch_schema):
    fund = model is Fund
    label = 'Quỹ' if fund else 'Danh mục thu chi'

    def form_page(request,data,is_new=False,**ctx):
        return render(request,'danh_muc_form.jinja',c=data,is_new=is_new,finance_catalog=True,
                      fund=fund,label=label,path=path,can_write=True,kinds=KINDS,directions=DIRECTIONS,**ctx)

    def listing(request: Request,q: str='',phan: int=0,user: User=Depends(finance_user),session: Session=Depends(get_session)):
        return render(request,'danh_muc_dong.jinja' if phan else 'danh_muc_ds.jinja',
                      rows=catalogs.search(session,user,model,q[:200]),q=q,path=path,label=label,
                      finance_catalog=True,fund=fund,kinds=KINDS,directions=DIRECTIONS,can_write=True)

    def new(request: Request,user: User=Depends(finance_user)):
        return form_page(request,dict(code='',name='',kind='TIEN_MAT',direction='THU',bank_name='',account_no='',
                                     note='',active=True,version=0),is_new=True,error=None)

    def view(request: Request,code: str,user: User=Depends(finance_user),session: Session=Depends(get_session)):
        return form_page(request,catalogs.snapshot(catalogs.load(session,user,model,code)),error=None,
                         saved=request.query_params.get('da_luu')=='1')

    def data_from(form,is_new):
        data = dict(name=str(form.get('name','')),note=str(form.get('note','')),active=form.get('active')=='1')
        if fund:
            data.update(kind=str(form.get('kind','')),bank_name=str(form.get('bank_name','')),account_no=str(form.get('account_no','')))
        else:
            data['direction'] = str(form.get('direction',''))
        if is_new:
            data['code'] = str(form.get('code',''))
        return data

    def save(request,form,session,code=None):
        user = request.state.user
        catalogs.check_writer(user)
        data = data_from(form,code is None)
        shown = dict(data,version=str(form.get('version','')),code=code) if code else dict(data,version=0)
        try:
            if code is None:
                row = catalogs.create(session,user,model,in_schema(**data))
            else:
                row = catalogs.update(session,user,model,code,patch_schema(version=_version(form),**data))
        except ValidationError:
            return form_page(request,shown,is_new=code is None,error='Tên, loại và các trường nhập phải hợp lệ; tên không được để trống.',
                             error_code='BAD_FORM',status=400)
        except BusinessError as exc:
            return form_page(request,shown,is_new=code is None,error=exc.message,error_code=exc.code,status=exc.status)
        return RedirectResponse(f'{path}/{quote(row.code,safe="")}?da_luu=1',status_code=303)

    def create(request: Request,form=Depends(checked_form),session: Session=Depends(get_session)):
        return save(request,form,session)

    def update(request: Request,code: str,form=Depends(checked_form),session: Session=Depends(get_session)):
        return save(request,form,session,code)

    router.add_api_route(path,listing,methods=['GET'])
    router.add_api_route(path+'/moi',new,methods=['GET'])
    router.add_api_route(path+'/moi',create,methods=['POST'])
    router.add_api_route(path+'/{code}',view,methods=['GET'])
    router.add_api_route(path+'/{code}',update,methods=['POST'])


_register('/quy',Fund,FundIn,FundPatch)
_register('/danh-muc-thu-chi',CashCategory,CashCategoryIn,CashCategoryPatch)


def _mapping_page(request,session,error=None,code='',status=200,failed_key='',submitted=None):
    rows = {row.key:mappings.snapshot(row) for row in mappings.search(session,request.state.user)}
    for key,row in rows.items():
        target = row['fund_code'] or row['category_code']
        model = Fund if key.startswith('THU_') else CashCategory
        item = session.get(model,target) if target else None
        row['name'] = item.name if item else ''
    return render(request,'ghep_tai_chinh.jinja',rows=rows,keys=mappings.KEYS,
                  can_write=has_role(request.state.user,'director'),error=error,error_code=code,
                  failed_key=failed_key,submitted=submitted or {},status=status,
                  saved=request.query_params.get('da_luu')=='1' and not error)


@router.get('/ghep-tai-chinh')
def mapping_page(request: Request,user: User=Depends(finance_user),session: Session=Depends(get_session)):
    return _mapping_page(request,session)


@router.get('/ghep-tai-chinh/tim/{key}')
def options(request: Request,key: str,q: str='',user: User=Depends(finance_user),session: Session=Depends(get_session)):
    return render(request,'ghep_tai_chinh_chon.jinja',rows=mappings.valid_options(session,user,key,q[:200]))


@router.post('/ghep-tai-chinh/{key}')
def mapping_save(request: Request,key: str,form=Depends(checked_form),session: Session=Depends(get_session)):
    user = request.state.user
    mappings.check_writer(user)
    target = str(form.get('target_code','')).strip() or None
    try:
        mappings.set_mapping(session,user,key,version=_version(form),
                             **({'fund_code':target} if key.startswith('THU_') else {'category_code':target}))
    except BusinessError as exc:
        return _mapping_page(request,session,error=exc.message,code=exc.code,status=exc.status,
                             failed_key=key,submitted=dict(form))
    return RedirectResponse('/ghep-tai-chinh?da_luu=1',status_code=303)

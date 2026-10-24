"""Màn hình kho: gọi nghiệp vụ hiện có; tồn và thẻ kho chỉ đọc."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from urllib.parse import quote, urlencode
import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import checked_form, render, web_user
from .. import part_requests, products, stock_docs, stock_posting, stock_min_levels, technician_stock, stock_reservations, stock_sources
from ..customers import like_pattern
from ..db import get_session
from ..errors import BusinessError
from ..inventory_catalogs import check_writer
from ..models import Product, StockBalance, StockDoc, StockLedger, StockMinLevel, Supplier, TechnicianReturn, TechnicianReturnLine, User, Warehouse
from ..schemas import StockDocCancel, StockDocIn, StockDocPatch, StockMinLevelIn
from ..security import has_role
from ..text import fold

router = APIRouter(include_in_schema=False)
VN = ZoneInfo('Asia/Ho_Chi_Minh')
KINDS = {"PN": "Nhập", "PX": "Xuất", "CK": "Chuyển", "KK": "Kiểm kê"}
STATES = {"NHAP": "Nháp", "DA_GHI": "Đã ghi", "DA_HUY": "Đã hủy"}


def _error(exc):
    if isinstance(exc, ValidationError):
        field = str(exc.errors()[0]['loc'][-1])
        labels = {'qty': 'Số lượng', 'unit_cost': 'Đơn giá', 'product_code': 'Mã hàng',
                  'doc_date': 'Ngày phiếu', 'lines': 'Dòng hàng', 'reason': 'Lý do', 'version': 'Phiên bản', 'min_qty': 'Mức tồn tối thiểu'}
        return labels.get(field, 'Dữ liệu') + ' chưa hợp lệ, hãy kiểm tra lại.'
    if exc.code == 'NEGATIVE_STOCK':
        d = exc.details
        return f"Không đủ tồn cho {d['product']}: tồn hiện có {Decimal(d['available']):.3f}, số cần {Decimal(d['requested']):.3f}."
    if exc.code == 'BAD_PRODUCT':
        return exc.message + ' ' + ', '.join(exc.details.get('invalid_codes', []))
    return exc.message


def _version(form):
    value = str(form.get('version', ''))
    if not value.isdigit() or int(value) < 1:
        raise BusinessError('BAD_VERSION', 'Thiếu phiên bản phiếu, hãy tải lại trang.')
    return int(value)


def _blank(kind='PN'):
    return dict(id=None, number='', kind=kind if kind in KINDS else 'PN', warehouse_code='',
                to_warehouse_code=None, supplier_code=None, customer_code=None,
                doc_date=date.today().isoformat(), reason='', note='', status='NHAP', version=0,
                lines=[dict(product_code='', qty='1', unit_cost='', serial='', note='')])


def _from_form(form):
    codes, qtys = form.getlist('product_code'), form.getlist('qty')
    lines = []
    # Giữ cả dòng rỗng để người dùng thấy lỗi, không tự bỏ hàng đã nhập.
    for i in range(max(len(codes), len(qtys))):
        row = {'product_code': codes[i] if i < len(codes) else '', 'qty': qtys[i] if i < len(qtys) else ''}
        for field, key in [('unit_cost', 'unit_cost'), ('serial', 'serial'), ('note', 'line_note')]:
            values = form.getlist(key)
            row[field] = values[i] if i < len(values) else ''
        lines.append(row)
    return dict(kind=str(form.get('kind', 'PN')), warehouse_code=str(form.get('warehouse_code', '')),
                to_warehouse_code=form.get('to_warehouse_code') or None,
                supplier_code=form.get('supplier_code') or None, customer_code=form.get('customer_code') or None,
                doc_date=str(form.get('doc_date', '')), reason=str(form.get('reason', '')),
                note=str(form.get('note', '')), lines=lines)


def _body(data):
    lines = []
    for i, row in enumerate(data['lines'], 1):
        cost = str(row['unit_cost']).strip()
        if cost and not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+)', cost):
            raise BusinessError('BAD_UNIT_COST', f'Đơn giá dòng {i} phải là số nguyên đồng không âm.')
        lines.append({**row, 'qty': str(row['qty']).replace(',', '.'),
                      'unit_cost': int(cost.replace('.', '')) if cost else None,
                      'serial': row['serial'] or None})
    return StockDocIn(**{**data, 'lines': lines})


def _form(request, session, doc, error=None, status=200, action_reason='', saved=False, error_code=''):
    # Chỉ các bảng danh mục được đọc để gợi ý; không sửa tồn hoặc sổ ở tầng web.
    warehouses = list(session.scalars(select(Warehouse).order_by(Warehouse.code)))
    suppliers = list(session.scalars(select(Supplier).where(or_(Supplier.active.is_(True), Supplier.code == doc.get('supplier_code'))).order_by(Supplier.name)))
    selected_dest = session.get(Warehouse,doc.get('to_warehouse_code')) if doc.get('to_warehouse_code') else None
    return render(request, 'phieu_form.jinja', d=doc, kinds=KINDS, states=STATES, tech_destination=selected_dest.name if selected_dest and selected_dest.kind == 'THO' else '',
                  warehouses=warehouses, suppliers=suppliers, error=error, error_code=error_code, status=status,
                  source=stock_sources.describe(session, stock_docs.load(session, doc['id'])) if doc.get('id') else None,
                  stock_values=stock_reservations.quantities(session,[(doc['warehouse_code'],r['product_code']) for r in doc['lines'] if r['product_code'] and doc['warehouse_code']]),
                  action_reason=action_reason, saved=saved,
                  product_names={code: name for code, name in session.execute(
                      select(Product.code, Product.name).where(Product.code.in_([r['product_code'] for r in doc['lines']]))
                  )},
                  can_write=has_role(request.state.user, 'warehouse'))


@router.get('/phieu-kho')
def listing(request: Request, loai: str = '', kho: str = '', trang_thai: str = '', tu: str = '', den: str = '', q: str = '',
            trang: str = '', so: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    from . import paging
    loai, trang_thai, q = (loai if loai in KINDS else ''), (trang_thai if trang_thai in STATES else ''), q[:200]
    error = None
    found = []
    try:
        start, end = date.fromisoformat(tu) if tu else None, date.fromisoformat(den) if den else None
        found = stock_docs.search(session, loai or None, kho or None, None, start, end)
    except ValueError:
        error = 'Ngày lọc chưa hợp lệ.'
    if q.strip():
        # Số phiếu hoặc mã/tên hàng trên dòng (không dấu).
        key = fold(q)
        hit = {code for code, text in session.execute(select(Product.code, Product.search_text).where(
            Product.code.in_({l.product_code for d in found for l in d.lines}))) if key in (text or '')}
        found = [d for d in found if key in fold(d.number) or any(l.product_code in hit or key in fold(l.product_code) for l in d.lines)]
    counts = {code: sum(1 for d in found if d.status == code) for code in STATES}
    rows = [d for d in found if not trang_thai or d.status == trang_thai]
    query = dict(q=q, loai=loai, kho=kho, trang_thai=trang_thai, tu=tu, den=den)
    page, shown = paging.slice_rows(rows, trang, so, query)
    link = paging.linker('/phieu-kho', query)
    can_value = has_role(user, 'warehouse', 'finance')
    values = _doc_values(session, rows) if can_value else {}
    codes = {l.product_code for d in shown for l in d.lines}
    month = datetime.now(VN).date().replace(day=1)
    month_docs = list(session.execute(select(StockDoc.kind, func.count()).where(StockDoc.status == 'DA_GHI', StockDoc.doc_date >= month).group_by(StockDoc.kind)).all())
    by_kind = dict(month_docs)
    drafts = session.scalar(select(func.count()).where(StockDoc.status == 'NHAP')) or 0
    month_iso = month.isoformat()
    tiles = [
        dict(label='Phiếu nháp', n=drafts, sub='Chưa ghi sổ' if drafts else 'Không có phiếu treo', href='/phieu-kho?trang_thai=NHAP', on=trang_thai == 'NHAP' and not loai, tone='cho' if drafts else ''),
        dict(label='Nhập tháng này', n=by_kind.get('PN', 0), sub='Phiếu nhập đã ghi', href=f'/phieu-kho?loai=PN&trang_thai=DA_GHI&tu={month_iso}', on=loai == 'PN' and tu == month_iso, tone='xong'),
        dict(label='Xuất tháng này', n=by_kind.get('PX', 0), sub='Giao đơn, cấp thợ, xuất khác', href=f'/phieu-kho?loai=PX&trang_thai=DA_GHI&tu={month_iso}', on=loai == 'PX' and tu == month_iso),
        dict(label='Chuyển · kiểm kê tháng này', n=by_kind.get('CK', 0) + by_kind.get('KK', 0), sub='Phiếu CK và KK đã ghi', href=f'/phieu-kho?trang_thai=DA_GHI&tu={month_iso}&loai=CK', on=loai in ('CK', 'KK') and tu == month_iso),
    ]
    tabs = [dict(label='Tất cả', n=len(found), href=link(trang_thai=''), on=not trang_thai)] + [
        dict(label=label, n=counts[code], href=link(trang_thai=code), on=trang_thai == code) for code, label in STATES.items()]
    chips = [dict(label=label, href=link(**{key: ''})) for key, label in (('kho', 'Kho ' + kho), ('loai', KINDS.get(loai, ''))) if query.get(key)]
    if tu or den:
        chips.append(dict(label='Ngày phiếu', href=link(tu='', den='')))
    posted = [d for d in rows if d.status == 'DA_GHI']
    return render(request, 'phieu_ds.jinja', rows=shown, page=page, tiles=tiles, tabs=tabs, chips=chips, link=link, kinds=KINDS, states=STATES,
                  warehouses=list(session.scalars(select(Warehouse).order_by(Warehouse.kind, Warehouse.code))),
                  loai=loai, kho=kho, trang_thai=trang_thai, tu=tu, den=den, q=q, error=error, values=values, can_value=can_value,
                  sources={d.id: stock_sources.describe(session, d) for d in shown},
                  in_sum=sum(values.get(d.id, 0) for d in posted if d.kind == 'PN'), out_sum=sum(values.get(d.id, 0) for d in posted if d.kind == 'PX'),
                  product_names=dict(session.execute(select(Product.code, Product.name).where(Product.code.in_(codes))).all()) if codes else {},
                  can_write=has_role(user, 'warehouse'))


def _doc_values(session, docs):
    """Giá trị phiếu theo giá vốn: phiếu đã ghi lấy từ sổ kho (chiều ra với PX/CK, chiều vào với PN), nháp lấy SL × đơn giá nếu có."""
    posted = [d.id for d in docs if d.status == 'DA_GHI' and d.kind != 'KK']
    out_kinds = {d.id: d.kind in ('PX', 'CK') for d in docs}
    values = {}
    if posted:
        for doc_id, qty_in, qty_out, amount in session.execute(select(StockLedger.doc_id, StockLedger.qty_in, StockLedger.qty_out, StockLedger.amount)
                .where(StockLedger.doc_id.in_(posted), StockLedger.is_reversal.is_(False))):
            if (qty_out > 0) if out_kinds[doc_id] else (qty_in > 0):
                values[doc_id] = values.get(doc_id, 0) + abs(int(amount))
    for d in docs:
        if d.status == 'NHAP' and all(l.unit_cost is not None for l in d.lines) and d.lines:
            values[d.id] = int(sum(l.qty * l.unit_cost for l in d.lines))
    return values


@router.get('/phieu-kho/tim-hang')
def product_picker(request: Request, q: str = '', warehouse: str = '', user: User = Depends(web_user), session: Session = Depends(get_session)):
    rows=products.search(session,q[:200],limit=20)
    return render(request, 'phieu_chon_hang.jinja', rows=rows,warehouse=warehouse,availability=stock_reservations.picker(session,rows,warehouse))


@router.get('/phieu-kho/tim-kho-tho')
def pick_tech_warehouse(request: Request,q: str='',user: User=Depends(web_user),session: Session=Depends(get_session)):
    rows = [dict(code=w.code,name=w.name,holder=u.full_name) for w,u in session.execute(select(Warehouse,User).join(User,User.id==Warehouse.holder_id).where(
        Warehouse.kind=='THO',Warehouse.active.is_(True)).order_by(User.full_name,Warehouse.code))
        if fold(q[:200]) in fold(w.code+' '+w.name+' '+u.full_name+' '+u.username)][:20]
    return render(request,'phieu_chon_kho_tho.jinja',rows=rows)


HIST_DAYS,HIST_RECENT,HIST_PAGE=7,5,20
RETURN_STATUSES={'CHO_NHAN':'Chờ nhận','DA_NHAN':'Đã nhận','DA_HUY':'Đã hủy'}


def _recent(rows,open_status):
    # Màn chính chỉ giữ việc đang chờ + vài phiếu xong gần đây; phần còn lại ở trang lịch sử.
    since=datetime.now(timezone.utc)-timedelta(days=HIST_DAYS)
    done=[r for r in rows if r.status!=open_status and r.created_at>=since][:HIST_RECENT]
    return [r for r in rows if r.status==open_status]+done


@router.get('/hang-toi-giu')
def technician_held(request: Request,tho: int | None=None,user: User=Depends(web_user),session: Session=Depends(get_session)):
    data=technician_stock.held(session,user,tho)
    own='technician' in user.roles and (not tho or tho==user.id)
    tech_id=data['holder'].id if data['holder'] else user.id
    returns=_recent(list(session.scalars(select(TechnicianReturn).where(TechnicianReturn.technician_id==tech_id,or_(
        TechnicianReturn.status=='CHO_NHAN',TechnicianReturn.created_at>=datetime.now(timezone.utc)-timedelta(days=HIST_DAYS))).order_by(TechnicianReturn.id.desc()))),'CHO_NHAN')
    requests=_recent(part_requests.for_requester(session,user,limit=None,since_days=HIST_DAYS),'CHO_CAP') if own else []
    return render(request,'hang_toi_giu.jinja',**data,can_return=own,return_requests=returns,can_manage=has_role(user,'warehouse'),
        return_total=session.scalar(select(func.count()).select_from(TechnicianReturn).where(TechnicianReturn.technician_id==tech_id)),
        request_total=part_requests.count_for_requester(session,user) if own else 0,return_statuses=RETURN_STATUSES,
        stock_requests=requests,part_statuses=part_requests.STATUSES,saved=request.query_params.get('da_luu')=='1',
        search_rows=[fold(f'{r.product_code} {r.name}') for r in data.get('rows',[])])


@router.get('/hang-toi-giu/lich-su')
def technician_history(request: Request,loai: str='yeu-cau',thang: str='',trang: int=1,tho: int | None=None,user: User=Depends(web_user),session: Session=Depends(get_session)):
    data=technician_stock.held(session,user,tho)
    if loai not in ('yeu-cau','tra-kho'):loai='yeu-cau'
    if loai=='yeu-cau' and tho and tho!=user.id:loai='tra-kho'  # yêu cầu mang theo chỉ chính thợ xem
    tech_id=data['holder'].id if data['holder'] else user.id
    month=re.fullmatch(r'(\d{4})-(\d{2})',thang or '')
    if loai=='tra-kho':
        model,stmt=TechnicianReturn,select(TechnicianReturn).where(TechnicianReturn.technician_id==tech_id)
    else:
        from ..models import PartRequest
        model,stmt=PartRequest,select(PartRequest).where(PartRequest.requester_id==user.id,PartRequest.ticket_id.is_(None))
    if month and 1<=int(month.group(2))<=12:
        y,m=int(month.group(1)),int(month.group(2))
        start=datetime(y,m,1,tzinfo=VN);end=datetime(y+(m==12),m%12+1,1,tzinfo=VN)
        stmt=stmt.where(model.created_at>=start,model.created_at<end)
    else:
        thang=''
    total=session.scalar(select(func.count()).select_from(stmt.subquery()))
    pages=max(1,-(-total//HIST_PAGE));trang=min(max(1,trang),pages)
    rows=list(session.scalars(stmt.order_by(model.id.desc()).offset((trang-1)*HIST_PAGE).limit(HIST_PAGE)))
    today=datetime.now(VN).date()
    months=[(today.year-(today.month-i<=0),(today.month-i-1)%12+1) for i in range(6)]
    return render(request,'hang_lich_su.jinja',loai=loai,thang=thang,trang=trang,pages=pages,total=total,rows=rows,
        months=[f'{y}-{m:02d}' for y,m in months],holder=data['holder'],tho=tho,
        own='technician' in user.roles and (not tho or tho==user.id),part_statuses=part_requests.STATUSES,return_statuses=RETURN_STATUSES)


@router.get('/hang-toi-giu/phieu/{doc_id}')
def technician_transfer(request: Request,doc_id: int,tho: int | None=None,user: User=Depends(web_user),session: Session=Depends(get_session)):
    data=technician_stock.held(session,user,tho)
    doc=stock_docs.load(session,doc_id)
    if not data['warehouse'] or doc.kind!='CK' or data['warehouse'].code not in (doc.warehouse_code,doc.to_warehouse_code):
        raise BusinessError('FORBIDDEN','Bạn chỉ được xem phiếu chuyển liên quan kho của mình.',status=403)
    return render(request,'hang_phieu_ck.jinja',d=doc,**data)


@router.get('/phieu-kho/moi')
def new(request: Request, loai: str = 'PN', user: User = Depends(web_user), session: Session = Depends(get_session)):
    data = _blank(loai)
    data['warehouse_code'] = request.query_params.get('kho_nguon','')
    data['to_warehouse_code'] = request.query_params.get('kho_dich') or None
    if loai == 'CK' and data['warehouse_code']:data['reason'] = 'Trả hàng từ kho thợ'
    return _form(request, session, data)


def _save_form(request, session, form, doc_id=None, posting=False):
    check_writer(request.state.user)
    data = _from_form(form)
    shown = {**(_blank() if doc_id is None else stock_docs.snapshot(stock_docs.load(session, doc_id))), **data,
             'version': form.get('version', '0')}
    try:
        body = _body(data)
        if doc_id is None:
            doc = stock_docs.create(session, request.state.user, body)
        else:
            doc = stock_docs.update(session, request.state.user, doc_id,
                                    StockDocPatch(version=_version(form), **body.model_dump()))
        # Sau khi lưu thành công, lỗi ghi sổ phải quay lại đúng phiếu và phiên bản mới.
        shown.update(id=doc.id, number=doc.number, version=doc.version)
        if posting:
            stock_posting.post(session, request.state.user, doc.id, doc.version)
    except (BusinessError, ValidationError) as exc:
        return _form(request, session, shown, error=_error(exc), status=getattr(exc, 'status', 400))
    return RedirectResponse(f'/phieu-kho/{doc.id}?da_luu=1', status_code=303)


@router.post('/phieu-kho/moi')
def create(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _save_form(request, session, form, posting=form.get('action') == 'post')


@router.get('/phieu-kho/{doc_id}')
def view(request: Request, doc_id: int, da_luu: int = 0, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _form(request, session, stock_docs.snapshot(stock_docs.load(session, doc_id)), saved=bool(da_luu))


@router.post('/phieu-kho/{doc_id}')
def update(request: Request, doc_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _save_form(request, session, form, doc_id)


@router.post('/phieu-kho/{doc_id}/post')
def post(request: Request, doc_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _save_form(request, session, form, doc_id, posting=True)


def _cancel(request, session, form, doc_id, posted):
    check_writer(request.state.user)
    shown = stock_docs.snapshot(stock_docs.load(session, doc_id))
    shown['version'] = form.get('version', '')
    reason = str(form.get('reason', ''))
    try:
        version = _version(form)
        if posted:
            stock_posting.void(session, request.state.user, doc_id, reason, version)
        else:
            stock_docs.cancel(session, request.state.user, doc_id, StockDocCancel(reason=reason, version=version))
    except (BusinessError, ValidationError) as exc:
        return _form(request, session, shown, error=_error(exc), error_code=getattr(exc, 'code', ''), status=getattr(exc, 'status', 400), action_reason=reason)
    return RedirectResponse(f'/phieu-kho/{doc_id}?da_luu=1', status_code=303)


@router.post('/phieu-kho/{doc_id}/cancel')
def cancel(request: Request, doc_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _cancel(request, session, form, doc_id, False)


@router.post('/phieu-kho/{doc_id}/void')
def void(request: Request, doc_id: int, form=Depends(checked_form), session: Session = Depends(get_session)):
    return _cancel(request, session, form, doc_id, True)


TON_SORTS = {'': 'Kho · mã hàng', 'it': 'Có thể dùng ít → nhiều', 'gia-tri': 'Giá trị tồn cao → thấp'}
TON_STATES = {'con': 'Còn hàng', 'het': 'Hết hàng'}


@router.get('/ton-kho')
def balances(request: Request, kho: str = '', q: str = '', phan: int = 0, nhom: str = '', xep: str = '', tt: str = '',
             canh_bao: bool = Query(default=False, alias='canh-bao'),
             user: User = Depends(web_user), session: Session = Depends(get_session)):
    from . import nav
    # Khuôn 3: chỉ người được xem Sắp hết mới thấy mức tối thiểu và ô Sắp hết.
    can_alert = nav.allowed(user, '/ton-kho', {'canh-bao': '1'})
    xep, tt = (xep if xep in TON_SORTS else ''), ('' if canh_bao or tt not in TON_STATES else tt)
    normal = select(StockBalance.warehouse_code, StockBalance.product_code, StockBalance.qty,
                  StockBalance.value, StockBalance.avg_cost, Product.name, Product.unit, Product.group_name,
                  Warehouse.kind.label('warehouse_kind'), func.coalesce(StockMinLevel.min_qty, 0).label('min_qty')).join(
        Warehouse,Warehouse.code==StockBalance.warehouse_code).join(
        Product, Product.code == StockBalance.product_code).outerjoin(StockMinLevel, (StockMinLevel.warehouse_code == StockBalance.warehouse_code)
        & (StockMinLevel.product_code == StockBalance.product_code)).order_by(StockBalance.warehouse_code, StockBalance.product_code)
    stmt, warehouse_column = (stock_min_levels.low_stock_query(), StockMinLevel.warehouse_code) if canh_bao else (normal, StockBalance.warehouse_code)
    def narrow(stmt, column):
        if kho:
            stmt = stmt.where(column == kho)
        if nhom:
            stmt = stmt.where(Product.group_name == nhom)
        if fold(q):
            stmt = stmt.where(or_(Product.search_text.like(like_pattern(fold(q))), Product.code.ilike(like_pattern(q))))
        return stmt
    rows = list(session.execute(narrow(stmt, warehouse_column)).mappings())
    values=stock_reservations.quantities(session,[(r['warehouse_code'],r['product_code']) for r in rows])
    rows=[{**r,**values.get((r['warehouse_code'],r['product_code']),dict(reserved=0,available=r['qty']))} for r in rows]
    for r in rows:
        low = can_alert and r.get('warehouse_kind', 'KHO') == 'KHO' and r['min_qty'] > 0 and r['qty'] <= r['min_qty']
        r['state'] = 'het' if r['qty'] <= 0 else 'sap' if low else 'con'
    # Ô đếm luôn tính trên tồn thường (cùng kho/nhóm/tìm), để bấm qua lại giữa các ô không đổi số.
    totals = [r.qty for r in session.execute(narrow(normal, StockBalance.warehouse_code))] if canh_bao else [r['qty'] for r in rows]
    counts = dict(all=len(totals), con=sum(x > 0 for x in totals), het=sum(x <= 0 for x in totals))
    if can_alert:
        counts['sap'] = len(rows) if canh_bao else int(session.scalar(select(func.count()).select_from(
            narrow(stock_min_levels.low_stock_query(), StockMinLevel.warehouse_code).order_by(None).subquery())))
    if tt:
        rows = [r for r in rows if (r['qty'] > 0) == (tt == 'con')]
    if xep == 'it':
        rows.sort(key=lambda r: r['available'])
    elif xep == 'gia-tri':
        rows.sort(key=lambda r: -r['value'])
    damage_sources=session.execute(select(TechnicianReturn,TechnicianReturnLine,User.full_name,StockDoc.to_warehouse_code).join(
        TechnicianReturnLine,TechnicianReturnLine.return_id==TechnicianReturn.id).join(User,User.id==TechnicianReturn.technician_id).join(
        StockDoc,StockDoc.id==TechnicianReturn.defect_stock_doc_id).where(TechnicianReturn.status=='DA_NHAN',StockDoc.status=='DA_GHI',
        TechnicianReturnLine.received_condition=='HU_HONG',TechnicianReturnLine.received_qty>0)).all()
    damage_sources=[r for r in damage_sources if (not kho or r.to_warehouse_code==kho) and (not fold(q) or any(
        b['product_code']==r.TechnicianReturnLine.product_code and b['warehouse_code']==r.to_warehouse_code for b in rows))]
    current = {'kho': kho, 'q': q, 'nhom': nhom, 'xep': xep, 'tt': tt, 'canh-bao': '1' if canh_bao else ''}
    def link(**changes):
        merged = {**current, **{k.replace('_', '-'): v for k, v in changes.items()}}
        return '/ton-kho?' + urlencode({k: v for k, v in merged.items() if v})
    groups = [g for g in session.scalars(select(Product.group_name).where(Product.group_name != '').distinct().order_by(Product.group_name))]
    return render(request, 'ton_dong.jinja' if phan else 'ton_ds.jinja', can_pending_transfer=has_role(user,'warehouse'),damage_sources=damage_sources,rows=rows, q=q, kho=kho, canh_bao=canh_bao,
                  warehouses=list(session.scalars(select(Warehouse).where(Warehouse.kind=='KHO') if canh_bao else select(Warehouse).order_by(Warehouse.code))),total_value=sum(row['value'] for row in rows),
                  nhom=nhom, xep=xep, tt=tt, sorts=TON_SORTS, groups=groups, counts=counts, can_alert=can_alert, link=link)


def _card(request, session, kho, ma, min_qty=None, version=None, error=None, status=200, saved=False):
    product = products.load(session, ma)
    warehouse = session.get(Warehouse, kho)
    if warehouse is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy kho.', status=404)
    rows = list(session.execute(select(StockLedger, StockDoc.number).join(StockDoc, StockDoc.id == StockLedger.doc_id)
                                .where(StockLedger.warehouse_code == kho, StockLedger.product_code == product.code)
                                .order_by(StockLedger.id)))
    level = session.get(StockMinLevel, (kho, product.code))
    return render(request, 'the_kho.jinja', rows=rows, product=product, warehouse=warehouse,
                  stock=stock_reservations.quantities(session,[(kho,ma)]).get((kho,ma),dict(qty=0,reserved=0,available=0)),
                  reservations=stock_reservations.held_orders(session,kho,ma),
                  min_qty=min_qty if min_qty is not None else str(level.min_qty if level else 0),
                  level_version=version if version is not None else (level.version if level else 0),
                  can_write=has_role(request.state.user, 'warehouse') and warehouse.kind=='KHO', error=error, status=status, saved=saved)


@router.get('/the-kho/{kho}/{ma}')
def ledger(request: Request, kho: str, ma: str, da_luu: int = 0,
           user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _card(request, session, kho, ma, saved=bool(da_luu))


@router.post('/the-kho/{kho}/{ma}')
def set_minimum(request: Request, kho: str, ma: str, form=Depends(checked_form), session: Session = Depends(get_session)):
    check_writer(request.state.user)
    raw_qty, version = str(form.get('min_qty', '')), form.get('version', '')
    try:
        body = StockMinLevelIn(min_qty=raw_qty.replace(',', '.'), version=version)
        stock_min_levels.set_level(session, request.state.user, kho, ma, body)
    except (BusinessError, ValidationError) as exc:
        return _card(request, session, kho, ma, min_qty=raw_qty, version=version,
                     error=_error(exc), status=getattr(exc, 'status', 400))
    return RedirectResponse(f'/the-kho/{quote(kho, safe="")}/{quote(ma, safe="")}?da_luu=1', status_code=303)

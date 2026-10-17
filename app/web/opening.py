"""Nhập CSV tồn đầu kỳ trên web; chỉ nghiệp vụ K5 tạo phiếu KK nháp."""
import csv
from datetime import date
from io import StringIO
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from . import checked_form, render, web_user
from .. import stock_opening
from ..db import get_session
from ..errors import BusinessError
from ..inventory_catalogs import check_writer
from ..models import StockDoc, User, Warehouse
from ..security import has_role

router = APIRouter(include_in_schema=False)
MAX_FILE_BYTES = 2 * 1024 * 1024
COLUMNS = ('MA_HANG', 'SO_LUONG', 'DON_GIA')
ERROR_LABELS = {
    'MISSING_CODE': 'Thiếu mã hàng',
    'DUPLICATE_CODE': 'Mã trùng',
    'BAD_QTY': 'Số lượng sai',
    'BAD_UNIT_COST': 'Đơn giá sai',
    'UNKNOWN_PRODUCT': 'Mã không có trong danh mục',
    'INACTIVE_PRODUCT': 'Mã đã ngừng bán',
}


def _page(request, session, warehouse='', doc_date='2026-12-31', error=None, errors=None, existing=None, status=200):
    return render(request, 'ton_dau_ky.jinja', warehouse=warehouse, doc_date=doc_date,
                  warehouses=list(session.scalars(select(Warehouse).order_by(Warehouse.code))),
                  can_write=has_role(request.state.user, 'warehouse'),
                  error=error, errors=errors or [], existing=existing or [], status=status)


def _read_csv(payload):
    try:
        text = payload.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise BusinessError('BAD_CSV', 'File phải dùng mã hóa UTF-8, có thể có BOM.')
    if not text.strip():
        return []
    try:
        # Xác định dấu phân cách từ tiêu đề để dòng dữ liệu lỗi không làm hỏng nhận diện.
        delimiter = csv.Sniffer().sniff(text.splitlines()[0], delimiters=',;').delimiter
        reader = csv.DictReader(StringIO(text, newline=''), delimiter=delimiter, strict=True)
        header = [(name or '').strip() for name in (reader.fieldnames or [])]
        if not set(COLUMNS).issubset(header) or len(set(header)) != len(header):
            raise BusinessError('BAD_CSV', 'File cần đủ ba cột MA_HANG, SO_LUONG, DON_GIA, không lặp tên cột.')
        reader.fieldnames = header
        rows = []
        for row in reader:
            if None in row:
                raise BusinessError('BAD_CSV', 'Dòng CSV có thừa cột; hãy kiểm tra dấu phân cách hoặc dấu ngoặc kép.')
            rows.append({column: row.get(column) or '' for column in COLUMNS})
        return rows
    except csv.Error:
        raise BusinessError('BAD_CSV', 'Định dạng CSV chưa hợp lệ. Dùng dấu phẩy hoặc chấm phẩy và đủ ba cột MA_HANG, SO_LUONG, DON_GIA.')


def _error_rows(errors, rows):
    positions = {}
    for number, row in enumerate(rows, 2):
        positions.setdefault(row['MA_HANG'].strip().upper(), number)
    shown = []
    for item in errors:
        label = ERROR_LABELS.get(item.get('error'), 'Dữ liệu không hợp lệ')
        if item.get('error') == 'DUPLICATE_CODE':
            label += f" (dòng đầu: {item.get('first_row', '—')})"
        shown.append(dict(row=item.get('row', positions.get(item.get('code'), '—')),
                          code=item.get('code', ''), message=label))
    return shown


@router.get('/ton-dau-ky')
def page(request: Request, user: User = Depends(web_user), session: Session = Depends(get_session)):
    return _page(request, session)


@router.get('/ton-dau-ky/mau.csv')
def sample(user: User = Depends(web_user)):
    return Response('MA_HANG,SO_LUONG,DON_GIA\r\nTD-0001,10,120000\r\n', media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="ton-dau-ky-mau.csv"'})


@router.get('/ton-dau-ky/ket-qua')
def result(request: Request, phieu: list[int] = Query(default=[], max_length=2000),
           user: User = Depends(web_user), session: Session = Depends(get_session)):
    docs = list(session.scalars(select(StockDoc).where(StockDoc.id.in_(phieu), StockDoc.kind == 'KK',
                                   StockDoc.reason == stock_opening.OPENING_REASON).order_by(StockDoc.id)))
    return render(request, 'ton_dau_ky_ket_qua.jinja', docs=docs)


def _import_file(request, session, warehouse, doc_date, payload):
    rows = []
    try:
        try:
            parsed_date = date.fromisoformat(doc_date)
        except ValueError:
            raise BusinessError('BAD_DATE', 'Ngày phiếu chưa hợp lệ.')
        rows = _read_csv(payload)
        docs = stock_opening.create_opening(session, request.state.user, warehouse, parsed_date, rows)
    except BusinessError as exc:
        existing = []
        if exc.code == 'OPENING_EXISTS':
            existing = list(session.scalars(select(StockDoc).where(
                StockDoc.warehouse_code == warehouse, StockDoc.number.in_(exc.details.get('docs', []))
            ).order_by(StockDoc.id)))
        return _page(request, session, warehouse, doc_date, error=exc.message,
                     errors=_error_rows(exc.details.get('errors', []), rows), existing=existing, status=exc.status)
    return RedirectResponse('/ton-dau-ky/ket-qua?' + urlencode([('phieu', doc.id) for doc in docs]), status_code=303)


@router.post('/ton-dau-ky')
async def upload(request: Request, form=Depends(checked_form), session: Session = Depends(get_session)):
    check_writer(request.state.user)
    warehouse, doc_date = str(form.get('warehouse_code', '')), str(form.get('doc_date', ''))
    file = form.get('file')
    if not isinstance(file, UploadFile) or not file.filename:
        return _page(request, session, warehouse, doc_date, error='Hãy chọn file CSV để tải lên.', status=400)
    try:
        payload = await file.read(MAX_FILE_BYTES + 1)
    finally:
        await file.close()
    if len(payload) > MAX_FILE_BYTES:
        return _page(request, session, warehouse, doc_date, error='File CSV không được lớn hơn 2 MB.', status=413)
    return await run_in_threadpool(_import_file, request, session, warehouse, doc_date, payload)

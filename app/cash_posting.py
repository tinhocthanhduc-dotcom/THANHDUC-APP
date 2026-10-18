"""T2: nơi duy nhất thay đổi số dư và thêm sổ quỹ; hủy bằng dòng đảo."""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from . import cash_docs as docs, day_closings
from .errors import BusinessError
from .finance_catalogs import check_reader, check_writer, check_version
from .models import FundBalance, FundLedger
from .schemas import CashDocIn
from .security import has_role

# Capability nội bộ: chỉ luồng duyệt điều chỉnh truyền, không nhận từ HTTP.
_CORRECTION_PERMIT = object()


def balance(session,user,fund_code):
    check_reader(user)
    # Chỉ SELECT, không tạo dòng balance khi xem quỹ chưa phát sinh.
    return session.scalar(select(FundBalance.balance).where(FundBalance.fund_code==fund_code)) or 0


def _lock_balances(session,codes):
    ordered = sorted(codes)
    session.execute(insert(FundBalance).values([dict(fund_code=c,balance=0,version=1) for c in ordered])
                    .on_conflict_do_nothing())
    return {code:session.scalar(select(FundBalance).where(FundBalance.fund_code==code).with_for_update()
                               .execution_options(populate_existing=True)) for code in ordered}


def _apply(session,doc,balances,moves,reversal=False,entry_date=None):
    # Kiểm tất cả trước khi thêm sổ, không có nửa phiếu chuyển quỹ.
    for code,amount in moves:
        current = balances[code].balance
        if current+amount<0:
            raise BusinessError('NEGATIVE_CASH',f'Quỹ {code} không đủ số dư để thực hiện.',
                                {'fund':code,'available':current,'requested':-amount},status=409)
        if current+amount>docs.MAX_MONEY:
            raise BusinessError('AMOUNT_TOO_LARGE','Số dư quỹ vượt giới hạn số nguyên đồng.',{'fund':code},status=409)
    for code,amount in moves:
        bal = balances[code]
        bal.balance += amount
        session.add(FundLedger(fund_code=code,entry_date=(entry_date or docs.today()) if reversal else doc.doc_date,
                               doc_id=doc.id,amount=amount,balance_after=bal.balance,is_reversal=reversal))


def post(session,user,doc_id,version):
    check_writer(user)
    with docs._transaction(session):
        doc = post_in_tx(session,user,doc_id,version)
    return doc


def post_in_tx(session,user,doc_id,version):
    """Không commit/kiểm vai trò; caller tự kiểm quyền. Đầu kỳ vẫn chỉ director."""
    doc = docs._load(session,doc_id,True)
    docs._opening(user,doc.is_opening)
    check_version(doc,version)
    day_closings.check_open(session,doc.doc_date)
    docs._draft(doc)
    docs._validate(session,user,CashDocIn(**{k:getattr(doc,k) for k in (*docs.HEADER_FIELDS,'source_type','source_id')}))
    before = docs.snapshot(doc)
    moves = [(doc.fund_code,doc.amount if doc.kind=='PT' else -doc.amount)]
    if doc.kind=='CQ':
        moves.append((doc.to_fund_code,doc.amount))
    _apply(session,doc,_lock_balances(session,{code for code,_ in moves}),moves)
    doc.status,doc.posted_by,doc.posted_at = 'DA_GHI',user.id,datetime.now(UTC)
    docs._record(session,user,doc,'post',before)
    return doc


def void(session,user,doc_id,reason,version):
    check_writer(user)
    with docs._transaction(session):
        doc = docs._load(session,doc_id,True)
        docs.check_source(doc)
        doc = void_in_tx(session,user,doc_id,reason,version)
    return doc


def void_in_tx(session,user,doc_id,reason,version,*,_correction_permit=None):
    """Đảo đúng tiền đã ghi, ngày hủy theo VN; không commit hay kiểm vai trò ngoài đầu kỳ."""
    doc = docs._load(session,doc_id,True)
    docs._opening(user,doc.is_opening)
    check_version(doc,version)
    if _correction_permit is not None and (_correction_permit is not _CORRECTION_PERMIT or not has_role(user,'director')):
        raise BusinessError('FORBIDDEN','Chỉ duyệt điều chỉnh được bỏ qua ngày gốc đã chốt.',status=403)
    reversal_day=docs.today()
    day_closings.check_days(session,[reversal_day] if _correction_permit is _CORRECTION_PERMIT else [doc.doc_date,reversal_day])
    if doc.status!='DA_GHI':
        raise BusinessError('DOC_NOT_POSTED','Chỉ hủy ghi đảo với phiếu đã ghi sổ.',status=409)
    reason = (reason or '').strip()
    if not reason or len(reason)>2000:
        raise BusinessError('REASON_REQUIRED','Hủy phiếu phải nhập lý do (tối đa 2000 ký tự).')
    before = docs.snapshot(doc)
    rows = session.scalars(select(FundLedger).where(FundLedger.doc_id==doc.id,FundLedger.is_reversal.is_(False))
                           .order_by(FundLedger.id.desc())).all()
    moves = [(r.fund_code,-r.amount) for r in rows]
    _apply(session,doc,_lock_balances(session,{code for code,_ in moves}),moves,True,reversal_day)
    doc.status,doc.void_reason = 'DA_HUY',reason
    docs._record(session,user,doc,'void',before,reason)
    return doc


def balances(session,user,codes):
    check_reader(user)
    return dict(session.execute(select(FundBalance.fund_code,FundBalance.balance).where(FundBalance.fund_code.in_(codes))).all())


def statement(session,user,fund_code,start,end):
    """Một snapshot SQL: số dư đầu + sổ trong kỳ; CQ/đầu kỳ không lẫn thu chi thường."""
    from sqlalchemy import case, func, true
    from .finance_catalogs import load
    from .models import CashDoc, Fund
    check_reader(user)
    fund = load(session,user,Fund,fund_code)
    if start>end:
        raise BusinessError('BAD_DATE_RANGE','Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
    opening = select(func.coalesce(func.sum(FundLedger.amount),0)).where(
        FundLedger.fund_code==fund_code,FundLedger.entry_date<start).correlate(None).scalar_subquery()
    conditions = (FundLedger.fund_code==fund_code,FundLedger.entry_date>=start,FundLedger.entry_date<=end)
    def total(condition,expr):
        return func.coalesce(func.sum(case((condition,expr),else_=0)),0)
    ordinary = CashDoc.is_opening.is_(False)
    amount = FundLedger.amount
    metrics = select(opening.label('opening'),
        (opening+func.coalesce(func.sum(amount),0)).label('closing'),
        total(ordinary & (CashDoc.kind=='PT'),amount).label('income'),
        total(ordinary & (CashDoc.kind=='PC'),-amount).label('expense'),
        total((CashDoc.kind=='CQ') & (amount>0),amount).label('transfer_in'),
        total((CashDoc.kind=='CQ') & (amount<0),-amount).label('transfer_out'),
        total(CashDoc.is_opening,amount).label('opening_adjustment')
    ).join(CashDoc,CashDoc.id==FundLedger.doc_id).where(*conditions).subquery()
    # balance_after lưu số dư thực lúc ghi. Khi ghi lùi ngày, sổ theo ngày kế toán dùng SUM OVER.
    lines = select(FundLedger.id,FundLedger.entry_date,FundLedger.doc_id,FundLedger.amount,FundLedger.is_reversal,
        CashDoc.number,CashDoc.kind,CashDoc.description,CashDoc.is_opening,
        (opening+func.sum(amount).over(order_by=(FundLedger.entry_date,FundLedger.id),rows=(None,0))).label('balance')
    ).join(CashDoc,CashDoc.id==FundLedger.doc_id).where(*conditions).subquery()
    rows = session.execute(select(metrics,lines).select_from(metrics.outerjoin(lines,true()))
                           .order_by(lines.c.entry_date,lines.c.id)).mappings().all()
    keys = ('opening','closing','income','expense','transfer_in','transfer_out','opening_adjustment')
    result = {k:int(rows[0][k]) for k in keys}
    result.update(fund=fund,entries=[{k:int(v) if k in ('amount','balance') else v for k,v in r.items() if k not in keys}
                                   for r in rows if r['id'] is not None])
    return result

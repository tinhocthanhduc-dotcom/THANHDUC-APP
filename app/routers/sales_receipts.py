from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import sales_orders, sales_receipts as svc
from ..db import get_session
from ..models import User
from ..schemas import (SalesOrderReceipts, SalesReceiptDecision, SalesReceiptOut,
                       SalesReceiptReport, SalesReceivableOut, QuoteVersion)
from ..security import current_user

router = APIRouter(tags=['Báo thu'])


@router.get('/sales-orders/receivables', response_model=list[SalesReceivableOut])
def receivables(user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.receivables(session,user)


@router.get('/sales-orders/{order_id}/receipts', response_model=SalesOrderReceipts)
def for_order(order_id: int,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.for_order(session,user,order_id)


@router.post('/sales-orders/{order_id}/receipts', response_model=SalesReceiptOut,status_code=201)
def report(order_id: int,body: SalesReceiptReport,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.report(session,user,order_id,body)


@router.get('/sales-receipts', response_model=list[SalesReceiptOut])
def pending(method: str|None=None,from_date: date|None=Query(default=None,alias='from'),
            to_date: date|None=Query(default=None,alias='to'),user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.list_pending(session,user,method,from_date,to_date)


@router.post('/sales-receipts/{receipt_id}/confirm',response_model=SalesReceiptOut)
def confirm(receipt_id: int,body: QuoteVersion,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.confirm(session,user,receipt_id,body.version)


@router.post('/sales-receipts/{receipt_id}/reject',response_model=SalesReceiptOut)
def reject(receipt_id: int,body: SalesReceiptDecision,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.reject(session,user,receipt_id,body.reason,body.version)


@router.post('/sales-receipts/{receipt_id}/void',response_model=SalesReceiptOut)
def void(receipt_id: int,body: SalesReceiptDecision,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.void(session,user,receipt_id,body.reason,body.version)


@router.get('/service-tickets/{ticket_id}/receipts',response_model=SalesOrderReceipts)
def for_ticket(ticket_id: int,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.for_ticket(session,user,ticket_id)


@router.post('/service-tickets/{ticket_id}/receipts',response_model=SalesReceiptOut,status_code=201)
def report_for_ticket(ticket_id: int,body: SalesReceiptReport,user: User=Depends(current_user),session: Session=Depends(get_session)):
    return svc.report_for_ticket(session,user,ticket_id,body)

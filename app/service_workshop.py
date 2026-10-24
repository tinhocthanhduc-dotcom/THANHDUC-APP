"""MV1: one dossier from onsite intake through workshop repair and return."""
from . import equipment_custody as custody, part_requests, service_charges, service_tickets as tickets, technician_work
from .errors import BusinessError
from .models import ServiceLine
from .schemas import CustodyHandover
from .security import has_role
from .stock_docs import _transaction

READY = 'SUA_XONG_CHO_TRA'
WAITING = 'CHO_SUA_XUONG'
WARRANTIES = (0, 1, 2, 3, 6, 12)


def hand_to_workshop(session,user,ident,version,custody_body):
    with _transaction(session):
        t=_owned(session,user,ident,version)
        if service_charges.return_locked(t):
            raise BusinessError('CHARGES_LOCKED','Phiếu đã chốt để trả máy.',status=409)
        eq=held_machine(session,t)
        if eq.custody_location!='THO_GIU' or eq.custody_holder_id!=user.id:
            raise BusinessError('MACHINE_NOT_HELD','Máy phải do chính bạn giữ theo phiếu này.',status=409)
        if custody_body.location not in ('KHO_KY_THUAT','DOI_TAC'):
            raise BusinessError('BAD_LOCATION','Chỉ bàn giao về Kho kỹ thuật hoặc Đối tác.')
        custody.handover_in_tx(session,user,t.id,custody_body)
        tickets.transition_in_tx(session,user,t,'TAM_DUNG','Bàn giao máy về xưởng',
            hold_reason=WAITING,_action='workshop_handover')
    return t


def _waiting(session,user,ident,version):
    tickets._require(user,tickets.DISPATCHERS,'Chỉ điều phối hoặc giám đốc được xử lý sửa ở xưởng.')
    t=part_requests._ticket(session,ident)
    tickets._check_version(t,version)
    if t.status!='TAM_DUNG' or t.hold_reason!=WAITING or service_charges.return_locked(t):
        raise BusinessError('BAD_STATUS','Phiếu không ở bước chờ sửa tại xưởng.',status=409)
    return t


def warranty_free(t):
    return t.billing=='MIEN_PHI' and t.warranty_basis in ('PHIEU_TRUOC','MAY_CON_HAN')


def quote_approve(session,user,ident,version,promised_return_on,note=''):
    from datetime import date, datetime
    with _transaction(session):
        t=_waiting(session,user,ident,version)
        if t.declined_at:raise BusinessError('BAD_STATUS','Khách đã từ chối sửa.',status=409)
        if not isinstance(promised_return_on,date) or isinstance(promised_return_on,datetime) or promised_return_on<tickets._now().astimezone(tickets.service_sla.TZ).date():
            raise BusinessError('BAD_APPOINTMENT','Chọn ngày hẹn trả từ hôm nay trở đi.')
        if not warranty_free(t) and not any(x.amount+x.vat_amount>0 for x in t.lines):
            raise BusinessError('EMPTY_CHARGES','Báo giá chưa có dòng phí hoặc vật tư có giá.')
        before=tickets.snapshot(t)
        t.quote_approved_at,t.quote_approved_by=tickets._now(),user.id
        t.promised_return_on=promised_return_on
        tickets._record(session,user,t,'quote_approve',before,t.status,note)
    return t


def _ready(session,user,t,action,before,note):
    t.status,t.hold_reason='TAM_DUNG',READY
    t.due_at=tickets.service_sla.due_at(t.status,t.priority,tickets._now(),t.appointment_at,t.appointment_slot)
    tickets._record(session,user,t,action,before,'TAM_DUNG',note)


def quote_decline(session,user,ident,version,mode,fee,reason):
    from .config import DEFAULT_VAT_RATE
    from .schemas import ServiceCharges
    reason=(reason or '').strip()
    with _transaction(session):
        t=_waiting(session,user,ident,version)
        if mode not in ('FULL','FREE'):raise BusinessError('BAD_COLLECTION','Chọn Thu phí kiểm tra hoặc Miễn phí.')
        before=tickets.snapshot(t)
        lines=[service_charges.line_input(x) for x in t.lines if x.stock_doc_id is not None]
        if mode=='FULL':
            amount=technician_work.money(fee)
            if amount<=0:raise BusinessError('CUSTOMER_AMOUNT_REQUIRED','Phí kiểm tra phải lớn hơn 0.')
            lines.append(dict(kind='PHI',description='Phí kiểm tra',qty=1,unit_price=amount,vat_rate=DEFAULT_VAT_RATE))
        service_charges.set_charges_in_tx(session,user,t,ServiceCharges(version=t.version,
            billing='MIEN_PHI' if mode=='FREE' else 'THU_TIEN',free_reason=reason if mode=='FREE' else '',
            billing_reason=reason,lines=lines))
        service_charges.complete(session,user,t)
        t.declined_at=tickets._now()
        t.repaired_by=None
        t.warranty_months,t.warranty_scope=0,''
        _ready(session,user,t,'quote_decline',before,reason)
    return t


def workshop_repaired(session,user,ident,version,repaired_by,mode,reason,fault,fix,warranty_months,warranty_scope=''):
    with _transaction(session):
        t=_waiting(session,user,ident,version)
        if t.quote_approved_at is None and not warranty_free(t):
            raise BusinessError('QUOTE_NOT_APPROVED','Khách phải đồng ý báo giá trước khi sửa xong.',status=409)
        eq=custody._machine(session,t.serial,lock=True)
        if eq.custody_location=='DOI_TAC':raise BusinessError('MACHINE_AT_PARTNER','Nhận máy từ đối tác về kho kỹ thuật trước khi sửa xong.',status=409)
        held_machine(session,t)
        if repaired_by is None:raise BusinessError('REPAIRER_REQUIRED','Chọn thợ đã sửa máy.')
        tickets._technician(session,repaired_by)
        if type(warranty_months) is not int or warranty_months not in WARRANTIES:
            raise BusinessError('WARRANTY_REQUIRED','Chọn bảo hành 0, 1, 2, 3, 6 hoặc 12 tháng.')
        scope=(warranty_scope or '').strip() or 'Bảo hành hạng mục và linh kiện đã sửa chữa'
        if len(scope)>200:raise BusinessError('TEXT_TOO_LONG','Phạm vi bảo hành tối đa 200 ký tự.')
        if mode not in ('FULL','FREE'):raise BusinessError('BAD_COLLECTION','Chọn Có thu tiền hoặc Miễn phí.')
        from .attachments import has_visible
        if not has_visible(session,'service_ticket',t.id,'SAU'):
            raise BusinessError('PHOTO_REQUIRED','Cần ảnh nghiệm thu sau sửa.')
        before=tickets.snapshot(t)
        technician_work.apply_report(session,t,fault,fix)
        if mode=='FREE':
            if len((reason or '').strip())<3:raise BusinessError('REASON_REQUIRED','Miễn phí phải ghi lý do.')
            t.billing,t.free_reason='MIEN_PHI',reason.strip()
        elif t.billing!='THU_TIEN':
            raise BusinessError('BILLING_REQUIRED','Lưu báo giá Có thu tiền trước khi Sửa xong.')
        service_charges.complete(session,user,t)
        t.repaired_by=repaired_by
        t.warranty_months,t.warranty_scope=warranty_months,scope
        _ready(session,user,t,'repair_done',before,'Sửa xong, chờ trả máy')
    return t


def check_handover_record(session,t):
    """Trả máy sửa ở xưởng cần một ảnh: ảnh biên bản riêng hoặc ảnh trả máy chụp kèm biên bản đã ký (TUẤN 24/10)."""
    if t.declined_at is not None or t.warranty_months is not None:
        from .attachments import has_visible
        if not has_visible(session,'service_ticket',t.id,'BIEN_BAN'):
            custody._photo(session,t.id,'TRA_MAY')


def _owned(session,user,ident,version):
    t=part_requests._ticket(session,ident)
    if 'technician' not in user.roles or t.assignee_id!=user.id:
        raise BusinessError('FORBIDDEN','Chỉ thợ được giao được thao tác sửa máy.',status=403)
    tickets._check_version(t,version)
    if t.status!='DANG_LAM':raise BusinessError('BAD_STATUS','Phiếu phải đang làm.',status=409)
    return t


def held_machine(session,t):
    if not t.serial:raise BusinessError('SERIAL_REQUIRED','Phiếu chưa có serial máy, gọi điều phối thêm serial.')
    eq=custody._machine(session,t.serial,lock=True)
    custody._no_pending(session,eq.serial)
    if eq.custody_ticket_id!=t.id or eq.custody_location not in ('THO_GIU','KHO_KY_THUAT'):
        raise BusinessError('MACHINE_NOT_HELD','Máy phải đang do thợ hoặc kho kỹ thuật giữ theo phiếu này.',status=409)
    return eq


def bring(session,user,ident,version,intake,materials,to_workshop=False):
    with _transaction(session):
        t=_owned(session,user,ident,version)
        if t.kind not in (None,'SUA_TAI_CHO','BAO_HANH','MANG_VE') or service_charges.return_locked(t):
            raise BusinessError('BAD_STATUS','Chỉ mang về từ phiếu đang làm, máy còn ở chỗ khách.',status=409)
        before=tickets.snapshot(t)
        custody.receive_in_tx(session,user,t.id,intake)
        entries=technician_work.material_lines(session,user,materials)
        from .technician_stock import own_warehouse
        own=own_warehouse(session,user)
        # Replace this technician's unposted draft selection with the declared
        # installed parts; never touch an issued row or another technician's row.
        t.lines[:]=[x for x in t.lines if x.kind!='VAT_TU' or x.stock_doc_id is not None or not own or x.warehouse_code!=own.code]
        session.flush()
        start=max((x.line_no for x in t.lines),default=0)+1
        added=[ServiceLine(line_no=i,**x,amount=0,vat_amount=0) for i,x in enumerate(entries,start)]
        t.lines.extend(added)
        service_charges.issue_materials(session,user,t,added)
        if t.kind in (None,'SUA_TAI_CHO'):t.kind='MANG_VE'
        t.version+=1
        tickets._record(session,user,t,'workshop',before,'DANG_LAM','Mang máy về xưởng')
        if to_workshop:
            # TUẤN 24/10: máy mang về mặc định giao Kho kỹ thuật (kho xác nhận nhận); gửi đối tác do điều phối làm sau.
            eq=custody._machine(session,t.serial,lock=True)
            custody.handover_in_tx(session,user,t.id,CustodyHandover(version=eq.version,location='KHO_KY_THUAT'))
            tickets.transition_in_tx(session,user,t,'TAM_DUNG','Mang máy về, giao Kho kỹ thuật',
                hold_reason=WAITING,_action='workshop_handover')
    return t


def repair_done(session,user,ident,version,total,mode,reason,materials,fault='',fix=''):
    with _transaction(session):
        t=_owned(session,user,ident,version)
        held_machine(session,t)
        if mode not in ('FULL','FREE'):
            raise BusinessError('BAD_COLLECTION','Chọn Có thu tiền hoặc Miễn phí khi sửa xong.')
        technician_work.apply_report(session,t,fault,fix)
        from .attachments import has_visible
        if not has_visible(session,'service_ticket',t.id,'SAU'):
            raise BusinessError('PHOTO_REQUIRED','Cần chụp ảnh nghiệm thu sau sửa.')
        body,_=technician_work.charges(session,user,t,version,total,mode,reason,materials)
        service_charges.set_charges_in_tx(session,user,t,body)
        service_charges.complete(session,user,t)
        t.repaired_by=user.id
        tickets.transition_in_tx(session,user,t,'TAM_DUNG','Sửa xong, chờ trả máy',hold_reason=READY,_action='repair_done')
    return t


def schedule_return(session,user,ident,version,appointment,slot,assignee_id,note=''):
    tickets._require(user,tickets.DISPATCHERS,'Chỉ điều phối hoặc giám đốc được hẹn trả máy.')
    with _transaction(session):
        t=part_requests._ticket(session,ident);tickets._check_version(t,version)
        if t.status!='TAM_DUNG' or t.hold_reason!=READY or not service_charges.return_locked(t):
            raise BusinessError('BAD_STATUS','Máy chưa ở bước sửa xong chờ trả.',status=409)
        eq=held_machine(session,t)
        tickets._technician(session,assignee_id)
        tickets.transition_in_tx(session,user,t,'DA_HEN',note,appointment,appointment_slot=slot)
        if t.assignee_id!=assignee_id:
            tickets.assign_in_tx(session,user,t.id,assignee_id,None,note)
        if eq.custody_location!='THO_GIU' or eq.custody_holder_id!=assignee_id:
            custody.handover_in_tx(session,user,t.id,CustodyHandover(version=eq.version,
                location='THO_GIU',holder_id=assignee_id))
    return t


def queue_query(now):
    """One shared SELECT for dispatch's queue and the R2 overdue section."""
    from datetime import timedelta
    from sqlalchemy import select, func, case, literal
    from sqlalchemy.orm import aliased
    from .models import ServiceTicket, ServiceStep, Equipment, EquipmentCustody, Customer, Product
    t=ServiceTicket
    current=aliased(EquipmentCustody)
    last=select(EquipmentCustody.id).where(EquipmentCustody.ticket_id==t.id,
        EquipmentCustody.status=='DA_NHAN').order_by(EquipmentCustody.id.desc()).limit(1).correlate(t).scalar_subquery()
    since=func.coalesce(select(func.max(ServiceStep.at)).where(ServiceStep.ticket_id==t.id,
        ServiceStep.action=='workshop_handover').correlate(t).scalar_subquery(),t.created_at)
    place=Equipment.custody_location
    late=(t.promised_return_on<now.astimezone(tickets.service_sla.TZ).date()) | ((place=='DOI_TAC') & (current.expected_back<now.astimezone(tickets.service_sla.TZ).date())) | (t.quote_approved_at.is_(None) & (since<now-timedelta(days=2)))
    priced=select(ServiceLine.id).where(ServiceLine.ticket_id==t.id,ServiceLine.amount+ServiceLine.vat_amount>0).exists()
    stage=case((t.quote_approved_at.is_not(None),literal('Khách đã đồng ý · hẹn trả ')+func.coalesce(func.to_char(t.promised_return_on,'DD/MM'),'')),
        (priced,'Chờ khách duyệt'),else_='Chờ báo giá')
    return select(t.id,t.number,t.status,Customer.name.label('customer'),func.coalesce(t.total,0).label('amount'),
        func.coalesce(late,False).label('overdue'),Equipment.serial.label('workshop_serial'),Product.name.label('workshop_machine'),
        place.label('workshop_location'),current.partner_name.label('workshop_partner'),stage.label('workshop_stage'),
        func.greatest(0,func.floor(func.extract('epoch',now-since)/86400)).label('workshop_days')).select_from(t).join(Customer,Customer.code==t.customer_code).join(
        Equipment,Equipment.serial==t.serial).join(Product,Product.code==Equipment.product_code).outerjoin(current,current.id==last).where(
        t.status=='TAM_DUNG',t.hold_reason==WAITING).order_by(func.coalesce(late,False).desc(),since,t.id)

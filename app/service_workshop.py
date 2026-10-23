"""MV1: one dossier from onsite intake through workshop repair and return."""
from . import equipment_custody as custody, part_requests, service_charges, service_tickets as tickets, technician_work
from .errors import BusinessError
from .models import ServiceLine
from .schemas import CustodyHandover
from .security import has_role
from .stock_docs import _transaction

READY = 'SUA_XONG_CHO_TRA'


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


def bring(session,user,ident,version,intake,materials):
    with _transaction(session):
        t=_owned(session,user,ident,version)
        if t.kind not in ('SUA_TAI_CHO','BAO_HANH') or service_charges.return_locked(t):
            raise BusinessError('BAD_STATUS','Chỉ mang về từ phiếu sửa tại chỗ hoặc bảo hành đang sửa.',status=409)
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
        if t.kind=='SUA_TAI_CHO':t.kind='MANG_VE'
        t.version+=1
        tickets._record(session,user,t,'workshop',before,'DANG_LAM','Mang máy về xưởng')
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

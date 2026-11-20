"""MV1: one dossier from onsite intake through workshop repair and return."""
from . import equipment, equipment_custody as custody, part_requests, service_charges, service_tickets as tickets, technician_work
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


def _waiting(session,user,ident,version,quote=False):
    tickets._require(user,tickets.DISPATCHERS,'Chỉ điều phối hoặc giám đốc được xử lý sửa ở xưởng.')
    if quote and not tickets.can_quote(user):
        raise BusinessError('FORBIDDEN','Chỉ kinh doanh hoặc giám đốc ghi khách đồng ý hay không sửa.',status=403)
    t=part_requests._ticket(session,ident)
    tickets._check_version(t,version)
    if t.status!='TAM_DUNG' or t.hold_reason!=WAITING or service_charges.return_locked(t):
        raise BusinessError('BAD_STATUS','Phiếu không ở bước chờ sửa tại xưởng.',status=409)
    return t


def warranty_free(t):
    return t.billing=='MIEN_PHI' and t.warranty_basis in ('PHIEU_TRUOC','MAY_CON_HAN')


def repairers(session):
    """Thợ đang làm, ít việc mở nhất lên đầu: gợi ý người sửa khi khách đồng ý (TUẤN 17/11/2026)."""
    from sqlalchemy import func, select
    from .models import ServiceTicket, User
    load=dict(session.execute(select(ServiceTicket.assignee_id,func.count()).where(ServiceTicket.status.in_(tickets.OPEN),
        ServiceTicket.assignee_id.is_not(None)).group_by(ServiceTicket.assignee_id)).all())
    techs=[u for u in session.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name)) if tickets.TECHNICIAN in u.roles]
    return sorted(((u,load.get(u.id,0)) for u in techs),key=lambda x:(x[1],x[0].full_name))


def quote_approve(session,user,ident,version,promised_return_on,note='',repairer_id=None):
    from datetime import date, datetime
    with _transaction(session):
        t=_waiting(session,user,ident,version,quote=True)
        if t.declined_at:raise BusinessError('BAD_STATUS','Khách đã từ chối sửa.',status=409)
        if not isinstance(promised_return_on,date) or isinstance(promised_return_on,datetime) or promised_return_on<tickets._now().astimezone(tickets.service_sla.TZ).date():
            raise BusinessError('BAD_APPOINTMENT','Chọn ngày hẹn trả từ hôm nay trở đi.')
        if not warranty_free(t) and not any(x.amount+x.vat_amount>0 for x in t.lines):
            raise BusinessError('EMPTY_CHARGES','Báo giá chưa có dòng phí hoặc vật tư có giá.')
        before=tickets.snapshot(t)
        again=t.quote_approved_at is not None  # chỉ đổi ngày hẹn trả: không làm mới bước thợ đã báo
        t.quote_approved_at,t.quote_approved_by=tickets._now(),user.id
        t.promised_return_on=promised_return_on
        if repairer_id:  # giao thợ sửa luôn: thợ thấy phiếu trên màn của mình
            tech=tickets._technician(session,repairer_id)
            t.assignee_id=tech.id
            note=(note+' · ' if note else '')+'Giao thợ sửa: '+tech.full_name
        from .repair_quotes import close_open
        close_open(session,t.id,'DONG_Y')  # đợt 4: lần báo giá đang chờ khách đóng theo
        tickets._record(session,user,t,'quote_reschedule' if again else 'quote_approve',before,t.status,note)
    return t


# Đổi thợ sửa máy ở xưởng (TUẤN duyệt mẫu PV1anDADorn327t1Wdjt77 20/11/2026): sau khi khách đồng ý, điều phối/giám đốc
# giao hoặc đổi thợ sửa; phiếu vẫn ở xưởng (TAM_DUNG/CHO_SUA_XUONG), hàng đã cấp đi theo thợ mới, không đổi khi thợ đã báo kết quả.
CHANGE_REASONS=('Thợ bận việc khác','Thợ nghỉ','Cần thợ rành máy này','Khác')


def can_change_repairer(session,user,t):
    return (has_role(user,*tickets.DISPATCHERS) and t.status=='TAM_DUNG' and t.hold_reason==WAITING and t.declined_at is None
            and not service_charges.return_locked(t) and (t.quote_approved_at is not None or warranty_free(t))
            and t.id not in reported(session,[t.id]) and t.id not in failed(session,[t.id]))


def change_repairer(session,user,ident,version,assignee_id,reason='',note=''):
    """Trả (phiếu, id thợ cũ hoặc None)."""
    note=' '.join(str(note or '').split())[:500]
    with _transaction(session):
        t=_waiting(session,user,ident,version)
        if t.declined_at is not None or (t.quote_approved_at is None and not warranty_free(t)):
            raise BusinessError('BAD_STATUS','Khách chưa đồng ý sửa, chưa giao thợ sửa được.',status=409)
        if t.id in reported(session,[t.id]) or t.id in failed(session,[t.id]):
            raise BusinessError('ALREADY_REPORTED','Thợ đã báo kết quả sửa, không đổi thợ được nữa. Kho chốt tiếp.',status=409)
        if assignee_id is None:raise BusinessError('BAD_ASSIGNEE','Chọn thợ sửa.')
        tech=tickets._technician(session,assignee_id)
        old_id=t.assignee_id
        if old_id==tech.id:raise BusinessError('SAME_ASSIGNEE','Máy đang giao cho thợ này rồi.',status=409)
        if old_id is not None:
            if reason not in CHANGE_REASONS:raise BusinessError('REASON_REQUIRED','Chọn lý do đổi thợ sửa.')
            if reason=='Khác' and len(note)<3:raise BusinessError('REASON_REQUIRED','Lý do Khác: ghi rõ vì sao.')
        before=tickets.snapshot(t)
        moved=''
        if old_id is not None:
            from . import technician_reassign
            moved=technician_reassign.describe(session,*technician_reassign.move_ticket_stock(session,user,t,tech.id))
        from .models import User
        old=session.get(User,old_id) if old_id else None
        t.assignee_id=tech.id
        head=f'Đổi thợ sửa: {old.full_name if old else "?"} → {tech.full_name}' if old_id else f'Giao thợ sửa: {tech.full_name}'
        text=' · '.join(x for x in (head,reason if old_id else '',note,moved) if x)
        tickets._record(session,user,t,'reassign' if old_id else 'assign',before,t.status,text[:2000])
    return t,old_id


def notify_repairer(ticket_id,old_id=None):
    """Chạy nền sau khi đổi/giao thợ sửa: Zalo cho thợ mới (và thợ cũ). Zalo lỗi vẫn ghi ở Tin nhắn Zalo, không làm hỏng việc đổi."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from . import zalo_bot
    from .config import get_settings
    from .db import engine
    from .dispatch_alerts import base_url
    from .models import Customer, ServiceStep, ServiceTicket, User, ZaloChat
    if not get_settings().zalo_bot_token.strip():
        return 0
    sent=0
    with Session(engine()) as session:
        t=session.get(ServiceTicket,ticket_id)
        if t is None or t.assignee_id is None:return 0
        step=session.scalar(select(ServiceStep).where(ServiceStep.ticket_id==t.id,ServiceStep.action.in_(('assign','reassign')))
                            .order_by(ServiceStep.id.desc()).limit(1))
        customer=session.get(Customer,t.customer_code)
        new=session.get(User,t.assignee_id)
        from .repair_inspection import machine_name
        machine=machine_name(session,t) or t.serial or 'máy'
        line=f'{t.number} · {machine} · {customer.name if customer else t.customer_code}'
        due=f'Hẹn trả khách: {t.promised_return_on.strftime("%d/%m")}' if t.promised_return_on else ''
        why=(step.note if step else '')[:300]
        bodies={t.assignee_id:'\n'.join(x for x in ('Anh/chị được giao sửa máy ở xưởng',line,due,why,f'{base_url()}/tho/phieu/{t.id}#sua-xuong') if x)}
        if old_id and old_id!=t.assignee_id:
            bodies[old_id]='\n'.join(('Máy ở xưởng đã chuyển cho '+(new.full_name if new else 'thợ khác')+' sửa',line,why))
        for uid,body in bodies.items():
            chat=session.scalar(select(ZaloChat).join(User,User.id==ZaloChat.user_id).where(ZaloChat.user_id==uid,User.active.is_(True),
                                ZaloChat.chat_type=='PRIVATE').order_by(ZaloChat.id).limit(1))
            if chat is None:continue
            zalo_bot.send_text(session,chat,body,None,'thong_bao')
            sent+=1
    return sent


def _ready(session,user,t,action,before,note):
    t.status,t.hold_reason='TAM_DUNG',READY
    t.due_at=tickets.service_sla.due_at(t.status,t.priority,tickets._now(),t.appointment_at,t.appointment_slot)
    tickets._record(session,user,t,action,before,'TAM_DUNG',note)


def quote_decline(session,user,ident,version,mode,fee,reason):
    from .config import DEFAULT_VAT_RATE
    from .schemas import ServiceCharges
    reason=(reason or '').strip()
    with _transaction(session):
        t=_waiting(session,user,ident,version,quote=True)
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
        from .repair_quotes import close_open
        close_open(session,t.id,'KHONG_SUA')
        t.repaired_by=None
        t.warranty_months,t.warranty_scope=0,''
        _ready(session,user,t,'quote_decline',before,reason)
    return t


# Thợ sửa ở xưởng có 3 nút (TUẤN duyệt 17/11/2026): sửa xong (kèm vật tư phát sinh không tính khách), phát sinh cần báo lại khách,
# không sửa được. Bước khách đồng ý (lần mới) hoặc sửa xong làm mới lại.
WORK_STEPS=('repair_reported','repair_failed','repair_extra','repair_done','quote_approve')
FAIL_REASONS=('Hỏng nặng, không đáng sửa','Không có linh kiện','Ngoài khả năng','Khác')


def tech_state(session,ids):
    """{ticket_id: ServiceStep} lần thợ báo gần nhất còn hiệu lực (sửa xong / không sửa được / phát sinh)."""
    from sqlalchemy import select
    from .models import ServiceStep
    if not ids:return {}
    last={}
    for x in session.scalars(select(ServiceStep).where(ServiceStep.ticket_id.in_(list(ids)),
            ServiceStep.action.in_(WORK_STEPS)).order_by(ServiceStep.id)):
        last[x.ticket_id]=x
    return {k:v for k,v in last.items() if v.action in ('repair_reported','repair_failed','repair_extra')}


def reported(session,ids):
    """{ticket_id: ServiceStep} thợ báo sửa xong, chờ kho chốt (đợt 2, TUẤN 17/11/2026)."""
    return {k:v for k,v in tech_state(session,ids).items() if v.action=='repair_reported'}


def failed(session,ids):
    """{ticket_id: ServiceStep} thợ báo không sửa được, chờ kinh doanh báo khách."""
    return {k:v for k,v in tech_state(session,ids).items() if v.action=='repair_failed'}


def can_report(user,t):
    return (tickets.TECHNICIAN in user.roles and t.assignee_id==user.id and t.status=='TAM_DUNG' and t.hold_reason==WAITING
            and t.declined_at is None and (t.quote_approved_at is not None or warranty_free(t)))


def _repairing(session,user,ident,version):
    t=part_requests._ticket(session,ident)
    tickets._check_version(t,version)
    if tickets.TECHNICIAN not in user.roles or t.assignee_id!=user.id:
        raise BusinessError('FORBIDDEN','Chỉ thợ được giao sửa máy này báo kết quả.',status=403)
    if not can_report(user,t):
        raise BusinessError('BAD_STATUS','Máy chưa ở bước sửa (khách chưa đồng ý hoặc đã chốt).',status=409)
    state=tech_state(session,[t.id]).get(t.id)
    if state is not None and state.action in ('repair_reported','repair_failed'):
        raise BusinessError('ALREADY_REPORTED','Đã báo kết quả sửa, chờ xử lý tiếp.',status=409)
    return t


def extra_materials(session,user,t,extras):
    """Vật tư phát sinh không tính khách: dòng VAT_TU giá 0 trên phiếu; kho trừ khi chốt Sửa xong, giá vốn vào lãi phiếu,
    giá khách không đổi nên khách không phải đồng ý lại."""
    from .config import DEFAULT_VAT_RATE
    from .models import Product, Warehouse
    from sqlalchemy import select
    rows=[]
    for code,wh,qty in extras:
        code,wh=str(code or '').strip().upper(),str(wh or '').strip()
        if not code:continue
        try:qty=int(qty)
        except (TypeError,ValueError):qty=0
        if qty<=0:raise BusinessError('BAD_QTY',f'{code}: số lượng phải lớn hơn 0.')
        p=session.get(Product,code)
        if p is None or not p.active:raise BusinessError('BAD_PRODUCT',f'Không có mã hàng {code}.')
        w=session.get(Warehouse,wh)
        own=session.scalar(select(Warehouse).where(Warehouse.kind=='THO',Warehouse.holder_id==user.id,Warehouse.active.is_(True)))
        if w is None or not w.active or not (w.kind=='KHO' or (own is not None and w.code==own.code)):
            raise BusinessError('BAD_WAREHOUSE','Chọn kho công ty hoặc kho của bạn.')
        rows.append((p,w.code,qty))
    if not rows:return []
    start=max((x.line_no for x in t.lines),default=0)+1
    added=[ServiceLine(line_no=start+i,kind='VAT_TU',product_code=p.code,warehouse_code=wh,
                       description=f'{p.name} · phát sinh, không tính khách'[:500],qty=qty,unit_price=0,
                       vat_rate=DEFAULT_VAT_RATE,amount=0,vat_amount=0) for i,(p,wh,qty) in enumerate(rows)]
    t.lines.extend(added)
    t.version+=1
    return added


def report_repaired(session,user,ident,version,note,extras=()):
    """Thợ sửa ở xưởng báo sửa xong: ảnh sau sửa + đã sửa gì (+ vật tư phát sinh không tính khách).
    Không đổi trạng thái, không thu tiền; kho chốt Sửa xong (trừ kho, bảo hành) rồi giao thợ trả."""
    from .attachments import has_visible
    note=' '.join(str(note or '').split())[:2000]
    with _transaction(session):
        t=_repairing(session,user,ident,version)
        if len(note)<3:raise BusinessError('FIX_REQUIRED','Ghi đã sửa gì.')
        if not has_visible(session,'service_ticket',t.id,'SAU'):
            raise BusinessError('PHOTO_REQUIRED','Cần chụp ảnh máy sau sửa.')
        before=tickets.snapshot(t)
        added=extra_materials(session,user,t,extras)
        if added:
            note+=' · Vật tư phát sinh: '+', '.join(f'{x.product_code} × {x.qty}' for x in added)
        tickets._record(session,user,t,'repair_reported',before,t.status,note[:2000])
    return t


def report_extra(session,user,ident,version,note):
    """Phát sinh, cần báo lại khách: bỏ khách đồng ý, máy về kho kiểm tra lại (thêm gói/linh kiện), kinh doanh gửi báo giá lần sau."""
    from .models import RepairInspection
    note=' '.join(str(note or '').split())[:1000]
    with _transaction(session):
        t=_repairing(session,user,ident,version)
        if len(note)<3:raise BusinessError('NOTE_REQUIRED','Ghi cần thêm gì, vì sao.')
        if t.quote_approved_at is None:
            raise BusinessError('WARRANTY_FREE','Máy bảo hành miễn phí: báo kho xử lý phần phát sinh.',status=409)
        row=session.get(RepairInspection,t.id,with_for_update=True)
        if row is None:raise BusinessError('NO_INSPECTION','Máy chưa có kết quả kiểm tra.',status=409)
        before=tickets.snapshot(t)
        t.quote_approved_at=t.quote_approved_by=None
        t.version+=1
        row.recheck_reason=('Phát sinh khi sửa: '+note)[:1000]
        row.applied_at=row.applied_by=None
        tickets._record(session,user,t,'repair_extra',before,t.status,note)
    return t


def report_failed(session,user,ident,version,reason,note=''):
    """Không sửa được: ghi lý do; kinh doanh/giám đốc báo khách rồi bấm Khách không sửa (phí kiểm tra hoặc miễn phí)."""
    note=' '.join(str(note or '').split())[:1000]
    with _transaction(session):
        t=_repairing(session,user,ident,version)
        if reason not in FAIL_REASONS:raise BusinessError('REASON_REQUIRED','Chọn lý do không sửa được.')
        if reason=='Khác' and len(note)<3:raise BusinessError('REASON_REQUIRED','Lý do Khác: ghi rõ vì sao.')
        before=tickets.snapshot(t)
        tickets._record(session,user,t,'repair_failed',before,t.status,reason+(' · '+note if note else ''))
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
    # Chỉ phiếu đã chốt ở xưởng (Sửa xong hoặc Khách không sửa). Sửa tại chỗ giờ cũng ghi tháng bảo hành (TUẤN 24/10)
    # nên không dựa vào warranty_months, nếu không phiếu sửa tại chỗ bị đòi ảnh trả máy và không hoàn thành được.
    from .service_charges import return_locked
    if return_locked(t):
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
        return _bring_in_tx(session,user,ident,version,intake,materials,to_workshop)


def _bring_in_tx(session,user,ident,version,intake,materials,to_workshop=False):
    """Same MV1/XG1 body; caller holds the transaction for one machine or a trip."""
    t=_owned(session,user,ident,version)
    if t.kind not in (None,'SUA_TAI_CHO','BAO_HANH','MANG_VE') or service_charges.return_locked(t):
        raise BusinessError('BAD_STATUS','Chỉ mang về từ phiếu đang làm, máy còn ở chỗ khách.',status=409)
    before=tickets.snapshot(t)
    custody.receive_in_tx(session,user,t.id,intake)
    if materials is not None:
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


def bring_trip(session,user,ident,version,items,materials):
    from sqlalchemy import select
    from .models import ServiceTicket, Equipment
    from .schemas import WorkshopTripItem, CustodyIntake
    with _transaction(session):
        root=_owned(session,user,ident,version)
        parts=[x if isinstance(x,WorkshopTripItem) else WorkshopTripItem.model_validate(x) for x in items]
        ids=[x.ticket_id for x in parts]
        if len(ids)!=len(set(ids)) or ident not in ids:
            raise BusinessError('BAD_TRIP','Danh sách nhận máy phải có phiếu đang mở và không trùng phiếu.')
        # TUẤN 13/11/2026: chuyến gồm phiếu đang mở và các máy cùng chỗ thợ tick "Mang về"; máy sửa xong tại chỗ ở lại.
        others=[t.id for t in tickets.family(session,root) if t.id!=root.id]
        children=list(session.scalars(select(ServiceTicket).where(ServiceTicket.id.in_(others))
            .order_by(ServiceTicket.id).with_for_update().execution_options(populate_existing=True))) if others else []
        family={root.id:root,**{t.id:t for t in children}}
        if set(ids)-family.keys():raise BusinessError('BAD_TRIP','Chỉ nhận máy trên các phiếu cùng chỗ.',status=409)
        eligible={root.id}
        for child in children:
            eq=session.scalar(select(Equipment).where(Equipment.serial==child.serial).execution_options(populate_existing=True)) if child.serial else None
            if child.assignee_id==root.assignee_id and child.status=='DANG_LAM' and eq is not None and eq.custody_location=='TAI_KHACH' \
                    and child.kind in (None,'SUA_TAI_CHO','BAO_HANH','MANG_VE') and not service_charges.return_locked(child):
                eligible.add(child.id)
        if set(ids)-eligible:
            raise BusinessError('BAD_TRIP','Chỉ mang về máy còn ở khách, phiếu đang làm và cùng thợ.',status=409)
        by_id={x.ticket_id:x for x in parts}
        # All ticket locks precede machine locks, in deterministic order.
        machines={serial:custody._machine(session,serial,lock=True) for serial in sorted({family[n].serial for n in ids if family[n].serial})}
        for n in sorted(ids):
            ticket=family[n];part=by_id[n]
            tickets._check_version(ticket,part.version)
            if not ticket.serial:raise BusinessError('SERIAL_REQUIRED','Chọn máy trước khi mang về.')
            custody._version(machines[ticket.serial],part.machine_version)
        result=[]
        for n in [ident]+sorted(set(ids)-{ident}):
            part=by_id[n]
            intake=CustodyIntake(version=part.machine_version,accessories=part.accessories,
                accessories_note=part.accessories_note,condition_note=part.condition_note)
            result.append(_bring_in_tx(session,user,n,part.version,intake,materials if n==ident else None,to_workshop=True))
    return result


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
        func.coalesce(late,False).label('overdue'),Equipment.serial.label('workshop_serial'),equipment.name_column().label('workshop_machine'),
        place.label('workshop_location'),current.partner_name.label('workshop_partner'),stage.label('workshop_stage'),
        func.greatest(0,func.floor(func.extract('epoch',now-since)/86400)).label('workshop_days')).select_from(t).join(Customer,Customer.code==t.customer_code).join(
        Equipment,Equipment.serial==t.serial).outerjoin(Product,Product.code==Equipment.product_code).outerjoin(current,current.id==last).where(
        t.status=='TAM_DUNG',t.hold_reason==WAITING).order_by(func.coalesce(late,False).desc(),since,t.id)

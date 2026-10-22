"""UX1d: hàng công ty hư giữ giá vốn, kho nhận mới ghi CK, atomic và phân quyền."""
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import attachments, my_work, stock_posting, technician_returns as svc
from app.errors import BusinessError
from app.models import (Attachment, FundLedger, StockBalance, StockDoc, StockLedger, TechnicianReturn, TechnicianReturnLine, Warehouse)
from test_my_work import people,purchase_people,ticket,Page
from test_technician_stock import held,balance,transfer
from test_ux1c_hen import supplied,state
from test_web import service_login,d5_image


def error(code,fn,*args,**kwargs):
    with pytest.raises(BusinessError) as exc:fn(*args,**kwargs)
    assert exc.value.code==code
    return exc.value


def defect(db,active=True):
    row=Warehouse(code='K900',name='Kho hàng lỗi',kind='LOI',active=active);db.add(row);db.commit();return row


def split(reason='rơi vỡ'):
    return [dict(product_code='TD-0001',qty=1,to_warehouse_code='K58',condition='TOT'),
        dict(product_code='TD-0001',qty=1,to_warehouse_code='K58',condition='HU_HONG',damage_reason=reason)]


def closed(db,setup):
    p,own,t=setup;svc.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa',split())
    return p,own,t,db.scalars(select(TechnicianReturn)).one()


def test_split_receipt_two_ck_and_defect_keeps_existing_cost(db,supplied):
    bad=defect(db);p,own,t,row=closed(db,supplied)
    assert len(row.lines)==2 and {l.condition for l in row.lines}=={'TOT','HU_HONG'}
    assert row.status=='CHO_NHAN' and balance(db,own.code)==2
    source_cost=db.get(StockBalance,(own.code,'TD-0001')).avg_cost
    svc.receive(db,p['warehouse'],row.id,row.version,{line.line_no:1 for line in row.lines})
    assert row.status=='DA_NHAN' and row.stock_doc_id and row.defect_stock_doc_id
    good_doc=db.get(StockDoc,row.stock_doc_id);bad_doc=db.get(StockDoc,row.defect_stock_doc_id)
    assert good_doc.status==bad_doc.status=='DA_GHI' and good_doc.kind==bad_doc.kind=='CK'
    assert good_doc.to_warehouse_code=='K58' and bad_doc.to_warehouse_code==bad.code
    assert balance(db,own.code)==0 and balance(db,'K58')==9 and balance(db,bad.code)==1
    posted=db.scalar(select(StockLedger).where(StockLedger.doc_id==bad_doc.id,StockLedger.warehouse_code==bad.code))
    assert posted.unit_cost==source_cost==1000
    assert db.get(StockBalance,(bad.code,'TD-0001')).value==1000 and db.query(FundLedger).count()==0


@pytest.mark.parametrize('reason',['','x','  '])
def test_missing_damage_reason_changes_nothing(db,supplied,reason):
    p,own,t=supplied;before=state(db,t)
    error('DAMAGE_REASON_REQUIRED',svc.cancel_by_technician,db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa',split(reason))
    assert state(db,t)==before and balance(db,own.code)==2


@pytest.mark.parametrize('inactive',[False,True])
def test_no_active_defect_warehouse_rolls_back_even_good_line(db,supplied,inactive):
    if inactive:defect(db,active=False)
    p,own,t,row=closed(db,supplied);before=state(db,t)
    error('NO_DEFECT_WAREHOUSE',svc.receive,db,p['warehouse'],row.id,row.version,{})
    assert state(db,t)==before and balance(db,own.code)==2
    assert row.stock_doc_id is None and row.defect_stock_doc_id is None and row.status=='CHO_NHAN'


def test_failure_second_ck_rolls_back_both(db,supplied,monkeypatch):
    bad=defect(db);p,own,t,row=closed(db,supplied);before=state(db,t);post=stock_posting.post_in_tx;calls=[]
    def fail(session,user,ident,version,**kwargs):
        calls.append(ident)
        if len(calls)==2:raise BusinessError('TEST_FAILURE','Lỗi ghi CK thứ hai')
        return post(session,user,ident,version,**kwargs)
    monkeypatch.setattr(stock_posting,'post_in_tx',fail)
    error('TEST_FAILURE',svc.receive,db,p['warehouse'],row.id,row.version,{})
    assert len(calls)==2 and state(db,t)==before and balance(db,own.code)==2 and balance(db,bad.code)==0


def test_standalone_from_held_screen_and_over_stock(client,db,held):
    p,own,_=held;transfer(db,p,qty=2);csrf=service_login(client,p['technician'])
    assert 'Trả kho / Báo hư' in client.get('/hang-toi-giu').text
    data=dict(csrf=csrf,return_product='TD-0001',return_qty='3',return_warehouse='K58',return_condition_0='HU_HONG',return_damage_reason_0='rơi vỡ')
    response=client.post('/tho-tra-hang/moi',data=data)
    assert response.status_code==409 and 'BAD_RETURN_QTY' in response.text and db.query(TechnicianReturn).count()==0
    data['return_qty']='1';response=client.post('/tho-tra-hang/moi',data=data,follow_redirects=False)
    assert response.status_code==303
    row=db.scalars(select(TechnicianReturn)).one()
    assert row.ticket_id is None and row.technician_id==p['technician'].id and row.lines[0].condition=='HU_HONG'
    assert balance(db,own.code)==2
    groups={g['key']:g for g in my_work.collect(db,p['warehouse'])}
    assert groups['technician_returns']['count']==1 and groups['technician_returns']['rows'][0]['damaged']
    service_login(client,p['warehouse']);page=client.get('/viec-cua-toi?nhom=technician_returns')
    assert row.number in page.text and 'Hư hỏng' in page.text


def test_receive_correction_to_good_needs_note_and_preserves_report(db,supplied):
    p,own,t,row=closed(db,supplied);damaged=next(l for l in row.lines if l.condition=='HU_HONG');before=state(db,t)
    error('REASON_REQUIRED',svc.receive,db,p['warehouse'],row.id,row.version,{},'',{damaged.line_no:'TOT'})
    assert state(db,t)==before
    svc.receive(db,p['warehouse'],row.id,row.version,{},'Kiểm tra thực tế còn tốt',{damaged.line_no:'TOT'})
    assert damaged.condition=='HU_HONG' and damaged.received_condition=='TOT'
    assert row.stock_doc_id and row.defect_stock_doc_id is None
    assert balance(db,own.code)==0 and balance(db,'K58')==10
    assert db.get(StockDoc,row.stock_doc_id).lines[0].qty==2


def test_receive_correction_to_damaged_requires_reason_and_note(db,supplied):
    bad=defect(db);p,own,t=supplied
    svc.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa',split()[:1]);row=db.scalars(select(TechnicianReturn)).one();line=row.lines[0]
    error('REASON_REQUIRED',svc.receive,db,p['warehouse'],row.id,row.version,{},'',{line.line_no:'HU_HONG'},{line.line_no:'vỡ hộp'})
    svc.receive(db,p['warehouse'],row.id,row.version,{},'Kho phát hiện vỡ hộp',{line.line_no:'HU_HONG'},{line.line_no:'vỡ hộp'})
    assert line.condition=='TOT' and line.received_condition=='HU_HONG' and line.received_damage_reason=='vỡ hộp'
    assert row.stock_doc_id is None and row.defect_stock_doc_id and balance(db,bad.code)==1


def test_cancel_own_only_reason_and_no_ck(client,db,held):
    p,own,_=held;transfer(db,p,qty=2);row=svc.create(db,p['technician'],split())[0]
    count=db.query(StockDoc).count()
    csrf=service_login(client,p['other_tech'])
    response=client.post(f'/tho-tra-hang/{row.id}/huy',data=dict(csrf=csrf,version=row.version,reason='Không đồng ý'))
    assert response.status_code==403 and row.status=='CHO_NHAN'
    csrf=service_login(client,p['technician']);response=client.post(f'/tho-tra-hang/{row.id}/huy',data=dict(csrf=csrf,version=row.version,reason=''))
    assert response.status_code==400 and 'REASON_REQUIRED' in response.text
    response=client.post(f'/tho-tra-hang/{row.id}/huy',data=dict(csrf=csrf,version=row.version,reason='Giữ lại hàng để kiểm tra'),follow_redirects=False)
    assert response.status_code==303
    db.expire_all();db.refresh(row)
    assert row.status=='DA_HUY' and row.void_reason and row.stock_doc_id is None and row.defect_stock_doc_id is None
    assert balance(db,own.code)==2 and db.query(StockDoc).count()==count


def test_photo_per_line_access_and_defect_source(client,db,held):
    bad=defect(db);p,own,_=held;transfer(db,p,qty=2);csrf=service_login(client,p['technician'])
    data=dict(csrf=csrf,return_product=['TD-0001','TD-0001'],return_qty=['1','1'],return_warehouse=['K58','K58'],
        return_condition_0='TOT',return_condition_1='HU_HONG',return_damage_reason_1='rơi vỡ')
    response=client.post('/tho-tra-hang/moi',data=data,files={'return_photo_1':('hu.png',d5_image(),'image/png')},follow_redirects=False)
    assert response.status_code==303
    row=db.scalars(select(TechnicianReturn)).one();line=next(l for l in row.lines if l.condition=='HU_HONG')
    photo=db.scalars(select(Attachment).where(Attachment.entity=='technician_return_line')).one()
    assert photo.entity_id==f'{row.id}:{line.line_no}' and photo.content_type=='image/jpeg'
    service_login(client,p['other_tech']);assert client.get(f'/attachments/{photo.id}/file').status_code==403
    csrf=service_login(client,p['warehouse']);page=client.get(f'/tho-tra-hang/{row.id}')
    assert f'/attachments/{photo.id}/file' in page.text and 'rơi vỡ' in page.text
    assert client.get(f'/attachments/{photo.id}/file').status_code==200
    bucket=client.get('/viec-cua-toi?nhom=technician_returns')
    assert 'rơi vỡ' in bucket.text and f'/attachments/{photo.id}/file' in bucket.text
    form=Page(page.text).forms['return-receive'];response=client.post(form['action'],data={**form['fields'],**{f'qty_line_{l.line_no}':'1' for l in row.lines}},follow_redirects=False)
    assert response.status_code==303
    page=client.get('/ton-kho?kho='+bad.code)
    assert 'Thợ báo hư' in page.text and p['technician'].full_name in page.text and row.number in page.text and 'rơi vỡ' in page.text


def test_photo_rollback_removes_files_and_records(db,supplied,monkeypatch):
    from app.config import get_settings
    p,own,t=supplied;entries=split();entries[1]['photo']=dict(filename='hu.png',data=d5_image());before=state(db,t)
    root=Path(get_settings().files_dir);paths=set(root.rglob('*')) if root.exists() else set();original=attachments.add
    def fail(*args,**kwargs):
        result=original(*args,**kwargs)
        # Caller must have a chance to register this path before the outer failure.
        return result
    monkeypatch.setattr(attachments,'add',fail)
    create=svc.create_requests
    def after_photos(*args,**kwargs):
        create(*args,**kwargs);raise BusinessError('TEST_FAILURE','Lỗi sau ảnh')
    monkeypatch.setattr(svc,'create_requests',after_photos)
    error('TEST_FAILURE',svc.cancel_by_technician,db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa',entries)
    assert state(db,t)==before and db.query(Attachment).count()==0
    assert {path for path in root.rglob('*') if path.is_file()}=={path for path in paths if path.is_file()}


@pytest.mark.parametrize('received',[False,True])
def test_migration_legacy_tot_and_receives_normally(db,supplied,received):
    p,own,t=supplied;svc.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa')
    row=db.scalars(select(TechnicianReturn)).one()
    if received:svc.receive(db,p['warehouse'],row.id,row.version,{})
    spec=importlib.util.spec_from_file_location('ux1d_migration',Path('migrations/versions/0035_ux1d_bao_hang_hu.py'))
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    with db.begin_nested():
        with Operations.context(MigrationContext.configure(db.connection())):
            migration.downgrade();migration.upgrade()
        legacy=db.execute(text('select condition,damage_reason,received_condition from technician_return_lines where return_id=:id'),dict(id=row.id)).one()
        assert legacy==('TOT','', 'TOT' if received else None)
    if not received:
        svc.receive(db,p['warehouse'],row.id,row.version,{})
        assert row.status=='DA_NHAN' and balance(db,own.code)==0


def test_db_damage_constraints_and_default(db,supplied):
    p,_,t=supplied;svc.cancel_by_technician(db,p['technician'],t.id,t.version,'Khách không muốn sửa nữa');row=db.scalars(select(TechnicianReturn)).one()
    assert row.lines[0].condition=='TOT' and row.lines[0].damage_reason==''
    for sql in ("update technician_return_lines set condition='HU_HONG',damage_reason=''", "update technician_return_lines set damage_reason='hỏng' where condition='TOT'"):
        with pytest.raises(DBAPIError),db.begin_nested():db.execute(text(sql))

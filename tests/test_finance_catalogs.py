import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import finance_catalogs as svc, finance_mappings as mappings
from app.errors import BusinessError
from app.models import Fund, CashCategory, FinanceMapping, AuditLog
from app.schemas import FundIn, FundPatch, CashCategoryIn, CashCategoryPatch


@pytest.fixture
def finance_people(make_user):
    return {role:make_user('t1.'+role,role) for role in ('finance','director','sales')}


def fund(db,user,code='QUY-TM',kind='TIEN_MAT',**kw):
    return svc.create(db,user,Fund,FundIn(code=code,name='Quỹ Tiền mặt',kind=kind,**kw))


def category(db,user,code='THU-BH',direction='THU'):
    return svc.create(db,user,CashCategory,CashCategoryIn(code=code,name='Thu bán hàng',direction=direction))


def fails(code,fn,*args,**kwargs):
    with pytest.raises(BusinessError) as exc:
        fn(*args,**kwargs)
    assert exc.value.code==code
    return exc.value


def test_codes_duplicates_bank_account_and_folded_search(db,finance_people):
    f=finance_people['finance']
    row=fund(db,f,'qu y-tm')
    assert row.code=='QUY-TM' and row.version==1 and row.active
    assert svc.search(db,f,Fund,'tien mat')[0].code=='QUY-TM'
    fails('DUPLICATE_CODE',fund,db,f,'QUY-TM')
    fails('ACCOUNT_REQUIRED',fund,db,f,'TK-ACB','NGAN_HANG')
    assert db.query(Fund).count()==1
    bank=fund(db,f,'tk - acb','NGAN_HANG',account_no='0123456789',bank_name='Á Châu')
    assert bank.code=='TK-ACB' and svc.search(db,f,Fund,'a chau')[0].code=='TK-ACB'
    fails('BAD_CODE',fund,db,f,'a/b')
    assert db.query(AuditLog).filter(AuditLog.entity=='fund').count()==2


def test_category_search_duplicate_and_no_delete(db,finance_people):
    u=finance_people['finance'];row=category(db,u,'thu - bh')
    assert row.code=='THU-BH' and svc.search(db,u,CashCategory,'thu ban hang')[0].code==row.code
    fails('DUPLICATE_CODE',category,db,u,'THU-BH')
    updated=svc.update(db,u,CashCategory,row.code,CashCategoryPatch(version=1,active=False))
    assert not updated.active and updated.version==2 and db.query(CashCategory).count()==1
    assert len(svc.search(db,u,CashCategory,active=True))==0
    audits=db.scalars(select(AuditLog).where(AuditLog.entity=='cash_category').order_by(AuditLog.id)).all()
    assert [a.action for a in audits]==['cash_category.create','cash_category.update']
    assert audits[1].before['active'] is True and audits[1].after['active'] is False


def test_mapping_validation_require_and_fixed_keys(db,finance_people):
    f,d=finance_people['finance'],finance_people['director']
    cash=fund(db,f);bank=fund(db,f,'TK-ACB','NGAN_HANG',account_no='123')
    thu=category(db,f);chi=category(db,f,'CHI-KHAC','CHI')
    inactive=fund(db,f,'NGUNG',active=False)
    for key,field,code in [('THU_TIEN_MAT','fund_code',bank.code),('THU_CHUYEN_KHOAN','fund_code',cash.code),
                           ('DANH_MUC_BAN_HANG','category_code',chi.code),('THU_TIEN_MAT','fund_code',inactive.code),
                           ('THU_TIEN_MAT','category_code',thu.code),('DANH_MUC_DICH_VU','fund_code',cash.code),
                           ('THU_TIEN_MAT','fund_code','KHONG-CO')]:
        fails('BAD_MAPPING',mappings.set_mapping,db,d,key,version=1,**{field:code})
    fails('MAPPING_REQUIRED',mappings.require,db,'THU_TIEN_MAT')
    for key,field,code in [('THU_TIEN_MAT','fund_code',cash.code),('THU_CHUYEN_KHOAN','fund_code',bank.code),
                           ('DANH_MUC_BAN_HANG','category_code',thu.code),('DANH_MUC_DICH_VU','category_code',thu.code)]:
        row=mappings.set_mapping(db,d,key,version=1,**{field:code})
        assert mappings.require(db,key)==code and row.version==2 and row.updated_by==d.id
    fails('BAD_MAPPING',mappings.set_mapping,db,d,'KHONG-CO',version=1)
    fails('BAD_MAPPING',mappings.require,db,'KHONG-CO')
    assert len(mappings.search(db,f))==4


def test_in_use_preserves_mapping_validity_and_unmap(db,finance_people):
    f,d=finance_people['finance'],finance_people['director']
    cash=fund(db,f);thu=category(db,f)
    mappings.set_mapping(db,d,'THU_TIEN_MAT',fund_code=cash.code,version=1)
    mappings.set_mapping(db,d,'DANH_MUC_BAN_HANG',category_code=thu.code,version=1)
    fails('IN_USE',svc.update,db,f,Fund,cash.code,FundPatch(version=1,active=False))
    fails('IN_USE',svc.update,db,f,CashCategory,thu.code,CashCategoryPatch(version=1,active=False))
    fails('BAD_MAPPING',svc.update,db,f,Fund,cash.code,FundPatch(version=1,kind='NGAN_HANG',account_no='123'))
    fails('BAD_MAPPING',svc.update,db,f,CashCategory,thu.code,CashCategoryPatch(version=1,direction='CHI'))
    assert db.get(Fund,cash.code).active and db.get(Fund,cash.code).kind=='TIEN_MAT'
    assert db.get(CashCategory,thu.code).direction=='THU'
    mappings.set_mapping(db,d,'THU_TIEN_MAT',version=2)
    fails('MAPPING_REQUIRED',mappings.require,db,'THU_TIEN_MAT')
    svc.update(db,f,Fund,cash.code,FundPatch(version=1,active=False))
    assert db.query(Fund).count()==1


def test_versions_audit_all_changes_and_role_checks(db,finance_people):
    f,d,s=(finance_people[k] for k in ('finance','director','sales'))
    cash=fund(db,f)
    changed=svc.update(db,f,Fund,cash.code,FundPatch(version=1,note='Ghi mới'))
    assert changed.version==2
    fails('VERSION_CONFLICT',svc.update,db,f,Fund,cash.code,FundPatch(version=1,note='Sai'))
    m=mappings.set_mapping(db,d,'THU_TIEN_MAT',fund_code=cash.code,version=1)
    fails('VERSION_CONFLICT',mappings.set_mapping,db,d,m.key,fund_code=cash.code,version=1)
    fails('FORBIDDEN',mappings.set_mapping,db,f,m.key,fund_code=cash.code,version=2)
    fails('FORBIDDEN',svc.create,db,s,Fund,FundIn(code='C',name='C',kind='TIEN_MAT'))
    fails('FORBIDDEN',svc.search,db,s,Fund)
    fails('FORBIDDEN',svc.load,db,s,Fund,cash.code)
    fails('FORBIDDEN',mappings.search,db,s)
    logs=db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [a.action for a in logs]==['fund.create','fund.update','finance_mapping.update']
    assert logs[1].before['note']=='' and logs[1].after['note']=='Ghi mới'
    assert logs[2].before['fund_code'] is None and logs[2].after['fund_code']==cash.code
    assert db.get(Fund,cash.code).note=='Ghi mới'


def test_require_refreshes_mapping_changed_in_another_session(db,engine,finance_people):
    d=finance_people['director'];a=fund(db,d);b=fund(db,d,'TM-2')
    cached=mappings.set_mapping(db,d,'THU_TIEN_MAT',fund_code=a.code,version=1)
    assert cached.fund_code==a.code
    with Session(engine) as other:
        mappings.set_mapping(other,d,cached.key,fund_code=b.code,version=2)
    assert mappings.require(db,cached.key)==b.code


def test_actual_migration_seeds_only_four_empty_mappings_and_downgrades(engine,monkeypatch):
    file=Path(__file__).parents[1]/'migrations/versions/0014_t1_quy_danh_muc.py'
    spec=importlib.util.spec_from_file_location('t1_migration',file)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    assert migration.down_revision=='0013'
    with engine.begin() as conn:
        conn.execute(text('create schema t1_migration_test'))
        conn.execute(text('set local search_path to t1_migration_test, public'))
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        rows=conn.execute(text('select key,fund_code,category_code,updated_by,version from finance_mappings')).all()
        assert {r.key for r in rows}==set(mappings.KEYS) and len(rows)==4
        assert all(r.fund_code is None and r.category_code is None and r.updated_by is None and r.version==1 for r in rows)
        assert conn.scalar(text('select count(*) from funds'))==0
        assert conn.scalar(text('select count(*) from cash_categories'))==0
        migration.downgrade()
        assert conn.scalar(text("select count(*) from information_schema.tables where table_schema='t1_migration_test'"))==0
        conn.execute(text('drop schema t1_migration_test'))

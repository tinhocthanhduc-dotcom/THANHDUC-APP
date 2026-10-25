#!/usr/bin/env python3
"""Import current CARE warranties. Dry run unless --apply; never creates customers.

Model khớp đúng tên hoặc mã hàng đang bán → gắn mã đó; không khớp → thiết bị ngoài danh mục với tên máy tự do (model_name)."""
import argparse
import csv
from datetime import date, datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app import equipment
from app.db import engine
from sqlalchemy.orm import Session
from app.errors import BusinessError
from app.models import Customer, CustomerPhone, Equipment, Product, User
from app.schemas import EquipmentIn
from app.security import has_role
from app.text import fold, phone

ALIASES={
    'serial':('serial','so serial','so se ri','so seri','s/n'),
    'phone':('sdt','so dien thoai','dien thoai','phone'),
    'model':('model','hang/model','hang model','model may','ma hang'),
    'warranty_until':('ngay het han bao hanh','ngay het bao hanh','het han bao hanh','bao hanh den','warranty_until'),
    'care_ref':('ma phieu care','ma phieu','so phieu','care_ref'),
}


def read_csv(path):
    with open(path,encoding='utf-8-sig',newline='') as stream:
        sample=stream.read(4096);stream.seek(0)
        try:dialect=csv.Sniffer().sniff(sample,delimiters=',;\t')
        except csv.Error:dialect=csv.excel
        reader=csv.DictReader(stream,dialect=dialect)
        mapping={key:next((h for h in (reader.fieldnames or []) if fold(h) in names),None) for key,names in ALIASES.items()}
        if not mapping['serial'] or not mapping['warranty_until'] or not mapping['phone']:
            raise ValueError('CSV cần cột serial, SĐT và ngày hết hạn bảo hành.')
        return [{key:(row.get(header) or '').strip() if header else '' for key,header in mapping.items()} for row in reader]


def _date(raw):
    for fmt in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y'):
        try:return datetime.strptime(raw,fmt).date()
        except ValueError:pass
    raise ValueError('Ngày bảo hành không hợp lệ (dd/mm/yyyy hoặc yyyy-mm-dd).')


def import_rows(session,user,rows,*,apply=False,today=None):
    if not has_role(user,'director'):raise BusinessError('FORBIDDEN','Chỉ giám đốc nhập bảo hành CARE.',status=403)
    today=today or datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()
    report=[];seen=set()
    products=list(session.scalars(select(Product).where(Product.active.is_(True))))
    for n,record in enumerate(rows,2):
        result={'row':n,'serial':record.get('serial',''),'status':''};report.append(result)
        try:
            serial=equipment.normalize_serial(record.get('serial',''));result['serial']=serial
            if not serial:result['status']='BO_QUA_THIEU_SERIAL';continue
            if len(serial)>64:raise ValueError('Serial dài quá 64 ký tự.')
            expiry=_date(record.get('warranty_until',''))
            if expiry<today:result['status']='BO_QUA_HET_HAN';continue
            if serial in seen:result['status']='BO_QUA_TRUNG_SERIAL';continue
            seen.add(serial)
            normalized=phone(record.get('phone',''))
            customers=list(session.scalars(select(Customer).join(CustomerPhone).where(CustomerPhone.phone==normalized,Customer.status=='DANG_DUNG')))
            if len(customers)!=1:
                result['status']='KHONG_KHOP_KHACH' if not customers else 'TRUNG_SDT';continue
            customer=customers[0];result['customer_code']=customer.code
            eq=session.scalar(select(Equipment).where(Equipment.serial==serial).with_for_update())
            note='Nhập từ CARE '+record.get('care_ref','')
            if eq:
                if eq.warranty_until is not None:result['status']='DA_CO';continue
                if eq.customer_code and eq.customer_code!=customer.code:
                    result['status']='KHAC_KHACH';continue
                result['status']='CAP_NHAT';result['warranty_until']=expiry.isoformat()
                if apply:
                    before=equipment.snapshot(eq);eq.warranty_until=expiry
                    if note.strip() not in eq.note: eq.note=(eq.note+'\n'+note).strip()
                    session.flush()
                    from app import audit
                    audit.record(session,user,'equipment.care_import','equipment',serial,before,equipment.snapshot(eq))
            else:
                raw_model=' '.join(record.get('model','').split());model=fold(raw_model)
                matches=[p for p in products if model and model in (fold(p.name),fold(p.code))]
                if len(matches)==1:
                    product_code,model_name=matches[0].code,''
                else:
                    # Model không khớp danh mục → thiết bị ngoài danh mục, ghi tên máy tự do (không tạo mã chung).
                    product_code,model_name=None,(raw_model or 'Máy nhập từ CARE')[:160]
                result.update(status='TAO_MOI',product_code=product_code,model_name=model_name or None,warranty_until=expiry.isoformat())
                if apply:
                    with session.begin_nested():
                        equipment.create_in_tx(session,user,EquipmentIn(serial=serial,product_code=product_code,model_name=model_name,customer_code=customer.code,
                            warranty_until=expiry,condition='CU',note=note))
        except (ValueError,BusinessError) as exc:
            result.update(status='LOI',message=getattr(exc,'message',str(exc)))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv',required=True,action='append',help='CSV từ Máy đã bán hoặc Sửa chữa; có thể lặp --csv.')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    try:
        rows=[r for path in args.csv for r in read_csv(path)]
        with Session(engine()) as session,session.begin():
            user=session.scalar(select(User).where(User.active.is_(True),User.roles.contains(['director'])).order_by(User.id).limit(1))
            if user is None:raise ValueError('Chưa có giám đốc đang hoạt động.')
            result=import_rows(session,user,rows,apply=args.apply)
            print(json.dumps({'apply':args.apply,'rows':result},ensure_ascii=False,indent=2))
        return 0
    except (ValueError,BusinessError) as exc:
        print(getattr(exc,'message',str(exc)),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())

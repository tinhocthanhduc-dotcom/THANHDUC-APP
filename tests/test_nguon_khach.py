"""Nguồn khách trên báo giá và đơn bán cho Growth OS (TUẤN duyệt 16/11/2026, Phần B, migration 0106)."""
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app import quotes, sales_orders as svc
from app.errors import BusinessError
from app.models import AuditLog, Quote, SalesOrder
from app.schemas import OrderLeadIn, QuoteIn, QuoteUpdate, SalesOrderFromQuote, SalesOrderIn, SalesOrderUpdate
from test_sales_orders import GOOD, people, stocked  # noqa: F401
from test_web import service_login
from web_forms import key_for

LINE = dict(description='Công lắp đặt', qty='1', unit='lần', unit_price=100000, vat_rate=8)


def _quote(db, user, **lead):
    q = quotes.create(db, user, QuoteIn(customer_code='KTD-000001', lines=[LINE], **lead))
    quotes.send(db, user, q.id, q.version)
    return quotes.accept(db, user, q.id, q.version)


def test_bao_gia_luu_va_chuan_hoa(db, people):
    q = quotes.create(db, people['sales'], QuoteIn(customer_code='KTD-000001', lines=[LINE], lead_source='facebook',
                                                     campaign_code='  td136x ', lead_ref=' L-2612-0031 '))
    assert (q.lead_source, q.campaign_code, q.lead_ref) == ('FACEBOOK', 'TD136X', 'L-2612-0031')
    assert q.updated_at is not None
    # Không bắt buộc; chuỗi rỗng = chưa rõ
    blank = quotes.create(db, people['sales'], QuoteIn(customer_code='KTD-000001', lines=[LINE], lead_source='', campaign_code=''))
    assert blank.lead_source is None and blank.campaign_code is None
    with pytest.raises(ValidationError):
        QuoteIn(customer_code='KTD-000001', lead_source='TIVI')
    with pytest.raises(ValidationError):
        QuoteIn(customer_code='KTD-000001', campaign_code='X' * 41)
    # Sửa nháp: API bỏ ô thì giữ nguyên; gửi rỗng thì xóa
    q = quotes.update_lines(db, people['sales'], q.id, QuoteUpdate(version=q.version, lines=[LINE]))
    assert q.lead_source == 'FACEBOOK'
    q = quotes.update_lines(db, people['sales'], q.id, QuoteUpdate(version=q.version, lines=[LINE], campaign_code=''))
    assert q.campaign_code is None and q.lead_source == 'FACEBOOK'
    # Bản sửa của báo giá đã gửi giữ nguồn
    quotes.send(db, people['sales'], q.id, q.version)
    db.refresh(q)
    new = quotes.revise(db, people['sales'], q.id, q.version)
    assert new.lead_source == 'FACEBOOK' and new.lead_ref == 'L-2612-0031'


def test_don_ke_thua_tu_bao_gia(db, people):
    user = people['sales']
    q = _quote(db, user, lead_source='ZALO', campaign_code='C-2612-136X', lead_ref='L-1')
    o = svc.create_from_quote(db, user, q.id, SalesOrderFromQuote(version=q.version))
    assert (o.lead_source, o.campaign_code, o.lead_ref, o.web_order_ref) == ('ZALO', 'C-2612-136X', 'L-1', None)
    svc.cancel(db, user, o.id, 'Lập lại', o.version)
    # Người lập đơn đổi được, kể cả để trống
    o = svc.create_from_quote(db, user, q.id, SalesOrderFromQuote(version=q.version, lead_source='SHOPEE', lead_ref='',
                                                                  web_order_ref='2611ABC'))
    assert (o.lead_source, o.campaign_code, o.lead_ref, o.web_order_ref) == ('SHOPEE', 'C-2612-136X', None, '2611ABC')
    # Lập lại từ đơn hủy chép nguồn
    svc.cancel(db, user, o.id, 'Lập lại', o.version)
    copy = svc.copy_cancelled(db, user, o.id)
    assert (copy.lead_source, copy.web_order_ref) == ('SHOPEE', '2611ABC')


def test_don_tao_moi_sua_nhap_va_giam_doc_sua_sau_chot(db, stocked):
    p = stocked
    o = svc.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000001', lines=[GOOD], lead_source='GOOGLE_MAPS'))
    assert o.lead_source == 'GOOGLE_MAPS' and o.updated_at is not None
    o = svc.update_lines(db, p['sales'], o.id, SalesOrderUpdate(version=o.version, lines=[GOOD], lead_source='WEB', web_order_ref='WC-991'))
    assert (o.lead_source, o.web_order_ref) == ('WEB', 'WC-991')
    o = svc.confirm(db, p['sales'], o.id, o.version)
    total, version = o.total, o.version
    # Sau chốt: kinh doanh không sửa được, giám đốc sửa được, không đổi tiền, có nhật ký
    with pytest.raises(BusinessError) as exc:
        svc.change_lead(db, p['sales'], o.id, o.version, OrderLeadIn(lead_source='FACEBOOK'))
    assert exc.value.status == 403
    o = svc.change_lead(db, p['director'], o.id, o.version, OrderLeadIn(lead_source='FACEBOOK', campaign_code='td136x'))
    assert (o.lead_source, o.campaign_code, o.web_order_ref) == ('FACEBOOK', 'TD136X', None)
    assert o.total == total and o.status == 'DA_CHOT' and o.version == version + 1
    log = db.scalars(select(AuditLog).where(AuditLog.action == 'sales_order.lead')).one()
    assert log.before['lead_source'] == 'WEB' and log.after['lead_source'] == 'FACEBOOK'
    with pytest.raises(BusinessError) as exc:
        svc.change_lead(db, p['director'], o.id, version, OrderLeadIn())
    assert exc.value.code == 'VERSION_CONFLICT' or exc.value.status == 409


def test_man_hinh_bao_gia_va_don(client, db, people):
    p = people
    csrf = service_login(client, p['sales'])
    page = client.get('/bao-gia/moi').text
    assert 'name="lead_source"' in page and 'Khách biết mình từ đâu?' in page and 'name="web_order_ref"' not in page
    r = client.post('/bao-gia/moi', data=dict(csrf=csrf, form_key=key_for(client, '/bao-gia/moi'), customer_code='KTD-000001',
                    contact_name='', valid_until='', note='', description='Công', qty='1', unit='lần', unit_price='100.000',
                    vat_0='8', lead_source='FACEBOOK', campaign_code='td136x', lead_ref=''), follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    q = db.scalars(select(Quote)).one()
    assert (q.lead_source, q.campaign_code, q.lead_ref) == ('FACEBOOK', 'TD136X', None)
    quotes.send(db, p['sales'], q.id, q.version)
    q = quotes.accept(db, p['sales'], q.id, q.version)
    assert 'Nguồn khách' in client.get(f'/bao-gia/{q.id}').text
    page = client.get(f'/don-ban/tu-bao-gia/{q.id}').text
    assert 'Lấy từ báo giá' in page and 'value="TD136X"' in page and 'name="web_order_ref"' in page
    # Nguồn không phải web/sàn: mã đơn sàn bị bỏ dù có gõ
    r = client.post(f'/don-ban/tu-bao-gia/{q.id}', data=dict(csrf=csrf, form_key=key_for(client, f'/don-ban/tu-bao-gia/{q.id}'),
                    version=str(q.version), lead_source='FACEBOOK', campaign_code='TD136X', lead_ref='', web_order_ref='SP-1'),
                    follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    o = db.scalars(select(SalesOrder)).one()
    assert (o.lead_source, o.campaign_code, o.web_order_ref, o.quote_id) == ('FACEBOOK', 'TD136X', None, q.id)


def test_giam_doc_sua_nguon_tren_man_don(client, db, stocked):
    p = stocked
    o = svc.create(db, p['sales'], SalesOrderIn(customer_code='KTD-000001', lines=[GOOD], lead_source='ZALO'))
    o = svc.confirm(db, p['sales'], o.id, o.version)
    csrf = service_login(client, p['sales'])
    page = client.get(f'/don-ban/{o.id}').text
    assert 'Nguồn khách' in page and 'Zalo' in page and 'Sửa nguồn khách' not in page
    csrf = service_login(client, p['director'])
    page = client.get(f'/don-ban/{o.id}').text
    assert 'Sửa nguồn khách' in page
    r = client.post(f'/don-ban/{o.id}/lead', data=dict(csrf=csrf, form_key=key_for(client, f'/don-ban/{o.id}/lead'), version=str(o.version),
                    lead_source='TIKTOK_SHOP', campaign_code='', lead_ref='', web_order_ref='TT-77'), follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    db.expire_all()
    o = db.get(SalesOrder, o.id)
    assert (o.lead_source, o.web_order_ref) == ('TIKTOK_SHOP', 'TT-77')

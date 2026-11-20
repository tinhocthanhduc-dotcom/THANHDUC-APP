"""Thu chi rõ ràng + Báo cáo tài chính mới (TUẤN duyệt mẫu 20/11/2026): danh mục, người nộp/nhận, chứng từ gốc, lọc;
báo cáo có tóm tắt, so kỳ trước, tiền vào/ra theo danh mục bấm mở Thu chi."""
import os
from datetime import date

import pytest

from app import finance_catalogs as catalogs, finance_reports as svc
from app.models import CashCategory, Supplier
from app.schemas import CashCategoryIn
from test_cash import cash_people, posted  # noqa: F401
from test_web import service_login

TODAY = date(2026, 10, 18)


def _dump(name, html):
    folder = os.environ.get('TD_DUMP_HTML')
    if folder:
        with open(os.path.join(folder, name), 'w') as f:
            f.write(html)


@pytest.fixture
def money_docs(db, cash_people, monkeypatch):
    monkeypatch.setattr(svc, '_today', lambda: TODAY)
    p = cash_people
    u = p['director']
    catalogs.create(db, u, CashCategory, CashCategoryIn(code='CHI-XANG', name='Xăng xe, gửi xe', direction='CHI'))
    catalogs.create(db, u, CashCategory, CashCategoryIn(code='CHI-NCC', name='Trả nhà cung cấp', direction='CHI'))
    db.add(Supplier(code='NCC-0009', name='Mực in Phúc Thịnh'))
    p['finance'].full_name = 'Đoàn Cảnh Phong'
    db.commit()
    f = p['finance']
    posted(db, u, amount=9000000, is_opening=True, category_code=None, doc_date=date(2026, 10, 1))
    posted(db, f, amount=2500000, doc_date=date(2026, 10, 3), description='Thu tiền máy in')
    posted(db, f, amount=800000, doc_date=date(2026, 9, 20))
    posted(db, f, 'PC', amount=4218754, category_code='CHI-NCC', supplier_code='NCC-0009', description='Trả tiền hàng', doc_date=date(2026, 10, 5))
    posted(db, f, 'PC', amount=350000, category_code='CHI-XANG', partner_name='Cây xăng Petrolimex', description='Đổ xăng xe giao hàng', doc_date=date(2026, 10, 6))
    posted(db, f, 'PC', amount=150000, partner_name='Đoàn Cảnh Phong', description='Ứng mua dây curoa', doc_date=date(2026, 10, 7))
    return p


def test_thu_chi_rows_show_category_partner_and_filter(client, money_docs):
    service_login(client, money_docs['finance'])
    page = client.get('/thu-chi?from=2026-10-01&to=2026-10-31').text
    _dump('thu-chi.html', page)
    for text in ('Danh mục · quỹ', 'Nộp / nhận', 'Trả nhà cung cấp', 'Mực in Phúc Thịnh', 'Xăng xe, gửi xe', 'Cây xăng Petrolimex',
                 'Nhân viên', 'Đoàn Cảnh Phong', 'Chi theo danh mục', 'Thu − chi trong kỳ', 'name="danh_muc"', 'name="doi_tuong"'):
        assert text in page
    # Chip chi theo danh mục: lớn nhất trước, bấm là lọc.
    assert page.index('Trả nhà cung cấp <b>4.218.754</b>') < page.index('Xăng xe, gửi xe <b>350.000</b>')
    only = client.get('/thu-chi?from=2026-10-01&to=2026-10-31&danh_muc=CHI-XANG').text
    assert 'Cây xăng Petrolimex' in only and 'Mực in Phúc Thịnh' not in only
    ncc = client.get('/thu-chi?from=2026-10-01&to=2026-10-31&doi_tuong=ncc').text
    assert 'Mực in Phúc Thịnh' in ncc and 'Cây xăng Petrolimex' not in ncc and 'Đoàn Cảnh Phong</b>' not in ncc
    staff = client.get('/thu-chi?from=2026-10-01&to=2026-10-31&doi_tuong=nv').text
    assert 'Ứng mua dây curoa' in staff and 'Trả tiền hàng' not in staff
    # Tìm theo tên NCC (phiếu chỉ lưu mã NCC).
    found = client.get('/thu-chi?from=2026-10-01&to=2026-10-31&q=phuc+thinh').text
    assert 'Trả tiền hàng' in found and 'Đổ xăng' not in found
    assert client.get('/thu-chi?doi_tuong=sai').status_code == 200


def test_bao_cao_tai_chinh_summary_compare_and_links(client, money_docs):
    service_login(client, money_docs['director'])
    page = client.get('/bao-cao/tai-chinh?period=month').text
    _dump('bao-cao.html', page)
    for text in ('Tiền vào từ đâu', 'Tiền ra cho việc gì', 'Tiền đang có (mọi quỹ)', 'So với: <b>tháng 9/2026</b>',
                 'Tháng 10 đến hôm nay', 'Trả nhà cung cấp', '4.218.754', 'class="bctc-chart"', 'Đầu kỳ 800.000',
                 'href="/thu-chi?loai=PC&danh_muc=CHI-NCC&from=2026-10-01&to=2026-10-31&trang_thai=DA_GHI"',
                 'Khoản chi lớn nhất là trả nhà cung cấp'):
        assert text in page, text
    # Tháng 9 thu 800.000, tháng 10 thu 2.500.000 → tăng 212%.
    assert '▲ 212%' in page
    assert svc.previous_range(date(2026, 10, 1), date(2026, 10, 31)) == (date(2026, 9, 1), date(2026, 9, 30))
    assert svc.previous_range(date(2026, 10, 5), date(2026, 10, 11)) == (date(2026, 9, 28), date(2026, 10, 4))
    assert client.get('/bao-cao/tai-chinh?period=year').status_code == 200
    assert client.get('/bao-cao/tai-chinh?period=week').status_code == 200

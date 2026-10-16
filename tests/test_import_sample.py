from sqlalchemy import text

from app.models import Customer
from scripts.import_sample import import_rows


def test_import_sample_keeps_ktd_codes_and_skips_bad_rows(db, client, login):
    rows = [
        {"MA_KHACH": "KTD-000201", "LOAI": "CA_NHAN", "TEN": "Anh Hùng", "DIEN_THOAI": "903123456"},
        {"MA_KHACH": "KH0012", "LOAI": "CONG_TY", "TEN": "Cty Phát Đạt", "MST": "301234567", "DIEN_THOAI": "2838123456"},
        {"MA_KHACH": "KH0013", "LOAI": "CA_NHAN", "TEN": "Trùng số", "DIEN_THOAI": "0903123456"},
        {"MA_KHACH": "KH0014", "LOAI": "CA_NHAN", "TEN": "Không số", "DIEN_THOAI": ""},
        {"MA_KHACH": "KH0015", "LOAI": "CONG_TY", "TEN": "MST sai", "MST": "123"},
        {"MA_KHACH": "KH0016", "LOAI": "CA_NHAN", "TEN": "Quá giới hạn", "DIEN_THOAI": "0909999999"},
    ]
    created, skipped = import_rows(db, rows, limit=2)
    assert created == ["KTD-000201", "KTD-000202"]
    assert [s[2] for s in skipped] == []  # dừng ngay khi đủ giới hạn
    created, skipped = import_rows(db, rows[2:5], limit=10)
    assert created == []
    assert [s[2] for s in skipped] == ["DUPLICATE_PHONE", "MISSING_PHONE", "INVALID_TAX"]
    c = db.get(Customer, "KTD-000202")
    assert c.old_codes == ["KH0012"] and c.tax_code == "0301234567" and c.is_test
    # Mã cấp mới tiếp nối sau mã cũ lớn nhất.
    h = login("banhang", "sales")
    r = client.post("/customers", json={"customer": {"name": "Mới", "phones": ["0911111111"]}}, headers=h)
    assert r.json()["customer"]["code"] == "KTD-000203"
    assert client.get("/customers/KH0012", headers=h).json()["code"] == "KTD-000202"
    assert db.execute(text("select count(*) from audit_log where action = 'customer.import_sample'")).scalar_one() == 2


def test_import_without_old_ktd_codes_starts_at_one(db):
    created, _ = import_rows(db, [{"MA_KHACH": "KH1", "TEN": "A", "DIEN_THOAI": "0903000001"}], limit=5)
    assert created == ["KTD-000001"]

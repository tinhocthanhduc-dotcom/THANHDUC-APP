import threading

from fastapi.testclient import TestClient

from app.main import app


def person(name="Nguyễn Văn An", phones=("0903123456",), **kw):
    return {"customer": {"kind": "CA_NHAN", "name": name, "phones": list(phones), **kw}}


def company(name="Công ty TNHH Minh Phát", tax="0301234567", phones=(), **kw):
    return {"customer": {"kind": "CONG_TY", "name": name, "tax_code": tax, "phones": list(phones), **kw}}


def test_create_issues_sequential_ktd_codes(client, login):
    h = login("banhang", "sales")
    a = client.post("/customers", json=person(), headers=h)
    b = client.post("/customers", json=company(), headers=h)
    assert a.status_code == 201, a.text
    assert a.json()["customer"]["code"] == "KTD-000001"
    assert b.json()["customer"]["code"] == "KTD-000002"


def test_phone_is_normalized(client, login):
    h = login("banhang", "sales")
    r = client.post("/customers", json=person(phones=["903 123 456"]), headers=h)
    assert r.json()["customer"]["phones"] == ["0903123456"]


def test_person_needs_phone_and_company_needs_identity(client, login):
    h = login("banhang", "sales")
    assert client.post("/customers", json=person(phones=[]), headers=h).json()["error"] == "MISSING_PHONE"
    assert client.post("/customers", json=company(tax=""), headers=h).json()["error"] == "MISSING_IDENTITY"


def test_duplicate_tax_blocked(client, login):
    h = login("banhang", "sales")
    client.post("/customers", json=company(), headers=h)
    r = client.post("/customers", json=company(name="Minh Phát chi nhánh", tax="301234567"), headers=h)
    assert r.status_code == 409
    assert r.json()["error"] == "DUPLICATE_TAX"
    assert r.json()["details"]["existing"] == "KTD-000001"


def test_duplicate_person_phone_blocked(client, login):
    h = login("banhang", "sales")
    client.post("/customers", json=person(), headers=h)
    r = client.post("/customers", json=person(name="Người khác", phones=["+84903123456"]), headers=h)
    assert r.status_code == 409
    assert r.json()["error"] == "DUPLICATE_PHONE"


def test_company_sharing_phone_needs_confirmation(client, login):
    h = login("banhang", "sales")
    client.post("/customers", json=person(), headers=h)
    body = company(phones=["0903123456"])
    r = client.post("/customers", json=body, headers=h)
    assert r.json()["error"] == "PHONE_IN_USE"
    body["confirmation"] = {"mode": "CREATE_SEPARATE", "reason": "Anh An là giám đốc công ty"}
    r = client.post("/customers", json=body, headers=h)
    assert r.status_code == 201
    assert r.json()["warnings"] == [{"code": "PHONE_SHARED_CONFIRMED", "customer": "KTD-000001"}]


def test_contact_phone_counts_for_duplicates(client, login):
    h = login("banhang", "sales")
    body = company(contacts=[{"name": "Chị Lan kế toán", "phones": ["0912000111"]}])
    c = client.post("/customers", json=body, headers=h).json()["customer"]
    assert c["phones"] == ["0912000111"]
    r = client.post("/customers", json=person(name="Lan", phones=["0912000111"]), headers=h)
    assert r.json()["error"] == "PHONE_IN_USE"


def test_similar_name_is_warning_only(client, login):
    h = login("banhang", "sales")
    client.post("/customers", json=person(name="Trần Bình"), headers=h)
    r = client.post("/customers", json=person(name="TRAN  BINH", phones=["0909000000"]), headers=h)
    assert r.status_code == 201
    assert r.json()["warnings"] == [{"code": "SIMILAR_NAME", "customer": "KTD-000001"}]


def test_search_without_accents_by_phone_and_tax(client, login):
    h = login("banhang", "sales")
    client.post("/customers", json=person(name="Nguyễn Văn Đức"), headers=h)
    client.post("/customers", json=company(name="Công ty In Ấn Sài Gòn", tax="0312345678", phones=["02838123456"]), headers=h)

    def codes(q):
        return [c["code"] for c in client.get("/customers", params={"q": q}, headers=h).json()]

    assert codes("van duc") == ["KTD-000001"]
    assert codes("ẤN SÀI") == ["KTD-000002"]
    assert codes("123456") == ["KTD-000002", "KTD-000001"]
    assert codes("0312345678") == ["KTD-000002"]
    assert codes("KTD-000002") == ["KTD-000002"]
    assert codes("100%") == []
    assert len(codes("")) == 2


def test_search_can_hide_test_customers(client, login):
    h = login("banhang", "sales")
    client.post("/customers", json=person(is_test=True), headers=h)
    assert client.get("/customers", params={"include_test": False}, headers=h).json() == []


def test_credit_fields_finance_only(client, login):
    sales = login("banhang", "sales")
    finance = login("ketoan", "finance")
    r = client.post("/customers", json=company(allow_debt=True, credit_limit=5_000_000), headers=sales)
    assert r.status_code == 403
    r = client.post("/customers", json=company(allow_debt=True, credit_limit=5_000_000), headers=finance)
    assert r.status_code == 201
    c = r.json()["customer"]
    r = client.patch(f"/customers/{c['code']}", json={"version": c["version"], "credit_limit": 1}, headers=sales)
    assert r.status_code == 403


def test_director_has_business_rights(client, login):
    h = login("giamdoc", "director")
    assert client.post("/customers", json=company(credit_limit=1_000_000), headers=h).status_code == 201


def test_warehouse_cannot_create_customer(client, login):
    h = login("kho58", "warehouse")
    assert client.post("/customers", json=person(), headers=h).status_code == 403


def test_update_with_version_and_history(client, login):
    sales = login("banhang", "sales")
    finance = login("ketoan", "finance")
    c = client.post("/customers", json=person(), headers=sales).json()["customer"]
    r = client.patch(f"/customers/{c['code']}", json={"version": 1, "address": "58 Lê Lợi", "reason": "khách báo"}, headers=sales)
    assert r.status_code == 200, r.text
    assert r.json()["customer"]["version"] == 2
    stale = client.patch(f"/customers/{c['code']}", json={"version": 1, "note": "x"}, headers=sales)
    assert stale.status_code == 409
    assert stale.json()["error"] == "VERSION_CONFLICT"
    hist = client.get(f"/customers/{c['code']}/history", headers=finance).json()
    assert [x["action"] for x in hist] == ["customer.update", "customer.create"]
    assert hist[0]["before"]["address"] == "" and hist[0]["after"]["address"] == "58 Lê Lợi"
    assert hist[0]["reason"] == "khách báo"
    assert client.get(f"/customers/{c['code']}/history", headers=sales).status_code == 403


def test_update_phone_checks_duplicates_and_keeps_contacts(client, login):
    h = login("banhang", "sales")
    client.post("/customers", json=person(), headers=h)
    body = company(phones=["0909111222"], contacts=[{"name": "Chị Lan", "phones": ["0912000111"]}])
    c = client.post("/customers", json=body, headers=h).json()["customer"]
    r = client.patch(f"/customers/{c['code']}", json={"version": 1, "phones": ["0903123456"]}, headers=h)
    assert r.json()["error"] == "PHONE_IN_USE"
    r = client.patch(f"/customers/{c['code']}", json={"version": 1, "phones": ["0909333444"]}, headers=h)
    assert r.status_code == 200
    assert r.json()["customer"]["phones"] == ["0909333444", "0912000111"]
    assert r.json()["customer"]["contacts"][0]["name"] == "Chị Lan"


def test_update_tax_to_existing_blocked(client, login):
    h = login("banhang", "sales")
    client.post("/customers", json=company(), headers=h)
    c = client.post("/customers", json=company(name="B", tax="0311111111"), headers=h).json()["customer"]
    r = client.patch(f"/customers/{c['code']}", json={"version": 1, "tax_code": "0301234567"}, headers=h)
    assert r.json()["error"] == "DUPLICATE_TAX"


def test_get_by_old_code_and_merged(client, login, db):
    from sqlalchemy import text

    h = login("banhang", "sales")
    client.post("/customers", json=person(), headers=h)
    client.post("/customers", json=person(name="B", phones=["0909000000"]), headers=h)
    db.execute(text("update customers set old_codes = '{KH0012}' where code = 'KTD-000001'"))
    db.execute(text("update customers set status = 'DA_GOP', merged_into = 'KTD-000001' where code = 'KTD-000002'"))
    db.commit()
    assert client.get("/customers/KH0012", headers=h).json()["code"] == "KTD-000001"
    assert client.get("/customers/KTD-000002", headers=h).json()["code"] == "KTD-000001"
    assert client.get("/customers/KTD-999999", headers=h).status_code == 404
    r = client.patch("/customers/KTD-000002", json={"version": 1, "note": "x"}, headers=h)
    assert r.json()["error"] == "MERGED"


def test_unknown_field_rejected(client, login):
    h = login("banhang", "sales")
    r = client.post("/customers", json={"customer": {"name": "A", "phones": ["0903123456"], "MA_KHACH": "X"}}, headers=h)
    assert r.status_code == 422


def test_concurrent_duplicate_phone_creates_only_one(engine, login):
    h = login("banhang", "sales")
    results = []

    def go():
        with TestClient(app) as c:
            results.append(c.post("/customers", json=person(), headers=h).status_code)

    threads = [threading.Thread(target=go) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201, 409, 409, 409, 409]


def test_changing_company_to_person_rechecks_phones(client, login):
    h = login("banhang", "sales")
    client.post("/customers", json=person(), headers=h)
    body = company(phones=["0903123456"])
    body["confirmation"] = {"mode": "CREATE_SEPARATE", "reason": "giám đốc dùng chung số"}
    c = client.post("/customers", json=body, headers=h).json()["customer"]
    r = client.patch(f"/customers/{c['code']}", json={"version": 1, "kind": "CA_NHAN"}, headers=h)
    assert r.json()["error"] == "DUPLICATE_PHONE"

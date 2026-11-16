def item(code="TD-0001", **kw):
    return {"code": code, "name": "Mực HP 12A", "group_name": "Mực in", "unit": "hộp", "sale_price": 0, **kw}


import pytest


@pytest.fixture(autouse=True)
def nhom(db):
    from app.models import ProductGroup
    db.add_all([ProductGroup(name="Mực in"), ProductGroup(name="Linh kiện")])
    db.commit()


def test_warehouse_manages_products(client, login):
    kho = login("kho58", "warehouse")
    sales = login("banhang", "sales")
    gd = login("giamdoc", "director")
    assert client.post("/products", json=item(), headers=sales).status_code == 403
    r = client.post("/products", json=item(), headers=kho)
    assert r.status_code == 201, r.text
    assert client.post("/products", json=item(), headers=kho).json()["error"] == "DUPLICATE_CODE"
    r = client.patch("/products/TD-0001", json={"version": 1, "sale_price": 360000}, headers=gd)
    assert r.json()["sale_price"] == 360000
    assert client.patch("/products/TD-0001", json={"version": 1, "sale_price": 1}, headers=gd).status_code == 409
    r = client.patch("/products/TD-0001", json={"version": 2, "name": "Mực HP 12A chính hãng", "sale_price": 360000}, headers=kho)
    assert r.status_code == 200 and r.json()["sale_price"] == 360000  # Kho sửa tên, giữ nguyên giá vẫn được


def test_only_director_sets_sale_price(client, login, db):
    """TUẤN 16/11/2026: Điều phối và Kho không sửa giá, chỉ giám đốc."""
    from app.models import Product
    kho = login("kho58", "warehouse")
    dp = login("dieuphoi", "dispatcher", "warehouse", "technician")
    gd = login("giamdoc", "director")
    for h in (kho, dp):
        r = client.post("/products", json=item(code="TD-0009", sale_price=350000), headers=h)
        assert r.status_code == 403 and r.json()["error"] == "FORBIDDEN"
    assert db.get(Product, "TD-0009") is None
    assert client.post("/products", json=item(), headers=kho).status_code == 201
    for h in (kho, dp):
        r = client.patch("/products/TD-0001", json={"version": 1, "sale_price": 360000}, headers=h)
        assert r.status_code == 403 and r.json()["error"] == "FORBIDDEN"
    db.expire_all()
    assert db.get(Product, "TD-0001").sale_price == 0
    assert client.post("/products", json=item(code="TD-0002", sale_price=500000), headers=gd).status_code == 201


def test_product_code_format(client, login):
    kho = login("kho58", "warehouse")
    assert client.post("/products", json=item(code="td-1"), headers=kho).status_code == 422
    assert client.post("/products", json=item(sale_price=-1), headers=kho).status_code == 422


def test_product_search_without_accents(client, login):
    kho = login("kho58", "warehouse")
    client.post("/products", json=item(), headers=kho)
    client.post("/products", json=item(code="TD-0002", name="Trống Brother DR-2385", group_name="Linh kiện"), headers=kho)
    r = client.get("/products", params={"q": "trong brother"}, headers=kho).json()
    assert [p["code"] for p in r] == ["TD-0002"]
    assert [p["code"] for p in client.get("/products", params={"q": "linh kien"}, headers=kho).json()] == ["TD-0002"]
    client.patch("/products/TD-0002", json={"version": 1, "active": False}, headers=kho)
    assert [p["code"] for p in client.get("/products", headers=kho).json()] == ["TD-0001"]
    assert client.get("/products/td-0002", headers=kho).json()["active"] is False


def test_product_group_must_exist(client, login):
    kho = login("kho58", "warehouse")
    r = client.post("/products", json=item(group_name="Nhóm lạ"), headers=kho)
    assert r.status_code == 400 and r.json()["error"] == "GROUP_NOT_FOUND"
    assert client.post("/products", json=item(group_name=""), headers=kho).status_code == 201

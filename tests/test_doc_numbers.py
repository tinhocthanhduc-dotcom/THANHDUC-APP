import threading
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.doc_numbers import next_number
from app.errors import BusinessError


def test_so_tang_dan_theo_loai(db):
    d = date(2026, 12, 31)
    assert next_number(db, "PN", d) == "PN-26-00001"
    assert next_number(db, "PN", d) == "PN-26-00002"
    assert next_number(db, "PX", d) == "PX-26-00001"
    db.commit()


def test_sang_nam_moi_ve_00001(db):
    next_number(db, "PN", date(2026, 12, 31))
    assert next_number(db, "PN", date(2027, 1, 1)) == "PN-27-00001"
    assert next_number(db, "PN", date(2026, 12, 31)) == "PN-26-00002"


def test_loai_la_bi_tu_choi(db):
    with pytest.raises(BusinessError) as e:
        next_number(db, "XX", date(2026, 1, 1))
    assert e.value.code == "UNKNOWN_DOC_TYPE"


def test_rollback_tra_lai_so(db):
    d = date(2026, 5, 1)
    next_number(db, "PT", d)
    db.commit()
    next_number(db, "PT", d)
    db.rollback()
    assert next_number(db, "PT", d) == "PT-26-00002"


def test_nhieu_nguoi_cung_xin_so_khong_trung_khong_nhay(engine):
    got, errors = [], []
    start = threading.Barrier(20)

    def go():
        try:
            with Session(engine) as s:
                start.wait()
                got.append(next_number(s, "PX", date(2026, 7, 1)))
                s.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(got) == [f"PX-26-{i:05d}" for i in range(1, 21)]

"""TUẤN 24/10: sửa tại chỗ thợ chọn bảo hành khi Hoàn thành, chọn sẵn 1 tháng; số khác 0/1/2/3/6/12 bị từ chối, không ghi gì."""
import pytest

from test_my_work import people, purchase_people  # noqa: F401
from test_technician_stock import held, transfer, balance  # noqa: F401
from test_ux1b_hoan_thanh_gop import job, snapshot  # noqa: F401
from test_ux1e_gon_man_tho import data, post
from test_web import service_login


def test_default_one_month_and_technician_choice(client, db, job):
    p, _, t = job
    service_login(client, p['technician'])
    html = client.get(f'/tho/phieu/{t.id}').text
    assert 'name="warranty_months" value="1" checked' in html and 'Bảo hành sau sửa' in html
    assert post(client, t, data(client, p, t, warranty_months='1')).status_code == 303
    db.expire_all()
    assert t.status in ('HOAN_THANH', 'DA_DONG') and t.warranty_months == 1


@pytest.mark.parametrize('months,expected', [('0', 0), ('12', 12)])
def test_technician_picks_other_months(client, db, job, months, expected):
    p, _, t = job
    assert post(client, t, data(client, p, t, warranty_months=months)).status_code == 303
    db.expire_all()
    assert t.warranty_months == expected


@pytest.mark.parametrize('months', ['5', 'abc'])
def test_bad_months_rolls_back(client, db, job, months):
    p, _, t = job
    form = data(client, p, t, warranty_months=months)
    before = snapshot(db, t)
    r = post(client, t, form)
    assert r.status_code == 400 and 'WARRANTY_REQUIRED' in r.text and snapshot(db, t) == before

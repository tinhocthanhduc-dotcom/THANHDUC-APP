"""S0: thật sự đi qua HTML, CSRF, route web và PostgreSQL, không tắt kiểm tra."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import engine as app_engine

from app import form_keys, sales_receipts
from app.errors import BusinessError
from app.main import app
from app.models import AuditLog, CashDoc, FormSubmission, SalesOrder, SalesReceipt, StockDoc
from test_my_work import people, purchase_people, order, ticket, DAY
from test_web import service_login, stock_form
from web_forms import Forms, key_for


def report_form(client, db, people, *, service=False, amount='300.000'):
    parent = ticket(db, people, 'HOAN_THANH') if service else order(db, people, 'DA_CHOT', amount=1000000)
    csrf = service_login(client, people['technician' if service else 'sales'])
    path = f"/{'dich-vu' if service else 'don-ban'}/{parent.id}/bao-thu"
    data = dict(csrf=csrf, form_key=key_for(client, path), version=parent.version,
                amount=amount, method='TIEN_MAT', received_at=DAY.isoformat(), note='Thu tại khách')
    return parent, path, data


def send_twice(client, path, data):
    first = client.post(path, data=data, follow_redirects=False)
    second = client.post(path, data=data, follow_redirects=False)
    assert first.status_code == second.status_code == 303, (first.text, second.text)
    assert first.headers['location'] == second.headers['location']
    return first


def test_sales_report_replay_has_one_receipt_and_cached_result(client, db, people):
    parent, path, data = report_form(client, db, people)
    before = db.query(AuditLog).count()
    result = send_twice(client, path, data)
    assert db.query(SalesReceipt).count() == 1
    assert db.query(AuditLog).count() == before + 1
    saved = db.get(FormSubmission, UUID(data['form_key']))
    assert (saved.status, saved.path, saved.user_id) == ('XONG', path, people['sales'].id)
    assert saved.result_url == result.headers['location'] and saved.finished_at is not None


def test_parallel_reports_only_one_business_operation(client, db, people):
    _, path, data = report_form(client, db, people)
    cookies = dict(client.cookies)
    barrier = Barrier(2)

    def submit():
        with TestClient(app, cookies=cookies) as browser:
            barrier.wait(timeout=10)
            response = browser.post(path, data=data, follow_redirects=False)
            return response.status_code, response.headers.get('location'), response.text

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: submit(), range(2)))
    assert sorted(r[0] for r in outcomes) in ([303, 303], [303, 409]), outcomes
    assert db.query(SalesReceipt).count() == 1
    if outcomes[0][0] == outcomes[1][0] == 303:
        assert outcomes[0][1] == outcomes[1][1]
    else:
        assert any('Yêu cầu này đang được xử lý' in row[2] for row in outcomes)


def test_business_error_releases_key_and_same_key_can_be_corrected(client, db, people):
    _, path, data = report_form(client, db, people, amount='1.000.001')
    error = client.post(path, data=data)
    assert error.status_code == 409 and 'OVER_PAYMENT' in error.text
    assert 'value="1.000.001"' in error.text
    assert db.get(FormSubmission, UUID(data['form_key'])) is None
    assert db.query(SalesReceipt).count() == 0
    rendered_keys = [f['fields']['form_key'] for f in Forms(error.text, error.url).forms if 'form_key' in f['fields']]
    assert data['form_key'] not in rendered_keys
    send_twice(client, path, {**data, 'amount': '500.000'})
    assert db.query(SalesReceipt).one().amount == 500000


def test_validation_error_also_releases_key(client, db, people):
    _, path, data = report_form(client, db, people, amount='sai')
    assert client.post(path, data=data).status_code == 400
    assert db.get(FormSubmission, UUID(data['form_key'])) is None
    send_twice(client, path, {**data, 'amount': '50.000'})
    assert db.query(SalesReceipt).one().amount == 50000


@pytest.mark.parametrize('kind', ['order', 'stock', 'cash', 'service_report'])
def test_creations_and_technician_report_are_not_duplicated(client, db, people, configured_finance, kind):
    if kind == 'service_report':
        _, path, data = report_form(client, db, people, service=True, amount='50.000')
        model = SalesReceipt
    else:
        csrf = service_login(client, people['director'])
        if kind == 'order':
            path = '/don-ban/moi'; model = SalesOrder
            data = dict(csrf=csrf, customer_code='KTD-000001', description=['Công cài đặt'],
                        kind_0='KHAC', product_code=[''], qty=['1'], unit=['lần'], unit_price=['120.000'], vat_0='8')
        elif kind == 'stock':
            path = '/phieu-kho/moi'; model = StockDoc
            data = stock_form(csrf)
        else:
            path = '/thu-chi/moi'; model = CashDoc
            data = dict(csrf=csrf, kind='PT', doc_date=DAY.isoformat(), fund_code='QUY-TM',
                        category_code='THU-BH', amount='120.000', description='Thu tiền')
        data['form_key'] = key_for(client, path)
    send_twice(client, path, data)
    assert db.query(model).count() == 1
    if kind == 'service_report':
        assert db.query(SalesReceipt).one().amount == 50000
        assert db.query(CashDoc).count() == 0  # thợ báo chưa ghi quỹ


def test_two_independent_page_views_create_two_real_orders(client, db, people):
    csrf = service_login(client, people['sales'])
    path = '/don-ban/moi'
    keys = [key_for(client, path), key_for(client, path)]
    assert keys[0] != keys[1]
    for key in keys:
        response = client.post(path, data=dict(csrf=csrf, form_key=key, customer_code='KTD-000001',
            kind_0='KHAC', description=['Cài máy'], qty=['1'], unit=['lần'], unit_price=['120.000'], vat_0='8'),
            follow_redirects=False)
        assert response.status_code == 303, response.text
    assert db.query(SalesOrder).count() == 2


@pytest.mark.parametrize('value', [None, '', 'not-a-uuid'])
def test_missing_or_invalid_key_is_400(client, db, people, value):
    _, path, data = report_form(client, db, people)
    data.pop('form_key')
    if value is not None:
        data['form_key'] = value
    response = client.request('POST', path, data=data, follow_redirects=False)
    assert response.status_code == 400 and 'Trang đã cũ, hãy tải lại rồi thử lại.' in response.text
    assert 'BAD_FORM_KEY' in response.text
    assert db.query(SalesReceipt).count() == db.query(FormSubmission).count() == 0


def test_key_belongs_to_first_user_not_next_user(client, db, people):
    _, path, data = report_form(client, db, people)
    send_twice(client, path, data)
    csrf = service_login(client, people['other_sales'])
    response = client.post(path, data={**data, 'csrf': csrf}, follow_redirects=False)
    assert response.status_code == 403
    assert db.query(SalesReceipt).count() == 1
    assert db.get(FormSubmission, UUID(data['form_key'])).status == 'XONG'


def test_csrf_is_checked_before_key_claim(client, db, people):
    _, path, data = report_form(client, db, people)
    response = client.post(path, data={**data, 'csrf': 'bad'}, follow_redirects=False)
    assert response.status_code == 403
    assert db.query(FormSubmission).count() == 0
    send_twice(client, path, data)


def test_abandoned_processing_key_stays_blocked(client, db, people):
    _, path, data = report_form(client, db, people)
    key = form_keys.reserve(data['form_key'], people['sales'].id, path)
    for _ in range(2):
        response = client.post(path, data=data, follow_redirects=False)
        assert response.status_code == 409 and 'đang được xử lý' in response.text
    assert db.get(FormSubmission, key).status == 'DANG_XU_LY'
    assert db.query(SalesReceipt).count() == 0


def test_unexpected_failure_after_business_commit_keeps_key(client, db, people, monkeypatch):
    _, path, data = report_form(client, db, people)
    original = sales_receipts.report

    def crash_after_commit(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('Giả lập tiến trình lỗi sau khi chứng từ đã commit')

    monkeypatch.setattr(sales_receipts, 'report', crash_after_commit)
    with TestClient(app, cookies=dict(client.cookies), raise_server_exceptions=False) as browser:
        assert browser.post(path, data=data).status_code == 500
        assert browser.post(path, data=data).status_code == 409
    assert db.query(SalesReceipt).count() == 1
    assert db.get(FormSubmission, UUID(data['form_key'])).status == 'DANG_XU_LY'


def test_all_post_template_forms_have_key_next_to_csrf():
    total = 0
    for template in (Path(__file__).parents[1] / 'app/web/templates').glob('*.jinja'):
        source = template.read_text()
        macros = {m[0]: m[1] for m in re.findall(r'{%\s*macro\s+(\w+)\([^%]*%}(.*?){%\s*endmacro\s*%}', source, re.S)}
        for match in re.finditer(r'<form\b[^>]*\bmethod=["\']post["\'][^>]*>(.*?)</form>', source, re.S | re.I):
            total += 1
            body = match.group(1)
            # Một số form dùng macro token/hidden/version ngay cạnh csrf.
            for name, contents in macros.items():
                body = re.sub(r'{{\s*' + name + r'\(.*?}}', lambda _: contents, body, flags=re.S)
            assert 'name="form_key"' in body and '{{ form_key() }}' in body, template.name
        for token in re.finditer(r'<input\b[^>]*name="csrf"[^>]*>', source):
            assert re.match(r'<input[^>]*name="form_key"', source[token.end():]), template.name
    assert total >= 50


def test_each_rendered_post_form_has_distinct_fresh_uuid(client, db, people):
    row = order(db, people, 'DA_CHOT')
    service_login(client, people['director'])
    previous = set()
    for _ in range(2):
        page = client.get(f'/don-ban/{row.id}')
        forms = [f for f in Forms(page.text, page.url).forms if f['method'] == 'post']
        keys = [f['fields']['form_key'] for f in forms]
        assert len(keys) >= 3 and len(set(keys)) == len(keys)
        assert not previous.intersection(keys)
        assert all(UUID(key).version == 4 for key in keys)
        previous.update(keys)


def test_purge_command_removes_only_old_rows(db, make_user, capsys, monkeypatch):
    user = make_user('s0.purge', 'sales')
    now = datetime.now(timezone.utc)
    old, recent = uuid4(), uuid4()
    db.add_all([FormSubmission(key=old, user_id=user.id, path='/don-ban/moi', status='DANG_XU_LY',
                              created_at=now-timedelta(days=31)),
                FormSubmission(key=recent, user_id=user.id, path='/don-ban/moi', status='DANG_XU_LY',
                              created_at=now-timedelta(days=29))])
    db.commit()
    monkeypatch.setattr('sys.argv', ['python -m app.form_keys', 'purge'])
    form_keys.main()
    assert 'Đã xóa 1 khóa form' in capsys.readouterr().out
    assert list(db.scalars(select(FormSubmission.key))) == [recent]


def test_completed_submission_is_immutable_and_cannot_be_released(db, make_user):
    user = make_user('s0.once', 'sales')
    key = form_keys.reserve(form_keys.form_key(), user.id, '/don-ban/moi')
    form_keys.finish(key, '/don-ban/1?da_luu=1')
    form_keys.finish(key, '/don-ban/2')  # cập nhật có điều kiện, không chốt lần hai
    form_keys.release(key)  # lỗi ở request khác không được gỡ khóa đã xong
    with Session(app_engine()) as session:
        row = session.get(FormSubmission, key)
        assert (row.status, row.result_url) == ('XONG', '/don-ban/1?da_luu=1')
        with pytest.raises(DBAPIError, match='chỉ được chốt một lần'):
            session.execute(update(FormSubmission).where(FormSubmission.key == key).values(result_url='/don-ban/2'))
        session.rollback()
        assert session.get(FormSubmission, key).result_url == '/don-ban/1?da_luu=1'


def test_error_cleanup_racing_duplicate_claim_retries_without_500(db, make_user):
    user = make_user('s0.cleanup.race', 'sales')
    value = form_keys.form_key()
    key = form_keys.reserve(value, user.id, '/don-ban/moi')
    released = False

    def cleanup_between_insert_and_select(conn, cursor, statement, parameters, context, executemany):
        nonlocal released
        if not released and statement.startswith('INSERT INTO form_submissions') and cursor.rowcount == 0:
            released = True
            # Transaction riêng, giống response lỗi của request đầu.
            form_keys.release(key)

    event.listen(app_engine(), 'after_cursor_execute', cleanup_between_insert_and_select)
    try:
        assert form_keys.reserve(value, user.id, '/don-ban/moi') == key
    finally:
        event.remove(app_engine(), 'after_cursor_execute', cleanup_between_insert_and_select)
    assert released
    assert db.get(FormSubmission, key).status == 'DANG_XU_LY'

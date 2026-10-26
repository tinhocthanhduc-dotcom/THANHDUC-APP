"""TD2: nhóm việc đếm tổng ngay trong câu lấy dòng; chuông chỉ tính nhóm việc người khác đẩy tới."""
from app import my_work as svc, notifications
from test_my_work import DAY, people, purchase_people, ticket


def test_window_count_matches_rows_and_past_last_page(db, people, monkeypatch):
    p = people
    for _ in range(3):
        ticket(db, p, 'MOI')
    monkeypatch.setattr(svc, 'LIMIT', 2)
    first = svc.filtered(db, p['dispatcher'], 'service_unassigned', 1, DAY)
    assert first['count'] == 3 and len(first['rows']) == 2 and '_total' not in first['rows'][0]
    second = svc.filtered(db, p['dispatcher'], 'service_unassigned', 2, DAY)
    assert second['count'] == 3 and len(second['rows']) == 1
    # Trang quá cuối: không có dòng nào nhưng tổng vẫn đúng.
    past = svc.filtered(db, p['dispatcher'], 'service_unassigned', 5, DAY)
    assert past['count'] == 3 and past['rows'] == []


def test_bell_computes_only_incoming_groups(db, people, monkeypatch):
    p = people
    ticket(db, p, 'MOI')
    seen = []
    real = svc._group
    monkeypatch.setattr(svc, '_group', lambda session, spec, page=1: seen.append(spec['key']) or real(session, spec, page))
    items = notifications.items(db, p['dispatcher'])
    assert seen and set(seen) <= set(notifications.INCOMING)
    assert any(i['id'].startswith('service_unassigned:') for i in items)

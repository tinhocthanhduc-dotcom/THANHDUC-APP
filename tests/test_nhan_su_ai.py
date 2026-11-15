"""Nhân sự AI (TUẤN duyệt 15/11/2026): nhắc vào ca, báo giám đốc chấm công, tin tuần KPI + hoa hồng, soát bảng lương."""
from datetime import date, datetime, time

import pytest
from sqlalchemy import func, select

from app import attendance, commissions, dispatch_alerts, nhan_su_ai as nsa, nhan_vien_ao as nva
from app.errors import BusinessError
from app.models import (AttendancePunch, AttendanceRequest, Employee, PayrollInput, PayrollLine, VirtualAgentLog, ZaloChat)
from app.service_sla import TZ
from test_web import service_login

DAY = attendance.today()
while DAY.weekday() == 6:   # Chủ nhật không phải ngày làm việc: lùi về Thứ 7 gần nhất
    DAY = date.fromordinal(DAY.toordinal() - 1)


def at(hm):
    return datetime.combine(DAY, time.fromisoformat(hm), TZ)


class Sent:
    def __init__(self):
        self.msgs = []

    def __call__(self, session, chat, body, *_):
        self.msgs.append((chat.user_id, body))
        return None

    def to(self, uid):
        return [b for u, b in self.msgs if u == uid]


@pytest.fixture
def team(db, make_user, monkeypatch):
    settings = nva.get_settings().model_copy(update=dict(zalo_bot_token='token', commission_start=date(2026, 1, 1)))
    for mod in (nva, nsa, dispatch_alerts, commissions):
        monkeypatch.setattr(mod, 'get_settings', lambda: settings)
    monkeypatch.setattr(nsa, '_workday', lambda session, day: True)
    p = dict(gd=make_user('nsa.gd', 'director'), phong=make_user('nsa.phong', 'technician'),
             thanh=make_user('nsa.thanh', 'sales', 'finance'), a=make_user('nsa.khongzalo', 'technician'))
    p['phong'].full_name, p['thanh'].full_name, p['gd'].full_name = 'Đoàn Cảnh Phong', 'Nguyễn Thị Thanh', 'Bùi Minh Tuấn'
    for i, key in enumerate(('gd', 'phong', 'thanh', 'a')):
        db.add(Employee(user_id=p[key].id, code=f'NV-T{i}', position='Khác', start_date=date(2026, 1, 1), created_by=p['gd'].id))
        if key != 'a':
            db.add(ZaloChat(chat_id=f'z{i}', chat_type='PRIVATE', display_name=key, user_id=p[key].id))
    db.commit()
    return p


def punch(db, user, kind, hm, source='APP'):
    db.add(AttendancePunch(user_id=user.id, kind=kind, at=at(hm), source=source, off_hours=False))
    db.commit()


def test_remind_only_staff_without_punch(db, team):
    punch(db, team['thanh'], 'IN', '07:31')
    send = Sent()
    assert nsa.remind_once(db, at('07:40'), send) == 0        # chưa tới mốc 07:45
    assert nsa.remind_once(db, at('07:50'), send) == 1
    assert len(send.to(team['phong'].id)) == 1 and 'chưa bấm Vào ca' in send.to(team['phong'].id)[0]
    assert not send.to(team['thanh'].id) and not send.to(team['gd'].id)   # đã vào ca / giám đốc không bị nhắc
    assert nsa.remind_once(db, at('07:55'), send) == 0        # không nhắc lặp
    assert nsa.remind_once(db, at('09:00'), send) == 0        # quá 1 giờ sau mốc
    entry = db.scalar(select(VirtualAgentLog).where(VirtualAgentLog.agent == 'NHAN_SU'))
    assert entry.action == 'NHAC' and entry.target_id == team['phong'].id


def test_remind_skips_leave(db, team):
    db.add(AttendanceRequest(user_id=team['phong'].id, kind='NGHI_PHEP', from_date=DAY, to_date=DAY, part='CA_NGAY',
                             reason='Việc nhà', status='DA_DUYET', number='CC-T1'))
    punch(db, team['thanh'], 'IN', '07:31')
    db.commit()
    assert nsa.remind_once(db, at('07:50'), Sent()) == 0


def test_director_morning_and_evening_report(db, team):
    punch(db, team['phong'], 'IN', '08:05')
    punch(db, team['a'], 'IN', '07:30')
    punch(db, team['a'], 'OUT', '17:30')
    send = Sent()
    assert nsa.report_once(db, at('08:35'), send) == 1
    body = send.to(team['gd'].id)[0]
    assert 'Đi trễ: Phong 08:05 (35 phút)' in body and 'Chưa vào ca: Thanh' in body and 'Đã vào ca 2/3' in body
    assert not send.to(team['phong'].id)
    assert nsa.report_once(db, at('08:40'), send) == 0
    punch(db, team['phong'], 'OUT', '16:50')
    punch(db, team['thanh'], 'IN', '09:00')
    punch(db, team['thanh'], 'OUT', '17:30', source='TU_DONG')
    assert nsa.report_once(db, at('17:50'), send) == 1
    body = send.to(team['gd'].id)[1]
    assert 'Về sớm: Phong ra 16:50 (sớm 40 phút)' in body and 'tự chốt ra ca (quên bấm): Thanh 17:30' in body


def test_nothing_to_report_sends_nothing(db, team):
    for key in ('phong', 'thanh', 'a'):
        punch(db, team[key], 'IN', '07:30')
    send = Sent()
    assert nsa.report_once(db, at('08:35'), send) == 0 and not send.msgs
    assert nsa.report_once(db, at('08:36'), send) == 0


def test_weekly_text_own_numbers_only(db, team):
    row = nva.rows(db)['NHAN_SU']
    row.config = dict(weekly_day=DAY.weekday())
    db.commit()
    punch(db, team['phong'], 'IN', '07:30')
    send = Sent()
    assert nsa.weekly_once(db, at('17:05'), send) == 3     # Phong, Thanh, giám đốc (người không Zalo bỏ qua)
    phong, thanh, gd = (send.to(team[k].id)[0] for k in ('phong', 'thanh', 'gd'))
    assert 'Công:' in phong and 'Phiếu hoàn thành' in phong and 'Hoa hồng tạm tính' in phong and 'Thanh' not in phong
    assert 'Lãi gộp' not in thanh and 'lãi gộp' not in thanh and 'Doanh thu chưa VAT' in thanh
    assert 'Phong:' in gd and 'Thanh:' in gd and 'lãi gộp' in gd and 'Tổng hoa hồng tạm tính' in gd
    assert nsa.weekly_once(db, at('17:10'), send) == 0


def test_payroll_check_lists_issues_without_writing(db, team):
    month = DAY.replace(day=1)
    db.add(AttendanceRequest(user_id=team['phong'].id, kind='BO_SUNG_VAO', punch_at=at('07:30'), reason='Quên',
                             status='CHO_DUYET', number='CC-T2'))
    db.commit()
    before = (db.scalar(select(func.count()).select_from(PayrollInput)), db.scalar(select(func.count()).select_from(PayrollLine)))
    check = nsa.payroll_check(db, month)
    titles = [i['title'] for i in check['items']]
    assert any('Hoa hồng tháng' in t and 'chưa khóa' in t for t in titles)
    assert any('Bảng công tháng' in t for t in titles)
    assert any('chưa có mức lương' in t and 'Phong' in t for t in titles)
    assert '1 yêu cầu chấm công chờ duyệt' in titles
    assert check['items'][0]['level'] == 'hot'
    assert (db.scalar(select(func.count()).select_from(PayrollInput)), db.scalar(select(func.count()).select_from(PayrollLine))) == before
    text_ = nsa.payroll_text(check, 'https://x')
    assert 'điểm cần xem trước khi duyệt' in text_ and '/luong?thang=' in text_


def test_payroll_once_on_configured_day(db, team):
    row = nva.rows(db)['NHAN_SU']
    row.config = dict(payroll_day=min(DAY.day, 28))
    db.commit()
    send = Sent()
    if DAY.day > 28:
        assert nsa.payroll_once(db, at('08:35'), send) == 0
        return
    assert nsa.payroll_once(db, at('08:35'), send) == 1
    assert 'Soát bảng lương tháng' in send.to(team['gd'].id)[0] and not send.to(team['phong'].id)
    assert nsa.payroll_once(db, at('08:40'), send) == 0


def test_switch_off_stops_all(db, team):
    rows = nva.rows(db)
    rows['NHAN_SU'].enabled = False
    db.commit()
    assert nsa.run_once(db, at('07:50'), Sent()) == 0


def test_save_settings(db, team):
    form = dict(bat='1', cai_dat='1', remind_at='', report_times='08:15, 18:00', weekly_day='4', weekly_time='16:30',
                weekly_director='0', payroll_day='27', payroll_time='09:00', change_pct='25')
    nva.save(db, team['gd'], 'NHAN_SU', form)
    c = nva.cfg(nva.rows(db)['NHAN_SU'])
    assert c['remind_at'] == '' and c['report_times'] == ['08:15', '18:00'] and c['weekly_day'] == 4
    assert c['weekly_director'] is False and c['payroll_day'] == 27 and c['change_pct'] == 25
    with pytest.raises(BusinessError):
        nva.save(db, team['gd'], 'NHAN_SU', dict(form, payroll_day='31'))
    db.rollback()
    with pytest.raises(BusinessError):
        nva.save(db, team['phong'], 'NHAN_SU', form)


def test_pages_show_agent_and_check(db, team, client):
    service_login(client, team['gd'])
    page = client.get('/nhan-vien-ao').text
    assert 'Nhân sự AI' in page and 'name="payroll_day"' in page
    luong = client.get('/luong').text
    assert 'Nhân sự AI soát trước' in luong and 'chưa có mức lương' in luong

"""Hạn xử lý phiếu dịch vụ theo quy trình điều phối (13/10/2026). Chỉnh thời hạn tại đây.

Mỗi trạng thái có hạn cho bước kế tiếp: MOI là hạn giao thợ, DA_GIAO là hạn thợ nhận, DA_NHAN là hạn hẹn khách,
DA_HEN là đúng giờ hẹn, DANG_LAM là kết thúc trong ngày. Trạng thái không có trong bảng thì không tính hạn.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
WORKDAY_END = time(18, 0)
END_OF_DAY = "END_OF_DAY"
APPOINTMENT = "APPOINTMENT"

SLA = {
    "MOI": {"KHAN": timedelta(minutes=15), "GAP": timedelta(minutes=30), "THUONG": timedelta(hours=2)},
    "DA_GIAO": {"KHAN": timedelta(minutes=15), "GAP": timedelta(minutes=15), "THUONG": timedelta(minutes=15)},
    "DA_NHAN": {"KHAN": timedelta(minutes=30), "GAP": END_OF_DAY, "THUONG": END_OF_DAY},
    "DA_HEN": {"KHAN": APPOINTMENT, "GAP": APPOINTMENT, "THUONG": APPOINTMENT},
    "DANG_LAM": {"KHAN": END_OF_DAY, "GAP": END_OF_DAY, "THUONG": END_OF_DAY},
}


def end_of_day(now: datetime) -> datetime:
    """Cuối giờ làm hôm nay theo giờ Việt Nam; đã qua giờ đó thì là cuối giờ làm hôm sau."""
    local = now.astimezone(TZ)
    end = datetime.combine(local.date(), WORKDAY_END, TZ)
    return end if end > local else end + timedelta(days=1)


def due_at(status: str, priority: str, now: datetime, appointment_at: datetime | None) -> datetime | None:
    rule = SLA.get(status, {}).get(priority)
    if rule is None:
        return None
    if rule == END_OF_DAY:
        return end_of_day(now)
    if rule == APPOINTMENT:
        return appointment_at
    return now + rule

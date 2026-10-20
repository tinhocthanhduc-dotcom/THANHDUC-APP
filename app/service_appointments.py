"""Khung hẹn theo giờ Việt Nam, dùng chung cho lõi và giao diện."""
from datetime import datetime, time, timedelta
from .config import get_settings
from .errors import BusinessError
from .service_sla import TZ

LABELS = {'SANG': 'Sáng', 'CHIEU': 'Chiều'}


def bounds(slot):
    raw = getattr(get_settings(), 'slot_' + slot.lower())
    return tuple(time.fromisoformat(value) for value in raw.split('-'))


def end(appointment, slot):
    if appointment is None or slot not in LABELS:
        return appointment
    return datetime.combine(appointment.astimezone(TZ).date(), bounds(slot)[1], TZ)


def validate(appointment, slot, now):
    if slot not in (None, 'SANG', 'CHIEU', 'GIO'):
        raise BusinessError('BAD_APPOINTMENT','Khung hẹn chưa hợp lệ.')
    if appointment is None:
        raise BusinessError('APPOINTMENT_REQUIRED', 'Phải chọn hẹn khách.')
    if appointment.tzinfo is None or end(appointment, slot) <= now:
        raise BusinessError('BAD_APPOINTMENT', 'Hẹn khách phải còn trong khung hoặc sau hiện tại.')
    if slot in LABELS and appointment.astimezone(TZ).timetz().replace(tzinfo=None) != bounds(slot)[0]:
        raise BusinessError('BAD_APPOINTMENT', 'Giờ bắt đầu chưa đúng khung hẹn.')


def choices(now, returning=False):
    local = now.astimezone(TZ); result = []
    for days, suffix in ((0, 'nay'), (1, 'mai')):
        for slot in LABELS:
            if returning and not days and slot == 'SANG':
                continue
            start = datetime.combine(local.date()+timedelta(days=days), bounds(slot)[0], TZ)
            if end(start, slot) > now:
                result.append(dict(value=f'{slot}:{start.date().isoformat()}', label=f'{LABELS[slot]} {suffix}'))
    return result


def parse(form, now, required=True):
    choice = str(form.get('appointment_choice', ''))
    slot = str(form.get('appointment_slot', '')) or None
    raw = str(form.get('appointment_at', '')).strip()
    if choice and choice != 'GIO':
        try:
            slot, day = choice.split(':')
            if slot not in LABELS: raise ValueError
            value = datetime.combine(datetime.fromisoformat(day).date(), bounds(slot)[0], TZ)
        except (ValueError, TypeError):
            raise BusinessError('BAD_APPOINTMENT', 'Khung hẹn chưa hợp lệ.')
    else:
        try:
            value = datetime.fromisoformat(raw) if raw else None
            if value and value.tzinfo is None: value = value.replace(tzinfo=TZ)
        except ValueError:
            raise BusinessError('BAD_APPOINTMENT', 'Ngày giờ hẹn chưa hợp lệ.')
        slot = 'GIO' if choice == 'GIO' else slot
    if required or value is not None:
        validate(value, slot, now)
    return value, slot


def label(appointment, slot, short=False):
    if appointment is None: return 'Chưa hẹn'
    local = appointment.astimezone(TZ)
    if slot in LABELS: return LABELS[slot] + ('' if short else ' '+local.strftime('%d/%m/%Y'))
    return local.strftime('%H:%M' if short else '%d/%m/%Y %H:%M')

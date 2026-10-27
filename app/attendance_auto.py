"""Vòng nền tự động Ra ca cho người quên bấm (TUẤN 27/10/2026). Chạy cùng cờ TD_PUSH_LOOP như Web Push;
2 worker uvicorn chỉ một worker chạy nhờ khóa advisory. Mỗi phút gọi attendance.auto_clock_out."""
import logging
import threading
import time

from sqlalchemy import text

log = logging.getLogger(__name__)
LOCK_ID = 7_301_027
EVERY = 60


def _loop():
    from .db import engine
    from . import attendance
    eng = engine()
    while True:
        try:
            with eng.connect() as conn:
                locked = conn.execute(text('select pg_try_advisory_lock(:k)'), dict(k=LOCK_ID)).scalar()
                conn.commit()
                if locked:
                    from sqlalchemy.orm import Session
                    while True:
                        with Session(bind=eng, expire_on_commit=False) as session:
                            for row in attendance.auto_clock_out(session):
                                log.info('auto clock-out user %s at %s', row.user_id, row.at)
                        time.sleep(EVERY)
        except Exception:  # noqa: BLE001 - vòng nền không được làm sập app; thử lại sau.
            log.exception('auto clock-out loop')
        time.sleep(EVERY)


def start():
    threading.Thread(target=_loop, name='auto-clock-out', daemon=True).start()

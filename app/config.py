"""Cấu hình đọc từ biến môi trường (file .env trên máy chủ, không đưa lên GitHub)."""
from functools import lru_cache
from datetime import date, time

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_VAT_RATE = 8
ALERT_RECEIPT_PENDING_DAYS = 1
ALERT_DEBT_DAYS = 30
ALERT_SLOW_STOCK_DAYS = 90
ALERT_CLOSING_LOOKBACK_DAYS = 7


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TD_", extra="ignore")

    database_url: str = "postgresql+psycopg://thanhduc:thanhduc@localhost:5432/thanhduc"
    # Khóa ký phiên đăng nhập. Bắt buộc đặt chuỗi ngẫu nhiên dài trên máy chủ thật.
    secret_key: str = "change-me"
    token_minutes: int = 12 * 60
    search_limit: int = 20
    supplier_hold_alert_days: int = Field(default=30, ge=0, le=36500)
    pending_alert_days: int = Field(default=30, ge=0, le=36500)
    commission_start: date = date(2027, 1, 1)
    service_warranty_days: int = Field(default=90, ge=0, le=36500)
    work_start: time = time(8, 0)
    work_end: time = time(17, 30)
    work_days: tuple[int, ...] = (0, 1, 2, 3, 4, 5)  # TD_WORK_DAYS='[0,1,2,3,4,5]', Monday=0
    late_grace_minutes: int = Field(default=10, ge=0, le=120)
    off_hours_start: time = time(6, 0)
    off_hours_end: time = time(21, 0)

    @model_validator(mode='after')
    def valid_work_schedule(self):
        if self.work_start >= self.work_end or self.off_hours_start >= self.off_hours_end:
            raise ValueError('Giờ bắt đầu phải trước giờ kết thúc')
        if not self.work_days or any(d < 0 or d > 6 for d in self.work_days):
            raise ValueError('Ngày làm việc từ 0 (thứ 2) đến 6 (Chủ nhật)')
        return self

    service_fee_group: str = 'Dịch vụ'
    sales_default_warehouse: str = '58'
    slot_sang: str = '08:00-12:00'
    slot_chieu: str = '13:30-17:30'

    @field_validator('slot_sang', 'slot_chieu')
    @classmethod
    def valid_slot(cls, value):
        import re
        from datetime import time
        if not re.fullmatch(r'\d{2}:\d{2}-\d{2}:\d{2}', value):
            raise ValueError('Khung giờ phải là HH:MM-HH:MM')
        start, end = (time.fromisoformat(v) for v in value.split('-'))
        if start >= end: raise ValueError('Giờ kết thúc phải sau giờ bắt đầu')
        return value

    # Bật trên máy chủ thật (HTTPS) để trình duyệt chỉ gửi cookie đăng nhập qua kết nối mã hóa.
    cookie_secure: bool = False
    # Thư mục lưu tệp đính kèm (ảnh nghiệm thu, biên bản). Sao lưu cùng database.
    files_dir: str = "data/files"
    max_upload_mb: int = 15
    company_name: str = 'CÔNG TY TNHH THƯƠNG MẠI DỊCH VỤ TIN HỌC THÀNH ĐỨC'
    company_address: str = 'Số 58 Đường Mã Lò, Phường Bình Trị Đông, Thành phố Hồ Chí Minh'
    company_phone: str = '028 6266 1869'
    company_hotline: str = '0888 304 668'
    company_tax_code: str = '0313015734'


@lru_cache
def get_settings() -> Settings:
    return Settings()

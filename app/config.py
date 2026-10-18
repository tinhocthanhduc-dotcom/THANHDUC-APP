"""Cấu hình đọc từ biến môi trường (file .env trên máy chủ, không đưa lên GitHub)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_VAT_RATE = 8


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TD_", extra="ignore")

    database_url: str = "postgresql+psycopg://thanhduc:thanhduc@localhost:5432/thanhduc"
    # Khóa ký phiên đăng nhập. Bắt buộc đặt chuỗi ngẫu nhiên dài trên máy chủ thật.
    secret_key: str = "change-me"
    token_minutes: int = 12 * 60
    search_limit: int = 20
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

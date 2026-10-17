"""Cấu hình đọc từ biến môi trường (file .env trên máy chủ, không đưa lên GitHub)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TD_", extra="ignore")

    database_url: str = "postgresql+psycopg://thanhduc:thanhduc@localhost:5432/thanhduc"
    # Khóa ký phiên đăng nhập. Bắt buộc đặt chuỗi ngẫu nhiên dài trên máy chủ thật.
    secret_key: str = "change-me"
    token_minutes: int = 12 * 60
    search_limit: int = 20
    # Bật trên máy chủ thật (HTTPS) để trình duyệt chỉ gửi cookie đăng nhập qua kết nối mã hóa.
    cookie_secure: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

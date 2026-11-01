"""Cấu hình đọc từ biến môi trường (đặt trong deploy/.env trên Z2, không đưa khóa lên GitHub)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "co", "có", "on")


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class CauHinh:
    database_url: str = ""
    wp_url: str = "https://thanhduc.vn"
    wp_user: str = ""
    wp_app_password: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5.5"
    ai_nha_cung_cap: str = ""
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "medium"
    ai_tra_web: bool = True
    tach_nen: bool = True
    anh_kich_thuoc: int = 1200
    anh_dinh_dang: str = "webp"
    dang_gia: bool = True
    vat_phan_tram: int = 0
    hotline: str = ""
    chuyen_muc_bai: str = ""
    thu_muc: Path = field(default_factory=lambda: Path("/data"))

    @classmethod
    def tu_moi_truong(cls, env: dict[str, str] | None = None) -> "CauHinh":
        e = os.environ if env is None else env
        dinh_dang = (e.get("WEB_ANH_DINH_DANG") or "webp").strip().lower()
        if dinh_dang not in ("webp", "jpg"):
            dinh_dang = "webp"
        effort = (e.get("WEB_AI_EFFORT") or "medium").strip().lower()
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            effort = "medium"
        return cls(
            database_url=e.get("TD_DATABASE_URL", ""),
            wp_url=(e.get("WP_URL") or "https://thanhduc.vn").strip().rstrip("/"),
            wp_user=(e.get("WP_USER") or "").strip(),
            # Application Password WordPress hiện dạng "abcd efgh ..."; bỏ dấu cách cho chắc.
            wp_app_password=(e.get("WP_APP_PASSWORD") or "").replace(" ", "").strip(),
            anthropic_api_key=(e.get("ANTHROPIC_API_KEY") or "").strip(),
            openai_api_key=(e.get("OPENAI_API_KEY") or "").strip(),
            openai_model=(e.get("WEB_OPENAI_MODEL") or "gpt-5.5").strip(),
            ai_nha_cung_cap=ncc if (ncc := (e.get("WEB_AI_NHA_CUNG_CAP") or "").strip().lower()) in ("claude", "openai", "mau") else "",
            ai_model=(e.get("WEB_AI_MODEL") or "claude-opus-5-5").strip(),
            ai_effort=effort,
            ai_tra_web=_bool(e.get("WEB_AI_TRA_WEB"), True),
            tach_nen=_bool(e.get("WEB_TACH_NEN"), True),
            anh_kich_thuoc=max(600, min(2400, _int(e.get("WEB_ANH_KICH_THUOC"), 1200))),
            anh_dinh_dang=dinh_dang,
            dang_gia=_bool(e.get("WEB_DANG_GIA"), True),
            vat_phan_tram=max(0, min(20, _int(e.get("WEB_VAT_PHAN_TRAM"), 0))),
            hotline=(e.get("WEB_HOTLINE") or "").strip(),
            chuyen_muc_bai=(e.get("WEB_CHUYEN_MUC_BAI") or "").strip(),
            thu_muc=Path(e.get("WEB_THU_MUC_TRONG") or "/data"),
        )

    # Các thư mục làm việc trên Z2 (~/THANHDUC-WEB gắn vào /data trong container).
    @property
    def anh_goc(self) -> Path:
        return self.thu_muc / "anh-goc"

    @property
    def anh_bai(self) -> Path:
        return self.thu_muc / "anh-bai"

    @property
    def ghi_chu(self) -> Path:
        return self.thu_muc / "ghi-chu"

    @property
    def anh_da_xu_ly(self) -> Path:
        return self.thu_muc / "anh-da-xu-ly"

    @property
    def xem_truoc(self) -> Path:
        return self.thu_muc / "xem-truoc"

    @property
    def ket_qua(self) -> Path:
        return self.thu_muc / "ket-qua"

    @property
    def file_chu_de(self) -> Path:
        return self.thu_muc / "chu-de.txt"

    @property
    def file_nhom_danh_muc(self) -> Path:
        return self.thu_muc / "nhom-danh-muc.json"

    @property
    def file_trang_thai(self) -> Path:
        return self.thu_muc / "da-dang.json"

    def tao_thu_muc(self) -> None:
        for p in (self.anh_goc, self.anh_bai, self.ghi_chu, self.anh_da_xu_ly, self.xem_truoc, self.ket_qua):
            p.mkdir(parents=True, exist_ok=True)

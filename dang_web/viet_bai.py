"""Nhờ Claude viết mô tả sản phẩm và bài viết (tiếng Việt, chuẩn SEO). Kết quả trả về dạng JSON theo khuôn cố định."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .du_lieu import SanPham

# Giá tham khảo USD / 1 triệu token (đầu vào, đầu ra) để ước tính chi phí in ra cho anh xem.
GIA_MODEL = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}
TY_GIA_VND = 26_000
GIA_TRA_WEB_USD = 0.01  # mỗi lượt tìm trên web

THE_HTML_DUOC_DUNG = "h2, h3, p, ul, ol, li, strong, em, table, thead, tbody, tr, th, td, br"

HE_THONG = """Bạn là người viết nội dung cho website thanhduc.vn của Công ty TNHH Thương mại Dịch vụ Tin Học Thành Đức (TP. Hồ Chí Minh).
Thành Đức bán máy in mới và đã qua sử dụng, hộp mực, mực in, linh kiện; sửa chữa, bảo trì máy in, nạp mực tận nơi và cung cấp mực định kỳ cho văn phòng.

Nguyên tắc bắt buộc:
- Viết tiếng Việt có dấu, giọng tư vấn thân thiện, rõ ràng, chuyên nghiệp; câu ngắn, dễ đọc trên điện thoại.
- KHÔNG bịa thông số kỹ thuật, máy tương thích, số trang in, xuất xứ. Chỉ dùng thông tin được cung cấp, thông tin tìm được trên web từ nguồn đáng tin, hoặc kiến thức phổ biến chắc chắn. Chỗ nào không chắc thì viết chung chung và ghi vào "can_kiem_tra".
- Không ghi giá tiền trong nội dung (giá hiển thị riêng). Không hứa khuyến mãi. Không nêu tên hay hạ thấp đối thủ.
- Chỉ dùng các thẻ HTML: """ + THE_HTML_DUOC_DUNG + """. Không dùng h1, không style, không script, không link ngoài.
- Từ khóa chính xuất hiện tự nhiên trong tiêu đề SEO, mô tả SEO, đoạn mở đầu và ít nhất một thẻ h2; không nhồi từ khóa.
- seo_title tối đa 60 ký tự, seo_description 120–155 ký tự, slug không dấu, nối bằng gạch ngang."""

KHUON_SAN_PHAM = {
    "type": "object",
    "properties": {
        "ten_web": {"type": "string", "description": "Tên sản phẩm hiển thị trên web, rõ hãng và mã, tối đa 80 ký tự"},
        "slug": {"type": "string"},
        "tu_khoa_chinh": {"type": "string"},
        "seo_title": {"type": "string"},
        "seo_description": {"type": "string"},
        "mo_ta_ngan_html": {"type": "string", "description": "3–5 gạch đầu dòng <ul><li> nêu điểm chính"},
        "mo_ta_html": {
            "type": "string",
            "description": "Mô tả chi tiết 400–700 từ: giới thiệu, thông số/tương thích, ưu điểm, hướng dẫn dùng hoặc bảo quản, dịch vụ tại Thành Đức, 3 câu hỏi thường gặp",
        },
        "alt_anh": {"type": "string", "description": "Mô tả ảnh ngắn gọn có từ khóa"},
        "can_kiem_tra": {"type": "array", "items": {"type": "string"}, "description": "Những điểm người duyệt nên kiểm tra lại"},
    },
    "required": ["ten_web", "slug", "tu_khoa_chinh", "seo_title", "seo_description", "mo_ta_ngan_html", "mo_ta_html", "alt_anh", "can_kiem_tra"],
    "additionalProperties": False,
}

KHUON_BAI_VIET = {
    "type": "object",
    "properties": {
        "tieu_de": {"type": "string"},
        "slug": {"type": "string"},
        "tu_khoa_chinh": {"type": "string"},
        "seo_title": {"type": "string"},
        "seo_description": {"type": "string"},
        "tom_tat": {"type": "string", "description": "Đoạn tóm tắt 1–2 câu dùng làm excerpt"},
        "noi_dung_html": {
            "type": "string",
            "description": "Bài 900–1500 từ: mở đầu nêu vấn đề, các phần h2/h3, các bước có đánh số khi hướng dẫn, khi nào nên gọi kỹ thuật, kết bài mời liên hệ Thành Đức",
        },
        "the": {"type": "array", "items": {"type": "string"}, "description": "3–6 thẻ (tag) ngắn"},
        "alt_anh": {"type": "string"},
        "can_kiem_tra": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tieu_de", "slug", "tu_khoa_chinh", "seo_title", "seo_description", "tom_tat", "noi_dung_html", "the", "alt_anh", "can_kiem_tra"],
    "additionalProperties": False,
}

_THE_CAM = re.compile(r"<\s*(script|style|iframe|h1)\b[^>]*>.*?<\s*/\s*\1\s*>", re.I | re.S)
_THUOC_TINH_CAM = re.compile(r"\s(on\w+|style)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)


def lam_sach_html(s: str) -> str:
    s = _THE_CAM.sub("", s or "")
    return _THUOC_TINH_CAM.sub("", s).strip()


@dataclass
class KetQuaAI:
    du_lieu: dict
    token_vao: int = 0
    token_ra: int = 0
    so_lan_tra_web: int = 0
    model: str = ""
    ghi_chu: list[str] = field(default_factory=list)

    @property
    def chi_phi_vnd(self) -> int:
        vao, ra = GIA_MODEL.get(self.model, GIA_MODEL["claude-opus-5-5"])
        usd = self.token_vao / 1e6 * vao + self.token_ra / 1e6 * ra + self.so_lan_tra_web * GIA_TRA_WEB_USD
        return int(round(usd * TY_GIA_VND, -2))


def yeu_cau_san_pham(sp: SanPham, ghi_chu: str, so_anh: int, hotline: str = "") -> str:
    thong_tin = {
        "ma_hang": sp.code,
        "ten_trong_kho": sp.name,
        "nhom_hang": sp.group_name,
        "don_vi": sp.unit,
        "bao_hanh_thang": sp.warranty_months,
        "con_hang": sp.con_hang,
        "so_anh": so_anh,
    }
    phan = [
        "Viết trang sản phẩm WooCommerce cho mặt hàng sau.",
        "Thông tin từ kho:",
        json.dumps(thong_tin, ensure_ascii=False, indent=2),
    ]
    if ghi_chu.strip():
        phan += ["Ghi chú thêm của Thành Đức (tin cậy, ưu tiên dùng):", ghi_chu.strip()]
    if sp.warranty_months:
        phan.append(f"Nêu rõ bảo hành {sp.warranty_months} tháng tại Thành Đức.")
    if hotline:
        phan.append(f"Cuối mô tả mời khách gọi hotline {hotline} để được tư vấn.")
    phan.append("Tên trong kho có thể viết tắt; hãy viết tên web đầy đủ, đúng hãng và mã máy/mực.")
    return "\n\n".join(phan)


def yeu_cau_bai_viet(chu_de: str, ghi_chu: str = "", hotline: str = "") -> str:
    phan = [f"Viết một bài blog hữu ích cho khách hàng văn phòng tại TP.HCM với chủ đề: {chu_de}"]
    if ghi_chu.strip():
        phan += ["Ghi chú thêm của Thành Đức:", ghi_chu.strip()]
    if hotline:
        phan.append(f"Kết bài mời khách gọi hotline {hotline} nếu cần kỹ thuật hỗ trợ.")
    return "\n\n".join(phan)


class NguoiViet:
    def __init__(self, api_key: str, model: str = "claude-opus-5-5", effort: str = "medium", tra_web: bool = True, client=None):
        if client is None:
            import anthropic

            if not api_key:
                raise RuntimeError("Chưa có ANTHROPIC_API_KEY.")
            client = anthropic.Anthropic(api_key=api_key, max_retries=3)
        self.client = client
        self.model = model
        self.effort = effort
        self.tra_web = tra_web

    def _goi(self, yeu_cau: str, khuon: dict) -> KetQuaAI:
        messages: list[dict] = [{"role": "user", "content": yeu_cau}]
        kq = KetQuaAI(du_lieu={}, model=self.model)
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}] if self.tra_web else None
        resp = None
        for _ in range(4):
            kw = dict(
                model=self.model,
                max_tokens=16000,
                system=HE_THONG,
                messages=messages,
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": khuon}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if tools:
                kw["tools"] = tools
            try:
                resp = self.client.beta.messages.create(**kw)
            except Exception as e:  # tra web hoặc fallback không dùng được thì thử lại bản đơn giản
                if not _la_loi_400(e):
                    raise
                kq.ghi_chu.append(f"Gọi AI lần đầu lỗi ({e.__class__.__name__}); thử lại không tra web.")
                tools = None
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    system=HE_THONG,
                    messages=messages,
                    output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": khuon}},
                )
            _cong_usage(kq, resp)
            if resp.stop_reason == "pause_turn":
                messages = messages + [{"role": "assistant", "content": resp.content}]
                continue
            break
        if resp is None:
            raise RuntimeError("AI không trả lời.")
        if resp.stop_reason == "refusal":
            raise RuntimeError("AI từ chối viết nội dung này; đổi cách mô tả rồi thử lại.")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Bài AI viết bị cắt giữa chừng (quá dài); thử lại.")
        van_ban = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        if not van_ban:
            raise RuntimeError("AI không trả nội dung.")
        try:
            kq.du_lieu = json.loads(van_ban[-1])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"AI trả nội dung không đúng khuôn: {e}") from e
        thieu = [k for k in khuon["required"] if k not in kq.du_lieu]
        if thieu:
            raise RuntimeError(f"AI trả thiếu mục: {', '.join(thieu)}")
        return kq

    def viet_san_pham(self, sp: SanPham, ghi_chu: str = "", so_anh: int = 0, hotline: str = "") -> KetQuaAI:
        kq = self._goi(yeu_cau_san_pham(sp, ghi_chu, so_anh, hotline), KHUON_SAN_PHAM)
        d = kq.du_lieu
        d["mo_ta_html"] = lam_sach_html(d["mo_ta_html"])
        d["mo_ta_ngan_html"] = lam_sach_html(d["mo_ta_ngan_html"])
        return kq

    def viet_bai(self, chu_de: str, ghi_chu: str = "", hotline: str = "") -> KetQuaAI:
        kq = self._goi(yeu_cau_bai_viet(chu_de, ghi_chu, hotline), KHUON_BAI_VIET)
        kq.du_lieu["noi_dung_html"] = lam_sach_html(kq.du_lieu["noi_dung_html"])
        return kq


def _la_loi_400(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 400


def _cong_usage(kq: KetQuaAI, resp) -> None:
    u = getattr(resp, "usage", None)
    if not u:
        return
    kq.token_vao += int(getattr(u, "input_tokens", 0) or 0) + int(getattr(u, "cache_read_input_tokens", 0) or 0)
    kq.token_ra += int(getattr(u, "output_tokens", 0) or 0)
    stu = getattr(u, "server_tool_use", None)
    kq.so_lan_tra_web += int(getattr(stu, "web_search_requests", 0) or 0) if stu else 0

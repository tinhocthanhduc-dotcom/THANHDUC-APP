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
- seo_title tối đa 60 ký tự, seo_description 120–155 ký tự, slug không dấu, nối bằng gạch ngang.
- KHÔNG nhắc số lượng ảnh hay "xem ảnh để kiểm tra". KHÔNG nhắc tình trạng kho, còn hàng, hết hàng, tồn kho (web tự hiện).
- Nếu ghi đơn vị tính thì ghi một gạch đầu dòng riêng đúng dạng "Đơn vị tính: <đơn vị>", không thêm câu nào khác vào dòng đó."""

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
        if self.model not in GIA_MODEL:
            return 0  # model ngoài bảng giá (ví dụ ChatGPT): xem chi phí trên trang OpenAI
        vao, ra = GIA_MODEL[self.model]
        usd = self.token_vao / 1e6 * vao + self.token_ra / 1e6 * ra + self.so_lan_tra_web * GIA_TRA_WEB_USD
        return int(round(usd * TY_GIA_VND, -2))


    @property
    def mo_ta_chi_phi(self) -> str:
        if self.chi_phi_vnd:
            return "~" + f"{self.chi_phi_vnd:,}".replace(",", ".") + "đ"
        if not self.token_vao and not self.token_ra:
            return "0đ"
        vao, ra = (f"{n:,}".replace(",", ".") for n in (self.token_vao, self.token_ra))
        return f"{vao} token vào, {ra} token ra (xem giá trên trang nhà cung cấp)"


_CAU_CAM = re.compile(
    r"[^.!?<>]*(?:\d+\s*(?:ảnh|hình)|(?:xem|kiểm tra)[^.!?<>]{0,20}(?:ảnh|hình)|"
    r"(?:ảnh|hình ảnh) sản phẩm để|chưa có hàng|còn hàng|hết hàng|tồn kho|"
    r"kho[^.!?<>]{0,15}(?:ghi nhận|chưa có|còn|hết))[^.!?<>]*[.!?]?",
    re.I,
)
_LI = re.compile(r"<li>(.*?)</li>", re.I | re.S)


def bo_cau_thua(html: str, don_vi: str = "") -> str:
    """Bỏ câu nhắc số ảnh / tình trạng kho; dòng "Đơn vị tính" chỉ giữ đúng đơn vị (theme lấy dòng này làm Thông tin nhanh)."""

    def li(m: re.Match) -> str:
        noi = m.group(1)
        if re.search(r"đơn vị tính", noi, re.I):
            return f"<li>Đơn vị tính: {don_vi.strip()}</li>" if don_vi.strip() else ""
        noi = _CAU_CAM.sub("", noi).strip()
        return f"<li>{noi}</li>" if re.sub(r"<[^>]+>", "", noi).strip() else ""

    html = _LI.sub(li, html or "")
    html = re.sub(r"(<p>)(.*?)(</p>)", lambda m: m.group(1) + _CAU_CAM.sub("", m.group(2)).strip() + m.group(3), html, flags=re.I | re.S)
    html = re.sub(r"<p>\s*</p>|<ul>\s*</ul>", "", html, flags=re.I)
    return html.strip()


def don_san_pham(d: dict, sp: SanPham) -> None:
    for k in ("mo_ta_ngan_html", "mo_ta_html"):
        d[k] = bo_cau_thua(lam_sach_html(d.get(k, "")), sp.unit or "")


def yeu_cau_san_pham(sp: SanPham, ghi_chu: str, so_anh: int, hotline: str = "") -> str:
    thong_tin = {
        "ma_hang": sp.code,
        "ten_trong_kho": sp.name,
        "nhom_hang": sp.group_name,
        "don_vi": sp.unit,
        "bao_hanh_thang": sp.warranty_months,
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


# ---------- sửa bài / sản phẩm đang có trên web ----------
_TOI_DA_HTML = 12000

KHUON_XEP = {
    "type": "object",
    "properties": {
        "ket_qua": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "danh_muc": {"type": "string", "description": "Đúng nguyên văn một dòng trong danh sách danh mục; không chắc thì để trống"},
                    "chac": {"type": "boolean"},
                },
                "required": ["id", "danh_muc", "chac"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ket_qua"],
    "additionalProperties": False,
}


def don_vi_trong(html: str) -> str:
    """Đơn vị tính đang ghi trên web ("Đơn vị tính: cái") để giữ nguyên khi viết lại."""
    m = re.search(r"đơn vị tính\s*:\s*([^<\n]{1,20})", html or "", re.I)
    return m.group(1).strip() if m else ""


def yeu_cau_sua_san_pham(hien_tai: dict, sp: SanPham | None, ghi_chu: str = "", hotline: str = "") -> str:
    phan = [
        "Viết lại trang sản phẩm WooCommerce ĐANG CÓ trên thanhduc.vn cho rõ ràng, đầy đủ và chuẩn SEO hơn.",
        "Giữ đúng sản phẩm, đúng thông số và máy tương thích đang ghi (không bịa thêm); bỏ câu trùng lặp, sửa lỗi chính tả.",
        "Nội dung hiện tại trên web:",
        json.dumps({k: (hien_tai.get(k) or "")[:_TOI_DA_HTML] for k in ("ten_web", "mo_ta_ngan_html", "mo_ta_html", "danh_muc")},
                   ensure_ascii=False, indent=2),
    ]
    if sp:
        phan += ["Thông tin từ kho (tin cậy):", json.dumps({"ma_hang": sp.code, "ten_trong_kho": sp.name, "nhom_hang": sp.group_name,
                                                            "don_vi": sp.unit, "bao_hanh_thang": sp.warranty_months}, ensure_ascii=False)]
        if sp.warranty_months:
            phan.append(f"Nêu rõ bảo hành {sp.warranty_months} tháng tại Thành Đức.")
    if ghi_chu.strip():
        phan += ["Ghi chú thêm của Thành Đức (tin cậy, ưu tiên dùng):", ghi_chu.strip()]
    if hotline:
        phan.append(f"Cuối mô tả mời khách gọi hotline {hotline} để được tư vấn.")
    phan.append("Tên web giữ gần tên hiện tại nếu đã đúng hãng và mã; slug giữ như cũ nếu đã ổn.")
    return "\n\n".join(phan)


def yeu_cau_sua_bai(hien_tai: dict, ghi_chu: str = "", hotline: str = "") -> str:
    phan = [
        "Viết lại bài blog ĐANG CÓ trên thanhduc.vn cho hữu ích, dễ đọc và chuẩn SEO hơn.",
        "Giữ đúng chủ đề và các ý đúng của bài cũ, bổ sung phần còn thiếu; không bịa số liệu.",
        "Bài hiện tại:",
        json.dumps({k: (hien_tai.get(k) or "")[:_TOI_DA_HTML] for k in ("tieu_de", "tom_tat", "noi_dung_html", "danh_muc")},
                   ensure_ascii=False, indent=2),
    ]
    if ghi_chu.strip():
        phan += ["Ghi chú thêm của Thành Đức:", ghi_chu.strip()]
    if hotline:
        phan.append(f"Kết bài mời khách gọi hotline {hotline} nếu cần kỹ thuật hỗ trợ.")
    return "\n\n".join(phan)


def yeu_cau_xep_danh_muc(loai: str, muc: list[dict], danh_muc: list[str]) -> str:
    ten_loai = "sản phẩm" if loai == "san_pham" else "bài viết"
    return "\n\n".join([
        f"Xếp mỗi {ten_loai} dưới đây vào ĐÚNG MỘT danh mục có sẵn trên website thanhduc.vn.",
        "Chỉ chọn trong danh sách danh mục (ghi nguyên văn cả dòng, kể cả dấu ›). Không tạo danh mục mới. "
        "Chọn danh mục cụ thể nhất phù hợp. Không chắc thì chac=false và danh_muc để trống.",
        "Danh mục:", "\n".join(danh_muc),
        f"Danh sách {ten_loai} (id, tên):", json.dumps([{"id": m["id"], "ten": m.get("ten", "")} for m in muc], ensure_ascii=False),
    ])


def _don_sua_sp(d: dict, hien_tai: dict, sp: SanPham | None) -> None:
    don_vi = (sp.unit if sp else "") or don_vi_trong(hien_tai.get("mo_ta_ngan_html", "")) or don_vi_trong(hien_tai.get("mo_ta_html", ""))
    for k in ("mo_ta_ngan_html", "mo_ta_html"):
        d[k] = bo_cau_thua(lam_sach_html(d.get(k, "")), don_vi)


class NguoiViet:
    def __init__(self, api_key: str, model: str = "claude-opus-5-5", effort: str = "medium", tra_web: bool = True, client=None):
        if client is None:
            import anthropic

            if not api_key:
                raise RuntimeError("Chưa có khóa AI: đặt OPENAI_API_KEY hoặc ANTHROPIC_API_KEY trong deploy/.env.")
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
        don_san_pham(kq.du_lieu, sp)
        return kq

    def viet_bai(self, chu_de: str, ghi_chu: str = "", hotline: str = "") -> KetQuaAI:
        kq = self._goi(yeu_cau_bai_viet(chu_de, ghi_chu, hotline), KHUON_BAI_VIET)
        kq.du_lieu["noi_dung_html"] = lam_sach_html(kq.du_lieu["noi_dung_html"])
        return kq

    def sua_san_pham(self, hien_tai: dict, sp: SanPham | None = None, ghi_chu: str = "", hotline: str = "") -> KetQuaAI:
        kq = self._goi(yeu_cau_sua_san_pham(hien_tai, sp, ghi_chu, hotline), KHUON_SAN_PHAM)
        _don_sua_sp(kq.du_lieu, hien_tai, sp)
        return kq

    def sua_bai(self, hien_tai: dict, ghi_chu: str = "", hotline: str = "") -> KetQuaAI:
        kq = self._goi(yeu_cau_sua_bai(hien_tai, ghi_chu, hotline), KHUON_BAI_VIET)
        kq.du_lieu["noi_dung_html"] = lam_sach_html(kq.du_lieu["noi_dung_html"])
        return kq

    def xep_danh_muc(self, loai: str, muc: list[dict], danh_muc: list[str]) -> KetQuaAI:
        return self._goi(yeu_cau_xep_danh_muc(loai, muc, danh_muc), KHUON_XEP)


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


class NguoiVietOpenAI(NguoiViet):
    """Cùng cách viết, cùng khuôn JSON, nhưng gọi ChatGPT qua OpenAI Responses API (anh đã có khóa OpenAI)."""

    def __init__(self, api_key: str, model: str = "gpt-5.5", effort: str = "medium", tra_web: bool = True, client=None):
        if client is None:
            import openai

            if not api_key:
                raise RuntimeError("Chưa có OPENAI_API_KEY.")
            client = openai.OpenAI(api_key=api_key, max_retries=3)
        self.client = client
        self.model = model
        self.effort = "high" if effort in ("xhigh", "max") else effort
        self.tra_web = tra_web

    def _goi(self, yeu_cau: str, khuon: dict) -> KetQuaAI:
        kq = KetQuaAI(du_lieu={}, model=self.model)
        kw = dict(
            model=self.model,
            instructions=HE_THONG,
            input=yeu_cau,
            max_output_tokens=16000,
            text={"format": {"type": "json_schema", "name": "noi_dung", "schema": khuon, "strict": True}},
            reasoning={"effort": self.effort},
        )
        if self.tra_web:
            kw["tools"] = [{"type": "web_search"}]
        try:
            resp = self.client.responses.create(**kw)
        except Exception as e:  # model không hỗ trợ tra web / reasoning thì thử bản đơn giản
            if not _la_loi_400(e):
                raise
            kq.ghi_chu.append(f"Gọi AI lần đầu lỗi ({e.__class__.__name__}); thử lại không tra web.")
            kw.pop("tools", None)
            kw.pop("reasoning", None)
            resp = self.client.responses.create(**kw)
        u = getattr(resp, "usage", None)
        if u:
            kq.token_vao += int(getattr(u, "input_tokens", 0) or 0)
            kq.token_ra += int(getattr(u, "output_tokens", 0) or 0)
        for item in getattr(resp, "output", []) or []:
            if getattr(item, "type", "") == "web_search_call":
                kq.so_lan_tra_web += 1
            for c in getattr(item, "content", None) or []:
                if getattr(c, "type", "") == "refusal":
                    raise RuntimeError("AI từ chối viết nội dung này; đổi cách mô tả rồi thử lại.")
        if getattr(resp, "status", "completed") == "incomplete":
            raise RuntimeError("Bài AI viết bị cắt giữa chừng (quá dài); thử lại.")
        van_ban = (getattr(resp, "output_text", "") or "").strip()
        if not van_ban:
            raise RuntimeError("AI không trả nội dung.")
        try:
            kq.du_lieu = json.loads(van_ban)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"AI trả nội dung không đúng khuôn: {e}") from e
        thieu = [k for k in khuon["required"] if k not in kq.du_lieu]
        if thieu:
            raise RuntimeError(f"AI trả thiếu mục: {', '.join(thieu)}")
        return kq


class NguoiVietMau:
    """Không dùng AI, miễn phí: điền thông tin kho vào khung có sẵn. Bài nào cũng giống nhau nên SEO yếu hơn."""

    model = "mẫu cố định"

    def viet_san_pham(self, sp: SanPham, ghi_chu: str = "", so_anh: int = 0, hotline: str = "") -> KetQuaAI:
        from html import escape

        ten = sp.name.strip()
        e = escape(ten)
        diem = [f"Mã hàng: {escape(sp.code)}", f"Nhóm: {escape(sp.group_name or 'Thiết bị văn phòng')}"]
        if sp.warranty_months:
            diem.append(f"Bảo hành {sp.warranty_months} tháng tại Thành Đức")
        if (sp.unit or "").strip():
            diem.append(f"Đơn vị tính: {escape(sp.unit.strip())}")
        diem.append("Giao hàng nhanh nội thành TP.HCM, hỗ trợ kỹ thuật tận nơi")
        ngan = "<ul>" + "".join(f"<li>{d}</li>" for d in diem) + "</ul>"
        dai = [f"<h2>Giới thiệu {e}</h2>", f"<p>{e} được Tin Học Thành Đức phân phối và hỗ trợ kỹ thuật tại TP. Hồ Chí Minh.</p>"]
        if ghi_chu.strip():
            dai += ["<h2>Thông tin sản phẩm</h2>"] + [f"<p>{escape(x.strip())}</p>" for x in ghi_chu.splitlines() if x.strip()]
        dai += [
            "<h2>Dịch vụ tại Thành Đức</h2>",
            "<ul><li>Tư vấn chọn đúng sản phẩm cho máy của khách</li><li>Giao hàng và lắp đặt tận nơi</li>"
            "<li>Sửa chữa, bảo trì máy in, nạp mực, cung cấp mực định kỳ cho văn phòng</li></ul>",
        ]
        if hotline:
            dai.append(f"<p>Gọi <strong>{escape(hotline)}</strong> để được tư vấn.</p>")
        mo_ta_seo = f"{ten} chính hãng tại Thành Đức, giao nhanh TP.HCM, hỗ trợ kỹ thuật tận nơi."
        d = {
            "ten_web": ten, "slug": ten, "tu_khoa_chinh": ten.lower(),
            "seo_title": f"{ten} | Thành Đức"[:60], "seo_description": mo_ta_seo[:155],
            "mo_ta_ngan_html": ngan, "mo_ta_html": "".join(dai), "alt_anh": ten,
            "can_kiem_tra": ["Nội dung theo mẫu cố định: nên bổ sung thông số, máy tương thích trước khi đăng"],
        }
        return KetQuaAI(du_lieu=d, model=self.model)

    def viet_bai(self, chu_de: str, ghi_chu: str = "", hotline: str = "") -> KetQuaAI:
        raise RuntimeError("Bài viết cần AI; chế độ không AI chỉ dùng cho sản phẩm.")

    def sua_san_pham(self, *a, **kw) -> KetQuaAI:
        raise RuntimeError("Sửa bằng AI cần AI tự động hoặc ChatGPT web.")

    sua_bai = sua_san_pham

    def xep_danh_muc(self, *a, **kw) -> KetQuaAI:
        raise RuntimeError("Xếp danh mục cần khóa AI (OPENAI_API_KEY hoặc ANTHROPIC_API_KEY).")


class ChoTraLoi(Exception):
    """Đã soạn câu lệnh cho ChatGPT web; chờ anh dán câu trả lời vào file."""

    def __init__(self, file_lenh, file_tra_loi):
        super().__init__(str(file_lenh))
        self.file_lenh = file_lenh
        self.file_tra_loi = file_tra_loi


def _mo_ta_khuon(khuon: dict) -> str:
    dong = []
    for k, v in khuon["properties"].items():
        kieu = "danh sách chuỗi" if v.get("type") == "array" else "chuỗi"
        dong.append(f'- "{k}" ({kieu}){": " + v["description"] if v.get("description") else ""}')
    return "\n".join(dong)


def doc_json_tu_van_ban(van_ban: str) -> dict:
    """Câu trả lời dán từ ChatGPT web có thể kèm lời dẫn hoặc ```json; lấy khối {...} ngoài cùng."""
    dau, cuoi = van_ban.find("{"), van_ban.rfind("}")
    if dau < 0 or cuoi <= dau:
        raise RuntimeError("Câu trả lời không có khối JSON { ... }.")
    try:
        return json.loads(van_ban[dau : cuoi + 1])
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Câu trả lời dán vào không đúng JSON: {e}") from e


class NguoiVietChatGPTWeb:
    """Bán thủ công, không tốn phí API: soạn câu lệnh ra file, anh dán vào ChatGPT web rồi dán câu trả lời lại."""

    model = "ChatGPT web (dán tay)"

    def __init__(self, thu_muc):
        from pathlib import Path

        self.thu_muc = Path(thu_muc)

    def _lay(self, ten: str, yeu_cau: str, khuon: dict) -> KetQuaAI:
        self.thu_muc.mkdir(parents=True, exist_ok=True)
        file_lenh = self.thu_muc / f"{ten}.txt"
        file_tra_loi = self.thu_muc / f"{ten}-tra-loi.txt"
        if file_tra_loi.exists() and file_tra_loi.read_text(encoding="utf-8").strip():
            d = doc_json_tu_van_ban(file_tra_loi.read_text(encoding="utf-8"))
            thieu = [k for k in khuon["required"] if k not in d]
            if thieu:
                raise RuntimeError(f"Câu trả lời thiếu mục: {', '.join(thieu)}")
            for k, v in khuon["properties"].items():
                if v.get("type") == "array" and not isinstance(d[k], list):
                    d[k] = [str(d[k])]
            return KetQuaAI(du_lieu=d, model=self.model)
        lenh = "\n\n".join([
            HE_THONG,
            yeu_cau,
            "Hãy tra cứu web nếu cần để lấy đúng thông số. Trả lời CHỈ bằng MỘT khối JSON (không giải thích thêm) gồm đủ các khóa sau:",
            _mo_ta_khuon(khuon),
        ])
        file_lenh.write_text(lenh, encoding="utf-8")
        raise ChoTraLoi(file_lenh, file_tra_loi)

    def viet_san_pham(self, sp: SanPham, ghi_chu: str = "", so_anh: int = 0, hotline: str = "") -> KetQuaAI:
        kq = self._lay(sp.code, yeu_cau_san_pham(sp, ghi_chu, so_anh, hotline), KHUON_SAN_PHAM)
        don_san_pham(kq.du_lieu, sp)
        return kq

    def viet_bai(self, chu_de: str, ghi_chu: str = "", hotline: str = "") -> KetQuaAI:
        from .wordpress import slug

        kq = self._lay("bai-" + slug(chu_de, 60), yeu_cau_bai_viet(chu_de, ghi_chu, hotline), KHUON_BAI_VIET)
        kq.du_lieu["noi_dung_html"] = lam_sach_html(kq.du_lieu["noi_dung_html"])
        return kq

    def sua_san_pham(self, hien_tai: dict, sp: SanPham | None = None, ghi_chu: str = "", hotline: str = "") -> KetQuaAI:
        kq = self._lay("sua-sp", yeu_cau_sua_san_pham(hien_tai, sp, ghi_chu, hotline), KHUON_SAN_PHAM)
        _don_sua_sp(kq.du_lieu, hien_tai, sp)
        return kq

    def sua_bai(self, hien_tai: dict, ghi_chu: str = "", hotline: str = "") -> KetQuaAI:
        kq = self._lay("sua-bai", yeu_cau_sua_bai(hien_tai, ghi_chu, hotline), KHUON_BAI_VIET)
        kq.du_lieu["noi_dung_html"] = lam_sach_html(kq.du_lieu["noi_dung_html"])
        return kq

    def xep_danh_muc(self, *a, **kw) -> KetQuaAI:
        raise RuntimeError("Xếp danh mục cần khóa AI (OPENAI_API_KEY hoặc ANTHROPIC_API_KEY).")


def chon_nguoi_viet(ch):
    """WEB_AI_NHA_CUNG_CAP=claude|openai|mau; để trống thì dùng loại nào có khóa (ưu tiên Claude)."""
    ncc = ch.ai_nha_cung_cap
    if ncc == "mau":
        return NguoiVietMau()
    if not ncc:
        ncc = "claude" if ch.anthropic_api_key else ("openai" if ch.openai_api_key else "claude")
    if ncc == "openai":
        return NguoiVietOpenAI(ch.openai_api_key, ch.openai_model, ch.ai_effort, ch.ai_tra_web)
    return NguoiViet(ch.anthropic_api_key, ch.ai_model, ch.ai_effort, ch.ai_tra_web)

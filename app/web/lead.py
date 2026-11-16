"""Ô nguồn khách trên báo giá / đơn bán cho Growth OS (TUẤN duyệt 16/11/2026, mẫu Awbm3EFjRHr8jPTUkscGN2).

Không bắt buộc. Nguồn = ô xổ xuống 9 mục; mã chiến dịch gõ tự do, app viết hoa, đối chiếu với Growth OS sau;
mã đơn web/sàn chỉ hiện khi nguồn là Web, Shopee, TikTok Shop.
"""
SOURCES = (('WEB', 'Web'), ('GOOGLE_MAPS', 'Google Maps'), ('FACEBOOK', 'Facebook'), ('ZALO', 'Zalo'),
           ('SHOPEE', 'Shopee'), ('TIKTOK_SHOP', 'TikTok Shop'), ('KHACH_CU', 'Khách cũ'), ('GIOI_THIEU', 'Giới thiệu'),
           ('KHAC', 'Khác'))
LABELS = dict(SOURCES)
MARKET = ('WEB', 'SHOPEE', 'TIKTOK_SHOP')
QUOTE_FIELDS = ('lead_source', 'campaign_code', 'lead_ref')
ORDER_FIELDS = QUOTE_FIELDS + ('web_order_ref',)


def raw(form, order=False) -> dict:
    """Chuỗi người gõ. Form không có khối nguồn khách (ô lead_source) thì trả {} để giữ nguyên giá trị cũ."""
    if 'lead_source' not in form:
        return {}
    out = {f: str(form.get(f, '') or '').strip() for f in (ORDER_FIELDS if order else QUOTE_FIELDS)}
    if order and out.get('lead_source') not in MARKET:
        out['web_order_ref'] = ''  # ô bị ẩn: không giữ mã cũ đã gõ trước khi đổi nguồn
    return out


def label(code) -> str:
    return LABELS.get(code or '', '')

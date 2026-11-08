"""QR chuyển khoản chuẩn VietQR (NAPAS 247, EMVCo) — đợt 1, TUẤN duyệt 08/11/2026.

Chỉ dựng chuỗi và vẽ ảnh; không ghi sổ, không trừ nợ. Tài khoản nhận là quỹ đang ghép
"Thu chuyển khoản" (finance_mappings.THU_CHUYEN_KHOAN) có đủ BIN, số tài khoản, chủ tài khoản.
Nội dung chuyển khoản chuẩn `TD DB2600123` để đợt sau khớp sao kê tự động.
"""
import re
import unicodedata

from sqlalchemy.orm import Session

from .models import Fund, FinanceMapping

# Mã BIN theo danh sách NAPAS (6 số). Tên ngắn để chọn trên màn Quỹ.
BANKS = [
    ('970436', 'Vietcombank'), ('970415', 'VietinBank'), ('970418', 'BIDV'), ('970405', 'Agribank'),
    ('970422', 'MB Bank'), ('970407', 'Techcombank'), ('970416', 'ACB'), ('970432', 'VPBank'),
    ('970423', 'TPBank'), ('970403', 'Sacombank'), ('970437', 'HDBank'), ('970441', 'VIB'),
    ('970443', 'SHB'), ('970431', 'Eximbank'), ('970426', 'MSB'), ('970448', 'OCB'),
    ('970429', 'SCB'), ('970412', 'PVcomBank'), ('970414', 'Oceanbank'), ('970428', 'Nam A Bank'),
    ('970427', 'VietABank'), ('970409', 'Bac A Bank'), ('970438', 'BaoViet Bank'), ('970400', 'SaigonBank'),
    ('970406', 'DongA Bank'), ('970430', 'PGBank'), ('970449', 'LPBank'), ('970454', 'VietCapital Bank'),
    ('970452', 'KienlongBank'), ('970440', 'SeABank'), ('970433', 'VietBank'), ('970425', 'ABBank'),
    ('970419', 'NCB'), ('970424', 'Shinhan Bank'), ('970457', 'Woori Bank'), ('970458', 'UOB'),
    ('970439', 'Public Bank'), ('970442', 'Hong Leong'), ('970455', 'IBK'), ('970421', 'VRB'),
    ('546034', 'CAKE by VPBank'), ('546035', 'Ubank by VPBank'), ('963388', 'Timo'), ('971005', 'Viettel Money'),
]
BANK_NAMES = dict(BANKS)

# Loại chứng từ → tiền tố trong nội dung chuyển khoản. KTD = trả nợ gộp theo khách.
CONTENT_PREFIXES = ('DB', 'DV', 'BG', 'BT', 'KTD')
MIN_AMOUNT = 1_000
MAX_AMOUNT = 9_999_999_999_999  # 13 chữ số theo trường 54 EMVCo


def ascii_upper(text: str, limit: int = 50) -> str:
    """Bỏ dấu tiếng Việt, viết hoa, chỉ giữ chữ số và khoảng trắng (ngân hàng không nhận ký tự lạ)."""
    folded = unicodedata.normalize('NFD', str(text or '')).replace('Đ', 'D').replace('đ', 'd')
    folded = ''.join(ch for ch in folded if unicodedata.category(ch) != 'Mn')
    folded = re.sub(r'[^A-Za-z0-9 ]+', ' ', folded)
    return re.sub(r' +', ' ', folded).strip().upper()[:limit]


def content_code(number: str) -> str:
    """`DB-26-00123` → `TD DB2600123`; `KTD-000123` → `TD KTD000123`."""
    compact = re.sub(r'[^A-Za-z0-9]', '', str(number or '')).upper()
    return f'TD {compact}' if compact else 'TD'


_CONTENT = re.compile(r'TD\s*-?\s*(DB|DV|BG|BT|KTD)\s*-?\s*(\d{2})\s*-?\s*(\d{5,6})|TD\s*-?\s*(KTD)\s*-?\s*(\d{6})', re.I)


def parse_content(text: str) -> tuple[str, str] | None:
    """Tìm mã chứng từ trong nội dung sao kê: trả (loại, số chứng từ) như `('DB', 'DB-26-00123')` hoặc None.

    Chấp nhận viết thường, mất khoảng trắng, thêm gạch nối; ngân hàng hay bỏ dấu và ghép chữ.
    """
    m = _CONTENT.search(str(text or ''))
    if not m:
        return None
    if m.group(4):
        return 'KTD', f'KTD-{m.group(5)}'
    kind = m.group(1).upper()
    if kind == 'KTD':
        return 'KTD', f'KTD-{m.group(2)}{m.group(3)}'
    return kind, f'{kind}-{m.group(2)}-{m.group(3)}'


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) theo EMVCo."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def _tlv(tag: str, value: str) -> str:
    if len(value) > 99:
        raise ValueError(f'Trường {tag} quá dài')
    return f'{tag}{len(value):02d}{value}'


def payload(bank_bin: str, account_no: str, amount: int | None = None, content: str = '') -> str:
    """Chuỗi VietQR: QR động khi có số tiền, QR tĩnh khi không."""
    bank_bin = re.sub(r'\D', '', str(bank_bin or ''))
    account_no = re.sub(r'[^A-Za-z0-9]', '', str(account_no or ''))
    if len(bank_bin) != 6 or not account_no:
        raise ValueError('Thiếu mã BIN ngân hàng hoặc số tài khoản')
    if amount is not None and not (MIN_AMOUNT <= int(amount) <= MAX_AMOUNT):
        raise ValueError('Số tiền ngoài khoảng cho phép')
    merchant = _tlv('00', 'A000000727') + _tlv('01', _tlv('00', bank_bin) + _tlv('01', account_no)) + _tlv('02', 'QRIBFTTA')
    parts = [_tlv('00', '01'), _tlv('01', '12' if amount is not None else '11'), _tlv('38', merchant),
             _tlv('52', '0000'), _tlv('53', '704')]
    if amount is not None:
        parts.append(_tlv('54', str(int(amount))))
    parts.append(_tlv('58', 'VN'))
    purpose = ascii_upper(content, 25)
    if purpose:
        parts.append(_tlv('62', _tlv('08', purpose)))
    body = ''.join(parts) + '6304'
    return body + f'{crc16(body.encode("ascii")):04X}'


def svg(data: str, scale: int = 6) -> str:
    """Ảnh SVG của chuỗi QR, vẽ tại chỗ bằng segno (không gọi dịch vụ ngoài)."""
    import segno
    qr = segno.make(data, error='m')
    return qr.svg_inline(scale=scale, border=2, dark='#111111', light='#ffffff', svgclass=None, lineclass=None,
                         omitsize=True)


def company_account(session: Session) -> dict | None:
    """Quỹ ngân hàng đang ghép Thu chuyển khoản, đủ BIN + số TK + chủ TK; thiếu gì trả None."""
    mapping = session.get(FinanceMapping, 'THU_CHUYEN_KHOAN')
    fund = session.get(Fund, mapping.fund_code) if mapping and mapping.fund_code else None
    if fund is None or fund.kind != 'NGAN_HANG' or not fund.active:
        return None
    if not (fund.bank_bin or '').strip() or not (fund.account_no or '').strip() or not (fund.account_name or '').strip():
        return None
    return dict(fund_code=fund.code, bank_bin=fund.bank_bin.strip(), bank_name=BANK_NAMES.get(fund.bank_bin.strip(), fund.bank_name),
                account_no=fund.account_no.strip(), account_name=ascii_upper(fund.account_name, 50))


def bank_line(account: dict | None) -> str:
    """Dòng chuyển khoản dán vào tin nhắn: `Vietcombank 0071000123456 · CTY TNHH TMDV TIN HOC THANH DUC`."""
    if not account:
        return ''
    return f"{account['bank_name']} {account['account_no']} · {account['account_name']}"


def for_document(session: Session, number: str, amount: int | None) -> dict | None:
    """QR cho một chứng từ: None khi chưa cấu hình tài khoản hoặc số tiền không hợp lệ (< 1.000 đ).

    Trả dict(svg, amount, content, bank_name, account_no, account_name, payload, line) cho khuôn in và trang QR.
    """
    account = company_account(session)
    if account is None:
        return None
    if amount is not None and int(amount) < MIN_AMOUNT:
        return None
    content = content_code(number)
    data = payload(account['bank_bin'], account['account_no'], amount, content)
    return dict(account, svg=svg(data), amount=amount, content=content, payload=data,
                line=f"{bank_line(account)} · Nội dung {content}" + (f" · {int(amount):,} đ".replace(',', '.') if amount else ''))

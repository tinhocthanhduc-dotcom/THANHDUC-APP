"""Trợ lý Zalo đợt 3 (TUẤN duyệt mẫu 06/11/2026): đọc ảnh nhân viên gửi bằng Codex/Claude trên máy Z2.

- Chỉ ảnh của nhân viên đã gắn, chat riêng. Ảnh khách gửi không đưa cho AI. Không dùng API dự phòng: Z2 không chạy thì báo.
- AI chỉ ĐỌC chữ trong ảnh (trả JSON). Tra bảo hành, tồn, giá, lập đơn mua nháp là app làm, theo quyền người gửi.
- Tem máy → máy của khách nào, bảo hành. Hộp mực / linh kiện → mã TD, tồn (giá nếu được xem). Hóa đơn NCC → đơn mua
  NHÁP (người lập = người gửi, cần quyền kho), ảnh đính kèm; dòng không chắc mã thì để ở "Chưa ghép mã", không đưa vào đơn.
- Ảnh lưu ở files_dir/zalo_anh/. Ảnh không gắn đơn mua tự xóa sau 30 ngày.
"""
import io
import json
import re
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .errors import BusinessError
from .models import Product, PurchaseOrder, PurchaseOrderLine, Supplier, User, Warehouse, ZaloAgentLog, ZaloAiJob, ZaloChat, ZaloMessage
from .security import has_role

KINDS = {'TEM_MAY': 'Tem máy', 'HOP_MUC': 'Hộp mực / linh kiện', 'HOA_DON': 'Hóa đơn NCC', 'KHAC': 'Ảnh khác'}
MAX_BYTES = 30 * 1024 * 1024  # ảnh HD Zalo có thể vài chục MB
MAX_SIDE = 2000  # đủ nét để đọc chữ nhỏ trên hóa đơn
KEEP_DAYS = 30
FOLDER = 'zalo_anh'
WAIT_TEXT = 'Em đang đọc ảnh, anh/chị chờ khoảng 30 giây đến 1 phút nhé.'
NO_Z2_TEXT = 'Chưa đọc được ảnh: máy Z2 đang không chạy. Anh/chị gửi lại sau hoặc nhắn bằng chữ nhé.'
NO_DOWNLOAD_TEXT = 'Em chưa tải được ảnh từ Zalo. Anh/chị gửi lại ảnh giúp em nhé.'
FAIL_TEXT = 'Em chưa đọc được ảnh này. Anh/chị chụp lại rõ hơn hoặc nhắn bằng chữ giúp em nhé.'

PROMPT = """Bạn đọc MỘT ảnh do nhân viên Công ty Tin Học Thành Đức (máy in, mực in, sửa máy) gửi qua Zalo.
Ảnh nằm ở tệp đính kèm{where}. Chỉ đọc chữ/số NHÌN THẤY trong ảnh. Không đoán, không bịa: chỗ nào mờ, lóa, bị che thì để trống
và ghi vào "chua_ro". Nếu không thấy ảnh nào thì trả {{"loai":"KHAC","ro":false,"chua_ro":"không thấy ảnh"}}.
Chú thích người gửi kèm ảnh: "{caption}"

Xếp ảnh vào một loại:
- TEM_MAY: tem/nhãn trên máy in, máy photo (model, serial/S/N, số máy).
- HOP_MUC: hộp mực, hộp drum, linh kiện, bao bì có mã hàng.
- HOA_DON: hóa đơn/phiếu giao hàng của nhà cung cấp bán cho Thành Đức (có tên NCC, các dòng hàng, số lượng, đơn giá).
- KHAC: ảnh khác.

Chỉ trả về MỘT khối JSON đúng dạng sau (bỏ phần không thuộc loại ảnh), không thêm chữ nào khác:
{{"loai": "TEM_MAY|HOP_MUC|HOA_DON|KHAC", "ro": true, "chua_ro": "",
 "tem": {{"hang": "", "model": "", "serial": ""}},
 "hop": {{"hang": "", "ma": ["mã in trên hộp, ví dụ Q2612A, 12A"], "ten": ""}},
 "hoa_don": {{"ncc": "", "mst": "", "so": "", "ngay": "YYYY-MM-DD", "gia_gom_vat": false, "tong": 0,
   "dong": [{{"ten": "", "ma": "", "dvt": "", "sl": 0, "don_gia": 0, "thue": 10}}]}}}}
Quy ước: "ro" = false nếu thông tin chính (serial, mã hộp, các dòng hóa đơn) không đọc chắc được. "don_gia" là đơn giá in trên
hóa đơn; "gia_gom_vat" = true chỉ khi đơn giá đó đã gồm thuế GTGT. Tiền là số nguyên đồng, không dấu chấm. "thue" là % thuế GTGT
(0, 5, 8, 10) nếu có.
"""


def _now():
    from .zalo_agent import _now as now
    return now()


PHOTO_KEYS = ('photo', 'photo_url', 'image', 'image_url', 'url', 'href', 'src', 'thumb', 'thumbnail', 'link', 'file_url')


def _find_url(node, depth=0) -> str:
    """Tìm link ảnh trong tin Zalo: ưu tiên các khóa tên ảnh, đi sâu vào dict/list (mỗi bản Zalo Bot đặt khác nhau)."""
    if depth > 5:
        return ''
    if isinstance(node, str):
        return node.strip() if node.strip().startswith(('https://', 'http://')) else ''
    if isinstance(node, dict):
        for key in PHOTO_KEYS:
            if key in node:
                found = _find_url(node[key], depth + 1)
                if found:
                    return found
        for key, value in node.items():
            if key in ('from', 'chat', 'sender') or key in PHOTO_KEYS:
                continue
            if isinstance(value, (dict, list)):
                found = _find_url(value, depth + 1)
                if found:
                    return found
    if isinstance(node, list):
        for item in node[:10]:
            found = _find_url(item, depth + 1)
            if found:
                return found
    return ''


def photo_of(msg: ZaloMessage) -> tuple[str, str]:
    """(link ảnh, chú thích) từ tin Zalo đã lưu. Zalo Bot gửi message.photo (+ caption); bản khác có thể lồng sâu hơn."""
    raw = msg.raw if isinstance(msg.raw, dict) else {}
    m = raw.get('message') if isinstance(raw.get('message'), dict) else {}
    return _find_url(m) or _find_url(raw), str(m.get('caption') or '').strip()[:500]


def raw_keys(msg: ZaloMessage) -> str:
    """Các khóa của tin (không có giá trị) để nhật ký cho biết Zalo gửi gì khi không thấy link ảnh."""
    raw = msg.raw if isinstance(msg.raw, dict) else {}
    m = raw.get('message') if isinstance(raw.get('message'), dict) else {}
    return ', '.join(sorted(m.keys()))[:200]


def _root() -> Path:
    return Path(get_settings().files_dir)


def download(url: str) -> bytes:
    if not url.startswith(('https://', 'http://')):
        raise BusinessError('BAD_PHOTO', 'Tin không có link ảnh.')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (ThanhDuc-App)', 'Accept': 'image/*,*/*'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - link do Zalo gửi
            data = resp.read(MAX_BYTES + 1)
            kind = resp.headers.get('Content-Type', '?')
    except urllib.error.HTTPError as exc:
        raise BusinessError('BAD_PHOTO', f'Zalo trả lỗi {exc.code} khi tải ảnh ({url[:80]}).') from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BusinessError('BAD_PHOTO', f'Không tải được ảnh ({url[:80]}): {exc}') from None
    if not data:
        raise BusinessError('BAD_PHOTO', f'Link ảnh trả về rỗng (loại {kind}, link {url[:80]}).')
    if len(data) > MAX_BYTES:
        raise BusinessError('BAD_PHOTO', f'Ảnh lớn hơn {MAX_BYTES // 1024 // 1024} MB.')
    return data


def prepare(data: bytes) -> bytes:
    """Xoay đúng chiều, bỏ EXIF, thu về cạnh dài 2000px, JPEG."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ('JPEG', 'PNG', 'WEBP') or im.width * im.height > 60_000_000:
                raise BusinessError('BAD_PHOTO', f'Ảnh dạng {im.format} hoặc quá lớn, chưa đọc được.')
            im = ImageOps.exif_transpose(im).convert('RGB')
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            im.save(out, 'JPEG', quality=85, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise BusinessError('BAD_PHOTO', f'Tệp tải về không phải ảnh (bắt đầu bằng {data[:12]!r}).') from None


def save(content: bytes) -> str:
    now = datetime.now()
    rel = f'{FOLDER}/{now:%Y/%m}/{uuid.uuid4().hex}.jpg'
    target = _root() / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return rel


def path_of(rel: str) -> Path | None:
    if not rel or not rel.startswith(FOLDER + '/') or '..' in rel:
        return None
    p = _root() / rel
    return p if p.is_file() else None


def cleanup(session: Session) -> int:
    """Xóa ảnh quá 30 ngày không gắn đơn mua (ảnh hóa đơn đã thành đơn mua giữ lâu dài)."""
    old = list(session.scalars(select(ZaloAgentLog).where(
        ZaloAgentLog.image_path != '', ZaloAgentLog.purchase_order_id.is_(None),
        ZaloAgentLog.created_at < _now() - timedelta(days=KEEP_DAYS)).limit(200)))
    for row in old:
        p = path_of(row.image_path)
        if p is not None:
            p.unlink(missing_ok=True)
        row.image_path = ''
    session.commit()
    return len(old)


def parse(text: str) -> dict:
    raw = (text or '').strip()
    try:
        obj = json.loads(raw[raw.find('{'):raw.rfind('}') + 1]) if '{' in raw else None
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        return dict(loai='KHAC', ro=False, chua_ro='AI trả lời không đúng khuôn')
    kind = str(obj.get('loai') or '').upper()
    obj['loai'] = kind if kind in KINDS else 'KHAC'
    obj['ro'] = bool(obj.get('ro', True))
    obj['chua_ro'] = str(obj.get('chua_ro') or '')[:300]
    return obj


def _section(read: dict, key: str) -> dict:
    v = read.get(key)
    return v if isinstance(v, dict) else {}


def brief(read: dict) -> str:
    """Một dòng "AI đọc được" cho nhật ký."""
    kind = read.get('loai')
    if kind == 'TEM_MAY':
        t = _section(read, 'tem')
        text = ' '.join(x for x in (str(t.get('hang') or ''), str(t.get('model') or '')) if x) + (f" · S/N {t.get('serial')}" if t.get('serial') else '')
    elif kind == 'HOP_MUC':
        h = _section(read, 'hop')
        text = ' '.join(str(x) for x in [h.get('hang') or '', h.get('ten') or ''] if x) + (' · ' + ', '.join(map(str, h.get('ma') or [])) if h.get('ma') else '')
    elif kind == 'HOA_DON':
        h = _section(read, 'hoa_don')
        lines = h.get('dong') if isinstance(h.get('dong'), list) else []
        text = f"{h.get('ncc') or 'NCC?'} · HĐ {h.get('so') or '?'} · {len(lines)} dòng" + (f" · {_money(h.get('tong'))}" if _int(h.get('tong')) else '')
    else:
        text = 'Ảnh khác'
    if read.get('chua_ro'):
        text += ' · chưa rõ: ' + read['chua_ro']
    return text.strip(' ·')[:300]


def _int(v) -> int:
    try:
        return int(Decimal(str(v).replace('.', '').replace(',', '').strip() or '0')) if not isinstance(v, (int, float)) else int(v)
    except (InvalidOperation, ValueError):
        return 0


def _money(v) -> str:
    return f'{_int(v):,}'.replace(',', '.')


def _qty(v) -> Decimal | None:
    try:
        q = Decimal(str(v).replace(',', '.').strip())
    except (InvalidOperation, ValueError):
        return None
    return q if q.is_finite() and Decimal(0) < q <= Decimal(100000) and q == q.quantize(Decimal('0.001')) else None


# ---------- Trả lời theo loại ảnh (app tra, không phải AI) ----------

def reply_label(session: Session, read: dict) -> tuple[str, str]:
    from . import zalo_tra_cuu
    t = _section(read, 'tem')
    model = ' '.join(str(x) for x in (t.get('hang') or '', t.get('model') or '') if x).strip()
    serial = str(t.get('serial') or '').strip()
    if not serial or not read['ro']:
        what = f'model {model}' if model else 'tem máy'
        why = read['chua_ro'] or 'không thấy số serial'
        return 'CHUP_LAI', f'Em đọc được {what} nhưng {why}. Anh/chị chụp lại gần hơn, nghiêng tránh lóa đèn giúp em nhé.'
    head = f'Đọc tem: {model or "máy"}, serial {serial}.'
    info = zalo_tra_cuu.tool_warranty(session, serial)
    if 'loi' in info:
        near = info.get('serial_gan_giong') or []
        return 'TRA_LOI', head + '\n\nSerial này chưa có trong app.' + (f" Serial gần giống: {', '.join(near)}." if near else '')
    lines = [head, '', f"Máy: {info['may'] or 'chưa rõ model'} · khách: {info['khach']}."]
    if info.get('bao_hanh_may_den'):
        lines.append(('Bảo hành máy: còn hạn đến ' if info['con_bao_hanh_may'] else 'Bảo hành máy: hết hạn từ ') + info['bao_hanh_may_den'] + '.')
    else:
        lines.append('Bảo hành máy: chưa ghi hạn.')
    if info.get('lan_sua_gan_nhat'):
        tail = (', bảo hành sửa ' + ('còn đến ' if info.get('con_bao_hanh_sua') else 'hết từ ') + info['bao_hanh_sua_den']) if info.get('bao_hanh_sua_den') else ''
        lines.append(f"Lần sửa gần nhất: {info['lan_sua_gan_nhat']}{tail}.")
    from urllib.parse import quote
    lines.append('Mở máy trên app: ' + zalo_tra_cuu._url('/thiet-bi/' + quote(info['serial'], safe='')))
    return 'TRA_LOI', '\n'.join(lines)


def reply_box(session: Session, staff: User, read: dict) -> tuple[str, str]:
    from .zalo_agent import can_do, tool_stock
    h = _section(read, 'hop')
    codes = [str(c).strip() for c in (h.get('ma') or []) if str(c).strip()][:4] if isinstance(h.get('ma'), list) else []
    name = str(h.get('ten') or '').strip()
    words = codes + ([name] if name else [])
    label = ' '.join(x for x in (str(h.get('hang') or '').strip(), name or (codes[0] if codes else '')) if x)
    if not words:
        return 'CHUP_LAI', f"Em chưa đọc được mã trên hộp ({read['chua_ro'] or 'không thấy mã'}). Anh/chị chụp mặt có mã hàng giúp em nhé."
    price = can_do(staff)['price']
    found = []
    for w in words:
        res = tool_stock(session, w, price)
        if res.get('ket_qua'):
            found = res['ket_qua']
            break
    head = f"Đọc hộp: {label}" + (f" ({', '.join(codes)})" if codes and name else '') + '.'
    if not found:
        return 'TRA_LOI', head + '\nApp chưa có mã hàng nào khớp.'
    lines = [head, '', f'App có {len(found)} mã:']
    for r in found:
        stores = r['kho'] if isinstance(r['kho'], str) else ', '.join(f"{k['kho']} {k['so_luong']}" for k in r['kho'])
        extra = f" · giá bán {_money(r['gia_ban_chua_vat'])}" if 'gia_ban_chua_vat' in r else ''
        lines.append(f"• {r['ma']} {r['ten']}: {stores}{extra}")
    if has_role(staff, 'sales'):
        lines += ['', 'Muốn lên đơn nháp thì nhắn tên khách và số lượng.']
    return 'TRA_LOI', '\n'.join(lines)


def _supplier(session: Session, h: dict) -> Supplier | None:
    from . import inventory_catalogs
    mst = re.sub(r'\D', '', str(h.get('mst') or ''))
    if len(mst) >= 10:
        row = session.scalars(select(Supplier).where(Supplier.tax_code == mst, Supplier.active.is_(True))).first()
        if row is not None:
            return row
    name = str(h.get('ncc') or '').strip()
    if len(name) >= 3:
        rows = [s for s in inventory_catalogs.search(session, Supplier, name[:200]) if s.active]
        if len(rows) == 1:
            return rows[0]
    return None


def _match(session: Session, supplier_code: str, line: dict) -> Product | None:
    """Ghép dòng hóa đơn với MỘT mã hàng: ưu tiên mã đã mua của NCC này, rồi tới danh mục. Không chắc thì không ghép."""
    from . import products
    bought = set(session.scalars(select(PurchaseOrderLine.product_code).join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.order_id)
                                 .where(PurchaseOrder.supplier_code == supplier_code).distinct()))
    for q in (str(line.get('ma') or '').strip(), str(line.get('ten') or '').strip()):
        if len(q) < 2:
            continue
        found = products.search(session, q[:200], limit=8)
        mine = [p for p in found if p.code in bought]
        if len(mine) == 1:
            return mine[0]
        if len(found) == 1:
            return found[0]
    return None


def _warehouse(session: Session) -> str | None:
    want = get_settings().sales_default_warehouse
    w = session.get(Warehouse, want)
    if w is not None and w.active and w.kind == 'KHO':
        return w.code
    return session.scalar(select(Warehouse.code).where(Warehouse.kind == 'KHO', Warehouse.active.is_(True)).order_by(Warehouse.code).limit(1))


def make_purchase(session: Session, staff: User, read: dict, log: ZaloAgentLog, image: bytes) -> tuple[str, str]:
    from . import attachments, purchase_orders
    from .config import DEFAULT_VAT_RATE
    from .schemas import PurchaseOrderIn, PurchaseOrderLineIn
    h = _section(read, 'hoa_don')
    lines = [x for x in (h.get('dong') if isinstance(h.get('dong'), list) else []) if isinstance(x, dict)][:60]
    summary = f"{h.get('ncc') or 'NCC chưa rõ'}, HĐ {h.get('so') or '?'}, {len(lines)} dòng" + (f", tổng {_money(h.get('tong'))}" if _int(h.get('tong')) else '')
    if not lines or not read['ro']:
        return 'CHUP_LAI', f"Em đọc được hóa đơn ({summary}) nhưng {read['chua_ro'] or 'chưa rõ các dòng hàng'}. Anh/chị chụp thẳng, đủ sáng, rõ phần bảng hàng giúp em nhé."
    supplier = _supplier(session, h)
    if supplier is None:
        return 'TRA_LOI', (f'Em đọc được hóa đơn: {summary}, mã số thuế {h.get("mst") or "không rõ"}. '
                           'Chưa khớp nhà cung cấp nào trong app nên em chưa lập đơn mua. Anh/chị lập đơn trên app và chọn NCC giúp em.')
    warehouse = _warehouse(session)
    if warehouse is None:
        return 'TRA_LOI', 'Chưa có kho nhận hàng nào đang dùng nên em chưa lập được đơn mua.'
    matched, missing = [], []
    for ln in lines:
        product = _match(session, supplier.code, ln)
        qty, cost = _qty(ln.get('sl')), _int(ln.get('don_gia'))
        vat = _int(ln.get('thue')) if _int(ln.get('thue')) in (0, 5, 8, 10) else DEFAULT_VAT_RATE
        if product is None or qty is None or cost < 0:
            missing.append(dict(ten=str(ln.get('ten') or ln.get('ma') or '')[:200], sl=str(ln.get('sl') or ''), don_gia=cost))
            continue
        matched.append(dict(line=PurchaseOrderLineIn(product_code=product.code, warehouse_code=warehouse, qty=qty, unit_cost=cost, vat_rate=vat),
                            hd=str(ln.get('ten') or ln.get('ma') or '')[:200]))
    if not matched:
        return 'TRA_LOI', (f'Em đọc được hóa đơn: {summary}, NCC {supplier.name}. Chưa ghép được dòng nào với mã hàng trong app '
                           'nên em chưa lập đơn mua. Anh/chị lập đơn trên app giúp em.')
    today = _now().date()
    try:
        day = date.fromisoformat(str(h.get('ngay') or '')[:10])
        day = day if today - timedelta(days=60) <= day <= today else today
    except ValueError:
        day = today
    note = [f"Lập từ ảnh hóa đơn Zalo ({staff.full_name}). HĐ số {h.get('so') or '?'} ngày {h.get('ngay') or '?'}."]
    if missing:
        note.append('Chưa ghép mã: ' + '; '.join(f"{m['ten']} · SL {m['sl']} · {_money(m['don_gia'])}" for m in missing))
    body = PurchaseOrderIn(supplier_code=supplier.code, order_date=day, note=' '.join(note)[:4000],
                           price_includes_vat=bool(h.get('gia_gom_vat')), lines=[m['line'] for m in matched])
    try:
        order = purchase_orders.create(session, staff, body)
    except BusinessError as exc:
        session.rollback()
        return 'TRA_LOI', f'Em đọc được hóa đơn: {summary}, nhưng chưa lập được đơn mua: {exc.message}'
    read['ghep'] = [dict(ma=m['line'].product_code, hd=m['hd']) for m in matched]
    read['chua_ghep'] = missing
    log.purchase_order_id, log.image_read = order.id, dict(read)
    session.commit()
    try:
        attachments.add(session, staff, 'purchase_order', str(order.id), 'KHAC', 'hoa-don-zalo.jpg', image)
    except BusinessError:
        session.rollback()
    from . import zalo_tra_cuu
    text = [f'Em đã lập đơn mua nháp {order.number}:', f'NCC: {supplier.name} ({supplier.code})',
            f"Hóa đơn số {h.get('so') or '?'} ngày {h.get('ngay') or '?'}",
            f'{len(lines)} dòng: {len(matched)} dòng đã ghép mã' + (f", {len(missing)} dòng chưa ghép ({'; '.join(m['ten'] for m in missing)[:200]})" if missing else '')]
    if _int(h.get('tong')):
        text.append(f"Tổng theo hóa đơn: {_money(h.get('tong'))}" + (' (đã gồm VAT)' if h.get('gia_gom_vat') else ''))
    text += ['', 'Ảnh hóa đơn đã đính kèm. Mở app kiểm số lượng, giá rồi bấm Đặt hàng: ' + zalo_tra_cuu._url(f'/don-mua/{order.id}')]
    return 'DON_MUA', '\n'.join(text)


# ---------- Một ảnh đến ----------

def handle(session: Session, chat: ZaloChat, staff: User, msg: ZaloMessage, log: ZaloAgentLog, cfg_row) -> None:
    from . import zalo_bot
    from .zalo_agent import _reply, _wait_job, z2_alive
    url, caption = photo_of(msg)
    log.question = ('[Ảnh] ' + caption).strip()[:4000]
    engine = cfg_row.staff_engine or 'api'
    if engine == 'api' or not z2_alive(session):
        log.result, log.error = 'LOI', 'Đọc ảnh cần Codex/Claude trên máy Z2 (đang chọn API hoặc Z2 không chạy).'
        _reply(session, chat, log, NO_Z2_TEXT)
        return
    try:
        content = prepare(download(url))
    except BusinessError as exc:
        log.result, log.error = 'LOI', exc.message + ('' if url else f' Tin có các mục: {raw_keys(msg)}.')
        _reply(session, chat, log, NO_DOWNLOAD_TEXT)
        return
    log.image_path, log.engine = save(content), engine
    session.commit()
    zalo_bot.send_text(session, chat, WAIT_TEXT, None, 'tro_ly')
    where = ' (tệp anh.jpg trong thư mục hiện tại)' if engine == 'claude' else ''
    job = ZaloAiJob(log_id=log.id, engine=engine, prompt=PROMPT.format(where=where, caption=caption.replace('"', "'")), image_path=log.image_path)
    session.add(job)
    session.commit()
    read = parse(_wait_job(session, job.id, max(get_settings().zalo_z2_wait, 180)))
    log.image_kind, log.image_read = read['loai'], read
    log.tools = [*log.tools, dict(ten='doc_anh', vao=dict(chu_thich=caption[:60]), ra=brief(read))]
    session.commit()
    kind = read['loai']
    if kind == 'TEM_MAY':
        result, text = reply_label(session, read)
    elif kind == 'HOP_MUC':
        result, text = reply_box(session, staff, read)
    elif kind == 'HOA_DON':
        if not has_role(staff, 'warehouse'):
            result, text = 'TRA_LOI', f"Em đọc được hóa đơn: {brief(read)}. Chỉ thủ kho hoặc giám đốc được lập đơn mua từ hóa đơn."
        elif not cfg_row.invoice_po:
            result, text = 'TRA_LOI', f"Em đọc được hóa đơn: {brief(read)}. Chức năng lập đơn mua từ hóa đơn đang tắt."
        else:
            result, text = make_purchase(session, staff, read, log, content)
    else:
        result, text = 'TRA_LOI', ('Em chưa nhận ra đây là tem máy, hộp mực hay hóa đơn nhà cung cấp. '
                                   'Anh/chị chụp rõ tem máy (serial), mã trên hộp, hoặc cả tờ hóa đơn giúp em nhé.')
    log.result = result
    _reply(session, chat, log, text)

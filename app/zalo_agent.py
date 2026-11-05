"""Trợ lý Zalo (TUẤN duyệt 05/11/2026): hỏi tồn kho và lên đơn nháp từ tin Zalo.

- Chỉ hội thoại riêng đã được giám đốc gắn với một nhân viên hoặc một khách mới được trả lời số liệu.
  Người chưa gắn chỉ nhận lời "đã nhận tin" (tối đa một lần mỗi 12 giờ); tin vẫn nằm ở Tin nhắn Zalo.
- AI (OpenAI) chỉ gọi được các công cụ dưới đây. Dữ liệu đã lọc theo quyền trước khi AI thấy:
  thợ, kho, điều phối chỉ thấy số lượng; kinh doanh, kế toán, giám đốc thấy thêm giá bán.
  Giá vốn, lãi, công nợ không bao giờ đưa vào công cụ nào.
- Ghi duy nhất một việc: tạo ĐƠN NHÁP (NHAP) qua sales_orders.create, như bấm tay. Đơn nháp không giữ hàng,
  không tính nợ. Kinh doanh kiểm giá rồi tự bấm Chốt. Trợ lý không chốt, không hủy, không sửa tồn hay tiền.
- Khách đã gắn nhắn đặt hàng: lập đơn nháp dưới tên người nhận đơn nháp (cấu hình), bot chỉ báo đã nhận,
  không báo giá, không báo tồn.
- Mỗi tin một dòng nhật ký (zalo_agent_logs): ai hỏi, công cụ đã gọi, trả lời, đơn nháp, chi phí.
"""
import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from . import audit, customers, products, sales_orders
from . import search as tim
from .config import get_settings
from .db import engine
from .errors import BusinessError
from .models import (Customer, Product, PurchaseOrder, PurchaseOrderLine, SalesOrder, SalesOrderLine, StockBalance, User,
                     Warehouse, ZaloAgentLog, ZaloChat, ZaloMessage, ZaloTroLy)
from .schemas import SalesOrderIn, SalesOrderLineIn
from .security import has_role

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
OPENAI_URL = 'https://api.openai.com/v1/chat/completions'
MAX_ROUNDS = 5
MAX_LINES = 20
MAX_QTY = 1000
RATE_VND = 26_000
# USD cho mỗi triệu token (vào, ra). Model ngoài bảng thì chi phí ghi 0, xem trên trang OpenAI.
PRICE_USD = {'gpt-5-mini': (0.25, 2.0), 'gpt-5-nano': (0.05, 0.4), 'gpt-5': (1.25, 10.0), 'gpt-4.1-mini': (0.4, 1.6)}
RECEIVED_TEXT = 'Thành Đức đã nhận tin, nhân viên sẽ trả lời anh/chị trong giờ làm việc (7:30–17:30, T2–T7).'
FAIL_TEXT = 'Trợ lý chưa trả lời được câu này, anh/chị xem trên app hoặc hỏi lại sau nhé.'
RESULTS = {'DANG_LAM': ('Đang xử lý', 'cho'), 'TRA_LOI': ('Đã trả lời', 'xong'), 'DON_NHAP': ('Đơn nháp', 'xong'),
           'KHONG_TRA_LOI': ('Không trả lời số liệu', 'huy'), 'BO_QUA': ('Không phải đặt hàng', 'nhap'),
           'HET_LUOT': ('Hết lượt hôm nay', 'cho'), 'LOI': ('Không chạy được', 'huy')}
ASKERS = {'NHAN_VIEN': 'Nhân viên', 'KHACH': 'Khách', 'CHUA_GAN': 'Chưa gắn'}
TICKET_DRAFT_REASON = 'Bỏ đơn nháp từ Zalo'


class AiError(Exception):
    """Lỗi gọi OpenAI, đã bỏ khóa nên ghi được vào nhật ký."""


def _now():
    return datetime.now(TZ)


def _day_start(now=None):
    now = (now or _now()).astimezone(TZ)
    return datetime(now.year, now.month, now.day, tzinfo=TZ)


def _director(user: User) -> None:
    if not user.active or 'director' not in user.roles:
        raise BusinessError('FORBIDDEN', 'Chỉ giám đốc được cấu hình Trợ lý Zalo.', status=403)


# ---------- Cấu hình và gắn Zalo ----------

def config(session: Session) -> ZaloTroLy:
    """Dòng cấu hình; chưa có thì trả bản mặc định (tắt) chưa lưu."""
    return session.get(ZaloTroLy, 1) or ZaloTroLy(id=1, enabled=False)


def draft_users(session: Session) -> list[User]:
    """Người có thể đứng tên đơn nháp của khách: kinh doanh hoặc giám đốc đang hoạt động."""
    rows = session.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name))
    return [u for u in rows if has_role(u, 'sales')]


def save_config(session: Session, user: User, enabled: bool, draft_user_id: int | None) -> ZaloTroLy:
    _director(user)
    if draft_user_id is not None and draft_user_id not in {u.id for u in draft_users(session)}:
        raise BusinessError('VALIDATION', 'Người nhận đơn nháp phải là kinh doanh hoặc giám đốc đang làm việc.', status=422)
    row = session.get(ZaloTroLy, 1)
    before = None if row is None else dict(enabled=row.enabled, draft_user_id=row.draft_user_id)
    if row is None:
        row = ZaloTroLy(id=1)
        session.add(row)
    row.enabled, row.draft_user_id, row.updated_by, row.updated_at = enabled, draft_user_id, user.id, _now()
    audit.record(session, user, 'zalo_tro_ly.config', 'zalo_tro_ly', '1', before, dict(enabled=enabled, draft_user_id=draft_user_id))
    session.commit()
    return row


def link(session: Session, user: User, chat_pk: int, kind: str, user_id: int | None = None, customer_code: str | None = None) -> ZaloChat:
    """Gắn hội thoại riêng với nhân viên (kind=NHAN_VIEN) hoặc khách (KHACH); kind=CHUA_GAN là bỏ gắn."""
    _director(user)
    chat = session.get(ZaloChat, chat_pk, with_for_update=True)
    if chat is None:
        raise BusinessError('NOT_FOUND', 'Không tìm thấy hội thoại.', status=404)
    if kind != 'CHUA_GAN' and chat.chat_type != 'PRIVATE':
        raise BusinessError('VALIDATION', 'Chỉ gắn được hội thoại riêng, không gắn nhóm.', status=422)
    before = dict(user_id=chat.user_id, customer_code=chat.customer_code)
    if kind == 'NHAN_VIEN':
        staff = session.get(User, user_id) if user_id else None
        if staff is None or not staff.active:
            raise BusinessError('VALIDATION', 'Hãy chọn một nhân viên đang làm việc.', status=422)
        taken = session.scalar(select(ZaloChat.id).where(ZaloChat.user_id == staff.id, ZaloChat.id != chat.id))
        if taken:
            raise BusinessError('VALIDATION', f'{staff.full_name} đã gắn với một hội thoại Zalo khác. Bỏ gắn ở đó trước.', status=409)
        chat.user_id, chat.customer_code = staff.id, None
    elif kind == 'KHACH':
        customer = session.get(Customer, (customer_code or '').strip())
        if customer is None or customer.status != 'DANG_DUNG':
            raise BusinessError('VALIDATION', 'Hãy chọn một khách hàng đang dùng.', status=422)
        chat.user_id, chat.customer_code = None, customer.code
    elif kind == 'CHUA_GAN':
        chat.user_id = chat.customer_code = None
    else:
        raise BusinessError('VALIDATION', 'Hãy chọn người này là nhân viên, khách hàng hay chưa rõ.', status=422)
    chat.linked_by, chat.linked_at = user.id, _now()
    audit.record(session, user, 'zalo_chat.link', 'zalo_chat', str(chat.id), before,
                 dict(user_id=chat.user_id, customer_code=chat.customer_code))
    session.commit()
    return chat


def linked_chats(session: Session) -> list[ZaloChat]:
    return list(session.scalars(select(ZaloChat).where((ZaloChat.user_id.is_not(None)) | (ZaloChat.customer_code.is_not(None)))
                                .order_by(ZaloChat.display_name, ZaloChat.id)))


def can_do(user: User) -> dict:
    """Quyền của nhân viên trên Zalo, theo đúng vai trò tài khoản app."""
    return dict(price=has_role(user, 'sales', 'finance'), draft=has_role(user, 'sales'))


def can_do_label(chat: ZaloChat, staff: User | None) -> str:
    if chat.customer_code:
        return 'Gửi yêu cầu đặt hàng'
    if staff is None or not staff.active:
        return 'Không (tài khoản đã khóa)'
    c = can_do(staff)
    return 'Hỏi tồn' + (', giá bán' if c['price'] else '') + (', lên đơn nháp' if c['draft'] else '')


# ---------- Công cụ cho AI (chỉ đọc, trừ tạo đơn nháp) ----------

def _num(value) -> int | float:
    value = Decimal(value or 0)
    return int(value) if value == value.to_integral_value() else float(value)


def tool_stock(session: Session, q: str, with_price: bool) -> dict:
    q = str(q or '').strip()[:100]
    if not q:
        return dict(loi='Thiếu từ khóa mã hoặc tên hàng.')
    found = products.search(session, q, limit=6)
    if not found:
        return dict(ket_qua=[], ghi_chu='Không có mã hàng nào khớp.')
    codes = [p.code for p in found]
    from .stock_reservations import quantities
    rows = session.execute(select(StockBalance.warehouse_code, StockBalance.product_code, StockBalance.qty, Warehouse.name, Warehouse.kind)
                           .join(Warehouse, Warehouse.code == StockBalance.warehouse_code)
                           .where(StockBalance.product_code.in_(codes), StockBalance.qty > 0, Warehouse.active.is_(True),
                                  Warehouse.kind.in_(('KHO', 'THO')))
                           .order_by(Warehouse.kind, Warehouse.code)).all()
    held = quantities(session, [(r.warehouse_code, r.product_code) for r in rows if r.kind == 'KHO'])
    coming = {r.product_code: r for r in session.execute(
        select(PurchaseOrderLine.product_code, func.sum(PurchaseOrderLine.qty - PurchaseOrderLine.received_qty).label('qty'),
               func.min(PurchaseOrder.expected_date).label('at'))
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.order_id)
        .where(PurchaseOrderLine.product_code.in_(codes), PurchaseOrder.status.in_(('DA_DAT', 'NHAN_MOT_PHAN')),
               PurchaseOrderLine.qty > PurchaseOrderLine.received_qty)
        .group_by(PurchaseOrderLine.product_code))}
    out = []
    for p in found:
        stores = []
        for r in rows:
            if r.product_code != p.code:
                continue
            item = dict(kho=r.name, so_luong=_num(r.qty))
            if r.kind == 'KHO':
                h = held.get((r.warehouse_code, r.product_code))
                if h and h['reserved']:
                    item.update(da_giu_cho_don=_num(h['reserved']), con_ban_duoc=_num(max(Decimal(0), h['available'])))
            stores.append(item)
        row = dict(ma=p.code, ten=p.name, don_vi=p.unit, kho=stores or 'Hết hàng ở mọi kho')
        if with_price:
            row['gia_ban_chua_vat'] = p.sale_price
        if p.code in coming:
            c = coming[p.code]
            row['dang_ve'] = dict(so_luong=_num(c.qty), du_kien=c.at.strftime('%d/%m/%Y') if c.at else 'chưa hẹn ngày')
        out.append(row)
    return dict(ket_qua=out)


def tool_customers(session: Session, q: str) -> dict:
    q = str(q or '').strip()[:100]
    if not q:
        return dict(loi='Thiếu tên, mã hoặc số điện thoại khách.')
    rows = [c for c in customers.search(session, q, include_test=False) if c.status == 'DANG_DUNG'][:5]
    return dict(ket_qua=[dict(ma=c.code, ten=c.name, dien_thoai=[x.phone for x in c.phones][:2], dia_chi=c.address[:200])
                         for c in rows], ghi_chu='' if rows else 'Không tìm thấy khách nào.')


def tool_history(session: Session, customer_code: str, q: str = '') -> dict:
    customer = session.get(Customer, str(customer_code or '').strip())
    if customer is None:
        return dict(loi='Không có mã khách này.')
    stmt = (select(SalesOrderLine.product_code, Product.name, func.max(SalesOrder.created_at).label('last'),
                   func.count(func.distinct(SalesOrder.id)).label('times'))
            .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_id).join(Product, Product.code == SalesOrderLine.product_code)
            .where(SalesOrder.customer_code == customer.code, SalesOrder.status.not_in(('NHAP', 'DA_HUY')),
                   SalesOrderLine.kind == 'HANG'))
    q = str(q or '').strip()[:100]
    if q:
        stmt = stmt.where(*tim.like_all(Product.search_text, q))
    rows = session.execute(stmt.group_by(SalesOrderLine.product_code, Product.name).order_by(func.max(SalesOrder.created_at).desc()).limit(8)).all()
    return dict(khach=customer.name, da_mua=[dict(ma=r.product_code, ten=r.name, lan_cuoi=r.last.astimezone(TZ).strftime('%d/%m/%Y'),
                                               so_don=r.times) for r in rows])


def _promised(raw) -> date | None:
    try:
        value = date.fromisoformat(str(raw or '')[:10])
    except ValueError:
        return None
    today = _now().date()
    return value if today <= value <= today + timedelta(days=60) else None


def tool_draft(session: Session, actor: User, customer_code: str, lines, promised, deliver: bool, note: str, question: str) -> dict:
    """Tạo đơn nháp. Trả dict cho AI và gắn khóa '_order' (đối tượng đơn) để ghi nhật ký."""
    if not isinstance(lines, list) or not lines:
        return dict(loi='Đơn phải có ít nhất một dòng hàng.')
    if len(lines) > MAX_LINES:
        return dict(loi=f'Đơn nháp tối đa {MAX_LINES} dòng.')
    body_lines, picks = [], []
    wh = get_settings().sales_default_warehouse
    wh = wh if (w := session.get(Warehouse, wh)) is not None and w.active and w.kind == 'KHO' else None
    for raw in lines:
        if not isinstance(raw, dict):
            return dict(loi='Dòng hàng không đúng dạng.')
        code = str(raw.get('ma_hang') or '').strip().upper()
        try:
            qty = int(raw.get('so_luong'))
        except (TypeError, ValueError):
            return dict(loi=f'Số lượng của {code or "dòng hàng"} phải là số nguyên.')
        if not 1 <= qty <= MAX_QTY:
            return dict(loi=f'Số lượng phải từ 1 đến {MAX_QTY}.')
        product = session.get(Product, code)
        if product is None or not product.active:
            return dict(loi=f'Không có mã hàng {code} đang bán. Hãy tra lại bằng tra_ton.')
        doubt = str(raw.get('can_chon_ma') or '').strip()[:300]
        if doubt:
            picks.append(f'{product.code}: {doubt}')
        body_lines.append(SalesOrderLineIn(kind='HANG', product_code=product.code, qty=qty, warehouse_code=wh))
    text = ['Đơn nháp từ Zalo. Giá do trợ lý điền theo danh mục, chưa kiểm.', f'Tin gốc: {question[:500]}']
    if picks:
        text.append('Cần chọn mã: ' + '; '.join(picks))
    if note:
        text.append('Ghi chú: ' + str(note)[:500])
    body = SalesOrderIn(customer_code=str(customer_code or '').strip(), promised_date=_promised(promised), note='\n'.join(text),
                        fulfillment='THO_GIAO' if deliver else 'TAI_QUAY', lines=body_lines)
    session.commit()  # lưu nhật ký trước: tạo đơn lỗi sẽ rollback phiên
    try:
        order = sales_orders.create(session, actor, body)
    except BusinessError as exc:
        return dict(loi=exc.message)
    return dict(da_tao=order.number, khach=order.customer_code, tong_tam_tinh=order.total, hen_giao=
                order.promised_date.strftime('%d/%m/%Y') if order.promised_date else '', _order=order, _picks=picks)


# ---------- Gọi AI ----------

def _fn(name, desc, props, required):
    return dict(type='function', function=dict(name=name, description=desc,
                                               parameters=dict(type='object', properties=props, required=required, additionalProperties=False)))


T_STOCK = _fn('tra_ton', 'Tra tồn kho theo mã hoặc tên hàng (không dấu cũng được). Trả tối đa 6 mã khớp, tồn từng kho, hàng đang về.',
              dict(tu_khoa=dict(type='string')), ['tu_khoa'])
T_CUSTOMER = _fn('tim_khach', 'Tìm khách theo tên, mã KTD hoặc số điện thoại. Trả tối đa 5 khách.', dict(tu_khoa=dict(type='string')), ['tu_khoa'])
T_HISTORY = _fn('lich_su_mua', 'Mã hàng khách đã mua gần nhất (8 mã), lọc thêm theo từ khóa nếu có.',
                dict(ma_khach=dict(type='string'), tu_khoa=dict(type='string')), ['ma_khach', 'tu_khoa'])
T_HISTORY_SELF = _fn('lich_su_mua', 'Mã hàng khách này đã mua gần nhất (8 mã), lọc thêm theo từ khóa nếu có.',
                     dict(tu_khoa=dict(type='string')), ['tu_khoa'])
_LINE = dict(type='array', items=dict(type='object', additionalProperties=False, required=['ma_hang', 'so_luong', 'can_chon_ma'], properties=dict(
    ma_hang=dict(type='string', description='Mã TD-xxxx lấy từ tra_ton hoặc lich_su_mua'), so_luong=dict(type='integer'),
    can_chon_ma=dict(type='string', description='Để trống nếu chắc chắn. Nếu từ khóa khớp nhiều mã, ghi các mã còn lại để kinh doanh chọn.'))))
_DRAFT_PROPS = dict(dong=_LINE, hen_giao=dict(type='string', description='YYYY-MM-DD hoặc chuỗi rỗng'),
                    giao_tan_noi=dict(type='boolean'), ghi_chu=dict(type='string'))
T_DRAFT = _fn('tao_don_nhap', 'Tạo ĐƠN NHÁP bán hàng (chưa chốt, không giữ hàng). Kinh doanh sẽ kiểm giá và tự chốt. Chỉ gọi một lần.',
              dict(ma_khach=dict(type='string'), **_DRAFT_PROPS), ['ma_khach', 'dong', 'hen_giao', 'giao_tan_noi', 'ghi_chu'])
T_DRAFT_SELF = _fn('tao_don_nhap', 'Tạo ĐƠN NHÁP cho khách này khi tin nhắn là yêu cầu đặt hàng rõ ràng. Chỉ gọi một lần.',
                   _DRAFT_PROPS, ['dong', 'hen_giao', 'giao_tan_noi', 'ghi_chu'])

STAFF_PROMPT = """Bạn là trợ lý nội bộ của Công ty Tin Học Thành Đức (máy in, mực in, sửa chữa) trả lời nhân viên qua Zalo.
Hôm nay là {today}. Người hỏi: {name} ({roles}).
Quy tắc:
- Chỉ trả lời về tồn kho{extra}. Câu hỏi khác thì nói ngắn gọn là trợ lý chỉ tra tồn kho{extra2}.
- Luôn tra bằng công cụ, không đoán số. Từ khóa khớp nhiều mã thì liệt kê từng mã kèm tồn từng kho.
- Không bao giờ nói giá vốn, lãi, công nợ. {price_rule}
{draft_rule}- Trả lời tiếng Việt, ngắn, văn bản thường (không markdown, không bảng), dưới 1500 ký tự. Xưng "em", gọi "anh/chị"."""
DRAFT_RULE = """- Khi được nhờ lên đơn: tìm khách bằng tim_khach. Có hơn một khách giống nhau thì hỏi lại, không tạo đơn.
  Ưu tiên mã khách đã mua (lich_su_mua), rồi tra_ton. Mã không chắc thì vẫn tạo nhưng ghi can_chon_ma.
  Sau khi tạo, báo số đơn nháp, khách, từng dòng và nhắc kiểm giá rồi bấm Chốt trên app. Trợ lý không chốt đơn.
"""
CUSTOMER_PROMPT = """Bạn đọc tin Zalo của khách hàng {name} (mã {code}) gửi Công ty Tin Học Thành Đức. Hôm nay là {today}.
Nếu tin là yêu cầu đặt mua hàng rõ ràng (có tên hàng), hãy: tra lich_su_mua để ưu tiên mã khách đã mua, tra_ton nếu cần,
rồi gọi tao_don_nhap đúng một lần. Mã không chắc thì ghi can_chon_ma. "giao", "gửi về" là giao_tan_noi=true.
Nếu tin không phải đặt hàng (hỏi chuyện, báo hỏng máy, hỏi giá...) thì KHÔNG gọi công cụ nào và trả lời đúng một chữ: KHONG.
Bạn không trả lời khách; nhân viên sẽ liên hệ."""


def ask_ai(messages: list, tools: list) -> dict:
    """Một lượt Chat Completions. Tách riêng để test thay thế. Lỗi không mang khóa."""
    cfg = get_settings()
    key = cfg.openai_api_key.strip()
    if not key:
        raise AiError('Chưa có khóa OpenAI (OPENAI_API_KEY trong deploy/.env).')
    payload = dict(model=cfg.zalo_ai_model, messages=messages, tools=tools, max_completion_tokens=4000)
    if cfg.zalo_ai_model.startswith(('gpt-5', 'o')):
        payload['reasoning_effort'] = 'low'
    req = urllib.request.Request(OPENAI_URL, data=json.dumps(payload).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            reason = json.loads(exc.read() or b'{}').get('error', {}).get('message', '')
        except (ValueError, AttributeError):
            reason = ''
        raise AiError(f'OpenAI trả lỗi HTTP {exc.code}. {reason}'.replace(key, '***')[:400]) from None
    except (OSError, ValueError):
        raise AiError('Không kết nối được OpenAI.') from None


def _cost(model: str, tin: int, tout: int) -> int:
    if model not in PRICE_USD:
        return 0
    a, b = PRICE_USD[model]
    return round((tin * a + tout * b) / 1e6 * RATE_VND)


def _run(session: Session, system: str, question: str, tools: list, handlers: dict, log: ZaloAgentLog) -> str:
    messages = [dict(role='system', content=system), dict(role='user', content=question[:2000])]
    model = get_settings().zalo_ai_model
    for _ in range(MAX_ROUNDS):
        resp = ask_ai(messages, tools)
        usage = resp.get('usage') or {}
        log.tokens_in += int(usage.get('prompt_tokens') or 0)
        log.tokens_out += int(usage.get('completion_tokens') or 0)
        log.cost_vnd = _cost(model, log.tokens_in, log.tokens_out)
        try:
            msg = resp['choices'][0]['message']
        except (KeyError, IndexError, TypeError):
            raise AiError('OpenAI trả kết quả không đúng dạng.') from None
        calls = msg.get('tool_calls') or []
        if not calls:
            return str(msg.get('content') or '').strip()
        messages.append(dict(role='assistant', content=msg.get('content'), tool_calls=calls))
        for call in calls:
            name = (call.get('function') or {}).get('name', '')
            try:
                args = json.loads((call.get('function') or {}).get('arguments') or '{}')
            except ValueError:
                args = {}
            handler = handlers.get(name)
            result = handler(args if isinstance(args, dict) else {}) if handler else dict(loi='Không có công cụ này.')
            shown = {k: v for k, v in result.items() if not k.startswith('_')}
            log.tools = [*log.tools, dict(ten=name, vao=args, ra=_brief(shown))]
            messages.append(dict(role='tool', tool_call_id=call.get('id', ''), content=json.dumps(shown, ensure_ascii=False, default=str)))
        session.commit()
    raise AiError('Trợ lý hỏi đi hỏi lại quá nhiều lần.')


def _brief(result: dict) -> str:
    if 'loi' in result:
        return 'Lỗi: ' + str(result['loi'])[:200]
    if 'da_tao' in result:
        return 'Tạo ' + result['da_tao']
    for key in ('ket_qua', 'da_mua'):
        if key in result:
            return f'{len(result[key])} dòng'
    return ''


# ---------- Xử lý một tin đến ----------

def _start_log(session: Session, chat: ZaloChat, msg: ZaloMessage, asker: str) -> ZaloAgentLog | None:
    pk = session.execute(insert(ZaloAgentLog).values(chat_pk=chat.id, message_id=msg.id, asker=asker, user_id=chat.user_id,
                                                     customer_code=chat.customer_code, question=msg.text[:4000], result='DANG_LAM')
                         .on_conflict_do_nothing(index_elements=['message_id']).returning(ZaloAgentLog.id)).scalar_one_or_none()
    session.commit()
    return session.get(ZaloAgentLog, pk) if pk else None


def _used_today(session: Session, chat_pk: int | None = None) -> int:
    stmt = select(func.count()).select_from(ZaloAgentLog).where(
        ZaloAgentLog.created_at >= _day_start(), ZaloAgentLog.asker != 'CHUA_GAN', ZaloAgentLog.result != 'HET_LUOT')
    if chat_pk is not None:
        stmt = stmt.where(ZaloAgentLog.chat_pk == chat_pk)
    return session.scalar(stmt) or 0


def _reply(session: Session, chat: ZaloChat, log: ZaloAgentLog, text: str) -> None:
    from . import zalo_bot
    text = text.strip()[:zalo_bot.MAX_TEXT]
    log.answer = text
    session.commit()
    zalo_bot.send_text(session, chat, text, None, 'tro_ly')


def _customer_reply(order: SalesOrder) -> str:
    items = ', '.join(f'{_num(line.qty)} × {line.description}' for line in order.lines)
    when = f', giao ngày {order.promised_date.strftime("%d/%m")}' if order.promised_date else ''
    return f'Thành Đức đã nhận yêu cầu: {items}{when}.\nNhân viên sẽ gọi xác nhận lại giá và giờ giao với anh/chị.'


def handle(session: Session, message_id: int) -> ZaloAgentLog | None:
    """Xử lý một tin đến. Không ném lỗi ra ngoài: mọi lỗi ghi vào nhật ký."""
    cfg_row = config(session)
    msg = session.get(ZaloMessage, message_id)
    if not cfg_row.enabled or msg is None or msg.direction != 'IN' or msg.kind != 'text' or not msg.text.strip():
        return None
    if isinstance(msg.raw, dict) and ((msg.raw.get('message') or {}).get('from') or {}).get('is_bot'):
        return None
    chat = session.get(ZaloChat, msg.chat_pk)
    if chat is None or chat.chat_type != 'PRIVATE':
        return None  # Nhóm: đợt này không trả lời số liệu trong nhóm.
    staff = session.get(User, chat.user_id) if chat.user_id else None
    customer = session.get(Customer, chat.customer_code) if chat.customer_code else None
    asker = 'NHAN_VIEN' if staff is not None and staff.active else 'KHACH' if customer is not None and customer.status == 'DANG_DUNG' else 'CHUA_GAN'
    log = _start_log(session, chat, msg, asker)
    if log is None:
        return None  # Tin này đã được xử lý.
    try:
        if asker == 'CHUA_GAN':
            log.result = 'KHONG_TRA_LOI'
            recent = session.scalar(select(ZaloAgentLog.id).where(
                ZaloAgentLog.chat_pk == chat.id, ZaloAgentLog.id != log.id, ZaloAgentLog.asker == 'CHUA_GAN',
                ZaloAgentLog.answer != '', ZaloAgentLog.created_at >= _now() - timedelta(hours=12)).limit(1))
            if recent is None:
                _reply(session, chat, log, RECEIVED_TEXT)
            session.commit()
            return log
        cfg = get_settings()
        if _used_today(session, chat.id) > cfg.zalo_ai_per_person or _used_today(session) > cfg.zalo_ai_per_day:
            log.result = 'HET_LUOT'
            if asker == 'NHAN_VIEN':
                _reply(session, chat, log, 'Hôm nay trợ lý đã hết lượt trả lời, anh/chị xem trên app hoặc mai hỏi tiếp nhé.')
            session.commit()
            return log
        if asker == 'NHAN_VIEN':
            _staff(session, chat, staff, msg, log)
        else:
            _customer(session, chat, customer, msg, log, cfg_row)
    except AiError as exc:
        session.rollback()
        log.result, log.error = 'LOI', str(exc)
        if asker == 'NHAN_VIEN' and not log.answer:
            _reply(session, chat, log, FAIL_TEXT)
    except Exception as exc:  # noqa: BLE001 - nhật ký phải ghi lại mọi lỗi, webhook không được hỏng
        session.rollback()
        log.result, log.error = 'LOI', f'{exc.__class__.__name__}: {str(exc)[:300]}'
        if asker == 'NHAN_VIEN' and not log.answer:
            _reply(session, chat, log, FAIL_TEXT)
    session.commit()
    return log


def _staff(session, chat, staff, msg, log):
    rights = can_do(staff)
    state = {}

    def draft(args):
        if 'order' in state:
            return dict(loi='Đã tạo đơn nháp cho tin này rồi.')
        result = tool_draft(session, staff, args.get('ma_khach'), args.get('dong'), args.get('hen_giao'),
                            bool(args.get('giao_tan_noi')), str(args.get('ghi_chu') or ''), msg.text)
        if '_order' in result:
            state['order'], state['picks'] = result['_order'], result['_picks']
        return result

    handlers = dict(tra_ton=lambda a: tool_stock(session, a.get('tu_khoa'), rights['price']))
    tools = [T_STOCK]
    if rights['draft']:
        handlers.update(tim_khach=lambda a: tool_customers(session, a.get('tu_khoa')),
                        lich_su_mua=lambda a: tool_history(session, a.get('ma_khach'), a.get('tu_khoa')), tao_don_nhap=draft)
        tools += [T_CUSTOMER, T_HISTORY, T_DRAFT]
    roles = ', '.join(staff.roles)
    system = STAFF_PROMPT.format(
        today=_now().strftime('%d/%m/%Y'), name=staff.full_name, roles=roles,
        extra=' và lên đơn nháp' if rights['draft'] else '', extra2=' và lên đơn nháp' if rights['draft'] else '',
        price_rule='Được nói giá bán danh mục (chưa VAT).' if rights['price'] else 'Không nói giá, kể cả giá bán.',
        draft_rule=DRAFT_RULE if rights['draft'] else '- Không lên đơn. Nhờ lên đơn thì bảo nhắn kinh doanh.\n')
    answer = _run(session, system, msg.text, tools, handlers, log) or FAIL_TEXT
    if 'order' in state:
        log.order_id, log.result, log.need_pick = state['order'].id, 'DON_NHAP', '; '.join(state['picks'])
        if state['order'].number not in answer:
            answer += f"\nĐơn nháp: {state['order'].number}. Mở app kiểm giá rồi bấm Chốt."
    else:
        log.result = 'TRA_LOI'
    _reply(session, chat, log, answer)


def _customer(session, chat, customer, msg, log, cfg_row):
    actor = session.get(User, cfg_row.draft_user_id) if cfg_row.draft_user_id else None
    if actor is None or not actor.active or not has_role(actor, 'sales'):
        log.result, log.error = 'LOI', 'Chưa chọn người nhận đơn nháp của khách (Trợ lý Zalo › Cấu hình).'
        return
    state = {}

    def draft(args):
        if 'order' in state:
            return dict(loi='Đã tạo đơn nháp cho tin này rồi.')
        result = tool_draft(session, actor, customer.code, args.get('dong'), args.get('hen_giao'), bool(args.get('giao_tan_noi')),
                            str(args.get('ghi_chu') or ''), msg.text)
        if '_order' in result:
            state['order'], state['picks'] = result['_order'], result['_picks']
        return result

    handlers = dict(tra_ton=lambda a: tool_stock(session, a.get('tu_khoa'), False),
                    lich_su_mua=lambda a: tool_history(session, customer.code, a.get('tu_khoa')), tao_don_nhap=draft)
    system = CUSTOMER_PROMPT.format(name=customer.name, code=customer.code, today=_now().strftime('%d/%m/%Y'))
    _run(session, system, msg.text, [T_STOCK, T_HISTORY_SELF, T_DRAFT_SELF], handlers, log)
    if 'order' not in state:
        log.result = 'BO_QUA'
        return
    order = session.scalars(select(SalesOrder).options(selectinload(SalesOrder.lines)).where(SalesOrder.id == state['order'].id)).one()
    log.order_id, log.result, log.need_pick = order.id, 'DON_NHAP', '; '.join(state['picks'])
    _reply(session, chat, log, _customer_reply(order))


def handle_message(message_id: int) -> None:
    """Chạy nền sau khi đã trả lời webhook."""
    with Session(engine()) as session:
        handle(session, message_id)


# ---------- Màn xem ----------

def logs(session: Session, day: date | None = None, offset: int = 0, size: int = 25):
    start = datetime(*(day or _now().date()).timetuple()[:3], tzinfo=TZ)
    where = (ZaloAgentLog.created_at >= start, ZaloAgentLog.created_at < start + timedelta(days=1))
    total = session.scalar(select(func.count()).select_from(ZaloAgentLog).where(*where)) or 0
    stats = session.execute(select(func.count(ZaloAgentLog.order_id), func.coalesce(func.sum(ZaloAgentLog.cost_vnd), 0))
                            .where(*where)).one()
    rows = list(session.scalars(select(ZaloAgentLog).where(*where).order_by(ZaloAgentLog.created_at.desc(), ZaloAgentLog.id.desc())
                                .offset(offset).limit(size)))
    return total, dict(drafts=stats[0], cost=int(stats[1])), rows


DRAFT_VIEWS = {'cho': ('Chờ kiểm', ('NHAP',)), 'chot': ('Đã chốt', None), 'bo': ('Đã bỏ', ('DA_HUY',))}


def _draft_where(view: str):
    base = SalesOrder.id.in_(select(ZaloAgentLog.order_id).where(ZaloAgentLog.order_id.is_not(None)))
    if view == 'cho':
        return (base, SalesOrder.status == 'NHAP')
    if view == 'bo':
        return (base, SalesOrder.status == 'DA_HUY')
    return (base, SalesOrder.status.not_in(('NHAP', 'DA_HUY')))


def draft_counts(session: Session) -> dict:
    return {v: session.scalar(select(func.count()).select_from(SalesOrder).where(*_draft_where(v))) or 0 for v in DRAFT_VIEWS}


def drafts(session: Session, user: User, view: str, offset: int, size: int):
    sales_orders.check_writer(user)
    view = view if view in DRAFT_VIEWS else 'cho'
    orders = list(session.scalars(select(SalesOrder).options(selectinload(SalesOrder.lines)).where(*_draft_where(view))
                                  .order_by(SalesOrder.created_at.desc(), SalesOrder.id.desc()).offset(offset).limit(size)))
    log_rows = {r.order_id: r for r in session.scalars(select(ZaloAgentLog).where(ZaloAgentLog.order_id.in_([o.id for o in orders])))}
    return orders, log_rows


def drop_draft(session: Session, user: User, order_id: int, version: int):
    """Bỏ đơn nháp từ Zalo = hủy đơn NHAP qua đúng hàm hủy đơn bán (kiểm quyền, phiên bản, nhật ký)."""
    sales_orders.check_writer(user)
    if session.scalar(select(ZaloAgentLog.id).where(ZaloAgentLog.order_id == order_id).limit(1)) is None:
        raise BusinessError('NOT_FOUND', 'Không phải đơn nháp từ Zalo.', status=404)
    order = session.get(SalesOrder, order_id)
    if order is None or order.status != 'NHAP':
        raise BusinessError('BAD_STATUS', 'Chỉ bỏ được đơn còn nháp.', status=409)
    return sales_orders.cancel(session, user, order_id, TICKET_DRAFT_REASON, version)


def sender_names(session: Session, rows) -> dict:
    ids = {r.user_id for r in rows if r.user_id}
    codes = {r.customer_code for r in rows if r.customer_code}
    names = {('u', u.id): u.full_name for u in session.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    if codes:
        names.update({('c', c.code): c.name for c in session.scalars(select(Customer).where(Customer.code.in_(codes)))})
    return names

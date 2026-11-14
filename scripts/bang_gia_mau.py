"""Nạp bảng giá mẫu: model máy phổ biến, gói sửa chữa 3 cấp theo nhóm máy, phụ phí, hiện tượng (TUẤN 14/11/2026).

    python -m scripts.bang_gia_mau [--user giamdoc] [--apply]

Chạy thử (không --apply) chỉ in ra sẽ thêm gì. Chỉ THÊM cái chưa có (so theo tên, không phân biệt hoa thường):
không bao giờ sửa giá, model hay dịch vụ đã có. Riêng gói do bộ mẫu tạo mà nhóm Máy in phun Canon
còn trống cả 3 cấp thì thêm giá mẫu cho nhóm đó (đổi nhóm máy 14/11: phun bình Canon trước nằm chung nhóm Epson). Giá là GIÁ MẪU chưa VAT để anh sửa lại trên trang Bảng giá;
mọi lần thêm ghi Lịch sử đổi giá như sửa tay.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import price_book as pb  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import PriceService, RepairGroup, RepairPrice, RepairSymptom, User  # noqa: E402

MODELS = {
    'LASER_A4_DON': ['Canon LBP 2900', 'Canon LBP 3300', 'Canon LBP 6030', 'Canon LBP 6230dn', 'Canon LBP 161dn',
                     'HP LaserJet P1102', 'HP LaserJet Pro M12a', 'HP LaserJet 107a', 'HP LaserJet Pro M402dn',
                     'HP LaserJet Pro M404dn', 'Brother HL-L2321D', 'Brother HL-L2366DW', 'Samsung ML-2161'],
    'LASER_A4_DA': ['Canon MF3010', 'Canon MF241d', 'Canon MF244dw', 'Canon MF445dw', 'HP LaserJet Pro MFP M127fn',
                    'HP LaserJet MFP 135a', 'HP LaserJet Pro MFP M428fdw', 'Brother DCP-L2520D', 'Brother MFC-L2701D'],
    'LASER_MAU': ['HP Color LaserJet Pro M454dw', 'HP Color LaserJet Pro MFP M479fdw', 'Canon LBP 621Cw', 'Canon MF641Cw',
                  'Brother HL-L3230CDN'],
    'LASER_A3': ['Canon LBP 3500', 'Canon LBP 3970', 'Canon LBP 8780x', 'HP LaserJet 5200', 'HP LaserJet M706n'],
    'PHUN_CANON': ['Canon PIXMA G1010', 'Canon PIXMA G2010', 'Canon PIXMA G3010', 'Canon PIXMA E410', 'Canon PIXMA MG2570S'],
    'PHUN_EPSON': ['Epson L3110', 'Epson L3150', 'Epson L3210', 'Epson L805', 'Epson L1800'],
    'KIM_NHIET': ['Epson LQ-310', 'Epson LQ-2190', 'Xprinter XP-365B', 'Godex G500'],
}

# Hệ số giá theo nhóm so với Laser A4 đơn năng; giá làm tròn 10.000.
FACTOR = {'LASER_A4_DON': 1.0, 'LASER_A4_DA': 1.2, 'LASER_MAU': 1.5, 'LASER_A3': 1.8,
          'PHUN_CANON': 1.0, 'PHUN_EPSON': 1.0, 'KIM_NHIET': 1.0}
LASER = ('LASER_A4_DON', 'LASER_A4_DA', 'LASER_MAU', 'LASER_A3')
ALL = tuple(FACTOR)

# tên, mô tả cho khách, giá gốc 3 cấp (Laser A4 đơn năng), chính hãng cấp 2/3 (cộng thêm), bảo hành 3 cấp, nhóm áp dụng,
# phạm vi 3 cấp, giờ chuẩn 3 cấp, phương án (tên, linh kiện, chính)
PACKAGES = [
    ('Sửa chữa cụm sấy', 'Khắc phục lỗi cụm sấy, kiểm tra và đảm bảo bản in bám mực, không nhăn giấy.',
     (250_000, 450_000, 650_000), (150_000, 200_000), (1, 3, 3), LASER,
     ('Vệ sinh, chỉnh; không thay linh kiện chính', 'Thay 1 linh kiện chính (bao lụa hoặc rulo) + phụ kiện nhỏ', 'Thay từ 2 linh kiện chính hoặc cả cụm'),
     ('1', '1,5', '2,5'),
     [('Thay bao lụa sấy', 'bao lụa', True), ('Thay rulo ép', 'trục ép', True), ('Thay bạc ép', 'bạc ép', False),
      ('Thay cảm biến nhiệt', 'cảm biến nhiệt', False)]),
    ('Sửa chữa bộ kéo giấy', 'Khắc phục kẹt giấy, kéo nhiều tờ, không kéo giấy.',
     (150_000, 250_000, 350_000), (100_000, 100_000), (1, 1, 2), ALL,
     ('Vệ sinh, chỉnh bánh xe kéo giấy', 'Thay 1 bộ phận kéo giấy (quả đào hoặc tấm tách)', 'Thay từ 2 bộ phận hoặc cả cụm kéo giấy'),
     ('0,5', '1', '1,5'),
     [('Thay quả đào kéo giấy', 'quả đào', True), ('Thay tấm tách giấy', 'tấm tách', True), ('Thay lò xo, bánh răng', 'phụ kiện', False)]),
    ('Sửa chữa nguồn', 'Khắc phục máy không lên nguồn, chập chờn, tự tắt.',
     (300_000, 450_000, 650_000), (0, 0), (3, 3, 3), ALL,
     ('Hàn, thay linh kiện nhỏ trên board nguồn', 'Thay 1 cụm linh kiện chính trên board nguồn', 'Thay cả board nguồn'),
     ('1', '1,5', '2'),
     [('Thay linh kiện board nguồn', 'tụ, cầu chì, IC', True), ('Thay board nguồn', 'board nguồn', True)]),
    ('Sửa chữa board mạch chính', 'Khắc phục lỗi điều khiển, báo lỗi, không nhận lệnh in.',
     (400_000, 700_000, 1_000_000), (0, 0), (3, 3, 3), ALL,
     ('Nạp lại firmware, xử lý lỗi phần mềm', 'Sửa, thay linh kiện trên board', 'Thay board mạch chính'),
     ('1', '2', '2'), [('Sửa board formatter', 'linh kiện board', True), ('Thay board formatter', 'board formatter', True)]),
    ('Sửa chữa cụm quang', 'Khắc phục bản in sọc, mờ, mất chữ do cụm quang.',
     (250_000, 450_000, 650_000), (0, 0), (1, 3, 3), LASER,
     ('Vệ sinh gương, chỉnh cụm quang', 'Thay 1 linh kiện cụm quang', 'Thay cả cụm quang (hộp quang)'),
     ('1', '1,5', '2'), [('Thay motor gương', 'motor gương', True), ('Thay hộp quang', 'hộp quang', True)]),
    ('Sửa chữa bộ phận scan, ADF', 'Khắc phục lỗi scan, photo, khay nạp bản gốc tự động.',
     (200_000, 350_000, 500_000), (0, 0), (1, 3, 3), ('LASER_A4_DA', 'LASER_MAU'),
     ('Vệ sinh, chỉnh cụm scan', 'Thay 1 linh kiện scan hoặc ADF', 'Thay cụm scan hoặc cụm ADF'),
     ('1', '1,5', '2'), [('Thay cáp scan', 'cáp scan', True), ('Thay quả đào ADF', 'quả đào ADF', True)]),
    ('Sửa chữa cụm đầu phun', 'Khắc phục in mất màu, sọc, tắc đầu phun.',
     (200_000, 400_000, 700_000), (0, 300_000), (0, 1, 3), ('PHUN_CANON', 'PHUN_EPSON'),
     ('Thông, vệ sinh đầu phun', 'Thông sâu, thay phụ kiện đầu phun', 'Thay đầu phun'),
     ('1', '1,5', '2'), [('Thông đầu phun', 'dung dịch', False), ('Thay đầu phun', 'đầu phun', True), ('Thay cáp đầu phun', 'cáp', True)]),
    ('Sửa chữa hệ thống dẫn mực', 'Khắc phục mực không xuống, tràn mực, đầy bộ đệm thải.',
     (150_000, 250_000, 400_000), (0, 0), (1, 1, 3), ('PHUN_CANON', 'PHUN_EPSON'),
     ('Xả khí, vệ sinh đường mực', 'Thay ống dẫn mực hoặc bộ đệm thải', 'Thay cụm bơm, dẫn mực'),
     ('0,5', '1', '1,5'), [('Thay bộ đệm thải, reset', 'bộ đệm thải', True), ('Thay cụm bơm mực', 'cụm bơm', True)]),
    ('Sửa chữa đầu kim, cơ in', 'Khắc phục in mất nét, đứt nét, ruy băng kẹt, máy in nhiệt in mờ.',
     (200_000, 400_000, 700_000), (0, 0), (1, 3, 3), ('KIM_NHIET',),
     ('Vệ sinh, chỉnh cơ in', 'Thay kim hoặc linh kiện cơ in', 'Thay đầu in'),
     ('1', '1,5', '2'), [('Thay kim in', 'kim in', True), ('Thay đầu in', 'đầu in', True)]),
    ('Vệ sinh, bảo dưỡng tổng thể', 'Vệ sinh toàn bộ máy, tra dầu mỡ, kiểm tra và in thử.',
     (120_000, 150_000, 200_000), (0, 0), (0, 0, 1), ALL,
     ('Vệ sinh ngoài, đường giấy', 'Vệ sinh trong, tra dầu mỡ cụm sấy và bánh răng', 'Tháo toàn bộ máy vệ sinh, bảo dưỡng'),
     ('0,5', '1', '1,5'), []),
]

FEES = [
    ('Phí kiểm tra máy', 'lần', 100_000, 0, 'Miễn phí nếu khách đồng ý sửa.'),
    ('Phụ phí sửa tận nơi nội thành', 'lần', 50_000, 0, 'Áp dụng khi kỹ thuật tới tận nơi.'),
    ('Phụ phí sửa tận nơi ngoại thành', 'lần', 100_000, 0, 'Áp dụng khi kỹ thuật tới tận nơi ngoài nội thành.'),
    ('Phụ phí xử lý gấp trong ngày', 'lần', 100_000, 0, 'Ưu tiên sửa xong trong ngày.'),
    ('Cài đặt máy in, driver', 'máy', 100_000, 0, 'Cài đặt máy in, driver, chia sẻ in qua mạng.'),
]

SYMPTOMS = [
    ('In bị nhăn giấy, bản in lem mực', 'Sửa chữa cụm sấy', 1, 3, None),
    ('Bản in không bám mực, quẹt tay bị nhòe', 'Sửa chữa cụm sấy', 1, 3, None),
    ('Kẹt giấy, kéo nhiều tờ một lúc', 'Sửa chữa bộ kéo giấy', 1, 3, None),
    ('Không kéo giấy', 'Sửa chữa bộ kéo giấy', 1, 3, None),
    ('Máy không lên nguồn', 'Sửa chữa nguồn', 1, 3, None),
    ('Báo lỗi, không nhận lệnh in', 'Sửa chữa board mạch chính', 1, 3, None),
    ('Bản in bị sọc, mờ một vệt', 'Sửa chữa cụm quang', 1, 3, None),
    ('Không scan, photo được', 'Sửa chữa bộ phận scan, ADF', 1, 3, None),
    ('In mất màu, sọc ngang', 'Sửa chữa cụm đầu phun', 1, 3, None),
    ('Báo đầy bộ đệm thải, mực không xuống', 'Sửa chữa hệ thống dẫn mực', 1, 3, None),
    ('In kim mất nét, đứt chữ', 'Sửa chữa đầu kim, cơ in', 1, 3, 'KIM_NHIET'),
]


SAMPLE_NOTE = 'Giá mẫu, giám đốc sửa lại theo thực tế.'
REFILL = ('PHUN_CANON',)


def _r(value: float) -> int:
    return int(-(-value // 10_000) * 10_000)


def _levels(base, genuine, months, code) -> dict:
    """Giá mẫu 3 cấp của một nhóm: {cấp: (giá, giá chính hãng hoặc 0, bảo hành)}."""
    f, out = FACTOR[code], {}
    for lv in (1, 2, 3):
        price = _r(base[lv - 1] * f)
        extra = genuine[lv - 2] if lv > 1 else 0
        out[lv] = (price, price + _r(extra * f) if extra else 0, months[lv - 1])
    return out


def run(session, user, apply: bool) -> list[str]:
    log = []
    groups = {g.code: g for g in session.scalars(select(RepairGroup))}
    for code, names in MODELS.items():
        for name in names:
            if pb.find_model(session, name):
                continue
            log.append(f'Model  {name} · {groups[code].name}')
            if apply:
                pb.save_model(session, user, name, groups[code].id, 'Mẫu')
    for sort, (name, desc, base, genuine, months, applies, scopes, hours, opts) in enumerate(PACKAGES, start=1):
        old = session.scalar(select(PriceService).where(PriceService.kind == 'GOI', func.lower(PriceService.name) == name.lower()))
        if old:
            # Chỉ bù giá cho nhóm mới đổi 14/11 (Máy in phun Canon) trên gói do bộ mẫu tạo; gói/nhóm anh tự đặt không đụng.
            for code in (c for c in applies if c in REFILL and old.technical_note == SAMPLE_NOTE):
                g = groups[code]
                if session.scalar(select(RepairPrice.id).where(RepairPrice.service_id == old.id, RepairPrice.group_id == g.id).limit(1)):
                    continue
                log.append(f'Giá    {old.name} · {g.name}')
                if apply:
                    levels = {lv: (p, ch or None, bh) for lv, (p, ch, bh) in _levels(base, genuine, months, code).items()}
                    pb._apply_levels(session, user, old, levels, group_id=g.id, label=g.name)
                    session.commit()
            continue
        log.append(f'Gói    {name} · {len(applies)} nhóm máy')
        form = dict(kind='GOI', name=name, description=desc, active='1', sort=str(sort), option_rows=str(len(opts)),
                    technical_note=SAMPLE_NOTE)
        for lv in (1, 2, 3):
            form[f'scope_{lv}'], form[f'hours_{lv}'] = scopes[lv - 1], hours[lv - 1]
        for i, (oname, hint, main) in enumerate(opts):
            form[f'opt_name_{i}'], form[f'opt_hint_{i}'] = oname, hint
            if main:
                form[f'opt_main_{i}'] = '1'
        for code in applies:
            g = groups[code]
            for lv, (price, ch, bh) in _levels(base, genuine, months, code).items():
                form[f'g{g.id}_p{lv}'], form[f'g{g.id}_bh{lv}'] = str(price), str(bh)
                if ch:
                    form[f'g{g.id}_ch{lv}'] = str(ch)
        if apply:
            pb.save_service(session, user, form)
    for name, unit, price, months, desc in FEES:
        if session.scalar(select(PriceService).where(PriceService.kind == 'PHI', func.lower(PriceService.name) == name.lower())):
            continue
        log.append(f'Phí    {name} · {price:,}'.replace(',', '.'))
        if apply:
            pb.save_service(session, user, dict(kind='PHI', name=name, unit=unit, price=str(price), warranty_months=str(months),
                                                description=desc, active='1'))
    for name, svc_name, lo, hi, gcode in SYMPTOMS:
        if session.scalar(select(RepairSymptom).where(func.lower(RepairSymptom.name) == name.lower())):
            continue
        svc = session.scalar(select(PriceService).where(PriceService.kind == 'GOI', func.lower(PriceService.name) == svc_name.lower()))
        if not svc and apply:
            continue
        log.append(f'Hiện tượng  {name} → {svc_name}')
        if apply:
            pb.save_symptom(session, user, dict(name=name, service_id=str(svc.id), level_min=str(lo), level_max=str(hi),
                                                group_id=str(groups[gcode].id) if gcode else ''))
    return log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--user', default='', help='tên đăng nhập giám đốc; mặc định giám đốc đầu tiên đang hoạt động')
    ap.add_argument('--apply', action='store_true', help='ghi thật; không có thì chỉ chạy thử')
    args = ap.parse_args(argv)
    with Session(engine()) as session:
        stmt = select(User).where(User.active.is_(True), User.roles.any('director')).order_by(User.id)
        if args.user:
            stmt = select(User).where(User.username == args.user)
        user = session.scalar(stmt)
        if not user or not pb.can_edit(user):
            print('Không tìm thấy tài khoản giám đốc đang hoạt động (--user).')
            return 1
        log = run(session, user, args.apply)
        for line in log:
            print(line)
        print(f"{'Đã thêm' if args.apply else 'Chạy thử, sẽ thêm'} {len(log)} mục. Mục đã có giữ nguyên." if log else 'Không có gì mới để thêm.')
        if not args.apply and log:
            print('Thêm --apply để ghi thật.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

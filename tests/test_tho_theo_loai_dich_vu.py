"""Mẫu LoaiDvTho TUẤN duyệt 27/10/2026: màn thợ đổi nội dung theo 4 loại dịch vụ."""
from datetime import date

from app.models import Equipment, ServiceTicket
from test_my_work import people, purchase_people  # noqa: F401
from test_technician_stock import held, transfer, balance  # noqa: F401
from test_ux1b_hoan_thanh_gop import job  # noqa: F401
from test_ux1e_gon_man_tho import data, post
from test_web import service_login


def page(client, p, t):
    service_login(client, p['technician'])
    return client.get(f'/tho/phieu/{t.id}').text


def test_sua_tai_cho_merged_chips_saved_into_report(client, db, job):  # noqa: F811
    p, _, t = job
    t.kind = 'SUA_TAI_CHO'; db.commit()
    html = page(client, p, t)
    assert 'data-report-fold' in html and 'Lỗi · việc đã làm' in html and 'bcg-result' in html and 'name="warranty_months"' in html
    assert html.index('id="tien"') < html.index('Thu tiền</p>')  # vật tư trước tiền
    form = data(client, p, t, repair_result='XONG', fault=['Kẹt giấy'], work=['Thay trục ép', 'Vệ sinh máy'], fault_found='', fix_done='')
    assert post(client, t, form).status_code == 303
    db.refresh(t)
    assert t.fault_found == 'Kẹt giấy' and t.fix_done == 'Đã sửa xong: Thay trục ép, Vệ sinh máy'


def test_muc_dinh_ky_no_report_photo_warranty_or_part_request(client, db, job):  # noqa: F811
    p, _, t = job
    t.kind = 'MUC_DINH_KY'; db.commit()
    html = page(client, p, t)
    assert 'data-check="report"' not in html and 'data-check="photo"' not in html
    assert 'Mực giao · kho của tôi' in html and 'Vật tư dùng · kho của tôi' in html and 'data-ink-materials' in html
    assert 'data-report-fold' not in html and 'Bảo hành sau sửa' not in html and 'Cần linh kiện' not in html
    assert '<input type="hidden" form="service-complete" name="warranty_months" value="0" data-no-restore>' in html
    form = data(client, p, t, fault_found='', fix_done='', warranty_months='0')
    form.pop('repair_result', None)
    assert post(client, t, form).status_code == 303
    db.refresh(t)
    assert t.status in ('HOAN_THANH', 'DA_DONG') and t.fix_done == 'Đã giao mực định kỳ' and t.warranty_months == 0


def test_bao_hanh_valid_shows_box_keeps_old_warranty_and_quick_recover(client, db, job):  # noqa: F811
    p, _, t = job
    t.kind = 'BAO_HANH'; t.warranty_basis = 'MAY_CON_HAN'
    if t.serial:
        db.get(Equipment, t.serial).warranty_until = date(2026, 12, 15)
    db.commit()
    html = page(client, p, t)
    assert 'data-warranty-box="valid"' in html and 'Còn bảo hành' in html
    assert 'name="warranty_months"' not in html and 'Lỗi do khách dùng' in html
    assert 'Linh kiện hư mang về kho' in html and 'data-flush-draft' in html


def test_bao_hanh_expired_box_no_month_choice(client, db, job):  # noqa: F811
    # TUẤN 27/10: phiếu Bảo hành không chọn bảo hành sau sửa, theo chế độ phiếu cũ.
    p, _, t = job
    t.kind = 'BAO_HANH'; t.warranty_basis = 'HET_HAN'; db.commit()
    html = page(client, p, t)
    assert 'data-warranty-box="expired"' in html and 'name="warranty_months"' not in html and 'Bảo hành sau sửa' not in html


def test_dispatcher_ink_box_title(client, db, job):  # noqa: F811
    p, _, t = job
    t.kind = 'MUC_DINH_KY'; t.status = 'CHO_LINH_KIEN'; db.commit()
    service_login(client, p['dispatcher'])
    html = client.get(f'/dich-vu/{t.id}').text
    assert 'Mực giao cho thợ mang đi' in html or 'Chọn hộp mực giao' in html


def test_part_picker_two_lines_and_own_warehouse_label(client, db, job):  # noqa: F811
    # TUẤN 27/10: kết quả tìm linh kiện bị rối; mỗi mã 2 dòng, kho của thợ ghi "Kho thợ".
    p, _, t = job
    service_login(client, p['technician'])
    html = client.get('/hang-toi-giu/tim-linh-kien?q=may').text
    assert 'class="part-opt"' in html and 'part-opt-main' in html and 'part-opt-stock' in html


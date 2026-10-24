"""Nhãn tiền trên danh sách Đơn bán: đơn chưa giao không được ghi "Đã thu đủ"."""
from app.sales_orders import _money_label


def test_money_label_undelivered_order_is_not_paid_in_full():
    assert _money_label('DA_CHOT', 0, 0, 0, 0, 0) == 'Chưa giao · chưa thu'
    assert _money_label('DA_CHOT', 0, 0, 54_000, 0, 0) == 'Chờ xác nhận thu 54.000'
    assert _money_label('DA_CHOT', 0, 100_000, 0, 0, 100_000) == 'Khách ứng trước'
    assert _money_label('DA_HUY', 0, 0, 0, 0, 0) == 'Đã hủy · không phát sinh'
    assert _money_label('NHAP', 0, 0, 0, 0, 0) == 'Chưa phát sinh'


def test_money_label_delivered_orders():
    assert _money_label('GIAO_MOT_PHAN', 50_000, 50_000, 0, 0, 0) == 'Đã thu đủ phần đã giao'
    assert _money_label('DA_GIAO', 205_200, 205_200, 0, 0, 0) == 'Đã thu đủ'
    assert _money_label('DA_GIAO', 205_200, 0, 0, 205_200, 0) == 'Còn nợ'

from pathlib import Path

UI = (Path(__file__).resolve().parents[1] / 'app' / 'web' / 'static' / 'ui.js').read_text()


def test_shrink_never_hangs_and_uses_img_decode():
    # TUẤN 16/11: iPhone ra ảnh có mảng đen + nén treo khóa thanh vuốt Giao xong.
    assert 'PHOTO_TIMEOUT' in UI and 'Promise.race' in UI
    assert 'imageOrientation' not in UI and 'naturalWidth' in UI


def test_pick_slot_shows_state_and_delete():
    assert 'data-photo-state' in UI and 'Đang xử lý ảnh' in UI and 'Ảnh sẵn sàng' in UI
    assert 'data-photo-del' in UI and '✕ Xóa ảnh' in UI


def test_swipe_explains_blocked_resend():
    assert 'Lần gửi trước chưa xong' in UI
    assert 'delete button.form.dataset.submitting' in UI

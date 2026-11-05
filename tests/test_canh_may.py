"""Canh máy chủ Z2 (TUẤN duyệt mẫu 05/11/2026): script chỉ đọc, gom vấn đề rồi gửi n8n; n8n hỏng thì nhắn thẳng Zalo."""
import json
import os
from datetime import datetime

from scripts import canh_may as cm

VN = cm.VN


def _dump(dir_, ten, gio, kb):
    p = dir_ / 'daily' / ten
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b'x' * kb * 1024)
    os.utime(p, (gio.timestamp(), gio.timestamp()))
    return p


def test_doc_env_bo_ngoac_va_crlf(tmp_path):
    p = tmp_path / '.env'
    p.write_bytes('﻿TD_ZALO_BOT_TOKEN="abc:1"\r\n# ghi chú\r\nCANH_MAY_ZALO_CHAT_ID= 11, 22 \r\n'.encode())
    cfg = cm.cau_hinh(cm.doc_env(p))
    assert cfg['token'] == 'abc:1'
    assert cfg['chat_ids'] == ['11', '22']
    assert cfg['n8n_url'].endswith('/webhook/canh-may')


def test_lan_sao_luu_phai_co():
    # 23:30: cron 23:00 mới chạy 30 phút, chưa đòi bản hôm nay
    assert cm.lan_sao_luu_phai_co(datetime(2026, 11, 5, 23, 30, tzinfo=VN), '23:00') == datetime(2026, 11, 4, 23, 0, tzinfo=VN)
    # 00:31 hôm sau: đã quá 90 phút, phải có bản 23:00 hôm qua
    assert cm.lan_sao_luu_phai_co(datetime(2026, 11, 6, 0, 31, tzinfo=VN), '23:00') == datetime(2026, 11, 5, 23, 0, tzinfo=VN)
    assert cm.lan_sao_luu_phai_co(datetime(2026, 11, 6, 14, 0, tzinfo=VN), '23:00') == datetime(2026, 11, 5, 23, 0, tzinfo=VN)


def test_sao_luu_thieu_ban_dem_qua(tmp_path):
    _dump(tmp_path, 'thanhduc-2026-11-04.dump', datetime(2026, 11, 4, 23, 2, tzinfo=VN), 40)
    vd, tom = cm.kiem_sao_luu(tmp_path, datetime(2026, 11, 6, 0, 40, tzinfo=VN), '23:00', '')
    assert [v['ma'] for v in vd] == ['sao-luu']
    assert '04/11 23:02' in vd[0]['chi_tiet']
    assert tom.startswith('sao lưu 04/11 23:02')


def test_sao_luu_du_va_ban_nho_bat_thuong(tmp_path):
    _dump(tmp_path, 'thanhduc-2026-11-04.dump', datetime(2026, 11, 4, 23, 2, tzinfo=VN), 40)
    _dump(tmp_path, 'thanhduc-2026-11-05.dump', datetime(2026, 11, 5, 23, 2, tzinfo=VN), 40)
    assert cm.kiem_sao_luu(tmp_path, datetime(2026, 11, 6, 8, 0, tzinfo=VN), '23:00', '')[0] == []
    _dump(tmp_path, 'thanhduc-2026-11-06.dump', datetime(2026, 11, 6, 23, 2, tzinfo=VN), 10)
    vd, _ = cm.kiem_sao_luu(tmp_path, datetime(2026, 11, 7, 8, 0, tzinfo=VN), '23:00', '')
    assert [v['ma'] for v in vd] == ['sao-luu-nho']


def test_google_drive_chua_day(tmp_path):
    _dump(tmp_path, 'thanhduc-2026-11-05.dump', datetime(2026, 11, 5, 23, 2, tzinfo=VN), 40)
    now = datetime(2026, 11, 6, 8, 0, tzinfo=VN)
    assert [v['ma'] for v in cm.kiem_sao_luu(tmp_path, now, '23:00', 'gdrive:x')[0]] == ['google-drive']
    dau = tmp_path / 'rclone-ok'
    dau.touch()
    t = datetime(2026, 11, 5, 23, 10, tzinfo=VN).timestamp()
    os.utime(dau, (t, t))
    assert cm.kiem_sao_luu(tmp_path, now, '23:00', 'gdrive:x')[0] == []
    # Vừa sao lưu xong 20 phút, rclone còn đang đẩy: chưa báo
    _dump(tmp_path, 'thanhduc-2026-11-06.dump', datetime(2026, 11, 6, 23, 2, tzinfo=VN), 40)
    assert cm.kiem_sao_luu(tmp_path, datetime(2026, 11, 6, 23, 22, tzinfo=VN), '23:00', 'gdrive:x')[0] == []


def test_ram_can_3_lan_lien_tiep():
    vd, tom = cm.kiem_ram('MemTotal: 16000000 kB\nMemFree: 1 kB\nMemAvailable: 1200000 kB\n')
    assert vd[0]['ma'] == 'ram' and vd[0]['can_lien_tiep'] == 3
    assert tom == 'RAM trống 8%'
    assert cm.kiem_ram('MemTotal: 100 kB\nMemAvailable: 60 kB\n') == ([], 'RAM trống 60%')


def test_cac_phan_cua_app():
    rows = cm.doc_compose_ps('\n'.join(json.dumps(r) for r in [
        {'Service': 'db', 'State': 'running', 'Health': 'healthy'},
        {'Service': 'api', 'State': 'running', 'Health': ''},
        {'Service': 'cloudflared', 'State': 'exited', 'Health': ''},
    ]))
    vd, tom = cm.kiem_phan(rows, ['db', 'api', 'dang-web-may', 'cloudflared'], 'running')
    assert {v['ma'] for v in vd} == {'phan-dang-web-may', 'phan-cloudflared'}
    assert 'docker compose' in vd[0]['chi_tiet']
    assert tom == '3/5 phần đang chạy'
    assert cm.doc_compose_ps('[{"Service": "db", "State": "running"}]')[0]['Service'] == 'db'


def test_web_phan_biet_tunnel_va_app():
    vd, _ = cm.kiem_web((False, 'mã 530'), (True, 'mã 200'))
    assert vd[0]['can_lien_tiep'] == 2 and 'tunnel' in vd[0]['chi_tiet']
    vd, _ = cm.kiem_web((False, 'URLError'), (False, 'URLError'))
    assert 'phần app đang hỏng' in vd[0]['chi_tiet']
    assert cm.kiem_web((True, 'mã 200'), (False, 'x')) == ([], 'web mở được')


def test_khoi_dong_lai_bao_mot_lan():
    st = {}
    now = datetime(2026, 11, 6, 9, 0, tzinfo=VN)
    assert cm.su_kien_khoi_dong(st, 'boot-1', 300, now) == []  # lần đầu sau khi cài
    assert cm.su_kien_khoi_dong(st, 'boot-1', 900, now) == []
    tin = cm.su_kien_khoi_dong(st, 'boot-2', 300, now)
    assert len(tin) == 1 and '08:55' in tin[0]
    assert cm.su_kien_khoi_dong(st, 'boot-2', 900, now) == []


def test_n8n_hong_nhan_thang_toi_da_2_gio():
    payload = {'van_de': [cm.van_de('dia', 'vang', 'Ổ đĩa đã dùng 82%')], 'tom_tat': 'đĩa 82%'}
    st = {}
    t0 = datetime(2026, 11, 6, 9, 0, tzinfo=VN).timestamp()
    tin = cm.xu_ly_n8n_hong(st, payload, 'URLError', t0)
    assert 'n8n không trả lời' in tin and '🟡 Ổ đĩa đã dùng 82%' in tin
    assert cm.xu_ly_n8n_hong(st, payload, 'URLError', t0 + 600) is None
    assert cm.xu_ly_n8n_hong(st, payload, 'URLError', t0 + 2 * 3600) is not None


def test_main_n8n_tra_tin_thi_gui_zalo(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(cm, 'cau_hinh', lambda env: {
        'token': 't', 'chat_ids': ['1'], 'n8n_url': 'http://x', 'khoa': 'k', 'state': tmp_path / 's.json'})
    monkeypatch.setattr(cm, 'gom_tinh_trang', lambda cfg, now: {'van_de': [], 'tom_tat': 'ổn'})
    monkeypatch.setattr(cm, 'boot_id', lambda: 'b1')
    monkeypatch.setattr(cm, 'gui_zalo', lambda cfg, text: sent.append(text) or True)
    (tmp_path / 's.json').write_text(json.dumps({'boot_id': 'b1', 'n8n_hong_bao_luc': 1}))
    monkeypatch.setattr(cm, 'gui_n8n', lambda cfg, payload: ['🟢 Báo cáo sáng · máy chủ ổn'])
    assert cm.main([]) == 0
    assert sent == ['🟢 n8n đã trả lời lại, tin canh máy đi qua n8n như cũ.', '🟢 Báo cáo sáng · máy chủ ổn']
    assert 'n8n_hong_bao_luc' not in json.loads((tmp_path / 's.json').read_text())


def test_workflow_n8n_hop_le():
    wf = json.loads((cm.ROOT / 'deploy' / 'n8n' / 'canh-may.json').read_text())
    types = [n['type'] for n in wf['nodes']]
    assert types == ['n8n-nodes-base.webhook', 'n8n-nodes-base.code', 'n8n-nodes-base.respondToWebhook']
    hook = wf['nodes'][0]['parameters']
    assert hook['path'] == 'canh-may' and hook['authentication'] == 'headerAuth'
    assert wf['settings']['timezone'] == 'Asia/Ho_Chi_Minh'
    assert 'getWorkflowStaticData' in wf['nodes'][1]['parameters']['jsCode']


def test_khong_doc_duoc_docker_chi_bao_mot_dong():
    vd, tom = cm.kiem_phan([], ['db', 'api'], 'running')
    assert [v['ma'] for v in vd] == ['docker'] and tom == 'không đọc được Docker'

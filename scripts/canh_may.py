#!/usr/bin/env python3
"""Canh máy chủ Z2 (TUẤN duyệt mẫu 05/11/2026): kiểm tra mỗi 10 phút, nhắn Zalo khi có chuyện.

Chạy trên máy chủ bằng cron của user tuan (chỉ cần python3 và docker, không cần cài thêm gì):
  */10 * * * * cd /home/tuan/THANHDUC-APP && python3 scripts/canh_may.py >> /var/backups/thanhduc/canh-may.log 2>&1

Script CHỈ ĐỌC: không xóa, không khởi động lại, không vào database. Nó gom tình trạng máy rồi gửi cho n8n
(workflow deploy/n8n/canh-may.json). n8n quyết định nhắn gì (lỗi mới, nhắc lại mỗi 2 giờ, đã ổn lại, báo cáo 8:00)
và trả danh sách tin về; script gửi các tin đó qua bot Zalo của app. Token bot chỉ nằm trong deploy/.env.
Nếu n8n không trả lời, script tự nhắn thẳng Zalo (tối đa 2 giờ một lần) để vẫn có cảnh báo.

Biến trong deploy/.env:
  TD_ZALO_BOT_TOKEN       token bot Zalo (đã có cho màn Tin nhắn Zalo)
  CANH_MAY_ZALO_CHAT_ID   mã cuộc trò chuyện nhận tin, xem ở màn Tin nhắn Zalo; nhiều mã cách nhau dấu phẩy
  CANH_MAY_N8N_URL        mặc định http://192.168.10.200:5678/webhook/canh-may
  CANH_MAY_KHOA           mã bí mật gửi kèm header X-Canh-May-Khoa, trùng với credential trong n8n
Tùy chọn: CANH_MAY_WEB_URL, CANH_MAY_GIO_SAO_LUU (23:00), CANH_MAY_N8N_CONTAINER (n8n-n8n-1), BACKUP_DIR, RCLONE_REMOTE.

Lệnh thử:
  python3 scripts/canh_may.py --xem       in tình trạng, không gửi gì
  python3 scripts/canh_may.py --thu-zalo  gửi một tin thử qua Zalo để kiểm tra token và mã cuộc trò chuyện
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

VN = timezone(timedelta(hours=7))
ROOT = Path(__file__).resolve().parent.parent
ZALO_API = 'https://bot-api.zaloplatforms.com/bot'
MAX_TEXT = 2000
NHAC_LAI_GIAY = 2 * 3600
TEN_PHAN = {'api': 'Phần app (api)', 'db': 'Database', 'cloudflared': 'Tunnel Cloudflare',
            'dang-web-may': 'Máy đăng web', 'n8n': 'n8n'}
LENH_CAP_NHAT = ('cd ~/THANHDUC-APP && docker compose -f deploy/docker-compose.yml '
                 '-f deploy/docker-compose.tunnel.yml --env-file deploy/.env up -d --build')


# ---------- đọc cấu hình ----------

def doc_env(path: Path) -> dict[str, str]:
    """Đọc deploy/.env như _compose.sh: không chạy file, bỏ \\r và ngoặc bao quanh."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip().replace('\r', '')
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in '"\'':
            val = val[1:-1]
        out[key.strip()] = val
    return out


def cau_hinh(env: dict[str, str]) -> dict:
    def get(key: str, default: str = '') -> str:
        return os.environ.get(key) or env.get(key) or default
    return {
        'token': get('TD_ZALO_BOT_TOKEN').strip(),
        'chat_ids': [c.strip() for c in get('CANH_MAY_ZALO_CHAT_ID').split(',') if c.strip()],
        'n8n_url': get('CANH_MAY_N8N_URL', 'http://192.168.10.200:5678/webhook/canh-may'),
        'khoa': get('CANH_MAY_KHOA'),
        'web_url': get('CANH_MAY_WEB_URL', 'https://quanlythanhduc.com/health'),
        'noi_bo_url': get('CANH_MAY_NOI_BO_URL', 'http://127.0.0.1:8010/health'),
        'gio_sao_luu': get('CANH_MAY_GIO_SAO_LUU', '23:00'),
        'n8n_container': get('CANH_MAY_N8N_CONTAINER', 'n8n-n8n-1'),
        'backup_dir': Path(get('BACKUP_DIR', '/var/backups/thanhduc')),
        'rclone': get('RCLONE_REMOTE'),
        'tunnel': bool(get('TUNNEL_TOKEN')),
        'state': Path(get('CANH_MAY_STATE', str(Path.home() / '.thanhduc-canh-may.json'))),
    }


# ---------- các phép kiểm tra (hàm thuần, dễ kiểm thử) ----------

def van_de(ma: str, muc: str, tieu_de: str, chi_tiet: str = '', can_lien_tiep: int = 1) -> dict:
    return {'ma': ma, 'muc': muc, 'tieu_de': tieu_de, 'chi_tiet': chi_tiet, 'can_lien_tiep': can_lien_tiep}


def hd(t: datetime) -> str:
    return t.astimezone(VN).strftime('%d/%m %H:%M')


def lan_sao_luu_phai_co(now: datetime, gio: str) -> datetime:
    """Lần sao lưu gần nhất đáng lẽ đã xong: giờ chạy cron gần nhất cách hiện tại ít nhất 90 phút."""
    h, m = (int(x) for x in gio.split(':'))
    now = now.astimezone(VN)
    moc = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if moc > now:
        moc -= timedelta(days=1)
    if now - moc < timedelta(minutes=90):
        moc -= timedelta(days=1)
    return moc


def kiem_sao_luu(dir_: Path, now: datetime, gio: str, rclone: str) -> tuple[list[dict], str]:
    files = sorted((dir_ / 'daily').glob('thanhduc-2*.dump'))
    if not files:
        return [van_de('sao-luu', 'do', 'Chưa có bản sao lưu nào',
                       f'Không thấy file trong {dir_}/daily. Anh kiểm tra cron 23:00 (crontab -l).')], 'sao lưu: chưa có'
    moi = files[-1]
    st = moi.stat()
    t_moi = datetime.fromtimestamp(st.st_mtime, VN)
    out: list[dict] = []
    phai_co = lan_sao_luu_phai_co(now, gio)
    if t_moi < phai_co - timedelta(minutes=5):
        out.append(van_de('sao-luu', 'do', 'Sao lưu đêm qua chưa có',
                          f'Bản gần nhất: {hd(t_moi)}. Anh kiểm tra: tail -30 {dir_}/backup.log'))
    elif len(files) >= 2:
        cu = files[-2].stat().st_size
        if cu > 0 and st.st_size < cu / 2:
            out.append(van_de('sao-luu-nho', 'do', 'Bản sao lưu mới nhỏ bất thường',
                              f'{moi.name}: {st.st_size // 1024} KB, bản trước {cu // 1024} KB. Có thể bị lỗi giữa chừng.'))
    if rclone:
        dau = dir_ / 'rclone-ok'
        t_dau = datetime.fromtimestamp(dau.stat().st_mtime, VN) if dau.exists() else None
        if (t_dau is None or t_dau < t_moi) and now.astimezone(VN) - t_moi > timedelta(minutes=60):
            lan = hd(t_dau) if t_dau else 'chưa có'
            out.append(van_de('google-drive', 'do', 'Chưa đẩy được bản sao lưu lên Google Drive',
                              f'Lần đẩy thành công gần nhất: {lan}. Anh kiểm tra: tail -30 {dir_}/backup.log'))
    mb = st.st_size / 1024 / 1024
    return out, f'sao lưu {hd(t_moi)} ({mb:.0f} MB)'


def kiem_dia(paths: list[Path]) -> tuple[list[dict], str]:
    worst = None
    for p in paths:
        try:
            u = shutil.disk_usage(p)
        except OSError:
            continue
        pct = round(u.used * 100 / u.total)
        if worst is None or pct > worst[0]:
            worst = (pct, u.free, p)
    if worst is None:
        return [], 'đĩa: ?'
    pct, free, _ = worst
    con = f'Còn {free // 1024 ** 3} GB. Dọn bộ nhớ đệm Docker: docker builder prune -f (không dùng volume prune).'
    if pct >= 90:
        return [van_de('dia', 'do', f'Ổ đĩa đã dùng {pct}%', con)], f'đĩa {pct}%'
    if pct >= 80:
        return [van_de('dia', 'vang', f'Ổ đĩa đã dùng {pct}%', con)], f'đĩa {pct}%'
    return [], f'đĩa {pct}%'


def kiem_ram(meminfo: str) -> tuple[list[dict], str]:
    vals = {}
    for line in meminfo.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(':') in ('MemTotal', 'MemAvailable'):
            vals[parts[0].rstrip(':')] = int(parts[1])
    if not vals.get('MemTotal') or 'MemAvailable' not in vals:
        return [], 'RAM: ?'
    pct = round(vals['MemAvailable'] * 100 / vals['MemTotal'])
    if pct < 10:
        return [van_de('ram', 'vang', f'RAM chỉ còn trống {pct}%', 'Kéo dài 30 phút. Xem phần nào ăn RAM: docker stats --no-stream',
                       can_lien_tiep=3)], f'RAM trống {pct}%'
    return [], f'RAM trống {pct}%'


def doc_compose_ps(text: str) -> list[dict]:
    """docker compose ps --format json: bản mới in mỗi dòng một JSON, bản cũ in một mảng."""
    text = text.strip()
    if not text:
        return []
    if text.startswith('['):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def kiem_phan(rows: list[dict], can_co: list[str], n8n_trang_thai: str | None) -> tuple[list[dict], str]:
    out: list[dict] = []
    if not rows:
        # Không đọc được docker compose: báo một dòng thay vì báo từng phần mất.
        out.append(van_de('docker', 'do', 'Không đọc được tình trạng các phần của app',
                          'Lệnh docker compose ps không trả lời. Anh thử: docker ps'))
        return out, 'không đọc được Docker'
    by = {r.get('Service'): r for r in rows}
    ok = 0
    tong = len(can_co) + 1
    for svc in can_co:
        r = by.get(svc)
        ten = TEN_PHAN.get(svc, svc)
        if r is None:
            out.append(van_de(f'phan-{svc}', 'do', f'{ten} không có', f'Anh chạy lệnh cập nhật chuẩn để bật lại:\n{LENH_CAP_NHAT}'))
        elif (r.get('State') or '').lower() != 'running':
            out.append(van_de(f'phan-{svc}', 'do', f'{ten} đang dừng ({r.get("State")})',
                              f'Anh chạy lệnh cập nhật chuẩn để bật lại:\n{LENH_CAP_NHAT}'))
        elif (r.get('Health') or '').lower() == 'unhealthy':
            out.append(van_de(f'phan-{svc}', 'do', f'{ten} báo lỗi sức khỏe', 'Xem nhật ký: docker logs --tail 50 ' + (r.get('Name') or svc)))
        else:
            ok += 1
    if n8n_trang_thai == 'running':
        ok += 1
    else:
        out.append(van_de('phan-n8n', 'do', f'n8n đang dừng ({n8n_trang_thai or "không thấy"})',
                          'Anh vào thư mục cài n8n và chạy: docker compose up -d'))
    return out, f'{ok}/{tong} phần đang chạy'


def kiem_web(web_ok: tuple[bool, str], noi_bo_ok: tuple[bool, str]) -> tuple[list[dict], str]:
    if web_ok[0]:
        return [], 'web mở được'
    if noi_bo_ok[0]:
        ct = f'Từ internet lỗi ({web_ok[1]}), trong máy app vẫn chạy: thường do tunnel Cloudflare hoặc mạng.'
    else:
        ct = f'Từ internet lỗi ({web_ok[1]}) và trong máy cũng không mở được ({noi_bo_ok[1]}): phần app đang hỏng.'
    return [van_de('web', 'do', 'Web quanlythanhduc.com không mở được', ct, can_lien_tiep=2)], 'web KHÔNG mở được'


# ---------- đọc máy thật ----------

def chay(cmd: list[str], timeout: int = 30) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT, check=False)
        return r.stdout if r.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def mo_url(url: str, timeout: int = 15) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers={'User-Agent': 'ThanhDuc-CanhMay/1'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, f'mã {resp.status}'
    except urllib.error.HTTPError as exc:
        return False, f'mã {exc.code}'
    except Exception as exc:  # noqa: BLE001 - mọi lỗi mạng đều là "không mở được"
        return False, type(exc).__name__


def gom_tinh_trang(cfg: dict, now: datetime) -> dict:
    issues: list[dict] = []
    tom: list[str] = []
    for kq in (
        kiem_dia([Path('/'), cfg['backup_dir']]),
        kiem_ram(Path('/proc/meminfo').read_text() if Path('/proc/meminfo').exists() else ''),
        kiem_sao_luu(cfg['backup_dir'], now, cfg['gio_sao_luu'], cfg['rclone']),
        kiem_web(mo_url(cfg['web_url']), mo_url(cfg['noi_bo_url'], timeout=5)),
    ):
        issues += kq[0]
        tom.append(kq[1])
    dc = ['docker', 'compose', '-f', 'deploy/docker-compose.yml']
    can_co = ['db', 'api', 'dang-web-may']
    if cfg['tunnel']:
        dc += ['-f', 'deploy/docker-compose.tunnel.yml']
        can_co.append('cloudflared')
    dc += ['--env-file', 'deploy/.env', 'ps', '-a', '--format', 'json']
    try:
        rows = doc_compose_ps(chay(dc))
    except ValueError:
        rows = []
    n8n = chay(['docker', 'inspect', '-f', '{{.State.Status}}', cfg['n8n_container']]).strip() or None
    kq = kiem_phan(rows, can_co, n8n)
    issues += kq[0]
    tom.append(kq[1])
    return {'thoi_gian': now.astimezone(VN).isoformat(timespec='seconds'), 'may': os.uname().nodename,
            'van_de': issues, 'tom_tat': ' · '.join(tom)}


def boot_id() -> str:
    try:
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return ''


def uptime_giay() -> float:
    try:
        return float(Path('/proc/uptime').read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def su_kien_khoi_dong(state: dict, bid: str, up: float, now: datetime) -> list[str]:
    """Báo một lần khi máy vừa khởi động lại (mất điện hoặc reboot)."""
    if not bid or state.get('boot_id') == bid:
        return []
    cu = state.get('boot_id')
    state['boot_id'] = bid
    if not cu:
        return []  # lần chạy đầu tiên sau khi cài: chưa biết máy có vừa khởi động lại hay không
    luc = hd(now - timedelta(seconds=up))
    return [(f'🟡 Máy chủ vừa khởi động lại lúc {luc} (mất điện hoặc có người khởi động lại). '
             'Nhớ bật lại Claude Remote Control nếu cần (tmux new -s claude).')]


# ---------- gửi tin ----------

def gui_n8n(cfg: dict, payload: dict) -> list[str]:
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(cfg['n8n_url'], data=data, method='POST',
                                 headers={'Content-Type': 'application/json', 'X-Canh-May-Khoa': cfg['khoa']})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read() or b'{}')
    if isinstance(body, list):
        body = body[0] if body else {}
    gui = body.get('gui') if isinstance(body, dict) else None
    if not isinstance(gui, list):
        raise TypeError('n8n trả về không có danh sách "gui"')
    return [str(t) for t in gui if str(t).strip()]


def gui_zalo(cfg: dict, text: str) -> bool:
    if not cfg['token'] or not cfg['chat_ids']:
        print('  Chưa có TD_ZALO_BOT_TOKEN hoặc CANH_MAY_ZALO_CHAT_ID, không gửi được Zalo.')
        return False
    ok = True
    for chat in cfg['chat_ids']:
        req = urllib.request.Request(f"{ZALO_API}{cfg['token']}/sendMessage", method='POST',
                                     data=json.dumps({'chat_id': chat, 'text': text[:MAX_TEXT]}).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read() or b'{}')
            if not body.get('ok'):
                ok = False
                print(f'  Zalo từ chối: {str(body.get("description") or body)[:200]}')
        except Exception as exc:  # noqa: BLE001 - không in URL vì có token
            ok = False
            print(f'  Lỗi gửi Zalo: {type(exc).__name__}')
    return ok


def tin_du_phong(payload: dict, loi: str) -> str:
    dong = [f'🔴 n8n không trả lời ({loi}). Tin canh máy tạm gửi thẳng, nhắc lại sau 2 giờ nếu chưa ổn.']
    for v in payload['van_de']:
        dau = '🔴' if v['muc'] == 'do' else '🟡'
        dong.append(f"{dau} {v['tieu_de']}")
    dong.append(payload['tom_tat'])
    return '\n'.join(dong)


def xu_ly_n8n_hong(state: dict, payload: dict, loi: str, now_ts: float) -> str | None:
    """Trả tin cần gửi thẳng khi n8n hỏng, tối đa 2 giờ một lần."""
    lan = state.get('n8n_hong_bao_luc')
    if lan and now_ts - lan < NHAC_LAI_GIAY:
        return None
    state['n8n_hong_bao_luc'] = now_ts
    return tin_du_phong(payload, loi)


def doc_state(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def ghi_state(p: Path, state: dict) -> None:
    try:
        p.write_text(json.dumps(state))
    except OSError as exc:
        print(f'  Không ghi được {p}: {exc}')


def main(argv: list[str]) -> int:
    cfg = cau_hinh(doc_env(ROOT / 'deploy' / '.env'))
    now = datetime.now(VN)
    if '--thu-zalo' in argv:
        ok = gui_zalo(cfg, f'🟢 Tin thử canh máy chủ từ {os.uname().nodename} lúc {hd(now)}. Nhận được tin này là Zalo đã thông.')
        print('Đã gửi tin thử.' if ok else 'Gửi tin thử KHÔNG được.')
        return 0 if ok else 1
    payload = gom_tinh_trang(cfg, now)
    if '--xem' in argv:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    state = doc_state(cfg['state'])
    tin = su_kien_khoi_dong(state, boot_id(), uptime_giay(), now)
    try:
        if not cfg['khoa']:
            raise ValueError('chưa đặt CANH_MAY_KHOA')
        tin += gui_n8n(cfg, payload)
        if state.pop('n8n_hong_bao_luc', None):
            tin.insert(0, '🟢 n8n đã trả lời lại, tin canh máy đi qua n8n như cũ.')
        trang_thai = 'n8n ok'
    except Exception as exc:  # noqa: BLE001
        loi = str(exc)[:120] if isinstance(exc, (ValueError, TypeError)) else type(exc).__name__
        du_phong = xu_ly_n8n_hong(state, payload, loi, now.timestamp())
        if du_phong:
            tin.append(du_phong)
        trang_thai = f'n8n lỗi: {loi}'
    for t in tin:
        gui_zalo(cfg, t)
    ghi_state(cfg['state'], state)
    print(f"{now.isoformat(timespec='seconds')} {trang_thai} · {len(payload['van_de'])} vấn đề · {len(tin)} tin · {payload['tom_tat']}")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

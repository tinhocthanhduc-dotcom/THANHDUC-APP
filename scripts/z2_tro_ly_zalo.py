#!/usr/bin/env python3
"""Codex hoặc Claude trên máy chủ Z2 trả lời Trợ lý Zalo cho nhân viên, bằng gói thuê bao (TUẤN 05/11/2026).

Cron gọi mỗi phút, chỉ dùng thư viện có sẵn của Python:
    * * * * * python3 ~/THANHDUC-APP/scripts/z2_tro_ly_zalo.py >> ~/.thanhduc-zalo-ai.log 2>&1
Mỗi lần chạy hỏi app vài giây một lần trong ~55 giây (TD_ZALO_HOI_GIAY, TD_ZALO_CHAY_GIAY). Lần hỏi cũng là nhịp
để app biết máy Z2 còn chạy. Mỗi lượt app gửi một câu lệnh chữ; script đưa cho `codex exec` hoặc `claude -p`
(app chọn theo cấu hình giám đốc ở màn Trợ lý Zalo), rồi gửi chữ trả lời về app.

Codex/Claude KHÔNG được chạy lệnh, đọc tệp hay vào mạng: Codex ở chế độ chỉ đọc trong thư mục tạm trống, Claude
tắt hết công cụ. Lượt có ảnh (đợt 3, ảnh nhân viên gửi): script tải ảnh vào thư mục tạm; Codex nhận ảnh qua -i,
Claude chỉ được mở công cụ Read để xem anh.jpg. Chúng chỉ trả về JSON "gọi công cụ nào" hoặc "câu trả lời"; app tự tra tồn, tìm khách, lập đơn
nháp theo quyền của người hỏi. Không đọc database, không đọc deploy/.env.

Khóa đọc từ ~/.thanhduc-ai-key-zalo (chmod 600) hoặc TD_AI_ZALO_KEY. App ở http://127.0.0.1:8010 (TD_AI_URL).
Tùy chọn: TD_ZALO_CODEX_THEM (mặc định đặt suy nghĩ thấp cho nhanh), TD_ZALO_CLAUDE_MODEL (mặc định sonnet),
TD_ZALO_CLAUDE_THEM, TD_ZALO_TIMEOUT (mặc định 120 giây mỗi lượt), TD_ZALO_CUNG_LUC (mặc định 2 lượt cùng lúc).
Thử tay: `python3 scripts/z2_tro_ly_zalo.py --thu codex` (hoặc claude) chạy một câu mẫu, không gọi app.
"""
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HOME = Path.home()
HOI_GIAY = float(os.environ.get('TD_ZALO_HOI_GIAY', '2'))
CHAY_GIAY = int(os.environ.get('TD_ZALO_CHAY_GIAY', '55'))
CHO_GIAY = int(os.environ.get('TD_ZALO_TIMEOUT', '120'))
CUNG_LUC = max(1, min(int(os.environ.get('TD_ZALO_CUNG_LUC', '2')), 4))
THU_MUC = HOME / '.thanhduc-zalo-ai'
MAU_THU = ('Bạn là trợ lý. Công cụ được phép: {"ten": "tra_ton", "tham_so": {"tu_khoa": "string"}}. '
           'Hội thoại: [{"nguoi_hoi": "còn hộp mực 12A không"}]. Trả về DUY NHẤT một JSON: '
           '{"goi": [{"ten": "...", "vao": {...}}]} hoặc {"tra_loi": "..."}')


def log(msg):
    print(datetime.now().strftime('%d/%m %H:%M:%S'), msg, flush=True)


def _key():
    raw = os.environ.get('TD_AI_ZALO_KEY', '').strip()
    if not raw:
        path = Path(os.environ.get('TD_AI_ZALO_KEY_FILE', HOME / '.thanhduc-ai-key-zalo'))
        try:
            raw = path.read_text().strip()
        except OSError:
            sys.exit(f'Chưa có khóa: tạo khóa "Codex/Claude · Trợ lý Zalo" ở màn Trợ lý AI rồi lưu vào {path} (chmod 600).')
    return raw


class LoiApp(Exception):
    def __init__(self, code, message):
        super().__init__(f'Lỗi {code}: {message}')
        self.code = code


def call(method, path, body=None):
    url = os.environ.get('TD_AI_URL', 'http://127.0.0.1:8010').rstrip('/') + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + _key(), 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b'null')
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read())
            message = detail.get('message') or detail.get('detail') or detail
        except ValueError:
            message = exc.reason
        raise LoiApp(exc.code, message) from exc
    except urllib.error.URLError as exc:
        raise LoiApp(0, f'Không kết nối được app ở {url}: {exc.reason}') from exc


def tai_anh(path: str, dich: Path) -> Path:
    """Đợt 3: tải ảnh của lượt (ảnh nhân viên gửi qua Zalo) về thư mục tạm của lượt."""
    url = os.environ.get('TD_AI_URL', 'http://127.0.0.1:8010').rstrip('/') + path
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + _key()})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            dich.write_bytes(resp.read(20 * 1024 * 1024))
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f'Không tải được ảnh của lượt: {exc}') from exc
    return dich


def _bin(name):
    found = os.environ.get(f'TD_{name.upper()}_BIN') or shutil.which(name)
    if found:
        return found
    for p in (HOME / '.local/bin' / name, HOME / '.npm-global/bin' / name, Path('/usr/local/bin') / name, Path('/usr/bin') / name):
        if p.exists():
            return str(p)
    nvm = sorted((HOME / '.nvm/versions/node').glob(f'*/bin/{name}'))
    return str(nvm[-1]) if nvm else None


def _loi(kq, ten):
    duoi = (kq.stderr or kq.stdout or '').strip().splitlines()[-3:]
    return RuntimeError(f'{ten} thoát mã {kq.returncode}: ' + ' | '.join(duoi)[:250])


def hoi_codex(lenh: str, thu_muc: Path, anh: Path | None = None) -> str:
    exe = _bin('codex')
    if not exe:
        raise RuntimeError('Không thấy lệnh codex trên máy chủ.')
    ra = thu_muc / 'tra-loi.txt'
    cmd = [exe, 'exec', '--skip-git-repo-check', '--sandbox', 'read-only', '--color', 'never', '-C', str(thu_muc), '-o', str(ra)]
    cmd += shlex.split(os.environ.get('TD_ZALO_CODEX_THEM', '-c model_reasoning_effort="low"'))
    if anh is not None:
        cmd += ['-i', str(anh)]  # đợt 3: đính kèm ảnh cho Codex đọc
    cmd.append('-')
    kq = subprocess.run(cmd, input=lenh, text=True, capture_output=True, cwd=thu_muc, timeout=CHO_GIAY)
    tra_loi = ra.read_text(encoding='utf-8').strip() if ra.exists() else ''
    if kq.returncode != 0 or not tra_loi:
        raise _loi(kq, 'codex')
    return tra_loi


def hoi_claude(lenh: str, thu_muc: Path, anh: Path | None = None) -> str:
    exe = _bin('claude')
    if not exe:
        raise RuntimeError('Không thấy lệnh claude trên máy chủ.')
    # --tools "" tắt mọi công cụ: Claude chỉ đọc câu lệnh và trả chữ.
    # Lượt có ảnh (đợt 3): chỉ mở công cụ Read để đọc anh.jpg trong thư mục tạm của lượt, không gì khác.
    tools = ['--tools', 'Read', '--allowedTools', 'Read'] if anh is not None else ['--tools', '']
    cmd = [exe, '-p', '--output-format', 'text', *tools, '--model', os.environ.get('TD_ZALO_CLAUDE_MODEL', 'sonnet')]
    cmd += shlex.split(os.environ.get('TD_ZALO_CLAUDE_THEM', ''))
    kq = subprocess.run(cmd, input=lenh, text=True, capture_output=True, cwd=thu_muc, timeout=CHO_GIAY)
    tra_loi = (kq.stdout or '').strip()
    if kq.returncode != 0 or not tra_loi:
        raise _loi(kq, 'claude')
    return tra_loi


def hoi(may: str, lenh: str, anh_url: str = '') -> str:
    THU_MUC.mkdir(mode=0o700, exist_ok=True)
    thu_muc = Path(tempfile.mkdtemp(prefix='luot-', dir=THU_MUC))
    try:
        anh = tai_anh(anh_url, thu_muc / 'anh.jpg') if anh_url else None
        if may == 'claude':
            return hoi_claude(lenh, thu_muc, anh)
        if may == 'codex':
            return hoi_codex(lenh, thu_muc, anh)
        raise RuntimeError(f'Không biết máy "{may}".')
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'{may} chạy quá {CHO_GIAY} giây') from exc
    finally:
        shutil.rmtree(thu_muc, ignore_errors=True)


def lam(v: dict) -> None:
    ten = f"lượt {v['id']} · {v['may']}"
    bat_dau = time.monotonic()
    try:
        tra_loi = hoi(v['may'], v['lenh'], v.get('anh') or '')
    except Exception as exc:  # hết lượt gói, chưa đăng nhập, quá giờ: báo app để app trả lời người hỏi
        log(f'{ten}: không làm được ({exc})')
        try:
            call('POST', f"/ai/zalo/viec/{v['id']}/khong-lam-duoc", {'ly_do': str(exc)[:300]})
        except LoiApp as e2:
            log(f'{ten}: {e2}')
        return
    try:
        call('POST', f"/ai/zalo/viec/{v['id']}/tra-loi", {'tra_loi': tra_loi[:20000]})
        log(f'{ten}: đã trả lời sau {time.monotonic() - bat_dau:.0f} giây')
    except LoiApp as exc:
        log(f'{ten}: {exc}')


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--thu':
        bat_dau = time.monotonic()
        print(hoi(sys.argv[2], MAU_THU))
        print(f'({time.monotonic() - bat_dau:.0f} giây)')
        return
    khoa = open(HOME / '.thanhduc-zalo-ai.lock', 'w')
    try:
        fcntl.flock(khoa, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # lượt trước còn đang chạy
    bat_dau = time.monotonic()
    dang = set()
    with ThreadPoolExecutor(CUNG_LUC) as pool:
        try:
            while time.monotonic() - bat_dau < CHAY_GIAY:
                dang = {f for f in dang if not f.done()}
                trong = CUNG_LUC - len(dang)
                viec = call('GET', f'/ai/zalo/viec?toi_da={trong}') if trong > 0 else None
                for v in viec or []:
                    dang.add(pool.submit(lam, v))
                time.sleep(HOI_GIAY)
        except LoiApp as exc:
            log(str(exc))
            if exc.code in (401, 403, 423):
                sys.exit(1)


if __name__ == '__main__':
    main()

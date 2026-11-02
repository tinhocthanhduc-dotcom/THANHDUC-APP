#!/usr/bin/env python3
"""Codex trên máy chủ Z2 làm việc Đăng web bằng gói ChatGPT, không tốn phí API (TUẤN 01/11/2026, đợt 2).

Cron gọi mỗi 5 phút, chỉ dùng thư viện có sẵn của Python:
    python3 ~/THANHDUC-APP/scripts/codex_dang_web.py
Mỗi lần chạy (đợt 1 ngày 02/11/2026): báo app còn đăng nhập ChatGPT không, rồi hỏi việc mỗi 30 giây trong ~4,5 phút
(TD_CODEX_HOI_GIAY, TD_CODEX_CHAY_GIAY) nên anh bấm xong chờ chừng nửa phút, không phải 5 phút. Mỗi lượt hỏi: hỏi app các câu lệnh đang chờ (qua cửa /ai/dang-web, khóa riêng của Codex), đưa từng câu lệnh cho
`codex exec`, rồi gửi câu trả lời về app. App chỉ lưu bản nháp; anh vẫn xem trước và bấm Đăng như cũ.
Codex lỗi hoặc hết lượt thì báo app "không làm được": app tự chuyển việc sang AI tự động (nếu có khóa API) hoặc dán tay.

Khóa đọc từ ~/.thanhduc-ai-key-web (chmod 600) hoặc TD_AI_WEB_KEY. App ở http://127.0.0.1:8010 (TD_AI_URL).
Không đọc database, không đọc deploy/.env, Codex chạy ở chế độ chỉ đọc trong thư mục riêng.
Vẽ ảnh minh họa bài viết (02/11/2026): Codex được ghi file chỉ trong một thư mục tạm của lượt vẽ, dùng công cụ
image_gen của gói ChatGPT; ảnh gửi về app qua /ai/dang-web/ve-anh rồi thư mục tạm bị xóa.
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
from datetime import datetime
from pathlib import Path

HOME = Path.home()
TOI_DA_MOI_LUOT = int(os.environ.get('TD_CODEX_MOI_LUOT', '5'))
CHO_GIAY = int(os.environ.get('TD_CODEX_TIMEOUT', '600'))
HOI_GIAY = int(os.environ.get('TD_CODEX_HOI_GIAY', '30'))  # hỏi việc mỗi 30 giây
CHAY_GIAY = int(os.environ.get('TD_CODEX_CHAY_GIAY', '270'))  # dừng trước lần cron sau (5 phút)
DAN_DAU = ('Bạn đang làm việc cho Công ty Tin Học Thành Đức. Chỉ trả lời câu lệnh dưới đây bằng MỘT khối JSON đúng các khóa '
           'được yêu cầu, không giải thích thêm. Không sửa hay tạo file nào, không chạy lệnh nào ngoài tra cứu.\n\n')


def log(msg):
    print(datetime.now().strftime('%d/%m %H:%M:%S'), msg, flush=True)


def _key():
    raw = os.environ.get('TD_AI_WEB_KEY', '').strip()
    if not raw:
        path = Path(os.environ.get('TD_AI_WEB_KEY_FILE', HOME / '.thanhduc-ai-key-web'))
        try:
            raw = path.read_text().strip()
        except OSError:
            sys.exit(f'Chưa có khóa: tạo khóa "Codex · Đăng web" ở màn Trợ lý AI rồi lưu vào {path} (chmod 600).')
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
        with urllib.request.urlopen(req, timeout=60) as resp:
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


def codex_bin():
    found = os.environ.get('TD_CODEX_BIN') or shutil.which('codex')
    if found:
        return found
    for p in (HOME / '.local/bin/codex', HOME / '.npm-global/bin/codex', Path('/usr/local/bin/codex'), Path('/usr/bin/codex')):
        if p.exists():
            return str(p)
    nvm = sorted((HOME / '.nvm/versions/node').glob('*/bin/codex'))
    return str(nvm[-1]) if nvm else None


def hoi_codex(lenh: str, thu_muc: Path) -> str:
    """Chạy `codex exec` một lần (chỉ đọc, không cần git), trả câu trả lời cuối cùng."""
    exe = codex_bin()
    if not exe:
        raise RuntimeError('Không thấy lệnh codex trên máy chủ.')
    with tempfile.NamedTemporaryFile('r', suffix='.txt', dir=thu_muc, delete=False) as f:
        ra = Path(f.name)
    try:
        cmd = [exe, 'exec', '--skip-git-repo-check', '--sandbox', 'read-only', '--color', 'never', '-o', str(ra)]
        cmd += shlex.split(os.environ.get('TD_CODEX_THEM', ''))  # ví dụ thêm "--search" nếu bản codex có
        cmd.append('-')
        kq = subprocess.run(cmd, input=DAN_DAU + lenh, text=True, capture_output=True, cwd=thu_muc, timeout=CHO_GIAY)
        tra_loi = ra.read_text(encoding='utf-8').strip() if ra.exists() else ''
        if kq.returncode != 0 or not tra_loi:
            duoi = (kq.stderr or kq.stdout or '').strip().splitlines()[-3:]
            raise RuntimeError(f'codex thoát mã {kq.returncode}: ' + ' | '.join(duoi)[:250])
        return tra_loi
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'codex chạy quá {CHO_GIAY} giây') from exc
    finally:
        ra.unlink(missing_ok=True)


def gui_anh(yc_id: str, f: Path):
    url = os.environ.get('TD_AI_URL', 'http://127.0.0.1:8010').rstrip('/') + f'/ai/dang-web/ve-anh/{yc_id}/anh'
    kieu = {'.png': 'image/png', '.webp': 'image/webp'}.get(f.suffix.lower(), 'image/jpeg')
    req = urllib.request.Request(url, data=f.read_bytes(), method='POST', headers={
        'Authorization': 'Bearer ' + _key(), 'Content-Type': kieu, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read() or b'null')
    except urllib.error.HTTPError as exc:
        raise LoiApp(exc.code, exc.reason) from exc
    except urllib.error.URLError as exc:
        raise LoiApp(0, f'Không kết nối được app: {exc.reason}') from exc


DUOI_ANH = ('.png', '.jpg', '.jpeg', '.webp')


def ve_codex(lenh: str, thu_muc: Path, so_anh: int) -> list[Path]:
    """Cho Codex vẽ bằng công cụ image_gen (gói ChatGPT), chỉ được ghi trong thư mục riêng của lượt vẽ.
    Codex quên chép ảnh ra thì lấy ảnh nó vừa tạo trong ~/.codex/generated_images."""
    exe = codex_bin()
    if not exe:
        raise RuntimeError('Không thấy lệnh codex trên máy chủ.')
    bat_dau = datetime.now().timestamp()
    cmd = [exe, 'exec', '--skip-git-repo-check', '--sandbox', 'workspace-write', '--color', 'never', '-C', str(thu_muc)]
    cmd += shlex.split(os.environ.get('TD_CODEX_THEM', ''))
    cmd.append('-')
    try:
        kq = subprocess.run(cmd, input=lenh, text=True, capture_output=True, cwd=thu_muc, timeout=CHO_GIAY)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'codex vẽ quá {CHO_GIAY} giây') from exc
    anh = sorted(p for p in thu_muc.iterdir() if p.suffix.lower() in DUOI_ANH and p.stat().st_size > 1000)
    if not anh:
        sinh = HOME / '.codex' / 'generated_images'
        if sinh.exists():
            anh = sorted((p for p in sinh.rglob('*') if p.is_file() and p.suffix.lower() in DUOI_ANH
                          and p.stat().st_mtime >= bat_dau - 5), key=lambda p: p.stat().st_mtime)
    if not anh:
        duoi = (kq.stderr or kq.stdout or '').strip().splitlines()[-3:]
        raise RuntimeError(f'codex không tạo ra ảnh (mã {kq.returncode}): ' + ' | '.join(duoi)[:250])
    return anh[:so_anh]


def ve_anh_luot() -> int:
    """Yêu cầu vẽ ảnh minh họa bài viết: mỗi yêu cầu một thư mục tạm, gửi ảnh về app rồi xóa thư mục."""
    yeu_cau = call('GET', '/ai/dang-web/ve-anh')
    xong = 0
    for yc in yeu_cau or []:
        ten = f"vẽ ảnh {yc['id']} · {yc.get('ten', '')}"
        thu_muc = Path(tempfile.mkdtemp(prefix=f"ve-{yc['id']}-", dir=HOME / '.thanhduc-codex-viec'))
        try:
            anh = ve_codex(yc['lenh'], thu_muc, int(yc.get('so_anh') or 2))
            for f in anh:
                gui_anh(yc['id'], f)
            call('POST', f"/ai/dang-web/ve-anh/{yc['id']}/xong", {'ly_do': ''})
            log(f'{ten}: đã gửi {len(anh)} ảnh')
            xong += 1
        except Exception as exc:  # Codex lỗi / hết lượt: báo app để anh bấm vẽ lại
            log(f'{ten}: không vẽ được ({exc})')
            try:
                call('POST', f"/ai/dang-web/ve-anh/{yc['id']}/xong", {'ly_do': str(exc)[:300]})
            except LoiApp as e2:
                log(f'{ten}: {e2}')
        finally:
            shutil.rmtree(thu_muc, ignore_errors=True)
    return xong


def mot_luot() -> int:
    viec = call('GET', '/ai/dang-web/viec')  # lần hỏi này cũng báo app "Codex đang chạy"
    if not viec:
        return 0
    thu_muc = HOME / '.thanhduc-codex-viec'
    thu_muc.mkdir(mode=0o700, exist_ok=True)
    xong = 0
    for v in viec[:TOI_DA_MOI_LUOT]:
        ten = f"{v['id']} · {v.get('ten') or v.get('kieu')}"
        try:
            tra_loi = hoi_codex(v['lenh'], thu_muc)
        except Exception as exc:  # Codex lỗi / hết lượt: để app chuyển sang cách khác
            log(f'{ten}: không làm được ({exc})')
            try:
                call('POST', f"/ai/dang-web/viec/{v['id']}/khong-lam-duoc", {'ly_do': str(exc)[:300]})
            except LoiApp as e2:
                log(f'{ten}: {e2}')
            continue
        try:
            call('POST', f"/ai/dang-web/viec/{v['id']}/tra-loi", {'tra_loi': tra_loi})
            log(f'{ten}: đã gửi câu trả lời')
            xong += 1
        except LoiApp as exc:
            log(f'{ten}: {exc}')
            if exc.code == 422:  # câu trả lời không đúng JSON
                try:
                    call('POST', f"/ai/dang-web/viec/{v['id']}/khong-lam-duoc", {'ly_do': 'Codex trả lời sai dạng JSON'})
                except LoiApp as e2:
                    log(f'{ten}: {e2}')
    return xong


def tinh_trang() -> dict:
    """Còn đăng nhập ChatGPT không và phiên bản codex, để bảng sức khỏe trong app báo cho anh."""
    exe = codex_bin()
    if not exe:
        return {'dang_nhap': None, 'loi': 'Không thấy lệnh codex trên máy chủ.'}
    ra = {}
    try:
        kq = subprocess.run([exe, 'login', 'status'], text=True, capture_output=True, timeout=30)
        chu = (kq.stdout + kq.stderr).lower()
        ra['dang_nhap'] = kq.returncode == 0 and 'not logged' not in chu
    except (OSError, subprocess.TimeoutExpired):
        ra['dang_nhap'] = None
    try:
        ra['ban'] = subprocess.run([exe, '--version'], text=True, capture_output=True, timeout=30).stdout.strip()[:60]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ra


def main():
    khoa = open(HOME / '.thanhduc-codex.lock', 'w')
    try:
        fcntl.flock(khoa, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # lượt trước còn đang chạy
    try:
        (HOME / '.thanhduc-codex-viec').mkdir(mode=0o700, exist_ok=True)
        bat_dau = time.monotonic()
        try:
            call('POST', '/ai/dang-web/tinh-trang', tinh_trang())
        except LoiApp as exc:
            if exc.code in (401, 403, 423):
                raise
            log(f'Không báo được tình trạng: {exc}')  # app bản cũ chưa có cửa này: vẫn làm việc bình thường
        while True:
            mot_luot()
            ve_anh_luot()
            if time.monotonic() - bat_dau + HOI_GIAY > CHAY_GIAY:
                break
            time.sleep(HOI_GIAY)
    except LoiApp as exc:
        log(str(exc))
        if exc.code in (401, 403, 423):
            sys.exit(1)


if __name__ == '__main__':
    main()

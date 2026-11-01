#!/usr/bin/env python3
"""Codex trên máy chủ Z2 làm việc Đăng web bằng gói ChatGPT, không tốn phí API (TUẤN 01/11/2026, đợt 2).

Chạy mỗi 5 phút bằng cron, chỉ dùng thư viện có sẵn của Python:
    python3 ~/THANHDUC-APP/scripts/codex_dang_web.py
Mỗi lượt: hỏi app các câu lệnh đang chờ (qua cửa /ai/dang-web, khóa riêng của Codex), đưa từng câu lệnh cho
`codex exec`, rồi gửi câu trả lời về app. App chỉ lưu bản nháp; anh vẫn xem trước và bấm Đăng như cũ.
Codex lỗi hoặc hết lượt thì báo app "không làm được": app tự chuyển việc sang AI tự động (nếu có khóa API) hoặc dán tay.

Khóa đọc từ ~/.thanhduc-ai-key-web (chmod 600) hoặc TD_AI_WEB_KEY. App ở http://127.0.0.1:8010 (TD_AI_URL).
Không đọc database, không đọc deploy/.env, Codex chạy ở chế độ chỉ đọc trong thư mục riêng.
"""
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home()
TOI_DA_MOI_LUOT = int(os.environ.get('TD_CODEX_MOI_LUOT', '5'))
CHO_GIAY = int(os.environ.get('TD_CODEX_TIMEOUT', '600'))
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


def main():
    khoa = open(HOME / '.thanhduc-codex.lock', 'w')
    try:
        fcntl.flock(khoa, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # lượt trước còn đang chạy
    try:
        mot_luot()
    except LoiApp as exc:
        log(str(exc))
        if exc.code in (401, 403, 423):
            sys.exit(1)


if __name__ == '__main__':
    main()

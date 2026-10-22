// Tự biết có việc mới: hỏi máy chủ dấu hàng việc mỗi 15 giây khi trang đang mở trên màn hình.
// Dấu khác lúc tải trang -> tự tải lại ngay (giữ vị trí cuộn); chỉ khi đang gõ/mở ô chọn mới hiện thanh "Tải lại" và chờ gõ xong.
(function () {
  var box = document.querySelector('[data-watch]');
  if (!box || !window.fetch) return;
  var url = box.dataset.watch, stamp = box.dataset.stamp, EVERY = 15000, IDLE = 3000, KEY = 'td-watch-scroll';
  var lastTouch = Date.now(), changed = false, banner = null, busy = false;
  ['pointerdown', 'keydown', 'input', 'scroll'].forEach(function (name) {
    document.addEventListener(name, function () { lastTouch = Date.now(); }, { passive: true, capture: true });
  });
  function typing() {
    var el = document.activeElement;
    if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return true;
    // Đang mở ô chọn/hộp thoại: không tự tải mất việc đang làm dở.
    return !!document.querySelector('dialog[open], main details[open], form[data-submitting]');
  }
  function idle() { return !typing() && Date.now() - lastTouch >= IDLE; }
  function reload() {
    try { sessionStorage.setItem(KEY, JSON.stringify({ path: location.pathname + location.search, y: window.scrollY })); } catch (e) {}
    window.location.reload();
  }
  // Vừa tự tải lại: về đúng chỗ đang xem và báo nhẹ.
  try {
    var back = JSON.parse(sessionStorage.getItem(KEY) || 'null'); sessionStorage.removeItem(KEY);
    if (back && back.path === location.pathname + location.search) {
      window.scrollTo(0, back.y || 0);
      if (window.tdToast) window.tdToast('Đã cập nhật việc mới');
    }
  } catch (e) {}
  function show() {
    if (banner) return;
    banner = document.createElement('div');
    banner.className = 'toast work-watch';
    banner.setAttribute('role', 'status');
    banner.dataset.workWatchBanner = '';
    banner.innerHTML = '<span>Có việc mới hoặc việc vừa thay đổi</span><button type="button">Tải lại</button>';
    banner.querySelector('button').addEventListener('click', reload);
    var stack = document.querySelector('[data-toast-stack]') || document.body;
    stack.appendChild(banner);
    document.title = '(Mới) ' + document.title;
    try { if (navigator.vibrate) navigator.vibrate([200, 100, 200]); } catch (e) {}
  }
  function check() {
    if (busy || document.visibilityState !== 'visible') return;
    if (changed) { if (idle()) reload(); return; }
    busy = true;
    fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok && /json/.test(r.headers.get('content-type') || '') ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.stamp || data.stamp === stamp) return;
        changed = true; show();
        if (idle()) reload();
      })
      .catch(function () {})
      .then(function () { busy = false; });
  }
  setInterval(check, EVERY);
  // Gõ xong (rời ô nhập) mà đang có thay đổi: tải lại luôn.
  document.addEventListener('focusout', function () { if (changed) setTimeout(function () { if (idle()) reload(); }, IDLE + 100); });
  // Mở lại điện thoại / quay lại tab: hỏi ngay, không chờ đủ 15 giây.
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') check(); });
})();

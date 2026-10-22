// Tự biết có việc mới: hỏi máy chủ dấu hàng việc mỗi 30 giây khi trang đang mở trên màn hình.
// Dấu khác lúc tải trang -> hiện thanh "Có việc mới", rung nhẹ; người dùng không thao tác thì tự tải lại.
(function () {
  var box = document.querySelector('[data-watch]');
  if (!box || !window.fetch) return;
  var url = box.dataset.watch, stamp = box.dataset.stamp, EVERY = 30000, IDLE = 45000;
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
  function reload() { window.location.reload(); }
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
  // Mở lại điện thoại / quay lại tab: hỏi ngay, không chờ đủ 30 giây.
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') check(); });
})();

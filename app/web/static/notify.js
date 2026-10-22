// Thông báo theo vị trí: mỗi 30 giây (khi trang đang hiện) hỏi danh sách việc người khác vừa đẩy tới mình.
// Việc chưa từng thấy trên máy này -> hiện thông báo nổi + rung; chuông luôn đếm số việc đang chờ.
(function () {
  var bell = document.querySelector('[data-notify]');
  if (!bell || !window.fetch) return;
  var url = bell.dataset.notify, store = 'td-seen-' + bell.dataset.notifyUser, EVERY = 30000, busy = false;
  var badge = bell.querySelector('[data-notify-count]'), list = bell.querySelector('[data-notify-list]');
  var seen = null;
  try { var raw = localStorage.getItem(store); if (raw) seen = JSON.parse(raw); } catch (e) {}
  function save(ids) { try { localStorage.setItem(store, JSON.stringify(ids)); } catch (e) {} }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; }
  function draw(items, fresh) {
    badge.hidden = !items.length; badge.textContent = items.length > 99 ? '99+' : String(items.length);
    list.textContent = '';
    if (!items.length) { list.appendChild(el('p', 'mu', 'Không có việc nào đang chờ bạn.')); return; }
    items.forEach(function (item) {
      var a = el('a', fresh[item.id] ? 'new' : ''); a.href = item.link;
      a.appendChild(el('strong', '', item.title)); a.appendChild(el('small', '', item.text));
      list.appendChild(a);
    });
  }
  function pop(items) {
    if (!items.length) return;
    var stack = document.querySelector('[data-toast-stack]');
    items.slice(0, 3).forEach(function (item) {
      var t = el('div', 'toast work-watch'); t.setAttribute('role', 'status'); t.dataset.notifyToast = '';
      var a = el('a', '', item.title + ' · ' + item.text); a.href = item.link;
      var close = el('button', '', '✕'); close.type = 'button'; close.setAttribute('aria-label', 'Đóng');
      close.addEventListener('click', function () { t.remove(); });
      t.appendChild(a); t.appendChild(close); (stack || document.body).appendChild(t);
      setTimeout(function () { t.remove(); }, 15000);
    });
    if (items.length > 3 && window.tdToast) window.tdToast('Và ' + (items.length - 3) + ' việc mới khác · bấm chuông để xem');
    try { if (navigator.vibrate) navigator.vibrate([200, 100, 200]); } catch (e) {}
  }
  function check() {
    if (busy || document.visibilityState !== 'visible') return;
    busy = true;
    fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok && /json/.test(r.headers.get('content-type') || '') ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.items) return;
        var ids = data.items.map(function (i) { return i.id; });
        // Lần đầu trên máy này: chỉ ghi nhớ, không bắn cả loạt thông báo cũ.
        var known = {}; (seen || ids).forEach(function (id) { known[id] = 1; });
        var fresh = {}, news = data.items.filter(function (i) { if (known[i.id]) return false; fresh[i.id] = 1; return true; });
        draw(data.items, fresh); pop(news);
        seen = ids; save(ids);
      })
      .catch(function () {})
      .then(function () { busy = false; });
  }
  check();
  setInterval(check, EVERY);
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') check(); });
})();

// Web Push: thông báo cả khi đã tắt màn hình/đóng app. Mỗi máy tự bật một lần trong chuông.
(function () {
  var bell = document.querySelector('[data-notify]');
  if (!bell) return;
  var button = bell.querySelector('[data-push-toggle]'), hint = bell.querySelector('[data-push-hint]');
  function say(text) { hint.textContent = text; hint.hidden = !text; }
  function post(path, fields) {
    var body = new FormData(); body.append('csrf', bell.dataset.csrf);
    Object.keys(fields).forEach(function (k) { body.append(k, fields[k]); });
    return fetch(path, { method: 'POST', body: body, credentials: 'same-origin' }).then(function (r) { if (!r.ok) throw new Error(); });
  }
  function key(b64) {
    var raw = atob((b64 + '='.repeat((4 - b64.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, function (c) { return c.charCodeAt(0); });
  }
  var ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !window.isSecureContext) {
    if (ios) say('iPhone: bấm nút Chia sẻ → "Thêm vào MH chính", mở app từ biểu tượng Thành Đức rồi bật thông báo tại đây.');
    return;
  }
  navigator.serviceWorker.register('/sw.js').then(function (reg) {
    return reg.pushManager.getSubscription().then(function (sub) {
      if (sub && Notification.permission === 'granted') {
        // Gửi lại mỗi lần mở để máy luôn gắn đúng người đang đăng nhập.
        post('/ca-nhan/thong-bao/dang-ky', { subscription: JSON.stringify(sub) }).catch(function () {});
        say('Máy này đã bật thông báo khi tắt màn hình.');
        return;
      }
      if (Notification.permission === 'denied') { say('Máy đã chặn thông báo. Mở cài đặt trình duyệt, cho phép thông báo cho trang này.'); return; }
      button.hidden = false;
      button.addEventListener('click', function () {
        button.disabled = true;
        Notification.requestPermission().then(function (perm) {
          if (perm !== 'granted') throw new Error('denied');
          return fetch('/ca-nhan/thong-bao/khoa', { credentials: 'same-origin' }).then(function (r) { return r.json(); });
        }).then(function (data) {
          return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key(data.public_key) });
        }).then(function (sub) {
          return post('/ca-nhan/thong-bao/dang-ky', { subscription: JSON.stringify(sub) });
        }).then(function () {
          button.hidden = true; say('Đã bật. Việc mới sẽ báo cả khi tắt màn hình.');
          if (window.tdToast) window.tdToast('Đã bật thông báo trên máy này');
        }).catch(function (e) {
          button.disabled = false;
          say(e && e.message === 'denied' ? 'Bạn chưa cho phép thông báo.' : 'Chưa bật được, thử lại sau.');
        });
      });
    });
  }).catch(function () {});
})();

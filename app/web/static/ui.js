'use strict';
// UI0: thành phần giao diện dùng chung. Chỉ là lớp tăng cường: không JS thì form vẫn gửi như thường,
// mọi kiểm tra quyền/tiền/kho vẫn ở máy chủ.
(function () {
  function buzz(ms) { try { if (navigator.vibrate) navigator.vibrate(ms || 20); } catch (e) { /* máy không hỗ trợ */ } }

  // ---- Thông báo nhỏ (toast) ----
  function stack() {
    var el = document.querySelector('[data-toast-stack]');
    if (!el) { el = document.createElement('div'); el.className = 'toast-stack'; el.dataset.toastStack = ''; document.body.appendChild(el); }
    return el;
  }
  window.tdToast = function (message, kind) {
    var t = document.createElement('div');
    t.className = 'toast' + (kind === 'bad' ? ' bad' : '');
    t.setAttribute('role', kind === 'bad' ? 'alert' : 'status');
    var text = document.createElement('span'); text.textContent = message; t.appendChild(text);
    if (kind === 'bad') {
      // Lỗi không tự ẩn để người dùng kịp đọc.
      var close = document.createElement('button'); close.type = 'button'; close.textContent = 'Đóng';
      close.addEventListener('click', function () { t.remove(); });
      t.appendChild(close);
    } else {
      setTimeout(function () { t.remove(); }, 3000);
      buzz(20);
    }
    stack().appendChild(t);
    return t;
  };
  document.querySelectorAll('[data-toast]').forEach(function (el) { window.tdToast(el.dataset.toast, el.dataset.toastKind); });
  document.addEventListener('click', function (event) {
    var b = event.target.closest('[data-toast-demo]');
    if (b) window.tdToast(b.dataset.toastDemo, b.dataset.toastKind);
  });

  // ---- Sáng / Tối, lưu trên từng máy ----
  function applyTheme(theme) {
    if (theme === 'dark' || theme === 'light') document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
  }
  document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
    button.addEventListener('click', function () {
      var current = document.documentElement.dataset.theme
        || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      var next = current === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      try { localStorage.setItem('td-theme', next); } catch (e) { /* trình duyệt chặn lưu */ }
    });
  });

  // ---- Vuốt từ trái sang phải để xác nhận ----
  // Markup: <div class="swipe" data-swipe data-tone="ok|danger|info"><button type="submit">Vuốt để …</button></div>
  // Chạm (không kéo) chỉ hiện hướng dẫn; bàn phím (Enter/Space) thì hỏi lại bằng confirm().
  function bindSwipe(box) {
    var button = box.querySelector('button');
    if (!button || box.dataset.swipeBound) return;
    box.dataset.swipeBound = '1';
    box.classList.add('swipe-on');
    var knob = document.createElement('span');
    knob.className = 'swipe-knob'; knob.setAttribute('aria-hidden', 'true'); knob.textContent = '→';
    box.appendChild(knob);
    var drag = null, x = 0;
    function maxX() { return box.clientWidth - knob.offsetWidth - 12; }
    function setX(v) { x = v; knob.style.setProperty('--x', v + 'px'); }
    function done() {
      setX(0);
      buzz(40);
      if (box.dataset.swipeDemo !== undefined) { window.tdToast(box.dataset.swipeDemo || 'Đã ghi nhận'); return; }
      if (button.form) button.form.requestSubmit(button);
    }
    knob.addEventListener('pointerdown', function (event) {
      if (button.disabled || box.classList.contains('swipe-off')) {
        window.tdToast(box.dataset.why || 'Chưa đủ điều kiện', 'bad'); return;
      }
      knob.setPointerCapture(event.pointerId);
      drag = { start: event.clientX - x, moved: false };
    });
    knob.addEventListener('pointermove', function (event) {
      if (!drag) return;
      var v = Math.max(0, Math.min(maxX(), event.clientX - drag.start));
      if (v > 4) drag.moved = true;
      setX(v);
    });
    function release() {
      if (!drag) return;
      var moved = drag.moved; drag = null;
      if (moved && x >= maxX() * 0.85) done();
      else { setX(0); if (!moved) window.tdToast('Giữ nút tròn và vuốt hết sang phải'); }
    }
    knob.addEventListener('pointerup', release);
    knob.addEventListener('pointercancel', release);
    button.addEventListener('click', function (event) {
      // detail === 0: bấm bằng bàn phím hoặc trình đọc màn hình.
      if (event.detail === 0 && window.confirm(button.textContent.trim() + '?')) {
        if (box.dataset.swipeDemo !== undefined) { event.preventDefault(); window.tdToast(box.dataset.swipeDemo || 'Đã ghi nhận'); }
        return;
      }
      event.preventDefault();
      if (event.detail !== 0) window.tdToast('Giữ nút tròn và vuốt hết sang phải');
    });
  }
  document.querySelectorAll('[data-swipe]').forEach(bindSwipe);
  window.bindSwipe = bindSwipe;

  // ---- Nút − / + cho ô số lượng ----
  document.addEventListener('click', function (event) {
    var b = event.target.closest('[data-step]');
    if (!b) return;
    var input = b.closest('.stepper').querySelector('input');
    var min = Number(input.min || 0), max = input.max ? Number(input.max) : Infinity;
    var v = Math.max(min, Math.min(max, (Number(input.value) || 0) + Number(b.dataset.step)));
    if (Number(b.dataset.step) > 0 && v === Number(input.value)) window.tdToast('Tối đa ' + max, 'bad');
    input.value = v;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });

  // ---- Phím tắt cho người ngồi máy tính ----
  function typing(el) {
    return el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  }
  document.addEventListener('keydown', function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'Escape') {
      var dialog = document.querySelector('dialog[open]');
      if (dialog) { dialog.close(); return; }
      var open = Array.prototype.slice.call(document.querySelectorAll('details[open]:not([data-web-menu])')).pop();
      if (open) { open.open = false; var s = open.querySelector('summary'); if (s) s.focus(); }
      return;
    }
    if (event.key === '/' && !typing(document.activeElement)) {
      var search = document.querySelector('main input[type=search], main input[data-live], main input[name=q]');
      if (search) { event.preventDefault(); search.focus(); }
    }
  });
})();

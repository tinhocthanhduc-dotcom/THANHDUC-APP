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

  // ---- Chip điền nhanh lý do (data-fill) và nút mở khung (data-open-details) ----
  document.addEventListener('click', function (event) {
    var chip = event.target.closest('[data-fill-value]');
    if (chip) {
      var box = chip.closest('[data-fill]'), sel = box.dataset.fillSel || '[name="' + box.dataset.fill + '"]', scope = box, target = null;
      // Tìm ô cùng tên gần nhất đi ngược lên, để chip đặt ngoài form vẫn điền đúng ô.
      while (scope && !(target = scope.querySelector(sel))) scope = scope.parentElement;
      if (target) {
        target.value = chip.dataset.fillValue;
        target.dispatchEvent(new Event('input', { bubbles: true }));
        box.querySelectorAll('[data-fill-value]').forEach(function (b) { b.classList.toggle('on', b === chip); });
        buzz(15);
      }
      return;
    }
    var opener = event.target.closest('[data-open-details]');
    if (opener) {
      var details = document.querySelector(opener.getAttribute('href'));
      if (details && details.tagName === 'DETAILS') {
        event.preventDefault();
        details.open = true;
        details.scrollIntoView({ block: 'start', behavior: 'smooth' });
        var summary = details.querySelector('summary'); if (summary) summary.focus({ preventScroll: true });
      }
    }
  });

  // ---- Nút − / + cho ô số lượng ----
  document.addEventListener('click', function (event) {
    var b = event.target.closest('[data-step]');
    if (!b) return;
    var input = b.closest('.stepper').querySelector('input');
    // Số lẻ viết dấu phẩy (2,5) như ô nhập số lượng của server.
    function num(text) { return Number(String(text || '').replace(',', '.')); }
    var min = num(input.min) || 0, max = input.max ? num(input.max) : Infinity, cur = num(input.value) || 0;
    var v = Math.max(min, Math.min(max, Math.round((cur + Number(b.dataset.step)) * 1000) / 1000));
    if (Number(b.dataset.step) > 0 && v === cur) window.tdToast('Tối đa ' + input.max, 'bad');
    input.value = String(v).replace('.', ',');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });

  // ---- UX5a: bộ chọn hàng gọn. Chỉ hiện dòng gợi ý/đã chọn; mã khác tìm bằng ô tìm (không dấu). ----
  function fold(text) {
    return String(text || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd');
  }
  function pickerRows(root) {
    return Array.prototype.slice.call(root.querySelectorAll('[data-pick-row]'));
  }
  function renderPicker(root) {
    var box = root.querySelector('[data-pick-box]');
    if (!box) return;
    var search = box.querySelector('[data-pick-search]'), list = box.querySelector('[data-pick-results]');
    var hidden = pickerRows(root).filter(function (row) { return row.hidden; });
    box.hidden = hidden.length === 0;
    var count = box.querySelector('[data-pick-count]');
    if (count) count.textContent = hidden.length;
    var q = fold(search.value).trim(), all = box.dataset.showAll === '1';
    list.innerHTML = '';
    if (!q && !all) return;
    var words = q.split(/\s+/).filter(Boolean);
    hidden.filter(function (row) {
      var text = fold(row.dataset.pickText);
      return words.every(function (w) { return text.indexOf(w) >= 0; });
    }).slice(0, all && !q ? 200 : 12).forEach(function (row) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'pick-result'; b.dataset.pickTake = '';
      b.innerHTML = '<b></b><small></small>';
      b.querySelector('b').textContent = row.dataset.pickName || row.dataset.pickText;
      b.querySelector('small').textContent = row.dataset.pickMeta || '';
      b._row = row;
      list.appendChild(b);
    });
    if (!list.children.length) list.innerHTML = '<p class="hint">Không có mã nào khớp.</p>';
  }
  document.querySelectorAll('[data-item-picker]').forEach(function (root) {
    pickerRows(root).forEach(function (row) { if (row.hasAttribute('data-pick-extra')) row.hidden = true; });
    renderPicker(root);
    root.addEventListener('input', function (event) { if (event.target.closest('[data-pick-search]')) renderPicker(root); });
    root.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && event.target.closest('[data-pick-search]')) {
        event.preventDefault();
        var first = root.querySelector('[data-pick-take]');
        if (first) first.click();
      }
    });
    root.addEventListener('click', function (event) {
      var toggle = event.target.closest('[data-pick-all]');
      if (toggle) {
        var box = root.querySelector('[data-pick-box]');
        box.dataset.showAll = box.dataset.showAll === '1' ? '0' : '1';
        toggle.textContent = box.dataset.showAll === '1' ? 'Ẩn danh sách' : 'Xem tất cả';
        renderPicker(root); return;
      }
      var take = event.target.closest('[data-pick-take]');
      if (!take || !take._row) return;
      var row = take._row;
      row.hidden = false;
      root.querySelector('[data-pick-search]').value = '';
      row.dispatchEvent(new CustomEvent('item-picked', { bubbles: true }));
      renderPicker(root);
      var focus = row.querySelector('input:not([type=hidden]):not([type=checkbox])');
      if (focus) focus.focus();
    });
    root.addEventListener('item-picker-refresh', function () { renderPicker(root); });
  });

  // ---- Bộ lọc: đổi ô chọn là lọc ngay (dùng tìm trực tiếp nếu form có, không thì gửi form) ----
  document.addEventListener('change', function (event) {
    var el = event.target.closest && event.target.closest('[data-autosubmit]');
    if (!el || !el.form) return;
    var live = el.form.querySelector('[data-live]');
    if (live) live.dispatchEvent(new Event('input'));
    else if (el.form.requestSubmit) el.form.requestSubmit();
    else el.form.submit();
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
      var open = Array.prototype.slice.call(document.querySelectorAll('details[open]:not([data-web-menu]):not([data-nav-group])')).pop();
      if (open) { open.open = false; var s = open.querySelector('summary'); if (s) s.focus(); }
      return;
    }
    if (event.key === '/' && !typing(document.activeElement)) {
      var search = document.querySelector('main input[type=search], main input[data-live], main input[name=q]');
      if (search) { event.preventDefault(); search.focus(); }
    }
  });
})();

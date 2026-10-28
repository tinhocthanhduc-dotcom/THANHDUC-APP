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
  // Lỗi TUẤN 27/10 "nhiều lúc không kéo được": chỉ nút tròn nhận kéo (đặt ngón vào chữ là không chạy),
  // nút bị khóa thì chạm vào chữ không báo gì, ô bắt buộc nằm trong mục đóng thì vuốt hết cũng không gửi mà không báo.
  // Nay kéo được từ bất kỳ chỗ nào trên thanh, khóa thì báo đúng mục còn thiếu. iOS 15: requestSubmit thay thế ở base.jinja.
  function bindSwipe(box) {
    var button = box.querySelector('button');
    if (!button || box.dataset.swipeBound) return;
    box.dataset.swipeBound = '1';
    box.classList.add('swipe-on');
    var knob = document.createElement('span');
    knob.className = 'swipe-knob'; knob.setAttribute('aria-hidden', 'true'); knob.textContent = '→';
    box.appendChild(knob);
    var drag = null, x = 0, quietClickUntil = 0, lockToast = null;
    function maxX() { return box.clientWidth - knob.offsetWidth - 12; }
    function setX(v) { x = v; knob.style.setProperty('--x', v + 'px'); }
    function locked() { return button.disabled || box.classList.contains('swipe-off'); }
    // iOS 15 không hiểu :has(): tự gắn lớp để nút tròn xám khi bị khóa.
    function syncLock() { box.classList.toggle('swipe-locked', locked()); }
    syncLock();
    if (window.MutationObserver) new MutationObserver(syncLock).observe(button, { attributes: true, attributeFilter: ['disabled'] });
    function explainLock() {
      var why = box.dataset.why || 'Chưa đủ điều kiện';
      var target = document.getElementById(button.getAttribute('aria-describedby') || '');
      if (target) {
        var missing = [].map.call(target.querySelectorAll('[data-check][data-ok="0"]'), function (li) { return li.dataset.label; });
        if (missing.length) why = 'Còn thiếu: ' + missing.join('; ');
        try { target.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) { target.scrollIntoView(); }
      }
      // Chạm nhiều lần không chồng nhiều thông báo đỏ.
      if (lockToast) lockToast.remove();
      lockToast = window.tdToast(why, 'bad');
    }
    function submit() {
      var form = button.form;
      if (!form) return;
      if (!button.formNoValidate && !form.noValidate && form.checkValidity && !form.checkValidity()) {
        // Ô bắt buộc nằm trong mục đang đóng thì trình duyệt không chỉ ra được: mở mục đó rồi báo.
        var bad = [].filter.call(form.elements, function (el) { return el.willValidate && !el.checkValidity(); })[0];
        for (var d = bad && bad.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) d.open = true;
        if (bad && bad.focus) { try { bad.focus(); } catch (e) { /* bỏ qua */ } }
        if (form.reportValidity) form.reportValidity();
        window.tdToast(bad && bad.validationMessage ? 'Chưa điền đủ: ' + bad.validationMessage : 'Chưa điền đủ thông tin bắt buộc', 'bad');
        return;
      }
      form.requestSubmit(button);
    }
    function done() {
      setX(0);
      buzz(40);
      if (box.dataset.swipeDemo !== undefined) { window.tdToast(box.dataset.swipeDemo || 'Đã ghi nhận'); return; }
      submit();
    }
    box.addEventListener('pointerdown', function (event) {
      if (event.button > 0 || !event.isPrimary) return;
      if (locked()) { quietClickUntil = Date.now() + 800; explainLock(); return; }
      try { box.setPointerCapture(event.pointerId); } catch (e) { /* trình duyệt cũ */ }
      drag = { start: event.clientX - x, moved: false };
      box.classList.add('swipe-drag');
    });
    box.addEventListener('pointermove', function (event) {
      if (!drag) return;
      var v = Math.max(0, Math.min(maxX(), event.clientX - drag.start));
      if (v > 4) drag.moved = true;
      if (drag.moved && event.cancelable) event.preventDefault();
      setX(v);
    });
    function release(event) {
      if (!drag) return;
      var moved = drag.moved; drag = null;
      box.classList.remove('swipe-drag');
      quietClickUntil = Date.now() + 800;
      if (event.type === 'pointerup' && moved && x >= maxX() * 0.8) done();
      else { setX(0); if (!moved && event.type === 'pointerup') window.tdToast('Vuốt nút tròn hết sang phải'); }
    }
    box.addEventListener('pointerup', release);
    box.addEventListener('pointercancel', release);
    box.addEventListener('lostpointercapture', function (event) { if (drag) release({ type: 'pointercancel' }); });
    button.addEventListener('click', function (event) {
      // detail === 0: bấm bằng bàn phím hoặc trình đọc màn hình.
      if (event.detail === 0 && Date.now() > quietClickUntil && window.confirm(button.textContent.trim() + '?')) {
        if (box.dataset.swipeDemo !== undefined) { event.preventDefault(); window.tdToast(box.dataset.swipeDemo || 'Đã ghi nhận'); }
        return;
      }
      event.preventDefault();
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
    else { list.firstElementChild.classList.add('pick-hl'); if (window.tdMarkHits) window.tdMarkHits(list, search.value); }
  }
  document.querySelectorAll('[data-item-picker]').forEach(function (root) {
    pickerRows(root).forEach(function (row) { if (row.hasAttribute('data-pick-extra')) row.hidden = true; });
    renderPicker(root);
    root.addEventListener('input', function (event) { if (event.target.closest('[data-pick-search]')) renderPicker(root); });
    root.addEventListener('keydown', function (event) {
      if (!event.target.closest('[data-pick-search]')) return;
      var all = Array.prototype.slice.call(root.querySelectorAll('[data-pick-take]'));
      var at = all.findIndex(function (b) { return b.classList.contains('pick-hl'); });
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        if (!all.length) return;
        event.preventDefault();
        at = Math.max(0, Math.min(all.length - 1, (at < 0 ? -1 : at) + (event.key === 'ArrowDown' ? 1 : -1)));
        all.forEach(function (b, i) { b.classList.toggle('pick-hl', i === at); });
        all[at].scrollIntoView({ block: 'nearest' });
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        var chosen = all[at < 0 ? 0 : at];
        if (chosen) chosen.click();
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
    // Thanh lọc chung (macro filter_bar): mọi ô chọn trong form data-autofilter cũng lọc ngay; ô tìm thì Enter.
    if (!el && event.target.form && event.target.form.hasAttribute('data-autofilter') && event.target.type !== 'search') el = event.target;
    // Tấm lọc trên điện thoại đang mở: chọn xong mới bấm "Xem kết quả", không gửi sau mỗi ô.
    if (el && el.form && el.form.classList.contains('lf-open')) return;
    if (!el || !el.form) return;
    var live = el.form.querySelector('[data-live]');
    if (live) live.dispatchEvent(new Event('input'));
    else if (el.form.requestSubmit) el.form.requestSubmit();
    else el.form.submit();
  });

  // ---- Đợt 2: khung danh sách. Nút "Bộ lọc (n)" mở/đóng tấm lọc; bấm cả dòng là mở chứng từ. ----
  document.addEventListener('click', function (event) {
    // Bấm nền mờ ngoài tấm lọc (chính form) cũng đóng tấm.
    var toggle = event.target.classList && event.target.classList.contains('lf-open') ? event.target : event.target.closest && event.target.closest('[data-lf-toggle]');
    if (toggle) {
      var bar = toggle.closest('.lf-bar'), open = !bar.classList.contains('lf-open');
      bar.classList.toggle('lf-open', open);
      bar.querySelectorAll('.lf-toggle').forEach(function (b) { b.setAttribute('aria-expanded', open ? 'true' : 'false'); });
      return;
    }
    var row = event.target.closest && event.target.closest('tr[data-href]');
    if (!row || event.target.closest('a, button, input, select, textarea, label, summary, form, details')) return;
    if (window.getSelection && String(window.getSelection())) return;
    if (event.ctrlKey || event.metaKey) window.open(row.dataset.href, '_blank');
    else location.href = row.dataset.href;
  });

  // ---- Phím tắt cho người ngồi máy tính ----
  function typing(el) {
    return el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  }
  // ---- Ảnh: nén trên máy trước khi gửi (cạnh dài 1280px, JPEG) để tải nhanh, đỡ tốn dung lượng ----
  // Máy không nén được thì gửi ảnh gốc, server vẫn tự thu nhỏ. Ô chụp màn thợ (data-tech-camera) tự nén riêng.
  // Một đoạn nén chung cho cả màn thợ (technician-photos.js gọi window.tdShrinkPhoto).
  // Máy thiếu createImageBitmap (iOS cũ) vẫn nén qua <img>, để không gửi ảnh gốc vài MB (TUẤN 27/10: tải ảnh chậm).
  var PHOTO_SIDE = 1280, PHOTO_QUALITY = 0.72;
  function decodePhoto(file) {
    if (window.createImageBitmap) return createImageBitmap(file, { imageOrientation: 'from-image' }).catch(function () { return createImageBitmap(file); });
    return new Promise(function (resolve, reject) {
      var img = new Image(), url = URL.createObjectURL(file);
      img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('Không đọc được ảnh')); };
      img.src = url;
    });
  }
  function shrinkPhoto(file) {
    if (!/^image\//.test(file.type)) return Promise.resolve(file);
    return decodePhoto(file).then(function (bmp) {
      var scale = Math.min(1, PHOTO_SIDE / Math.max(bmp.width, bmp.height)), canvas = document.createElement('canvas');
      canvas.width = Math.round(bmp.width * scale); canvas.height = Math.round(bmp.height * scale);
      canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
      if (bmp.close) bmp.close();
      return new Promise(function (resolve) {
        canvas.toBlob(function (blob) {
          resolve(blob && blob.size < file.size ? new File([blob], file.name.replace(/\.[^.]*$/, '') + '.jpg', { type: 'image/jpeg' }) : file);
        }, 'image/jpeg', PHOTO_QUALITY);
      });
    }).catch(function () { return file; });
  }
  window.tdShrinkPhoto = shrinkPhoto;
  document.addEventListener('change', function (event) {
    var input = event.target;
    if (!input.matches || !input.matches('input[type=file]') || input.hasAttribute('data-tech-camera') || input.hasAttribute('data-photo-twin') || input.dataset.shrunk === '1' || !window.DataTransfer) return;
    var files = Array.prototype.slice.call(input.files || []);
    if (!files.some(function (f) { return /^image\//.test(f.type); })) return;
    event.stopImmediatePropagation();
    var buttons = input.form ? Array.prototype.slice.call(input.form.querySelectorAll('button')) : [];
    buttons.forEach(function (b) { b.disabled = true; });
    Promise.all(files.map(shrinkPhoto)).then(function (out) {
      try { var dt = new DataTransfer(); out.forEach(function (f) { dt.items.add(f); }); input.files = dt.files; } catch (e) { /* giữ ảnh gốc */ }
    }).then(function () {
      buttons.forEach(function (b) { b.disabled = false; });
      input.dataset.shrunk = '1';
      input.dispatchEvent(new Event('change', { bubbles: true }));
      delete input.dataset.shrunk;
    });
  }, true);

  // ---- Nút 🖼 Ảnh có sẵn (o_anh.jinja): chép ảnh đã chọn sang ô 📷 Chụp ảnh cạnh bên rồi xử lý như ảnh vừa chụp ----
  document.addEventListener('change', function (event) {
    var twin = event.target;
    if (!twin.matches || !twin.matches('[data-photo-twin]') || !twin.files.length) return;
    var btns = twin.closest('.photo-btns'), main = btns && btns.querySelector('input[type=file]:not([data-photo-twin])');
    if (!main) return;
    try {
      var dt = new DataTransfer();
      Array.prototype.slice.call(twin.files).forEach(function (f) { dt.items.add(f); });
      main.files = dt.files; twin.value = '';
    } catch (e) {
      // Trình duyệt không cho chép tệp: chuyển tên ô sang nút Ảnh có sẵn để form gửi đúng ảnh này.
      twin.name = main.name; twin.required = main.required; main.removeAttribute('name'); main.required = false; main.value = '';
      Array.prototype.slice.call(main.attributes).forEach(function (a) { if (/^data-(autosubmit|photo-pick)$/.test(a.name)) { twin.setAttribute(a.name, a.value); main.removeAttribute(a.name); } });
      twin.removeAttribute('data-photo-twin');
      twin.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }
    main.dispatchEvent(new Event('change', { bubbles: true }));
  });

  // ---- Ô ảnh chuẩn (o_anh.jinja pick): xem trước ảnh đã chụp, tối đa 2 ảnh; ảnh lưu cùng nút của form ----
  document.addEventListener('change', function (event) {
    var input = event.target;
    if (!input.matches || !input.matches('[data-photo-pick]')) return;
    var box = input.closest('[data-photo-pick-box]'), list = box.querySelector('[data-photo-picked]'), note = box.querySelector('[data-photo-pick-note]');
    var files = Array.prototype.slice.call(input.files || []);
    if (files.length > 2) {
      try { var dt = new DataTransfer(); files.slice(0, 2).forEach(function (f) { dt.items.add(f); }); input.files = dt.files; } catch (e) { /* trình duyệt cũ */ }
      files = files.slice(0, 2);
      if (window.tdToast) window.tdToast('Mỗi ô tối đa 2 ảnh, đã giữ 2 ảnh đầu', 'warn');
    }
    list.innerHTML = '';
    files.forEach(function (file) {
      var tile = document.createElement('article'); tile.className = 'tech-photo';
      var img = document.createElement('img'); img.alt = 'Ảnh đã chụp'; img.src = URL.createObjectURL(file);
      var small = document.createElement('small'); small.textContent = 'Lưu khi bấm nút bên dưới';
      tile.appendChild(img); tile.appendChild(small); list.appendChild(tile);
    });
    if (note) note.hidden = files.length > 0;
  });
  // Nút chính bên ngoài trỏ vào ô ảnh (vd. "Tải ảnh sau sửa") khi chưa chọn ảnh: mở máy ảnh luôn.
  document.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('button[form]');
    var form = button && document.getElementById(button.getAttribute('form'));
    if (!form || !form.hasAttribute('data-photo-slot-form')) return;
    var input = form.querySelector('input[type=file]');
    if (input && !input.files.length) { event.preventDefault(); input.click(); }
  });
  document.addEventListener('invalid', function (event) {
    if (!event.target.matches || !event.target.matches('[data-photo-pick]')) return;
    if (window.tdToast) window.tdToast('Chụp ảnh trước khi lưu', 'bad');
    var box = event.target.closest('[data-photo-pick-box]'); if (box && box.scrollIntoView) box.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, true);

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

  // Báo cáo sửa gọn (phương án A): dòng gập "Lỗi · việc đã làm" tóm tắt các nút đã chọn + Ghi thêm.
  function reportSummary(fold) {
    var out = fold.querySelector('[data-report-summary]'); if (!out) return;
    var picked = Array.prototype.map.call(fold.querySelectorAll('input[type=checkbox]:checked'), function (i) { return i.value; });
    var extra = fold.querySelector('[name=fix_extra]'); if (extra && extra.value.trim()) picked.push(extra.value.trim());
    out.textContent = picked.length ? picked.slice(0, 2).join(', ') + (picked.length > 2 ? ' +' + (picked.length - 2) : '') : out.dataset.empty;
  }
  function allSummaries() { document.querySelectorAll('[data-report-fold]').forEach(reportSummary); }
  document.addEventListener('change', function (event) { var fold = event.target.closest && event.target.closest('[data-report-fold]'); if (fold) reportSummary(fold); });
  document.addEventListener('input', function (event) { var fold = event.target.closest && event.target.closest('[data-report-fold]'); if (fold) reportSummary(fold); });
  window.addEventListener('load', allSummaries);  // sau khi nháp thợ được khôi phục
})();

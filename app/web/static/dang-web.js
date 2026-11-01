// Màn Đăng web: tìm mã hàng, gom nhiều ảnh (chụp từng tấm hoặc chọn nhiều), chép câu lệnh ChatGPT, tự tải lại khi máy đang soạn.
(function () {
  'use strict';

  // ---- Tìm và chọn mã hàng ----
  var timer = null;
  document.addEventListener('input', function (event) {
    var input = event.target.closest && event.target.closest('[data-dw-search]');
    if (!input) return;
    var box = input.closest('[data-dw-picker]'), out = box.querySelector('[data-dw-results]');
    box.querySelector('input[name=ma]').value = '';
    clearTimeout(timer);
    timer = setTimeout(function () {
      var q = input.value.trim();
      if (!q) { out.innerHTML = ''; return; }
      fetch('/dang-web/tim-hang?q=' + encodeURIComponent(q), { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.text() : ''; })
        .then(function (html) { if (input.value.trim() === q) out.innerHTML = html; });
    }, 250);
  });
  document.addEventListener('click', function (event) {
    var b = event.target.closest && event.target.closest('[data-dw-pick]');
    if (!b) return;
    var box = b.closest('[data-dw-picker]');
    box.querySelector('input[name=ma]').value = b.dataset.dwPick;
    box.querySelector('[data-dw-search]').value = b.dataset.label;
    box.querySelector('[data-dw-results]').innerHTML = '';
    var picked = box.querySelector('[data-dw-picked]');
    if (picked) picked.textContent = 'Đã chọn: ' + b.dataset.label;
  });

  // ---- Ảnh: gom tối đa data-max tấm; chụp thêm không làm mất ảnh trước ----
  document.querySelectorAll('[data-dw-photos]').forEach(function (box) {
    var max = parseInt(box.dataset.max || '6', 10), keep = [], hidden = box.querySelector('input[name=anh]');
    var list = box.querySelector('[data-dw-list]'), note = box.querySelector('[data-dw-note]');
    function sync() {
      try { var dt = new DataTransfer(); keep.forEach(function (f) { dt.items.add(f); }); hidden.files = dt.files; } catch (e) { /* trình duyệt cũ: chỉ gửi lần chọn cuối */ }
      list.innerHTML = '';
      keep.forEach(function (file, i) {
        var tile = document.createElement('figure'); tile.className = 'dw-ph';
        var img = document.createElement('img'); img.alt = 'Ảnh ' + (i + 1); img.src = URL.createObjectURL(file);
        var x = document.createElement('button'); x.type = 'button'; x.className = 'dw-ph-x'; x.textContent = '×'; x.setAttribute('aria-label', 'Bỏ ảnh ' + (i + 1));
        x.addEventListener('click', function () { keep.splice(i, 1); sync(); });
        var cap = document.createElement('figcaption'); cap.textContent = i === 0 ? 'Ảnh chính' : 'Ảnh ' + (i + 1);
        tile.appendChild(img); tile.appendChild(x); tile.appendChild(cap); list.appendChild(tile);
      });
      if (note) note.textContent = keep.length ? keep.length + '/' + max + ' ảnh. Ảnh đầu tiên làm ảnh chính.' : note.dataset.empty;
    }
    box.addEventListener('change', function (event) {
      var input = event.target;
      if (!input.matches('[data-dw-add]')) return;
      var files = Array.prototype.slice.call(input.files || []);
      input.value = '';
      var room = max - keep.length;
      if (files.length > room && window.tdToast) window.tdToast('Tối đa ' + max + ' ảnh', 'warn');
      files = files.slice(0, Math.max(0, room));
      var shrink = window.tdShrinkPhoto || function (f) { return Promise.resolve(f); };
      Promise.all(files.map(function (f) { return shrink(f).catch(function () { return f; }); })).then(function (out) {
        keep = keep.concat(out); sync();
      });
    });
    if (hidden && !window.DataTransfer) { hidden.hidden = false; box.querySelectorAll('[data-dw-add]').forEach(function (b) { b.closest('label').hidden = true; }); }
  });

  // ---- Sửa ảnh: ‹ › đổi chỗ ảnh (thứ tự gửi theo ô ẩn thu_tu) ----
  document.addEventListener('click', function (event) {
    var b = event.target.closest && event.target.closest('[data-dw-move]');
    if (!b) return;
    var li = b.closest('li'), list = li && li.parentNode;
    if (!list) return;
    if (b.dataset.dwMove === '-1' && li.previousElementSibling) list.insertBefore(li, li.previousElementSibling);
    else if (b.dataset.dwMove === '1' && li.nextElementSibling) list.insertBefore(li.nextElementSibling, li);
    b.focus();
  });

  // ---- Chép câu lệnh ChatGPT ----
  document.addEventListener('click', function (event) {
    var b = event.target.closest && event.target.closest('[data-dw-copy]');
    if (!b) return;
    var src = document.querySelector(b.dataset.dwCopy);
    if (!src) return;
    var done = function () { if (window.tdToast) window.tdToast('Đã chép câu lệnh, dán vào ChatGPT', 'ok'); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(src.value).then(done, function () { src.select(); document.execCommand('copy'); done(); });
    else { src.select(); document.execCommand('copy'); done(); }
  });

  // ---- Máy đang soạn/đăng: tự tải lại tới khi xong ----
  var auto = document.querySelector('[data-dw-reload]');
  if (auto) setTimeout(function () {
    var a = document.activeElement;
    if (!a || !/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) location.reload();
  }, parseInt(auto.dataset.dwReload, 10) || 5000);

  // ---- Tab Hàng hóa: tick nhiều mã rồi soạn một lượt ----
  var multi = document.querySelector('[data-dw-multi]');
  if (multi) {
    var max = parseInt(multi.dataset.max, 10) || 10;
    var btn = document.querySelector('[data-dw-multi-btn]');
    var boxes = function () { return Array.prototype.slice.call(document.querySelectorAll('input[name="ma"][form="' + multi.id + '"]')); };
    var update = function () {
      var n = boxes().filter(function (b) { return b.checked; }).length;
      if (btn) { btn.disabled = n === 0; btn.textContent = n ? 'Soạn ' + n + ' mã đã chọn' : 'Soạn các mã đã chọn'; }
    };
    document.addEventListener('change', function (event) {
      var b = event.target;
      if (!b.matches || !b.matches('input[name="ma"]')) return;
      if (b.checked && boxes().filter(function (x) { return x.checked; }).length > max) {
        b.checked = false;
        if (window.tdToast) window.tdToast('Mỗi lần tối đa ' + max + ' mã', 'warn');
      }
      update();
    });
    update();
  }
})();

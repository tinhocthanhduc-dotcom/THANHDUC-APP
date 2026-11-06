// Tin gửi khách (Trợ lý Zalo đợt 4): đếm ký tự, Sao chép, hỏi lại trước khi gửi/bỏ, chờ AI viết lại xong thì tải lại trang.
(function () {
  var form = document.querySelector('[data-tg-form]');
  if (!form) return;
  var box = form.querySelector('[data-tg-text]');
  var len = form.querySelector('[data-tg-len]');
  var dirty = false;
  if (box && len) box.addEventListener('input', function () { dirty = true; len.textContent = box.value.length; });

  var copy = form.querySelector('[data-tg-copy]');
  if (copy && box) copy.addEventListener('click', function () {
    var done = function () { copy.textContent = 'Đã sao chép · dán vào Zalo cá nhân'; };
    var old = function () { box.focus(); box.select(); try { document.execCommand('copy'); done(); } catch (e) { copy.textContent = 'Chọn chữ trong khung để sao chép'; } };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(box.value).then(done, old); else old();
  });

  form.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-tg-confirm]');
    if (btn && !window.confirm(btn.getAttribute('data-tg-confirm'))) ev.preventDefault();
  });

  var url = form.getAttribute('data-tg-ai');
  if (url && window.fetch) {
    var note = form.querySelector('[data-tg-ai-note]');
    var tries = 0;
    var poll = function () {
      tries += 1;
      fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.status === 'DANG' && tries < 60) { setTimeout(poll, 5000); return; }
        if (!dirty) { window.location.reload(); return; }
        if (note) note.textContent = 'AI đã viết xong. Bấm Lưu nháp để giữ phần đang sửa, trang sẽ hiện bản AI.';
      }).catch(function () { if (tries < 60) setTimeout(poll, 8000); });
    };
    setTimeout(poll, 5000);
  }
})();

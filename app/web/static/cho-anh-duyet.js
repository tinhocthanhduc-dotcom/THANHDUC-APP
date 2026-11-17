// Đợt 2 tối ưu thao tác (TUẤN duyệt mẫu 17/11/2026): Duyệt/Từ chối ngay tại dòng trang Chờ anh duyệt.
// Duyệt chờ 5 giây để Hoàn tác rồi mới gửi; gửi bằng fetch để duyệt liên tiếp nhiều dòng không mất dòng nào.
// Rời trang khi còn dòng đang đếm: gửi luôn bằng sendBeacon (người dùng đã bấm Duyệt).
(function () {
  var list = document.querySelector('[data-ap-list]');
  if (!list || !window.fetch) return;
  var WAIT = 5, pending = [];

  function state(row, text, tone, undo) {
    var box = row.querySelector('[data-ap-state]'), acts = row.querySelector('[data-ap-acts]');
    acts.hidden = !!text; box.hidden = !text; box.className = 'ap-state' + (tone ? ' ap-' + tone : '');
    box.textContent = text || '';
    if (undo) { var b = document.createElement('button'); b.type = 'button'; b.className = 'btn btn-ghost'; b.textContent = 'Hoàn tác'; b.addEventListener('click', undo); box.appendChild(b); }
  }

  function send(form, row, doneText) {
    row.classList.add('ap-busy');
    state(row, 'Đang gửi…');
    fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin' }).then(function (res) {
      return res.text().then(function (html) {
        if (res.ok && /[?&]da_luu=/.test(res.url)) { row.classList.remove('ap-busy', 'ap-rejecting'); row.classList.add('ap-done'); form.hidden = form.hasAttribute('data-ap-approve') ? form.hidden : true; state(row, doneText, 'ok'); return; }
        var doc = new DOMParser().parseFromString(html, 'text/html'), alert = doc.querySelector('.alert.bad');
        row.classList.remove('ap-busy');
        state(row, (alert ? alert.textContent : 'Không gửi được, hãy tải lại trang.'), 'bad', function () { state(row); });
      });
    }).catch(function () { row.classList.remove('ap-busy'); state(row, 'Mất kết nối, thử lại.', 'bad', function () { state(row); }); });
  }

  list.addEventListener('submit', function (e) {
    var form = e.target.closest('[data-ap-form]'); if (!form) return;
    e.preventDefault();
    var row = form.closest('[data-ap-row]');
    if (row.classList.contains('ap-busy') || row.classList.contains('ap-done')) return;
    if (!form.hasAttribute('data-ap-approve')) { send(form, row, '✓ Đã từ chối'); return; }
    var left = WAIT, item = { form: form, row: row };
    function tick() {
      if (left <= 0) { cancel(); send(form, row, '✓ Đã duyệt'); return; }
      state(row, '✓ Đã duyệt · ghi sau ' + left + ' giây', 'ok', function () { cancel(); state(row); });
      left -= 1;
    }
    function cancel() { clearInterval(item.timer); pending = pending.filter(function (p) { return p !== item; }); }
    pending.push(item); tick(); item.timer = setInterval(tick, 1000);
  });

  list.addEventListener('click', function (e) {
    var row = e.target.closest('[data-ap-row]'); if (!row) return;
    var rej = row.querySelector('.ap-rej');
    if (e.target.closest('[data-ap-reject]')) { rej.hidden = false; row.classList.add('ap-rejecting'); rej.querySelector('[name=reason]').focus(); }
    if (e.target.closest('[data-ap-cancel]')) { rej.hidden = true; row.classList.remove('ap-rejecting'); }
  });

  window.addEventListener('pagehide', function () {
    pending.forEach(function (p) { clearInterval(p.timer); if (navigator.sendBeacon) navigator.sendBeacon(p.form.action, new FormData(p.form)); });
    pending = [];
  });
})();

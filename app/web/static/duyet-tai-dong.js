// Đợt 2 tối ưu thao tác (TUẤN duyệt mẫu 17/11/2026): Duyệt/Từ chối ngay tại dòng (Chờ anh duyệt, Việc của tôi).
// Duyệt chờ 5 giây để Hoàn tác rồi mới gửi; gửi bằng fetch (header X-Tai-Dong, máy chủ trả JSON) để bấm liên tiếp nhiều dòng.
// Rời trang khi còn dòng đang đếm: gửi luôn bằng sendBeacon (người dùng đã bấm). Không JS: form gửi thường.
(function () {
  if (!window.fetch || !document.querySelector('[data-ap-row]')) return;
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
    fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: { 'X-Tai-Dong': '1' } }).then(function (res) {
      return res.json().catch(function () { return { ok: false }; }).then(function (data) {
        row.classList.remove('ap-busy');
        if (res.ok && data.ok) {
          row.classList.remove('ap-rejecting'); row.classList.add('ap-done');
          var rej = row.querySelector('.ap-rej'); if (rej) rej.hidden = true;
          state(row, doneText, 'ok'); return;
        }
        state(row, data.message || 'Không gửi được, hãy tải lại trang.', 'bad', function () { state(row); });
      });
    }).catch(function () { row.classList.remove('ap-busy'); state(row, 'Mất kết nối, thử lại.', 'bad', function () { state(row); }); });
  }

  document.addEventListener('submit', function (e) {
    var form = e.target.closest && e.target.closest('[data-ap-form]'); if (!form) return;
    var row = form.closest('[data-ap-row]'); if (!row) return;
    e.preventDefault();
    if (row.classList.contains('ap-busy') || row.classList.contains('ap-done')) return;
    if (!form.hasAttribute('data-ap-approve')) { send(form, row, '✓ Đã từ chối'); return; }
    var done = form.getAttribute('data-ap-done') || '✓ Đã duyệt', left = WAIT, item = { form: form };
    function cancel() { clearInterval(item.timer); pending = pending.filter(function (p) { return p !== item; }); }
    function tick() {
      if (left <= 0) { cancel(); send(form, row, done); return; }
      state(row, done + ' · ghi sau ' + left + ' giây', 'ok', function () { cancel(); state(row); });
      left -= 1;
    }
    pending.push(item); tick(); item.timer = setInterval(tick, 1000);
  });

  document.addEventListener('click', function (e) {
    var row = e.target.closest && e.target.closest('[data-ap-row]'); if (!row) return;
    var rej = row.querySelector('.ap-rej'); if (!rej) return;
    if (e.target.closest('[data-ap-reject]')) { rej.hidden = false; row.classList.add('ap-rejecting'); rej.querySelector('[name=reason]').focus(); }
    if (e.target.closest('[data-ap-cancel]')) { rej.hidden = true; row.classList.remove('ap-rejecting'); }
  });

  window.addEventListener('pagehide', function () {
    pending.forEach(function (p) { clearInterval(p.timer); if (navigator.sendBeacon) navigator.sendBeacon(p.form.action, new FormData(p.form)); });
    pending = [];
  });
})();

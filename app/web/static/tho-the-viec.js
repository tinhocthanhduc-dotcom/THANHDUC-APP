// Đợt 3 tối ưu thao tác (TUẤN duyệt mẫu 17/11/2026): màn Hôm nay của thợ.
// Tự nhận việc: 1 lần bấm, chờ 5 giây có Hoàn tác rồi mới gửi (rời trang khi đang đếm thì gửi luôn).
// Đã đến khách trên thẻ: lấy vị trí như trong phiếu (tối đa 4 giây, không chặn thợ) rồi gửi.
(function () {
  var WAIT = 5, pending = [];

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (form.hasAttribute('data-undo-form')) {
      e.preventDefault();
      if (form.dataset.busy) return;
      var go = e.submitter && e.submitter.value === '1', choice = form.querySelector('[data-undo-choice]');
      if (choice) choice.value = go ? '1' : '';
      var label = (form.getAttribute('data-undo-label') || 'Nhận việc') + (go ? ' và đi' : ''), left = WAIT, item = { form: form };
      var buttons = Array.prototype.slice.call(form.querySelectorAll('button'));
      var box = document.createElement('div'); box.className = 'tho-undo';
      var text = document.createElement('span'), undo = document.createElement('button');
      undo.type = 'button'; undo.className = 'btn btn-ghost'; undo.textContent = 'Hoàn tác';
      box.appendChild(text); box.appendChild(undo);
      buttons.forEach(function (b) { b.hidden = true; }); form.appendChild(box); form.dataset.busy = '1';
      function stop() { clearInterval(item.timer); pending = pending.filter(function (p) { return p !== item; }); }
      undo.addEventListener('click', function () { stop(); box.remove(); buttons.forEach(function (b) { b.hidden = false; }); delete form.dataset.busy; });
      function tick() {
        if (left <= 0) { stop(); text.textContent = 'Đang gửi…'; undo.hidden = true; form.submit(); return; }
        text.textContent = '✓ ' + label + ' · ghi sau ' + left + ' giây'; left -= 1;
      }
      pending.push(item); tick(); item.timer = setInterval(tick, 1000);
      return;
    }
    if (form.hasAttribute('data-gps-form')) {
      var lat = form.querySelector('[name=gps_lat]');
      if (form.dataset.busy) { e.preventDefault(); return; }
      if (!navigator.geolocation || lat.value) return;
      e.preventDefault(); form.dataset.busy = '1';
      var btn = form.querySelector('button'); if (btn) btn.textContent = 'Đang ghi…';
      var sent = false;
      function send() { if (sent) return; sent = true; form.submit(); }
      setTimeout(send, 4000);
      navigator.geolocation.getCurrentPosition(function (pos) {
        lat.value = pos.coords.latitude.toFixed(6);
        form.querySelector('[name=gps_lng]').value = pos.coords.longitude.toFixed(6);
        form.querySelector('[name=gps_accuracy]').value = Math.round(pos.coords.accuracy || 0);
        send();
      }, send, { enableHighAccuracy: true, timeout: 3500, maximumAge: 60000 });
    }
  });

  window.addEventListener('pagehide', function () {
    pending.forEach(function (p) { clearInterval(p.timer); if (navigator.sendBeacon) navigator.sendBeacon(p.form.action, new FormData(p.form)); });
    pending = [];
  });
})();

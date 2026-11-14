// Gửi báo giá sửa (mẫu M5 14/11/2026): sao chép link / tin trong lúc bấm, chuyển 3 khung Khách trả lời.
(function () {
  var box = document.querySelector('[data-rq-send]');
  if (!box) return;
  function copy(text, btn, area) {
    function done() { btn.textContent = 'Đã sao chép ✓'; }
    function fallback() { if (area) { area.focus(); area.select(); try { document.execCommand('copy'); done(); } catch (e) {} } }
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback); else fallback();
  }
  var linkBtn = box.querySelector('[data-rq-copy-link]'), textBtn = box.querySelector('[data-rq-copy-text]');
  if (linkBtn) linkBtn.addEventListener('click', function () { copy(box.querySelector('[data-rq-link]').textContent.trim(), linkBtn, null); });
  if (textBtn) textBtn.addEventListener('click', function () { var a = box.querySelector('[data-rq-text]'); copy(a.value, textBtn, a); });
  var tabs = box.querySelector('[data-rq-tabs]');
  function show() {
    var on = (tabs.querySelector('input:checked') || {}).value || 'dong-y';
    box.querySelectorAll('[data-rq-pane]').forEach(function (p) { p.hidden = p.dataset.rqPane !== on; });
  }
  if (tabs) { tabs.addEventListener('change', show); show(); }
  box.querySelectorAll('[data-xg-days] input').forEach(function (r) {
    r.addEventListener('change', function () { var d = r.closest('form').querySelector('[data-xg-date]'); if (d) d.value = r.value; });
  });
})();

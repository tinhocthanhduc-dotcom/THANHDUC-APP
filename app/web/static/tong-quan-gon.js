// Đợt 7 tối ưu thao tác (TUẤN duyệt mẫu 18/11/2026): Tổng quan gọn.
// Khối gập (details[data-fold]) anh đã mở thì lần sau mở sẵn trên máy này; link #id trong khối gập thì mở khối đó.
(function () {
  var KEY = 'tq-mo', saved = [];
  try { saved = JSON.parse(localStorage.getItem(KEY) || '[]') || []; } catch (e) { saved = []; }
  var folds = Array.prototype.slice.call(document.querySelectorAll('details[data-fold]'));
  if (!folds.length) return;
  var ready = false;
  folds.forEach(function (d) {
    if (saved.indexOf(d.getAttribute('data-fold')) >= 0) d.open = true;
    d.addEventListener('toggle', function () {
      if (!ready) return;
      var open = folds.filter(function (x) { return x.open; }).map(function (x) { return x.getAttribute('data-fold'); });
      try { localStorage.setItem(KEY, JSON.stringify(open)); } catch (e) { /* trình duyệt chặn lưu: vẫn dùng được */ }
    });
  });
  function toHash() {
    var id = decodeURIComponent(location.hash.slice(1)); if (!id) return;
    var t = document.getElementById(id); if (!t) return;
    var d = t.matches('details[data-fold]') ? t : t.closest('details[data-fold]');
    if (d && !d.open) { d.open = true; }
    t.scrollIntoView();
  }
  toHash();
  window.addEventListener('hashchange', toHash);
  setTimeout(function () { ready = true; }, 0);
})();

// Giá theo model (TUẤN duyệt mẫu 16/11/2026): tìm model không dấu, ô vừa sửa xanh lá, đếm ô đã sửa, nhắc khi rời trang chưa lưu.
(function () {
  function fold(s) { return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase(); }
  function money(n) { return String(Math.round(n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  function num(v) { return parseInt(String(v || '').replace(/\D/g, ''), 10) || 0; }
  var page = document.querySelector('.gm-page');
  var vat = page ? (+page.dataset.vatRate || 8) : 8;

  // Ô chọn model: gõ là lọc danh sách, Enter mở dòng đầu.
  var find = document.querySelector('[data-gm-find]'), list = document.querySelector('[data-gm-list]');
  if (find && list) {
    var items = Array.prototype.slice.call(list.querySelectorAll('a[data-s]')), add = list.querySelector('[data-gm-new]');
    var show = function () {
      var words = fold(find.value).split(/\s+/).filter(Boolean), n = 0;
      var compact = fold(find.value).replace(/[^a-z0-9]/g, '');
      items.forEach(function (a) {
        var s = a.dataset.s, ok = n < 15 && (words.every(function (w) { return s.indexOf(w) >= 0; }) || (compact && s.replace(/[^a-z0-9]/g, '').indexOf(compact) >= 0));
        a.hidden = !ok; a.classList.remove('on');
        if (ok) n += 1;
      });
      var first = list.querySelector('a[data-s]:not([hidden])');
      if (first) first.classList.add('on');
      if (add) add.href = '/bang-gia/model-may?ten=' + encodeURIComponent(find.value.trim());
      list.hidden = false;
    };
    find.addEventListener('input', show);
    find.addEventListener('focus', function () { find.select(); show(); });
    find.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      var first = list.querySelector('a[data-s]:not([hidden])');
      if (first) { e.preventDefault(); window.location.href = first.href; }
    });
    document.addEventListener('click', function (e) { if (!e.target.closest('.gm-find')) list.hidden = true; });
  }

  // Ô tìm gói: Enter lọc lại trang.
  var q = document.querySelector('[data-gm-q]');
  if (q) q.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    var href = q.dataset.href, v = q.value.trim();
    window.location.href = href + (v ? (href.indexOf('?') >= 0 ? '&' : '?') + 'q=' + encodeURIComponent(v) : '');
  });

  // Ô giá: so với giá đang lưu, vừa sửa thì xanh lá; dòng nhỏ ghi giá nhóm và gồm VAT.
  var form = document.querySelector('[data-gm-form]');
  if (!form) return;
  var inputs = Array.prototype.slice.call(form.querySelectorAll('[data-gm-in]'));
  var saving = false, countEl = form.querySelector('[data-gm-count]');
  inputs.forEach(function (el) { el.dataset.orig = String(num(el.defaultValue)); });
  var refresh = function (el) {
    var v = num(el.value), changed = String(v) !== el.dataset.orig, base = num(el.dataset.base);
    el.classList.toggle('gm-chg', changed);
    el.classList.toggle('gm-own', !changed && v > 0);
    var sub = el.parentNode.querySelector('[data-gm-sub]');
    if (sub && changed) {
      sub.className = 'gm-sub chg';
      sub.textContent = v ? ('nhóm ' + (base ? money(base) : 'chưa có') + ' · ' + money(Math.floor((v * (100 + vat) + 50) / 100)) + ' gồm VAT')
        : (base ? 'về giá nhóm ' + money(base) : 'bỏ giá riêng');
    }
    var n = inputs.filter(function (x) { return String(num(x.value)) !== x.dataset.orig; }).length;
    if (countEl) countEl.textContent = n;
  };
  form.addEventListener('input', function (e) { if (e.target.matches('[data-gm-in]')) refresh(e.target); });
  form.addEventListener('submit', function () { saving = true; });
  window.addEventListener('beforeunload', function (e) {
    if (saving) return;
    if (inputs.some(function (x) { return String(num(x.value)) !== x.dataset.orig; })) { e.preventDefault(); e.returnValue = ''; }
  });
})();

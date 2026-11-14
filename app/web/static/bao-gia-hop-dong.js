// Báo giá hợp đồng (mẫu H1 14/11/2026): tìm khách, chọn chung gói cho mọi model, bật/tắt một cấp ở mọi gói.
(function () {
  var form = document.querySelector('[data-hd-form]');
  if (!form) return;
  var find = form.querySelector('[data-hd-find]');
  if (find) {
    var options = form.querySelector('[data-hd-options]'), khach = form.querySelector('[data-hd-khach]');
    var picked = form.querySelector('[data-hd-picked]'), seq = 0, timer = null;
    find.addEventListener('input', function () {
      clearTimeout(timer);
      var q = find.value.trim();
      if (q.length < 2) { options.replaceChildren(); return; }
      timer = setTimeout(function () {
        var cur = ++seq;
        fetch(find.dataset.hdFind + '?' + new URLSearchParams({q: q}), {credentials: 'same-origin'})
          .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
          .then(function (html) { if (cur === seq) options.innerHTML = html; }).catch(function () {});
      }, 250);
    });
    find.addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });
    options.addEventListener('click', function (e) {
      var b = e.target.closest('[data-service-customer]');
      if (!b) return;
      khach.value = b.dataset.serviceCustomer;
      form.querySelector('[data-hd-picked-name]').textContent = b.dataset.serviceCustomer + ' · ' + b.dataset.label;
      picked.hidden = false; find.hidden = true; find.value = ''; options.replaceChildren();
    });
    form.querySelector('[data-hd-unpick]').addEventListener('click', function () {
      khach.value = ''; picked.hidden = true; find.hidden = false; find.focus();
    });
  }
  function globalLevels() {
    return Array.prototype.map.call(form.querySelectorAll('[data-hd-glv]:checked'), function (x) { return x.value; });
  }
  // Chọn chung: gói đang có ít nhất một ô chọn ở bất kỳ model nào thì tắt hết; không thì bật theo cấp chung.
  form.querySelectorAll('[data-hd-all]').forEach(function (btn) {
    var sid = btn.dataset.hdAll;
    function boxes() { return form.querySelectorAll('[data-hd-svc="' + sid + '"] input[type=checkbox]'); }
    function paint() { btn.classList.toggle('on', Array.prototype.some.call(boxes(), function (x) { return x.checked; })); }
    btn.addEventListener('click', function () {
      var on = !Array.prototype.some.call(boxes(), function (x) { return x.checked; }), lv = globalLevels();
      boxes().forEach(function (x) { x.checked = on && lv.indexOf(x.value) >= 0; });
      paint();
    });
    form.addEventListener('change', paint);
    paint();
  });
  form.querySelectorAll('[data-hd-glv]').forEach(function (g) {
    g.addEventListener('change', function () {
      form.querySelectorAll('[data-hd-lv="' + g.value + '"]').forEach(function (x) {
        var row = x.closest('[data-hd-svc]');
        var any = Array.prototype.some.call(row.querySelectorAll('input[type=checkbox]'), function (y) { return y !== x && y.checked; });
        if (!g.checked) x.checked = false; else if (any) x.checked = true;
      });
    });
  });
})();

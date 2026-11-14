// Báo giá nhanh (mẫu B1 14/11/2026): đổi model / hiện tượng / nơi sửa là tính lại giá; tìm khách; sao chép tin.
(function () {
  function copyText(area) {
    var text = area.value;
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).catch(function () { fallback(area); });
    fallback(area);
  }
  function fallback(area) {
    area.focus(); area.select();
    try { document.execCommand('copy'); } catch (e) {}
  }
  // Màn chi tiết: chỉ sao chép (không lưu trạng thái).
  document.querySelectorAll('[data-hg-copy-only]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var area = btn.form.querySelector('[data-hg-text]');
      copyText(area); btn.textContent = 'Đã sao chép ✓';
    });
  });

  var form = document.querySelector('[data-hg-form]');
  if (!form) return;
  var text = form.querySelector('[data-hg-text]'), edited = form.querySelector('[data-hg-edited]');
  var reset = form.querySelector('[data-hg-reset]'), seq = 0, timer = null;

  // Nút Sao chép: chép ngay trong lúc bấm (trình duyệt chỉ cho chép khi người dùng bấm), rồi gửi form để lưu.
  var copyBtn = form.querySelector('[data-hg-copy]');
  if (copyBtn) copyBtn.addEventListener('click', function () { if (text.value.trim()) copyText(text); });

  text.addEventListener('input', function () { edited.value = '1'; reset.hidden = false; });
  form.querySelector('[data-hg-reset-btn]').addEventListener('click', function () { edited.value = ''; reset.hidden = true; refresh(); });

  function params() {
    var data = new URLSearchParams();
    ['model', 'noi', 'khach'].forEach(function (name) {
      var el = form.querySelector('[name=' + name + ']:not([type=radio])') || form.querySelector('[name=' + name + ']:checked');
      if (el) data.append(name, el.value);
    });
    form.querySelectorAll('[name=ht]:checked').forEach(function (el) { data.append('ht', el.value); });
    return data;
  }
  function refresh() {
    var current = ++seq;
    fetch(form.dataset.preview + '?' + params().toString(), {credentials: 'same-origin'})
      .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
      .then(function (html) {
        if (current !== seq) return;
        var box = document.createElement('div'); box.innerHTML = html;
        ['group', 'chips', 'result'].forEach(function (name) {
          var part = box.querySelector('[data-part=' + name + ']'), slot = form.querySelector('[data-slot=' + name + ']');
          if (part && slot) slot.innerHTML = part.innerHTML;
        });
        var t = box.querySelector('[data-part=text]');
        if (t && edited.value !== '1') text.value = t.value;
      })
      .catch(function () {});
  }
  form.addEventListener('change', function (event) {
    var n = event.target.name;
    if (n === 'ht' || n === 'noi' || n === 'model') refresh();
  });
  form.querySelector('[data-hg-model]').addEventListener('input', function () {
    clearTimeout(timer); timer = setTimeout(refresh, 400);
  });

  // Khách (không bắt buộc): gõ tìm, bấm chọn.
  var find = form.querySelector('[data-hg-find]'), options = form.querySelector('[data-hg-options]');
  var khach = form.querySelector('[data-hg-khach]'), picked = form.querySelector('[data-hg-picked]');
  var findSeq = 0, findTimer = null;
  find.addEventListener('input', function () {
    clearTimeout(findTimer);
    var q = find.value.trim();
    if (q.length < 2) { options.replaceChildren(); return; }
    findTimer = setTimeout(function () {
      var current = ++findSeq;
      fetch(find.dataset.hgFind + '?' + new URLSearchParams({q: q}), {credentials: 'same-origin'})
        .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
        .then(function (html) { if (current === findSeq) options.innerHTML = html; })
        .catch(function () {});
    }, 250);
  });
  find.addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });
  options.addEventListener('click', function (event) {
    var btn = event.target.closest('[data-service-customer]');
    if (!btn) return;
    khach.value = btn.dataset.serviceCustomer;
    form.querySelector('[data-hg-picked-name]').textContent = btn.dataset.serviceCustomer + ' · ' + btn.dataset.label;
    picked.hidden = false; find.hidden = true; find.value = ''; options.replaceChildren();
  });
  form.querySelector('[data-hg-unpick]').addEventListener('click', function () {
    khach.value = ''; picked.hidden = true; find.hidden = false; find.focus();
  });
})();

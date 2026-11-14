// Tra giá thợ (mẫu T1 duyệt 15/11/2026): gõ model là hiện danh sách khớp (không cần dấu), bấm để chọn.
(function () {
  var input = document.querySelector('[data-tg2-find]'), list = document.querySelector('[data-tg2-list]');
  if (!input || !list) return;
  var seq = 0, timer = null;
  input.addEventListener('input', function () {
    clearTimeout(timer);
    var q = input.value.trim();
    timer = setTimeout(function () {
      var current = ++seq;
      fetch(input.dataset.tg2Find + '?' + new URLSearchParams({q: q}), {credentials: 'same-origin'})
        .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
        .then(function (html) { if (current === seq) list.innerHTML = html; })
        .catch(function () {});
    }, 250);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    var first = list.querySelector('a');
    if (first) window.location.href = first.href;
  });
})();
// Giá sản phẩm (mẫu SP1 duyệt 15/11/2026): gõ tên/mã là hiện hàng khớp ngay, giữ đường dẫn để bấm nhóm hàng/quay lại đúng chỗ.
(function () {
  var input = document.querySelector('[data-tgsp-find]'), list = document.querySelector('[data-tgsp-list]');
  if (!input || !list) return;
  var seq = 0, timer = null;
  input.addEventListener('input', function () {
    clearTimeout(timer);
    var q = input.value.trim();
    timer = setTimeout(function () {
      var current = ++seq;
      fetch(input.dataset.tgspFind + '?' + new URLSearchParams({q: q}), {credentials: 'same-origin'})
        .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
        .then(function (html) {
          if (current !== seq) return;
          list.innerHTML = html;
          history.replaceState(null, '', '/tra-gia/san-pham' + (q ? '?' + new URLSearchParams({q: q}) : ''));
        })
        .catch(function () {});
    }, 250);
  });
})();

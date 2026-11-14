// Báo giá sửa chữa (mẫu M4 14/11/2026): đổi PA1/PA2 thì bản khách đổi giá, VAT, tổng theo (giá đã tính sẵn ở máy chủ).
(function () {
  var form = document.querySelector('[data-rq-quote]');
  if (!form) return;
  function fmt(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  form.addEventListener('change', function () {
    var sum = 0, vat = 0;
    form.querySelectorAll('input[data-line]:checked').forEach(function (el) {
      var price = Number(el.dataset.price), v = Number(el.dataset.vat);
      sum += price; vat += v;
      var cell = form.querySelector('[data-rq-price="' + el.dataset.line + '"]');
      if (cell) cell.textContent = fmt(price);
    });
    form.querySelector('[data-rq-vat]').textContent = fmt(vat);
    form.querySelector('[data-rq-total]').textContent = fmt(sum + vat);
  });
})();

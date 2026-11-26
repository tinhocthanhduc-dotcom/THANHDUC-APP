// Báo giá sửa chữa (mẫu M4 14/11/2026): đổi PA1/PA2 thì bản khách đổi giá, VAT, tổng theo (giá đã tính sẵn ở máy chủ).
// Giá riêng phiếu (mẫu G296xR5EspFChNAWzbvvTy 26/11/2026): gõ Giá chốt thì giá dòng, VAT, tổng tính lại ngay.
(function () {
  var form = document.querySelector('[data-rq-quote]');
  if (!form) return;
  function fmt(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  function digits(v) { var d = String(v || '').replace(/\D/g, ''); return d ? Number(d) : 0; }
  function vatOf(p) { return Math.floor((p * 108 + 50) / 100) - p; }
  function linePrice(el) {
    var box = form.querySelector('[data-rq-custom="' + el.dataset.line + '"]');
    var typed = box ? digits(box.querySelector('[data-rq-custom-price]').value) : 0;
    if (typed > 0) return typed;
    if (box && box.hasAttribute('data-was-custom')) return Number(box.dataset.table || 0);  // đang giá riêng, xóa ô = về giá bảng
    return Number(el.dataset.price);
  }
  function update() {
    var sum = 0, vat = 0;
    form.querySelectorAll('input[data-line]:checked').forEach(function (el) {
      var price = linePrice(el), v = vatOf(price);
      if (!form.querySelector('[data-rq-missing]') || price > 0) { sum += price; vat += v; }  // gói thiếu giá: không vào tổng gửi khách
      var cell = form.querySelector('[data-rq-price="' + el.dataset.line + '"]');
      if (cell) cell.textContent = price ? fmt(price) : 'chưa có giá';
    });
    // Dòng vật tư/phí khác trên phiếu (TUẤN duyệt 20/11/2026) cộng sẵn ở data-base.
    var vatEl = form.querySelector('[data-rq-vat]'), totEl = form.querySelector('[data-rq-total]');
    vatEl.textContent = fmt(vat + Number(vatEl.dataset.base || 0));
    totEl.textContent = fmt(sum + vat + Number(totEl.dataset.base || 0));
  }
  form.addEventListener('change', update);
  form.addEventListener('input', function (e) { if (e.target.matches('[data-rq-custom-price]')) update(); });
  form.addEventListener('click', function (e) {
    var b = e.target.closest('[data-rq-custom-clear]');
    if (!b) return;
    var box = b.closest('[data-rq-custom]');
    box.querySelector('[data-rq-custom-price]').value = '';
    box.querySelector('textarea').value = '';
    var badge = box.querySelector('[data-rq-custom-badge]');
    if (badge) badge.remove();
    update();
  });
})();

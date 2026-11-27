// Báo giá sửa chữa (mẫu M4 14/11/2026): đổi PA1/PA2 thì bản khách đổi giá, VAT, tổng theo (giá đã tính sẵn ở máy chủ).
// Giá riêng phiếu (mẫu G296xR5EspFChNAWzbvvTy 26/11/2026): gõ Giá chốt thì giá dòng, VAT, tổng tính lại ngay.
// TUẤN 27/11/2026: Giá chốt gõ được giá đã gồm VAT (máy chủ tách ra giá chưa VAT); ô Bảo hành có nút Về theo gói.
(function () {
  var form = document.querySelector('[data-rq-quote]');
  if (!form) return;
  function fmt(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  function digits(v) { var d = String(v || '').replace(/\D/g, ''); return d ? Number(d) : 0; }
  function vatOf(p) { return Math.floor((p * 108 + 50) / 100) - p; }
  function withVat(p) { return p + vatOf(p); }
  function exVat(total) {  // cùng cách máy chủ (contract_quotes.ex_vat_for): giá chưa VAT mà gồm VAT ra đúng số đã gõ
    var guess = Math.round(total * 100 / 108);
    for (var d = 0; d <= 3; d++) {
      if (guess - d > 0 && withVat(guess - d) === total) return guess - d;
      if (withVat(guess + d) === total) return guess + d;
    }
    return Math.max(1, guess);
  }
  function gross() { var r = form.querySelector('input[name="gia_gom_vat"]:checked'); return !!(r && r.value === '1'); }
  function linePrice(el) {
    var box = form.querySelector('[data-rq-custom="' + el.dataset.line + '"]');
    var typed = box ? digits(box.querySelector('[data-rq-custom-price]').value) : 0;
    if (typed > 0) return gross() ? exVat(typed) : typed;
    if (box && box.hasAttribute('data-was-custom')) return Number(box.dataset.table || 0);  // đang giá riêng, xóa ô = về giá bảng
    return Number(el.dataset.price);
  }
  function typedGross(el) {  // gõ gồm VAT: VAT = số gõ − giá chưa VAT (giữ đúng tổng như máy chủ)
    var box = form.querySelector('[data-rq-custom="' + el.dataset.line + '"]');
    var typed = box && gross() ? digits(box.querySelector('[data-rq-custom-price]').value) : 0;
    return typed > 0 ? typed : 0;
  }
  function update() {
    var sum = 0, vat = 0;
    form.querySelectorAll('input[data-line]:checked').forEach(function (el) {
      var price = linePrice(el), g = typedGross(el), v = g ? g - price : vatOf(price);
      if (!form.querySelector('[data-rq-missing]') || price > 0) { sum += price; vat += v; }  // gói thiếu giá: không vào tổng gửi khách
      var cell = form.querySelector('[data-rq-price="' + el.dataset.line + '"]');
      if (cell) cell.textContent = price ? fmt(price) : 'chưa có giá';
    });
    // Dòng vật tư/phí khác trên phiếu (TUẤN duyệt 20/11/2026) cộng sẵn ở data-base.
    var vatEl = form.querySelector('[data-rq-vat]'), totEl = form.querySelector('[data-rq-total]');
    vatEl.textContent = fmt(vat + Number(vatEl.dataset.base || 0));
    totEl.textContent = fmt(sum + vat + Number(totEl.dataset.base || 0));
  }
  form.addEventListener('change', function (e) {
    if (e.target.name === 'gia_gom_vat') {
      var on = gross();
      form.querySelectorAll('[data-rq-custom-price]').forEach(function (inp) {
        var v = digits(inp.value);
        if (v > 0) inp.value = fmt(on ? withVat(v) : exVat(v));
        var box = inp.closest('[data-rq-custom]'), table = Number(box.dataset.table || 0);
        if (table) inp.placeholder = fmt(on ? withVat(table) : table);
      });
      form.querySelectorAll('[data-rq-vatlabel]').forEach(function (el) { el.textContent = on ? '(đã gồm VAT)' : '(chưa VAT)'; });
      form.querySelectorAll('[data-rq-table-text]').forEach(function (el) { el.textContent = on ? el.dataset.gross : el.dataset.net; });
    }
    update();
  });
  form.addEventListener('input', function (e) { if (e.target.matches('[data-rq-custom-price]')) update(); });
  form.addEventListener('click', function (e) {
    var w = e.target.closest('[data-rq-warranty-reset]');
    if (w) {
      var box0 = w.closest('[data-rq-warranty]'), radio = box0.querySelector('input[value="' + box0.dataset.table + '"]');
      if (radio) radio.checked = true;
      var wb = box0.querySelector('[data-rq-warranty-badge]');
      if (wb) wb.remove();
      w.remove();
      return;
    }
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

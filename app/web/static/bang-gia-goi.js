// Sửa gói sửa chữa (mẫu gọn TUẤN duyệt 15/11/2026): gõ giá là hiện ngay giá gồm VAT dưới ô.
(function () {
  var VAT = 8;
  function money(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  document.addEventListener('input', function (e) {
    var el = e.target.closest('[data-vat-src]');
    if (!el) return;
    var out = el.parentNode.querySelector('[data-vat]');
    if (!out) return;
    var p = parseInt(String(el.value).replace(/\D/g, ''), 10) || 0;
    out.textContent = p ? 'gồm VAT ' + money(Math.floor((p * (100 + VAT) + 50) / 100)) : '';
  });
})();

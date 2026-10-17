(function() {
  var form = document.querySelector('[data-device-form]');
  if (!form) return;
  var customer = form.querySelector('[name=customer_code]'), sites = form.querySelector('[data-customer-sites]'), sequence = 0;
  customer.addEventListener('input', function() { sequence++; sites.textContent = 'Chọn khách để xem địa điểm.'; });
  form.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-device-pick]');
    if (!btn) return;
    var picker = btn.closest('[data-picker]'), input = picker.querySelector('input');
    input.value = btn.dataset.devicePick;
    input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('[data-picked-label]').textContent = btn.dataset.label;
    picker.querySelector('.device-options').replaceChildren();
    if (input === customer) {
      var current = ++sequence;
      sites.textContent = 'Đang tải địa điểm…';
      fetch('/thiet-bi/dia-diem?' + new URLSearchParams({customer_code: input.value}), {credentials:'same-origin'})
        .then(function(r) { if (!r.ok) throw new Error(); return r.text(); })
        .then(function(html) { if (current === sequence) sites.innerHTML = html; })
        .catch(function() { if (current === sequence) sites.textContent = 'Không tải được địa điểm, hãy chọn khách lại.'; });
    }
  });
})();

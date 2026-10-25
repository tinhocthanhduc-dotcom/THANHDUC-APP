(function() {
  var form = document.querySelector('[data-device-form]');
  if (!form) return;
  var customer = form.querySelector('[name=customer_q]'), sites = form.querySelector('[data-customer-sites]'), sequence = 0;
  // Ô gõ chỉ để tìm; mã thật nằm ở ô ẩn và chỉ có khi bấm chọn một dòng.
  form.querySelectorAll('[data-picker]').forEach(function(picker) {
    var input = picker.querySelector('input[data-live]');
    input.addEventListener('input', function() {
      picker.querySelector('[data-picked-code]').value = '';
      picker.querySelector('[data-picked-label]').textContent = input.value.trim()
        ? (input.name === 'product_q' ? 'Có trong Hàng hóa thì bấm chọn; không chọn thì lưu là máy ngoài danh mục “' + input.value.trim() + '”.' : 'Bấm chọn một dòng trong danh sách bên dưới.')
        : '';
    });
  });
  customer.addEventListener('input', function() { sequence++; sites.textContent = ''; });
  form.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-device-pick]');
    if (!btn) return;
    var picker = btn.closest('[data-picker]'), input = picker.querySelector('input[data-live]'), code = picker.querySelector('[data-picked-code]');
    code.value = btn.dataset.devicePick;
    // Dòng "Ngoài danh mục": không có mã hàng, giữ nguyên tên đã gõ làm tên máy.
    input.value = btn.hasAttribute('data-device-free') ? btn.dataset.label : btn.dataset.devicePick + ' · ' + btn.dataset.label;
    input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('[data-picked-label]').textContent = btn.hasAttribute('data-device-free') ? 'Máy ngoài danh mục: ' + btn.dataset.label : 'Đã chọn: ' + btn.dataset.devicePick;
    picker.querySelector('.device-options').replaceChildren();
    if (input === customer) {
      var current = ++sequence;
      sites.textContent = 'Đang tải địa điểm…';
      fetch('/thiet-bi/dia-diem?' + new URLSearchParams({customer_code: code.value}), {credentials:'same-origin'})
        .then(function(r) { if (!r.ok) throw new Error(); return r.text(); })
        .then(function(html) { if (current === sequence) sites.innerHTML = html; })
        .catch(function() { if (current === sequence) sites.textContent = 'Không tải được địa điểm, hãy chọn khách lại.'; });
    }
  });
})();

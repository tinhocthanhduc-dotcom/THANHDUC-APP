(function() {
  var form = document.querySelector('[data-device-form]');
  if (!form) return;
  var customer = form.querySelector('[name=customer_q]'), sites = form.querySelector('[data-customer-sites]'), sequence = 0;
  // Ô gõ chỉ để tìm; mã thật nằm ở ô ẩn và chỉ có khi bấm chọn một dòng.
  form.querySelectorAll('[data-picker]').forEach(function(picker) {
    var input = picker.querySelector('input[data-live]');
    input.addEventListener('input', function() {
      picker.querySelector('[data-picked-code]').value = '';
      picker.querySelector('[data-picked-label]').textContent = input.value.trim() ? 'Bấm chọn một dòng trong danh sách bên dưới.' : '';
    });
  });
  customer.addEventListener('input', function() { sequence++; sites.textContent = 'Chọn khách để xem địa điểm.'; });
  // Máy ngoài danh mục Hàng hóa: ẩn ô tìm hàng, bật ô tên máy; ô đang ẩn bị disabled để không chặn gửi form.
  function source() {
    var picked = form.querySelector('[name=product_source]:checked'), value = picked ? picked.value : 'DM';
    form.querySelectorAll('[data-device-source-block]').forEach(function(block) {
      var on = block.dataset.deviceSourceBlock === value;
      block.hidden = !on;
      block.querySelectorAll('input').forEach(function(el) { el.disabled = !on; });
    });
  }
  form.querySelectorAll('[name=product_source]').forEach(function(radio) { radio.addEventListener('change', source); });
  if (form.querySelector('[name=product_source]')) source();
  form.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-device-pick]');
    if (!btn) return;
    var picker = btn.closest('[data-picker]'), input = picker.querySelector('input[data-live]'), code = picker.querySelector('[data-picked-code]');
    code.value = btn.dataset.devicePick;
    input.value = btn.dataset.devicePick + ' · ' + btn.dataset.label;
    input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('[data-picked-label]').textContent = 'Đã chọn: ' + btn.dataset.devicePick;
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

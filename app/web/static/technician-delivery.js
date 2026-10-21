// UX5c: giao đơn của thợ. Tiền cần thu tự tính theo số hàng giao (giá đã gồm VAT), tối đa số còn phải thu.
(function () {
  var form = document.querySelector('[data-tho-delivery]');
  if (!form) return;
  var amount = form.querySelector('[data-delivery-amount]'), fields = form.querySelector('[data-collect-fields]');
  var available = Number(form.dataset.available) || 0, manual = !!(amount && amount.hasAttribute('data-manual'));
  function num(v) { return Number(String(v || '').replace(',', '.')) || 0; }
  function format(n) { return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  function suggest() {
    var all = true, total = 0;
    form.querySelectorAll('[data-dline]').forEach(function (row) {
      var q = num(row.querySelector('input').value);
      if (q < num(row.dataset.remaining)) all = false;
      total += q * num(row.dataset.gross);
    });
    return all ? available : Math.min(available, Math.round(total));
  }
  function collecting() { var c = form.querySelector('[name=collect]:checked'); return c && c.value === 'YES'; }
  function update() {
    fields.hidden = !collecting();
    if (amount) amount.disabled = !collecting();
    if (amount && collecting() && !manual) amount.value = suggest() > 0 ? format(suggest()) : '';
  }
  form.addEventListener('input', function (event) {
    if (event.target === amount) { manual = true; return; }
    update();
  });
  form.addEventListener('change', update);
  // Ảnh giao hàng: nút lớn mở máy ảnh, hiện ảnh đã chụp; thiếu ảnh thì nhắc chứ không im lặng.
  var camera = form.querySelector('[data-delivery-camera]'), thumbs = form.querySelector('[data-photo-thumbs]'), count = form.querySelector('[data-photo-count]');
  if (camera) {
    camera.addEventListener('change', function () {
      thumbs.innerHTML = '';
      Array.prototype.forEach.call(camera.files, function (file) {
        var img = document.createElement('img'); img.alt = 'Ảnh giao hàng'; img.src = URL.createObjectURL(file); thumbs.appendChild(img);
      });
      count.textContent = camera.files.length ? 'Đã chụp ' + camera.files.length + ' ảnh. Bấm nút để chụp lại.' : 'Chưa có ảnh. Bấm nút trên để mở máy ảnh.';
    });
    camera.addEventListener('invalid', function () {
      if (window.tdToast) window.tdToast('Chụp ảnh giao hàng trước khi giao', 'bad');
      camera.closest('[data-delivery-photos]').scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
  }
  update();
})();

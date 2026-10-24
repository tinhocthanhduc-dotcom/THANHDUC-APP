// Đợt 4: Tồn kho › Sắp hết — tick nhiều dòng rồi lập một đơn mua điền sẵn (mã, kho, số lượng Nên đặt).
(function() {
  var root = document.querySelector('#stock-results');
  if (!root) return;
  function update() {
    var bar = root.querySelector('[data-ls-bar]');
    if (!bar) return;
    var picked = [].slice.call(root.querySelectorAll('[data-ls-pick]:checked'));
    var params = new URLSearchParams();
    picked.forEach(function(box) {
      params.append('product_code', box.dataset.code); params.append('kho', box.dataset.kho); params.append('qty', box.dataset.qty);
    });
    params.append('tu', 'sap-het');
    bar.querySelector('[data-ls-n]').textContent = picked.length;
    var go = bar.querySelector('[data-ls-go]');
    go.href = '/don-mua/moi?' + params.toString();
    go.textContent = picked.length ? 'Lập đơn mua cho ' + picked.length + ' dòng' : 'Chọn dòng để lập đơn mua';
    go.classList.toggle('is-blocked', !picked.length); go.setAttribute('aria-disabled', picked.length ? 'false' : 'true');
    root.querySelectorAll('[data-ls-pick]').forEach(function(box) { box.closest('.stock-balance').classList.toggle('ls-on', box.checked); });
  }
  root.addEventListener('change', function(e) { if (e.target.matches('[data-ls-pick]')) update(); });
  root.addEventListener('click', function(e) {
    if (e.target.closest('[data-ls-none]')) { root.querySelectorAll('[data-ls-pick]').forEach(function(box) { box.checked = false; }); update(); }
    var go = e.target.closest('[data-ls-go]');
    if (go && go.classList.contains('is-blocked')) e.preventDefault();
  });
  new MutationObserver(update).observe(root, {childList: true});
  update();
})();

(function() {
  var form = document.querySelector('[data-stock-form]');
  if (!form) return;
  function kind() { var el = form.querySelector('[name=kind]:checked') || form.querySelector('input[type=hidden][name=kind]'); return el ? el.value : 'PN'; }
  function panels() {
    form.querySelectorAll('[data-kind-only]').forEach(function(panel) {
      var show = panel.dataset.kindOnly === kind(); panel.hidden = !show;
      if (panel.tagName === 'FIELDSET') panel.disabled = !show;
      else panel.querySelectorAll('input,select').forEach(function(el) { el.disabled = !show; });
    });
    form.querySelectorAll('[data-cost]').forEach(function(panel) {
      panel.hidden = kind() === 'PX' || kind() === 'CK'; panel.querySelector('input').disabled = panel.hidden;
    });
  }
  form.addEventListener('change', function(e) { if (e.target.name === 'kind') panels(); });
  form.addEventListener('click', function(e) {
    var pick = e.target.closest('[data-pick-product]');
    if (pick) {
      var row = pick.closest('[data-stock-line]'), input = row.querySelector('[name=product_code]');
      window.pickAvailability(pick); input.value = pick.dataset.pickProduct; input.dispatchEvent(new Event('stock-picked'));
      row.querySelector('.product-name').textContent = pick.dataset.productName;
      row.querySelector('.product-options').replaceChildren(); return;
    }
    var remove = e.target.closest('[data-remove-line]');
    if (remove) { remove.closest('[data-stock-line]').remove(); return; }
    if (e.target.closest('[data-add-stock-line]')) {
      var row = document.querySelector('#stock-line-template').content.firstElementChild.cloneNode(true);
      row.querySelectorAll('[data-live]').forEach(window.bindLive);
      row.querySelectorAll('[data-money]').forEach(window.bindMoney);
      form.querySelector('[data-stock-lines]').appendChild(row); panels(); row.querySelector('input').focus();
    }
  });
  document.querySelectorAll('[data-confirm-void]').forEach(function(el) {
    el.addEventListener('submit', function(e) { if (!window.confirm('Hủy phiếu đã ghi và đảo các dòng sổ kho?')) e.preventDefault(); });
  });
})();

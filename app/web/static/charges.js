(function() {
  var form = document.querySelector('[data-charge-form]');
  if (!form) return;
  var editable = form.dataset.chargeEditable === '1', dirty = editable && form.dataset.chargeUnsaved === '1';
  function format(value) { return value.toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  function billing() { var input = form.querySelector('[name=billing]:checked'); return input ? input.value : ''; }
  function preview() {
    var subtotal = 0n, vatTotal = 0n, valid = true;
    form.querySelector('[data-free-reason]').required = billing() === 'MIEN_PHI';
    form.querySelectorAll('[data-charge-line]').forEach(function(row) {
      var quantity = row.querySelector('[name=line_qty]').value.trim().replace(',', '.');
      var enteredPrice = row.querySelector('[name=line_price]').value.trim();
      var price = enteredPrice.replace(/\./g, '');
      var vat = row.querySelector('[data-charge-vat]:checked');
      if (!/^(?:\d+(?:\.\d{0,3})?|\.\d{1,3})$/.test(quantity) || !/^(?:\d+|\d{1,3}(?:\.\d{3})+)$/.test(enteredPrice) || !vat) {
        valid = false; row.querySelector('[data-charge-row-total]').textContent = '—'; return;
      }
      var parts = quantity.split('.'), qty = BigInt(parts[0] || '0') * 1000n + BigInt((parts[1] || '').padEnd(3, '0'));
      var amount = (qty * BigInt(price) + 500n) / 1000n;
      var tax = (amount * BigInt(vat.value) + 50n) / 100n;
      subtotal += amount; vatTotal += tax;
      row.querySelector('[data-charge-row-total]').textContent = format(amount + tax);
    });
    form.querySelector('[data-charge-subtotal]').textContent = valid ? format(subtotal) : '—';
    form.querySelector('[data-charge-vat-total]').textContent = valid ? format(vatTotal) : '—';
    form.querySelector('[data-charge-total]').textContent = valid ? format(subtotal + vatTotal) : '—';
    form.querySelector('[data-charge-receivable]').textContent = billing() === 'MIEN_PHI' ? '0' : valid && billing() ? format(subtotal + vatTotal) : '—';
  }
  function reindex() {
    form.querySelectorAll('[data-charge-line]').forEach(function(row, index) {
      row.querySelectorAll('[data-charge-vat]').forEach(function(input) { input.name = 'vat_' + index; });
      row.querySelectorAll('[data-charge-warehouse]').forEach(function(input) { input.name = 'warehouse_' + index; });
    });
  }
  function changed() { if (editable) dirty = true; preview(); }
  form.addEventListener('input', changed); form.addEventListener('change', changed);
  form.addEventListener('click', function(event) {
    if (!editable) return;
    var button = event.target.closest('[data-charge-add]');
    if (button) {
      var row = document.querySelector('#charge-template-' + button.dataset.chargeAdd).content.firstElementChild.cloneNode(true);
      form.querySelector('[data-charge-lines]').appendChild(row); reindex();
      row.querySelectorAll('[data-live]').forEach(window.bindLive);
      row.querySelectorAll('[data-money]').forEach(window.bindMoney);
      changed(); row.querySelector('input:not([type=hidden])').focus(); return;
    }
    button = event.target.closest('[data-charge-remove]');
    if (button) { button.closest('[data-charge-line]').remove(); reindex(); changed(); return; }
    button = event.target.closest('[data-charge-pick]');
    if (button) {
      var row = button.closest('[data-charge-line]'), input = row.querySelector('[name=line_product]');
      window.pickAvailability(button); input.value = button.dataset.chargePick; input.dispatchEvent(new Event('stock-picked'));
      row.querySelector('[data-charge-name]').textContent = button.dataset.name;
      row.querySelector('.charge-options').replaceChildren();
      if (!row.querySelector('[name=line_description]').value) row.querySelector('[name=line_description]').value = button.dataset.name;
      input = row.querySelector('[name=line_price]'); input.value = format(BigInt(button.dataset.price));
      changed();
    }
  });
  document.querySelectorAll('[name=to_status][value=HOAN_THANH]').forEach(function(input) {
    var actionForm = input.form;
    actionForm.addEventListener('submit', function(event) {
      if (!dirty) return;
      event.preventDefault();
      var warning = form.querySelector('[data-charge-warning]');
      warning.hidden = false; warning.textContent = 'Tiền và vật tư vừa thay đổi chưa được lưu. Bấm Lưu tiền trước khi Hoàn thành.';
      warning.scrollIntoView({block:'center'});
    });
  });
  preview();
})();

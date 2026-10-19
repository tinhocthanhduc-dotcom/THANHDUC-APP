(function () {
  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-pick-holder], [data-pick-tech-warehouse]');
    if (!button) return;
    var picker = button.closest('[data-picker]'), value = picker.querySelector('[data-picked-value]');
    value.value = button.dataset.pickHolder || button.dataset.pickTechWarehouse;
    var input = picker.querySelector('input:not([type=hidden])');
    input.value = button.dataset.label; input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('.product-options').replaceChildren();
    if (button.dataset.pickTechWarehouse) { document.querySelector('[name=to_warehouse_code]').value = value.value; }
    if (button.dataset.pickTechWarehouse) document.querySelectorAll('[name=to_regular_warehouse]').forEach(function (r) { r.checked = false; });
  });
  document.addEventListener('change', function (event) {
    if (event.target.name === 'to_regular_warehouse') {
      document.querySelector('[name=to_warehouse_code]').value = event.target.value;
      document.querySelector('[data-tech-destination] input:not([type=hidden])').value = '';
    }
    if (event.target.name === 'kind' && event.target.value === 'KHO') {
      var picker = document.querySelector('[data-holder-picker]');
      if (picker) { picker.querySelector('[data-picked-value]').value = ''; picker.querySelector('input:not([type=hidden])').value = ''; }
    }
  });
  document.querySelectorAll('[data-holder-picker], [data-tech-destination]').forEach(function (picker) {
    var input = picker.querySelector('input:not([type=hidden])');
    input.addEventListener('input', function () { picker.querySelector('[data-picked-value]').value = ''; if (picker.hasAttribute('data-tech-destination')) { document.querySelector('[name=to_warehouse_code]').value=''; document.querySelectorAll('[name=to_regular_warehouse]').forEach(function(r){r.checked=false;}); } });
  });
})();

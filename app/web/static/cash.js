document.querySelectorAll('[data-cash-form],[data-correction-form]').forEach(function(form) {
  var opening=form.querySelector('[data-cash-opening]'), category=form.querySelector('[data-cash-category]');
  function toggle() {
    if (!opening || !category) return;
    category.hidden=opening.checked;
    category.querySelectorAll('input').forEach(function(input) { input.disabled=opening.checked; input.required=!opening.checked; });
  }
  if (opening) { opening.addEventListener('change',toggle); toggle(); }
  form.addEventListener('click',function(event) {
    var button=event.target.closest('[data-cash-pick]');
    if (!button) return;
    var picker=button.closest('[data-picker]'),input=picker.querySelector('input');
    input.value=button.dataset.cashPick;input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('.cash-options').replaceChildren();
    if (input.name==='customer_code' || input.name==='supplier_code') {
      form.querySelector('[name=partner_name]').value=button.dataset.name;
      var other=form.querySelector('[name="'+(input.name==='customer_code'?'supplier_code':'customer_code')+'"]');
      other.value='';other.dispatchEvent(new Event('stock-picked'));
      other.closest('[data-picker]').querySelector('.cash-options').replaceChildren();
    }
    input.focus();
  });
});

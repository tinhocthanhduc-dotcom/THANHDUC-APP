document.addEventListener('click',function(event) {
  var button=event.target.closest('[data-finance-pick]');
  if (!button) return;
  var picker=button.closest('[data-picker]'), input=picker.querySelector('[name=target_code]');
  input.value=button.dataset.financePick;
  input.dispatchEvent(new Event('stock-picked'));
  picker.querySelector('.finance-options').replaceChildren();
  input.focus();
});

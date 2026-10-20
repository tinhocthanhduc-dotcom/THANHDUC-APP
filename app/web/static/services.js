(function() {
  document.querySelectorAll('[data-service-confirm]').forEach(function(button) {
    button.form.addEventListener('submit', function(event) {
      if (!window.confirm(button.dataset.serviceConfirm)) event.preventDefault();
    });
  });
  var form = document.querySelector('[data-service-form]');
  if (!form) return;
  var customer = form.querySelector('[name=customer_code]');
  var choices = form.querySelector('[data-service-choices]'), sequence = 0;
  var picker = customer.closest('[data-picker]');
  customer.addEventListener('input', function() {
    sequence++;
    choices.textContent = 'Chọn khách để xem địa điểm và máy.';
    picker.querySelector('[data-picked-label]').textContent = '';
  });
  form.addEventListener('click', function(event) {
    var button = event.target.closest('[data-service-customer]');
    if (!button) return;
    customer.value = button.dataset.serviceCustomer;
    customer.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('[data-picked-label]').textContent = button.dataset.label;
    picker.querySelector('.service-options').replaceChildren();
    var current = ++sequence;
    choices.textContent = 'Đang tải địa điểm và máy…';
    fetch('/dich-vu/chon-khach?' + new URLSearchParams({customer_code: customer.value}), {credentials:'same-origin'})
      .then(function(response) { if (!response.ok) throw new Error(); return response.text(); })
      .then(function(html) { if (current === sequence) { choices.innerHTML = html; if(window.initPicked)window.initPicked(choices); customer.closest('[data-picked]')._choices=html; } })
      .catch(function() { if (current === sequence) choices.textContent = 'Không tải được địa điểm và máy, hãy chọn khách lại.'; });
  });
})();

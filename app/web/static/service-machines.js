(function() {
  function refresh(root) {
    root.querySelectorAll('[data-machine-selector]').forEach(function(form) {
      var choice = form.querySelector('[name=machine_pick]:checked');
      var fields = form.querySelector('[data-machine-new-fields]');
      if (fields) fields.hidden = fields.disabled = !(choice && choice.value === 'NEW');
    });
    var intake = document.querySelector('[data-service-form]');
    if (intake) {
      var kind = intake.querySelector('[name=kind]');
      var required = kind && kind.value === 'MANG_VE';
      var label = intake.querySelector('[data-machine-required-label]');
      if (label) label.textContent = required ? '(bắt buộc)' : '(không bắt buộc)';
      var create = intake.querySelector('[name=create_machine]');
      var isNew = !!(create && create.checked);
      var serial = intake.querySelector('[name=serial]');
      if (serial) { serial.disabled = isNew; serial.required = required && !isNew; }
      var fields = intake.querySelector('[data-machine-new-fields]');
      if (fields) fields.hidden = fields.disabled = !isNew;
    }
    if (window.bindLive) root.querySelectorAll('[data-machine-product-picker] [data-live]').forEach(window.bindLive);
  }
  document.addEventListener('change', function(event) {
    if (event.target.matches('[name=machine_pick], [name=create_machine], [name=kind]')) refresh(document);
  });
  document.addEventListener('input', function(event) {
    var picker = event.target.closest('[data-machine-product-picker]');
    if (!picker || !event.target.matches('[data-live]')) return;
    picker.querySelector('[data-machine-product-code]').value = '';
    picker.querySelector('[data-machine-picked-label]').textContent = 'Bấm chọn một kết quả tìm.';
  });
  document.addEventListener('click', function(event) {
    var button = event.target.closest('[data-device-pick]');
    var picker = button && button.closest('[data-machine-product-picker]');
    if (!picker) return;
    var input = picker.querySelector('[data-live]');
    picker.querySelector('[data-machine-product-code]').value = button.dataset.devicePick;
    input.value = button.dataset.devicePick + ' · ' + button.dataset.label;
    input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('[data-machine-picked-label]').textContent = 'Đã chọn: ' + button.dataset.devicePick;
    picker.querySelector('.machine-product-options').replaceChildren();
  });
  var choices = document.querySelector('[data-service-choices]');
  if (choices) new MutationObserver(function() { refresh(choices); }).observe(choices, {childList: true});
  refresh(document);
})();

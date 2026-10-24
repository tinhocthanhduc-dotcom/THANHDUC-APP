(function() {
  function refresh(root) {
    root.querySelectorAll('[data-machine-selector]').forEach(function(form) {
      var fields = form.querySelector('[data-machine-new-fields]');
      var newPicked = Array.prototype.some.call(form.elements, function(el) { return el.name === 'machine_pick' && el.value === 'NEW' && el.checked; });
      if (fields) fields.hidden = fields.disabled = !newPicked;
      // MM1: báo trước số phiếu sẽ tách thêm khi tick từ 2 máy.
      var count = Array.prototype.filter.call(form.elements, function(el) { return el.name === 'machine_pick' && el.checked; }).length;
      var note = form.querySelector('[data-machine-split-note]');
      if (note) {
        if (!note.dataset.base) note.dataset.base = note.textContent;
        note.textContent = count > 1 ? 'Đã chọn ' + count + ' máy: lưu sẽ tách thêm ' + (count - 1) + ' phiếu, mỗi máy một phiếu, cùng thợ.' : note.dataset.base;
        note.classList.toggle('machine-split-on', count > 1);
      }
      var button = form.querySelector('button.btn-ok');
      if (button) button.textContent = count > 1 ? 'Lưu ' + count + ' máy' : 'Lưu máy';
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

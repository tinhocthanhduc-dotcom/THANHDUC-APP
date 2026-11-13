(function() {
  function refresh(root) {
    root.querySelectorAll('[data-machine-selector]').forEach(function(form) {
      var add = form.hasAttribute('data-add-form');
      var pickName = add ? 'add_pick' : 'machine_pick';
      var fields = form.querySelector('[data-machine-new-fields]');
      var newPicked = Array.prototype.some.call(form.elements, function(el) { return el.name === pickName && el.value === 'NEW' && el.checked; });
      if (fields) fields.hidden = fields.disabled = !newPicked;
      // TUẤN 13/11/2026: nhiều khung máy mới; mỗi khung là một máy.
      var blocks = newPicked ? form.querySelectorAll('[data-nm-block]').length || 1 : 0;
      form.querySelectorAll('[data-nm-block]').forEach(function(block, i) {
        var title = block.querySelector('[data-nm-title]');
        if (title) title.textContent = 'Máy mới ' + (i + 1);
        var temp = block.querySelector('[data-nm-temp]');
        var serial = block.querySelector('[data-nm-serial]');
        if (temp && serial) { serial.readOnly = temp.checked; serial.required = !temp.checked; if (temp.checked) serial.value = ''; serial.placeholder = temp.checked ? 'App cấp mã tạm khi lưu' : ''; }
      });
      var picked = Array.prototype.filter.call(form.elements, function(el) { return el.name === pickName && el.checked && el.value !== 'NEW'; }).length;
      var count = picked + blocks;
      var note = form.querySelector('[data-machine-split-note]');
      if (note) {
        if (!note.dataset.base) note.dataset.base = note.textContent;
        if (add) note.textContent = count ? 'Đã chọn ' + count + ' máy: lưu sẽ tách thêm ' + count + ' phiếu, mỗi máy một phiếu, cùng thợ.' : note.dataset.base;
        else note.textContent = count > 1 ? 'Đã chọn ' + count + ' máy: lưu sẽ tách thêm ' + (count - 1) + ' phiếu, mỗi máy một phiếu, cùng thợ.' : note.dataset.base;
        note.classList.toggle('machine-split-on', add ? count > 0 : count > 1);
      }
      if (add) {
        var submit = form.querySelector('[data-add-submit]');
        if (submit) { submit.textContent = count ? 'Thêm ' + count + ' máy, tách phiếu' : 'Thêm máy, tách phiếu'; submit.disabled = !count; }
      } else {
        var button = form.querySelector('button.btn-ok');
        if (button) button.textContent = count > 1 ? 'Lưu ' + count + ' máy' : 'Lưu máy';
      }
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
    if (event.target.matches('[name=machine_pick], [name=add_pick], [name=create_machine], [name=kind], [data-nm-temp]')) refresh(document);
  });
  document.addEventListener('input', function(event) {
    var picker = event.target.closest('[data-machine-product-picker]');
    if (!picker || !event.target.matches('[data-live]')) return;
    picker.querySelector('[data-machine-product-code]').value = '';
    picker.querySelector('[data-machine-picked-label]').textContent = event.target.value.trim() ? 'Có trong Hàng hóa thì bấm chọn; không chọn thì lưu là máy ngoài danh mục “' + event.target.value.trim() + '”.' : 'Không chọn dòng nào thì tên đã gõ được lưu là máy ngoài danh mục.';
  });
  document.addEventListener('click', function(event) {
    var more = event.target.closest('[data-nm-add]');
    if (more) {
      var list = more.closest('[data-nm-list]');
      var blocks = list.querySelectorAll('[data-nm-block]');
      if (blocks.length >= 20) return;
      var next = 0;
      list.querySelectorAll('[name=nm_idx]').forEach(function(el) { next = Math.max(next, parseInt(el.value, 10) + 1 || 0); });
      var html = list.querySelector('[data-nm-template]').innerHTML.split('__I__').join(String(next)).split('__N__').join(String(blocks.length + 1));
      more.insertAdjacentHTML('beforebegin', html);
      refresh(list.closest('form') || document);
      var added = more.previousElementSibling && more.previousElementSibling.querySelector('[data-live]');
      if (added) added.focus();
      return;
    }
    var drop = event.target.closest('[data-nm-remove]');
    if (drop) {
      var box = drop.closest('[data-nm-list]');
      var form = drop.closest('form');
      drop.closest('[data-nm-block]').remove();
      if (!box.querySelector('[data-nm-block]') && form) {
        var chip = Array.prototype.find.call(form.elements, function(el) { return (el.name === 'add_pick' || el.name === 'machine_pick') && el.value === 'NEW'; });
        if (chip) chip.checked = false;
        var html0 = box.querySelector('[data-nm-template]').innerHTML.split('__I__').join('0').split('__N__').join('1');
        box.querySelector('[data-nm-add]').insertAdjacentHTML('beforebegin', html0);
      }
      refresh(form || document);
      return;
    }
    var button = event.target.closest('[data-device-pick]');
    var picker = button && button.closest('[data-machine-product-picker]');
    if (!picker) return;
    var input = picker.querySelector('[data-live]');
    var free = button.hasAttribute('data-device-free');
    picker.querySelector('[data-machine-product-code]').value = button.dataset.devicePick;
    input.value = free ? button.dataset.label : button.dataset.devicePick + ' · ' + button.dataset.label;
    input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('[data-machine-picked-label]').textContent = free ? 'Máy ngoài danh mục: ' + button.dataset.label : 'Đã chọn: ' + button.dataset.devicePick;
    picker.querySelector('.machine-product-options').replaceChildren();
  });
  var choices = document.querySelector('[data-service-choices]');
  if (choices) new MutationObserver(function() { refresh(choices); }).observe(choices, {childList: true});
  refresh(document);
})();

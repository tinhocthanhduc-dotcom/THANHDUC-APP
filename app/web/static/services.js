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
    fetch((form.dataset.choicesSource || '/dich-vu/chon-khach') + '?' + new URLSearchParams({customer_code: customer.value}), {credentials:'same-origin'})
      .then(function(response) { if (!response.ok) throw new Error(); return response.text(); })
      .then(function(html) { if (current === sequence) { choices.innerHTML = html; if(window.initPicked)window.initPicked(choices); customer.closest('[data-picked]')._choices=html; } })
      .catch(function() { if (current === sequence) choices.textContent = 'Không tải được địa điểm và máy, hãy chọn khách lại.'; });
  });
})();
// Nhận đơn: gợi ý theo loại, thêm nhanh mô tả lỗi, tóm tắt phiếu cập nhật khi nhập.
(function() {
  var form = document.querySelector('form.svn[data-service-form]');
  if (!form) return;
  var hints = {};
  try { hints = JSON.parse(form.dataset.kindHints || '{}'); } catch (e) {}
  function q(sel) { return form.querySelector(sel); }
  function picked(name) {
    var el = form.querySelector('select[name=' + name + ']') || form.querySelector('[name=' + name + ']:checked');
    if (!el) return null;
    if (el.tagName === 'SELECT') { var o = el.options[el.selectedIndex]; return o ? {value: el.value, label: o.textContent.trim(), option: o} : null; }
    var span = el.closest('label'); return {value: el.value, label: span ? span.textContent.trim() : el.value};
  }
  function set(key, text, empty) { var dd = q('[data-sv-sum=' + key + ']'); if (dd) { dd.textContent = text; dd.classList.toggle('svn-empty', !!empty); } }
  function check(key, ok, text) {
    var li = q('[data-sv-check=' + key + ']'); if (!li) return;
    li.dataset.ok = li.hasAttribute('data-optional') ? (ok ? '1' : 'opt') : (ok ? '1' : '0');
    if (text) li.textContent = text;
  }
  function appointment() {
    var c = picked('appointment_choice');
    if (!c) return '';
    if (c.value === 'GIO') { var t = q('[name=appointment_at]'); return t && t.value ? t.value.replace('T', ' ') : ''; }
    return c.label;
  }
  function update() {
    var title = q('[data-card-title]'), code = q('[name=customer_code]');
    var hasCustomer = !!(code && code.value && title && title.textContent.trim());
    set('customer', hasCustomer ? title.textContent.split(' · ').slice(1).join(' · ') || title.textContent : 'Chưa chọn', !hasCustomer);
    var site = picked('site_id');
    set('site', site ? site.label : 'Địa chỉ chính');
    var addr = q('[data-sv-site-address]');
    if (addr && site && site.option) addr.textContent = site.option.dataset.address || '';
    var kind = picked('kind');
    set('kind', kind && kind.value ? kind.label : 'Chưa phân loại', !(kind && kind.value));
    var hint = q('[data-sv-kind-hint]');
    if (hint) hint.textContent = hints[kind ? kind.value : ''] || hints[''] || '';
    var prio = picked('priority'); set('priority', prio ? prio.label : '');
    var ap = appointment(); set('appointment', ap || 'Chưa hẹn', !ap);
    var tech = picked('assignee_id');
    var hasTech = !!(tech && tech.value);
    set('tech', hasTech ? tech.label : 'Điều phối giao sau', !hasTech);
    var techHint = q('[data-sv-tech-hint]');
    if (techHint) techHint.textContent = hasTech ? 'Phiếu vào thẳng màn Hôm nay của ' + tech.label + ', thợ nhận thông báo.' : 'Chưa giao: phiếu nằm ở hàng chờ Điều phối.';
    var symptom = q('[name=symptom]');
    // TUẤN 27/10: Mực định kỳ chọn mực ngay khi nhận đơn; ô mô tả lỗi thành ghi chú cho thợ, điền sẵn.
    var isInk = !!(kind && kind.value === 'MUC_DINH_KY'), inkBox = q('[data-ink-section]'), symLabel = q('[data-symptom-label]');
    if (inkBox) inkBox.hidden = !isInk;
    if (symLabel && symLabel.firstChild) symLabel.firstChild.nodeValue = (isInk ? 'Ghi chú cho thợ' : 'Mô tả lỗi') + ' ';
    if (isInk && symptom && !symptom.value.trim()) symptom.value = 'Giao mực định kỳ';
    check('customer', hasCustomer);
    check('symptom', symptom && symptom.value.trim().length >= 3);
    check('appointment', !!ap);
    check('kind', kind && kind.value, kind && kind.value ? 'Loại: ' + kind.label : 'Loại dịch vụ: để trống được');
  }
  form.addEventListener('click', function(event) {
    var chip = event.target.closest('[data-sv-quick]');
    if (chip) {
      var box = q('[name=symptom]'), cur = box.value.replace(/[\s,]*$/, '');
      box.value = (cur ? cur + ', ' : '') + chip.dataset.svQuick;
      box.focus();
    }
    setTimeout(update, 0);
  });
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  new MutationObserver(function() { setTimeout(update, 0); }).observe(q('[data-service-choices]'), {childList: true});
  var card = q('[data-customer-card]');
  if (card) new MutationObserver(update).observe(card, {childList: true, subtree: true, characterData: true});
  update();
})();

// Thợ tự nhận (TUẤN 04/11/2026): ô tự xám khi phiếu Khẩn, Bảo hành hoặc đã chọn thợ (server cũng bỏ qua các phiếu này).
(function () {
  var box = document.querySelector('[data-sv-self-claim]');
  if (!box || !box.form) return;
  var form = box.form, label = box.closest('label');
  function value(name) {
    var el = form.elements[name];
    if (!el) return '';
    if (el.length !== undefined && !el.tagName) { for (var i = 0; i < el.length; i++) if (el[i].checked) return el[i].value; return ''; }
    return el.value || '';
  }
  function sync() {
    var off = value('priority') === 'KHAN' || value('kind') === 'BAO_HANH' || value('assignee_id') !== '';
    box.disabled = off;
    if (label) label.classList.toggle('is-off', off);
  }
  form.addEventListener('change', sync);
  sync();
})();
// Mực định kỳ lần trước (TUẤN duyệt 06/11/2026): Dùng lại chỉ điền sẵn form, người nhận đơn vẫn sửa trước khi Nhận đơn.
(function() {
  var form = document.querySelector('form.svn[data-service-form]');
  if (!form) return;
  var filled = [], used = null;
  function q(sel) { return form.querySelector(sel); }
  function mark(el) { if (el) { el.classList.add('svn-filled'); filled.push(el); } }
  function pick(select, value) {
    if (!select || select.disabled) return false;
    var ok = Array.prototype.some.call(select.options, function(o) { return o.value === value; });
    if (ok) { select.value = value; mark(select); }
    return ok;
  }
  function inkBox() { return q('[data-ink-section]'); }
  function clearInk() { var box = inkBox(); if (box) box.querySelector('[data-part-lines]').replaceChildren(); }
  function fire() {
    var box = inkBox(); if (box) box.dispatchEvent(new Event('part-added'));
    form.dispatchEvent(new Event('change', {bubbles: true}));
  }
  function fill(r) {
    var kind = q('select[name=kind]');
    if (kind) { kind.value = 'MUC_DINH_KY'; mark(kind); }
    if (r.site_id) pick(q('select[name=site_id]'), r.site_id);
    if (r.serial) pick(q('select[name=serial]'), r.serial);
    var box = inkBox();
    if (box) {
      clearInk();
      var lines = box.querySelector('[data-part-lines]'), tpl = box.querySelector('[data-part-line-template]');
      (r.lines || []).forEach(function(x) {
        var row = tpl.content.firstElementChild.cloneNode(true);
        row.querySelector('[data-part-code]').value = x.code;
        row.querySelector('strong').textContent = x.code;
        row.querySelector('small').textContent = x.name;
        row.querySelector('[data-part-qty]').value = x.qty;
        lines.appendChild(row);
      });
      if (r.lines && r.lines.length) mark(lines);
    }
    var price = q('[name=ink_price]');
    if (price && r.price) { price.value = r.price; mark(price); }
    var symptom = q('[name=symptom]');
    if (symptom && r.note && (!symptom.value.trim() || symptom.value.trim() === 'Giao mực định kỳ')) { symptom.value = r.note; mark(symptom); }
  }
  function undo() {
    var kind = q('select[name=kind]'); if (kind) kind.value = '';
    var serial = q('select[name=serial]'); if (serial && !serial.disabled) serial.value = '';
    clearInk();
    var price = q('[name=ink_price]'); if (price) price.value = '';
    var symptom = q('[name=symptom]'); if (symptom && used && symptom.value === used.note) symptom.value = '';
    filled.forEach(function(el) { el.classList.remove('svn-filled'); }); filled = []; used = null;
  }
  form.addEventListener('click', function(event) {
    var useBtn = event.target.closest('[data-sv-repeat-use]'), undoBtn = event.target.closest('[data-sv-repeat-undo]');
    var section = event.target.closest('[data-sv-repeat]');
    if (!section || !(useBtn || undoBtn)) return;
    var done = section.querySelector('[data-sv-repeat-done]');
    if (useBtn) {
      var r; try { r = JSON.parse(useBtn.dataset.svRepeatUse); } catch (e) { return; }
      undo(); used = r; fill(r);
      section.classList.add('svn-repeat-used');
      section.querySelector('[data-sv-repeat-label]').textContent = '✓ Đã điền từ ' + r.number;
      done.hidden = false;
      if (window.tdToast) window.tdToast('Đã điền theo ' + r.number + ', kiểm tra lại rồi bấm Nhận đơn');
    } else {
      undo();
      section.classList.remove('svn-repeat-used');
      done.hidden = true;
    }
    fire();
  });
})();

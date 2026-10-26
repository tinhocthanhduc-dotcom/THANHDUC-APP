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

// Thẻ B: lấy vị trí lúc bấm Đã đến khách (không chặn thợ) và tự lưu nháp báo cáo lên máy chủ.
(function () {
  var lat = document.querySelector('[data-gps-lat]');
  if (lat && navigator.geolocation) {
    var form = lat.form;
    navigator.geolocation.getCurrentPosition(function (pos) {
      lat.value = pos.coords.latitude.toFixed(6);
      form.querySelector('[data-gps-lng]').value = pos.coords.longitude.toFixed(6);
      form.querySelector('[data-gps-accuracy]').value = Math.round(pos.coords.accuracy || 0);
    }, function () {}, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  }

  var box = document.querySelector('[data-tech-report]'), complete = document.getElementById('service-complete');
  if (!box || !complete) return;
  var skip = { csrf: 1, form_key: 1, version: 1, return_to: 1, material_product: 1 };
  var status = box.querySelector('[data-draft-status]'), timer = null, last = '';
  function fields() {
    return Array.prototype.filter.call(complete.elements, function (el) {
      return el.name && !skip[el.name] && el.type !== 'file' && el.type !== 'submit' && el.type !== 'button';
    });
  }
  function collect() {
    var data = {};
    fields().forEach(function (el) {
      if (el.name === 'material_qty') {
        var code = el.closest('[data-tech-material]').querySelector('[name=material_product]').value;
        data['mat:' + code] = el.value;
      } else if (el.type === 'radio') {
        if (el.checked) data[el.name] = el.value;
        else if (!(el.name in data)) data[el.name] = '';
      } else if (el.type === 'checkbox') data[el.name + ':' + el.value] = el.checked ? '1' : '';
      else data[el.name] = el.value;
    });
    return data;
  }
  function restore(data) {
    fields().forEach(function (el) {
      if (el.name === 'material_qty') {
        var row = el.closest('[data-tech-material]'), code = row.querySelector('[name=material_product]').value, v = data['mat:' + code];
        if (v === undefined) return;
        el.value = v;
        if (Number(String(v).replace(',', '.')) > 0) row.hidden = false;
      } else if (el.type === 'checkbox') {
        if ((el.name + ':' + el.value) in data) el.checked = data[el.name + ':' + el.value] === '1';
      } else if (el.name in data) {
        if (el.type === 'radio') el.checked = data[el.name] === el.value;
        else el.value = data[el.name];
      }
    });
    var picker = complete.querySelector('[data-item-picker]');
    if (picker) picker.dispatchEvent(new Event('item-picker-refresh'));
    complete.dispatchEvent(new Event('change', { bubbles: true }));
  }
  try { var saved = JSON.parse(box.dataset.draft || 'null'); if (saved) { restore(saved); status.textContent = 'Đã mở lại nháp đang làm dở.'; } } catch (e) {}
  last = JSON.stringify(collect());
  function save() {
    var json = JSON.stringify(collect());
    if (json === last) return;
    var body = new FormData();
    body.set('csrf', box.dataset.csrf);
    body.set('data', json);
    status.textContent = 'Đang lưu…';
    fetch('/tho/phieu/' + box.dataset.ticket + '/nhap', { method: 'POST', body: body, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(); last = json; status.textContent = 'Đã lưu ' + new Date().toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' }); })
      .catch(function () { status.textContent = 'Chưa lưu, sẽ thử lại'; });
  }
  function later() { clearTimeout(timer); timer = setTimeout(save, 1500); }
  var screen = document.querySelector('[data-technician-ticket]') || document;
  screen.addEventListener('input', later);
  screen.addEventListener('change', later);
  screen.addEventListener('click', function (e) { if (e.target.closest('[data-qty-step],[data-fill-value],[data-fee-suggestion],[data-collection-clear]')) later(); });
  complete.addEventListener('submit', function () { clearTimeout(timer); });
})();

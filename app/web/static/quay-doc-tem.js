// Chụp tem máy tại quầy (TUẤN duyệt mẫu 06/11/2026): gửi ảnh tem cho máy Z2 đọc, hỏi lại mỗi 3 giây,
// đọc xong thì điền sẵn khách + máy (viền xanh). Chỉ điền ô còn trống; nhân viên vẫn tự bấm Nhận máy.
(function() {
  var box = document.querySelector('[data-tem-box]');
  var form = box && box.closest('form');
  if (!form) return;
  var KEY = 'td-quay-tem', POLL = 3000, LIMIT = 220000;
  var jobInput = box.querySelector('[data-tem-job]'), body = box.querySelector('[data-tem-body]');
  var img = box.querySelector('[data-tem-img]'), info = box.querySelector('[data-tem-info]');
  var pick = box.querySelector('[data-tem-pick]'), chk = box.querySelector('[data-tem-chk]'), actions = box.querySelector('[data-tem-actions]');
  var title = box.querySelector('[data-tem-title]'), intro = box.querySelector('[data-tem-intro]');
  var run = 0, pending = null, marked = [], objectUrl = '';

  function q(sel) { return form.querySelector(sel); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) { var r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); });
  }
  function store(v) { try { if (v) sessionStorage.setItem(KEY, JSON.stringify(v)); else sessionStorage.removeItem(KEY); } catch (e) {} }
  function stored() { try { return JSON.parse(sessionStorage.getItem(KEY) || 'null'); } catch (e) { return null; } }
  function mark(el) { if (el) { el.classList.add('svn-filled'); marked.push(el); } }
  function tone(name) { box.classList.remove('tem-ok', 'tem-warn', 'tem-bad'); if (name) box.classList.add('tem-' + name); }
  function show(state) {
    var idle = state === 'idle';
    pick.hidden = !idle; intro.hidden = !idle; body.hidden = idle; actions.hidden = idle;
    chk.hidden = state !== 'done';
  }
  function setTitle(t) { title.textContent = t; }

  function reset() {
    run++; pending = null; jobInput.value = ''; store(null);
    marked.forEach(function(el) { el.classList.remove('svn-filled'); }); marked = [];
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = ''; }
    img.removeAttribute('src'); info.replaceChildren(); tone(''); setTitle('Chụp tem máy để điền sẵn'); show('idle');
  }

  function fail(text) {
    tone('bad'); setTitle('Chưa đọc được tem'); show('error');
    info.innerHTML = '<p>' + esc(text) + '</p>';
    jobInput.value = ''; store(null);
  }

  // Chọn khách giống bấm một dòng trong ô tìm khách (services.js tải lại danh sách máy).
  function chooseCustomer(c) {
    var picker = q('[data-picker]');
    if (!picker) return;
    var b = document.createElement('button');
    b.type = 'button'; b.hidden = true; b.dataset.serviceCustomer = c.code; b.dataset.label = c.code + ' · ' + c.name;
    picker.appendChild(b); b.click(); b.remove();
    mark(q('[name=customer_code]'));
  }

  // Áp phần điền máy khi danh sách máy của khách đã tải (gọi lại mỗi lần danh sách đổi).
  function applyMachine() {
    if (!pending) return;
    var serial = q('select[name=serial]');
    if (!serial) return;
    if (pending.serial) {
      var ok = Array.prototype.some.call(serial.options, function(o) { return o.value === pending.serial; });
      if (ok && !serial.disabled) { serial.value = pending.serial; mark(serial); serial.dispatchEvent(new Event('change', {bubbles: true})); }
      pending = null;
      return;
    }
    if (pending.newSerial) {
      var create = q('[name=create_machine]');
      if (!create) return;
      if (!create.checked) { create.checked = true; create.dispatchEvent(new Event('change', {bubbles: true})); }
      var ns = q('[name=new_serial]');
      if (ns && !ns.value.trim()) { ns.value = pending.newSerial; mark(ns); }
      var qInput = q('[name=new_product_q]'), code = q('[data-machine-product-code]'), label = q('[data-machine-picked-label]');
      if (qInput && !qInput.value.trim()) {
        if (pending.product && pending.product.code) {
          qInput.value = pending.product.code + ' · ' + pending.product.name;
          if (code) code.value = pending.product.code;
          if (label) label.textContent = 'Đã chọn: ' + pending.product.code + ' (gợi ý theo tem, kiểm lại)';
        } else if (pending.model) {
          qInput.value = pending.model;
          qInput.dispatchEvent(new Event('input', {bubbles: true}));  // hiện danh sách mã gần đúng để chọn
        }
        mark(qInput);
      }
      var hint = info.querySelector('[data-tem-hint]');
      if (hint) hint.textContent = 'Đã điền serial và model vào "+ Máy mới" (viền xanh). Kiểm lại với khách.';
      pending = null;
    }
  }
  var choices = q('[data-service-choices]');
  if (choices) new MutationObserver(function() { setTimeout(applyMachine, 0); }).observe(choices, {childList: true});

  function pill(cls, text) { return '<span class="tem-pill ' + cls + '">' + esc(text) + '</span>'; }

  function render(r, apply) {
    show('done');
    var model = [r.hang, r.model].filter(Boolean).join(' ');
    if (!r.ro) {
      tone('bad'); setTitle('Chưa đọc được serial'); chk.hidden = true; jobInput.value = ''; store(null);
      info.innerHTML = '<p>' + (model ? 'Đọc được model <b>' + esc(model) + '</b> nhưng ' : 'Chưa đọc được: ') + esc(r.chua_ro || 'không thấy số serial') +
        '. Chụp lại gần hơn, nghiêng tránh lóa đèn, hoặc nhập tay bên dưới.</p>';
      return;
    }
    var customerInput = q('[name=customer_code]'), current = customerInput ? customerInput.value.trim() : '';
    var rows = '<dt>Model</dt><dd>' + esc(model || r.machine_name || 'chưa rõ') + '</dd><dt>Serial</dt><dd>' + esc(r.serial) + '</dd>';
    var notes = '';
    if (r.found) {
      tone('ok'); setTitle('Đọc được tem ✓');
      rows += '<dt>Của khách</dt><dd>' + (r.customer ? esc(r.customer.code + ' ' + r.customer.name) : 'chưa gắn khách') + '</dd>';
      rows += '<dt>Bảo hành máy</dt><dd>' + (r.warranty_until ? pill(r.warranty_ok ? 'ok' : 'bad', (r.warranty_ok ? 'Còn đến ' : 'Hết từ ') + r.warranty_until) : 'chưa ghi hạn') + '</dd>';
      if (r.last) rows += '<dt>Lần sửa gần nhất</dt><dd>' + esc(r.last.number + (r.last.date ? ' · ' + r.last.date : '')) +
        (r.last.until ? ' · ' + pill(r.last.ok ? 'warn' : 'bad', 'BH sửa ' + (r.last.ok ? 'còn đến ' : 'hết từ ') + r.last.until) : '') + '</dd>';
      if (r.held) {
        notes += '<p class="tem-note bad">Máy đang giữ theo ' + esc(r.held) + ', chưa trả khách. Không tự chọn máy; trả máy theo phiếu đó trước.</p>';
      } else if (!r.customer || !r.customer.active) {
        notes += '<p class="tem-note">' + (r.customer ? 'Khách của máy này đang ngừng dùng.' : 'Máy chưa gắn khách nào.') + ' Chọn khách bên dưới rồi chọn máy.</p>';
      } else if (current && current !== r.customer.code) {
        notes += '<p class="tem-note">Serial này đang ghi cho <b>' + esc(r.customer.code + ' ' + r.customer.name) + '</b>, khác khách đang chọn. ' +
          '<button type="button" class="btn btn-new" data-tem-switch>Đổi sang khách này</button></p>';
      } else {
        notes += '<p class="hint">Đã điền khách và chọn máy (viền xanh). Kiểm lại với khách.</p>';
      }
      if (apply && !r.held && r.customer && r.customer.active) {
        if (!current) { pending = {serial: r.serial}; chooseCustomer(r.customer); }
        else if (current === r.customer.code) { pending = {serial: r.serial}; applyMachine(); }
      }
    } else {
      tone('warn'); setTitle('Đọc được tem, máy chưa có trong app');
      if (r.near && r.near.length) rows += '<dt>Serial gần giống</dt><dd>' + esc(r.near.join(', ')) + '</dd>';
      if (r.product && r.product.code) rows += '<dt>Mã hàng gợi ý</dt><dd>' + esc(r.product.code + ' · ' + r.product.name) + '</dd>';
      notes += '<p class="hint" data-tem-hint>' + (current ? 'Đã điền serial và model vào "+ Máy mới" (viền xanh).' : 'Chọn khách (hoặc + Tạo mới khách hàng), app sẽ điền serial và model vào "+ Máy mới".') + '</p>';
      if (apply) { pending = {newSerial: r.serial, model: model, product: r.product}; applyMachine(); }
    }
    info.innerHTML = '<dl>' + rows + '</dl>' + notes;
    var sw = info.querySelector('[data-tem-switch]');
    if (sw) sw.addEventListener('click', function() {
      pending = {serial: r.serial}; chooseCustomer(r.customer);
      sw.closest('p').outerHTML = '<p class="hint">Đã đổi sang ' + esc(r.customer.code + ' ' + r.customer.name) + ', máy được chọn sẵn.</p>';
    });
  }

  function poll(job, mine, started, apply) {
    if (mine !== run) return;
    fetch('/quay-sua/doc-tem/' + job, {credentials: 'same-origin', headers: {Accept: 'application/json'}})
      .then(function(res) { if (!res.ok) throw new Error(String(res.status)); return res.json(); })
      .then(function(r) {
        if (mine !== run) return;
        if (r.status === 'DANG') {
          if (Date.now() - started > LIMIT) return fail('Máy Z2 chưa trả lời. Chụp lại hoặc nhập tay bên dưới.');
          return setTimeout(function() { poll(job, mine, started, apply); }, POLL);
        }
        if (r.status === 'LOI') return fail(r.error || 'Máy Z2 chưa đọc được ảnh. Chụp lại hoặc nhập tay.');
        render(r, apply);
      })
      .catch(function(err) {
        if (mine !== run) return;
        if (err && err.message === '404') return fail('Lượt đọc tem này không còn. Chụp lại hoặc nhập tay.');
        if (Date.now() - started > LIMIT) return fail('Mất kết nối khi chờ đọc tem. Chụp lại hoặc nhập tay.');
        setTimeout(function() { poll(job, mine, started, apply); }, POLL);
      });
  }

  function waiting() {
    tone(''); setTitle('Đang đọc tem…'); show('wait');
    info.innerHTML = '<div class="tem-spin"><span class="tem-dot"></span>Đang đọc tem… thường 20–60 giây</div>' +
      '<p class="hint">Trong lúc chờ, cứ ghi mô tả lỗi, phụ kiện, chụp ảnh máy. Đọc xong app chỉ điền vào ô còn trống. Bấm Nhận máy được ngay, không cần chờ.</p>';
  }

  function send(file) {
    reset();
    var mine = run;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file); img.src = objectUrl;
    waiting();
    var data = new FormData();
    data.append('csrf', form.querySelector('[name=csrf]').value);
    data.append('form_key', uuid());
    data.append('photo', file, file.name || 'tem.jpg');
    fetch(box.dataset.temStart, {method: 'POST', body: data, credentials: 'same-origin', headers: {Accept: 'application/json'}})
      .then(function(res) { return res.json().catch(function() { return {error: 'Không gửi được ảnh (mã ' + res.status + ').'}; }); })
      .then(function(r) {
        if (mine !== run) return;
        if (!r.job) return fail(r.error || 'Không gửi được ảnh.');
        jobInput.value = r.job;
        store({job: r.job, at: Date.now()});
        poll(r.job, mine, Date.now(), true);
      })
      .catch(function() { if (mine === run) fail('Mất kết nối khi gửi ảnh. Chụp lại hoặc nhập tay.'); });
  }

  box.querySelectorAll('[data-tem-file]').forEach(function(input) {
    input.addEventListener('change', function() {
      var file = input.files && input.files[0];
      input.value = '';
      if (file) send(file);
    });
  });
  box.querySelector('[data-tem-again]').addEventListener('click', function() { reset(); box.querySelector('[data-tem-file]').click(); });
  box.querySelector('[data-tem-drop]').addEventListener('click', reset);
  form.addEventListener('submit', function() { store(null); });

  // Mở lại màn: sau lỗi lưu (server giữ tem_job) chỉ hiện lại kết quả; quay về từ "+ Tạo mới khách hàng" thì điền tiếp máy mới.
  var saved = jobInput.value, apply = false;
  if (!saved) {
    var s = stored();
    if (s && s.job && Date.now() - s.at < 30 * 60 * 1000 && /[?&]customer_code=/.test(location.search)) { saved = String(s.job); apply = true; }
    else store(null);
  }
  if (saved) {
    jobInput.value = saved; img.src = '/quay-sua/doc-tem/' + encodeURIComponent(saved) + '/anh';
    waiting(); poll(saved, run, Date.now(), apply);
  } else {
    show('idle');
  }
})();

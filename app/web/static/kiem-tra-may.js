// Kiểm tra máy (mẫu M3 14/11/2026): tick phương án là app tự chọn cấp (0 chính → C1, 1 → C2, từ 2 → C3); chọn cấp khác thì hiện ô lý do.
// Mẫu K1 15/11/2026: đổi model là dịch vụ của nhóm máy lên đầu ngay, thêm dòng linh kiện, khung tóm tắt bên phải cập nhật theo lựa chọn.
(function () {
  var form = document.querySelector('[data-rq-form]');
  if (!form) return;
  var sum = form.querySelector('[data-ktm-sum]'), sumModel = form.querySelector('[data-ktm-sum-model]');
  var partNames = {};
  document.querySelectorAll('#ktm-part-list option').forEach(function (o) { partNames[o.value.toUpperCase()] = o.textContent; });

  function el(tag, cls, text) { var x = document.createElement(tag); if (cls) x.className = cls; if (text) x.textContent = text; return x; }

  function summary() {
    if (!sum) return;
    sum.textContent = '';
    form.querySelectorAll('[data-rq-svc]').forEach(function (box) {
      if (!box.querySelector('[data-rq-on]').checked) return;
      var cap = box.querySelector('[data-rq-cap]:checked');
      var parts = [].filter.call(box.querySelectorAll('[data-ktm-code]'), function (i) { return i.value.trim(); }).length;
      var li = el('li');
      li.appendChild(el('b', '', box.querySelector('.ktm-svc-head b').textContent));
      var bits = [cap ? cap.dataset.label : 'chưa chọn cấp'];
      if (box.querySelector('[data-ktm-ch]').checked) bits.push('chính hãng');
      if (parts) bits.push(parts + ' linh kiện');
      li.appendChild(el('small', '', bits.join(' · ')));
      sum.appendChild(li);
    });
    if (!sum.children.length) sum.appendChild(el('li', 'mu', 'Chưa chọn dịch vụ'));
  }

  form.querySelectorAll('[data-rq-svc]').forEach(function (box) {
    var caps = box.querySelectorAll('[data-rq-cap]'), reason = box.querySelector('[data-rq-reason]');
    var text = box.querySelector('[data-rq-auto-text]'), tag = box.querySelector('[data-ktm-tag]'), touched = false;
    function auto() {
      var n = box.querySelectorAll('input[data-main]:checked').length;
      return n === 0 ? 1 : n === 1 ? 2 : 3;
    }
    function picked() {
      var c = box.querySelector('[data-rq-cap]:checked');
      return c ? Number(c.value) : 0;
    }
    function sync() {
      var a = auto();
      if (!touched || !picked()) caps.forEach(function (c) { c.checked = Number(c.value) === a; });
      if (text) text.textContent = '· app tính cấp ' + a;
      reason.hidden = picked() === a;
      var input = reason.querySelector('input');
      if (input) input.required = !reason.hidden && box.querySelector('[data-rq-on]').checked;
      var c = box.querySelector('[data-rq-cap]:checked');
      if (tag) tag.textContent = c ? c.dataset.label : '';
    }
    if (picked() && picked() !== auto()) touched = true;
    box.addEventListener('change', function (e) {
      if (e.target.matches('[data-rq-cap]')) touched = true;
      sync();
      summary();
    });
    sync();

    // Linh kiện: hiện tên hàng dưới mã, nút thêm dòng (bản không JS vẫn có một dòng trống).
    var list = box.querySelector('[data-ktm-parts]'), add = box.querySelector('[data-ktm-add]');
    function showName(input) {
      var name = input.parentNode.querySelector('[data-ktm-pname]');
      if (name) name.textContent = partNames[input.value.trim().toUpperCase()] || '';
    }
    list.querySelectorAll('[data-ktm-code]').forEach(showName);
    list.addEventListener('input', function (e) { if (e.target.matches('[data-ktm-code]')) { showName(e.target); summary(); } });
    if (add) {
      add.hidden = false;
      add.addEventListener('click', function () {
        var row = list.lastElementChild.cloneNode(true);
        row.querySelector('[data-ktm-code]').value = '';
        row.querySelector('input[inputmode]').value = '1';
        row.querySelector('[data-ktm-pname]').textContent = '';
        list.appendChild(row);
        row.querySelector('[data-ktm-code]').focus();
      });
    }
  });

  // Model: dịch vụ có giá cho nhóm/model đó lên đầu, còn lại vào "Dịch vụ khác"; dịch vụ đã tick luôn ở trên.
  var model = form.querySelector('[data-ktm-model]'), group = form.querySelector('[data-ktm-group]');
  var main = form.querySelector('[data-ktm-main]'), other = form.querySelector('[data-ktm-other-list]');
  var others = form.querySelector('[data-ktm-others]'), empty = form.querySelector('[data-ktm-empty]');
  var models = {};
  document.querySelectorAll('#ktm-models option').forEach(function (o) {
    models[o.value.trim().toLowerCase()] = { g: o.dataset.g, m: o.dataset.m, name: o.textContent };
  });
  function regroup() {
    var hit = models[model.value.replace(/\s+/g, ' ').trim().toLowerCase()];
    if (sumModel) sumModel.textContent = model.value.trim() || '—';
    if (group) {
      group.classList.toggle('none', !hit);
      group.textContent = '';
      if (hit) { group.appendChild(document.createTextNode('Nhóm: ')); group.appendChild(el('b', '', hit.name)); }
      else group.textContent = 'Chưa có trong bảng giá';
    }
    if (!hit || !main || !other) return;
    var boxes = [].slice.call(form.querySelectorAll('[data-rq-svc]'));
    var top = 0;
    boxes.forEach(function (box) {
      var keys = (box.dataset.priced || '').split(' ');
      var up = keys.indexOf(hit.g) >= 0 || keys.indexOf(hit.m) >= 0 || box.querySelector('[data-rq-on]').checked;
      if (up) { main.insertBefore(box, empty); top++; } else other.appendChild(box);
    });
    if (empty) empty.hidden = top > 0;
    if (others) {
      var n = other.children.length;
      others.hidden = n === 0;
      var count = others.querySelector('[data-ktm-others-n]');
      if (count) count.textContent = n;
      if (!top) others.open = true;
    }
  }
  if (model) {
    model.addEventListener('change', regroup);
    model.addEventListener('input', regroup);
  }
  summary();
})();

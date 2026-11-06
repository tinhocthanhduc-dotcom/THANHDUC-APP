// SX1: bộ chọn hàng (dòng ẩn không gửi lên server), chọn một thành phẩm, ô lý do khi hỏng hết.
(function () {
  function setRow(row, on) {
    row.hidden = !on;
    row.querySelectorAll('input, select, textarea, button').forEach(function (el) { el.disabled = !on; });
  }
  document.querySelectorAll('[data-item-picker]').forEach(function (root) {
    root.querySelectorAll('[data-pick-row]').forEach(function (row) {
      if (row.hidden || row.hasAttribute('data-pick-extra')) setRow(row, false);
    });
    root.addEventListener('item-picked', function (event) {
      var row = event.target.closest('[data-pick-row]');
      if (!row) return;
      if (root.hasAttribute('data-pick-single')) {
        root.querySelectorAll('[data-pick-row]').forEach(function (other) { if (other !== row) setRow(other, false); });
        var radio = row.querySelector('input[type=radio]');
        if (radio) radio.checked = true;
      }
      setRow(row, true);
      row.dispatchEvent(new CustomEvent('item-picker-refresh', { bubbles: true }));
    });
    root.addEventListener('click', function (event) {
      var remove = event.target.closest('[data-pick-remove]');
      if (!remove) return;
      setRow(remove.closest('[data-pick-row]'), false);
      root.dispatchEvent(new CustomEvent('item-picker-refresh'));
      var search = root.querySelector('[data-pick-search]');
      if (search) search.focus();
    });
  });
  function num(el) { return Number(String(el.value || '').replace(',', '.')) || 0; }
  function sum(sel, root) { var t = 0; (root || document).querySelectorAll(sel).forEach(function (el) { if (!el.disabled) t += num(el); }); return t; }
  // SX2: ô lý do hỏng hết hiện khi tổng Tốt của mọi thành phẩm = 0.
  var complete = document.querySelector('[data-sx-complete]');
  if (complete) {
    var reason = complete.querySelector('[data-sx-void]'), submit = complete.querySelector('[data-sx-submit]');
    var sync = function () {
      var good = sum('[data-sx-good]', complete), scrap = sum('[data-sx-scrap]', complete);
      if (reason) reason.hidden = good > 0;
      if (submit) submit.textContent = sum('[data-sx-used]', complete) > 0 ? submit.getAttribute('data-with') : submit.getAttribute('data-without');
      // Ô tóm tắt ở cột phải và dòng Cộng của bảng theo số đang gõ.
      complete.querySelectorAll('[data-sx-sum-good]').forEach(function (el) { el.textContent = good; });
      complete.querySelectorAll('[data-sx-sum-scrap]').forEach(function (el) { el.textContent = scrap; });
      var lines = 0; complete.querySelectorAll('[data-sx-used]').forEach(function (el) { if (!el.disabled && num(el) > 0) lines++; });
      complete.querySelectorAll('[data-sx-sum-lines]').forEach(function (el) { el.textContent = lines; });
    };
    complete.addEventListener('input', sync);
    complete.addEventListener('click', function () { setTimeout(sync, 0); });
    complete.addEventListener('item-picker-refresh', sync);
    sync();
  }
  // SX2: tổng số định làm của các thành phẩm ở form tạo.
  var form = document.querySelector('[data-sx-form]');
  if (form) {
    var tally = function () {
      var plan = sum('[data-sx-plan]', form), outs = 0, mats = 0;
      form.querySelectorAll('[data-sx-plan]').forEach(function (el) { if (!el.disabled) outs++; });
      form.querySelectorAll('[data-sx-material]').forEach(function (el) { if (!el.disabled && num(el) > 0) mats++; });
      form.querySelectorAll('[data-sx-plan-total]').forEach(function (el) { el.textContent = plan; });
      form.querySelectorAll('[data-sx-output-count]').forEach(function (el) { el.textContent = outs; });
      form.querySelectorAll('[data-sx-material-count]').forEach(function (el) { el.textContent = mats; });
    };
    form.addEventListener('input', tally);
    form.addEventListener('click', function () { setTimeout(tally, 0); });
    form.addEventListener('item-picker-refresh', tally);
    tally();
  }
  // Định mức vật tư (TUẤN duyệt 06/11/2026). Form lập phiếu: vật tư dự kiến = số định làm × định mức, cộng dồn theo mã.
  function fmt(n) { return String(Math.round(n * 1000) / 1000).replace('.', ','); }
  function readJson(id) { var el = document.getElementById(id); try { return el ? JSON.parse(el.textContent) : {}; } catch (e) { return {}; } }
  var mats = form && form.querySelector('[data-sx-materials-box]');
  if (form && mats) {
    var boms = readJson('sx-boms'), techHeld = readJson('sx-tech-held');
    var chip = mats.querySelector('[data-bom-chip]'), undo = mats.querySelector('[data-bom-undo]');
    var matRows = function () { return Array.prototype.slice.call(mats.querySelectorAll('[data-pick-row]')); };
    var auto = !matRows().some(function (r) { return !r.hidden; });
    var heldNow = function () {
      if (mats.hasAttribute('data-own')) return null;   // thợ tự lập: cột Tồn kho thợ đã có sẵn
      var from = form.querySelector('input[name=from_warehouse_code]:checked'), tech = form.querySelector('input[name=technician_id]:checked');
      if (!tech || !tech.value || (from && from.value)) return undefined;
      return techHeld[tech.value] || {};
    };
    var fill = function () {
      var need = {};
      form.querySelectorAll('[data-sx-plan]').forEach(function (el) {
        if (el.disabled) return;
        var code = el.closest('[data-pick-row]').querySelector('input[name=output_product]').value;
        (boms[code] || []).forEach(function (m) { need[m[0]] = (need[m[0]] || 0) + Number(m[1]) * num(el); });
      });
      var held = heldNow(), any = false;
      matRows().forEach(function (row) {
        var code = row.querySelector('input[name=material_product]').value, input = row.querySelector('[data-sx-material]');
        if (auto && !row.hasAttribute('data-bom-manual')) {
          if (need[code] > 0) { setRow(row, true); row.setAttribute('data-bom-auto', ''); input.value = fmt(need[code]); }
          else if (row.hasAttribute('data-bom-auto')) { row.removeAttribute('data-bom-auto'); setRow(row, false); }
        }
        if (row.hasAttribute('data-bom-auto')) any = true;
        var short = row.querySelector('[data-bom-short]'), want = row.hidden ? 0 : num(input);
        var have = held === null ? Number(row.getAttribute('data-held') || 0) : held === undefined ? null : Number(held[code] || 0);
        if (short) { short.hidden = !(have !== null && want > have); short.textContent = 'thiếu ' + fmt(want - (have || 0)); }
      });
      mats.classList.toggle('dm-filled', any);
      if (chip) chip.hidden = !any;
      if (undo) undo.hidden = !any;
      tally();
    };
    mats.addEventListener('click', function (event) { var gone = event.target.closest('[data-pick-remove]'); if (gone) gone.closest('[data-pick-row]').setAttribute('data-bom-manual', ''); });
    mats.addEventListener('input', function (event) { var row = event.target.closest('[data-bom-auto]'); if (row) row.setAttribute('data-bom-manual', ''); });
    if (undo) undo.addEventListener('click', function () {
      auto = false;
      matRows().forEach(function (row) { if (row.hasAttribute('data-bom-auto')) { row.removeAttribute('data-bom-auto'); if (!row.hasAttribute('data-bom-manual')) setRow(row, false); } });
      fill();
    });
    form.addEventListener('input', fill);
    form.addEventListener('change', fill);
    form.addEventListener('click', function () { setTimeout(fill, 0); });
    form.addEventListener('item-picker-refresh', function () { setTimeout(fill, 0); });
    fill();
  }
  // Sửa định mức: vốn ước tính theo số đang gõ.
  var bomForm = document.querySelector('[data-bom-form]');
  if (bomForm) {
    var price = Number(bomForm.getAttribute('data-price') || 0);
    var money = function (n) { return Math.round(n).toLocaleString('vi-VN'); };
    var calc = function () {
      var total = 0, lines = 0;
      bomForm.querySelectorAll('[data-pick-row]').forEach(function (row) {
        var input = row.querySelector('[data-bom-qty]'), amount = row.querySelector('[data-bom-amount]');
        if (row.hidden || !input || input.disabled) return;
        var v = num(input) * Number(row.getAttribute('data-cost') || 0);
        if (num(input) > 0) lines++;
        total += v;
        if (amount) amount.textContent = money(v);
      });
      bomForm.querySelectorAll('[data-bom-cost]').forEach(function (el) { el.textContent = money(total); });
      bomForm.querySelectorAll('[data-bom-margin]').forEach(function (el) { el.textContent = price ? money(price - total) + ' (' + Math.round((price - total) / price * 100) + '%)' : '–'; });
      bomForm.querySelectorAll('[data-bom-lines]').forEach(function (el) { el.textContent = lines; });
    };
    bomForm.addEventListener('input', calc);
    bomForm.addEventListener('click', function () { setTimeout(calc, 0); });
    bomForm.addEventListener('item-picker-refresh', calc);
    calc();
  }
  var confirmBox = document.querySelector('[data-sx-confirm] textarea');
  if (confirmBox) confirmBox.focus();
})();

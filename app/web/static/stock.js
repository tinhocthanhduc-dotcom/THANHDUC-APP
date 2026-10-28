// Đợt 3 · khuôn 5: lập phiếu kho. Loại phiếu đổi nhãn kho và ô đối tác; tìm hàng để thêm, cột Tồn theo kho đang chọn, Tóm tắt và Bước tiếp.
(function() {
  var form = document.querySelector('[data-stock-form]');
  if (!form) return;
  var live = form.dataset.editable === '1', lines = form.querySelector('[data-stock-lines]');
  var KINDS = {PN: 'Nhập', PX: 'Xuất', CK: 'Chuyển', KK: 'Kiểm kê'}, WH = {PN: 'Kho nhận', KK: 'Kho kiểm'};
  function kind() { var el = form.querySelector('[name=kind]:checked') || form.querySelector('input[type=hidden][name=kind]'); return el ? el.value : 'PN'; }
  function wh() { var el = form.querySelector('[name=warehouse_code]:checked'); return el ? el.value : ''; }
  function num(text) { return Number(String(text || '').replace(',', '.')); }
  function digits(text) { var d = String(text || '').replace(/\D/g, ''); return d === '' ? NaN : Number(d); }
  function money(n) { return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); }
  function qtyText(n) { return String(Math.round(n * 1000) / 1000).replace('.', ','); }
  function rows() { return [].slice.call(lines.querySelectorAll('[data-stock-line]')); }
  function panels() {
    var k = kind(), nocost = k === 'PX' || k === 'CK';
    form.querySelectorAll('[data-kind-only]').forEach(function(panel) {
      var show = panel.dataset.kindOnly === k; panel.hidden = !show;
      if (panel.tagName === 'FIELDSET') panel.disabled = !show;
      else panel.querySelectorAll('input,select').forEach(function(el) { el.disabled = !show; });
    });
    form.querySelectorAll('[data-cost]').forEach(function(panel) { panel.hidden = nocost; panel.querySelector('input').disabled = nocost; });
    form.classList.toggle('sk-nocost', nocost);
    var label = form.querySelector('[data-sk-wh-label]'); if (label) label.textContent = WH[k] || 'Kho xuất';
    var row = form.querySelector('[data-sk-value-row]'); if (row) row.hidden = nocost;
    form.querySelectorAll('[data-kind-check]').forEach(function(li) { li.hidden = li.dataset.kindCheck !== k; });
    // Kiểm kê: thủ kho chỉ gửi giám đốc duyệt; giám đốc tự ghi sổ (TUẤN 2026-10-28). Máy chủ cũng chỉ lưu nháp.
    var primary = form.querySelector('[data-sk-primary]'), save = form.querySelector('[data-sk-save]'), note = form.querySelector('[data-sk-post-note]');
    if (primary && form.dataset.kkApprove === '1') {
      var send = k === 'KK';
      primary.textContent = send ? 'Gửi giám đốc duyệt' : 'Ghi sổ';
      if (save) save.hidden = send;
      if (note) note.textContent = send ? 'Phiếu kiểm kê chờ giám đốc duyệt mới đổi tồn kho.' : 'Ghi sổ là tồn kho đổi ngay. Ghi sai thì hủy phiếu có lý do.';
    }
  }
  function update() {
    var w = wh(), qty = 0, value = 0, valued = true, filled = true, n = rows().length;
    rows().forEach(function(row) {
      var code = row.querySelector('[name=product_code]').value.trim(), q = num(row.querySelector('[name=qty]').value);
      var stock = {}; try { stock = JSON.parse(row.dataset.stock || '{}'); } catch (e) {}
      var badge = row.querySelector('[data-sk-stock]');
      badge.textContent = code && w ? (stock[w] || '0') : '';
      badge.classList.toggle('is-out', !!(code && w) && !(num(stock[w]) > 0));
      if (!isNaN(q)) qty += q;
      var cost = row.querySelector('[name=unit_cost]'), c = cost ? digits(cost.value) : NaN;
      if (isNaN(c) || isNaN(q)) valued = false; else value += q * c;
      // Kiểm kê được đếm 0 (hết hàng); phiếu khác phải lớn hơn 0.
      var typedQty = row.querySelector('[name=qty]').value.trim() !== '';
      if (!code || !(q > 0 || (kind() === 'KK' && typedQty && q === 0))) filled = false;
    });
    var k = kind();
    form.querySelector('[data-sk-kind]').textContent = KINDS[k] || k;
    form.querySelector('[data-sk-wh]').textContent = w || 'Chưa chọn';
    form.querySelector('[data-sk-lines]').textContent = n;
    form.querySelector('[data-sk-qty]').textContent = n ? qtyText(qty) : '—';
    var bar = form.querySelector('[data-sk-qty-bar]'); if (bar) bar.textContent = n ? qtyText(qty) : '—';
    form.querySelector('[data-sk-value]').textContent = n && valued ? money(value) + ' đồng' : '—';
    form.querySelector('[data-sk-count]').textContent = n + ' dòng';
    form.querySelector('[data-sk-empty]').hidden = n > 0;
    if (!live) return;
    var supplier = form.querySelector('[name=supplier_code]'), dest = form.querySelector('[name=to_warehouse_code]');
    var checks = {wh: !!w, party: !!(supplier && supplier.value.trim()), dest: !!(dest && dest.value), lines: n > 0, filled: n > 0 && filled};
    var missing = [];
    form.querySelectorAll('[data-sk-checks] [data-check]').forEach(function(li) {
      if (li.hidden) return;
      var ok = checks[li.dataset.check]; li.dataset.ok = ok ? '1' : '0';
      li.textContent = (ok ? '✓ ' : '✕ ') + li.textContent.replace(/^[✓✕] /, '');
      if (!ok) missing.push(li.textContent.slice(2));
    });
    var primary = form.querySelector('[data-sk-primary]');
    primary.classList.toggle('is-blocked', missing.length > 0); primary.setAttribute('aria-disabled', missing.length ? 'true' : 'false');
    primary.dataset.missing = missing.join(' · ');
  }
  function fill(row, data) {
    row.querySelector('[name=product_code]').value = data.code;
    row.querySelector('.product-name').textContent = data.name;
    row.querySelector('[data-sk-code]').textContent = data.code + (data.unit ? ' · ' + data.unit : '');
    row.dataset.stock = data.stock || '{}'; row.dataset.picked = '1'; row.classList.remove('pol-bad');
    var err = row.querySelector('.field-error'); if (err) err.remove();
    row.querySelector('.product-options').replaceChildren();
  }
  function addRow(data) {
    var row = document.querySelector('#stock-line-template').content.firstElementChild.cloneNode(true);
    lines.appendChild(row);
    row.querySelectorAll('[data-live]').forEach(window.bindLive);
    row.querySelectorAll('[data-money]').forEach(window.bindMoney);
    if (data) fill(row, data);
    panels();
    return row;
  }
  form.addEventListener('change', function(e) { if (e.target.name === 'kind') panels(); update(); });
  form.addEventListener('input', function(e) {
    // Gõ lại ô mã: dòng trở về chưa chọn để hiện ô tìm; gõ lại ô đối tác: bỏ thẻ đã chọn.
    if (e.target.name === 'product_code') e.target.closest('[data-stock-line]').dataset.picked = '0';
    var party = e.target.closest('[data-sk-party]'); if (party) party.dataset.picked = '0';
    update();
  });
  form.addEventListener('click', function(e) {
    var pick = e.target.closest('[data-pick-product]');
    if (pick) {
      var row = pick.closest('[data-stock-line]');
      window.pickAvailability(pick); row.querySelector('[name=product_code]').dispatchEvent(new Event('stock-picked'));
      fill(row, {code: pick.dataset.pickProduct, name: pick.dataset.productName, unit: pick.dataset.unit, stock: pick.dataset.stock});
      update(); return;
    }
    var party = e.target.closest('[data-pick-party]');
    if (party) {
      var box = party.closest('[data-sk-party]'), input = box.querySelector('input[name]');
      input.value = party.dataset.pickParty; input.dispatchEvent(new Event('stock-picked'));
      box.querySelector('[data-party-name]').textContent = party.dataset.pickParty + ' · ' + party.dataset.name;
      box.querySelector('[data-party-info]').textContent = party.dataset.info || '';
      box.querySelector('.product-options').replaceChildren();
      box.dataset.picked = '1'; box.classList.remove('field-invalid'); update(); return;
    }
    var change = e.target.closest('[data-party-change]');
    if (change) { var b = change.closest('[data-sk-party]'), i = b.querySelector('input[name]'); b.dataset.picked = '0'; i.select(); i.focus(); return; }
    var remove = e.target.closest('[data-remove-line]');
    if (remove) { remove.closest('[data-stock-line]').remove(); update(); return; }
    if (e.target.closest('[data-add-stock-line]')) { var r = addRow(); update(); r.querySelector('[name=product_code]').focus(); }
  });
  form.addEventListener('submit', function(e) {
    var primary = form.querySelector('[data-sk-primary]');
    if (primary && e.submitter === primary && primary.classList.contains('is-blocked')) {
      e.preventDefault();
      var note = form.querySelector('[data-sk-missing]'); note.hidden = false; note.textContent = 'Còn thiếu: ' + primary.dataset.missing;
      window.tdToast && window.tdToast('Còn thiếu: ' + primary.dataset.missing, 'bad');
    }
  });
  // Ô "+ Tìm hàng để thêm": kết quả kèm số có thể dùng ở kho đang chọn. Nhập nhanh như đơn bán:
  // ↑↓ chọn, Enter thêm rồi nhảy xuống Số lượng → Đơn giá (nếu có) → về ô tìm; "3*107a" thêm 3 cái; F2 về ô tìm.
  var find = form.querySelector('[data-sk-find]');
  if (find) {
    var q = find.querySelector('[data-sk-q]'), list = find.querySelector('[data-sk-list]'), timer, seq = 0, hl = 0, moved = false, latest = Promise.resolve();
    var typed = function() { var m = /^\s*(\d{1,4})\s*\*\s*(\S.*)$/.exec(q.value); return m ? {count: Number(m[1]) || 1, q: m[2]} : {count: 1, q: q.value}; };
    var choices = function() { return [].slice.call(list.querySelectorAll('[data-sk-add]')); };
    var mark = function() {
      var all = choices(); hl = Math.max(0, Math.min(hl, all.length - 1));
      all.forEach(function(b, i) { var item = b.closest('.qb-item') || b; item.classList.toggle('qb-hl', i === hl); if (i === hl && moved) item.scrollIntoView({block: 'nearest'}); });
    };
    var load = function() {
      var cur = ++seq;
      latest = fetch('/phieu-kho/san-pham?' + new URLSearchParams({q: typed().q, warehouse: wh()}), {credentials: 'same-origin'})
        .then(function(r) { if (!r.ok) throw new Error(); return r.text(); })
        .then(function(html) { if (cur !== seq) return; list.innerHTML = html; hl = 0; moved = false; if (window.tdMarkHits) window.tdMarkHits(list, typed().q); mark(); })
        .catch(function() { if (cur === seq) list.textContent = 'Không tải được kết quả, hãy thử lại.'; });
      return latest;
    };
    var focusQty = function(row) { var input = row.querySelector('[name=qty]'); input.focus(); input.select(); input.scrollIntoView({block: 'nearest'}); };
    var add = function(button, count) {
      var data = {code: button.dataset.code, name: button.dataset.name, unit: button.dataset.unit, stock: button.dataset.stock}, n = count || 1, row;
      var dup = rows().filter(function(r) { return r.querySelector('[name=product_code]').value === data.code; })[0];
      if (dup && kind() !== 'KK' && !dup.querySelector('[name=serial]').value) {
        var input = dup.querySelector('[name=qty]'); input.value = qtyText((num(input.value) || 0) + n); row = dup;
        window.tdToast && window.tdToast(data.name + ': đã có trong phiếu, tăng số lượng lên ' + input.value);
      } else { row = addRow(data); if (n !== 1) row.querySelector('[name=qty]').value = qtyText(n); }
      q.value = ''; clearTimeout(timer); timer = null; list.hidden = true; list.innerHTML = ''; update();
      focusQty(row);
    };
    q.addEventListener('input', function() { list.hidden = false; clearTimeout(timer); timer = setTimeout(function() { timer = null; load(); }, 200); });
    q.addEventListener('focus', function() { if (!list.innerHTML) load(); list.hidden = false; });
    q.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') { list.hidden = true; return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); list.hidden = false; moved = true; hl += e.key === 'ArrowDown' ? 1 : -1; mark(); return; }
      if (e.key !== 'Enter') return;
      e.preventDefault();
      if (!q.value.trim() && !moved) return;
      var want = typed().count;
      // Gõ nhanh rồi Enter ngay (máy quét mã): chờ kết quả của chữ vừa gõ rồi mới thêm.
      var go = function() { var b = choices()[hl]; if (b) add(b, want); else window.tdToast && window.tdToast('Không có mã nào khớp "' + typed().q + '".', 'bad'); };
      if (timer || !list.innerHTML) { clearTimeout(timer); timer = null; load(); }
      latest.then(go);
    });
    list.addEventListener('click', function(e) { var b = e.target.closest('[data-sk-add]'); if (b) add(b, typed().count); });
    document.addEventListener('click', function(e) { if (!find.contains(e.target)) list.hidden = true; });
    // Enter trong dòng: Số lượng → Đơn giá (phiếu nhập, kiểm kê) → về ô tìm. Enter không bao giờ gửi phiếu ở đây.
    lines.addEventListener('keydown', function(e) {
      if (e.key !== 'Enter' || !e.target.matches('input')) return;
      var row = e.target.closest('[data-stock-line]'); if (!row) return;
      e.preventDefault();
      if (e.target.name === 'product_code') return; // ô chọn hàng tự xử lý Enter (base.jinja)
      var cost = row.querySelector('[name=unit_cost]');
      if (e.target.name === 'qty' && cost && !cost.disabled) { cost.focus(); cost.select(); return; }
      if (e.target.name === 'qty' || e.target.name === 'unit_cost') { q.focus(); q.select(); return; }
      focusQty(row);
    });
    document.addEventListener('keydown', function(e) { if (e.key === 'F2') { e.preventDefault(); q.focus(); q.select(); } });
    // Đổi kho: kết quả đang mở phải tính lại số có thể dùng.
    form.addEventListener('change', function(e) { if (e.target.name === 'warehouse_code' && list.innerHTML) list.innerHTML = ''; });
  }
  document.querySelectorAll('[data-confirm-void]').forEach(function(el) {
    el.addEventListener('submit', function(e) { if (!window.confirm('Hủy phiếu đã ghi và đảo các dòng sổ kho?')) e.preventDefault(); });
  });
  panels(); update();
})();
// "Kho khác ▾": ô tìm lọc kho chờ xử lý/NCC và kho thợ (không dấu); chọn xong đóng lại và nút hiện tên kho.
(function() {
  var more = document.querySelector('[data-sk-wh-more]');
  if (!more) return;
  var form = more.closest('form'), label = more.querySelector('[data-sk-wh-more-label]'), q = more.querySelector('[data-sk-wh-q]');
  function plain(t) { return String(t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase(); }
  function opts() { return [].slice.call(more.querySelectorAll('.sk-wh-opt')); }
  function filter() {
    var words = plain(q.value).split(/\s+/).filter(Boolean), shown = 0;
    opts().forEach(function(o) { var t = plain(o.dataset.whText), ok = words.every(function(w) { return t.indexOf(w) >= 0; }); o.hidden = !ok; if (ok) shown++; });
    more.querySelectorAll('[data-wh-group]').forEach(function(g) { g.hidden = !opts().some(function(o) { return !o.hidden && o.dataset.whKind === g.dataset.whGroup; }); });
    more.querySelector('[data-sk-wh-none]').hidden = shown > 0;
  }
  q.addEventListener('input', filter);
  q.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); var first = opts().filter(function(o) { return !o.hidden; })[0]; if (first) { var r = first.querySelector('input'); if (!r.disabled) { r.checked = true; r.dispatchEvent(new Event('change', {bubbles: true})); } } }
    else if (e.key === 'ArrowDown') { e.preventDefault(); var f = opts().filter(function(o) { return !o.hidden; })[0]; if (f) f.querySelector('input').focus(); }
    else if (e.key === 'Escape') { more.open = false; }
  });
  more.addEventListener('toggle', function() { if (more.open) { q.value = ''; filter(); q.focus(); } });
  form.addEventListener('change', function(e) {
    if (e.target.name !== 'warehouse_code') return;
    var inMore = more.contains(e.target);
    label.textContent = (inMore ? e.target.nextElementSibling.textContent : 'Kho khác') + ' ▾';
    label.classList.toggle('on', inMore);
    if (inMore) more.open = false;
  });
  document.addEventListener('click', function(e) { if (more.open && !more.contains(e.target)) more.open = false; });
})();

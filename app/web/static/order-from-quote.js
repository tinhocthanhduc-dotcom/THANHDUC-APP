/* Tạo đơn từ báo giá: chọn kho từng dòng, xem tồn, giao nhận và thanh toán. Máy chủ kiểm lại tất cả khi lưu. */
(function () {
  var form = document.querySelector('[data-order-from-quote]');
  if (!form) return;
  var confirmBtn = form.querySelector('[data-oq-confirm]');
  var hints = {
    TAI_QUAY: 'Khách nhận tại quầy: chốt xong giao luôn, có thể báo thu cùng lúc.',
    KHACH_LAY_SAU: 'Giữ hàng chờ khách đến lấy; nhắc khi quá ngày hẹn.',
    THO_GIAO: 'Điều phối giao cho thợ; thợ giao xong chụp ảnh và thu tiền.',
    VAN_CHUYEN: 'Ghi đơn vị vận chuyển và mã vận đơn khi giao.'
  };
  function fmt(n) { return String(Math.round(n * 1000) / 1000).replace('.', ','); }
  function checked(name) { var r = form.querySelector('[name=' + name + ']:checked'); return r ? r.value : ''; }
  function setCheck(key, ok, text) {
    var li = form.querySelector('[data-oq-check="' + key + '"]');
    if (!li) return;
    li.dataset.ok = ok ? '1' : '0';
    if (text) li.textContent = text;
  }
  function update() {
    var unset = 0, short = 0;
    form.querySelectorAll('[data-oq-line][data-stock]').forEach(function (row) {
      var stock = JSON.parse(row.dataset.stock || '{}'), qty = Number(row.dataset.qty) || 0;
      var pick = row.querySelector('[data-order-warehouse]:checked'), wh = pick ? pick.value : '';
      var cell = row.querySelector('[data-oq-stock]');
      cell.classList.remove('ok', 'no'); row.classList.remove('line-short');
      if (!wh) {
        unset++;
        cell.textContent = Object.keys(stock).map(function (k) { return k + ': ' + fmt(stock[k]); }).join(' · ');
        return;
      }
      // Kho thanh lý (Chờ Xử lý) không có trong bảng tồn của trang: để máy chủ kiểm.
      if (!(wh in stock)) { cell.textContent = 'Máy chủ kiểm khi chốt'; return; }
      var left = Number(stock[wh]) - qty;
      if (left >= 0) { cell.textContent = 'Còn ' + fmt(stock[wh]); cell.classList.add('ok'); }
      else { cell.textContent = 'Thiếu ' + fmt(-left); cell.classList.add('no'); row.classList.add('line-short'); short++; }
    });
    var ful = checked('fulfillment'), needAddr = ful === 'THO_GIAO' || ful === 'VAN_CHUYEN';
    var addr = form.querySelector('[data-ship-address]'), addrText = form.querySelector('[name=delivery_address]').value.trim();
    if (addr) addr.hidden = !needAddr && !addrText;
    var hint = form.querySelector('[data-oq-ship-hint]'); if (hint) hint.textContent = hints[ful] || '';
    var pay = checked('payment_term');
    var debt = form.querySelector('[data-debt-days]'), dep = form.querySelector('[data-deposit-amount]');
    if (debt) debt.hidden = pay !== 'CONG_NO';
    form.querySelectorAll('[data-credit-note]').forEach(function (box) { box.hidden = pay !== 'CONG_NO'; });
    if (dep) dep.hidden = pay !== 'DAT_COC';
    setCheck('wh', !unset, unset ? 'Còn ' + unset + ' dòng hàng chưa chọn kho' : 'Mọi dòng hàng đã có kho');
    setCheck('stock', !short, short ? short + ' dòng thiếu hàng ở kho đã chọn' : 'Đủ tồn khả dụng');
    setCheck('addr', !needAddr || !!addrText, needAddr ? (addrText ? 'Có địa chỉ giao' : 'Thiếu địa chỉ giao') : 'Không cần địa chỉ giao');
    var all = form.querySelectorAll('[data-oq-wh-all]'), first = form.querySelector('[data-oq-line][data-stock] [data-order-warehouse]:checked');
    var same = first && first.value && Array.from(form.querySelectorAll('[data-oq-line][data-stock]')).every(function (row) {
      var p = row.querySelector('[data-order-warehouse]:checked'); return p && p.value === first.value;
    });
    all.forEach(function (b) { b.classList.toggle('on', !!same && b.dataset.oqWhAll === first.value); });
    if (confirmBtn) {
      confirmBtn.disabled = !!(unset || short || (needAddr && !addrText));
      confirmBtn.title = confirmBtn.disabled ? 'Chọn đủ kho, đủ tồn và địa chỉ giao trước khi chốt' : '';
    }
  }
  form.addEventListener('change', update);
  form.addEventListener('input', function (e) { if (e.target.name === 'delivery_address') update(); });
  form.addEventListener('click', function (e) {
    var all = e.target.closest('[data-oq-wh-all]');
    if (all) {
      form.querySelectorAll('[data-oq-line][data-stock]').forEach(function (row) {
        var r = row.querySelector('[data-order-warehouse][value="' + all.dataset.oqWhAll + '"]'); if (r) r.checked = true;
      });
      update(); return;
    }
    var d = e.target.closest('[data-promise-days]');
    if (d) {
      var t = new Date(); t.setDate(t.getDate() + Number(d.dataset.promiseDays));
      form.querySelector('[name=promised_date]').value = t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0');
    }
  });
  update();
})();

// Sửa báo thu (TUẤN duyệt mẫu 12/11/2026): chip "Sai ở đâu?" mở đúng ô, ô "Sau khi sửa" tính trước công nợ và quỹ.
// Chỉ hiển thị; số thật do lõi app.receipt_corrections kiểm và ghi.
(function() {
  var form = document.querySelector('[data-sbt-form]');
  if (!form) return;
  var d = form.dataset;
  var money = function(n) { return String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); };
  var sign = function(n) { return (n > 0 ? '+ ' : n < 0 ? '− ' : '') + money(n); };
  var esc = function(s) { var x = document.createElement('span'); x.textContent = s; return x.innerHTML; };
  var amountInput = form.querySelector('[data-sbt-amount]');
  var dateInput = form.querySelector('[data-sbt-date]');
  var oldAmount = +d.oldAmount;

  function amount() { var v = (amountInput.value || '').replace(/\D/g, ''); return v ? +v : 0; }
  function target() { var t = form.querySelector('input[name=target]:checked'); return t ? {key: t.value, number: t.dataset.number, room: +t.dataset.room} : null; }
  function method() { var m = form.querySelector('input[name=method]:checked'); return m ? m.value : d.oldMethod; }
  function changed(key) {
    if (key === 'amount') return amount() !== oldAmount;
    if (key === 'date') return dateInput.value !== d.oldDate;
    if (key === 'method') return method() !== d.oldMethod;
    if (key === 'customer') return d.customer !== d.oldCustomer;
    var t = target(); return !!t && t.key !== d.oldTarget;
  }
  function show() {
    form.querySelectorAll('[data-sbt-toggle]').forEach(function(box) {
      var key = box.dataset.sbtToggle;
      if (changed(key)) box.checked = true;
      if (key === 'customer' && box.checked) form.querySelector('[data-sbt-toggle=target]').checked = true;
    });
    form.querySelectorAll('[data-sbt-group]').forEach(function(g) {
      var box = form.querySelector('[data-sbt-toggle=' + g.dataset.sbtGroup + ']');
      g.hidden = !(box && box.checked);
    });
  }
  function row(label, value, cls) { return '<div class="sbt-line"><span>' + label + '</span><b class="' + (cls || '') + '">' + value + '</b></div>'; }
  function preview() {
    var a = amount(), t = target(), m = method(), html = '', warn = '';
    var diff = form.querySelector('[data-sbt-diff]');
    if (diff) diff.textContent = a && a !== oldAmount ? ' → ' + (a > oldAmount ? 'thêm ' : 'bớt ') + money(a - oldAmount) : '';
    if (!t || !a) { form.querySelector('[data-sbt-preview]').innerHTML = '<p class="hint">Chọn đơn, phiếu và nhập số tiền để xem trước.</p>'; return; }
    html += '<h3 class="sbt-sub">Công nợ khách</h3>';
    if (t.key === d.oldTarget) {
      html += row(esc(t.number) + ' còn nợ', a === oldAmount ? 'không đổi' : sign(oldAmount - a), a < oldAmount ? 'minus' : a > oldAmount ? 'plus' : '');
    } else {
      html += row(esc(d.oldNumber) + ' nợ lại', '+ ' + money(oldAmount), 'minus');
      html += row(esc(t.number) + ' đã thu', '− ' + money(a), 'plus');
    }
    if (d.customer === d.oldCustomer) html += row('<b>Tổng nợ ' + esc(d.customerName) + '</b>', sign(oldAmount - a));
    else html += row('<b>Nợ ' + esc(d.oldCustomerName) + '</b>', '+ ' + money(oldAmount), 'minus') + row('<b>Nợ ' + esc(d.customerName) + '</b>', '− ' + money(a), 'plus');
    if (a > t.room) warn = t.number + ' chỉ gán được tối đa ' + money(t.room) + ' đ.';
    var oldFund = form.getAttribute('data-fund-' + d.oldMethod.toLowerCase());
    var newFund = form.getAttribute('data-fund-' + m.toLowerCase());
    html += '<h3 class="sbt-sub">Sổ quỹ</h3>';
    html += row('Hủy ' + esc(d.oldPt || 'phiếu thu cũ') + ' · ' + esc(oldFund) + ' (đảo ' + d.today + ')', '− ' + money(oldAmount), 'minus');
    html += row('Phiếu thu mới · ' + esc(newFund), '+ ' + money(a), 'plus');
    if (m === d.oldMethod) {
      html += row('<b>' + esc(newFund) + '</b>', sign(a - oldAmount), a < oldAmount ? 'minus' : a > oldAmount ? 'plus' : '');
      if (a < oldAmount && m === 'TIEN_MAT' && !warn) warn = 'Quỹ tiền mặt sẽ giảm ' + money(oldAmount - a) + ' đ. Kiểm lại tiền trong két trước khi bấm.';
      if (a > oldAmount && !warn) warn = (m === 'TIEN_MAT' ? 'Quỹ tiền mặt' : 'Quỹ ngân hàng') + ' sẽ tăng ' + money(a - oldAmount) + ' đ. Chỉ sửa khi chắc tiền đã có.';
    } else if (!warn) {
      warn = m === 'CHUYEN_KHOAN' ? 'Kiểm sao kê: chỉ sửa khi chắc ' + money(a) + ' đ đã về tài khoản.' : 'Chỉ sửa khi chắc khách trả tiền mặt và tiền đã vào két.';
    }
    form.querySelector('[data-sbt-preview]').innerHTML = html;
    var w = form.querySelector('[data-sbt-warn]');
    w.hidden = !warn; w.textContent = warn;
  }
  form.addEventListener('change', function(e) {
    if (e.target.matches('[data-sbt-toggle]') && !e.target.checked) {
      // Bỏ chọn: trả ô về giá trị cũ để không sửa nhầm.
      var key = e.target.dataset.sbtToggle;
      if (key === 'amount') amountInput.value = money(oldAmount);
      if (key === 'date') dateInput.value = d.oldDate;
      if (key === 'method') { var o = form.querySelector('input[name=method][value=' + d.oldMethod + ']'); if (o) o.checked = true; }
      if (key === 'target') { var t = form.querySelector('input[name=target][value="' + d.oldTarget + '"]'); if (t) t.checked = true; }
      if (key === 'customer' && d.customer !== d.oldCustomer) { location.href = form.action.replace(/\/sua-bao-thu\/(\d+).*/, '/sua-bao-thu/$1'); return; }
    }
    show(); preview();
  });
  form.addEventListener('input', preview);
  show(); preview();
})();

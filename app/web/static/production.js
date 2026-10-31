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
      if (reason) reason.hidden = sum('[data-sx-good]', complete) > 0;
      if (submit) submit.textContent = sum('[data-sx-used]', complete) > 0 ? submit.getAttribute('data-with') : submit.getAttribute('data-without');
    };
    complete.addEventListener('input', sync);
    complete.addEventListener('click', function () { setTimeout(sync, 0); });
    complete.addEventListener('item-picker-refresh', sync);
    sync();
  }
  // SX2: tổng số định làm của các thành phẩm ở form tạo.
  var total = document.querySelector('[data-sx-plan-total]'), form = document.querySelector('[data-sx-form]');
  if (total && form) {
    var tally = function () { total.textContent = sum('[data-sx-plan]', form); };
    form.addEventListener('input', tally);
    form.addEventListener('click', function () { setTimeout(tally, 0); });
    form.addEventListener('item-picker-refresh', tally);
    tally();
  }
  var confirmBox = document.querySelector('[data-sx-confirm] textarea');
  if (confirmBox) confirmBox.focus();
})();

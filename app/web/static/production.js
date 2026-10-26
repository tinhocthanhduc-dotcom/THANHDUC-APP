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
  var good = document.querySelector('[data-sx-good]'), reason = document.querySelector('[data-sx-void]');
  if (good && reason) {
    var sync = function () { reason.hidden = Number(String(good.value || '').replace(',', '.')) > 0; };
    good.addEventListener('input', sync);
    sync();
  }
})();

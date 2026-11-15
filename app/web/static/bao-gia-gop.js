// Báo giá gộp (mẫu duyệt 15/11/2026): cộng tổng máy đã tick; ô hẹn trả chọn nhanh.
(function () {
  var pick = document.querySelector('[data-bgg-pick]');
  if (pick) {
    var sum = pick.querySelector('[data-bgg-sum]');
    var fmt = function (n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '.'); };
    var update = function () {
      var t = 0;
      pick.querySelectorAll('input[data-bgg-total]:checked').forEach(function (c) { t += parseInt(c.dataset.bggTotal, 10) || 0; });
      if (sum) sum.textContent = fmt(t);
    };
    pick.addEventListener('change', update);
  }
  document.querySelectorAll('[data-xg-days] input').forEach(function (r) {
    r.addEventListener('change', function () { var d = r.closest('form').querySelector('[data-xg-date]'); if (d) d.value = r.value; });
  });
  document.querySelectorAll('[data-bgg-all]').forEach(function (b) {
    b.addEventListener('click', function () {
      b.closest('form').querySelectorAll('[data-bgg-row]').forEach(function (row) {
        var r = row.querySelector('input[value="' + row.dataset.bggRow + '"]'); if (r) r.checked = true;
      });
    });
  });
})();

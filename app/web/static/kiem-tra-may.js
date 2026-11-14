// Kiểm tra máy (mẫu M3 14/11/2026): tick phương án là app tự chọn cấp (0 chính → C1, 1 → C2, từ 2 → C3); chọn cấp khác thì hiện ô lý do.
(function () {
  document.querySelectorAll('[data-rq-svc]').forEach(function (box) {
    var caps = box.querySelectorAll('[data-rq-cap]'), reason = box.querySelector('[data-rq-reason]');
    var text = box.querySelector('[data-rq-auto-text]'), touched = false;
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
    }
    if (picked() && picked() !== auto()) touched = true;
    box.addEventListener('change', function (e) {
      if (e.target.matches('[data-rq-cap]')) touched = true;
      sync();
    });
    sync();
  });
})();

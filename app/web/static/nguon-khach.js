// Ô "Mã đơn trên web / sàn" chỉ hiện khi nguồn khách là Web, Shopee, TikTok Shop (TUẤN duyệt 16/11/2026).
(function () {
  function sync(box) {
    var sel = box.querySelector('[name="lead_source"]');
    var web = box.querySelector('[data-lead-web]');
    if (!sel || !web) return;
    var on = (box.getAttribute('data-lead-market') || '').split(',').indexOf(sel.value) >= 0;
    web.hidden = !on;
    var input = web.querySelector('input');
    if (input) input.disabled = !on;
  }
  function init() {
    document.querySelectorAll('[data-lead]').forEach(function (box) {
      sync(box);
      box.addEventListener('change', function (e) { if (e.target.name === 'lead_source') sync(box); });
      var code = box.querySelector('[name="campaign_code"]');
      if (code) code.addEventListener('blur', function () { code.value = code.value.trim().toUpperCase(); });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

// Kiểm trình duyệt thật cho đổi loại dịch vụ (R1, báo cáo kiểm tra lại 25/10/2026).
// Chạy service-kind.js trong Chromium với fetch giả, không cần server:
//   node scripts/kiem_service_kind_browser.js            (dùng app/web/static/service-kind.js)
//   node scripts/kiem_service_kind_browser.js duong/dan/khac.js
// Kết quả cuối: KIEM_TRINH_DUYET_OK hoặc KIEM_TRINH_DUYET_FAIL (mã thoát 1). Cần playwright đã cài sẵn.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const file = process.argv[2] || path.join(__dirname, '..', 'app', 'web', 'static', 'service-kind.js');
  const js = fs.readFileSync(file, 'utf8');
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setContent(`
    <form data-kind-inline action="/dich-vu/1/kind" method="post">
      <input type="hidden" name="csrf" value="c"><input type="hidden" name="version" value="3"><input type="hidden" name="form_key" value="k1">
      <select name="kind"><option value="SUA_TAI_CHO" selected>Sửa tại chỗ</option><option value="MUC_DINH_KY">Mực định kỳ</option></select>
      <span data-kind-status></span>
    </form>
    <form><input name="version" value="3"><input name="amount" value="777.000"></form>`);
  await page.evaluate(() => {
    window.__calls = [];
    window.fetch = (url, opt) => {
      window.__calls.push({ url: String(url), body: String(opt.body), disabledDuring: document.querySelector('select').disabled });
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true, version: 4, form_key: 'k2', kind: 'MUC_DINH_KY', label: 'Mực định kỳ' }) });
    };
  });
  await page.addScriptTag({ content: js });
  await page.selectOption('select', 'MUC_DINH_KY');
  await page.waitForFunction(() => /^(Đã lưu|Không lưu|Mất kết nối)/.test(document.querySelector('[data-kind-status]').textContent), null, { timeout: 5000 });
  const r = await page.evaluate(() => ({
    calls: window.__calls,
    versions: [...document.querySelectorAll('input[name=version]')].map((e) => e.value),
    key: document.querySelector('input[name=form_key]').value,
    amount: document.querySelector('input[name=amount]').value,
    disabledAfter: document.querySelector('select').disabled,
    status: document.querySelector('[data-kind-status]').textContent,
  }));
  await browser.close();
  console.log(JSON.stringify(r));
  const ok = r.calls.length === 1 && /(^|&)kind=MUC_DINH_KY(&|$)/.test(r.calls[0].body) && r.calls[0].disabledDuring
    && !r.disabledAfter && r.versions.join() === '4,4' && r.key === 'k2' && r.amount === '777.000' && r.status.startsWith('Đã lưu loại');
  console.log(ok ? 'KIEM_TRINH_DUYET_OK' : 'KIEM_TRINH_DUYET_FAIL');
  process.exit(ok ? 0 : 1);
})();

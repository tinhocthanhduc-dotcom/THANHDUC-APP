// Màn thợ sửa ở xưởng (mẫu 18/11): cập nhật "Trước khi báo sửa xong" và mở nút vuốt khi đủ báo cáo + ảnh nghiệm thu.
(function () {
  const form = document.getElementById("ws-report");
  if (!form) return;
  const button = document.querySelector('[form="ws-report"][data-tho-primary]');
  const mark = (key, ok) => {
    const li = document.querySelector(`[data-ws-check="${key}"]`);
    if (!li) return;
    li.dataset.ok = ok ? "1" : "0";
    li.textContent = (ok ? "✓ " : "✗ ") + li.textContent.replace(/^[✓✗]\s*/, "");
  };
  function update() {
    const report = Array.from(document.querySelectorAll('[form="ws-report"][name=done]')).some((x) => x.checked)
      || ((document.querySelector('[form="ws-report"][name=fix_extra]') || {}).value || "").trim().length >= 3;
    const photos = document.querySelectorAll('[data-photo-tag="SAU"] [data-saved-photo]').length > 0;
    const uploading = document.querySelector('[data-photo-tag="SAU"] [data-uploading]');
    mark("report", report);
    mark("photo", photos);
    if (button) {
      button.disabled = !(report && photos) || !!uploading;
      if (button.disabled) button.setAttribute("aria-describedby", "ws-checks");
      else button.removeAttribute("aria-describedby");
    }
  }
  // "Lấy từ" (TUẤN 18/11): ô tìm chỉ hiện hàng của kho đang chọn; dòng đã chọn giữ nguyên, ghi rõ kho.
  const box = document.querySelector("[data-ws-materials]");
  function source() {
    if (!box) return;
    const picked = box.querySelector("[data-ws-src]:checked");
    if (!picked) return;
    box.querySelectorAll("[data-ws-wh]").forEach((row) => {
      const qty = Number((row.querySelector("[name=extra_qty]") || {}).value || 0);
      const keep = row.dataset.wsWh === picked.value || !row.hidden || qty > 0;
      if (keep) { row.setAttribute("data-pick-row", ""); }
      else { row.removeAttribute("data-pick-row"); }
    });
    box.dispatchEvent(new Event("item-picker-refresh"));
  }
  if (box) box.addEventListener("change", (e) => { if (e.target.matches("[data-ws-src]")) source(); });
  source();
  document.addEventListener("input", update);
  document.addEventListener("change", update);
  document.addEventListener("tech-photo-change", update);
  update();
})();

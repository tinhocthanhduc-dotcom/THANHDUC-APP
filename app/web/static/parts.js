// Dùng data-live chung; người dùng chọn đúng mã, không đoán theo tên.
// Form [data-part-multi]: mỗi lần chọn thêm một dòng vào danh sách, gửi nhiều mã trong một lần.
document.querySelectorAll('[data-part-multi]').forEach(form => {
  const search = form.querySelector('[data-part-search]');
  const single = form.querySelector('[data-single-qty]');
  // Có JS thì ô tìm chỉ để tìm; mã và số lượng gửi đi nằm ở các dòng đã chọn.
  if (search) search.removeAttribute('name');
  if (single) { single.querySelector('input').removeAttribute('name'); single.hidden = true; }
  if (search && search.value) search.value = '';
  const count = () => {
    const n = form.querySelectorAll('[data-part-line]').length;
    const el = form.querySelector('[data-part-count]'); if (el) el.textContent = n + ' mã';
  };
  form.addEventListener('click', event => {
    const remove = event.target.closest('[data-part-remove]');
    if (remove) { remove.closest('[data-part-line]').remove(); count(); }
  });
  form.addEventListener('part-added', count);
  // Enter ở Số lượng: về ô tìm để thêm mã tiếp, không gửi form.
  form.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || !event.target.closest('[data-part-line]') || !event.target.matches('input')) return;
    event.preventDefault();
    if (search) { search.focus(); search.select(); }
  });
});
document.addEventListener('click', event => {
  const button = event.target.closest('[data-part-pick]');
  if (!button) return;
  const picker = button.closest('[data-part-picker]');
  if (!picker) return;
  const multi = picker.closest('[data-part-multi]');
  if (multi) {
    const lines = multi.querySelector('[data-part-lines]');
    const code = button.dataset.partPick;
    const codeOf = row => row.querySelector('[data-part-code]') || row.querySelector('[name=product_code]');
    const existing = Array.from(lines.querySelectorAll('[data-part-line]')).find(row => codeOf(row).value === code);
    let row = existing;
    if (existing) {
      const q = existing.querySelector('[data-part-qty]') || existing.querySelector('[name=qty]'); q.value = String((Number(q.value.replace(',', '.')) || 0) + 1);
    } else {
      row = multi.querySelector('[data-part-line-template]').content.firstElementChild.cloneNode(true);
      codeOf(row).value = code;
      row.querySelector('strong').textContent = code;
      row.querySelector('small').textContent = button.dataset.name + (button.dataset.stock ? ' · ' + button.dataset.stock : '');
      lines.appendChild(row);
    }
    multi.dispatchEvent(new Event('part-added'));
    if (window.tdToast) window.tdToast('Đã thêm ' + code);
    const search = picker.querySelector('[data-part-search]');
    if (search) search.value = '';
    picker.querySelector('.part-options').replaceChildren();
    // Chọn bằng bàn phím (Enter): nhảy xuống Số lượng của dòng đó như đơn bán; bấm tay thì giữ ô tìm.
    const qty = event.detail === 0 && (row.querySelector('[data-part-qty]') || row.querySelector('[name=qty]'));
    if (qty && qty.type !== 'hidden') { qty.focus(); qty.select(); } else if (search) search.focus();
    return;
  }
  picker.querySelector('[name=product_code]').value = button.dataset.partPick;
  picker.querySelector('[data-part-name]').textContent = button.dataset.name;
  picker.querySelector('.part-options').replaceChildren();
});
// Mẫu MucGiaoTho (TUẤN duyệt 05/11/2026): bảng linh kiện/mực của phiếu như dòng đơn bán.
// Đếm thay đổi chưa lưu, đánh số lại, gạch dòng bấm ✕; nút Lưu danh sách chỉ sáng khi có thay đổi.
document.querySelectorAll('[data-part-list]').forEach(form => {
  const save = form.querySelector('[data-pl-save]');
  const dirtyText = form.querySelector('[data-pl-dirty]');
  const reset = form.querySelector('[data-pl-reset]');
  const countText = form.querySelector('[data-pl-count]');
  const unit = countText && /hộp/.test(countText.textContent) ? 'hộp' : 'cái';
  const num = text => Number(String(text || '').replace(',', '.')) || 0;
  const update = () => {
    let dirty = 0, codes = 0, total = 0, n = 0;
    form.querySelectorAll('.pl-row').forEach(row => {
      n += 1; const no = row.querySelector('.pl-no'); if (no) no.textContent = n;
      const qty = row.querySelector('[data-part-qty]');
      const box = row.querySelector('[data-pl-remove]');
      const removed = !!(box && box.checked);
      row.classList.toggle('pl-del', removed);
      const changed = row.classList.contains('pl-new') || removed || (qty && row.dataset.plOrig !== undefined && num(qty.value) !== num(row.dataset.plOrig));
      row.classList.toggle('pl-chg', !!changed && !removed && !row.classList.contains('pl-new'));
      if (changed) dirty += 1;
      if (!removed) { codes += 1; total += qty ? num(qty.value) : num((row.querySelector('.pl-fixed') || {}).textContent?.replace('×', '')); }
    });
    if (countText) countText.textContent = codes + ' mã · ' + String(Math.round(total * 1000) / 1000).replace('.', ',') + ' ' + unit;
    if (save) save.disabled = dirty === 0;
    if (reset) reset.hidden = dirty === 0;
    if (dirtyText) { dirtyText.textContent = dirty ? dirty + ' thay đổi chưa lưu' : 'Đã lưu'; dirtyText.classList.toggle('pl-dirty', dirty > 0); }
  };
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  form.addEventListener('part-added', () => {
    // Chọn lại mã đang gạch xóa thì bỏ gạch (cộng số lượng vào dòng đó).
    form.querySelectorAll('[data-pl-remove]:checked').forEach(box => {
      const row = box.closest('.pl-row'), qty = row.querySelector('[data-part-qty]');
      if (qty && num(qty.value) !== num(row.dataset.plOrig)) box.checked = false;
    });
    const empty = form.querySelector('[data-pl-empty]'); if (empty) empty.hidden = true;
    update();
  });
  form.addEventListener('click', event => { if (event.target.closest('[data-part-remove]')) setTimeout(update, 0); });
  update();
});

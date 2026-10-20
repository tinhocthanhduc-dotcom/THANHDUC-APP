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
    const existing = Array.from(lines.querySelectorAll('[data-part-line]')).find(row => row.querySelector('[name=product_code]').value === code);
    if (existing) {
      const q = existing.querySelector('[name=qty]'); q.value = String((Number(q.value.replace(',', '.')) || 0) + 1);
    } else {
      const row = multi.querySelector('[data-part-line-template]').content.firstElementChild.cloneNode(true);
      row.querySelector('[name=product_code]').value = code;
      row.querySelector('strong').textContent = code;
      row.querySelector('small').textContent = button.dataset.name;
      lines.appendChild(row);
    }
    multi.dispatchEvent(new Event('part-added'));
    if (window.tdToast) window.tdToast('Đã thêm ' + code);
    const search = picker.querySelector('[data-part-search]');
    if (search) { search.value = ''; search.focus(); }
    picker.querySelector('.part-options').replaceChildren();
    return;
  }
  picker.querySelector('[name=product_code]').value = button.dataset.partPick;
  picker.querySelector('[data-part-name]').textContent = button.dataset.name;
  picker.querySelector('.part-options').replaceChildren();
});

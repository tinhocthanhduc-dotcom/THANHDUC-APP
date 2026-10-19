// Dùng data-live chung; người dùng chọn đúng mã, không đoán theo tên.
document.addEventListener('click', event => {
  const button = event.target.closest('[data-part-pick]');
  if (!button) return;
  const picker = button.closest('[data-part-picker]');
  if (!picker) return;
  picker.querySelector('[name=product_code]').value = button.dataset.partPick;
  picker.querySelector('[data-part-name]').textContent = button.dataset.name;
  picker.querySelector('.part-options').replaceChildren();
});

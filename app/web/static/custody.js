document.querySelectorAll('[data-custody-handover]').forEach(form => {
  function update() {
    const location = form.querySelector('[name=location]:checked')?.value;
    ['holder_id','partner_name','expected_back'].forEach(name => {
      const input = form.querySelector(`[name=${name}]`);
      input.disabled = name === 'holder_id' ? location !== 'THO_GIU' : location !== 'DOI_TAC';
      input.required = !input.disabled && name !== 'expected_back';
      input.closest('label').hidden = input.disabled;
    });
  }
  form.addEventListener('change', update);
  update();
});

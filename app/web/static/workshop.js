(function () {
  document.querySelectorAll('[data-workshop-materials]').forEach(function(box) {
    box.addEventListener('click',function(e) {
      const button=e.target.closest('[data-workshop-step]');if(!button)return;
      const input=button.parentElement.querySelector('[name=material_qty]');
      input.value=String(Math.max(0,Math.min(Number(input.dataset.held),Number(input.value.replace(',','.'))+Number(button.dataset.workshopStep))));
      input.dispatchEvent(new Event('input',{bubbles:true}));
    });
  });
  document.querySelectorAll('#workshop-bring,[data-workshop-return]').forEach(function(form) {
    form.addEventListener('change',function(e) {
      if(e.target.name==='accessories' && e.target.checked) {
        form.querySelectorAll('[name=accessories]').forEach(function(input) {
          if(e.target.value==='KHONG_CO' ? input!==e.target : input.value==='KHONG_CO')input.checked=false;
        });
      }
    });
  });
  const form=document.querySelector('[data-workshop-return]');if(!form)return;
  function update() {
    const mode=form.querySelector('[name=collection_mode]:checked')?.value || '';
    const partial=form.querySelector('[name=partial_amount]');partial.required=mode==='PARTIAL';
    form.querySelector('[data-return-partial]').hidden=mode!=='PARTIAL';
    form.querySelector('[data-return-method]').hidden=!['FULL','PARTIAL'].includes(mode);
    const returned=form.dataset.returned==='1';
    const photo=!!document.querySelector('[data-photo-tag=TRA_MAY] [data-saved-photo]') && !document.querySelector('[data-photo-tag=TRA_MAY] [data-uploading]');
    const accessories=returned || !!form.querySelector('[name=accessories]:checked');
    const due=Number(form.elements.customer_amount.value);
    const collected=Number(partial.value.replace(/\./g,''));
    const record=form.dataset.requiresRecord!=='1' || !!document.querySelector('[data-photo-tag=BIEN_BAN] [data-saved-photo]');
    const ready=record && (returned || (form.dataset.holder==='1' && photo)) && accessories && (mode!=='PARTIAL' || (collected>0 && collected<due));
    const button=document.querySelector('[data-tho-primary]');if(button)button.disabled=!ready;
  }
  form.addEventListener('input',update);form.addEventListener('change',update);
  document.addEventListener('tech-photo-change',update);update();
})();

(function () {
  document.querySelectorAll('[data-workshop-handover]').forEach(function(form) {
    const partner=form.querySelector('[data-workshop-partner]');
    function update() { const chosen=form.querySelector('[name=location]:checked'); partner.hidden=partner.disabled=!(chosen && chosen.value==='DOI_TAC'); }
    form.addEventListener('change',update);update();
  });
})();

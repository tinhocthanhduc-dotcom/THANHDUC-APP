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
    const ready=(returned || (form.dataset.holder==='1' && photo)) && accessories && (mode!=='PARTIAL' || (collected>0 && collected<due));
    const button=document.querySelector('[data-tho-primary]');if(button)button.disabled=!ready;
  }
  form.addEventListener('input',update);form.addEventListener('change',update);
  document.addEventListener('tech-photo-change',update);update();
})();

(function () {
  document.querySelectorAll('[data-workshop-handover]').forEach(function(form) {
    const partner=form.querySelector('[data-workshop-partner]');
    function update() { const chosen=form.querySelector('[name=location]:checked'); const off=!(chosen && chosen.value==='DOI_TAC'); partner.hidden=off; partner.querySelectorAll('input').forEach(function(input){ input.disabled=off; }); }
    form.addEventListener('change',update);update();
  });
})();

(function () {
  // Khung sửa ở xưởng của điều phối: nút ngày hẹn trả điền ô ngày; chỉ hiện ô lý do / phí khi cần.
  document.querySelectorAll('[data-xg-panel]').forEach(function(panel) {
    const date=panel.querySelector('[data-xg-date]');
    panel.addEventListener('change',function(e) {
      if(e.target.name==='return_pick' && date)date.value=e.target.value;
      if(e.target===date)panel.querySelectorAll('[name=return_pick]').forEach(function(r){ r.checked=r.value===date.value; });
      update();
    });
    function show(box,on) { if(!box)return; box.hidden=!on; box.querySelectorAll('input').forEach(function(input){ input.disabled=!on; }); }
    function update() {
      const repaired=panel.querySelector('[data-xg-repaired]');
      if(repaired)show(repaired.querySelector('[data-xg-free-reason]'),repaired.querySelector('[name=mode]:checked')?.value==='FREE');
      const decline=panel.querySelector('[data-xg-decline]');
      if(decline) {
        const free=decline.querySelector('[name=mode]:checked')?.value==='FREE';
        show(decline.querySelector('[data-xg-fee]'),!free);show(decline.querySelector('[data-xg-decline-reason]'),free);
      }
    }
    update();
  });
})();

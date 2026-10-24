document.querySelectorAll('[data-cash-form],[data-correction-form]').forEach(function(form) {
  var opening=form.querySelector('[data-cash-opening]'), category=form.querySelector('[data-cash-category]');
  function toggle() {
    if (!opening || !category) return;
    category.hidden=opening.checked;
    category.querySelectorAll('input').forEach(function(input) { input.disabled=opening.checked; input.required=!opening.checked; });
  }
  if (opening) { opening.addEventListener('change',function(){ toggle(); update(); }); toggle(); }
  // Đợt 3: chọn một trong ba đối tượng; ô của đối tượng khác bị khóa nên không gửi lên.
  function party() {
    var on=form.querySelector('[data-tc-party]:checked'); if (!on) return;
    form.querySelectorAll('[data-party-only]').forEach(function(box) {
      var show=box.dataset.partyOnly===on.value; box.hidden=!show;
      box.querySelectorAll('input').forEach(function(input){ input.disabled=!show; });
    });
  }
  function digits(text) { var d=String(text||'').replace(/\D/g,''); return d==='' ? 0 : Number(d); }
  function update() {
    var checks=form.querySelector('[data-tc-checks]'); if (!checks) return;
    var amount=form.querySelector('[name=amount]'), fund=form.querySelector('[name=fund_code]:checked'), to=form.querySelector('[name=to_fund_code]:checked');
    var cat=form.querySelector('[name=category_code]');
    var ok={amount: digits(amount.value)>0, fund: !!fund, to_fund: !!to && (!fund || to.value!==fund.value), category: !cat || cat.disabled || !!cat.value.trim()};
    var missing=[];
    checks.querySelectorAll('[data-check]').forEach(function(li) {
      var good=ok[li.dataset.check]; li.dataset.ok=good ? '1' : '0';
      li.textContent=(good ? '✓ ' : '✕ ')+li.textContent.replace(/^[✓✕] /,'');
      if (!good) missing.push(li.textContent.slice(2));
    });
    form.querySelector('[data-tc-total]').textContent=amount.value || '0';
    var primary=form.querySelector('[data-tc-primary]');
    primary.classList.toggle('is-blocked',missing.length>0); primary.setAttribute('aria-disabled',missing.length ? 'true' : 'false');
    primary.dataset.missing=missing.join(' · ');
  }
  form.addEventListener('change',function(event) { if (event.target.matches('[data-tc-party]')) party(); update(); });
  form.addEventListener('input',function(event) {
    var box=event.target.closest('[data-tc-pick]'); if (box && event.target.name) box.dataset.picked='0';
    update();
  });
  form.addEventListener('click',function(event) {
    var day=event.target.closest('[data-tc-day]');
    if (day) {
      var d=new Date(); d.setDate(d.getDate()+Number(day.dataset.tcDay));
      form.querySelector('[data-tc-date]').value=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
      return;
    }
    var change=event.target.closest('[data-pick-change]');
    if (change) { var b=change.closest('[data-tc-pick]'), i=b.querySelector('input[name]'); b.dataset.picked='0'; i.select(); i.focus(); return; }
    var button=event.target.closest('[data-cash-pick]');
    if (!button) return;
    var picker=button.closest('[data-picker]'),input=picker.querySelector('input');
    input.value=button.dataset.cashPick;input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('.cash-options').replaceChildren();
    if (picker.hasAttribute('data-tc-pick')) {
      picker.querySelector('[data-pick-name]').textContent=button.dataset.cashPick+' · '+button.dataset.name;
      picker.dataset.picked='1'; picker.classList.remove('field-invalid');
      var err=picker.querySelector('.field-error'); if (err) err.remove();
    }
    if (input.name==='customer_code' || input.name==='supplier_code') {
      form.querySelector('[name=partner_name]').value=button.dataset.name;
      var other=form.querySelector('[name="'+(input.name==='customer_code'?'supplier_code':'customer_code')+'"]');
      if (other) { other.value='';other.dispatchEvent(new Event('stock-picked'));
        other.closest('[data-picker]').querySelector('.cash-options').replaceChildren(); }
    }
    update();
    if (!picker.hasAttribute('data-tc-pick')) input.focus();
  });
  form.addEventListener('submit',function(e) {
    var primary=form.querySelector('[data-tc-primary]');
    if (primary && e.submitter===primary && primary.classList.contains('is-blocked')) {
      e.preventDefault();
      var note=form.querySelector('[data-tc-missing]'); note.hidden=false; note.textContent='Còn thiếu: '+primary.dataset.missing;
      window.tdToast && window.tdToast('Còn thiếu: '+primary.dataset.missing,'bad');
    }
  });
  party(); update();
});

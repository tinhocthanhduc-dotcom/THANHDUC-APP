(function () {
  document.querySelectorAll('[data-warranty-picker]').forEach(function (picker) {
    var form=picker.closest('form'), query=picker.querySelector('[data-warranty-query]'), selected=picker.querySelector('[name=warranty_of_id]');
    var options=picker.querySelector('[data-warranty-options]'), timer, sequence=0;
    function load() {
      var current=++sequence;
      var customer=form.querySelector('[name=customer_code]'), serial=form.querySelector('select[name=serial]')||form.querySelector('[name=serial]:checked');
      var params=new URLSearchParams({q:query.value,customer_code:customer?customer.value:picker.dataset.customer,serial:serial?serial.value:picker.dataset.serial});
      fetch('/dich-vu/tim-bao-hanh?'+params,{credentials:'same-origin'}).then(function(r){if(!r.ok)throw new Error();return r.text();})
        .then(function(html){if(current===sequence)options.innerHTML=html;}).catch(function(){if(current===sequence)options.textContent='Không tải được phiếu trước, hãy thử lại.';});
    }
    query.addEventListener('input',function(){sequence++;clearTimeout(timer);selected.value='';picker.querySelector('[data-warranty-selected]').textContent='Chưa chọn phiếu trước';timer=setTimeout(load,250);});
    query.addEventListener('focus',load);
    picker.addEventListener('click',function(event){
      var button=event.target.closest('[data-warranty-ticket]');
      if(button){sequence++;clearTimeout(timer);selected.value=button.dataset.warrantyTicket;query.value=button.dataset.label;picker.querySelector('[data-warranty-selected]').textContent=button.textContent;options.replaceChildren();}
      if(event.target.closest('[data-warranty-clear]')){sequence++;clearTimeout(timer);selected.value='';query.value='';options.replaceChildren();picker.querySelector('[data-warranty-selected]').textContent='Chưa chọn phiếu trước';}
    });
    var section=picker.closest('[data-warranty-section]');
    if(section){
      function toggle(){var kind=form.querySelector('select[name=kind]')||form.querySelector('[name=kind]:checked');section.hidden=!kind||kind.value!=='BAO_HANH';selected.disabled=section.hidden;if(!section.hidden)load();}
      form.querySelectorAll('[name=kind]').forEach(function(input){input.addEventListener('change',toggle);});
      function reset(){selected.value='';query.value='';sequence++;clearTimeout(timer);options.replaceChildren();picker.querySelector('[data-warranty-selected]').textContent='Chưa chọn phiếu trước';if(!section.hidden)load();}
      form.addEventListener('change',function(event){if(event.target.matches('[name=serial]'))reset();});
      var customer=form.querySelector('[name=customer_code]');if(customer){customer.addEventListener('input',reset);form.addEventListener('click',function(event){if(event.target.closest('[data-service-customer]'))reset();});}
      toggle();
    }
  });
})();

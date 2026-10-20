(function() {
  document.querySelectorAll('[data-appointment-picker]').forEach(function(picker) {
    var options=picker.querySelector('[data-appointment-options]'), summary=picker.querySelector('[data-appointment-summary]'), time=picker.querySelector('[name=appointment_at]');
    function update(collapse) {
      var selected=picker.querySelector('[name=appointment_choice]:checked');
      var value=selected ? selected.value : '';
      picker.querySelector('[data-appointment-time]').hidden=value!=='GIO';time.disabled=value!=='GIO';
      var text='';
      if (value.indexOf(':')>=0) {
        var parts=value.split(':'), day=parts[1].split('-');
        text=(parts[0]==='SANG'?'Sáng ':'Chiều ')+day[2]+'/'+day[1];
      } else if (value==='GIO' && time.value) text=time.value.replace('T',' ');
      else if (value==='LAN_SAU') text='Lần sau (chưa có giờ)';
      if (collapse && text) { summary.hidden=false;options.hidden=true;summary.querySelector('strong').textContent=text; }
      var returns=picker.form ? picker.form.querySelector('[data-return-fields]') : picker.closest('form').querySelector('[data-return-fields]');
      var submit=picker.closest('form').querySelector('[data-appointment-submit]');
      if (submit) submit.textContent=value==='LAN_SAU'?'Đóng phiếu · Hẹn lần sau':submit.dataset.label;
      if (returns) {
        returns.hidden=value!=='LAN_SAU';
        returns.querySelectorAll('input,select').forEach(function(input){input.disabled=value!=='LAN_SAU';});
      }
    }
    picker.addEventListener('change',function(){update(true);});
    picker.querySelector('[data-appointment-edit]').addEventListener('click',function(){summary.hidden=true;options.hidden=false;});
    update(true);
  });
})();

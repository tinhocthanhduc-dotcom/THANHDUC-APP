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
      if (collapse && text) { summary.hidden=false;options.hidden=true;summary.querySelector('strong').textContent=text; }
    }
    picker.addEventListener('change',function(){update(true);});
    picker.querySelector('[data-appointment-edit]').addEventListener('click',function(){summary.hidden=true;options.hidden=false;});
    update(true);
  });
})();

(function(){
 var form=document.querySelector('[data-attendance-punch]');if(!form)return;
 var ready=false,pending=false;
 form.addEventListener('submit',function(event){
  if(ready)return;
  event.preventDefault();event.stopPropagation();if(pending)return;pending=true;
  var button=event.submitter||form.querySelector('button'),label=button.textContent;
  button.disabled=true;button.textContent='Đang chấm công…';
  function send(position){
   if(ready)return;ready=true;
   if(position){form.elements.lat.value=position.coords.latitude;form.elements.lng.value=position.coords.longitude;form.elements.accuracy.value=Math.ceil(position.coords.accuracy);}
   // Submit after the original submit event has finished (also without a GPS API).
   setTimeout(function(){button.disabled=false;button.textContent=label;form.requestSubmit(button);},0);
  }
  if(!navigator.geolocation){send();return;}
  form.querySelector('[data-location-status]').textContent='Đang lấy vị trí (tối đa 5 giây)…';
  var timer=setTimeout(function(){send();},5000);
  try { navigator.geolocation.getCurrentPosition(function(p){clearTimeout(timer);send(p);},function(){clearTimeout(timer);send();},{timeout:5000,maximumAge:0,enableHighAccuracy:false}); }
  catch(error){clearTimeout(timer);send();}
 });
})();

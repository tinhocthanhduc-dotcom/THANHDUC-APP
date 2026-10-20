(function(){
 document.querySelectorAll('[data-account-confirm]').forEach(form=>form.addEventListener('submit',event=>{if(!confirm(form.dataset.accountConfirm))event.preventDefault();}));
 const secret=document.querySelector('[data-temp-password]'),copy=document.querySelector('[data-copy-password]');
 if(copy&&secret){copy.addEventListener('click',async()=>{try{if(navigator.clipboard)await navigator.clipboard.writeText(secret.value);else{secret.select();if(!document.execCommand('copy'))throw new Error();}copy.textContent='Đã sao chép';}catch(_){secret.select();copy.textContent='Chọn mật khẩu để sao chép';}});window.addEventListener('pagehide',()=>{secret.value='';copy.disabled=true;});}
})();

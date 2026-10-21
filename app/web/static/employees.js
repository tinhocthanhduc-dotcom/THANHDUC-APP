(function(){
 document.addEventListener('input',function(event){
  if(event.target.name!=='account_query')return;
  var picker=event.target.closest('[data-employee-picker]');if(!picker)return;
  picker.querySelector('[name=user_id]').value='';
  picker.querySelector('[data-employee-selected]').textContent='Chưa chọn tài khoản';
 });
 document.addEventListener('click',function(event){
  var button=event.target.closest('[data-employee-user]');if(!button)return;
  var picker=button.closest('[data-employee-picker]');
  picker.querySelector('[name=user_id]').value=button.dataset.employeeUser;
  picker.querySelector('[data-employee-selected]').textContent=button.dataset.employeeName;
  var input=picker.querySelector('[name=account_query]');input.value=button.dataset.employeeName;
  input.dispatchEvent(new Event('stock-picked'));
  picker.querySelector('.employee-options').replaceChildren();
 });
})();

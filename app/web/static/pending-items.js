'use strict';
document.addEventListener('click',event=>{
 const button=event.target.closest('[data-pending-product]');if(!button)return;
 const picker=button.closest('[data-picker]');picker.querySelector('[name=product_code]').value=button.dataset.pendingProduct;
 picker.querySelector('[name=product_query]').value=button.dataset.pendingProduct+' · '+button.dataset.pendingName;
 picker.querySelector('.picker-options').replaceChildren();
});
document.querySelectorAll('[name=product_query]').forEach(input=>input.addEventListener('input',()=>{input.closest('[data-picker]').querySelector('[name=product_code]').value='';}));
document.querySelectorAll('[data-pending-supplier]').forEach(label=>{
 const form=label.closest('form');const update=()=>{label.hidden=form.querySelector('[name=group_code]:checked')?.value!=='CHO_BH_NCC';};
 form.querySelectorAll('[name=group_code]').forEach(input=>input.addEventListener('change',update));update();
});
document.querySelectorAll('[data-pending-qty]').forEach(input=>input.addEventListener('input',()=>{
 const parts=input.value.replace(/[^\d,]/g,'').split(',');const whole=parts[0].replace(/^0+(?=\d)/,'').replace(/\B(?=(\d{3})+(?!\d))/g,'.');
 input.value=whole+(parts.length>1?','+parts.slice(1).join('').slice(0,3):'');
}));

document.addEventListener('click',event=>{
 const button=event.target.closest('[data-pending-supplier-code]');if(!button)return;
 const scope=button.closest('[data-picker]');scope.querySelector('[name=supplier_code]').value=button.dataset.pendingSupplierCode;
 const input=scope.querySelector('[name=supplier_query]');input.value=button.dataset.pendingSupplierCode+' · '+button.dataset.pendingSupplierName;input.dispatchEvent(new Event('stock-picked'));scope.querySelector('.picker-options').replaceChildren();
});
document.querySelectorAll('[name=supplier_query]').forEach(input=>input.addEventListener('input',()=>{input.closest('[data-picker]').querySelector('[name=supplier_code]').value='';}));
document.querySelectorAll('[data-disposition-panel]').forEach(panel=>panel.addEventListener('toggle',()=>{if(panel.open)document.querySelectorAll('[data-disposition-panel]').forEach(other=>{if(other!==panel)other.open=false;});}));

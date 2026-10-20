document.addEventListener('click',function(event){
  var button=event.target.closest('[data-recovered-product]');if(!button)return;
  var picker=button.closest('[data-picker]'),input=picker.querySelector('[name=product_code]');
  input.value=button.dataset.recoveredProduct;input.dispatchEvent(new Event('stock-picked'));picker.querySelector('.recovered-options').replaceChildren();
});

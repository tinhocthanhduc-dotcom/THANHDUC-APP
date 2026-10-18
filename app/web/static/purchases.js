(function() {
  var form=document.querySelector('[data-purchase-form]'); if (!form || form.dataset.purchaseEditable!=='1') return;
  var dirty=form.dataset.purchaseUnsaved==='1';
  form.addEventListener('input',function(){ dirty=true; });
  form.addEventListener('change',function(){ dirty=true; });
  function reindex() {
    form.querySelectorAll('[data-purchase-line]').forEach(function(row,i) {
      row.querySelectorAll('[data-purchase-vat]').forEach(function(input){ input.name='vat_'+i; });
      row.querySelectorAll('[data-purchase-warehouse]').forEach(function(input){ input.name='warehouse_'+i; });
    });
  }
  form.addEventListener('click',function(event) {
    if (event.target.closest('[data-purchase-add]')) {
      var row=document.querySelector('#purchase-line-template').content.firstElementChild.cloneNode(true);
      form.querySelector('[data-purchase-lines]').appendChild(row);reindex();
      row.querySelectorAll('[data-live]').forEach(window.bindLive);row.querySelectorAll('[data-money]').forEach(window.bindMoney);
      dirty=true;row.querySelector('input').focus();return;
    }
    var remove=event.target.closest('[data-purchase-remove]');
    if (remove) {remove.closest('[data-purchase-line]').remove();reindex();dirty=true;return;}
    var pick=event.target.closest('[data-purchase-pick]');if (!pick) return;
    var picker=pick.closest('[data-picker]'),input=picker.querySelector('input'),row=pick.closest('[data-purchase-line]');input.value=pick.dataset.purchasePick;input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('.product-options').replaceChildren();
    if (pick.dataset.kind==='supplier') form.querySelector('[data-purchase-supplier]').textContent=pick.dataset.name;
    else {
      row.querySelector('[data-purchase-name]').textContent=pick.dataset.name;
      row.querySelector('[name=unit_cost]').value=pick.dataset.cost.replace(/\B(?=(\d{3})+(?!\d))/g,'.');
    }
    dirty=true;
  });
  var order=document.querySelector('[data-purchase-order]');
  if (order) order.addEventListener('submit',function(event){if(dirty){event.preventDefault();var warning=form.querySelector('[data-purchase-warning]');warning.hidden=false;warning.textContent='Có thay đổi chưa lưu. Bấm Lưu đơn mua trước khi Đặt hàng.';warning.scrollIntoView({block:'center'});}});
})();

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
// Khuôn 5: màn nhận hàng. Nhận đủ / về 0 và dòng tóm tắt trên nút ghi sổ.
(function() {
  var form=document.querySelector('[data-purchase-receive]'); if (!form) return;
  var inputs=Array.prototype.slice.call(form.querySelectorAll('[data-recv-left]')), summary=form.querySelector('[data-recv-summary]');
  function num(text){ return Number(String(text||'').replace(',','.')); }
  function update() {
    var lines=0, over=0;
    inputs.forEach(function(input){
      var v=num(input.value), bad=isNaN(v)||v<0||v>num(input.dataset.recvLeft);
      input.closest('[data-recv-line]').classList.toggle('field-invalid',bad);
      if (bad) over++; else if (v>0) lines++;
    });
    if (summary) summary.textContent=over?over+' dòng có số lượng sai hoặc vượt số còn lại.':(lines?'Ghi sổ phiếu nhập cho '+lines+' dòng.':'Chưa nhập số lượng nào.');
  }
  form.addEventListener('input',update);
  form.addEventListener('click',function(event){
    var fill=event.target.closest('[data-recv-fill]'), zero=event.target.closest('[data-recv-zero]'); if (!fill&&!zero) return;
    inputs.forEach(function(input){ input.value=fill?input.dataset.recvLeft:'0'; });update();
  });
  update();
})();

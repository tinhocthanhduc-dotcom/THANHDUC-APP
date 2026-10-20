(function() {
  var form = document.querySelector('[data-quote-form]');
  if (!form) return;
  var editable = form.dataset.quoteEditable === '1', dirty = editable && form.dataset.quoteUnsaved === '1';
  function changed() { if (editable) dirty = true; }
  form.addEventListener('input', changed); form.addEventListener('change', changed);
  function reindex() {
    form.querySelectorAll('[data-quote-line]').forEach(function(row,index) {
      row.querySelectorAll('[data-quote-vat]').forEach(function(input) { input.name='vat_'+index; });
    });
  }
  form.addEventListener('click',function(event) {
    if (!editable) return;
    var button=event.target.closest('[data-quote-add]');
    if (button) {
      var row=document.querySelector('#quote-line-template').content.firstElementChild.cloneNode(true);
      form.querySelector('[data-quote-lines]').appendChild(row); row.classList.add('line-open'); reindex();
      row.querySelectorAll('[data-live]').forEach(window.bindLive);
      row.querySelectorAll('[data-money]').forEach(window.bindMoney);
      changed(); row.querySelector('input').focus(); return;
    }
    button=event.target.closest('[data-quote-remove]');
    if (button) { button.closest('[data-quote-line]').remove(); reindex(); changed(); return; }
    button=event.target.closest('[data-quote-pick]');
    if (!button) return;
    var picker=button.closest('[data-picker]'), input=picker.querySelector('input'), row=button.closest('[data-quote-line]');
    input.value=button.dataset.quotePick; input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('.product-options').replaceChildren();
    if (button.dataset.kind==='customer') {
      form.querySelector('[data-quote-customer-name]').textContent=button.dataset.name;
    } else {
      row.querySelector('[data-quote-product-name]').textContent=button.dataset.name;
      row.querySelector('[name=description]').value=button.dataset.name;
      row.querySelector('[name=unit]').value=button.dataset.unit;
      var price=row.querySelector('[name=unit_price]');
      if (!price.dataset.userTyped) price.value=(button.dataset.lastPrice || button.dataset.price).replace(/\B(?=(\d{3})+(?!\d))/g,'.');
      row.querySelector('[data-last-price-hint]').textContent=button.dataset.lastPrice ? 'Giá lần trước '+button.dataset.lastDate+': '+button.dataset.lastPrice.replace(/\B(?=(\d{3})+(?!\d))/g,'.') : '';
    }
    changed();
  });
  var send=document.querySelector('[data-quote-send]');
  if (send) send.addEventListener('submit',function(event) {
    if (!dirty) return;
    event.preventDefault();
    var warning=form.querySelector('[data-quote-warning]'); warning.hidden=false;
    warning.textContent='Có thay đổi chưa lưu. Bấm Lưu báo giá trước khi Gửi.';
    warning.scrollIntoView({block:'center'});
  });
})();

(function() {
  document.querySelectorAll('[data-order-filter]').forEach(function(filter) {
    filter.querySelectorAll('input[type=date]').forEach(function(input) {
      input.addEventListener('change',function() { filter.querySelector('[data-live]').dispatchEvent(new Event('input')); });
    });
  });
  document.querySelectorAll('[data-order-cancel]').forEach(function(cancel) {
    cancel.addEventListener('submit',function(event) {
      if (!window.confirm('Hủy sẽ trả hàng về kho. Bạn có chắc chắn muốn hủy đơn?')) event.preventDefault();
    });
  });
  var form=document.querySelector('[data-order-form]');
  if (!form) return;
  var editable=form.dataset.orderEditable==='1', dirty=editable && form.dataset.orderUnsaved==='1';
  function changed() { if (editable) dirty=true; }
  function liquidationFulfillment() {
    var tech=form.querySelector('[name=fulfillment][value=THO_GIAO]');
    if (!tech) return;
    tech.required=true;
    var incompatible=tech.checked && !!form.querySelector('[data-liquidation="1"]:checked');
    tech.setCustomValidity(incompatible ? 'Hàng thanh lý giao tại kho hoặc kho giao, không giao qua thợ.' : '');
    var warning=form.querySelector('[data-liquidation-warning]');
    if (!warning) { warning=document.createElement('p');warning.dataset.liquidationWarning='1';warning.className='alert bad';var picked=tech.closest('[data-picked]');(picked || tech.closest('fieldset')).after(warning); }
    var block=tech.closest('[data-picked]');
    if(incompatible && block){block.querySelector('[data-picked-detail]').hidden=false;block.querySelector('[data-picked-summary]').hidden=true;}
    warning.hidden=!incompatible;warning.dataset.errorCode='THANH_LY_KHONG_GIAO_THO';
    warning.textContent='Hàng thanh lý giao tại kho hoặc kho giao, không giao qua thợ.';
  }
  form.addEventListener('change',liquidationFulfillment);
  liquidationFulfillment();

  function kind(row,clear) {
    var selected=row.querySelector('[data-order-kind]:checked'), goods=!selected || selected.value==='HANG';
    row.querySelector('[data-order-goods]').hidden=!goods;
    if (goods || !clear) return;
    var input=row.querySelector('[name=product_code]'); input.value=''; input.dispatchEvent(new Event('stock-picked'));
    row.querySelector('[data-order-product-name]').textContent='';
    row.querySelector('.product-options').replaceChildren();
    row.querySelector('[data-order-warehouse][value=""]').checked=true;
  }
  function reindex() {
    form.querySelectorAll('[data-order-line]').forEach(function(row,index) {
      [['kind','kind'],['warehouse','warehouse'],['vat','vat']].forEach(function(pair) {
        row.querySelectorAll('[data-order-'+pair[0]+']').forEach(function(input) { input.name=pair[1]+'_'+index; });
      });
    });
  }
  form.querySelectorAll('[data-order-line]').forEach(function(row) { kind(row,false); });
  form.addEventListener('input',changed);
  form.addEventListener('change',function(event) {
    changed();
    if (editable && event.target.matches('[data-order-kind]')) kind(event.target.closest('[data-order-line]'),true);
  });
  form.addEventListener('click',function(event) {
    if (!editable) return;
    var button=event.target.closest('[data-order-add]');
    if (button) {
      var row=document.querySelector('#order-line-template').content.firstElementChild.cloneNode(true);
      form.querySelector('[data-order-lines]').appendChild(row); row.classList.add('line-open'); reindex();
      row.querySelectorAll('[data-live]').forEach(window.bindLive);
      row.querySelectorAll('[data-money]').forEach(window.bindMoney);
      changed(); row.querySelector('[name=product_code]').focus(); return;
    }
    button=event.target.closest('[data-order-remove]');
    if (button) { button.closest('[data-order-line]').remove(); reindex(); changed(); return; }
    button=event.target.closest('[data-order-pick]');
    if (!button) return;
    var picker=button.closest('[data-picker]'), input=picker.querySelector('input'), row=button.closest('[data-order-line]');
    window.pickAvailability(button); input.value=button.dataset.orderPick; input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('.product-options').replaceChildren();
    if (button.dataset.kind==='customer') {
      form.querySelector('[data-order-customer-name]').textContent=button.dataset.name;

    } else {
      var previous=row.querySelector('[data-order-warehouse]:checked'), selected=previous ? previous.value : '';
      var group=row.querySelector('[data-order-warehouse]').closest('.seg');
      group.querySelectorAll('[data-liquidation-warehouse]').forEach(function(label){label.remove();});
      JSON.parse(button.dataset.liquidationChoices || '[]').forEach(function(wh){
        var label=document.createElement('label');label.dataset.liquidationWarehouse='1';
        var radio=document.createElement('input');radio.type='radio';radio.name=group.querySelector('input').name;radio.value=wh.code;radio.dataset.orderWarehouse='';radio.dataset.liquidation='1';radio.checked=wh.code===selected;
        var text=document.createElement('span');text.textContent=wh.code+' · Kho Chờ Xử lý · thanh lý còn '+wh.available;label.append(radio,text);group.append(label);
      });
      if(!group.querySelector('input:checked')) group.querySelector('input[value=""]').checked=true;
      liquidationFulfillment();
      var availability=row.querySelector('[data-availability-summary]');
      if(button.hasAttribute('data-available')) availability.dataset.available=button.dataset.available; else delete availability.dataset.available;
      row.querySelector('[data-order-product-name]').textContent=button.dataset.name;
      row.querySelector('[name=description]').value=button.dataset.name;
      row.querySelector('[name=unit]').value=button.dataset.unit;
      var price=row.querySelector('[name=unit_price]');
      if (!price.dataset.userTyped) price.value=(button.dataset.lastPrice || button.dataset.price).replace(/\B(?=(\d{3})+(?!\d))/g,'.');
      row.querySelector('[data-last-price-hint]').textContent=button.dataset.lastPrice ? 'Giá lần trước '+button.dataset.lastDate+': '+button.dataset.lastPrice.replace(/\B(?=(\d{3})+(?!\d))/g,'.') : '';
    }
    changed();
  });
  document.querySelectorAll('[data-order-confirm]').forEach(function(confirm) { confirm.addEventListener('submit',function(event) {
    if (!dirty) return;
    event.preventDefault();
    var warning=form.querySelector('[data-order-warning]'); warning.hidden=false;
    warning.textContent='Có thay đổi chưa lưu. Bấm Lưu đơn trước khi Chốt đơn.';
    warning.scrollIntoView({block:'center'});
  }); });
})();

(function () {
  document.querySelectorAll('[data-order-terms]').forEach(function (block) {
    function update() {
      var fulfillment=block.querySelector('[name=fulfillment]:checked');
      var term=block.querySelector('[name=payment_term]:checked');
      if (!fulfillment || !term) return;
      block.querySelector('[data-debt-days]').hidden=term.value!=='CONG_NO';
      block.querySelector('[data-deposit-amount]').hidden=term.value!=='DAT_COC';
      var receiving={TAI_QUAY:'Chốt xong giữ hàng, sẵn sàng giao tại quầy.',KHACH_LAY_SAU:'Chốt xong giữ hàng, chờ khách đến lấy; nhắc khi quá ngày hẹn.',THO_GIAO:'Chốt xong giữ hàng; đơn có nhãn thợ giao để xử lý tiếp.',VAN_CHUYEN:'Giao hàng theo số thực và ghi đơn vị vận chuyển, mã vận đơn.'};
      var payment={TRA_NGAY:'Trả ngay: báo thu khi nhận tiền; tại quầy có thể dùng một nút để chốt, giao, báo thu.',DAT_COC:'Nhắc thu cọc, không chặn giao. Số cọc dự kiến chưa ghi tiền.',CONG_NO:'Chốt kiểm hạn mức; hạn thanh toán tính từ ngày giao đầu còn hiệu lực.'};
      block.querySelector('[data-terms-hint]').textContent=receiving[fulfillment.value]+' '+payment[term.value];
    }
    block.addEventListener('change',update); update();
  });
})();

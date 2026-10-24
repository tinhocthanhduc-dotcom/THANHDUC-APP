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
    row.querySelectorAll('[data-order-goods]').forEach(function(el){ el.hidden=!goods; });
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
      var row=newRow(); row.classList.add('line-open');
      if (button.dataset.orderAdd==='KHAC') {
        row.querySelector('[data-order-kind][value=KHAC]').checked=true; kind(row,true); row.dataset.pickedProduct='1';
        changed(); row.querySelector('[name=description]').focus(); return;
      }
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

    } else applyProduct(row,button);
    changed();
  });
  function applyProduct(row,button) {
      row.dataset.pickedProduct='1'; row.querySelector('[name=description]').title=button.dataset.orderPick||button.dataset.code||'';
      var previous=row.querySelector('[data-order-warehouse]:checked'), selected=previous ? previous.value : '';
      var group=row.querySelector('[data-order-warehouse]').closest('.seg');
      group.querySelectorAll('[data-liquidation-warehouse]').forEach(function(label){label.remove();});
      JSON.parse(button.dataset.liquidationChoices || '[]').forEach(function(wh){
        var label=document.createElement('label');label.dataset.liquidationWarehouse='1';
        var radio=document.createElement('input');radio.type='radio';radio.name=group.querySelector('input').name;radio.value=wh.code;radio.dataset.orderWarehouse='';radio.dataset.liquidation='1';radio.checked=wh.code===selected;
        var text=document.createElement('span'),name=document.createElement('i');name.className='wh-name';name.textContent=' · Kho Chờ Xử lý · thanh lý còn '+wh.available;text.append(wh.code,name);label.append(radio,text);group.append(label);
      });
      if(!group.querySelector('input:checked')) group.querySelector('input[value=""]').checked=true;
      liquidationFulfillment();
      var availability=row.querySelector('[data-availability-summary]');
      if(button.hasAttribute('data-available')) availability.dataset.available=button.dataset.available; else delete availability.dataset.available;
      row.querySelector('[data-order-product-name]').textContent=button.dataset.name;
      var serialField=row.querySelector('[data-counter-serials]'); if(serialField) serialField.hidden=!(Number(button.dataset.warrantyMonths)>0);
      row.querySelector('[name=description]').value=button.dataset.name;
      row.querySelector('[name=unit]').value=button.dataset.unit;
      var price=row.querySelector('[name=unit_price]');
      if (!price.dataset.userTyped) price.value=(button.dataset.lastPrice || button.dataset.price).replace(/\B(?=(\d{3})+(?!\d))/g,'.');
      row.querySelector('[data-last-price-hint]').textContent=button.dataset.lastPrice ? 'Giá lần trước '+button.dataset.lastDate+': '+button.dataset.lastPrice.replace(/\B(?=(\d{3})+(?!\d))/g,'.') : '';
  }

  // Bản 2: VAT từng dòng chỉ hiện mức đang chọn, bấm để đổi vòng; SĐT khách tự điền khi chọn khách.
  form.addEventListener('click',function(e){
    var label=e.target.closest('.qline-vat label, .qline-wh label');
    if (!label || !editable) return;
    var seg=label.closest('.seg'), radios=[].slice.call(seg.querySelectorAll('input[type=radio]')), cur=label.querySelector('input');
    var other=[].slice.call(seg.querySelectorAll('label')).filter(function(l){ return l!==label; })[0];
    // Chỉ khi đang ở dạng gọn (các mức khác bị ẩn) mới đổi vòng; điện thoại vẫn bấm chọn trực tiếp.
    if (!cur.checked || !other || getComputedStyle(other).display!=='none') return;
    e.preventDefault();
    if (seg.closest('.qline-wh')) radios=radios.filter(function(r){ return r.value || r===cur; });
    if (radios.length<2) return;
    var next=radios[(radios.indexOf(cur)+1)%radios.length]; next.checked=true;
    next.dispatchEvent(new Event('change',{bubbles:true}));
  });
  form.addEventListener('click',function(e){
    var b=e.target.closest('[data-kind=customer]'), phone=form.querySelector('[data-customer-phone]');
    if (b && phone) phone.value=(b.dataset.phone||'');
  });
  // ---- Màn 3 cột: tra giá + tồn, thêm nhanh, VAT cả đơn, chọn nhanh ngày hẹn (chỉ giao diện) ----
  // Nút chọn một lần cho cả đơn (kho, VAT): value '*' = các dòng đang khác nhau.
  function pickGroup(el) {
    if (!el) return null;
    var g={el:el,
      get value() { return el.dataset.value; },
      set value(v) {
        el.dataset.value=v;
        el.querySelectorAll('[data-pick]').forEach(function(b){ b.classList.toggle('on',b.dataset.pick===v); b.setAttribute('aria-pressed',b.dataset.pick===v ? 'true' : 'false'); });
        el.querySelector('[data-pick-mixed]').hidden=v!=='*';
      },
      addEventListener: function(type,fn) { el.addEventListener('click',function(e){ var b=e.target.closest('[data-pick]'); if (!b) return; g.value=b.dataset.pick; fn(); }); }};
    g.value=el.dataset.value;
    return g;
  }
  var lines=form.querySelector('[data-order-lines]'), vatAll=pickGroup(form.querySelector('[data-order-vat-all]')), whAll=pickGroup(form.querySelector('[data-order-wh-all]'));
  function warehouse() { return whAll && whAll.value!=='*' ? whAll.value : (form.dataset.defaultWarehouse||''); }
  function setWarehouse(row,code) {
    var radio=row.querySelector('[data-order-warehouse][value="'+code+'"]');
    if (!radio || radio.checked) return;
    radio.checked=true;
    var summary=row.querySelector('[data-availability-summary]'); summary.textContent=''; delete summary.dataset.available;
  }
  function syncLines() {
    var rows=form.querySelectorAll('[data-order-line]'), count=form.querySelector('[data-line-count]'), empty=form.querySelector('[data-lines-empty]');
    if (count) count.textContent=rows.length+' dòng';
    if (empty) empty.hidden=rows.length>0;
    if (!vatAll) return;
    var rates={}; rows.forEach(function(row){ var c=row.querySelector('[data-order-vat]:checked'); if (c) rates[c.value]=1; });
    var keys=Object.keys(rates);
    if (keys.length===1) vatAll.value=keys[0]; else if (keys.length>1) vatAll.value='*';
    if (!whAll) return;
    var whs={}; rows.forEach(function(row){ var k=row.querySelector('[data-order-kind]:checked'), c=row.querySelector('[data-order-warehouse]:checked'); if (k && k.value==='HANG' && c) whs[c.value]=1; });
    var w=Object.keys(whs);
    if (w.length===1) whAll.value=w[0]; else if (w.length>1) whAll.value='*';
  }
  function newRow() {
    var row=document.querySelector('#order-line-template').content.firstElementChild.cloneNode(true);
    lines.appendChild(row); reindex();
    row.querySelectorAll('[data-live]').forEach(window.bindLive);
    row.querySelectorAll('[data-money]').forEach(window.bindMoney);
    if (vatAll && vatAll.value!=='*') row.querySelectorAll('[data-order-vat]').forEach(function(r){ r.checked=r.value===vatAll.value; });
    if (whAll && whAll.value!=='*') setWarehouse(row,whAll.value);
    return row;
  }
  function addProduct(b) {
    var code=b.dataset.code, wh=warehouse(), hit=null;
    form.querySelectorAll('[data-order-line]').forEach(function(row){
      var w=row.querySelector('[data-order-warehouse]:checked');
      if (row.querySelector('[name=product_code]').value.trim().toUpperCase()===code && (!wh || (w && w.value===wh))) hit=hit||row;
    });
    if (hit) {
      var q=hit.querySelector('[name=qty]'), n=Number(q.value.replace(',','.'))||0;
      q.value=String(Math.round((n+1)*1000)/1000).replace('.',',');
      // Chọn lại hàng đã có: cộng vào dòng cũ, báo rõ để không tưởng là nhập trùng.
      if (window.tdToast) window.tdToast('Đã có '+(hit.querySelector('[name=description]').value||code)+': tăng số lượng lên '+q.value);
    } else {
      hit=newRow();
      var input=hit.querySelector('[name=product_code]'); input.value=code; input.dispatchEvent(new Event('stock-picked'));
      hit.querySelector('[data-availability-summary]').textContent=b.dataset.availabilityText||'';
      applyProduct(hit,b);
      hit.dataset.lineEdited='1'; hit.dataset.pickedProduct='1'; hit.querySelector('[name=description]').title=code; hit.classList.remove('line-open','line-error');
    }
    hit.classList.remove('qline-flash'); void hit.offsetWidth; hit.classList.add('qline-flash');
    hit.querySelector('[name=qty]').dispatchEvent(new Event('input',{bubbles:true}));
    changed(); syncLines();
  }
  var browse=form.querySelector('[data-browse]');
  if (browse && editable) {
    var bq=browse.querySelector('[data-browse-q]'), list=browse.querySelector('[data-browse-list]'), group='', timer, seq=0;
    function load() {
      var current=++seq, params=new URLSearchParams({q:bq.value, nhom:group, customer_code:form.querySelector('[name=customer_code]').value, warehouse:warehouse()});
      fetch('/don-ban/san-pham?'+params,{credentials:'same-origin'}).then(function(r){ if (!r.ok) throw new Error(); return r.text(); })
        .then(function(html){ if (current===seq) list.innerHTML=html; })
        .catch(function(){ if (current===seq) list.textContent='Không tải được danh sách, hãy thử lại.'; });
    }
    bq.addEventListener('input',function(){ clearTimeout(timer); timer=setTimeout(load,250); });
    bq.addEventListener('keydown',function(e){
      if (e.key!=='Enter') return;
      e.preventDefault();
      var first=list.querySelector('[data-browse-add]');
      if (first) { addProduct(first); bq.select(); }
    });
    browse.addEventListener('click',function(e){
      var g=e.target.closest('[data-browse-group]');
      if (g) { group=g.dataset.browseGroup; browse.querySelectorAll('[data-browse-group]').forEach(function(x){ x.classList.toggle('on',x===g); }); load(); return; }
      var b=e.target.closest('[data-browse-add]');
      if (b) addProduct(b);
    });
    form.querySelector('[name=customer_code]').addEventListener('stock-picked',function(){ setTimeout(load,0); });
  }
  if (vatAll && editable) vatAll.addEventListener('change',function(){
    var rate=vatAll.value;
    if (rate==='*') return;
    form.querySelectorAll('[data-order-vat]').forEach(function(r){ if (r.value===rate) r.checked=true; });
    changed(); lines.dispatchEvent(new Event('change',{bubbles:true}));
  });
  if (whAll && editable) whAll.addEventListener('change',function(){
    var code=whAll.value;
    if (code==='*') return;
    form.querySelectorAll('[data-order-line]').forEach(function(row){ var k=row.querySelector('[data-order-kind]:checked'); if (k && k.value==='HANG') { setWarehouse(row,code); refreshStock(row); } });
    changed(); lines.dispatchEvent(new Event('change',{bubbles:true}));
    if (browse) load();
  });
  form.addEventListener('change',function(e){ if (e.target.matches('[data-order-vat],[data-order-warehouse],[data-order-kind]')) syncLines(); });
  form.addEventListener('click',function(e){
    if (!editable) return;
    var d=e.target.closest('[data-promise-days]');
    if (d) {
      var t=new Date(); t.setDate(t.getDate()+Number(d.dataset.promiseDays));
      var v=form.querySelector('[name=promised_date]');
      v.value=t.getFullYear()+'-'+String(t.getMonth()+1).padStart(2,'0')+'-'+String(t.getDate()).padStart(2,'0');
      v.dispatchEvent(new Event('input',{bubbles:true})); return;
    }
    if (e.target.closest('[data-order-clear]')) {
      if (!form.querySelector('[data-order-line]') || !confirm('Xóa tất cả dòng hàng?')) return;
      form.querySelectorAll('[data-order-line]').forEach(function(row){ row.remove(); });
      reindex(); changed(); syncLines();
    }
  });
  // Ô Tồn: so tồn khả dụng của kho đang chọn với số lượng; đổi kho thì tải lại tồn.
  function qtyOf(row) { return Number((row.querySelector('[name=qty]').value||'').replace(',','.'))||0; }
  function badge(row) {
    var el=row.querySelector('[data-stock-badge]'); if (!el) return;
    var k=row.querySelector('[data-order-kind]:checked'), s=row.querySelector('[data-availability-summary]');
    var has=s && s.dataset.available!==undefined && (!k || k.value==='HANG');
    el.classList.remove('ok','no'); row.classList.remove('line-short');
    if (!has) { el.textContent=''; return; }
    var left=Number(s.dataset.available)-qtyOf(row), fmt=function(n){ return String(Math.round(n*1000)/1000).replace('.',','); };
    if (left>=0) { el.textContent='Còn '+fmt(Number(s.dataset.available)); el.classList.add('ok'); }
    else { el.textContent='Thiếu '+fmt(-left); el.classList.add('no'); row.classList.add('line-short'); }
  }
  function refreshStock(row) {
    var code=row.querySelector('[name=product_code]').value.trim(), wh=row.querySelector('[data-order-warehouse]:checked'), s=row.querySelector('[data-availability-summary]');
    if (!s) return;
    if (!code || !wh || !wh.value) { delete s.dataset.available; badge(row); return; }
    var want=code+'|'+wh.value; row.dataset.stockKey=want;
    fetch('/don-ban/tim-hang?'+new URLSearchParams({q:code,warehouse:wh.value}),{credentials:'same-origin'}).then(function(r){ if (!r.ok) throw new Error(); return r.text(); }).then(function(html){
      if (row.dataset.stockKey!==want) return;
      var box=document.createElement('div'); box.innerHTML=html;
      var hit=[].slice.call(box.querySelectorAll('[data-order-pick]')).filter(function(b){ return b.dataset.orderPick.toUpperCase()===code.toUpperCase(); })[0];
      if (hit && hit.hasAttribute('data-available')) { s.dataset.available=hit.dataset.available; s.textContent=hit.dataset.availabilityText||''; } else delete s.dataset.available;
      badge(row);
    }).catch(function(){});
  }
  form.addEventListener('input',function(e){ var row=e.target.closest('[data-order-line]'); if (row && e.target.name==='qty') badge(row); });
  form.addEventListener('change',function(e){
    var row=e.target.closest('[data-order-line]'); if (!row) return;
    if (e.target.matches('[data-order-warehouse]')) refreshStock(row); else badge(row);
  });
  form.addEventListener('click',function(e){ if (e.target.closest('[data-step], [data-order-pick]')) { var row=e.target.closest('[data-order-line]'); if (row) setTimeout(function(){ badge(row); },0); } });
  form.querySelectorAll('[data-order-line]').forEach(badge);
  // Ô tìm hàng: danh sách thả xuống khi bấm vào hoặc gõ; bấm ra ngoài hoặc Esc thì đóng.
  if (browse && editable) {
    var drop=browse.querySelector('[data-browse-list]'), q=browse.querySelector('[data-browse-q]');
    var open=function(){ drop.hidden=false; }, close=function(){ drop.hidden=true; };
    q.addEventListener('focus',open); q.addEventListener('input',open);
    q.addEventListener('keydown',function(e){ if (e.key==='Escape') { close(); q.blur(); } });
    document.addEventListener('click',function(e){ if (!browse.contains(e.target)) close(); });
    drop.addEventListener('click',function(e){ if (e.target.closest('[data-browse-add]')) setTimeout(function(){ q.value=''; q.dispatchEvent(new Event('input')); close(); },0); });
  }
  // Địa chỉ giao chỉ cần khi thợ giao hoặc gửi vận chuyển.
  var addr=form.querySelector('[data-ship-address]');
  if (addr) form.addEventListener('change',function(e){
    if (!e.target.matches('[name=fulfillment]')) return;
    addr.hidden=!(['THO_GIAO','VAN_CHUYEN'].indexOf(e.target.value)>=0 || addr.querySelector('textarea').value.trim());
  });
  if (lines) { new MutationObserver(syncLines).observe(lines,{childList:true}); syncLines(); }
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
      // Tại quầy + Trả ngay: nút chính thành Bán tại quầy (chốt + giao đủ + báo thu một lần).
      var primary=block.querySelector('[data-counter-label]');
      if (primary) {
        var counter=fulfillment.value==='TAI_QUAY' && term.value==='TRA_NGAY';
        primary.textContent=counter ? primary.dataset.counterLabel : primary.dataset.normalLabel;
        primary.value=counter ? 'counter' : primary.dataset.normalThen;
        block.querySelectorAll('[data-counter-sale],[data-counter-alt]').forEach(function (el) { el.hidden=!counter; });
      }
    }
    block.addEventListener('change',update); update();
  });
})();

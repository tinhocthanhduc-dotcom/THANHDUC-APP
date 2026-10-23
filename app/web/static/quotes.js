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
      var row=newRow(); row.classList.add('line-open');
      changed(); row.querySelector('[name=description]').focus(); return;
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

  // Bản 2: VAT từng dòng chỉ hiện mức đang chọn, bấm để đổi vòng; SĐT khách tự điền khi chọn khách.
  form.addEventListener('click',function(e){
    var label=e.target.closest('.qline-vat label');
    if (!label || !editable) return;
    var seg=label.closest('.seg'), radios=[].slice.call(seg.querySelectorAll('input[type=radio]')), cur=label.querySelector('input');
    var other=[].slice.call(seg.querySelectorAll('label')).filter(function(l){ return l!==label; })[0];
    // Chỉ khi đang ở dạng gọn (các mức khác bị ẩn) mới đổi vòng; điện thoại vẫn bấm chọn trực tiếp.
    if (!cur.checked || !other || getComputedStyle(other).display!=='none') return;
    e.preventDefault();
    var next=radios[(radios.indexOf(cur)+1)%radios.length]; next.checked=true;
    next.dispatchEvent(new Event('change',{bubbles:true}));
  });
  form.addEventListener('click',function(e){
    var b=e.target.closest('[data-kind=customer]'), phone=form.querySelector('[data-customer-phone]');
    if (b && phone) phone.value=(b.dataset.phone||'');
  });
  // ---- Màn 3 cột: tra giá, thêm nhanh, VAT cả báo giá, chọn nhanh hiệu lực (chỉ giao diện) ----
  // Nút chọn một lần cho cả báo giá (VAT): value '*' = các dòng đang khác nhau.
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
  var lines=form.querySelector('[data-quote-lines]'), vatAll=pickGroup(form.querySelector('[data-quote-vat-all]'));
  function thousands(v) { return String(v).replace(/\B(?=(\d{3})+(?!\d))/g,'.'); }
  function syncLines() {
    var rows=form.querySelectorAll('[data-quote-line]'), count=form.querySelector('[data-line-count]'), empty=form.querySelector('[data-lines-empty]');
    if (count) count.textContent=rows.length+' dòng';
    if (empty) empty.hidden=rows.length>0;
    if (!vatAll) return;
    var rates={}; rows.forEach(function(row){ var c=row.querySelector('[data-quote-vat]:checked'); if (c) rates[c.value]=1; });
    var keys=Object.keys(rates);
    if (keys.length===1) vatAll.value=keys[0]; else if (keys.length>1) vatAll.value='*';
  }
  function newRow() {
    var row=document.querySelector('#quote-line-template').content.firstElementChild.cloneNode(true);
    lines.appendChild(row); reindex();
    row.querySelectorAll('[data-live]').forEach(window.bindLive);
    row.querySelectorAll('[data-money]').forEach(window.bindMoney);
    if (vatAll && vatAll.value!=='*') row.querySelectorAll('[data-quote-vat]').forEach(function(r){ r.checked=r.value===vatAll.value; });
    return row;
  }
  function addProduct(b) {
    var code=b.dataset.code, hit=null;
    form.querySelectorAll('[data-quote-line]').forEach(function(row){ if (row.querySelector('[name=product_code]').value.trim().toUpperCase()===code) hit=hit||row; });
    if (hit) {
      var q=hit.querySelector('[name=qty]'), n=Number(q.value.replace(',','.'))||0;
      q.value=String(Math.round((n+1)*1000)/1000).replace('.',',');
      // Chọn lại hàng đã có: cộng vào dòng cũ, báo rõ để không tưởng là nhập trùng.
      if (window.tdToast) window.tdToast('Đã có '+(hit.querySelector('[name=description]').value||code)+': tăng số lượng lên '+q.value);
    } else {
      hit=newRow();
      hit.querySelector('[name=product_code]').value=code;
      hit.querySelector('[data-quote-product-name]').textContent=b.dataset.name;
      hit.querySelector('[name=description]').value=b.dataset.name;
      hit.querySelector('[name=unit]').value=b.dataset.unit;
      hit.querySelector('[name=unit_price]').value=thousands(b.dataset.lastPrice||b.dataset.price);
      hit.querySelector('[data-last-price-hint]').textContent=b.dataset.lastPrice ? 'Giá lần trước '+b.dataset.lastDate+': '+thousands(b.dataset.lastPrice) : '';
      hit.dataset.lineEdited='1'; hit.dataset.pickedProduct='1'; hit.querySelector('[name=description]').title=code; hit.classList.remove('line-open','line-error');
    }
    hit.classList.remove('qline-flash'); void hit.offsetWidth; hit.classList.add('qline-flash');
    hit.querySelector('[name=qty]').dispatchEvent(new Event('input',{bubbles:true}));
    syncLines();
  }
  var browse=form.querySelector('[data-browse]');
  if (browse && editable) {
    var bq=browse.querySelector('[data-browse-q]'), list=browse.querySelector('[data-browse-list]'), group='', timer, seq=0;
    function load() {
      var current=++seq, params=new URLSearchParams({q:bq.value, nhom:group, customer_code:form.querySelector('[name=customer_code]').value});
      fetch('/bao-gia/san-pham?'+params,{credentials:'same-origin'}).then(function(r){ if (!r.ok) throw new Error(); return r.text(); })
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
    form.querySelectorAll('[data-quote-vat]').forEach(function(r){ if (r.value===rate) r.checked=true; });
    changed(); lines.dispatchEvent(new Event('change',{bubbles:true}));
  });
  form.addEventListener('change',function(e){ if (e.target.matches('[data-quote-vat]')) syncLines(); });
  form.addEventListener('click',function(e){
    if (!editable) return;
    var d=e.target.closest('[data-valid-days]');
    if (d) {
      var t=new Date(form.dataset.today+'T00:00:00'); t.setDate(t.getDate()+Number(d.dataset.validDays));
      var v=form.querySelector('[name=valid_until]');
      v.value=t.getFullYear()+'-'+String(t.getMonth()+1).padStart(2,'0')+'-'+String(t.getDate()).padStart(2,'0');
      v.dispatchEvent(new Event('input',{bubbles:true})); return;
    }
    if (e.target.closest('[data-quote-clear]')) {
      if (!form.querySelector('[data-quote-line]') || !confirm('Xóa tất cả dòng hàng?')) return;
      form.querySelectorAll('[data-quote-line]').forEach(function(row){ row.remove(); });
      reindex(); changed(); syncLines();
    }
  });
  new MutationObserver(syncLines).observe(lines,{childList:true});
  syncLines();
  var send=document.querySelector('[data-quote-send]');
  if (send) send.addEventListener('submit',function(event) {
    if (!dirty) return;
    event.preventDefault();
    var warning=form.querySelector('[data-quote-warning]'); warning.hidden=false;
    warning.textContent='Có thay đổi chưa lưu. Bấm Lưu báo giá trước khi Gửi.';
    warning.scrollIntoView({block:'center'});
  });
})();

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
  // Nhập nhanh: thêm xong nhảy tới Số lượng của dòng đó (điện thoại thì mở dòng ra trước).
  function focusQty(row) {
    var qty=row.querySelector('[name=qty]');
    if (!qty.offsetParent) { row.classList.add('line-open'); var s=row.querySelector('[data-line-summary]'); if (s) s.setAttribute('aria-expanded','true'); }
    qty.focus(); qty.select(); qty.scrollIntoView({block:'nearest'});
  }
  function addProduct(b,count) {
    var code=b.dataset.code, hit=null, add=count||1;
    form.querySelectorAll('[data-quote-line]').forEach(function(row){ if (row.querySelector('[name=product_code]').value.trim().toUpperCase()===code) hit=hit||row; });
    if (hit) {
      var q=hit.querySelector('[name=qty]'), n=Number(q.value.replace(',','.'))||0;
      q.value=String(Math.round((n+add)*1000)/1000).replace('.',',');
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
      if (add!==1) hit.querySelector('[name=qty]').value=String(add);
    }
    hit.classList.remove('qline-flash'); void hit.offsetWidth; hit.classList.add('qline-flash');
    hit.querySelector('[name=qty]').dispatchEvent(new Event('input',{bubbles:true}));
    changed(); syncLines();
    return hit;
  }
  var browse=form.querySelector('[data-browse]');
  if (browse && editable) {
    var bq=browse.querySelector('[data-browse-q]'), list=browse.querySelector('[data-browse-list]'), group='', timer, seq=0, hl=0, moved=false, latest=Promise.resolve();
    // "3*107a": thêm 3 cái mã khớp đầu; phần sau dấu * mới là chữ tìm.
    function typed() { var m=/^\s*(\d{1,4})\s*\*\s*(\S.*)$/.exec(bq.value); return m ? {count:Number(m[1])||1,q:m[2]} : {count:1,q:bq.value}; }
    function choices() { return [].slice.call(list.querySelectorAll('[data-browse-add]')); }
    function mark() {
      var all=choices(); hl=Math.max(0,Math.min(hl,all.length-1));
      all.forEach(function(b,i){ var item=b.closest('.qb-item')||b; item.classList.toggle('qb-hl',i===hl); if (i===hl && moved) item.scrollIntoView({block:'nearest'}); });
    }
    function load() {
      var current=++seq, params=new URLSearchParams({q:typed().q, nhom:group, customer_code:form.querySelector('[name=customer_code]').value});
      var done=fetch('/bao-gia/san-pham?'+params,{credentials:'same-origin'}).then(function(r){ if (!r.ok) throw new Error(); return r.text(); })
        .then(function(html){ if (current===seq) { list.innerHTML=html; hl=0; moved=false; if (window.tdMarkHits) window.tdMarkHits(list,typed().q); mark(); } })
        .catch(function(){ if (current===seq) list.textContent='Không tải được danh sách, hãy thử lại.'; });
      latest=done;
      return done;
    }
    // Thêm dòng đang sáng: xóa ô tìm rồi nhảy xuống Số lượng.
    function take(b,count) {
      var row=addProduct(b,count);
      bq.value=''; clearTimeout(timer); timer=null; load();
      focusQty(row);
    }
    bq.addEventListener('input',function(){ clearTimeout(timer); timer=setTimeout(function(){ timer=null; load(); },200); });
    bq.addEventListener('keydown',function(e){
      if (e.key==='ArrowDown' || e.key==='ArrowUp') { e.preventDefault(); moved=true; hl+=e.key==='ArrowDown' ? 1 : -1; mark(); return; }
      if (e.key!=='Enter') return;
      e.preventDefault();
      if (!bq.value.trim() && !moved) return;
      var want=typed().count;
      // Gõ nhanh rồi Enter ngay (máy quét mã): chờ kết quả của chữ vừa gõ rồi mới thêm.
      var go=function(){ var b=choices()[hl]; if (b) take(b,want); else if (window.tdToast) window.tdToast('Không có mã nào khớp "'+typed().q+'".','bad'); };
      if (timer) { clearTimeout(timer); timer=null; load(); }
      latest.then(go);
    });
    browse.addEventListener('click',function(e){
      var g=e.target.closest('[data-browse-group]');
      if (g) { group=g.dataset.browseGroup; browse.querySelectorAll('[data-browse-group]').forEach(function(x){ x.classList.toggle('on',x===g); }); load(); return; }
      var b=e.target.closest('[data-browse-add]');
      if (b) take(b,typed().count);
    });
    // Enter trong dòng: Số lượng → Đơn giá → về ô tìm. Enter không bao giờ gửi form ở đây.
    lines.addEventListener('keydown',function(e){
      if (e.key!=='Enter' || !e.target.matches('input')) return;
      var row=e.target.closest('[data-quote-line]'); if (!row) return;
      e.preventDefault();
      if (e.target.name==='product_code') return; // ô chọn hàng tự xử lý Enter (base.jinja)
      if (e.target.name==='qty') { var price=row.querySelector('[name=unit_price]'); price.focus(); price.select(); return; }
      if (e.target.name==='unit_price') { bq.focus(); bq.select(); return; }
      var qty=row.querySelector('[name=qty]'); qty.focus(); qty.select();
    });
    document.addEventListener('keydown',function(e){ if (e.key==='F2') { e.preventDefault(); bq.focus(); bq.select(); } });
    mark();
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

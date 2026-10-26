// Đợt 3 · khuôn 5: soạn đơn mua như Đơn bán. Tìm hàng để thêm, dòng gọn, kho/VAT cả đơn, tổng tạm tính, Bước tiếp.
(function() {
  var form=document.querySelector('[data-purchase-form]'); if (!form || form.dataset.purchaseEditable!=='1') return;
  var lines=form.querySelector('[data-purchase-lines]'), tpl=document.querySelector('#purchase-line-template');
  function money(n) { return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g,'.'); }
  function digits(text) { var d=String(text||'').replace(/\D/g,''); return d==='' ? NaN : Number(d); }
  function num(text) { return Number(String(text||'').replace(',','.')); }
  function rows() { return [].slice.call(lines.querySelectorAll('[data-purchase-line]')); }
  function reindex() {
    rows().forEach(function(row,i) {
      row.querySelectorAll('[data-purchase-vat]').forEach(function(input){ input.name='vat_'+i; });
      row.querySelectorAll('[data-purchase-warehouse]').forEach(function(input){ input.name='warehouse_'+i; });
    });
  }
  // Nút chọn một lần cho cả đơn (kho, VAT): '*' = các dòng đang khác nhau.
  function group(el, field) {
    if (!el) return null;
    function show(v) {
      el.dataset.value=v;
      el.querySelectorAll('[data-pick]').forEach(function(b){ b.classList.toggle('on',b.dataset.pick===v); b.setAttribute('aria-pressed',b.dataset.pick===v ? 'true' : 'false'); });
      el.querySelector('[data-pick-mixed]').hidden=v!=='*';
    }
    el.addEventListener('click',function(e) {
      var b=e.target.closest('[data-pick]'); if (!b) return;
      show(b.dataset.pick);
      rows().forEach(function(row){ var r=row.querySelector(field+'[value="'+b.dataset.pick+'"]'); if (r) r.checked=true; });
      update();
    });
    show(el.dataset.value);
    return {show:show, get value() { return el.dataset.value; }};
  }
  var whAll=group(form.querySelector('[data-po-wh-all]'),'[data-purchase-warehouse]'), vatAll=group(form.querySelector('[data-po-vat-all]'),'[data-purchase-vat]');
  function same(field) {
    var vals=rows().map(function(row){ var r=row.querySelector(field+':checked'); return r ? r.value : ''; }).filter(Boolean);
    if (!vals.length) return null;
    return vals.every(function(v){ return v===vals[0]; }) ? vals[0] : '*';
  }
  function update() {
    var sub=0, vat=0, filled=true;
    rows().forEach(function(row) {
      var qty=num(row.querySelector('[name=qty]').value), cost=digits(row.querySelector('[name=unit_cost]').value);
      var rate=row.querySelector('[data-purchase-vat]:checked'), wh=row.querySelector('[data-purchase-warehouse]:checked');
      var amount=isNaN(qty)||isNaN(cost) ? NaN : Math.round(qty*cost);
      row.querySelector('[data-purchase-amount]').textContent=isNaN(amount) ? '—' : money(amount);
      if (!isNaN(amount)) { sub+=amount; vat+=Math.round(amount*(rate ? Number(rate.value) : 0)/100); }
      var stock={}; try { stock=JSON.parse(row.dataset.stock||'{}'); } catch (e) {}
      var badge=row.querySelector('[data-purchase-stock]'); badge.textContent=wh && row.querySelector('[name=product_code]').value ? (stock[wh.value]||'0') : '';
      if (!row.querySelector('[name=product_code]').value || !wh || isNaN(cost) || !(qty>0)) filled=false;
    });
    form.querySelector('[data-po-sub]').textContent=money(sub);
    form.querySelector('[data-po-vat]').textContent=money(vat);
    form.querySelector('[data-po-total]').textContent=money(sub+vat);
    form.querySelector('[data-po-total-bar]').textContent=money(sub+vat);
    var n=rows().length;
    form.querySelector('[data-po-count]').textContent=n+' dòng';
    form.querySelector('[data-po-empty]').hidden=n>0;
    var w=same('[data-purchase-warehouse]'), v=same('[data-purchase-vat]');
    if (whAll && w) whAll.show(w); if (vatAll && v) vatAll.show(v);
    var checks={supplier: !!form.querySelector('[name=supplier_code]').value.trim(), lines: n>0, filled: n>0 && filled};
    var missing=[];
    form.querySelectorAll('[data-po-checks] [data-check]').forEach(function(li) {
      var ok=checks[li.dataset.check]; li.dataset.ok=ok ? '1' : '0';
      li.textContent=(ok ? '✓ ' : '✕ ')+li.textContent.replace(/^[✓✕] /,'');
      if (!ok) missing.push(li.textContent.slice(2));
    });
    var primary=form.querySelector('[data-po-primary]');
    primary.classList.toggle('is-blocked',missing.length>0); primary.setAttribute('aria-disabled',missing.length ? 'true' : 'false');
    primary.dataset.missing=missing.join(' · ');
  }
  function addRow(data) {
    var row=tpl.content.firstElementChild.cloneNode(true);
    lines.appendChild(row); reindex();
    var wh=whAll && whAll.value!=='*' ? whAll.value : form.dataset.defaultWarehouse;
    if (wh) { var r=row.querySelector('[data-purchase-warehouse][value="'+wh+'"]'); if (r) r.checked=true; }
    if (vatAll && vatAll.value!=='*') { var v=row.querySelector('[data-purchase-vat][value="'+vatAll.value+'"]'); if (v) v.checked=true; }
    row.querySelectorAll('[data-live]').forEach(window.bindLive); row.querySelectorAll('[data-money]').forEach(window.bindMoney);
    if (data) fill(row,data);
    return row;
  }
  function fill(row, data) {
    row.querySelector('[name=product_code]').value=data.code;
    row.querySelector('[data-purchase-name]').textContent=data.name;
    row.querySelector('[data-purchase-code]').textContent=data.code+(data.unit ? ' · '+data.unit : '');
    row.querySelector('[data-purchase-last]').textContent=data.cost ? ' · lần trước '+money(Number(data.cost)) : '';
    var price=row.querySelector('[name=unit_cost]'); if (!price.value && data.cost) price.value=money(Number(data.cost));
    row.dataset.stock=data.stock||'{}'; row.dataset.picked='1'; row.classList.remove('pol-bad');
    var err=row.querySelector('.field-error'); if (err) err.remove();
    var opts=row.querySelector('.product-options'); if (opts) opts.replaceChildren();
  }
  // Ô "+ Tìm hàng để thêm"
  // Nhập nhanh như Đơn bán: ↑↓ chọn, Enter thêm rồi nhảy xuống Số lượng → Giá → về ô tìm; "3*mã" thêm 3 cái.
  var find=form.querySelector('[data-po-find]'), q=find.querySelector('[data-po-q]'), list=find.querySelector('[data-po-list]'), timer, seq=0, hl=0, latest=Promise.resolve();
  function typed() { var m=/^\s*(\d{1,4})\s*\*\s*(\S.*)$/.exec(q.value); return m ? {count:Number(m[1])||1,q:m[2]} : {count:1,q:q.value}; }
  function choices() { return [].slice.call(list.querySelectorAll('[data-po-add]')); }
  function mark(scroll) {
    var all=choices(); hl=Math.max(0,Math.min(hl,all.length-1));
    all.forEach(function(b,i){ var item=b.closest('.qb-item')||b; item.classList.toggle('qb-hl',i===hl); if (i===hl && scroll) item.scrollIntoView({block:'nearest'}); });
  }
  function load(show) {
    var cur=++seq, text=typed().q;
    latest=fetch('/don-mua/san-pham?'+new URLSearchParams({q:text}),{credentials:'same-origin'}).then(function(r){ if (!r.ok) throw new Error(); return r.text(); })
      .then(function(html){ if (cur!==seq) return; list.innerHTML=html; hl=0; if (window.tdMarkHits) window.tdMarkHits(list,text); mark(false); if (show!==false) list.hidden=false; })
      .catch(function(){ list.textContent='Không tải được kết quả, hãy thử lại.'; list.hidden=false; });
    return latest;
  }
  function focusQty(row) { var input=row.querySelector('[name=qty]'); input.focus(); input.select(); input.scrollIntoView({block:'nearest'}); }
  function add(button,count) {
    count=count||typed().count;
    var data={code:button.dataset.code,name:button.dataset.name,unit:button.dataset.unit,cost:button.dataset.cost,stock:button.dataset.stock};
    var wh=whAll && whAll.value!=='*' ? whAll.value : form.dataset.defaultWarehouse;
    var dup=rows().filter(function(row){ var r=row.querySelector('[data-purchase-warehouse]:checked'); return row.querySelector('[name=product_code]').value===data.code && (!r || r.value===wh); })[0];
    var row=dup;
    if (dup) { var input=dup.querySelector('[name=qty]'); input.value=String(Math.round(((num(input.value)||0)+count)*1000)/1000).replace('.',','); window.tdToast && window.tdToast(data.name+': đã có trong đơn, tăng số lượng lên '+input.value); }
    else { row=addRow(data); if (count!==1) row.querySelector('[name=qty]').value=String(count); }
    q.value=''; clearTimeout(timer); timer=null; load(false); list.hidden=true; update(); focusQty(row);
  }
  q.addEventListener('input',function(){ clearTimeout(timer); timer=setTimeout(function(){ timer=null; load(); },200); });
  q.addEventListener('focus',function(){ if (!list.innerHTML) load(); else list.hidden=false; });
  q.addEventListener('keydown',function(e) {
    if (e.key==='Escape') { list.hidden=true; return; }
    if (e.key==='ArrowDown' || e.key==='ArrowUp') { e.preventDefault(); list.hidden=false; hl+=e.key==='ArrowDown' ? 1 : -1; mark(true); return; }
    if (e.key!=='Enter') return;
    e.preventDefault();
    if (!q.value.trim() && list.hidden) return;
    var count=typed().count;
    // Gõ nhanh rồi Enter ngay (máy quét mã): chờ kết quả của chữ vừa gõ rồi mới thêm.
    if (timer) { clearTimeout(timer); timer=null; load(); }
    latest.then(function(){ var b=choices()[hl]; if (b) add(b,count); else if (window.tdToast) window.tdToast('Không có mã nào khớp "'+typed().q+'".','bad'); });
  });
  list.addEventListener('click',function(e){ var b=e.target.closest('[data-po-add]'); if (b) add(b); });
  // Enter trong dòng: Số lượng → Giá → về ô tìm; không gửi form.
  lines.addEventListener('keydown',function(e) {
    if (e.key!=='Enter' || !e.target.matches('input')) return;
    var row=e.target.closest('[data-purchase-line]'); if (!row) return;
    if (e.target.name==='product_code') return; // ô chọn hàng tự xử lý Enter (base.jinja)
    e.preventDefault();
    if (e.target.name==='qty') { var price=row.querySelector('[name=unit_cost]'); price.focus(); price.select(); return; }
    if (e.target.name==='unit_cost') { q.focus(); q.select(); list.hidden=false; return; }
    focusQty(row);
  });
  document.addEventListener('keydown',function(e){ if (e.key==='F2') { e.preventDefault(); q.focus(); q.select(); list.hidden=false; } });
  document.addEventListener('click',function(e){ if (!find.contains(e.target)) list.hidden=true; });
  form.addEventListener('click',function(event) {
    if (event.target.closest('[data-purchase-add]')) { var row=addRow(); update(); row.querySelector('[name=product_code]').focus(); return; }
    var remove=event.target.closest('[data-purchase-remove]');
    if (remove) { remove.closest('[data-purchase-line]').remove(); reindex(); update(); return; }
    var change=event.target.closest('[data-po-change]');
    if (change) { var box=change.closest('[data-po-supplier]'); box.dataset.picked='0'; var inp=box.querySelector('[name=supplier_code]'); inp.select(); inp.focus(); return; }
    var days=event.target.closest('[data-po-days]');
    if (days) {
      var base=form.querySelector('[name=order_date]').value, d=base ? new Date(base+'T00:00:00') : new Date();
      d.setDate(d.getDate()+Number(days.dataset.poDays));
      form.querySelector('[data-po-expected]').value=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
      return;
    }
    // Kho, VAT từng dòng ở dạng gọn (chỉ hiện mức đang chọn): bấm để đổi vòng.
    var label=event.target.closest('.pol-wh label, .pol-vat label');
    if (label) {
      var seg=label.closest('.seg'), cur=label.querySelector('input'), others=[].slice.call(seg.querySelectorAll('label')).filter(function(l){ return l!==label; });
      if (cur.checked && others[0] && getComputedStyle(others[0]).display==='none') {
        event.preventDefault();
        var radios=[].slice.call(seg.querySelectorAll('input[type=radio]:not(:disabled)')), next=radios[(radios.indexOf(cur)+1)%radios.length];
        next.checked=true; update();
      }
      return;
    }
    var pick=event.target.closest('[data-purchase-pick]'); if (!pick) return;
    var picker=pick.closest('[data-picker]'), input=picker.querySelector('input');
    input.value=pick.dataset.purchasePick; input.dispatchEvent(new Event('stock-picked'));
    picker.querySelector('.product-options').replaceChildren();
    if (pick.dataset.kind==='supplier') {
      form.querySelector('[data-purchase-supplier]').textContent=pick.dataset.purchasePick+' · '+pick.dataset.name;
      form.querySelector('[data-po-supplier-info]').textContent=pick.dataset.info||'';
      picker.dataset.picked='1'; picker.closest('.po-supplier').classList.remove('field-invalid');
    } else fill(pick.closest('[data-purchase-line]'),{code:pick.dataset.purchasePick,name:pick.dataset.name,unit:pick.dataset.unit,cost:pick.dataset.cost,stock:pick.dataset.stock});
    update();
  });
  form.addEventListener('input',update);
  form.addEventListener('change',update);
  form.addEventListener('submit',function(e) {
    var primary=form.querySelector('[data-po-primary]');
    if (e.submitter===primary && primary.classList.contains('is-blocked')) {
      e.preventDefault();
      var note=form.querySelector('[data-po-missing]'); note.hidden=false; note.textContent='Còn thiếu: '+primary.dataset.missing;
      window.tdToast && window.tdToast('Còn thiếu: '+primary.dataset.missing,'bad');
    }
  });
  reindex(); update();
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

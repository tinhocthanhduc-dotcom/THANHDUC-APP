'use strict';
document.querySelectorAll('[data-commission-user-search]').forEach(function(input) {
  input.addEventListener('input', function() {
    var q=input.value.toLocaleLowerCase('vi').normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/đ/g,'d');
    input.closest('.commission-picker').querySelectorAll('[data-person-name]').forEach(function(label) {
      var name=label.dataset.personName.normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/đ/g,'d');
      label.hidden=!name.includes(q);
    });
  });
});

// Quy tắc hoa hồng: một popup cho Thêm và Thay. Không có JS thì nút Thay vẫn mở ?thay= (server điền sẵn).
(function() {
  var dlg=document.querySelector('[data-hh-dialog]');if(!dlg)return;
  var form=dlg.querySelector('[data-hh-form]');
  function q(sel){return form.querySelector(sel);}
  function val(name){var el=form.querySelector('[name="'+name+'"]:checked');return el?el.value:'';}
  function setRadio(name,value){form.querySelectorAll('[name="'+name+'"]').forEach(function(r){r.checked=r.value===value;});}
  function refresh() {
    var replacing=!!q('[name=replaces_id]').value,target=replacing?form.dataset.target:val('target');
    var kind=replacing?form.dataset.kind:val('service_kind');
    if(!replacing){target=val('target');kind=val('service_kind');}
    var sales=target==='KINH_DOANH',perUnit=!sales&&(kind==='SAN_XUAT'||kind==='GIAO_DON');
    q('[data-hh-kinds]').hidden=sales;
    if(sales&&!replacing)setRadio('service_kind','');
    q('[data-hh-fixed]').hidden=sales;
    if(sales)q('[name=fixed_amount]').value='0';
    q('[data-hh-pct]').hidden=perUnit;
    if(perUnit)q('[name=pct]').value='0';
    q('[data-hh-pct-hint]').textContent=sales?'% lãi gộp đơn bán':'% tiền công phiếu';
    q('[data-hh-fixed-hint]').textContent=kind==='SAN_XUAT'?'mỗi sản phẩm đạt':kind==='GIAO_DON'?'mỗi lần giao':'mỗi phiếu';
    var role=sales?'sales':'technician';
    q('[data-hh-user]').querySelectorAll('option[data-roles]').forEach(function(o){
      var ok=(' '+o.dataset.roles+' ').indexOf(' '+role+' ')>=0;o.hidden=!ok;o.disabled=!ok;
      if(!ok&&o.selected)q('[data-hh-user]').value='';
    });
  }
  function open(){if(!dlg.open)dlg.showModal();}
  function today(){var d=new Date(),z=function(n){return (n<10?'0':'')+n;};return d.getFullYear()+'-'+z(d.getMonth()+1)+'-'+z(d.getDate());}
  function reset(){
    form.querySelectorAll('.alert').forEach(function(a){a.remove();});
    q('[name=pct]').value='0';q('[name=fixed_amount]').value='0';q('[name=reason]').value='';
    if(!q('[name=effective_from]').value)q('[name=effective_from]').value=today();
  }
  document.addEventListener('click',function(e){
    var add=e.target.closest('[data-hh-add]'),rep=e.target.closest('[data-hh-replace]');
    if(e.target.closest('[data-hh-close]')){e.preventDefault();dlg.close();return;}
    if(add){
      e.preventDefault();reset();q('[name=replaces_id]').value='';form.dataset.mode='them';
      q('[data-hh-title]').textContent='Thêm quy tắc';q('[data-hh-submit]').textContent='Thêm quy tắc';
      q('[data-hh-pick]').hidden=false;q('[data-hh-scope]').hidden=true;
      if(add.dataset.target)setRadio('target',add.dataset.target);
      setRadio('service_kind',add.dataset.kind||'');q('[data-hh-user]').value='';
      refresh();open();return;
    }
    if(rep){
      e.preventDefault();reset();var d=rep.dataset;
      q('[name=replaces_id]').value=d.hhReplace;form.dataset.mode='thay';form.dataset.target=d.target;form.dataset.kind=d.kind;
      setRadio('target',d.target);setRadio('service_kind',d.kind);q('[data-hh-user]').value=d.user;
      q('[name=pct]').value=d.pct;q('[name=fixed_amount]').value=d.fixed;
      q('[data-hh-title]').textContent='Thay quy tắc #'+d.hhReplace;q('[data-hh-submit]').textContent='Thay bản này';
      q('[data-hh-pick]').hidden=true;q('[data-hh-scope]').hidden=false;
      q('[data-hh-scope-name]').textContent=d.targetLabel+' · '+d.kindLabel+' · '+d.who;
      q('[data-hh-current]').textContent=d.current;
      refresh();open();q('[name=pct]').closest('label').hidden?q('[name=fixed_amount]').focus():q('[name=pct]').focus();
    }
  });
  form.addEventListener('change',function(e){if(e.target.name==='target'||e.target.name==='service_kind')refresh();});
  if(q('[name=replaces_id]').value){var k=val('service_kind');form.dataset.target=val('target');form.dataset.kind=k;}
  refresh();
  if(dlg.hasAttribute('data-open'))open();
})();

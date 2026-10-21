(function(){
  function condition(row,active){
    var picked=row.querySelector('[data-condition-choice]:checked'), damage=picked && picked.value==='HU_HONG';
    var fields=row.querySelector('[data-damage-fields]');if(!fields)return;
    fields.hidden=!damage;
    fields.querySelectorAll('textarea,input').forEach(function(input){input.disabled=!active||!damage;});
    var reason=fields.querySelector('[data-damage-reason]');if(reason)reason.required=!!damage&&active&&(!row.querySelector('[name=return_qty]')||Number(row.querySelector('[name=return_qty]').value.replace(',','.'))>0);
  }
  document.querySelectorAll('[data-return-fields]').forEach(function(root){
    function update(){
      root.querySelectorAll('[data-return-row]').forEach(function(row,index){row.querySelectorAll('[data-condition-choice]').forEach(function(input){input.name='return_condition_tmp_'+index;});});
      root.querySelectorAll('[data-return-row]').forEach(function(row,index){
      row.querySelectorAll('[data-condition-choice]').forEach(function(input){input.name='return_condition_'+index;});
      row.querySelector('[data-damage-reason]').name='return_damage_reason_'+index;
      row.querySelector('[data-damage-photo]').name='return_photo_'+index;
      condition(row,!root.hidden);
    });}
    root.addEventListener('change',update);root.addEventListener('input',update);root.addEventListener('return-state',update);
    root.addEventListener('click',function(event){
      var button=event.target.closest('[data-return-split]');if(!button)return;
      var original=button.closest('[data-return-row]'), copy=original.cloneNode(true), chosen=original.querySelector('[data-condition-choice]:checked').value;
      copy.querySelector('[name=return_qty]').value='0';copy.querySelector('[data-damage-reason]').value='';copy.querySelector('[data-damage-photo]').value='';
      copy.querySelectorAll('[data-condition-choice]').forEach(function(input){input.name='return_condition_pending';});
      original.after(copy);update();
      copy.querySelector('[data-condition-choice][value="'+(chosen==='TOT'?'HU_HONG':'TOT')+'"]').checked=true;update();
      copy.querySelector('[name=return_qty]').focus();
    });update();
  });
  // Thẻ A: tick từng mã cần trả; bỏ tick thì số trả về 0 và ẩn phần chi tiết.
  function qtyOf(row){var q=row.querySelector('[name=return_qty]');return q?Number(q.value.replace(',','.'))||0:0;}
  function syncPick(row){
    var pick=row.querySelector('[data-return-pick]'),detail=row.querySelector('[data-return-detail]');if(!pick)return;
    pick.checked=qtyOf(row)>0;if(detail)detail.hidden=!pick.checked;
  }
  document.querySelectorAll('[data-return-fields]').forEach(function(root){
    root.querySelectorAll('[data-return-row]').forEach(syncPick);
    root.addEventListener('change',function(event){
      var pick=event.target.closest('[data-return-pick]');if(!pick)return;
      var row=pick.closest('[data-return-row]'),q=row.querySelector('[name=return_qty]');
      if(pick.checked&&qtyOf(row)===0)q.value=q.dataset.held&&Number(q.dataset.held)>0?String(Number(q.dataset.held)):'1';
      if(!pick.checked)q.value='0';
      q.dispatchEvent(new Event('input',{bubbles:true}));
    });
    // Ô tick tự phát 'input' trước 'change': bỏ qua để không tự bỏ tick trước khi kịp đặt số lượng.
    root.addEventListener('input',function(event){if(event.target.closest('[data-return-pick]'))return;var row=event.target.closest('[data-return-row]');if(row)syncPick(row);});
    root.addEventListener('click',function(event){
      if(event.target.closest('[data-return-split]')){setTimeout(function(){root.querySelectorAll('[data-return-row]').forEach(function(row){var d=row.querySelector('[data-return-detail]'),pick=row.querySelector('[data-return-pick]');if(d)d.hidden=false;if(pick)pick.checked=true;});},0);}
      if(!event.target.closest('[data-return-all]'))return;
      root.querySelectorAll('[data-return-row]').forEach(function(row){
        var q=row.querySelector('[name=return_qty]');if(qtyOf(row)===0)q.value=q.dataset.held&&Number(q.dataset.held)>0?String(Number(q.dataset.held)):'1';
        syncPick(row);
      });
      root.dispatchEvent(new Event('return-state'));
    });
  });
  document.querySelectorAll('[data-receive-condition]').forEach(function(row){
    row.addEventListener('change',function(){condition(row,true);});condition(row,true);
  });
})();

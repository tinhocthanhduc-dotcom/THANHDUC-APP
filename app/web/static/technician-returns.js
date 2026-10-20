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
  document.querySelectorAll('[data-receive-condition]').forEach(function(row){
    row.addEventListener('change',function(){condition(row,true);});condition(row,true);
  });
})();

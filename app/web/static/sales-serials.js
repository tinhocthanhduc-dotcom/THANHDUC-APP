/* BH1: one serial field per machine, with comma/newline paste support. */
(function(){
  function init(){document.querySelectorAll('.sale-serials,[data-counter-serials]').forEach(function(group){
    if(group.dataset.serialReady)return;group.dataset.serialReady='1';
    var paste=group.querySelector('textarea'); if(!paste)return;
    var host=group.closest('[data-order-line],article,[data-dline],form');
    var suffix=paste.name.match(/^serials_(\d+)$/);
    var quantity=host&&host.querySelector(suffix?'[name=deliver_'+suffix[1]+'],[name=qty_'+suffix[1]+']':'[name=qty]');
    var fixed=Number(group.dataset.serialQty||0), boxes=document.createElement('div');boxes.className='serial-boxes';
    paste.before(boxes);
    function values(){return paste.value.split(/[,\r\n]+/).map(function(s){return s.trim();}).filter(Boolean);}
    function render(){
      var n=quantity?Number(quantity.value.replace(',','.')):fixed, serials=values();
      if(!Number.isInteger(n)||n<0||n>200){boxes.replaceChildren();return;}
      boxes.replaceChildren();
      for(var i=0;i<n;i++){
        var input=document.createElement('input');input.type='text';input.maxLength=64;
        input.value=serials[i]||'';input.placeholder='Serial máy '+(i+1);input.setAttribute('aria-label',input.placeholder);
        input.autocomplete='off';input.autocapitalize='characters';input.dataset.serialBox='1';
        input.addEventListener('input',function(){paste.value=Array.from(boxes.querySelectorAll('input')).map(function(s){return s.value.trim();}).filter(Boolean).join('\n');});
        boxes.append(input);
      }
    }
    paste.addEventListener('input',render);
    if(quantity){quantity.addEventListener('input',render);quantity.addEventListener('change',render);}
    render();
  });}
  init();new MutationObserver(init).observe(document.body,{childList:true,subtree:true});
})();

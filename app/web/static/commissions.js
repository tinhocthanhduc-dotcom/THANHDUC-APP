'use strict';
document.querySelectorAll('[data-commission-user-search]').forEach(function(input) {
  input.addEventListener('input', function() {
    var q=input.value.toLocaleLowerCase('vi').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/đ/g,'d');
    input.closest('.commission-picker').querySelectorAll('[data-person-name]').forEach(function(label) {
      var name=label.dataset.personName.normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/đ/g,'d');
      label.hidden=!name.includes(q);
    });
  });
});
